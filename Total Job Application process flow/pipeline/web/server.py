"""Dashboard HTTP server: routes, actions, and the toast decide endpoint."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .. import db, usage
from .. import watchlist as wl
from ..collector import CollectorThread
from ..config import TIERS, Config
from ..dedupe import fuzzy_key
from ..jd import fetch_jd, store_jd
from ..models import BACKLOG_STATES, Job, JobState, Listing, Stage
from ..roles import ensure_role_folder
from ..sources.base import normalize_category
from ..statemachine import IllegalTransition, retry_target, stage_after, transition
from ..urlnorm import ats_identity, canonical_key
from ..util import now_iso, tail_lines
from ..worker import WorkerPool
from . import views

log = logging.getLogger("pipeline.web")


def done_lane(job: Job) -> str:
    """The lane a finished job actually ran, from its meta.json (full when absent)."""
    try:
        return str(json.loads((Path(job.role_folder) / "meta.json").read_text(encoding="utf-8")).get("lane") or "full")
    except (OSError, ValueError, TypeError):
        return "full"
STATIC_DIR = Path(__file__).resolve().parent / "static"


class AppState:
    def __init__(self, cfg: Config, *, collector: CollectorThread | None = None, worker: WorkerPool | None = None):
        self.cfg = cfg
        views.DOLLARS_PER_PCT = float(usage.settings(cfg)["dollars_per_percent"])
        self.db_path = cfg.db_path
        self.collector = collector
        self.worker = worker
        self.started_at = now_iso()

    def conn(self):
        return db.thread_conn(self.db_path)

    def enqueue(self, job_id: int, stop_after: Stage | None = None) -> bool:
        if self.worker is None:
            return False
        return self.worker.enqueue(job_id, stop_after=stop_after)

    def queued(self) -> set[int]:
        if self.worker is None:
            return set()
        return set(self.worker.queue_snapshot()) | set(self.worker.active.keys())


Route = tuple[str, re.Pattern, Callable]


class Handler(BaseHTTPRequestHandler):
    state: AppState  # set per server via subclassing in make_server
    server_version = "JobPipeline/1.0"

    # ------------------------------------------------------------------ plumbing
    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("%s " + fmt, self.address_string(), *args)

    def _send(self, body: str | bytes, status: int = 200, ctype: str = "text/html; charset=utf-8", headers: dict | None = None) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _form(self) -> dict[str, list[str]]:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8") if n else ""
        return urllib.parse.parse_qs(raw, keep_blank_values=True)

    def _check_csrf(self, form: dict[str, list[str]]) -> bool:
        token = db.get_setting(self.state.conn(), "csrf_token")
        return bool(token) and form.get("csrf", [""])[0] == token

    def _smartapply_url(self) -> str:
        try:
            return str(self.state.cfg.smartapply.url or "")
        except Exception:  # noqa: BLE001  (older config objects without the section)
            return ""

    def _counts(self) -> dict[str, int]:
        c = db.count_by_state(self.state.conn())
        return {"pinged": c.get("pinged", 0), "backlog": sum(c.get(s.value, 0) for s in BACKLOG_STATES),
                "running": sum(c.get(s, 0) for s in ("ideamaker_running", "building", "reconciling", "reviewing", "tailoring")),
                "done": c.get("done", 0)}

    def _badges(self, jobs, *, tier_only: bool = False) -> dict[int, str]:
        """job id -> "<tier> <lane>" for the dashboard. A finished job shows the lane it actually ran (meta.json)."""
        from ..rank import company_tier
        from ..worker import effective_lane
        conn, cfg = self.state.conn(), self.state.cfg
        out: dict[int, str] = {}
        tiers: dict[str, str] = {}
        for j in jobs:
            try:
                if j.company not in tiers:
                    tiers[j.company] = company_tier(conn, cfg, j.company)
                tier = tiers[j.company]
                if tier_only:
                    out[j.id] = tier
                    continue
                out[j.id] = f"{tier} {done_lane(j) if j.state == JobState.DONE else effective_lane(conn, cfg, j)}"
            except Exception:  # noqa: BLE001
                continue
        return out

    def _token(self) -> str:
        return db.get_setting(self.state.conn(), "csrf_token") or ""

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        for m, pattern, fn in ROUTES:
            if m != method:
                continue
            mm = pattern.fullmatch(path)
            if mm:
                try:
                    fn(self, query, *mm.groups())
                except IllegalTransition as e:
                    self._send(views.message_page("Not allowed", str(e)), 409)
                except Exception as e:  # noqa: BLE001
                    log.exception("handler error for %s %s", method, path)
                    self._send(views.message_page("Error", f"{type(e).__name__}: {e}"), 500)
                return
        self._send(views.message_page("Not found", path), 404)

    # ------------------------------------------------------------------ pages
    def page_root(self, q, *a) -> None:
        self._redirect("/pending")

    def page_static(self, q, name: str) -> None:
        p = STATIC_DIR / name
        if not p.exists() or ".." in name:
            self._send("not found", 404, "text/plain")
            return
        self._send(p.read_bytes(), 200, "text/css; charset=utf-8" if name.endswith(".css") else "application/octet-stream")

    def page_pending(self, q) -> None:
        conn = self.state.conn()
        # Only surface listings that are still active (not expired/stale) for review.
        pinged = [j for j in db.jobs_in_states(conn, [JobState.PINGED], order="pinged_at DESC") if j.active]
        new = db.jobs_in_states(conn, [JobState.NEW], order="id DESC")
        deferred = [j for j in new if "ping_deferred" in j.flags and j.active]
        seeded = [j for j in new if "ping_deferred" not in j.flags and j.active]
        badges = self._badges(pinged + deferred, tier_only=True)
        order = {t: i for i, t in enumerate(TIERS)}

        def best_first(j):  # tier, then company score; the newest ping first within a tie (stable sort)
            return order.get(badges.get(j.id, ""), len(order)), -(j.priority or 0)
        pinged.sort(key=best_first)
        deferred.sort(key=best_first)
        self._send(views.pending_page(pinged, deferred, seeded, self._token(), self._counts(), badges=badges))

    def post_pending_bulk(self, q) -> None:
        form = self._form()
        if not self._check_csrf(form):
            self._send(views.message_page("Forbidden", "bad csrf token"), 403)
            return
        action = form.get("action", [""])[0]
        if action not in ("approve", "ignore"):
            self._send(views.message_page("Not allowed", "action must be approve or ignore"), 400)
            return
        conn = self.state.conn()
        for sid in form.get("ids", []):
            try:
                job = db.job_by_id(conn, int(sid))
            except ValueError:
                continue
            if job is None:
                continue
            if action == "approve":
                if job.state in (JobState.NEW, JobState.PINGED, JobState.IGNORED):
                    transition(conn, job.id, JobState.APPROVED, "approved_dashboard", {"bulk": True})
                    self.state.enqueue(job.id)
            elif job.state in (JobState.NEW, JobState.PINGED):
                if job.state == JobState.NEW:
                    transition(conn, job.id, JobState.PINGED, "pinged_state")
                transition(conn, job.id, JobState.IGNORED, "ignored_dashboard", {"bulk": True})
        self._redirect("/pending")

    def page_backlog(self, q) -> None:
        conn = self.state.conn()
        jobs = db.jobs_in_states(conn, list(BACKLOG_STATES), order="state_updated_at DESC")
        self._send(views.backlog_page(jobs, self._token(), self._counts(), sorted(self.state.queued()), badges=self._badges(jobs)))

    def page_running(self, q) -> None:
        conn = self.state.conn()
        items = []
        for run in db.running_stage_runs(conn):
            job = db.job_by_id(conn, run.job_id)
            if not job:
                continue
            items.append({"run": run, "job": job, "tail": tail_lines(Path(run.log_path), 15), "cost": db.job_cost(conn, job.id),
                          "status": (self.state.worker.status.get(job.id, "") if self.state.worker else "")})
        pool = self.state.worker.snapshot() if self.state.worker else {"threads": 0}
        col: dict[str, Any] = {}
        if self.state.collector:
            col = {"last": self.state.collector.last_report.summary() if self.state.collector.last_report else None,
                   "error": self.state.collector.last_error}
        self._send(views.running_page(items, pool, col, self._counts(), usage.snapshot(self.state.cfg, conn)))

    def page_done(self, q) -> None:
        conn = self.state.conn()
        items = []
        for job in db.jobs_in_states(conn, [JobState.DONE], order="state_updated_at DESC"):
            folder = Path(job.role_folder) if job.role_folder else None
            pdfs = [str(p) for p in sorted(folder.glob("*.pdf"))] if folder and folder.exists() else []
            review = str(folder / "review.md") if folder and (folder / "review.md").exists() else None
            verdict = ""
            repos: list[str] = []
            if folder and (folder / "review.json").exists():
                try:
                    rj = json.loads((folder / "review.json").read_text(encoding="utf-8"))
                    verdict = f"review {rj.get('verdict')} {rj.get('score', '')}"
                except json.JSONDecodeError:
                    pass
            if folder and (folder / "build_report.json").exists():
                try:
                    br = json.loads((folder / "build_report.json").read_text(encoding="utf-8"))
                    repos = [p.get("repo_url") for p in br.get("projects", []) if isinstance(p, dict) and p.get("repo_url")]
                except json.JSONDecodeError:
                    pass
            applied_at = None
            if "applied" in job.flags:
                ev = [e for e in db.events_for_job(conn, job.id) if e["event"] == "applied"]
                if ev:
                    import json as _json
                    try:
                        applied_at = (_json.loads(ev[-1]["detail"] or "{}") or {}).get("at")
                    except (ValueError, TypeError):
                        applied_at = None
            items.append({"job": job, "pdfs": pdfs, "review": review, "verdict": verdict, "repos": repos,
                          "badge": self._badges([job]).get(job.id, ""),
                          "cost": db.job_cost(conn, job.id), "applied": "applied" in job.flags, "applied_at": applied_at})
        self._send(views.done_page(items, self._token(), self._counts(), smartapply_url=self._smartapply_url()))

    def page_screened(self, q) -> None:
        conn = self.state.conn()
        jobs = db.jobs_in_states(conn, [JobState.SCREENED_OUT, JobState.IGNORED], order="state_updated_at DESC", limit=500)
        self._send(views.screened_page(jobs, self._token(), self._counts(),
                                       badges=self._badges([j for j in jobs if j.state == JobState.IGNORED], tier_only=True)))

    def page_add(self, q) -> None:
        self._send(views.add_page(self._token(), self._counts(), q.get("msg", "")))

    def post_add(self, q) -> None:
        form = self._form()
        if not self._check_csrf(form):
            self._send(views.message_page("Forbidden", "bad csrf token"), 403)
            return
        g = lambda k: form.get(k, [""])[0].strip()  # noqa: E731
        url, company, title = g("url"), g("company"), g("title")
        if not (url and company and title):
            self._redirect("/add?msg=company%2C+title+and+url+are+required")
            return
        jd = g("jd")
        conn = self.state.conn()
        ats, ats_id = ats_identity(url)
        listing = Listing(source="manual", company=company, title=title, url=url,
                          locations=[g("location")] if g("location") else [], category=normalize_category(g("category"), title),
                          jd_text=jd or None, ats=ats, ats_id=ats_id)
        key = canonical_key(url)
        existing = db.job_by_key(conn, key)
        if existing:
            jid = existing.id
            if jd and not existing.jd_text:
                db.update_job(conn, jid, jd_text=jd, jd_source="manual", jd_fetched_at=now_iso())
            if existing.state in (JobState.NEW, JobState.PINGED, JobState.IGNORED, JobState.SCREENED_OUT):
                transition(conn, jid, JobState.APPROVED, "approved_manual")
        else:
            jid = db.insert_job(conn, listing, key, fuzzy_key(company, title, listing.locations), state=JobState.APPROVED)
            if jd:
                db.update_job(conn, jid, jd_source="manual")
            db.add_event(conn, jid, "discovered", None, "approved", {"source": "manual"})
        self.state.enqueue(jid)
        self._redirect(f"/job/{jid}")

    def page_watchlist(self, q) -> None:
        self._send(views.watchlist_page(db.watchlist_all(self.state.conn()), self._token(), self._counts()))

    def post_watchlist_add(self, q) -> None:
        form = self._form()
        if not self._check_csrf(form):
            self._send(views.message_page("Forbidden", "bad csrf token"), 403)
            return
        g = lambda k: form.get(k, [""])[0].strip() or None  # noqa: E731
        try:
            poll = int(g("poll_minutes") or 60)
        except ValueError:
            poll = 60
        conn = self.state.conn()
        if not db.watchlist_find(conn, g("ats") or "", g("board"), g("host"), g("tenant"), g("site")):
            db.watchlist_insert(conn, company=g("company") or "", ats=g("ats") or "greenhouse", board=g("board"), host=g("host"),
                                tenant=g("tenant"), site=g("site"), added_by="manual", poll_minutes=poll)
        self._redirect("/watchlist")

    def post_watchlist_action(self, q, wid: str, action: str) -> None:
        form = self._form()
        if not self._check_csrf(form):
            self._send(views.message_page("Forbidden", "bad csrf token"), 403)
            return
        conn = self.state.conn()
        entry = db.watchlist_by_id(conn, int(wid))
        if entry is None:
            self._send(views.message_page("Not found", "no such watchlist entry"), 404)
            return
        if action == "toggle":
            db.watchlist_update(conn, entry.id, enabled=not entry.enabled)
        elif action == "delete":
            db.watchlist_delete(conn, entry.id)
        elif action == "poll":
            db.watchlist_update(conn, entry.id, last_polled_at=None)
            if self.state.collector:
                self.state.collector.trigger()
        self._redirect("/watchlist")

    def page_costs(self, q) -> None:
        conn = self.state.conn()
        self._send(views.costs_page(db.cost_totals(conn), self._counts(), usage.snapshot(self.state.cfg, conn)))

    def page_job(self, q, jid: str) -> None:
        conn = self.state.conn()
        job = db.job_by_id(conn, int(jid))
        if job is None:
            self._send(views.message_page("Not found", "no such job"), 404)
            return
        artifacts: list[str] = []
        if job.role_folder and Path(job.role_folder).exists():
            folder = Path(job.role_folder)
            for pat in ("*.pdf", "experience_descriptions_*.md", "review.md", "reconciliation.md", "build_report.json", "no_brainer_hire_*.md"):
                artifacts += [str(p) for p in sorted(folder.glob(pat))]
        self._send(views.job_page(job, db.stage_runs_for_job(conn, job.id), db.events_for_job(conn, job.id), self._token(),
                                  self._counts(), db.job_cost(conn, job.id), artifacts, job.id in self.state.queued(),
                                  smartapply_url=self._smartapply_url(), badge=self._badges([job]).get(job.id, "")))

    def page_job_log(self, q, jid: str) -> None:
        conn = self.state.conn()
        runs = db.stage_runs_for_job(conn, int(jid))
        stage = q.get("stage")
        attempt = q.get("attempt")
        sel = [r for r in runs if (not stage or r.stage == stage) and (not attempt or str(r.attempt) == attempt)]
        if not sel:
            self._send("no such run", 404, "text/plain; charset=utf-8")
            return
        n = int(q.get("tail", "400") or 400)
        self._send("\n".join(tail_lines(Path(sel[-1].log_path), n)), 200, "text/plain; charset=utf-8")

    def post_job_action(self, q, jid: str, action: str) -> None:
        form = self._form()
        if not self._check_csrf(form):
            self._send(views.message_page("Forbidden", "bad csrf token"), 403)
            return
        conn = self.state.conn()
        job = db.job_by_id(conn, int(jid))
        if job is None:
            self._send(views.message_page("Not found", "no such job"), 404)
            return
        if action == "approve":
            if job.state in (JobState.NEW, JobState.PINGED, JobState.IGNORED):
                transition(conn, job.id, JobState.APPROVED, "approved_dashboard")
            self.state.enqueue(job.id)
        elif action == "ignore":
            if job.state == JobState.NEW:
                transition(conn, job.id, JobState.PINGED, "pinged_state")
            transition(conn, job.id, JobState.IGNORED, "ignored_dashboard")
        elif action == "override":
            transition(conn, job.id, JobState.APPROVED, "override", {"previous_reason": job.screen_reason})
            self.state.enqueue(job.id)
        elif action == "retry":
            tgt = retry_target(job)
            if tgt is None:
                self._send(views.message_page("Not allowed", "job is not in a failed state"), 409)
                return
            transition(conn, job.id, tgt, "retry")
            self.state.enqueue(job.id)
        elif action == "requeue":
            self.state.enqueue(job.id)
        elif action in ("applied", "unapplied"):
            flags = [f for f in job.flags if f != "applied"]
            if action == "applied":
                flags.append("applied")
            db.update_job(conn, job.id, flags=flags)
            db.add_event(conn, job.id, action, detail={"at": now_iso()})
            self._redirect(self.headers.get("Referer") or "/done")
            return
        elif action == "upgrade-full":
            self._upgrade_full(conn, job)
        elif action == "rereview":
            transition(conn, job.id, JobState.REVIEWING, "rereview")
            transition(conn, job.id, JobState.RECONCILED, "rereview_reset")
            self.state.enqueue(job.id)
        elif action == "jd":
            text = form.get("jd", [""])[0].strip()
            if not text:
                self._redirect(f"/job/{job.id}")
                return
            db.update_job(conn, job.id, jd_text=text, jd_source="manual", jd_fetched_at=now_iso())
            db.add_event(conn, job.id, "jd_fetched", detail={"source": "manual", "chars": len(text)})
            job = db.job_by_id(conn, job.id)
            if job.state == JobState.NEEDS_JD:
                folder = ensure_role_folder(job, self.state.cfg)
                transition(conn, job.id, JobState.JD_READY, "jd_ready", {"folder": str(folder)}, extra_fields={"role_folder": str(folder)})
            self.state.enqueue(job.id)
        elif action == "refetch-jd":
            res = fetch_jd(job, self.state.cfg, allow_browser=True)
            store_jd(conn, self.state.cfg, job.id, res)
            job = db.job_by_id(conn, job.id)
            if res.ok and job.state == JobState.NEEDS_JD:
                folder = ensure_role_folder(job, self.state.cfg)
                transition(conn, job.id, JobState.JD_READY, "jd_ready", {"folder": str(folder)}, extra_fields={"role_folder": str(folder)})
                self.state.enqueue(job.id)
        else:
            self._send(views.message_page("Not found", "unknown action"), 404)
            return
        self._redirect(f"/job/{job.id}")

    def _upgrade_full(self, conn, job: Job) -> None:
        """Force the full pipeline for a job the tiers routed to a lite lane. A finished lite resume keeps its folder
        (it may already have been sent); the full run starts in a fresh <folder>_full folder."""
        flags = [f for f in job.flags if not f.startswith("lane:") and f != "awaiting_lane"] + ["lane:full"]
        if job.state == JobState.DONE:
            from ..roles import write_job_json
            old = Path(job.role_folder) if job.role_folder else ensure_role_folder(job, self.state.cfg)
            new = old.with_name(old.name + "_full")
            n = 2
            while new.exists():
                new = old.with_name(f"{old.name}_full{n}")
                n += 1
            new.mkdir(parents=True)
            write_job_json(new, job, self.state.cfg)
            (new / "jd.txt").write_text(job.jd_text or "", encoding="utf-8")
            transition(conn, job.id, JobState.JD_READY, "upgrade_full", {"from_folder": str(old)},
                       extra_fields={"role_folder": str(new), "flags": flags})
        else:
            db.update_job(conn, job.id, flags=flags)
            db.add_event(conn, job.id, "upgrade_full", detail={"state": job.state.value})
            job = db.job_by_id(conn, job.id)
            if job.state == JobState.FAILED:
                tgt = retry_target(job)
                if tgt is not None:
                    transition(conn, job.id, tgt, "retry")
        self.state.enqueue(job.id)

    def post_backlog_run(self, q) -> None:
        form = self._form()
        if not self._check_csrf(form):
            self._send(views.message_page("Forbidden", "bad csrf token"), 403)
            return
        stop = form.get("stop_after", [""])[0].strip()
        stop_after = Stage(stop) if stop else None
        conn = self.state.conn()
        n = 0
        for sid in form.get("ids", []):
            try:
                job = db.job_by_id(conn, int(sid))
            except ValueError:
                continue
            if job is None:
                continue
            if job.state == JobState.FAILED:
                tgt = retry_target(job)
                if tgt is not None:
                    transition(conn, job.id, tgt, "retry")
            if self.state.enqueue(job.id, stop_after=stop_after):
                n += 1
        self._redirect("/backlog")

    def get_decide(self, q) -> None:
        conn = self.state.conn()
        try:
            jid = int(q.get("id", ""))
        except ValueError:
            self._send("bad request", 400, "text/plain")
            return
        job = db.job_by_id(conn, jid)
        if job is None or not job.decide_token or q.get("token") != job.decide_token:
            self._send("forbidden", 403, "text/plain")
            return
        yes = q.get("yes") == "1"
        already = job.state not in (JobState.NEW, JobState.PINGED)
        if not already:
            if job.state == JobState.NEW:
                transition(conn, jid, JobState.PINGED, "pinged_state")
            if yes:
                transition(conn, jid, JobState.APPROVED, "approved_toast")
                self.state.enqueue(jid)
            else:
                transition(conn, jid, JobState.IGNORED, "ignored_toast")
        job = db.job_by_id(conn, jid)
        self._send(views.decide_page(job, "approved" if yes else "ignored", already))

    def get_api_status(self, q) -> None:
        conn = self.state.conn()
        col = self.state.collector
        body = {
            "ok": True, "started_at": self.state.started_at, "counts": db.count_by_state(conn),
            "running": [{"job_id": r.job_id, "stage": r.stage, "attempt": r.attempt, "started_at": r.started_at, "heartbeat_at": r.heartbeat_at}
                        for r in db.running_stage_runs(conn)],
            "worker": self.state.worker.snapshot() if self.state.worker else None,
            "job_object": bool(self.state.worker and self.state.worker.job_object.handle) if self.state.worker else False,
            "collector": {"last": col.last_report.summary() if (col and col.last_report) else None, "error": col.last_error if col else None,
                          "interval_minutes": float(self.state.cfg.collector.interval_minutes)} if col else None,
            "sources": db.recent_source_runs(conn, 10),
            "usage": usage.snapshot(self.state.cfg, conn),
        }
        self._send(json.dumps(body, indent=1), 200, "application/json")

    def post_collect_now(self, q) -> None:
        form = self._form()
        if not self._check_csrf(form):
            self._send(views.message_page("Forbidden", "bad csrf token"), 403)
            return
        if self.state.collector:
            self.state.collector.trigger()
        self._redirect("/running")


ROUTES: list[Route] = [
    ("GET", re.compile(r"/"), Handler.page_root),
    ("GET", re.compile(r"/static/([A-Za-z0-9_.-]+)"), Handler.page_static),
    ("GET", re.compile(r"/pending"), Handler.page_pending),
    ("POST", re.compile(r"/pending/bulk"), Handler.post_pending_bulk),
    ("GET", re.compile(r"/backlog"), Handler.page_backlog),
    ("POST", re.compile(r"/backlog/run"), Handler.post_backlog_run),
    ("GET", re.compile(r"/running"), Handler.page_running),
    ("GET", re.compile(r"/done"), Handler.page_done),
    ("GET", re.compile(r"/screened"), Handler.page_screened),
    ("GET", re.compile(r"/add"), Handler.page_add),
    ("POST", re.compile(r"/add"), Handler.post_add),
    ("GET", re.compile(r"/watchlist"), Handler.page_watchlist),
    ("POST", re.compile(r"/watchlist/add"), Handler.post_watchlist_add),
    ("POST", re.compile(r"/watchlist/(\d+)/(toggle|delete|poll)"), Handler.post_watchlist_action),
    ("GET", re.compile(r"/costs"), Handler.page_costs),
    ("GET", re.compile(r"/job/(\d+)"), Handler.page_job),
    ("GET", re.compile(r"/job/(\d+)/log"), Handler.page_job_log),
    ("POST", re.compile(r"/job/(\d+)/(approve|ignore|override|retry|requeue|rereview|upgrade-full|jd|refetch-jd|applied|unapplied)"), Handler.post_job_action),
    ("GET", re.compile(r"/decide"), Handler.get_decide),
    ("GET", re.compile(r"/api/status"), Handler.get_api_status),
    ("POST", re.compile(r"/collect/now"), Handler.post_collect_now),
]


def make_server(state: AppState, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"state": state})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    return srv


def serve_in_thread(state: AppState, host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, threading.Thread]:
    srv = make_server(state, host, port)
    t = threading.Thread(target=srv.serve_forever, name="dashboard", daemon=True)
    t.start()
    return srv, t
