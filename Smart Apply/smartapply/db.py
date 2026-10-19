"""SQLite store with numbered .sql migrations (Archon's pattern) and WAL (the pipeline's pattern)."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .paths import MIGRATIONS_DIR
from .util import new_token, now_iso, now_local_iso

_MIGRATION_RE = re.compile(r"^(\d{4})_.*\.sql$")
_local = threading.local()


def connect(db_path: Path, *, migrations_dir: Path = MIGRATIONS_DIR) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn, migrations_dir)
    if get_setting(conn, "csrf_token") is None:
        set_setting(conn, "csrf_token", new_token())
    return conn


def thread_conn(db_path: Path) -> sqlite3.Connection:
    """One connection per thread and path."""
    key = str(Path(db_path).resolve())
    pool: dict[str, sqlite3.Connection] = getattr(_local, "pool", None) or {}
    if key not in pool:
        pool[key] = connect(db_path)
        _local.pool = pool
    return pool[key]


def _migrate(conn: sqlite3.Connection, migrations_dir: Path) -> None:
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    files = sorted(p for p in Path(migrations_dir).glob("*.sql") if _MIGRATION_RE.match(p.name))
    for p in files:
        n = int(_MIGRATION_RE.match(p.name).group(1))
        if n <= current:
            continue
        sql = p.read_text(encoding="utf-8")
        # executescript commits any open transaction first, so it wraps the whole file itself; no manual BEGIN.
        try:
            conn.executescript("BEGIN;\n" + sql + f"\nPRAGMA user_version = {n};\nCOMMIT;")
        except Exception as e:  # noqa: BLE001
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise RuntimeError(f"migration {p.name} failed: {e}") from e
        current = n


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------- settings

def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))


# ---------------------------------------------------------------- applications

def insert_application(conn: sqlite3.Connection, **fields: Any) -> int:
    ts = now_iso()
    fields.setdefault("created_at", ts)
    fields.setdefault("updated_at", ts)
    fields.setdefault("state", "queued")
    if isinstance(fields.get("ats_params"), dict):
        fields["ats_params"] = json.dumps(fields["ats_params"])
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    cur = conn.execute(f"INSERT INTO applications({cols}) VALUES({marks})", tuple(fields.values()))
    return int(cur.lastrowid)


def update_application(conn: sqlite3.Connection, app_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now_iso()
    if isinstance(fields.get("ats_params"), dict):
        fields["ats_params"] = json.dumps(fields["ats_params"])
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE applications SET {sets} WHERE id=?", (*fields.values(), app_id))


def application_by_id(conn: sqlite3.Connection, app_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()


def application_by_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone()


def applications_in_states(conn: sqlite3.Connection, states: list[str] | tuple[str, ...],
                           *, limit: int | None = None) -> list[sqlite3.Row]:
    marks = ", ".join("?" for _ in states)
    sql = f"SELECT * FROM applications WHERE state IN ({marks}) ORDER BY updated_at DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return list(conn.execute(sql, tuple(states)))


def all_applications(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM applications ORDER BY updated_at DESC"))


def count_by_state(conn: sqlite3.Connection) -> dict[str, int]:
    return {r["state"]: r["n"] for r in conn.execute("SELECT state, COUNT(*) n FROM applications GROUP BY state")}


# ---------------------------------------------------------------- events, fields, costs

def add_event(conn: sqlite3.Connection, app_id: int, event: str, *, from_state: str | None = None,
              to_state: str | None = None, detail: dict | None = None) -> None:
    conn.execute("INSERT INTO app_events(app_id, ts, ts_local, event, from_state, to_state, detail) VALUES(?,?,?,?,?,?,?)",
                 (app_id, now_iso(), now_local_iso(), event, from_state, to_state,
                  json.dumps(detail, ensure_ascii=False) if detail else None))


def events_for_app(conn: sqlite3.Connection, app_id: int) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM app_events WHERE app_id=? ORDER BY id", (app_id,)):
        d = dict(r)
        d["detail"] = json.loads(d["detail"]) if d.get("detail") else None
        out.append(d)
    return out


def record_field(conn: sqlite3.Connection, app_id: int, *, page: str, name: str, label: str, intended: Any,
                 observed: Any, status: str, note: str | None = None) -> None:
    conn.execute("DELETE FROM fields WHERE app_id=? AND page=? AND name=?", (app_id, page, name))
    conn.execute("INSERT INTO fields(app_id, page, name, label, intended, observed, status, note, updated_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?)",
                 (app_id, page, name, label, _s(intended), _s(observed), status, note, now_iso()))


def fields_for_app(conn: sqlite3.Connection, app_id: int) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM fields WHERE app_id=? ORDER BY id", (app_id,)))


def record_cost(conn: sqlite3.Connection, app_id: int | None, *, purpose: str, model: str, cost_usd: float,
                num_turns: int, session_id: str | None, subtype: str, duration_s: float) -> None:
    conn.execute("INSERT INTO costs(app_id, purpose, model, cost_usd, num_turns, session_id, subtype, duration_s, ts, ts_local) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (app_id, purpose, model, float(cost_usd or 0), int(num_turns or 0), session_id, subtype,
                  float(duration_s or 0), now_iso(), now_local_iso()))


def cost_totals(conn: sqlite3.Connection) -> dict[str, Any]:
    total = conn.execute("SELECT COALESCE(SUM(cost_usd),0) c, COUNT(*) n FROM costs").fetchone()
    per_day = list(conn.execute("SELECT substr(ts_local,1,10) day, SUM(cost_usd) c, COUNT(*) n FROM costs "
                                "GROUP BY day ORDER BY day DESC LIMIT 30"))
    per_app = list(conn.execute(
        "SELECT c.app_id, a.job_id, a.company, a.ats, SUM(c.cost_usd) c, COUNT(*) n, SUM(c.num_turns) turns FROM costs c "
        "LEFT JOIN applications a ON a.id=c.app_id GROUP BY c.app_id ORDER BY c DESC LIMIT 50"))
    per_purpose = list(conn.execute("SELECT purpose, model, SUM(cost_usd) c, COUNT(*) n, SUM(num_turns) turns, "
                                    "SUM(duration_s) secs FROM costs GROUP BY purpose, model ORDER BY c DESC"))
    return {"total_usd": float(total["c"]), "runs": int(total["n"]), "per_day": per_day, "per_app": per_app,
            "per_purpose": per_purpose}


# ---------------------------------------------------------------- accounts, memory

def account_for(conn: sqlite3.Connection, ats: str, tenant: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM accounts WHERE ats=? AND tenant=?", (ats, tenant)).fetchone()


def upsert_account(conn: sqlite3.Connection, ats: str, tenant: str, email: str, **fields: Any) -> None:
    row = account_for(conn, ats, tenant)
    if row is None:
        conn.execute("INSERT INTO accounts(ats, tenant, email, created_at) VALUES(?,?,?,?)", (ats, tenant, email, now_iso()))
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE accounts SET {sets} WHERE ats=? AND tenant=?", (*fields.values(), ats, tenant))


def remember_answer(conn: sqlite3.Connection, question: str, answer: str, *, kind: str = "confirmed",
                    ats: str | None = None) -> None:
    conn.execute("INSERT INTO answer_memory(question, answer, kind, ats, created_at) VALUES(?,?,?,?,?) "
                 "ON CONFLICT(question, ats) DO UPDATE SET answer=excluded.answer, kind=excluded.kind",
                 (question.strip().lower(), answer, kind, ats, now_iso()))


def recall_answers(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM answer_memory ORDER BY id"))


def forget_answer(conn: sqlite3.Connection, memory_id: int) -> bool:
    return conn.execute("DELETE FROM answer_memory WHERE id=?", (memory_id,)).rowcount > 0


def _s(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)
