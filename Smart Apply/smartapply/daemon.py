"""The long-running process: dashboard + a single fill worker + periodic applied detection.

One Chrome, one worker thread (Playwright sync API is single-threaded per connection), a queue of application ids.
Nothing here ever raises a window; `open_review` is called only from the dashboard route the toast points at.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any

from playwright.sync_api import sync_playwright

from . import applied, db, tracker
from .browser.chrome import Chrome
from .config import Config
from .log import get_logger
from .models import Application, AppState
from .notify import batch_ready_toast
from .profile import Profile

log = get_logger("daemon")


class Runner:
    def __init__(self, cfg: Config, profile: Profile):
        self.cfg = cfg
        self.profile = profile
        self.chrome = Chrome(cfg)
        self.q: "queue.Queue[int]" = queue.Queue()
        self.active: int | None = None
        self.stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="filler", daemon=True)
        self._poll = threading.Thread(target=self._poll_loop, name="applied-poll", daemon=True)
        self.fill_fn = None   # set by phase 1: callable(cfg, conn, app, profile, chrome) -> FillReport

    def conn(self):
        return db.thread_conn(self.cfg.db_path)

    def start(self) -> None:
        self._thread.start()
        self._poll.start()

    # ---------------------------------------------------------------- dashboard hooks

    def enqueue_fill(self, app_id: int) -> None:
        self.q.put(app_id)

    def open_review(self) -> None:
        """Restore the SmartApply window. The one place a window is raised, and only because Alex clicked."""
        if not self.chrome.is_running():
            return
        with sync_playwright() as pw:
            browser = self.chrome.attach(pw)
            tids = self.chrome.unpark_all(browser)
            if tids:
                page = self.chrome.page_for_target(browser, tids[0])
                if page is not None:
                    page.bring_to_front()
            browser.close()

    def mark_applied(self, app_id: int, *, source: str) -> bool:
        return applied.mark_applied(self.conn(), self.cfg, app_id, source=source)

    # ---------------------------------------------------------------- worker

    def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                app_id = self.q.get(timeout=1.0)
            except queue.Empty:
                continue
            self.active = app_id
            try:
                self._fill_one(app_id)
            except Exception as e:  # noqa: BLE001
                log.exception("fill %s crashed: %s", app_id, e)
                try:
                    tracker.fail(self.conn(), app_id, f"crash: {type(e).__name__}: {e}")
                except Exception:  # noqa: BLE001
                    pass
            finally:
                self.active = None
                if self.q.empty():
                    self._maybe_toast()

    def _fill_one(self, app_id: int) -> None:
        conn = self.conn()
        app = tracker.load(conn, app_id)
        if app.state in (AppState.APPLIED, AppState.SKIPPED):
            return
        if self.fill_fn is None:
            tracker.needs_human(conn, app_id, "adapter_error", {"note": "no filler wired (phase 0)"})
            return
        self.fill_fn(self.cfg, conn, app, self.profile, self.chrome)

    def _maybe_toast(self) -> None:
        c = db.count_by_state(self.conn())
        ready, nh = c.get("ready_for_review", 0), c.get("needs_human", 0)
        if ready or nh:
            batch_ready_toast(self.cfg, ready, nh)

    # ---------------------------------------------------------------- applied detection (no CDP attach: /json only)

    def _poll_loop(self) -> None:
        while not self.stop.is_set():
            try:
                self._poll_once()
            except Exception as e:  # noqa: BLE001
                log.debug("poll error: %s", e)
            self.stop.wait(20.0)

    def _poll_once(self) -> None:
        conn = self.conn()
        applied.retry_pending_pipeline_marks(conn, self.cfg)
        if not self.chrome.is_running():
            return
        tabs = {t["id"]: t for t in self.chrome.list_tabs()}
        for row in db.applications_in_states(conn, ("ready_for_review", "needs_human")):
            app = Application.from_row(row)
            if not app.target_id or app.target_id not in tabs:
                continue
            url = tabs[app.target_id].get("url", "")
            if url != app.tab_url:
                db.update_application(conn, app.id, tab_url=url)
            if applied.url_confirms(app.ats, url):
                log.info("confirmation URL seen for app %s: %s", app.id, url)
                applied.mark_applied(conn, self.cfg, app.id, source="confirmation_url")
