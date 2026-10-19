"""Collector: fetch sources, dedupe into the store, screen new jobs, ping the user."""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass, field

from . import db, watchlist
from .config import Config
from .dedupe import find_duplicate, fuzzy_key
from .jd import fetch_jd, store_jd
from .models import Job, JobState, Listing
from .notify import in_quiet_hours, notify_job
from .screen import screen_jd, screen_metadata
from .sources import SOURCE_TYPES
from .statemachine import transition
from .urlnorm import canonical_key
from . import util as _util
from .util import http_get, now_iso

log = logging.getLogger("pipeline.collector")
_http_get = http_get


@dataclass
class CollectReport:
    fetched_per_source: dict[str, int] = field(default_factory=dict)
    new: int = 0
    updated: int = 0
    deactivated: int = 0
    screened_out: int = 0
    pinged: int = 0
    deferred: int = 0
    tier_ignored: int = 0
    seed: bool = False
    errors: dict[str, str] = field(default_factory=dict)
    suspicious: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def summary(self) -> str:
        per = ", ".join(f"{k}={v}" for k, v in self.fetched_per_source.items())
        s = (f"fetched={sum(self.fetched_per_source.values())} ({per}) new={self.new} updated={self.updated} "
             f"deactivated={self.deactivated} screened_out={self.screened_out} pinged={self.pinged} deferred={self.deferred}"
             f"{f' tier_ignored={self.tier_ignored}' if self.tier_ignored else ''}")
        if self.seed:
            s += " SEED"
        if self.errors:
            s += " errors=" + ",".join(self.errors)
        if self.suspicious:
            s += " suspicious=" + ",".join(self.suspicious)
        return s


def _fetch_tier1(conn: sqlite3.Connection, cfg: Config, only: list[str] | None, report: CollectReport) -> tuple[list[Listing], set[str]]:
    listings: list[Listing] = []
    fetched: set[str] = set()
    for src in cfg.collector.sources:
        name, typ = src["name"], src["type"]
        if not src.get("enabled", True) or (only and name not in only):
            continue
        parser = SOURCE_TYPES.get(typ)
        if parser is None:
            report.errors[name] = f"unknown source type {typ}"
            continue
        try:
            raw = _http_get(src["url"], timeout=int(cfg.collector.http_timeout_s), user_agent=str(cfg.collector.user_agent))
            items = parser(raw, name, today=_util.today())
        except Exception as e:  # noqa: BLE001
            report.errors[name] = str(e)[:300]
            db.record_source_run(conn, name, 0, False, str(e)[:300])
            log.warning("source %s failed: %s", name, e)
            continue
        prev = db.last_source_count(conn, name)
        if prev and (len(items) == 0 or len(items) < prev * 0.4):
            report.suspicious.append(name)
            log.warning("source %s returned %d items (previous %d): marking suspicious", name, len(items), prev)
        db.record_source_run(conn, name, len(items), True)
        report.fetched_per_source[name] = len(items)
        listings.extend(items)
        fetched.add(name)
    return listings, fetched


def _fetch_tier2(conn: sqlite3.Connection, cfg: Config, report: CollectReport) -> tuple[list[Listing], set[str]]:
    listings: list[Listing] = []
    fetched: set[str] = set()
    for entry in watchlist.due_entries(conn):
        items = watchlist.poll_entry(conn, cfg, entry)
        name = f"watch:{entry.company}"
        e = db.watchlist_by_id(conn, entry.id)
        if e and e.last_error:
            report.errors[name] = e.last_error
            continue
        report.fetched_per_source[name] = len(items)
        listings.extend(items)
        fetched.add(name)
    return listings, fetched


def merge_listings(conn: sqlite3.Connection, listings: list[Listing], fetched_sources: set[str],
                   report: CollectReport) -> list[int]:
    """Insert or update. Returns ids of newly inserted jobs (in insertion order)."""
    ts = now_iso()
    new_ids: list[int] = []
    present_keys: set[str] = set()
    with db.tx(conn):
        for item in listings:
            key = canonical_key(item.url)
            present_keys.add(key)
            existing = find_duplicate(conn, item, key)
            if existing is None:
                jid = db.insert_job(conn, item, key, fuzzy_key(item.company, item.title, item.locations), ts=ts)
                db.add_event(conn, jid, "discovered", None, JobState.NEW.value, {"source": item.source, "url": item.url})
                new_ids.append(jid)
                continue
            present_keys.add(existing.key)
            fields: dict = {"last_seen": ts, "active": True}
            sources = list(existing.sources)
            if item.source not in sources:
                sources.append(item.source)
                fields["sources"] = sources
            if item.source_id:
                sids = dict(existing.source_ids)
                sids[item.source] = item.source_id
                fields["source_ids"] = sids
            for f in ("company", "title", "category", "sponsorship", "pay", "date_posted"):
                cur = getattr(existing, f)
                val = getattr(item, f)
                if (not cur or cur == "Other") and val and val != "Other":
                    fields[f] = val
            for f in ("locations", "terms", "degrees"):
                if not getattr(existing, f) and getattr(item, f):
                    fields[f] = getattr(item, f)
            if item.ats and not existing.ats:
                fields["ats"], fields["ats_id"] = item.ats, item.ats_id
            if item.jd_text and not existing.jd_text:
                fields["jd_text"], fields["jd_source"], fields["jd_fetched_at"] = item.jd_text, "ats_api", ts
            db.update_job(conn, existing.id, **fields)
            report.updated += 1
        # Deactivate jobs that vanished from every source that listed them (only among sources fetched OK).
        if fetched_sources:
            rows = conn.execute("SELECT id, key, sources FROM jobs WHERE active=1").fetchall()
            for r in rows:
                if r["key"] in present_keys:
                    continue
                import json as _json
                srcs = set(_json.loads(r["sources"]))
                if srcs and srcs <= fetched_sources:
                    db.update_job(conn, r["id"], active=False)
                    report.deactivated += 1
    report.new = len(new_ids)
    return new_ids


def screen_and_ping(conn: sqlite3.Connection, cfg: Config, new_ids: list[int], *, notify: bool, seed: bool,
                    report: CollectReport) -> None:
    """Screen each new job; ping those that pass (up to the per-run cap), best companies first. Companies are
    scored before pinging, and a tier whose `tiers.ping` is false is ignored automatically instead (event
    `ignored_tier`, decided_at left empty so it reads as automatic; approve it from /screened to undo)."""
    pinged = 0
    cap = int(cfg.notify.max_pings_per_run)
    quiet = in_quiet_hours(cfg)
    # Deferred from an earlier tick, oldest first. 'missed_paused' jobs (the backlog that piled up while the
    # pipeline was paused) are review-only: they live in the dashboard for manual review and are never auto-pinged.
    deferred_rows = conn.execute("SELECT id FROM jobs WHERE state='new' AND flags LIKE '%ping_deferred%' AND flags NOT LIKE '%missed_paused%' ORDER BY id").fetchall()
    candidates = [int(r["id"]) for r in deferred_rows] + [j for j in new_ids]
    seen: set[int] = set()
    to_ping: list[int] = []
    for jid in candidates:
        if jid in seen:
            continue
        seen.add(jid)
        job = db.job_by_id(conn, jid)
        if job is None or job.state != JobState.NEW:
            continue
        is_new = jid in new_ids
        if is_new:
            res = screen_metadata(job, cfg, _util.today())
            if res.verdict == "screen_out":
                transition(conn, jid, JobState.SCREENED_OUT, "screened", res.to_dict(),
                           extra_fields={"screen_reason": res.reason, "flags": res.flags})
                report.screened_out += 1
                continue
            flags = list(res.flags)
            if seed and bool(cfg.collector.seed_quiet):
                flags.append("seed")
                db.update_job(conn, jid, flags=flags)
                db.add_event(conn, jid, "screened", detail=res.to_dict())
                continue
            if bool(cfg.screen.fetch_jd_before_ping) and not job.jd_text:
                try:
                    jr = fetch_jd(job, cfg, allow_browser=False)
                    if jr.ok:
                        store_jd(conn, cfg, jid, jr)
                        job = db.job_by_id(conn, jid)
                except Exception as e:  # noqa: BLE001
                    log.info("pre-ping jd fetch failed for %s: %s", jid, e)
            if job and job.jd_text:
                jres = screen_jd(job, job.jd_text, cfg, _util.today())
                if jres.verdict == "screen_out":
                    transition(conn, jid, JobState.SCREENED_OUT, "screened", jres.to_dict(),
                               extra_fields={"screen_reason": jres.reason, "flags": flags + jres.flags})
                    report.screened_out += 1
                    continue
                for f in jres.flags:
                    if f not in flags:
                        flags.append(f)
            db.update_job(conn, jid, flags=flags)
            db.add_event(conn, jid, "screened", detail=res.to_dict())
        to_ping.append(jid)
    if not to_ping:
        return
    from .rank import company_tier, ensure_scores, get_score, tier_pings
    companies = {jid: (db.job_by_id(conn, jid).company or "") for jid in to_ping}
    if bool(cfg.get("rank", {}).get("enabled", True)):
        ensure_scores(conn, cfg, set(companies.values()))
    order = {jid: i for i, jid in enumerate(to_ping)}
    to_ping.sort(key=lambda j: (-get_score(conn, companies[j]), order[j]))
    for jid in to_ping:
        tier = company_tier(conn, cfg, companies[jid])
        if not tier_pings(cfg, tier):
            job = db.job_by_id(conn, jid)
            flags = [f for f in job.flags if f != "ping_deferred"]
            transition(conn, jid, JobState.PINGED, "pinged_state", {"auto": True})
            transition(conn, jid, JobState.IGNORED, "ignored_tier", {"tier": tier, "score": get_score(conn, companies[jid])},
                       extra_fields={"decided_at": None, "flags": flags})
            report.tier_ignored += 1
            continue
        # Ping or defer.
        if not notify or quiet or pinged >= cap:
            db.add_flag(conn, jid, "ping_deferred")
            report.deferred += 1
            continue
        job = db.job_by_id(conn, jid)
        assert job is not None
        if "ping_deferred" in job.flags:
            db.update_job(conn, jid, flags=[f for f in job.flags if f != "ping_deferred"])
            job = db.job_by_id(conn, jid)
        transition(conn, jid, JobState.PINGED, "pinged_state")
        notify_job(conn, cfg, job)  # type: ignore[arg-type]
        pinged += 1
        report.pinged += 1


def run_collect(conn: sqlite3.Connection, cfg: Config, *, notify: bool = True, only_sources: list[str] | None = None,
                tier2: bool = True) -> CollectReport:
    report = CollectReport(started_at=now_iso())
    seed = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    report.seed = seed
    listings, fetched = _fetch_tier1(conn, cfg, only_sources, report)
    if tier2 and not only_sources:
        l2, f2 = _fetch_tier2(conn, cfg, report)
        listings.extend(l2)
        fetched |= f2
    if not fetched and not listings:
        report.finished_at = now_iso()
        log.error("collect: all sources failed")
        return report
    new_ids = merge_listings(conn, listings, fetched, report)
    screen_and_ping(conn, cfg, new_ids, notify=notify, seed=seed, report=report)
    if bool(cfg.get("rank", {}).get("enabled", True)):
        from .rank import ensure_scores
        pinged = [r[0] for r in conn.execute("SELECT DISTINCT company FROM jobs WHERE state='pinged'")]
        ensure_scores(conn, cfg, pinged)
    report.finished_at = now_iso()
    log.info("collect: %s", report.summary())
    return report


class CollectorThread(threading.Thread):
    def __init__(self, cfg: Config, db_path, *, interval_minutes: float | None = None):
        super().__init__(name="collector", daemon=True)
        self.cfg = cfg
        self.db_path = db_path
        self.interval = float(interval_minutes or cfg.collector.interval_minutes) * 60.0
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.last_report: CollectReport | None = None
        self.last_error: str | None = None

    def trigger(self) -> None:
        self.wake.set()

    def run(self) -> None:
        conn = db.thread_conn(self.db_path)
        while not self.stop_event.is_set():
            try:
                self.last_report = run_collect(conn, self.cfg)
                self.last_error = None
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
                log.exception("collector tick failed")
            self.wake.clear()
            self.wake.wait(self.interval)

    def stop(self) -> None:
        self.stop_event.set()
        self.wake.set()
