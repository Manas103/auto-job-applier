"""Enums and dataclasses shared across the pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any


class JobState(str, Enum):
    NEW = "new"
    SCREENED_OUT = "screened_out"
    PINGED = "pinged"
    IGNORED = "ignored"
    APPROVED = "approved"
    NEEDS_JD = "needs_jd"
    JD_READY = "jd_ready"
    IDEAMAKER_RUNNING = "ideamaker_running"
    IDEAMAKER_DONE = "ideamaker_done"
    BUILDING = "building"
    BUILT = "built"
    RECONCILING = "reconciling"
    RECONCILED = "reconciled"
    REVIEWING = "reviewing"
    TAILORING = "tailoring"  # light/archetype lane (pipeline/lite.py): one pass, no stages
    DONE = "done"
    FAILED = "failed"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Stage(str, Enum):
    IDEAMAKER = "ideamaker"
    BUILDER = "builder"
    RECONCILE = "reconcile"
    CRITIC = "critic"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


RUNNING_STATES = {JobState.IDEAMAKER_RUNNING, JobState.BUILDING, JobState.RECONCILING, JobState.REVIEWING,
                  JobState.TAILORING}
BACKLOG_STATES = {JobState.APPROVED, JobState.NEEDS_JD, JobState.JD_READY, JobState.IDEAMAKER_DONE,
                  JobState.BUILT, JobState.RECONCILED, JobState.FAILED}
READY_STATES = {JobState.JD_READY, JobState.IDEAMAKER_DONE, JobState.BUILT, JobState.RECONCILED}

_JSON_LIST_FIELDS = ("locations", "terms", "degrees", "sources", "flags")
_JSON_DICT_FIELDS = ("source_ids",)


@dataclass
class Listing:
    """One normalized listing from any source (tier 1 lists or tier 2 ATS boards)."""
    source: str
    company: str
    title: str
    url: str
    source_id: str = ""
    locations: list[str] = field(default_factory=list)
    category: str = "Other"
    sponsorship: str = ""
    terms: list[str] = field(default_factory=list)
    degrees: list[str] = field(default_factory=list)
    pay: str = ""
    date_posted: str = ""
    active: bool = True
    jd_text: str | None = None
    ats: str | None = None
    ats_id: str | None = None


@dataclass
class Job:
    id: int
    key: str
    url: str
    company: str
    title: str
    locations: list[str]
    category: str
    sponsorship: str
    terms: list[str]
    degrees: list[str]
    pay: str
    date_posted: str
    sources: list[str]
    source_ids: dict[str, str]
    ats: str | None
    ats_id: str | None
    fuzzy_key: str
    first_seen: str
    last_seen: str
    active: bool
    state: JobState
    state_updated_at: str
    failed_stage: str | None = None
    screen_reason: str | None = None
    flags: list[str] = field(default_factory=list)
    jd_text: str | None = None
    jd_source: str | None = None
    jd_fetched_at: str | None = None
    role_folder: str | None = None
    decide_token: str | None = None
    pinged_at: str | None = None
    decided_at: str | None = None
    priority: int = 0
    notes: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "Job":
        d = dict(row)
        for k in _JSON_LIST_FIELDS:
            v = d.get(k)
            d[k] = json.loads(v) if isinstance(v, str) else (v or [])
        for k in _JSON_DICT_FIELDS:
            v = d.get(k)
            d[k] = json.loads(v) if isinstance(v, str) else (v or {})
        d["active"] = bool(d.get("active", 1))
        d["state"] = JobState(d["state"])
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

    def to_public_dict(self) -> dict[str, Any]:
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        d["state"] = self.state.value
        d.pop("jd_text", None)
        d.pop("decide_token", None)
        return d

    @property
    def location_str(self) -> str:
        return "; ".join(self.locations[:3]) if self.locations else ""


@dataclass
class StageRun:
    id: int
    job_id: int
    stage: str
    attempt: int
    status: str
    session_id: str | None
    cost_usd: float
    num_turns: int
    fix_rounds: int
    started_at: str
    finished_at: str | None
    heartbeat_at: str | None
    log_path: str
    pid: int | None
    worker_pid: int | None = None
    result_json: str | None = None
    verify_json: str | None = None
    error: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "StageRun":
        d = dict(row)
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class WatchlistEntry:
    id: int
    company: str
    ats: str
    board: str | None
    host: str | None
    tenant: str | None
    site: str | None
    search_text: str
    enabled: bool
    added_by: str
    added_at: str
    poll_minutes: int
    last_polled_at: str | None = None
    last_ok_at: str | None = None
    last_error: str | None = None
    last_count: int | None = None
    consecutive_failures: int = 0

    @classmethod
    def from_row(cls, row: Any) -> "WatchlistEntry":
        d = dict(row)
        d["enabled"] = bool(d.get("enabled", 1))
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class Failure:
    """One deterministic verification failure, formatted for the fix round."""
    code: str
    file: str | None = None
    line: int | None = None
    message: str = ""
    hint: str = ""

    def format(self) -> str:
        loc = ""
        if self.file:
            loc = self.file + (f":{self.line}" if self.line else "")
        parts = [f"[{self.code}]"]
        if loc:
            parts.append(loc + ":")
        parts.append(self.message)
        s = " ".join(parts)
        if self.hint:
            s = s.rstrip(".") + f". Fix: {self.hint}"
        return s

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "file": self.file, "line": self.line, "message": self.message, "hint": self.hint}
