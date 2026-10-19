"""State transitions for applications. Every change goes through `transition` and writes an event."""

from __future__ import annotations

import sqlite3
from typing import Any

from . import db
from .models import TRANSITIONS, Application, AppState
from .util import now_iso


class IllegalTransition(Exception):
    pass


def load(conn: sqlite3.Connection, app_id: int) -> Application:
    row = db.application_by_id(conn, app_id)
    if row is None:
        raise KeyError(f"application {app_id} not found")
    return Application.from_row(row)


def transition(conn: sqlite3.Connection, app_id: int, to: AppState, event: str, *, detail: dict | None = None,
               reason: str | None = None, expect_from: AppState | None = None, **extra_fields: Any) -> Application:
    """Atomic move. Raises IllegalTransition when TRANSITIONS forbids it."""
    with db.tx(conn):
        row = db.application_by_id(conn, app_id)
        if row is None:
            raise KeyError(f"application {app_id} not found")
        cur = AppState(row["state"])
        if expect_from is not None and cur != expect_from:
            raise IllegalTransition(f"app {app_id} is {cur.value}, expected {expect_from.value}")
        if to != cur and to not in TRANSITIONS.get(cur, set()):
            raise IllegalTransition(f"app {app_id}: {cur.value} -> {to.value} is not allowed")
        fields: dict[str, Any] = {"state": to.value, "reason": reason}
        if to == AppState.READY_FOR_REVIEW:
            fields["ready_at"] = now_iso()
        if to == AppState.APPLIED:
            fields["applied_at"] = now_iso()
        fields.update(extra_fields)
        db.update_application(conn, app_id, **fields)
        db.add_event(conn, app_id, event, from_state=cur.value, to_state=to.value,
                     detail={**(detail or {}), **({"reason": reason} if reason else {})} or None)
    return load(conn, app_id)


def note(conn: sqlite3.Connection, app_id: int, event: str, detail: dict | None = None) -> None:
    """An event without a state change."""
    db.add_event(conn, app_id, event, detail=detail)


def needs_human(conn: sqlite3.Connection, app_id: int, reason: str, detail: dict | None = None) -> Application:
    return transition(conn, app_id, AppState.NEEDS_HUMAN, "needs_human", reason=reason, detail=detail)


def fail(conn: sqlite3.Connection, app_id: int, reason: str, detail: dict | None = None) -> Application:
    return transition(conn, app_id, AppState.FAILED, "failed", reason=reason, detail=detail)
