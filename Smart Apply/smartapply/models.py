"""Application states and the dataclasses the rest of the package passes around."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class AppState(str, Enum):
    QUEUED = "queued"
    PREFETCHED = "prefetched"
    DRAFTED = "drafted"
    FILLING = "filling"
    READY_FOR_REVIEW = "ready_for_review"
    NEEDS_HUMAN = "needs_human"
    APPLIED = "applied"
    SKIPPED = "skipped"
    SELF_APPLY = "self_apply"      # Alex applies by hand (selfapply.py); fills and batches skip it
    FAILED = "failed"


TERMINAL_STATES = {AppState.APPLIED, AppState.SKIPPED}

# Every legal move. A crash mid-fill goes back to DRAFTED (Workday) or FILLING resumes from the draft.
TRANSITIONS: dict[AppState, set[AppState]] = {
    AppState.QUEUED: {AppState.PREFETCHED, AppState.FAILED, AppState.SKIPPED, AppState.NEEDS_HUMAN, AppState.SELF_APPLY},
    AppState.PREFETCHED: {AppState.DRAFTED, AppState.FAILED, AppState.SKIPPED, AppState.NEEDS_HUMAN, AppState.QUEUED, AppState.SELF_APPLY},
    AppState.DRAFTED: {AppState.FILLING, AppState.FAILED, AppState.SKIPPED, AppState.NEEDS_HUMAN, AppState.PREFETCHED, AppState.SELF_APPLY},
    AppState.FILLING: {AppState.READY_FOR_REVIEW, AppState.NEEDS_HUMAN, AppState.FAILED, AppState.DRAFTED, AppState.SKIPPED, AppState.SELF_APPLY},
    AppState.READY_FOR_REVIEW: {AppState.APPLIED, AppState.SKIPPED, AppState.DRAFTED, AppState.NEEDS_HUMAN, AppState.FILLING, AppState.SELF_APPLY},
    AppState.NEEDS_HUMAN: {AppState.PREFETCHED, AppState.DRAFTED, AppState.FILLING, AppState.READY_FOR_REVIEW, AppState.APPLIED,
                           AppState.SKIPPED, AppState.FAILED, AppState.QUEUED, AppState.SELF_APPLY},
    AppState.FAILED: {AppState.QUEUED, AppState.DRAFTED, AppState.SKIPPED, AppState.SELF_APPLY},
    AppState.APPLIED: set(),
    AppState.SELF_APPLY: {AppState.APPLIED, AppState.SKIPPED, AppState.QUEUED},
    AppState.SKIPPED: {AppState.QUEUED, AppState.SELF_APPLY},
}

NEEDS_HUMAN_REASONS = ("captcha", "login", "verification_pending", "unanswered_required", "unknown_site",
                       "adapter_error", "post_submit_code")


@dataclass
class Application:
    id: int
    job_id: int
    company: str
    title: str
    url: str
    ats: str | None
    ats_id: str | None
    ats_params: dict
    role_folder: Path
    slug: str
    workdir: Path
    state: AppState
    reason: str | None = None
    target_id: str | None = None
    tab_url: str | None = None
    tenant: str | None = None
    generation: str | None = None
    application_id: str | None = None
    last_step: int = 0
    created_at: str = ""
    updated_at: str = ""
    ready_at: str | None = None
    applied_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Application":
        d = dict(row)
        params = d.get("ats_params") or "{}"
        return cls(
            id=d["id"], job_id=d["job_id"], company=d["company"], title=d["title"], url=d["url"],
            ats=d.get("ats"), ats_id=d.get("ats_id"), ats_params=json.loads(params) if isinstance(params, str) else params,
            role_folder=Path(d["role_folder"]), slug=d["slug"], workdir=Path(d["workdir"]),
            state=AppState(d["state"]), reason=d.get("reason"), target_id=d.get("target_id"), tab_url=d.get("tab_url"),
            tenant=d.get("tenant"), generation=d.get("generation"), application_id=d.get("application_id"),
            last_step=int(d.get("last_step") or 0), created_at=d.get("created_at") or "", updated_at=d.get("updated_at") or "",
            ready_at=d.get("ready_at"), applied_at=d.get("applied_at"),
        )

    # Conventional file names inside the workdir.
    @property
    def questions_path(self) -> Path:
        return self.workdir / "questions.json"

    @property
    def answers_path(self) -> Path:
        return self.workdir / "answers.json"

    @property
    def fill_report_path(self) -> Path:
        return self.workdir / "fill_report.json"

    @property
    def job_json_path(self) -> Path:
        return self.workdir / "job.json"


@dataclass
class Question:
    """One normalized application question, whatever the ATS calls it."""
    id: str                       # ATS field id / name (Greenhouse `question_123`, Workday questionnaire id, Lever `cards[..][field0]`)
    label: str
    type: str                     # text | textarea | select | multiselect | boolean | file | date | number | hidden
    required: bool = False
    options: list[str] = field(default_factory=list)
    section: str = ""             # e.g. "Voluntary Self-Identification", "My Experience"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "type": self.type, "required": self.required,
                "options": self.options, "section": self.section, "extra": self.extra}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Question":
        return cls(id=str(d["id"]), label=str(d.get("label", "")), type=str(d.get("type", "text")),
                   required=bool(d.get("required", False)), options=list(d.get("options") or []),
                   section=str(d.get("section", "")), extra=dict(d.get("extra") or {}))


@dataclass
class Answer:
    """What the filler will type. `needs_human` means it types nothing and flags the field."""
    question_id: str
    value: Any = None             # str | list[str] | bool | None
    source: str = "profile"       # profile | experience | memory | claude | needs_human
    needs_human: str | None = None
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {"question_id": self.question_id, "value": self.value, "source": self.source,
                "needs_human": self.needs_human, "confidence": self.confidence}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Answer":
        return cls(question_id=str(d["question_id"]), value=d.get("value"), source=str(d.get("source", "claude")),
                   needs_human=d.get("needs_human"), confidence=float(d.get("confidence", 1.0)))


@dataclass
class FieldOutcome:
    page: str
    name: str
    label: str
    intended: Any
    observed: Any
    status: str                   # filled | failed | left_for_human | skipped
    note: str | None = None


@dataclass
class FillReport:
    status: str                   # ready_for_review | needs_human | failed
    reason: str | None = None
    pages_completed: int = 0
    fields: list[FieldOutcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason, "pages_completed": self.pages_completed,
                "fields": [f.__dict__ for f in self.fields], "notes": self.notes}

    @property
    def left_for_human(self) -> list[FieldOutcome]:
        return [f for f in self.fields if f.status in ("left_for_human", "failed")]
