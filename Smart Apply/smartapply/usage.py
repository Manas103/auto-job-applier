"""Weekly Claude usage: the hard cap Alex set on 2026-10-09 (no new Claude call once the week reaches 70%).

Every stream-json run reports the account's windows in a `rate_limit_event` line; we keep the latest in
data/usage_latest.json, which the pipeline also reads for its own brake (pipeline/usage.py, which also owns the
project-share cap). Smart Apply's own Claude runs count toward this project's share, so the pipeline's caps apply
here too (Alex, 2026-10-10: "the autofilling still applies to our 6% cap"): the project's share since local midnight
(`daily_cap_pct`) and since the week's start (`project_cap_pct`), both read from the pipeline's config.json `usage`
section and estimated the same way the pipeline does it: pipeline stage_runs (read-only) plus our costs table,
converted at `dollars_per_percent`.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import Config


def _latest_paths(cfg: Config) -> list[Path]:
    return [cfg.data_dir / "usage_latest.json", cfg.pipeline_root_path / "data" / "usage_latest.json"]


def note_line(cfg: Config, line: str) -> None:
    if '"rate_limit_event"' not in line:
        return
    try:
        info = json.loads(line).get("rate_limit_info") or {}
        w = info.get("unifiedWindows") or {}
        week, five = w.get("seven_day") or {}, w.get("five_hour") or {}
        if "utilization" not in week:
            return
        r = {"ts": time.time(), "week_pct": round(float(week["utilization"]) * 100, 1),
             "week_resets_at": int(week.get("resetsAt") or 0),
             "five_hour_pct": round(float(five.get("utilization") or 0) * 100, 1),
             "five_hour_resets_at": int(five.get("resetsAt") or 0), "status": info.get("status"), "source": "smartapply"}
        p = cfg.data_dir / "usage_latest.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(r), encoding="utf-8")
        os.replace(tmp, p)
    except (ValueError, TypeError, AttributeError, OSError):
        return


def _newest(cfg: Config, now: float) -> dict | None:
    best = None
    for p in _latest_paths(cfg):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and "week_pct" in d and (best is None or float(d.get("ts") or 0) > float(best.get("ts") or 0)):
            best = d
    if best is None:
        return None
    best = dict(best)
    while best.get("week_resets_at") and now >= float(best["week_resets_at"]):
        best.update(week_pct=0.0, week_resets_at=int(best["week_resets_at"]) + 7 * 86400)
    return best


def week_pct(cfg: Config, now: float | None = None) -> float | None:
    """The newest reading of the week's usage in percent (0 after the reset), or None without a reading."""
    r = _newest(cfg, time.time() if now is None else now)
    return None if r is None else float(r["week_pct"])


PIPELINE_DEFAULTS = {"enabled": True, "project_cap_pct": 40, "daily_cap_pct": 6, "dollars_per_percent": 11.0}


def pipeline_caps(cfg: Config) -> dict:
    out = dict(PIPELINE_DEFAULTS)
    try:
        u = json.loads((cfg.pipeline_root_path / "config.json").read_text(encoding="utf-8")).get("usage") or {}
        out.update({k: u[k] for k in PIPELINE_DEFAULTS if k in u})
    except (OSError, ValueError, AttributeError):
        pass
    return out


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def project_cost_since(cfg: Config, since_ts: float) -> float:
    """Reported run cost of this project (pipeline stages plus our runs) since a time. Both DBs opened read-only."""
    since, total = _iso(since_ts), 0.0
    for path, sql in ((cfg.pipeline_db_path, "SELECT COALESCE(SUM(cost_usd),0) FROM stage_runs WHERE COALESCE(finished_at, started_at) >= ?"),
                      (cfg.data_dir / "smartapply.sqlite3", "SELECT COALESCE(SUM(cost_usd),0) FROM costs WHERE ts >= ?")):
        if not Path(path).exists():
            continue
        try:
            c = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5)
            try:
                total += float(c.execute(sql, (since,)).fetchone()[0] or 0)
            finally:
                c.close()
        except sqlite3.Error:
            continue
    return total


def project_shares(cfg: Config, now: float | None = None) -> tuple[float, float]:
    """(this project's % of the week so far, its % of the week since local midnight)."""
    now = time.time() if now is None else now
    caps = pipeline_caps(cfg)
    r = _newest(cfg, now)
    week_start = (float(r["week_resets_at"]) - 7 * 86400) if r and r.get("week_resets_at") else now - 7 * 86400
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    per = max(0.01, float(caps["dollars_per_percent"]))
    return (round(project_cost_since(cfg, week_start) / per, 1),
            round(project_cost_since(cfg, max(midnight, week_start)) / per, 1))


def hard_cap_reached(cfg: Config) -> str | None:
    """A reason when no Claude call may start, else None."""
    cap = float(cfg.get("usage_hard_cap_pct", 70) or 70)
    pct = week_pct(cfg)
    if pct is not None and pct >= cap:
        return f"weekly Claude usage is at {pct:.0f}% (hard cap {cap:.0f}%)"
    return None


def brake_reason(cfg: Config) -> str | None:
    """A reason when no Claude call may start: the weekly hard cap, or this project's daily or weekly share."""
    hard = hard_cap_reached(cfg)
    if hard:
        return hard
    caps = pipeline_caps(cfg)
    if not bool(caps["enabled"]):
        return None
    week, today = project_shares(cfg)
    if today >= float(caps["daily_cap_pct"]):
        return f"this project used {today:.1f}% of the week today (daily cap {float(caps['daily_cap_pct']):.0f}%); resumes after midnight"
    if week >= float(caps["project_cap_pct"]):
        return f"this project used {week:.1f}% of the week (cap {float(caps['project_cap_pct']):.0f}%)"
    return None
