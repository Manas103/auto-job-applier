"""Marking an application as applied, in the pipeline (so its dashboard and Archon see it) and locally.

The pipeline's only write path for "applied" is its dashboard route POST /job/<id>/applied with the CSRF token
from its settings table. Smart Apply never writes the pipeline DB directly.
"""

from __future__ import annotations

import sqlite3
import urllib.parse

from . import db, intake, memory, tracker
from .config import Config
from .log import get_logger
from .models import AppState
from .util import HttpError, http_post

log = get_logger("applied")

# URL paths that only exist after a real submission (research/03-ats-mechanics.md).
CONFIRMATION_URL_PATTERNS = {
    "greenhouse": ("/confirmation",),
    "lever": ("/thanks",),
    "jobvite": ("/applyConfirmation",),
    "smartrecruiters": ("/success",),
    "phenom": ("/applythankyou",),
    "workday": ("/jobTasks/completed/application",),
}


def url_confirms(ats: str | None, url: str) -> bool:
    for frag in CONFIRMATION_URL_PATTERNS.get(ats or "", ()):
        if frag in (url or ""):
            return True
    return False


def mark_applied_in_pipeline(cfg: Config, job_id: int) -> bool:
    """POST to the pipeline dashboard. Returns True on success; the caller records locally either way."""
    try:
        with intake.open_pipeline_db(cfg) as pconn:
            token = intake.pipeline_csrf_token(pconn)
    except (FileNotFoundError, sqlite3.Error) as e:
        log.warning("pipeline DB unavailable for csrf: %s", e)
        return False
    if not token:
        log.warning("pipeline csrf token missing; cannot mark applied there")
        return False
    url = f"{str(cfg.pipeline_dashboard).rstrip('/')}/job/{job_id}/applied"
    data = urllib.parse.urlencode({"csrf": token}).encode("utf-8")
    try:
        http_post(url, data, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=10)
        return True
    except HttpError as e:
        # 303 redirect is the success path for that route; urllib raises on 3xx without Location handling only for some codes
        if e.status in (303, 302, 301):
            return True
        log.warning("pipeline applied POST failed: %s", e)
        return False


def mark_applied(conn: sqlite3.Connection, cfg: Config, app_id: int, *, source: str) -> bool:
    app = tracker.load(conn, app_id)
    ok = mark_applied_in_pipeline(cfg, app.job_id)
    remembered = 0
    if app.state != AppState.APPLIED:
        try:
            remembered = memory.remember_from_application(conn, app)
        except Exception as e:  # noqa: BLE001
            log.warning("answer memory skipped for app %s: %s", app_id, e)
        tracker.transition(conn, app_id, AppState.APPLIED, "applied",
                           detail={"source": source, "pipeline_marked": ok, "remembered_answers": remembered})
    else:
        tracker.note(conn, app_id, "applied_again", {"source": source, "pipeline_marked": ok})
    if not ok:
        db.set_setting(conn, f"pipeline_applied_pending:{app.job_id}", "1")
    return ok


def retry_pending_pipeline_marks(conn: sqlite3.Connection, cfg: Config) -> int:
    """Called by the daemon periodically: re-send applied marks the pipeline dashboard missed (it was down)."""
    rows = list(conn.execute("SELECT key FROM settings WHERE key LIKE 'pipeline_applied_pending:%'"))
    done = 0
    for r in rows:
        job_id = int(r["key"].split(":", 1)[1])
        if mark_applied_in_pipeline(cfg, job_id):
            conn.execute("DELETE FROM settings WHERE key=?", (r["key"],))
            done += 1
    return done
