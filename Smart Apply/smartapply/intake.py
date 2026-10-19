"""Read the pipeline's jobs and role folders (read-only) and turn them into applications.

The pipeline DB is opened with `?mode=ro`; its own db.connect() runs DDL and must not be reused.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from . import db
from .config import Config
from .models import Application, AppState
from .util import now_iso, read_json, write_json

_LOCALE_SEG = re.compile(r"^[a-z]{2}-[A-Za-z]{2}$")
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


# ---------------------------------------------------------------- ATS detection (superset of pipeline/urlnorm.py)

def detect_ats(url: str) -> tuple[str | None, dict]:
    """(kind, params). Kinds: workday greenhouse lever ashby smartrecruiters oracle_hcm icims successfactors taleo
    jobvite rippling gem eightfold workable phenom linkedin, or None for an unknown career site."""
    url = (url or "").strip()
    p = urllib.parse.urlsplit(url)
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    segs = [s for s in p.path.split("/") if s]
    q = dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))

    if host in ("job-boards.greenhouse.io", "boards.greenhouse.io", "job-boards.eu.greenhouse.io", "boards.eu.greenhouse.io"):
        if len(segs) >= 3 and segs[1] == "jobs" and segs[2].isdigit():
            return "greenhouse", {"board": segs[0], "job_id": segs[2], "eu": ".eu." in host}
        if "gh_jid" in q and q["gh_jid"].isdigit():
            return "greenhouse", {"board": segs[0] if segs else None, "job_id": q["gh_jid"], "eu": ".eu." in host}
    if "gh_jid" in q and q["gh_jid"].isdigit():
        return "greenhouse", {"board": None, "job_id": q["gh_jid"], "eu": False}

    if re.fullmatch(r"jobs(\.[a-z]+)?\.lever\.co", host):
        if len(segs) >= 2 and re.fullmatch(_UUID, segs[1]):
            return "lever", {"company": segs[0], "posting_id": segs[1].lower(), "eu": host.startswith("jobs.eu.")}

    if host == "jobs.ashbyhq.com":
        if len(segs) >= 2 and re.fullmatch(_UUID, segs[1]):
            return "ashby", {"org": segs[0], "posting_id": segs[1].lower()}

    m = re.fullmatch(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com", host)
    if m:
        tenant = m.group(1)
        s = list(segs)
        if s and _LOCALE_SEG.match(s[0]):
            s = s[1:]
        if len(s) >= 2 and s[1] == "job":
            tail = s[-1]
            rm = re.search(r"_([A-Za-z0-9-]+)$", tail)
            return "workday", {"host": host, "tenant": tenant, "site": s[0], "external_path": "/" + "/".join(s[1:]),
                               "req_id": rm.group(1) if rm else None}
        if s:
            return "workday", {"host": host, "tenant": tenant, "site": s[0], "external_path": None, "req_id": None}

    # Workday's other public host: wd3.myworkdaysite.com/{locale}/recruiting/{tenant}/{site}/job/... (Brevan Howard)
    if re.fullmatch(r"wd\d+\.myworkdaysite\.com", host):
        s = list(segs)
        if s and _LOCALE_SEG.match(s[0]):
            s = s[1:]
        if len(s) >= 3 and s[0] == "recruiting":
            tenant, site, rest = s[1], s[2], s[3:]
            if rest and rest[0] == "job":
                rm = re.search(r"_([A-Za-z0-9-]+)$", rest[-1])
                return "workday", {"host": host, "tenant": tenant, "site": site, "external_path": "/" + "/".join(rest),
                                   "req_id": rm.group(1) if rm else None}
            return "workday", {"host": host, "tenant": tenant, "site": site, "external_path": None, "req_id": None}

    if host == "jobs.smartrecruiters.com":
        return "smartrecruiters", {"host": host, "path": p.path}
    if host.endswith(".oraclecloud.com") and "hcmUI/CandidateExperience" in p.path:
        return "oracle_hcm", {"host": host, "path": p.path}
    if host.endswith(".icims.com"):
        return "icims", {"host": host, "path": p.path}
    if host.endswith(".successfactors.com") or host.endswith(".successfactors.eu"):
        return "successfactors", {"host": host, "path": p.path}
    if host.endswith(".taleo.net"):
        return "taleo", {"host": host, "path": p.path}
    if host == "jobs.jobvite.com":
        return "jobvite", {"host": host, "path": p.path}
    if host == "ats.rippling.com":
        return "rippling", {"host": host, "path": p.path}
    if host == "jobs.gem.com":
        return "gem", {"host": host, "path": p.path}
    if host.endswith(".eightfold.ai"):
        return "eightfold", {"host": host, "path": p.path}
    if host == "apply.workable.com":
        return "workable", {"host": host, "path": p.path}
    if host.endswith("linkedin.com"):
        return "linkedin", {"host": host, "path": p.path}
    return None, {"host": host, "path": p.path}


# ---------------------------------------------------------------- pipeline DB (read-only)

@contextmanager
def open_pipeline_db(cfg: Config) -> Iterator[sqlite3.Connection]:
    """Read-only connection to the pipeline's SQLite file; closed on exit."""
    path = cfg.pipeline_db_path
    if not path.exists():
        raise FileNotFoundError(f"pipeline DB not found: {path}")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def candidate_jobs(pconn: sqlite3.Connection, *, include_applied: bool = False) -> list[sqlite3.Row]:
    """Pipeline jobs that have a finished role folder and are not yet marked applied there."""
    sql = ("SELECT id, url, company, title, locations, ats, ats_id, state, flags, role_folder, priority, decided_at "
           "FROM jobs WHERE state='done' AND active=1 AND role_folder IS NOT NULL AND role_folder != ''")
    if not include_applied:
        sql += " AND flags NOT LIKE '%applied%'"
    # set on the pipeline side when an application would be a duplicate (e.g. HRT takes one per cycle)
    sql += " AND flags NOT LIKE '%do_not_apply%'"
    sql += " ORDER BY priority DESC, decided_at DESC"
    return list(pconn.execute(sql))


def pipeline_job(pconn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return pconn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def pipeline_csrf_token(pconn: sqlite3.Connection) -> str | None:
    row = pconn.execute("SELECT value FROM settings WHERE key='csrf_token'").fetchone()
    return row["value"] if row else None


# ---------------------------------------------------------------- role folders

@dataclass
class RoleFolder:
    path: Path
    slug: str
    realistic_pdf: Path | None
    realistic_tex: Path | None
    experience_md: Path | None
    no_brainer_md: Path | None
    realistic_breakdown_md: Path | None
    reconciliation_md: Path | None
    jd_txt: Path | None
    meta: dict = field(default_factory=dict)
    job: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def resume_skills(tex: Path | None, limit: int = 10) -> list[str]:
    """Skills listed on the role's tailored resume (its Skills section), interleaved across the categories so a short
    list still covers languages, frameworks and tools. Only what the resume already claims (2026-10-13)."""
    if not tex or not Path(tex).exists():
        return []
    src = Path(tex).read_text(encoding="utf-8", errors="replace")
    m = re.search(r"\\section\{Skills\}(.*?)(\\section\{|\\end\{document\})", src, re.S)
    if not m:
        return []
    groups: list[list[str]] = []
    for body in re.findall(r"\\textbf\{[^}]*\}\{:\s*(.*?)\}\s*(?:\\\\|$)", m.group(1), re.M):
        items, depth, cur = [], 0, ""
        for ch in body:
            depth += 1 if ch == "(" else -1 if ch == ")" else 0
            if ch == "," and depth == 0:
                items.append(cur)
                cur = ""
            else:
                cur += ch
        items.append(cur)
        # "TypeScript/JavaScript" is two skills on Workday's list
        items = [y for x in items for y in (x.split("/") if re.fullmatch(r"[\w+#.]+(/[\w+#.]+)+", x.strip()) else [x])]
        clean =[re.sub(r"\\[a-zA-Z]+\s*|[{}]", "", x.replace("\\&", "&")).strip() for x in items]
        groups.append([x for x in clean if x and len(x) <= 40])
    out: list[str] = []
    for i in range(max((len(g) for g in groups), default=0)):
        for g in groups:
            if i < len(g) and g[i] not in out:
                out.append(g[i])
    return out[:limit]


def resolve_role_folder(folder: Path) -> RoleFolder:
    folder = Path(folder)
    meta = read_json(folder / "meta.json", {}) or {}
    job = read_json(folder / "job.json", {}) or {}
    slug = str(meta.get("slug") or job.get("slug") or "")
    problems: list[str] = []
    if not folder.exists():
        problems.append("role folder missing")
    if not slug:
        # Legacy folders have no meta.json; infer from the realistic PDF name.
        pdfs = sorted(folder.glob("ideal_candidate_realistic_*.pdf"))
        if pdfs:
            slug = re.sub(r"^ideal_candidate_realistic_", "", pdfs[0].stem).split("_")[0]
        else:
            problems.append("no slug and no realistic PDF")

    def pick(pattern: str) -> Path | None:
        cands = sorted(folder.glob(pattern))
        if not cands:
            return None
        # Prefer the base file over variants (_nopapers, _preprint).
        base = [c for c in cands if c.stem == pattern.replace("*", slug).rsplit(".", 1)[0]]
        return base[0] if base else cands[0]

    pdf = folder / f"ideal_candidate_realistic_{slug}.pdf"
    if not pdf.exists():
        pdf = pick("ideal_candidate_realistic_*.pdf")
    if pdf is None or not pdf.exists():
        problems.append("realistic PDF missing")
        pdf = None
    tex = folder / f"ideal_candidate_realistic_{slug}.tex"
    exp = folder / f"experience_descriptions_{slug}.md"
    nb = folder / f"no_brainer_hire_{slug}.md"
    bd = folder / f"ideal_candidate_realistic_{slug}_breakdown.md"
    rec = folder / "reconciliation.md"
    jd = folder / "jd.txt"
    return RoleFolder(path=folder, slug=slug, realistic_pdf=pdf, realistic_tex=tex if tex.exists() else None,
                      experience_md=exp if exp.exists() else pick("experience_descriptions_*.md"),
                      no_brainer_md=nb if nb.exists() else pick("no_brainer_hire_*.md"),
                      realistic_breakdown_md=bd if bd.exists() else pick("ideal_candidate_realistic_*_breakdown.md"),
                      reconciliation_md=rec if rec.exists() else None, jd_txt=jd if jd.exists() else None,
                      meta=meta, job=job, problems=problems)


# ---------------------------------------------------------------- enqueue

def enqueue_job(conn: sqlite3.Connection, cfg: Config, job: sqlite3.Row, *, profile_resume_name: str) -> Application:
    """Create (or return) the application row and its workdir for one pipeline job."""
    existing = db.application_by_job(conn, int(job["id"]))
    if existing is not None:
        return Application.from_row(existing)
    role = resolve_role_folder(Path(job["role_folder"]))
    if role.problems:
        raise ValueError(f"job {job['id']} ({job['company']}): " + "; ".join(role.problems))
    kind, params = detect_ats(job["url"])
    ats = job["ats"] or kind
    workdir = cfg.applications_dir / str(job["id"])
    workdir.mkdir(parents=True, exist_ok=True)
    write_json(workdir / "job.json", {k: job[k] for k in job.keys()})
    stage_resume(role, workdir, profile_resume_name)
    app_id = db.insert_application(
        conn, job_id=int(job["id"]), company=job["company"], title=job["title"], url=job["url"], ats=ats,
        ats_id=job["ats_id"], ats_params=params, role_folder=str(role.path), slug=role.slug, workdir=str(workdir),
        tenant=params.get("tenant"), state=AppState.QUEUED.value)
    db.add_event(conn, app_id, "queued", to_state=AppState.QUEUED.value, detail={"ats": ats})
    return Application.from_row(db.application_by_id(conn, app_id))


def stage_resume(role: RoleFolder, workdir: Path, resume_name: str) -> Path:
    """Copy the realistic PDF under the name a recruiter should see. Never the 'full ideal' PDF."""
    if role.realistic_pdf is None:
        raise FileNotFoundError(f"no realistic PDF in {role.path}")
    dest = workdir / resume_name
    if not dest.exists() or dest.stat().st_mtime < role.realistic_pdf.stat().st_mtime:
        shutil.copy2(role.realistic_pdf, dest)
    return dest


def load_job_json(app: Application) -> dict:
    return read_json(app.job_json_path, {}) or {}


# ---------------------------------------------------------------- experience entries (for Workday's My Experience)

_MONTH_NUM = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def parse_month(text: str) -> str | None:
    """'Feb. 2026' / 'May 2025' / '2026-02' / 'Present' -> 'YYYY-MM' or None (present/unknown)."""
    t = (text or "").strip()
    if not t or re.match(r"(?i)present|current|now", t):
        return None
    m = re.match(r"(\d{4})-(\d{2})", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{4})", t)
    if m and m.group(1).lower() in _MONTH_NUM:
        return f"{m.group(2)}-{_MONTH_NUM[m.group(1).lower()]:02d}"
    m = re.search(r"\b(\d{4})\b", t)
    return f"{m.group(1)}-01" if m else None


@dataclass
class ExperienceEntry:
    company: str
    title: str
    location: str
    start: str | None          # YYYY-MM
    end: str | None            # YYYY-MM or None = present
    short: str = ""            # the "Short (under 600 characters)" block
    is_project: bool = False


def parse_experience_entries(path: Path | None) -> list[ExperienceEntry]:
    """The role folder's experience_descriptions_<slug>.md: one `Form fields: Company | Title | Location | Start | End`
    line per entry plus a Short block. Every number and date there matches the tailored resume."""
    if path is None or not Path(path).exists():
        return []
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    out: list[ExperienceEntry] = []
    blocks = re.split(r"(?m)^##\s+", text)
    for block in blocks[1:]:
        m = re.search(r"(?m)^Form fields:\s*(.+)$", block)
        if not m:
            continue
        parts = [p.strip() for p in m.group(1).split("|")]
        while len(parts) < 5:
            parts.append("")
        company, title, location, start, end = parts[:5]
        sm = re.search(r"Short[^\n]*\n```\n(.*?)\n```", block, re.S)
        short = sm.group(1).strip() if sm else ""
        out.append(ExperienceEntry(company=company, title=title, location=location, start=parse_month(start),
                                   end=parse_month(end), short=short,
                                   is_project=bool(re.search(r"(?i)personal project|^project", company))))
    return out
