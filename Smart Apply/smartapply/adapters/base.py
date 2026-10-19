"""Adapter contract. An adapter fills a page from an AnswerPacket and reports every field's outcome.
It never submits: the only way to click is through Actions, which refuses submit-like elements."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from ..answers import AnswerPacket
from ..browser.actions import Actions, Refused
from ..config import Config
from ..log import get_logger
from ..models import Answer, Application, FieldOutcome, FillReport, Question
from ..profile import Profile

log = get_logger("adapter")


class Adapter:
    ats: str = ""
    single_page: bool = True

    def __init__(self, cfg: Config, conn: sqlite3.Connection, app: Application, profile: Profile, page: Page,
                 actions: Actions):
        self.cfg = cfg
        self.conn = conn
        self.app = app
        self.profile = profile
        self.page = page
        self.act = actions
        self.report = FillReport(status="filling")

    # ---------------------------------------------------------------- contract

    @classmethod
    def start_url(cls, app: Application, questions: list[Question]) -> str:
        return app.url

    def review_reached(self, page: Page) -> bool:
        return False

    def fill(self, questions: list[Question], packet: AnswerPacket) -> FillReport:  # pragma: no cover - abstract
        raise NotImplementedError

    # ---------------------------------------------------------------- helpers

    @property
    def resume_path(self) -> Path:
        return self.app.workdir / self.profile.resume_filename

    def transcript_path(self) -> Path:
        """The profile's transcript, copied into the workdir (uploads are only allowed from there)."""
        import shutil
        src = Path(self.profile.transcript_path)
        dest = self.app.workdir / src.name
        if not dest.exists() or dest.stat().st_mtime < src.stat().st_mtime:
            shutil.copy2(src, dest)
        return dest

    def record(self, page: str, q: Question | None, name: str, intended: Any, observed: Any, status: str,
               note: str | None = None) -> FieldOutcome:
        fo = FieldOutcome(page=page, name=name, label=q.label if q else name, intended=intended, observed=observed,
                          status=status, note=note)
        self.report.fields.append(fo)
        return fo

    def guarded(self, fn, *a, **kw):
        """Run an action; Refused and lookup errors become field outcomes instead of crashes."""
        try:
            return fn(*a, **kw), None
        except Refused as e:
            return None, f"refused: {e}"
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if len(msg) > 500:
                msg = msg[:250] + " ... " + msg[-250:]
            return None, f"{type(e).__name__}: {msg}"

    def recheck_upload(self, name: str, shown, reupload) -> None:
        """Read an uploaded file's chip once more after every other field is filled: a form can drop an accepted
        upload when it re-renders (Ashby, Harvey 2026-10-05: the fill read the filename back, the page later showed
        "Upload File" again). Re-upload once; if it still does not show, the field is failed so it is never missed.
        `shown()` -> bool reads the page; `reupload()` -> str uploads and returns the shown name."""
        rec = next((f for f in self.report.fields if f.name == name and f.status == "filled"), None)
        if rec is None:
            return
        try:
            if shown():
                return
        except Exception:  # noqa: BLE001
            pass
        (obs, err) = self.guarded(reupload)
        ok = False
        if obs and not err:
            self.page.wait_for_timeout(1500)
            try:
                ok = bool(shown())
            except Exception:  # noqa: BLE001
                ok = False
        if ok:
            rec.observed, rec.note = obs, "re-uploaded at the end: the form had dropped it"
        else:
            rec.status, rec.observed, rec.note = "failed", None, f"upload not shown on the form at the end of the fill{': ' + err if err else ''}"
        self.report.notes.append(f"{name}: {'re-uploaded' if ok else 'missing'} at the final check")

    def finish(self, packet: AnswerPacket) -> FillReport:
        failed_required = [f for f in self.report.fields if f.status in ("failed",)]
        left = [f for f in self.report.fields if f.status == "left_for_human"]
        if failed_required:
            self.report.status = "needs_human"
            self.report.reason = "adapter_error"
            self.report.notes.append(f"{len(failed_required)} field(s) failed: " + ", ".join(f.label for f in failed_required[:6]))
        elif packet.unanswered_required or left:
            self.report.status = "needs_human"
            self.report.reason = "unanswered_required"
            self.report.notes.append("left for you: " + ", ".join(f.label for f in left[:8]))
        else:
            self.report.status = "ready_for_review"
        return self.report
