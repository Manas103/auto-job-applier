"""Company watchlist polled directly on the ATS (tier 2). Auto-added when a job is approved."""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from . import db
from .config import Config
from .models import Job, Listing, WatchlistEntry
from .sources import ashby, greenhouse, lever, workday
from .urlnorm import detect_ats
from .util import now_iso

log = logging.getLogger("pipeline.watchlist")


def auto_add_from_url(conn: sqlite3.Connection, cfg: Config, job: Job, *, added_by: str = "auto") -> WatchlistEntry | None:
    kind, params = detect_ats(job.url)
    if kind is None:
        return None
    board = host = tenant = site = None
    if kind == "greenhouse":
        board = params.get("board") or greenhouse.guess_board(job.company, params["job_id"])
        if not board:
            log.info("watchlist: greenhouse board unknown for %s", job.company)
            return None
    elif kind == "lever":
        board = params["company"]
    elif kind == "ashby":
        board = params["org"]
    elif kind == "workday":
        host, tenant, site = params["host"], params["tenant"], params["site"]
    existing = db.watchlist_find(conn, kind, board, host, tenant, site)
    if existing:
        return existing
    search_text = str(cfg.watchlist.workday_search_text) if kind == "workday" else "intern"
    wid = db.watchlist_insert(conn, company=job.company, ats=kind, board=board, host=host, tenant=tenant, site=site,
                              search_text=search_text, added_by=added_by,
                              poll_minutes=int(cfg.watchlist.default_poll_minutes))
    return db.watchlist_by_id(conn, wid)


def due_entries(conn: sqlite3.Connection, now: datetime | None = None) -> list[WatchlistEntry]:
    now = now or datetime.now(timezone.utc)
    out = []
    for e in db.watchlist_all(conn):
        if not e.enabled:
            continue
        if not e.last_polled_at:
            out.append(e)
            continue
        try:
            last = datetime.fromisoformat(e.last_polled_at)
        except ValueError:
            out.append(e)
            continue
        interval = e.poll_minutes * (2 ** min(e.consecutive_failures, 5)) if e.consecutive_failures >= 3 else e.poll_minutes
        interval = min(interval, 24 * 60)
        if now - last >= timedelta(minutes=interval):
            out.append(e)
    return out


def poll_entry(conn: sqlite3.Connection, cfg: Config, entry: WatchlistEntry) -> list[Listing]:
    title_re = re.compile(str(cfg.watchlist.title_regex), re.I)
    try:
        if entry.ats == "greenhouse":
            items = greenhouse.list_jobs(entry.board or "")
        elif entry.ats == "lever":
            items = lever.list_postings(entry.board or "")
        elif entry.ats == "ashby":
            items = ashby.list_jobs(entry.board or "")
        elif entry.ats == "workday":
            items = workday.list_jobs(entry.host or "", entry.tenant or "", entry.site or "",
                                      search_text=entry.search_text or "intern",
                                      max_pages=int(cfg.watchlist.workday_max_pages))
        else:
            raise ValueError(f"unknown ats {entry.ats}")
    except Exception as e:  # noqa: BLE001
        db.watchlist_update(conn, entry.id, last_polled_at=now_iso(), last_error=str(e)[:300],
                            consecutive_failures=entry.consecutive_failures + 1)
        log.warning("watchlist poll failed for %s (%s): %s", entry.company, entry.ats, e)
        return []
    kept = [it for it in items if title_re.search(it.title or "")]
    for it in kept:
        it.company = entry.company or it.company
        it.source = f"watch:{entry.company}"
    db.watchlist_update(conn, entry.id, last_polled_at=now_iso(), last_ok_at=now_iso(), last_error=None,
                        last_count=len(kept), consecutive_failures=0)
    return kept
