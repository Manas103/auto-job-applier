"""Company ranking for work-queue order (Alex, 2026-10-03): the best companies for a software internship get
their resumes built first, the weakest last.

Each company gets one 0-100 score, cached in `company_scores`. Scores come from one batched `claude -p` call per
group of unscored companies (a few cents), made when the collector pings a job, so a score already exists by the
time Alex clicks Yes. An unscored company falls back to DEFAULT_SCORE so it is never starved or skipped.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import sys

from .config import Config
from .util import now_iso

log = logging.getLogger("pipeline.rank")

DEFAULT_SCORE = 50
BATCH = 25

PROMPT = """You rank companies by how good a SOFTWARE ENGINEERING INTERNSHIP there is for a strong U.S. computer
science undergraduate (green-card holder, graduating May 2028) whose goal is the best possible new-grad software
career. Weigh, in roughly this order: engineering bar and caliber of the people they would work with; how much the
name on a resume opens doors afterward; intern pay; quality of the intern program and mentorship; real return-offer
value. A top quant/trading firm or frontier AI lab is near 100; FAANG-level big tech about 85-95; strong well-known
tech, fintech and unicorns about 65-85; large non-tech enterprises with real software teams about 40-65; small or
little-known companies, government contractors with routine IT work, and non-software roles at non-tech firms lower.
Judge the company as an employer of software interns, not its stock or consumer brand.

Answer from your own knowledge without using any tools; if a company is unfamiliar, score it conservatively
from its name and industry. Score every company below. Respond with ONLY a JSON object mapping each company name, exactly as written, to
{"score": <integer 0-100>, "tier": "S"|"A"|"B"|"C"|"D", "reason": "<under 15 words>"}.

Companies:
"""


def get_score(conn: sqlite3.Connection, company: str) -> int:
    row = conn.execute("SELECT score FROM company_scores WHERE company=?", (company,)).fetchone()
    return int(row[0]) if row else DEFAULT_SCORE


def job_priority(conn: sqlite3.Connection, job_id: int) -> int:
    row = conn.execute("SELECT company FROM jobs WHERE id=?", (job_id,)).fetchone()
    return get_score(conn, row[0]) if row and row[0] else DEFAULT_SCORE


def unscored(conn: sqlite3.Connection, companies) -> list[str]:
    out = []
    for c in sorted({c for c in companies if c}):
        if not conn.execute("SELECT 1 FROM company_scores WHERE company=?", (c,)).fetchone():
            out.append(c)
    return out


def _ask(cfg: Config, names: list[str], model: str) -> dict:
    # One turn, no tools: a web search on an unfamiliar company would burn the only turn and return nothing.
    cmd = [*[str(x) for x in cfg.claude.cmd], "-p", "--model", model, "--output-format", "json", "--max-turns", "1",
           "--tools", "", "--strict-mcp-config"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    proc = subprocess.run(cmd, input=PROMPT + "\n".join(f"- {n}" for n in names), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600, creationflags=flags)
    env = json.loads(proc.stdout)
    m = re.search(r"\{.*\}", env.get("result") or "", re.S)
    if not m:
        raise ValueError(f"no JSON in ranking reply (subtype={env.get('subtype')}, turns={env.get('num_turns')}): "
                         f"{(env.get('result') or '')[:200]}")
    return json.loads(m.group(0))


def ensure_scores(conn: sqlite3.Connection, cfg: Config, companies) -> int:
    """Score any companies not yet in company_scores. Returns how many were scored. Never raises."""
    todo = unscored(conn, companies)
    if not todo:
        return 0
    from . import usage
    ok, why = usage.check(cfg, conn, "light")
    if not ok:
        log.info("company scoring skipped: %s", why)
        return 0
    model = str(cfg.get("rank", {}).get("model") or "claude-sonnet-5") if hasattr(cfg, "get") else "claude-sonnet-5"
    n = 0
    for i in range(0, len(todo), BATCH):
        names = todo[i:i + BATCH]
        try:
            got = _ask(cfg, names, model)
        except Exception as e:  # noqa: BLE001
            log.warning("company scoring failed for %d companies: %s", len(names), e)
            continue
        for name in names:
            v = got.get(name)
            if not isinstance(v, dict) or not isinstance(v.get("score"), (int, float)):
                continue
            conn.execute("INSERT OR REPLACE INTO company_scores(company, score, tier, reason, scored_at, model) "
                         "VALUES (?,?,?,?,?,?)", (name, max(0, min(100, int(v["score"]))), str(v.get("tier") or ""),
                                                  str(v.get("reason") or "")[:200], now_iso(), model))
            n += 1
    log.info("scored %d of %d companies", n, len(todo))
    return n


# ----------------------------------------------------------------------------- tiers and lanes

def _tiers_cfg(cfg: Config):
    from .config import DEFAULTS
    t = cfg.get("tiers") if hasattr(cfg, "get") else None
    return t if t is not None else DEFAULTS["tiers"]


def tier_for_score(cfg: Config, score: int) -> str:
    cuts = _tiers_cfg(cfg)["score_cutoffs"]
    for t in ("S", "A", "B", "C"):
        if score >= float(cuts[t]):
            return t
    return "D"


def company_tier(conn: sqlite3.Connection, cfg: Config, company: str) -> str:
    """The stored tier when it is a real tier letter, else the score mapped through tiers.score_cutoffs.
    An unscored company maps DEFAULT_SCORE (C with the default cutoffs)."""
    row = conn.execute("SELECT score, tier FROM company_scores WHERE company=?", (company,)).fetchone() if company else None
    if row is None:
        return tier_for_score(cfg, DEFAULT_SCORE)
    tier = str(row[1] or "").strip().upper()
    return tier if tier in ("S", "A", "B", "C", "D") else tier_for_score(cfg, int(row[0]))


def job_tier(conn: sqlite3.Connection, cfg: Config, job_id: int) -> str:
    row = conn.execute("SELECT company FROM jobs WHERE id=?", (job_id,)).fetchone()
    return company_tier(conn, cfg, row[0] if row else "")


def lane_override(flags) -> str | None:
    """A `lane:<x>` flag forces a lane (set from the dashboard, e.g. Upgrade to full)."""
    for f in flags or []:
        if isinstance(f, str) and f.startswith("lane:") and f[5:] in ("full", "light", "archetype"):
            return f[5:]
    return None


def job_lane(conn: sqlite3.Connection, cfg: Config, job_id: int) -> str:
    """full | light | archetype. With tiers disabled every job runs the full pipeline."""
    row = conn.execute("SELECT flags FROM jobs WHERE id=?", (job_id,)).fetchone()
    forced = lane_override(json.loads(row[0]) if row and row[0] else [])
    if forced:
        return forced
    t = _tiers_cfg(cfg)
    if not bool(t["enabled"]):
        return "full"
    return str(t["lanes"][job_tier(conn, cfg, job_id)])


def tier_pings(cfg: Config, tier: str) -> bool:
    t = _tiers_cfg(cfg)
    return (not bool(t["enabled"])) or bool(t["ping"].get(tier, True))
