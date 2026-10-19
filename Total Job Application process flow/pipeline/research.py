"""Company research dossier: one cheap, small-context web-research run per company, cached and shared by every role
at that company (RUBRIC 1.3.0 cost work, 2026-10-03).

The ideamaker used to do ~20 searches and fetches inside its own Opus session, where every call rereads a context
that grows past 250k tokens. Doing the company-level research here, on Sonnet with an empty context, costs a fraction
of that, and a second role at the same company gets it free. The ideamaker still searches the web itself for anything
role-specific the dossier does not cover.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .config import Config
from .log import stage_log_path
from .models import Job
from .stages.claude_runner import run_claude, session_cost

log = logging.getLogger("pipeline.research")

STAGE = "research"
DEFAULT_TTL_DAYS = 45
TOOLS = ["WebSearch", "WebFetch"]
MIN_SOURCES = 6
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

PROMPT = """You are a research assistant preparing a company dossier for someone who tailors internship resumes. No human
will answer questions; do not ask any. Research the company below on the live web and reply with the dossier only, as
Markdown, in exactly the sections listed. Be specific and cite: every claim names its source by number.

COMPANY: {company}
EXAMPLE POSTING AT THIS COMPANY (for orientation only; the dossier covers the company, not just this role):
TITLE: {title}
URL: {url}
POSTING EXCERPT:
{jd}

Primary sources first, in this order of trust: the company's careers and student-program pages; its engineering blog,
newsroom, and published intern-project write-ups (the most valuable source type: what interns actually built); the
company's other job postings (to reverse-engineer the real stack); recruiting-team posts; Glassdoor and interview
aggregators (NodeFlair, TechPrep, InterviewQuery, Exponent, JoinTaro, LinkJob); Blind and Reddit insider accounts;
candidate blog posts; third-party press. Cross-reference before treating anything as reliable, and label confidence.

Sections (use these exact headings):
## What they build and who hires interns
Products, the engineering orgs and named teams that take software or technical interns, locations.
## Real tech stack
Languages, frameworks, infrastructure, data and ML tooling, each with its evidence (blog post, posting, talk).
## What interns actually built
Published intern projects and write-ups, with what was built, stack, and outcome. Say plainly if none were found.
## Hiring funnel
Each stage separately (ATS, recruiter screen, online assessment with format and platform, technical rounds, team
matching, offer), timeline, and anything unusual, such as an assessment that decides before a human reads the resume.
## What they reward
Stated values, what recruiters and engineers say they look for, signals insiders report, each with confidence.
## University pipelines
Schools the company visibly recruits engineers from or partners with, with evidence.
## Disconfirmed
At least one hypothesis you checked and rejected, with the source that rejected it.
## Sources
Numbered list of every URL used, at least {min_sources}.

Do not use em dashes anywhere; use commas or separate sentences.
"""


def company_slug(company: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (company or "unknown").lower()).strip("_")[:60] or "unknown"


def dossier_path(cfg: Config, company: str) -> Path:
    return cfg.data_dir / "companies" / f"{company_slug(company)}.md"


def _fresh(path: Path, ttl_days: float) -> bool:
    if not path.exists() or path.stat().st_size < 500:
        return False
    age_days = (datetime.now(timezone.utc).timestamp() - path.stat().st_mtime) / 86400
    return age_days <= ttl_days


def _sources(text: str) -> int:
    m = re.search(r"^##\s*Sources\s*$(.*)", text, re.M | re.S)
    return len(set(re.findall(r"https?://\S+", m.group(1)))) if m else 0


def current_dossier(cfg: Config, company: str) -> Path | None:
    """The cached dossier for this company if it is fresh, else None."""
    rc = cfg.get("research", {}) if hasattr(cfg, "get") else {}
    p = dossier_path(cfg, company)
    return p if _fresh(p, float(rc.get("ttl_days", DEFAULT_TTL_DAYS))) else None


def ensure_dossier(cfg: Config, job: Job, conn: sqlite3.Connection) -> Path | None:
    """Return a fresh dossier for the job's company, researching it first when needed. Never raises: on any failure
    the ideamaker simply does all of its research itself, as it always could."""
    rc = cfg.get("research", {}) if hasattr(cfg, "get") else {}
    if not bool(rc.get("enabled", True)) or not job.company:
        return None
    with _locks_guard:
        lock = _locks.setdefault(company_slug(job.company), threading.Lock())
    with lock:  # two roles at one company research it once
        return _ensure_locked(cfg, job, conn, rc)


def _ensure_locked(cfg: Config, job: Job, conn: sqlite3.Connection, rc) -> Path | None:
    have = current_dossier(cfg, job.company)
    if have:
        return have
    path = dossier_path(cfg, job.company)
    path.parent.mkdir(parents=True, exist_ok=True)
    attempt = db.last_attempt(conn, job.id, STAGE) + 1
    log_path = stage_log_path(cfg, job.id, STAGE, attempt)
    run_id = db.start_stage_run(conn, job.id, STAGE, attempt, str(log_path))
    prompt = PROMPT.format(company=job.company, title=job.title, url=job.url, jd=(job.jd_text or "")[:6000],
                           min_sources=MIN_SOURCES)
    try:
        res = run_claude(cfg, prompt=prompt, cwd=path.parent, log_path=log_path,
                         max_budget_usd=float(rc.get("max_budget_usd", 3.0)), timeout_s=int(rc.get("timeout_min", 20)) * 60,
                         max_turns=int(rc.get("max_turns", 40)), model=str(rc.get("model") or "claude-sonnet-5"),
                         tools=TOOLS, env_extra={"PIPELINE_JOB_ID": str(job.id), "PIPELINE_STAGE": STAGE})
    except Exception as e:  # noqa: BLE001
        db.finish_stage_run(conn, run_id, "failed", error=str(e)[:500])
        log.warning("research for %s failed: %s", job.company, e)
        return None
    seg = {"subtype": res.subtype, "cost_usd": res.cost_usd, "num_turns": res.num_turns, "session_id": res.session_id}
    text = (res.result_text or "").replace("—", ", ").strip()
    first = text.find("## ")
    text = text[first:] if first > 0 else text  # drop any preamble before the first section
    ok = res.ok and _sources(text) >= MIN_SOURCES and "## Real tech stack" in text
    err = None if ok else f"research {res.subtype}: {_sources(text)} sources"
    db.finish_stage_run(conn, run_id, "succeeded" if ok else "failed", session_id=res.session_id,
                        cost_usd=session_cost([seg]), num_turns=res.num_turns, result_json=json.dumps([seg]), error=err)
    if not ok:
        log.warning("research for %s unusable: %s", job.company, err)
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path.write_text(f"# Company dossier: {job.company}\n\nResearched {stamp} for the ideamaker (cached, shared by every "
                    f"role at this company). Cite these URLs as your own sources where you rely on them.\n\n{text}\n",
                    encoding="utf-8")
    return path
