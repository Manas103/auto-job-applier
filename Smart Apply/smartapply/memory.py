"""Answer memory: answers Alex has effectively confirmed by submitting an application.

When an application is marked applied, the Claude-written answers in its answers.json (never profile facts, never
knock-outs, never anything he was asked to answer himself) are stored by question label, and the next draft that
meets the same label takes the stored answer before Claude is asked (`answers.deterministic_answers`). The browser
agent sees the same list in its prompt. Every entry can be forgotten on the dashboard's Memory page or with
`smartapply memory --forget ID`.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import db
from .log import get_logger
from .models import Application, Question
from .util import read_json

log = get_logger("memory")

REMEMBER_TYPES = {"text", "textarea", "select", "multiselect", "boolean"}
MIN_LABEL = 8          # shorter labels ("Name", "Email") are profile facts or too ambiguous to reuse


def encode(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def decode(text: str) -> Any:
    if text in ("true", "false") or text[:1] == "[":
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


def memory_map(conn: sqlite3.Connection) -> dict[str, Any]:
    """Normalized question label -> answer, for the answer writer."""
    return {r["question"]: decode(r["answer"]) for r in db.recall_answers(conn)}


def remember_from_application(conn: sqlite3.Connection, app: Application) -> int:
    """Store the Claude answers of an applied application as confirmed. Returns how many were stored."""
    from .answers import KNOCKOUT, LOCATION_IDS, MOOT_IDS       # local import: answers imports db, not memory

    questions = [Question.from_dict(d) for d in (read_json(app.questions_path, []) or [])]
    packet = read_json(app.answers_path, None) if app.answers_path.exists() else None
    if not questions or not isinstance(packet, dict):
        return 0
    answers = packet.get("answers") or {}
    if isinstance(answers, list):     # AnswerPacket.to_dict writes a list keyed by question_id (Walleye, 2026-10-05)
        answers = {a.get("question_id"): a for a in answers if isinstance(a, dict)}
    n = 0
    for q in questions:
        a = answers.get(q.id)
        if not isinstance(a, dict) or a.get("source") != "claude" or a.get("needs_human"):
            continue
        value = a.get("value")
        if value in (None, "", []) or q.type not in REMEMBER_TYPES:
            continue
        label = " ".join(q.label.split())
        if len(label) < MIN_LABEL or KNOCKOUT.search(label) or q.id in LOCATION_IDS or q.id in MOOT_IDS:
            continue
        db.remember_answer(conn, label, encode(value), kind="confirmed", ats=None)
        n += 1
    if n:
        log.info("remembered %d confirmed answers from app %s (%s)", n, app.id, app.company)
    return n
