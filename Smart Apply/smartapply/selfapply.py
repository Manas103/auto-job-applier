"""Jobs Alex applies to himself (Alex, 2026-10-16): JPMorgan's captcha, Tesla, Google, "no AI" postings, sites
that are just too hard to automate. Marking one moves the application to SELF_APPLY, which every fill and batch
skips, and writes a packet folder with everything he needs to apply by hand: the link, the tailored resume, the
transcript, the experience write-ups, the JD, his standard facts, and whatever answers were already drafted.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

from . import db, intake, tracker
from .config import Config
from .log import get_logger
from .models import AppState, Application
from .profile import Profile

log = get_logger("selfapply")

def packets_dir(cfg: Config) -> Path:
    """Smart Apply/self_apply/."""
    return cfg.project_root / "self_apply"


def packet_dir(cfg: Config, app: Application) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in f"{app.company}_{app.title}")[:80].strip("_")
    return packets_dir(cfg) / f"{safe}_{app.job_id}"


def _drafted(app: Application) -> list[tuple[str, str, str]]:
    """(question, answer, note) from the last fill, else from answers.json; answers only, no file uploads."""
    out: list[tuple[str, str, str]] = []
    labels: dict[str, str] = {}
    qpath = app.workdir / "questions.json"
    if qpath.exists():
        try:
            labels = {q["id"]: q.get("label") or q["id"] for q in json.loads(qpath.read_text(encoding="utf-8"))}
        except (ValueError, KeyError, TypeError):
            labels = {}
    rpath = app.workdir / "fill_report.json"
    if rpath.exists():
        try:
            for f in json.loads(rpath.read_text(encoding="utf-8")).get("fields", []):
                if f.get("page") == "agent" and str(f.get("label", "")).startswith("<"):
                    continue          # the browser agent's raw element names say nothing useful
                val = f.get("observed") or f.get("intended")
                if val in (None, "", []) and f.get("status") != "left_for_human":
                    continue
                out.append((str(f.get("label") or f.get("name")), _flat(val) or "(you answer)",
                            f.get("note") or "" if f.get("status") == "left_for_human" else ""))
        except ValueError:
            pass
    if out:
        return out
    apath = app.workdir / "answers.json"
    if apath.exists():
        try:
            for a in json.loads(apath.read_text(encoding="utf-8")).get("answers", []):
                out.append((labels.get(a["question_id"], a["question_id"]), _flat(a.get("value")) or "(you answer)",
                            a.get("needs_human") or ""))
        except (ValueError, KeyError):
            pass
    return out


def _flat(v) -> str:
    if isinstance(v, list):
        return "; ".join(str(x) for x in v)
    return "" if v is None else str(v).strip()


def _facts(p: Profile) -> list[str]:
    s, w, a, e = p.school, p.work_auth, p.address, p.eeo
    exp = [f"{x.title}, {x.company} ({x.location}), {x.start} to {x.end or 'present'}" for x in p.experiences]
    return [
        f"Name: {p.full_name}", f"Email: {p.email}", f"Phone: {p.phone}",
        f"Address: {a.street} {a.street2}, {a.city}, {a.state} {a.zip}".replace("  ", " "),
        f"LinkedIn: {p.linkedin}", f"GitHub: {p.github}",
        f"School: {s.name}, {s.degree} in {s.major}, {s.start} to {s.graduation} (expected), GPA {s.gpa}/{s.gpa_scale}",
        f"Work authorization: {w.statement}; {w.status}; sponsorship needed: {'Yes' if w.sponsorship_required else 'No'}",
        f"EEO: gender {e.gender}; Hispanic/Latino {e.hispanic_latino}; race {e.ethnicity}; veteran {e.veteran}; "
        f"disability {e.disability}",
        "Housing needed: No. Able to commute: Yes. Willing to relocate: Yes.",
        "Experience: " + " | ".join(exp),
    ]


def write_packet(cfg: Config, app: Application, profile: Profile, reason: str) -> Path:
    d = packet_dir(cfg, app)
    d.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    resume = app.workdir / profile.resume_filename
    if resume.exists():
        shutil.copy2(resume, d / profile.resume_filename)
        copied.append(profile.resume_filename)
    if profile.transcript_path and Path(profile.transcript_path).exists():
        shutil.copy2(profile.transcript_path, d / Path(profile.transcript_path).name)
        copied.append(Path(profile.transcript_path).name)
    for pattern in ("experience_descriptions_*.md", "jd.txt"):
        for f in sorted(app.role_folder.glob(pattern))[:1]:
            shutil.copy2(f, d / f.name)
            copied.append(f.name)
    lines = [f"# {app.company}: {app.title}", "",
             f"- **Apply here:** {app.url}",
             f"- **Why you are doing this one:** {reason}",
             f"- **Pipeline job:** {app.job_id}. After you submit, run `smartapply applied {app.job_id}` (or tell Claude).",
             f"- **Packet written:** {date.today().isoformat()}", "",
             "## Files in this folder", ""] + [f"- {c}" for c in copied] + [
             "", "The resume here is the one tailored for this job. The experience file has short and long write-ups "
             "for every job and project on it, sized for application text boxes.", "",
             "## Your standard answers", ""] + [f"- {x}" for x in _facts(profile)]
    drafted = _drafted(app)
    if drafted:
        lines += ["", "## Answers already drafted for this form", "",
                  "Check each one before you use it. \"(you answer)\" means it was left for you.", ""]
        for q, a, note in drafted:
            lines.append(f"- **{q.strip()[:200]}**: {a[:1500]}" + (f" _(note: {note[:200]})_" if note else ""))
    (d / "APPLY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


def mark_self_apply(cfg: Config, conn: sqlite3.Connection, profile: Profile, job_id: int, reason: str) -> tuple[Application, Path]:
    row = db.application_by_job(conn, job_id)
    if row is None:
        with intake.open_pipeline_db(cfg) as pconn:
            job = intake.pipeline_job(pconn, job_id)
        if job is None:
            raise ValueError(f"pipeline job {job_id} not found")
        app = intake.enqueue_job(conn, cfg, job, profile_resume_name=profile.resume_filename)
    else:
        app = Application.from_row(row)
    if app.state == AppState.APPLIED:
        raise ValueError(f"job {job_id} ({app.company}) is already applied")
    if app.state != AppState.SELF_APPLY:
        tracker.transition(conn, app.id, AppState.SELF_APPLY, "self_apply", detail={"reason": reason}, reason=reason)
        app = tracker.load(conn, app.id)
    return app, write_packet(cfg, app, profile, reason)


def self_apply_list(conn: sqlite3.Connection) -> list[Application]:
    return [a for a in (Application.from_row(r) for r in db.all_applications(conn)) if a.state == AppState.SELF_APPLY]
