"""Dashboard HTTP server: queue, review list, needs-human list, applied list, costs, doctor, and the
protocol-activation targets the toasts point at (open-review)."""

from __future__ import annotations

import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .. import db, intake, tracker
from ..config import Config
from ..log import get_logger
from ..models import Application, AppState
from ..profile import Profile
from . import views

log = get_logger("web")
STATIC_DIR = Path(__file__).resolve().parent / "static"


class AppState_:
    """Shared server state. `runner` is set by the daemon (serve) so the dashboard can start fills."""

    def __init__(self, cfg: Config, *, profile: Profile | None = None, runner: Any = None):
        self.cfg = cfg
        self.profile = profile
        self.runner = runner        # object with enqueue_fill(app_id), open_review(), apply_marked(app_id)
        self.doctor: Callable[[], list[tuple[str, bool, str]]] | None = None

    def conn(self):
        return db.thread_conn(self.cfg.db_path)


Route = tuple[str, re.Pattern, Callable]


class Handler(BaseHTTPRequestHandler):
    state: AppState_
    server_version = "SmartApply/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("%s " + fmt, self.address_string(), *args)

    def _send(self, body: str | bytes, status: int = 200, ctype: str = "text/html; charset=utf-8") -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
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

    def _token(self) -> str:
        return db.get_setting(self.state.conn(), "csrf_token") or ""

    def _check_csrf(self, form: dict[str, list[str]]) -> bool:
        return form.get("csrf", [""])[0] == self._token()

    def _trusted_origin(self) -> bool:
        """A cross-site form POST is accepted only from the pipeline dashboard (or this dashboard): the browser's
        Origin header is the CSRF check for the one route another local dashboard is allowed to call."""
        origin = (self.headers.get("Origin") or "").rstrip("/").lower()
        if not origin:
            return False
        cfg = self.state.cfg
        allowed = {_origin_of(str(cfg.pipeline_dashboard)), f"http://{cfg.dashboard.host}:{cfg.dashboard.port}",
                   f"http://localhost:{cfg.dashboard.port}"}
        return origin in allowed

    def _counts(self) -> dict[str, int]:
        c = db.count_by_state(self.state.conn())
        return {"queued": c.get("queued", 0) + c.get("prefetched", 0) + c.get("drafted", 0), "filling": c.get("filling", 0),
                "review": c.get("ready_for_review", 0), "needs you": c.get("needs_human", 0), "applied": c.get("applied", 0)}

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        for m, pattern, fn in ROUTES:
            if m != method:
                continue
            mm = pattern.fullmatch(parsed.path)
            if mm:
                try:
                    fn(self, query, *mm.groups())
                except tracker.IllegalTransition as ex:
                    self._send(views.message_page("Not allowed", str(ex)), 409)
                except Exception as ex:  # noqa: BLE001
                    log.exception("handler error for %s %s", method, parsed.path)
                    self._send(views.message_page("Error", f"{type(ex).__name__}: {ex}"), 500)
                return
        self._send(views.message_page("Not found", parsed.path), 404)

    # ---------------------------------------------------------------- pages

    def page_root(self, q) -> None:
        self._redirect("/queue")

    def page_static(self, q, name: str) -> None:
        p = STATIC_DIR / name
        if ".." in name or not p.exists():
            self._send("not found", 404, "text/plain")
            return
        self._send(p.read_bytes(), 200, "text/css; charset=utf-8" if name.endswith(".css") else "application/octet-stream")

    def _apps(self, states: tuple[str, ...]) -> list[Application]:
        return [Application.from_row(r) for r in db.applications_in_states(self.state.conn(), states)]

    def page_queue(self, q) -> None:
        apps = self._apps(("queued", "prefetched", "drafted", "filling", "failed"))
        token = self._token()

        def acts(a: Application):
            out = []
            if a.state in (AppState.QUEUED, AppState.PREFETCHED, AppState.DRAFTED, AppState.FAILED):
                out.append(("fill", "Fill", "primary"))
            out.append(("skip", "Skip", "danger"))
            return out
        banner = "" if self.state.runner else "Daemon not running: Fill buttons are inactive. Start with `python -m smartapply serve`."
        self._send(views.list_page("Queue", apps, token, active="/queue", counts=self._counts(), actions_for=acts,
                                   banner=banner, refresh=15 if any(a.state == AppState.FILLING for a in apps) else None,
                                   footer='<p><a href="/candidates">Add jobs from the pipeline</a></p>'))

    def page_candidates(self, q) -> None:
        conn = self.state.conn()
        queued = {r["job_id"] for r in db.all_applications(conn)}
        try:
            with intake.open_pipeline_db(self.state.cfg) as pconn:
                rows = [dict(r) for r in intake.candidate_jobs(pconn) if int(r["id"]) not in queued]
        except FileNotFoundError as ex:
            self._send(views.message_page("Pipeline DB missing", str(ex)), 500)
            return
        self._send(views.candidates_page(rows, self._token(), self._counts()))

    def page_review(self, q) -> None:
        apps = self._apps(("ready_for_review",))

        def acts(a: Application):
            return [("applied", "Mark applied", "primary"), ("refill", "Refill", ""), ("skip", "Skip", "danger")]
        banner = "Review each tab in the SmartApply Chrome window, fix anything, then click Submit yourself."
        self._send(views.list_page("Ready for review", apps, self._token(), active="/review", counts=self._counts(),
                                   actions_for=acts, banner=banner))

    def page_needs_human(self, q) -> None:
        apps = self._apps(("needs_human",))

        def acts(a: Application):
            return [("fill", "Retry fill", "primary"), ("applied", "Mark applied", ""), ("skip", "Skip", "danger")]
        self._send(views.list_page("Needs you", apps, self._token(), active="/needs-human", counts=self._counts(), actions_for=acts))

    def page_done(self, q) -> None:
        apps = self._apps(("applied", "skipped"))

        def acts(a: Application):
            return [("requeue", "Requeue", "")] if a.state == AppState.SKIPPED else []
        self._send(views.list_page("Applied and skipped", apps, self._token(), active="/done", counts=self._counts(), actions_for=acts))

    def page_app(self, q, app_id: str) -> None:
        conn = self.state.conn()
        app = tracker.load(conn, int(app_id))
        files: dict[str, str] = {}
        for name in ("questions.json", "answers.json", "fill_report.json", "agent_result.json"):
            p = app.workdir / name
            if p.exists():
                files[name] = p.read_text(encoding="utf-8", errors="replace")[:20000]
        acts = [("fill", "Fill", "primary"), ("applied", "Mark applied", ""), ("skip", "Skip", "danger"), ("requeue", "Requeue", "")]
        self._send(views.app_page(app, db.events_for_app(conn, app.id), [dict(r) for r in db.fields_for_app(conn, app.id)],
                                  self._token(), self._counts(), files, acts))

    def page_costs(self, q) -> None:
        self._send(views.costs_page(db.cost_totals(self.state.conn()), self._counts()))

    def page_memory(self, q) -> None:
        rows = [dict(r) for r in db.recall_answers(self.state.conn())]
        self._send(views.memory_page(rows, self._token(), self._counts()))

    def page_doctor(self, q) -> None:
        checks = self.state.doctor() if self.state.doctor else [("doctor", False, "not wired")]
        self._send(views.doctor_page(checks, self._counts()))

    def get_open_review(self, q) -> None:
        """Toast target: restore the SmartApply Chrome window. The only place a window is ever raised."""
        if self.state.runner is not None:
            try:
                self.state.runner.open_review()
            except Exception as ex:  # noqa: BLE001
                log.warning("open_review failed: %s", ex)
        self._redirect("/review")

    def get_api_status(self, q) -> None:
        import json
        self._send(json.dumps({"counts": db.count_by_state(self.state.conn()), "daemon": self.state.runner is not None}),
                   200, "application/json")

    # ---------------------------------------------------------------- actions

    def post_queue_add(self, q) -> None:
        form = self._form()
        if not self._check_csrf(form):
            self._send(views.message_page("Forbidden", "bad csrf"), 403)
            return
        job_id = int(form.get("job_id", ["0"])[0])
        conn = self.state.conn()
        with intake.open_pipeline_db(self.state.cfg) as pconn:
            job = intake.pipeline_job(pconn, job_id)
        if job is None:
            self._send(views.message_page("Not found", f"pipeline job {job_id}"), 404)
            return
        name = self.state.profile.resume_filename if self.state.profile else "Resume.pdf"
        intake.enqueue_job(conn, self.state.cfg, job, profile_resume_name=name)
        self._redirect("/queue")

    def post_fill_from_pipeline(self, q) -> None:
        """The pipeline dashboard's "Fill application" button: queue the pipeline job here (if it is not already)
        and start its fill. Trusted by Origin, not by our CSRF token, which the pipeline cannot know."""
        form = self._form()
        if not (self._trusted_origin() or self._check_csrf(form)):
            self._send(views.message_page("Forbidden", "fill requests are accepted only from the pipeline dashboard"), 403)
            return
        try:
            job_id = int(form.get("job_id", ["0"])[0])
        except ValueError:
            job_id = 0
        conn = self.state.conn()
        row = db.application_by_job(conn, job_id)
        if row is None:
            try:
                with intake.open_pipeline_db(self.state.cfg) as pconn:
                    job = intake.pipeline_job(pconn, job_id)
            except FileNotFoundError as ex:
                self._send(views.message_page("Pipeline DB missing", str(ex)), 500)
                return
            if job is None:
                self._send(views.message_page("Not found", f"pipeline job {job_id}"), 404)
                return
            name = self.state.profile.resume_filename if self.state.profile else "Resume.pdf"
            try:
                app = intake.enqueue_job(conn, self.state.cfg, job, profile_resume_name=name)
            except (ValueError, FileNotFoundError) as ex:
                self._send(views.message_page("Cannot queue", str(ex)), 400)
                return
        else:
            app = Application.from_row(row)
        if app.state in (AppState.APPLIED, AppState.FILLING):
            self._redirect(f"/app/{app.id}")
            return
        if self.state.runner is None:
            self._send(views.message_page("Queued, daemon not running",
                                          f"{app.company} is queued here; start `python -m smartapply serve` "
                                          f"(or `python -m smartapply fill {job_id}`) to fill it."), 503)
            return
        if app.state == AppState.READY_FOR_REVIEW:
            tracker.transition(conn, app.id, AppState.DRAFTED, "refill_requested")
        elif app.state == AppState.SKIPPED:
            tracker.transition(conn, app.id, AppState.QUEUED, "requeued_from_pipeline")
        self.state.runner.enqueue_fill(app.id)
        self._redirect(f"/app/{app.id}")

    def post_memory_forget(self, q, memory_id: str) -> None:
        form = self._form()
        if not self._check_csrf(form):
            self._send(views.message_page("Forbidden", "bad csrf"), 403)
            return
        db.forget_answer(self.state.conn(), int(memory_id))
        self._redirect("/memory")

    def post_app_action(self, q, app_id: str, action: str) -> None:
        form = self._form()
        if not self._check_csrf(form):
            self._send(views.message_page("Forbidden", "bad csrf"), 403)
            return
        conn = self.state.conn()
        aid = int(app_id)
        app = tracker.load(conn, aid)
        if action == "skip":
            tracker.transition(conn, aid, AppState.SKIPPED, "skipped_by_human")
        elif action == "requeue":
            tracker.transition(conn, aid, AppState.QUEUED, "requeued_by_human")
        elif action == "applied":
            if self.state.runner is not None:
                self.state.runner.mark_applied(aid, source="human")
            else:
                tracker.transition(conn, aid, AppState.APPLIED, "applied_by_human")
        elif action in ("fill", "refill"):
            if self.state.runner is None:
                self._send(views.message_page("Daemon not running", "Start `python -m smartapply serve` to fill."), 503)
                return
            if app.state == AppState.READY_FOR_REVIEW:
                tracker.transition(conn, aid, AppState.DRAFTED, "refill_requested")
            self.state.runner.enqueue_fill(aid)
        else:
            self._send(views.message_page("Unknown action", action), 404)
            return
        self._redirect(self.headers.get("Referer") or "/queue")


ROUTES: list[Route] = [
    ("GET", re.compile(r"/"), Handler.page_root),
    ("GET", re.compile(r"/static/([\w.-]+)"), Handler.page_static),
    ("GET", re.compile(r"/queue"), Handler.page_queue),
    ("GET", re.compile(r"/candidates"), Handler.page_candidates),
    ("GET", re.compile(r"/review"), Handler.page_review),
    ("GET", re.compile(r"/needs-human"), Handler.page_needs_human),
    ("GET", re.compile(r"/done"), Handler.page_done),
    ("GET", re.compile(r"/app/(\d+)"), Handler.page_app),
    ("GET", re.compile(r"/costs"), Handler.page_costs),
    ("GET", re.compile(r"/memory"), Handler.page_memory),
    ("POST", re.compile(r"/memory/(\d+)/forget"), Handler.post_memory_forget),
    ("POST", re.compile(r"/fill"), Handler.post_fill_from_pipeline),
    ("GET", re.compile(r"/doctor"), Handler.page_doctor),
    ("GET", re.compile(r"/open-review"), Handler.get_open_review),
    ("GET", re.compile(r"/api/status"), Handler.get_api_status),
    ("POST", re.compile(r"/queue/add"), Handler.post_queue_add),
    ("POST", re.compile(r"/app/(\d+)/(skip|requeue|applied|fill|refill)"), Handler.post_app_action),
]


def _origin_of(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    return f"{u.scheme}://{u.netloc}".lower()


def make_server(state: AppState_, host: str, port: int) -> ThreadingHTTPServer:
    bound = type("BoundHandler", (Handler,), {"state": state})
    srv = ThreadingHTTPServer((host, port), bound)
    srv.daemon_threads = True
    return srv


def serve_in_thread(state: AppState_, host: str, port: int) -> ThreadingHTTPServer:
    srv = make_server(state, host, port)
    t = threading.Thread(target=srv.serve_forever, name="dashboard", daemon=True)
    t.start()
    log.info("dashboard on http://%s:%s", host, port)
    return srv
