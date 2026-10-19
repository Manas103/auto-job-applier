"""SQLite store: schema, connections (WAL, per-thread), transactions, typed helpers."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .models import Job, JobState, Listing, StageRun, WatchlistEntry
from .util import new_token, now_iso

SCHEMA_VERSION = 1

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id               INTEGER PRIMARY KEY,
  key              TEXT NOT NULL UNIQUE,
  url              TEXT NOT NULL,
  company          TEXT NOT NULL,
  title            TEXT NOT NULL,
  locations        TEXT NOT NULL DEFAULT '[]',
  category         TEXT NOT NULL DEFAULT 'Other',
  sponsorship      TEXT NOT NULL DEFAULT '',
  terms            TEXT NOT NULL DEFAULT '[]',
  degrees          TEXT NOT NULL DEFAULT '[]',
  pay              TEXT NOT NULL DEFAULT '',
  date_posted      TEXT NOT NULL DEFAULT '',
  sources          TEXT NOT NULL DEFAULT '[]',
  source_ids       TEXT NOT NULL DEFAULT '{}',
  ats              TEXT,
  ats_id           TEXT,
  fuzzy_key        TEXT NOT NULL,
  first_seen       TEXT NOT NULL,
  last_seen        TEXT NOT NULL,
  active           INTEGER NOT NULL DEFAULT 1,
  state            TEXT NOT NULL DEFAULT 'new',
  state_updated_at TEXT NOT NULL,
  failed_stage     TEXT,
  screen_reason    TEXT,
  flags            TEXT NOT NULL DEFAULT '[]',
  jd_text          TEXT,
  jd_source        TEXT,
  jd_fetched_at    TEXT,
  role_folder      TEXT,
  decide_token     TEXT,
  pinged_at        TEXT,
  decided_at       TEXT,
  priority         INTEGER NOT NULL DEFAULT 0,
  notes            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS ix_jobs_fuzzy ON jobs(fuzzy_key);
CREATE INDEX IF NOT EXISTS ix_jobs_ats ON jobs(ats, ats_id);
CREATE INDEX IF NOT EXISTS ix_jobs_company ON jobs(company);

CREATE TABLE IF NOT EXISTS job_events (
  id         INTEGER PRIMARY KEY,
  job_id     INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  ts         TEXT NOT NULL,
  event      TEXT NOT NULL,
  from_state TEXT,
  to_state   TEXT,
  detail     TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_job ON job_events(job_id, id);

CREATE TABLE IF NOT EXISTS stage_runs (
  id           INTEGER PRIMARY KEY,
  job_id       INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  stage        TEXT NOT NULL,
  attempt      INTEGER NOT NULL,
  status       TEXT NOT NULL,
  session_id   TEXT,
  cost_usd     REAL NOT NULL DEFAULT 0,
  num_turns    INTEGER NOT NULL DEFAULT 0,
  fix_rounds   INTEGER NOT NULL DEFAULT 0,
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  heartbeat_at TEXT,
  log_path     TEXT NOT NULL,
  pid          INTEGER,
  worker_pid   INTEGER,
  result_json  TEXT,
  verify_json  TEXT,
  error        TEXT,
  UNIQUE(job_id, stage, attempt)
);
CREATE INDEX IF NOT EXISTS ix_runs_status ON stage_runs(status);

CREATE TABLE IF NOT EXISTS watchlist (
  id                   INTEGER PRIMARY KEY,
  company              TEXT NOT NULL,
  ats                  TEXT NOT NULL,
  board                TEXT,
  host                 TEXT,
  tenant               TEXT,
  site                 TEXT,
  search_text          TEXT NOT NULL DEFAULT 'intern',
  enabled              INTEGER NOT NULL DEFAULT 1,
  added_by             TEXT NOT NULL,
  added_at             TEXT NOT NULL,
  poll_minutes         INTEGER NOT NULL DEFAULT 60,
  last_polled_at       TEXT,
  last_ok_at           TEXT,
  last_error           TEXT,
  last_count           INTEGER,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  UNIQUE(ats, board, host, tenant, site)
);

CREATE TABLE IF NOT EXISTS source_runs (
  id        INTEGER PRIMARY KEY,
  ts        TEXT NOT NULL,
  source    TEXT NOT NULL,
  count     INTEGER NOT NULL,
  ok        INTEGER NOT NULL,
  error     TEXT
);

-- How good a company is for a software internship (0-100). Sets work-queue priority: best companies first.
CREATE TABLE IF NOT EXISTS company_scores (
  company    TEXT PRIMARY KEY,
  score      INTEGER NOT NULL,
  tier       TEXT,
  reason     TEXT,
  scored_at  TEXT NOT NULL,
  model      TEXT
);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_DDL)
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('csrf_token', ?)", (new_token(),))


_local = threading.local()


def thread_conn(db_path: Path | str) -> sqlite3.Connection:
    """One connection per (thread, db path)."""
    key = str(Path(db_path))
    store = getattr(_local, "conns", None)
    if store is None:
        store = {}
        _local.conns = store
    conn = store.get(key)
    if conn is None:
        conn = connect(db_path)
        init_schema(conn)
        store[key] = conn
    return conn


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT / ROLLBACK. Nested use is not supported."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


# ----------------------------------------------------------------------------- settings

def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))


def del_setting(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM settings WHERE key=?", (key,))


# ----------------------------------------------------------------------------- jobs

def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def job_by_id(conn: sqlite3.Connection, job_id: int) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return Job.from_row(row) if row else None


def job_by_key(conn: sqlite3.Connection, key: str) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE key=?", (key,)).fetchone()
    return Job.from_row(row) if row else None


def job_by_ats(conn: sqlite3.Connection, ats: str, ats_id: str) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE ats=? AND ats_id=?", (ats, ats_id)).fetchone()
    return Job.from_row(row) if row else None


def jobs_in_states(conn: sqlite3.Connection, states: Iterable[JobState | str], limit: int | None = None,
                   order: str = "state_updated_at DESC") -> list[Job]:
    vals = [s.value if isinstance(s, JobState) else s for s in states]
    if not vals:
        return []
    q = f"SELECT * FROM jobs WHERE state IN ({','.join('?' * len(vals))}) ORDER BY {order}"
    if limit:
        q += f" LIMIT {int(limit)}"
    return [Job.from_row(r) for r in conn.execute(q, vals).fetchall()]


def all_jobs(conn: sqlite3.Connection) -> list[Job]:
    return [Job.from_row(r) for r in conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()]


def jobs_by_company(conn: sqlite3.Connection, company_norm: str, *, norm_fn) -> list[Job]:
    rows = conn.execute("SELECT * FROM jobs").fetchall()
    return [Job.from_row(r) for r in rows if norm_fn(r["company"]) == company_norm]


def count_by_state(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state").fetchall()
    return {r["state"]: r["n"] for r in rows}


def insert_job(conn: sqlite3.Connection, listing: Listing, key: str, fuzzy_key: str, *,
               state: JobState = JobState.NEW, ts: str | None = None) -> int:
    ts = ts or now_iso()
    cur = conn.execute(
        """INSERT INTO jobs(key, url, company, title, locations, category, sponsorship, terms, degrees, pay,
                            date_posted, sources, source_ids, ats, ats_id, fuzzy_key, first_seen, last_seen,
                            active, state, state_updated_at, jd_text, jd_source, jd_fetched_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (key, listing.url, listing.company, listing.title, _j(listing.locations), listing.category,
         listing.sponsorship, _j(listing.terms), _j(listing.degrees), listing.pay, listing.date_posted,
         _j([listing.source]), _j({listing.source: listing.source_id} if listing.source_id else {}),
         listing.ats, listing.ats_id, fuzzy_key, ts, ts, 1 if listing.active else 0, state.value, ts,
         listing.jd_text, ("ats_api" if listing.jd_text else None), (ts if listing.jd_text else None)),
    )
    return int(cur.lastrowid)


def update_job(conn: sqlite3.Connection, job_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols, vals = [], []
    for k, v in fields.items():
        if k in ("locations", "terms", "degrees", "sources", "flags", "source_ids"):
            v = _j(v)
        elif isinstance(v, JobState):
            v = v.value
        elif isinstance(v, bool):
            v = 1 if v else 0
        cols.append(f"{k}=?")
        vals.append(v)
    vals.append(job_id)
    conn.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE id=?", vals)


def add_flag(conn: sqlite3.Connection, job_id: int, flag: str) -> None:
    job = job_by_id(conn, job_id)
    if job is None:
        return
    if flag not in job.flags:
        update_job(conn, job_id, flags=job.flags + [flag])


def add_event(conn: sqlite3.Connection, job_id: int, event: str, from_state: str | None = None,
              to_state: str | None = None, detail: dict | None = None) -> None:
    conn.execute("INSERT INTO job_events(job_id, ts, event, from_state, to_state, detail) VALUES(?,?,?,?,?,?)",
                 (job_id, now_iso(), event, from_state, to_state, _j(detail) if detail is not None else None))


def events_for_job(conn: sqlite3.Connection, job_id: int) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM job_events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("detail"):
            try:
                d["detail"] = json.loads(d["detail"])
            except json.JSONDecodeError:
                pass
        out.append(d)
    return out


# ----------------------------------------------------------------------------- stage runs

def start_stage_run(conn: sqlite3.Connection, job_id: int, stage: str, attempt: int, log_path: str,
                    worker_pid: int | None = None) -> int:
    ts = now_iso()
    cur = conn.execute(
        """INSERT INTO stage_runs(job_id, stage, attempt, status, started_at, heartbeat_at, log_path, worker_pid)
           VALUES(?,?,?,'running',?,?,?,?)""",
        (job_id, stage, attempt, ts, ts, log_path, worker_pid))
    return int(cur.lastrowid)


def update_stage_run(conn: sqlite3.Connection, run_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols, vals = [], []
    for k, v in fields.items():
        cols.append(f"{k}=?")
        vals.append(v)
    vals.append(run_id)
    conn.execute(f"UPDATE stage_runs SET {', '.join(cols)} WHERE id=?", vals)


def heartbeat_stage_run(conn: sqlite3.Connection, run_id: int, pid: int | None = None) -> None:
    if pid is not None:
        conn.execute("UPDATE stage_runs SET heartbeat_at=?, pid=? WHERE id=?", (now_iso(), pid, run_id))
    else:
        conn.execute("UPDATE stage_runs SET heartbeat_at=? WHERE id=?", (now_iso(), run_id))


def finish_stage_run(conn: sqlite3.Connection, run_id: int, status: str, *, session_id: str | None = None,
                     cost_usd: float = 0.0, num_turns: int = 0, result_json: str | None = None,
                     verify_json: str | None = None, fix_rounds: int = 0, error: str | None = None) -> None:
    conn.execute(
        """UPDATE stage_runs SET status=?, session_id=?, cost_usd=?, num_turns=?, result_json=?, verify_json=?,
                                 fix_rounds=?, error=?, finished_at=? WHERE id=?""",
        (status, session_id, cost_usd, num_turns, result_json, verify_json, fix_rounds, error, now_iso(), run_id))


def stage_run_by_id(conn: sqlite3.Connection, run_id: int) -> StageRun | None:
    row = conn.execute("SELECT * FROM stage_runs WHERE id=?", (run_id,)).fetchone()
    return StageRun.from_row(row) if row else None


def running_stage_runs(conn: sqlite3.Connection) -> list[StageRun]:
    rows = conn.execute("SELECT * FROM stage_runs WHERE status='running' ORDER BY id").fetchall()
    return [StageRun.from_row(r) for r in rows]


def stage_runs_for_job(conn: sqlite3.Connection, job_id: int) -> list[StageRun]:
    rows = conn.execute("SELECT * FROM stage_runs WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    return [StageRun.from_row(r) for r in rows]


def last_attempt(conn: sqlite3.Connection, job_id: int, stage: str) -> int:
    row = conn.execute("SELECT MAX(attempt) AS a FROM stage_runs WHERE job_id=? AND stage=?", (job_id, stage)).fetchone()
    return int(row["a"] or 0)


def failed_attempts(conn: sqlite3.Connection, job_id: int, stage: str) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM stage_runs WHERE job_id=? AND stage=? AND status='failed'", (job_id, stage)).fetchone()
    return int(row["n"] or 0)


def job_cost(conn: sqlite3.Connection, job_id: int) -> float:
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) AS c FROM stage_runs WHERE job_id=?", (job_id,)).fetchone()
    return float(row["c"] or 0.0)


def cost_totals(conn: sqlite3.Connection) -> dict[str, Any]:
    dur = "(julianday(COALESCE(r.finished_at, r.heartbeat_at, r.started_at)) - julianday(r.started_at)) * 1440"
    per_job = conn.execute(
        f"""SELECT j.id, j.company, j.title, j.state, COALESCE(SUM(r.cost_usd),0) AS cost, COUNT(r.id) AS runs,
            CAST(ROUND(COALESCE(SUM({dur}),0)) AS INT) AS minutes
           FROM jobs j JOIN stage_runs r ON r.job_id=j.id GROUP BY j.id ORDER BY cost DESC""").fetchall()
    per_stage = conn.execute(
        f"""SELECT stage, COALESCE(SUM(cost_usd),0) AS cost, COUNT(*) AS runs,
            CAST(ROUND(COALESCE(SUM({dur}),0)) AS INT) AS minutes
           FROM stage_runs r GROUP BY stage""").fetchall()
    total = conn.execute("SELECT COALESCE(SUM(cost_usd),0) AS c FROM stage_runs").fetchone()["c"]
    week = conn.execute("SELECT COALESCE(SUM(cost_usd),0) AS c FROM stage_runs WHERE started_at >= datetime('now','-7 days')").fetchone()["c"]
    return {"per_job": [dict(r) for r in per_job], "per_stage": [dict(r) for r in per_stage],
            "total": float(total or 0), "last_7_days": float(week or 0)}


# ----------------------------------------------------------------------------- watchlist

def watchlist_all(conn: sqlite3.Connection) -> list[WatchlistEntry]:
    rows = conn.execute("SELECT * FROM watchlist ORDER BY company, id").fetchall()
    return [WatchlistEntry.from_row(r) for r in rows]


def watchlist_by_id(conn: sqlite3.Connection, wid: int) -> WatchlistEntry | None:
    row = conn.execute("SELECT * FROM watchlist WHERE id=?", (wid,)).fetchone()
    return WatchlistEntry.from_row(row) if row else None


def watchlist_find(conn: sqlite3.Connection, ats: str, board: str | None, host: str | None, tenant: str | None,
                   site: str | None) -> WatchlistEntry | None:
    row = conn.execute(
        "SELECT * FROM watchlist WHERE ats=? AND board IS ? AND host IS ? AND tenant IS ? AND site IS ?",
        (ats, board, host, tenant, site)).fetchone()
    return WatchlistEntry.from_row(row) if row else None


def watchlist_insert(conn: sqlite3.Connection, *, company: str, ats: str, board: str | None = None,
                     host: str | None = None, tenant: str | None = None, site: str | None = None,
                     search_text: str = "intern", added_by: str = "manual", poll_minutes: int = 60) -> int:
    cur = conn.execute(
        """INSERT INTO watchlist(company, ats, board, host, tenant, site, search_text, enabled, added_by, added_at, poll_minutes)
           VALUES(?,?,?,?,?,?,?,1,?,?,?)""",
        (company, ats, board, host, tenant, site, search_text, added_by, now_iso(), poll_minutes))
    return int(cur.lastrowid)


def watchlist_update(conn: sqlite3.Connection, wid: int, **fields: Any) -> None:
    if not fields:
        return
    cols, vals = [], []
    for k, v in fields.items():
        if isinstance(v, bool):
            v = 1 if v else 0
        cols.append(f"{k}=?")
        vals.append(v)
    vals.append(wid)
    conn.execute(f"UPDATE watchlist SET {', '.join(cols)} WHERE id=?", vals)


def watchlist_delete(conn: sqlite3.Connection, wid: int) -> None:
    conn.execute("DELETE FROM watchlist WHERE id=?", (wid,))


# ----------------------------------------------------------------------------- source runs

def record_source_run(conn: sqlite3.Connection, source: str, count: int, ok: bool, error: str | None = None) -> None:
    conn.execute("INSERT INTO source_runs(ts, source, count, ok, error) VALUES(?,?,?,?,?)",
                 (now_iso(), source, count, 1 if ok else 0, error))


def last_source_count(conn: sqlite3.Connection, source: str) -> int | None:
    row = conn.execute("SELECT count FROM source_runs WHERE source=? AND ok=1 ORDER BY id DESC LIMIT 1", (source,)).fetchone()
    return int(row["count"]) if row else None


def recent_source_runs(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM source_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
