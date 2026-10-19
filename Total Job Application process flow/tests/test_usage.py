"""The weekly usage brake (pipeline/usage.py): readings from claude's rate_limit_event lines, the project's share,
and which lanes may start a Claude call."""

from __future__ import annotations

import json
import time

from pipeline import db as dbmod
from pipeline import usage
from pipeline.models import Listing


def _event(week: float, resets_at: int, five: float = 0.1) -> str:
    return json.dumps({"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed", "unifiedWindows": {"five_hour": {"utilization": five, "resetsAt": resets_at - 3600},
                                                "seven_day": {"utilization": week, "resetsAt": resets_at}}}})


def _job_with_cost(conn, cost: float) -> None:
    jid = dbmod.insert_job(conn, Listing(source="s", company="Acme", title="SWE Intern", url=f"https://x/{cost}",
                                         locations=["NYC"], category="Software", terms=["Summer 2027"]), f"k{cost}", f"f{cost}")
    rid = dbmod.start_stage_run(conn, jid, "builder", 1, "x.log")
    dbmod.finish_stage_run(conn, rid, "succeeded", cost_usd=cost)


def test_reading_is_parsed_and_saved(cfg):
    reset = int(time.time()) + 3 * 86400
    assert usage.parse_event('{"type":"assistant"}') is None
    usage.note_line(cfg, _event(0.42, reset), "builder_1.log")
    r = usage.latest(cfg)
    assert r["week_pct"] == 42.0 and r["week_resets_at"] == reset and r["source"] == "builder_1.log"
    assert usage.history_path(cfg).read_text(encoding="utf-8").count("\n") == 1


def test_a_reading_from_before_the_reset_counts_as_zero(cfg):
    old_reset = int(time.time()) - 3600
    usage.note_line(cfg, _event(0.92, old_reset))
    r = usage.latest(cfg)
    assert r["week_pct"] == 0.0 and r["week_resets_at"] == old_reset + 7 * 86400


def test_hard_cap_stops_everything(cfg, conn):
    usage.note_line(cfg, _event(0.71, int(time.time()) + 86400))
    assert usage.check(cfg, conn, "full")[0] is False
    ok, why = usage.check(cfg, conn, "light")
    assert ok is False and "70%" in why


def test_project_share_stops_full_lane_only(cfg, conn):
    usage.note_line(cfg, _event(0.50, int(time.time()) + 86400))
    _job_with_cost(conn, 11.0 * 41)          # 41% of the week at the default 11 per 1%
    snap = usage.snapshot(cfg, conn)
    assert snap["project_pct"] == 41.0
    assert usage.check(cfg, conn, "full") == (False, snap["reason"])
    assert usage.check(cfg, conn, "light") == (True, None)


def test_runs_from_last_week_do_not_count(cfg, conn):
    reset = int(time.time()) + 86400            # this week started 6 days ago
    usage.note_line(cfg, _event(0.10, reset))
    _job_with_cost(conn, 500.0)
    conn.execute("UPDATE stage_runs SET started_at=?, finished_at=?", (usage._iso(reset - 8 * 86400),) * 2)
    conn.commit()
    assert usage.snapshot(cfg, conn)["project_pct"] == 0.0
    assert usage.check(cfg, conn, "full") == (True, None)


def test_brake_can_be_turned_off(cfg, conn):
    usage.note_line(cfg, _event(0.95, int(time.time()) + 86400))
    assert usage.check(cfg, conn, "full")[0] is False
    cfg.usage = {"enabled": False}
    assert usage.check(cfg, conn, "full") == (True, None)


def test_worker_holds_a_full_stage_while_the_brake_is_on(cfg, conn, monkeypatch):
    """The stage is not claimed: the job stays in its ready state and run_stage reports no progress."""
    import threading
    from pipeline.models import JobState, Stage
    from pipeline.roles import ensure_role_folder
    from pipeline.worker import JobRunner
    usage.note_line(cfg, _event(0.80, int(time.time()) + 86400))
    jid = dbmod.insert_job(conn, Listing(source="s", company="Acme", title="SWE Intern", url="https://x/1",
                                         locations=["NYC"], category="Software", terms=["Summer 2027"]), "k1", "f1")
    dbmod.update_job(conn, jid, jd_text="Responsibilities: x. " * 20)
    folder = ensure_role_folder(dbmod.job_by_id(conn, jid), cfg)
    dbmod.update_job(conn, jid, role_folder=str(folder), state=JobState.JD_READY)
    stop = threading.Event()
    monkeypatch.setattr(usage, "probe", lambda *a, **k: None)
    pool = type("P", (), {"stop_event": stop, "governor": None, "builder_lock": threading.Lock(), "job_object": None})()
    seen: list[str] = []
    runner = JobRunner(cfg, conn, pool, jid)
    monkeypatch.setattr(runner, "_status", lambda s: (seen.append(s), stop.set()))
    assert runner.run_stage(Stage.IDEAMAKER) is False
    assert dbmod.job_by_id(conn, jid).state == JobState.JD_READY
    assert seen and "usage brake" in seen[0] and "70%" in seen[0]


def test_daily_cap_holds_full_stages_until_tomorrow(cfg, conn):
    usage.note_line(cfg, _event(0.20, int(time.time()) + 3 * 86400))
    _job_with_cost(conn, 11.0 * 6.5)            # 6.5% of the week, all of it today
    snap = usage.snapshot(cfg, conn)
    assert snap["today_pct"] == 6.5 and snap["project_pct"] == 6.5
    ok, why = usage.check(cfg, conn, "full")
    assert ok is False and "daily cap" in why
    assert usage.check(cfg, conn, "light") == (True, None)
    conn.execute("UPDATE stage_runs SET started_at=?, finished_at=?", (usage._iso(time.time() - 86400 * 1.5),) * 2)
    conn.commit()
    snap = usage.snapshot(cfg, conn)
    assert snap["today_pct"] == 0.0 and snap["project_pct"] == 6.5 and snap["full_ok"] is True
