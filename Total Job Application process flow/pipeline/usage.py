"""Weekly Claude usage brake.

Every `claude -p` stream reports the account's rate-limit windows in a `rate_limit_event` line
(`unifiedWindows.seven_day.utilization`, `five_hour`, `resetsAt`). `note_line` saves each one, so the latest reading of
Alex's whole account (this project, Smart Apply and his own chats) is always on disk.

The brake (Alex, 2026-10-09: measured in % of the weekly limit, never dollars):
- hard cap: when the week reaches `usage.hard_cap_pct` (70) nothing here starts a Claude call;
- project cap: when this project's share of the week reaches `usage.project_cap_pct` (40), full-lane stages wait.
  Light tailoring sessions (about 0.05% each) keep running until the hard cap.
- daily cap: when this project has used `usage.daily_cap_pct` (6, the weekly share spread over 7 days) of the week
  since local midnight, full-lane stages wait for the next day, so the week is not spent in one day.
The account reading has no per-project split, so the project's share is estimated from the API-equivalent cost the
CLI reports for our own runs (pipeline stage_runs plus Smart Apply's costs table), converted at
`usage.dollars_per_percent`: about $11 to $14 of runs moved the week by 1% in Aug to Oct 2026; the low end is used so
the estimate errs high. A reading taken before the week's reset counts as 0%.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

log = logging.getLogger("pipeline.usage")

DEFAULTS = {"enabled": True, "project_cap_pct": 40, "daily_cap_pct": 6, "hard_cap_pct": 70, "dollars_per_percent": 11.0,
            "probe_model": "claude-haiku-4-5-20251001", "probe_every_min": 30, "poll_s": 60,
            "smartapply_db": "../Smart Apply/data/smartapply.sqlite3",
            "smartapply_latest": "../Smart Apply/data/usage_latest.json"}

_lock = threading.Lock()
_last_probe = 0.0


def settings(cfg) -> dict:
    u = cfg.get("usage", {}) if hasattr(cfg, "get") else {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in dict(u or {}).items()})
    return out


def latest_path(cfg) -> Path:
    return Path(cfg.data_dir) / "usage_latest.json"


def history_path(cfg) -> Path:
    return Path(cfg.data_dir) / "usage_readings.jsonl"


def _resolve(cfg, rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else (Path(cfg.project_root) / p).resolve()


# ----------------------------------------------------------------------------- readings

def parse_event(line: str) -> dict | None:
    """A reading from one stream-json line, or None."""
    if '"rate_limit_event"' not in line:
        return None
    try:
        info = json.loads(line).get("rate_limit_info") or {}
        w = info.get("unifiedWindows") or {}
        week, five = w.get("seven_day") or {}, w.get("five_hour") or {}
        if "utilization" not in week:
            return None
        return {"ts": time.time(), "week_pct": round(float(week["utilization"]) * 100, 1),
                "week_resets_at": int(week.get("resetsAt") or 0),
                "five_hour_pct": round(float(five.get("utilization") or 0) * 100, 1),
                "five_hour_resets_at": int(five.get("resetsAt") or 0), "status": info.get("status")}
    except (ValueError, TypeError, AttributeError):
        return None


def record(cfg, reading: dict, source: str = "") -> None:
    reading = dict(reading, source=source)
    with _lock:
        try:
            p = latest_path(cfg)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(reading), encoding="utf-8")
            os.replace(tmp, p)
            with history_path(cfg).open("a", encoding="utf-8") as f:
                f.write(json.dumps(reading) + "\n")
        except OSError as e:
            log.debug("usage reading not saved: %s", e)


def note_line(cfg, line: str, source: str = "") -> None:
    r = parse_event(line)
    if r is not None:
        record(cfg, r, source)


def _read(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and "week_pct" in d else None
    except (OSError, ValueError):
        return None


def latest(cfg, now: float | None = None) -> dict | None:
    """The newest reading (ours or Smart Apply's). After the week's reset time the week counts as 0%."""
    now = time.time() if now is None else now
    cands = [r for r in (_read(latest_path(cfg)), _read(_resolve(cfg, settings(cfg)["smartapply_latest"]))) if r]
    if not cands:
        return None
    r = dict(max(cands, key=lambda x: float(x.get("ts") or 0)))
    while r.get("week_resets_at") and now >= r["week_resets_at"]:
        r.update(week_pct=0.0, week_resets_at=int(r["week_resets_at"]) + 7 * 86400, stale_reset=True)
    if r.get("five_hour_resets_at") and now >= r["five_hour_resets_at"]:
        r["five_hour_pct"] = 0.0
    return r


def probe(cfg, force: bool = False) -> dict | None:
    """A fresh reading from a one-word Haiku call (a tiny fraction of a percent). At most every `probe_every_min`."""
    global _last_probe
    s = settings(cfg)
    if not force and time.time() - _last_probe < float(s["probe_every_min"]) * 60:
        return None
    _last_probe = time.time()
    cmd = [*[str(x) for x in cfg.claude.cmd], "-p", "Reply with OK", "--model", str(s["probe_model"]),
           "--output-format", "stream-json", "--verbose", "--max-turns", "1", "--tools", ""]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                              stdin=subprocess.DEVNULL, creationflags=flags, cwd=str(cfg.data_dir))
    except (OSError, subprocess.SubprocessError) as e:
        log.info("usage probe failed: %s", e)
        return None
    got = None
    for ln in proc.stdout.splitlines():
        r = parse_event(ln)
        if r is not None:
            got = r
    if got is not None:
        record(cfg, got, "probe")
    return got


# ----------------------------------------------------------------------------- this project's share

def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def project_cost_since(cfg, conn: sqlite3.Connection, since_ts: float) -> float:
    since = _iso(since_ts)
    total = float(conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM stage_runs WHERE COALESCE(finished_at, started_at) >= ?",
                               (since,)).fetchone()[0] or 0)
    p = _resolve(cfg, settings(cfg)["smartapply_db"])
    if p.exists():
        try:
            sa = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=5)
            try:
                total += float(sa.execute("SELECT COALESCE(SUM(cost_usd),0) FROM costs WHERE ts >= ?", (since,)).fetchone()[0] or 0)
            finally:
                sa.close()
        except sqlite3.Error as e:
            log.debug("smart apply costs unreadable: %s", e)
    return total


def snapshot(cfg, conn: sqlite3.Connection, now: float | None = None) -> dict:
    """Week %, project share %, caps and whether each gate is open. Week % is None with no reading yet."""
    s = settings(cfg)
    now = time.time() if now is None else now
    r = latest(cfg, now)
    # without any reading the week's start is unknown; the last 7 days stand in (errs high)
    week_start = (r["week_resets_at"] - 7 * 86400) if r and r.get("week_resets_at") else now - 7 * 86400
    project = round(project_cost_since(cfg, conn, week_start) / max(0.01, float(s["dollars_per_percent"])), 1)
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    today = round(project_cost_since(cfg, conn, max(midnight, week_start)) / max(0.01, float(s["dollars_per_percent"])), 1)
    week = r["week_pct"] if r else None
    hard, cap, daily = float(s["hard_cap_pct"]), float(s["project_cap_pct"]), float(s["daily_cap_pct"])
    reason_hard = f"weekly usage {week:.0f}% has reached the {hard:.0f}% hard cap" if week is not None and week >= hard else None
    reason_proj = f"this project used {project:.1f}% of the week (cap {cap:.0f}%)" if project >= cap else None
    reason_day = f"this project used {today:.1f}% of the week today (daily cap {daily:.0f}%)" if today >= daily else None
    return {"enabled": bool(s["enabled"]), "week_pct": week, "five_hour_pct": r["five_hour_pct"] if r else None,
            "week_resets_at": r.get("week_resets_at") if r else None, "reading_age_min": round((now - float(r["ts"])) / 60) if r else None,
            "project_pct": project, "project_cap_pct": cap, "today_pct": today, "daily_cap_pct": daily, "hard_cap_pct": hard,
            "full_ok": not (reason_hard or reason_proj or reason_day), "light_ok": not reason_hard,
            "reason": reason_hard or reason_proj or reason_day, "daily_only": bool(reason_day and not (reason_hard or reason_proj))}


def check(cfg, conn: sqlite3.Connection, kind: str = "full") -> tuple[bool, str | None]:
    """kind: 'full' (ideamaker, builder, reconcile, critic, research) or 'light' (tailoring, company scoring)."""
    s = settings(cfg)
    if not bool(s["enabled"]):
        return True, None
    snap = snapshot(cfg, conn)
    ok = snap["full_ok"] if kind == "full" else snap["light_ok"]
    return ok, (None if ok else snap["reason"])


def wait_until_ok(cfg, conn: sqlite3.Connection, kind: str, stop_event: threading.Event | None = None,
                  on_wait: Callable[[str], None] | None = None) -> bool:
    """Block while the brake is on for `kind`. A paused worker re-reads usage with a cheap probe every
    `probe_every_min` so it notices the weekly reset. Returns False if the pool is stopping."""
    s = settings(cfg)
    announced = None
    while True:
        ok, why = check(cfg, conn, kind)
        if ok:
            return True
        if stop_event is not None and stop_event.is_set():
            return False
        if on_wait is not None and why != announced:
            r = latest(cfg)
            when = datetime.fromtimestamp(r["week_resets_at"]).strftime("%a %b %d %I:%M %p") if r and r.get("week_resets_at") else "the weekly reset"
            if "daily cap" in (why or ""):
                when = "midnight"
            on_wait(f"paused by the usage brake: {why}; resumes after {when}")
            announced = why
            log.info("usage brake on (%s): %s", kind, why)
        probe(cfg)
        if stop_event is not None:
            stop_event.wait(float(s["poll_s"]))
        else:
            time.sleep(float(s["poll_s"]))


def fmt_pct(cost_usd: float, cfg) -> str:
    """A run's cost as a share of the weekly limit, for the dashboard."""
    pct = float(cost_usd or 0) / max(0.01, float(settings(cfg)["dollars_per_percent"]))
    return f"{pct:.2f}%" if pct < 1 else f"{pct:.1f}%"


