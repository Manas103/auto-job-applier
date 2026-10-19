"""The weekly hard cap (Alex, 2026-10-09): no Claude call starts once the week is at 70%."""

from __future__ import annotations

import json
import time

from smartapply import usage
from smartapply.claude_runner import run_claude


def _write(path, pct, resets_at, ts=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ts": ts or time.time(), "week_pct": pct, "week_resets_at": resets_at}), encoding="utf-8")


def test_hard_cap_skips_the_claude_call(cfg, tmp_path):
    _write(cfg.data_dir / "usage_latest.json", 72.0, int(time.time()) + 86400)
    res = run_claude(cfg, prompt="hi", cwd=tmp_path / "cwd", log_path=tmp_path / "x.log", model="m", timeout_s=5)
    assert res.ok is False and res.subtype == "usage_brake" and "72%" in res.result_text


def test_newest_reading_wins_and_a_reset_clears_it(cfg):
    _write(cfg.data_dir / "usage_latest.json", 80.0, int(time.time()) + 86400, ts=time.time() - 600)
    _write(cfg.pipeline_root_path / "data" / "usage_latest.json", 30.0, int(time.time()) + 86400)
    assert usage.week_pct(cfg) == 30.0 and usage.hard_cap_reached(cfg) is None
    _write(cfg.pipeline_root_path / "data" / "usage_latest.json", 95.0, int(time.time()) - 60)
    assert usage.week_pct(cfg) == 0.0


def test_readings_are_recorded_from_the_stream(cfg):
    line = json.dumps({"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
        "seven_day": {"utilization": 0.41, "resetsAt": int(time.time()) + 3600}, "five_hour": {"utilization": 0.1}}}})
    usage.note_line(cfg, line)
    assert usage.week_pct(cfg) == 41.0


def _pipeline_with_cost(cfg, cost: float, caps: dict | None = None) -> None:
    import sqlite3
    root = cfg.pipeline_root_path
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text(json.dumps({"usage": caps or {}}), encoding="utf-8")
    c = sqlite3.connect(cfg.pipeline_db_path)
    c.execute("CREATE TABLE IF NOT EXISTS stage_runs (cost_usd REAL, started_at TEXT, finished_at TEXT)")
    c.execute("INSERT INTO stage_runs VALUES (?, ?, ?)", (cost, usage._iso(time.time()), usage._iso(time.time())))
    c.commit()
    c.close()


def test_the_pipeline_daily_cap_also_stops_autofill(cfg, tmp_path):
    """Alex, 2026-10-10: Smart Apply's runs share the project's 6% a day with the pipeline."""
    _write(cfg.data_dir / "usage_latest.json", 20.0, int(time.time()) + 3 * 86400)
    _pipeline_with_cost(cfg, 11.0 * 6.2)
    week, today = usage.project_shares(cfg)
    assert today == 6.2 and week == 6.2
    res = run_claude(cfg, prompt="hi", cwd=tmp_path / "cwd", log_path=tmp_path / "x.log", model="m", timeout_s=5)
    assert res.ok is False and res.subtype == "usage_brake" and "daily cap 6%" in res.result_text


def test_caps_come_from_the_pipeline_config(cfg):
    _write(cfg.data_dir / "usage_latest.json", 20.0, int(time.time()) + 3 * 86400)
    _pipeline_with_cost(cfg, 11.0 * 6.2, {"daily_cap_pct": 8})
    assert usage.brake_reason(cfg) is None
    _pipeline_with_cost(cfg, 0.0, {"enabled": False, "daily_cap_pct": 1})
    assert usage.brake_reason(cfg) is None
