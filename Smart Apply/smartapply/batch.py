"""Batch mode: pick the next N pipeline jobs that have an adapter, fill them one after another in the parked
SmartApply window, then toast once so Alex reviews the whole batch in one sitting."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import db, intake, tracker
from .browser.chrome import Chrome
from .config import Config
from .log import get_logger
from .models import Application, AppState, FillReport
from .notify import batch_ready_toast
from .profile import Profile

log = get_logger("batch")


@dataclass
class BatchItem:
    job_id: int
    company: str
    title: str
    ats: str | None
    app: Application | None = None
    report: FillReport | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        return self.report.status if self.report else "pending"


def pick_candidates(cfg: Config, conn: sqlite3.Connection, *, n: int, ats_filter: list[str] | None = None,
                    job_ids: list[int] | None = None, include_agent: bool | None = None) -> list[sqlite3.Row]:
    """Pipeline jobs not yet queued here that an adapter (or, when `include_agent`, the Claude fallback agent)
    can fill, in the pipeline's priority order. `--ats agent` selects only agent-handled sites."""
    if include_agent is None:
        include_agent = bool(cfg.batch.get("include_agent", True)) or bool(ats_filter and "agent" in ats_filter)
    from .adapters import for_ats as _for_ats
    queued = {r["job_id"] for r in db.all_applications(conn)}
    with intake.open_pipeline_db(cfg) as pconn:
        if job_ids:
            rows = [r for jid in job_ids if (r := intake.pipeline_job(pconn, int(jid))) is not None]
        else:
            rows = list(intake.candidate_jobs(pconn))
    out = []
    for r in rows:
        if not job_ids and int(r["id"]) in queued:
            continue
        ats = r["ats"] or intake.detect_ats(r["url"])[0]
        cls = _for_ats(ats)
        if cls is None:
            continue
        if getattr(cls, "external", False) and not include_agent:
            continue
        if ats_filter and not (ats in ats_filter or (getattr(cls, "external", False) and "agent" in ats_filter)):
            continue
        out.append(r)
        if len(out) >= n:
            break
    if cfg.batch.get("single_page_last", True):
        # Workday drafts live on the server and survive anything; Greenhouse/Lever/Ashby live only in the open
        # tab, so they are filled last (closest to the moment Alex sits down).
        def _order(r) -> int:
            cls = _for_ats(r["ats"] or intake.detect_ats(r["url"])[0])
            if getattr(cls, "external", False):
                return 2          # the Claude agent: slowest and costliest, and its state also lives only in the tab
            return 1 if getattr(cls, "single_page", True) else 0
        out.sort(key=_order)
    return out


def run_batch(cfg: Config, conn: sqlite3.Connection, profile: Profile, chrome: Chrome, *, n: int,
              ats_filter: list[str] | None = None, job_ids: list[int] | None = None, use_claude: bool = True,
              notify: bool = True, include_agent: bool | None = None) -> list[BatchItem]:
    from .filler import fill_application
    rows = pick_candidates(cfg, conn, n=n, ats_filter=ats_filter, job_ids=job_ids, include_agent=include_agent)
    items: list[BatchItem] = []
    from . import usage
    for r in rows:
        if use_claude:
            # a fill with Claude blocked would leave every written answer for Alex; stop the batch instead
            blocked = usage.brake_reason(cfg)
            if blocked:
                log.info("batch stopped by the usage brake: %s", blocked)
                break
        item = BatchItem(job_id=int(r["id"]), company=r["company"], title=r["title"], ats=r["ats"] or intake.detect_ats(r["url"])[0])
        items.append(item)
        try:
            item.app = intake.enqueue_job(conn, cfg, r, profile_resume_name=profile.resume_filename)
        except ValueError as e:
            item.error = str(e)
            log.warning("batch: skip job %s: %s", item.job_id, e)
            continue
        if item.app.state in (AppState.APPLIED, AppState.SKIPPED, AppState.SELF_APPLY):
            item.error = f"already {item.app.state.value}"
            continue
        try:
            item.report = fill_application(cfg, conn, item.app, profile, chrome, use_claude=use_claude)
        except Exception as e:  # noqa: BLE001
            log.exception("batch: fill crashed for job %s", item.job_id)
            item.error = f"{type(e).__name__}: {str(e)[:200]}"
            try:
                tracker.fail(conn, item.app.id, item.error)
            except Exception:  # noqa: BLE001
                pass
    if notify and items:
        c = db.count_by_state(conn)
        batch_ready_toast(cfg, c.get("ready_for_review", 0), c.get("needs_human", 0))
    return items


def summary_lines(items: list[BatchItem]) -> list[str]:
    lines = []
    for it in items:
        left = ""
        if it.report is not None:
            lf = it.report.left_for_human
            left = f"  left for you: {', '.join(f.label[:40] for f in lf[:6])}" if lf else ""
        lines.append(f"{it.job_id:>6}  {it.ats or '?':<11} {it.company[:26]:<26} {it.status:<17}{it.error or ''}{left}")
    return lines


def to_dicts(items: list[BatchItem]) -> list[dict[str, Any]]:
    return [{"job_id": i.job_id, "company": i.company, "title": i.title, "ats": i.ats, "status": i.status, "error": i.error,
             "app_id": i.app.id if i.app else None,
             "left_for_human": [f.label for f in (i.report.left_for_human if i.report else [])]} for i in items]
