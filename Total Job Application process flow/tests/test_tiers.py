"""Company tiers and lanes: tier mapping, priority writes, the D-tier ping gate, archetype and light lanes, the
lite worker thread, recovery from tailoring, and the dashboard upgrade."""

from __future__ import annotations

import itertools
import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from pipeline import collector, db as dbmod, lite, rank, worker as wk
from pipeline.index import classify_archetype
from pipeline.models import JobState, Listing
from pipeline.statemachine import transition
from pipeline.util import now_iso

GOOD = Path(__file__).parent / "fixtures" / "tex" / "good"
_SEQ = itertools.count(1)


@pytest.fixture
def tiers_on(cfg):
    cfg.tiers.enabled = True
    return cfg


@pytest.fixture
def fake_log(cfg, monkeypatch):
    logp = cfg.data_dir / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(logp))
    return logp


def _calls(logp):
    return [json.loads(l) for l in logp.read_text(encoding="utf-8").splitlines()] if logp.exists() else []


def _script(cfg, monkeypatch, calls):
    p = cfg.data_dir / "fake_script.json"
    p.write_text(json.dumps({"calls": calls}), encoding="utf-8")
    st = Path(str(p) + ".state")
    if st.exists():
        st.unlink()
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(p))


def _score(conn, company, score, tier=""):
    conn.execute("INSERT OR REPLACE INTO company_scores(company, score, tier, scored_at) VALUES (?,?,?,?)",
                 (company, score, tier, now_iso()))


def _mk_job(conn, cfg, *, company="Acme Inc.", state=JobState.JD_READY, url=None, title="Software Engineer Intern"):
    url = url or f"https://example.com/jobs/{company.replace(' ', '-')}-{next(_SEQ)}"
    jid = dbmod.insert_job(conn, Listing(source="s", company=company, title=title, url=url, locations=["Chicago, IL"],
                                         category="Software", terms=["Summer 2027"]), url, "f")
    dbmod.update_job(conn, jid, jd_text="Build backend services and REST APIs on a cloud platform. " * 10)
    from pipeline.roles import ensure_role_folder
    folder = ensure_role_folder(dbmod.job_by_id(conn, jid), cfg)
    dbmod.update_job(conn, jid, role_folder=str(folder), state=state)
    return dbmod.job_by_id(conn, jid), folder


def _archetype(cfg, name="backend", approved=True):
    d = lite.archetypes_dir(cfg) / name
    d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(GOOD / "ideal_candidate_realistic_t.tex", d / "resume.tex")
    shutil.copyfile(GOOD / "ideal_candidate_realistic_t.pdf", d / "resume.pdf")
    shutil.copyfile(GOOD / "experience_descriptions_t.md", d / "experience_descriptions.md")
    projects = [dict(p, relationship="reuse") for p in json.loads((GOOD / "meta.json").read_text(encoding="utf-8"))["projects"]]
    (d / "meta.json").write_text(json.dumps({"name": name, "version": 1, "approved": approved, "projects": projects}),
                                 encoding="utf-8")
    return d


# ----------------------------------------------------------------------------- tiers and lanes

def test_tier_from_stored_tier_or_score(cfg, conn):
    _score(conn, "Stored", 40, "A")
    _score(conn, "Scored", 91)
    _score(conn, "Low", 20, "?")
    assert rank.company_tier(conn, cfg, "Stored") == "A"
    assert rank.company_tier(conn, cfg, "Scored") == "S"
    assert rank.company_tier(conn, cfg, "Low") == "D"
    assert rank.company_tier(conn, cfg, "Never Heard Of It") == "C"  # DEFAULT_SCORE 50
    assert [rank.tier_for_score(cfg, s) for s in (90, 75, 55, 35, 34)] == ["S", "A", "B", "C", "D"]


def test_lane_by_tier_override_and_disabled(cfg, tiers_on, conn):
    _score(conn, "Big", 95, "S")
    _score(conn, "Mid", 60, "B")
    _score(conn, "Small", 20, "D")
    big, _ = _mk_job(conn, cfg, company="Big")
    mid, _ = _mk_job(conn, cfg, company="Mid")
    small, _ = _mk_job(conn, cfg, company="Small")
    assert [rank.job_lane(conn, cfg, j.id) for j in (big, mid, small)] == ["full", "light", "archetype"]
    dbmod.update_job(conn, small.id, flags=["lane:full"])
    assert rank.job_lane(conn, cfg, small.id) == "full"
    cfg.tiers.enabled = False
    assert rank.job_lane(conn, cfg, mid.id) == "full"


def test_classify_archetype():
    assert classify_archetype("Software Engineer Intern", "") == "backend"
    assert classify_archetype("Quantitative Trading Intern", "") == "quant"
    assert classify_archetype("Marketing Intern", "Own the marketing calendar.") == "backend"  # marketing is not market
    assert classify_archetype("Data Analyst Intern", "SQL and Tableau dashboards") == "data"
    assert classify_archetype("Machine Learning Engineer Intern", "") == "ml"
    assert classify_archetype("Flight Software Intern", "embedded C on avionics") == "systems"


# ----------------------------------------------------------------------------- enqueue

def test_enqueue_writes_priority_and_scores_unscored_company_first(cfg, conn, monkeypatch):
    cfg.rank.enabled = True
    asked: list[list[str]] = []

    def fake_ask(cfg_, names, model):
        asked.append(list(names))
        return {n: {"score": 88, "tier": "A", "reason": "x"} for n in names}

    monkeypatch.setattr(rank, "_ask", fake_ask)
    job, _ = _mk_job(conn, cfg, company="Fresh Co")
    pool = wk.WorkerPool(cfg, cfg.db_path, n_threads=1)
    assert pool.enqueue(job.id)
    assert asked == [["Fresh Co"]]
    assert dbmod.job_by_id(conn, job.id).priority == 88
    assert pool.q.get_nowait()[0] == -88


def test_lite_job_waits_until_an_archetype_is_approved(cfg, tiers_on, conn, fake_log):
    _score(conn, "Small Co", 30, "D")
    job, _ = _mk_job(conn, cfg, company="Small Co")
    pool = wk.WorkerPool(cfg, cfg.db_path, n_threads=1)
    assert not pool.enqueue(job.id)
    j = dbmod.job_by_id(conn, job.id)
    assert "awaiting_lane" in j.flags and j.state == JobState.JD_READY and pool.queue_snapshot() == []
    _archetype(cfg)
    pool = wk.WorkerPool(cfg, cfg.db_path, n_threads=1)
    pool.recover(conn)  # a restart picks held jobs up once the library is ready
    assert pool.queue_snapshot() == [job.id] and pool.lite_q.qsize() == 1 and pool.q.qsize() == 0
    assert "awaiting_lane" not in dbmod.job_by_id(conn, job.id).flags


def test_enqueue_routes_by_lane(cfg, tiers_on, conn):
    _archetype(cfg)
    _score(conn, "Big", 95, "S")
    _score(conn, "Small", 20, "D")
    big, _ = _mk_job(conn, cfg, company="Big")
    small, _ = _mk_job(conn, cfg, company="Small")
    mid_pipeline, _ = _mk_job(conn, cfg, company="Small", state=JobState.IDEAMAKER_DONE)
    pool = wk.WorkerPool(cfg, cfg.db_path, n_threads=1)
    assert pool.enqueue(big.id) and pool.enqueue(small.id) and pool.enqueue(mid_pipeline.id)
    assert sorted(x[2] for x in pool.q.queue) == sorted([big.id, mid_pipeline.id])  # started full, finishes full
    assert [x[2] for x in pool.lite_q.queue] == [small.id]


# ----------------------------------------------------------------------------- lanes end to end

def test_archetype_lane_reaches_done_without_claude(cfg, tiers_on, conn, fake_log):
    _archetype(cfg)
    _score(conn, "Small Co", 30, "D")
    job, folder = _mk_job(conn, cfg, company="Small Co", state=JobState.APPROVED)
    assert wk.run_one(cfg, job.id) == JobState.DONE
    assert _calls(fake_log) == []
    slug = "small"
    assert (folder / f"ideal_candidate_realistic_{slug}.pdf").exists()
    assert (folder / f"experience_descriptions_{slug}.md").exists()
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert (meta["lane"], meta["policy"], meta["archetype"], meta["company"]) == ("archetype", "archetype", "backend", "Small Co")
    runs = dbmod.stage_runs_for_job(conn, job.id)
    assert [(r.stage, r.status, r.cost_usd) for r in runs] == [("lite", "succeeded", 0.0)]
    events = [e["event"] for e in dbmod.events_for_job(conn, job.id)]
    assert "lite_started" in events and events[-1] == "lite_finished"


def test_light_lane_makes_one_claude_call_on_the_lite_model(cfg, tiers_on, conn, fake_log, monkeypatch):
    _archetype(cfg)
    _score(conn, "Mid Co", 60, "B")
    cfg.lite.model = "lite-model-x"
    _script(cfg, monkeypatch, [{"match": {"resume": None}, "result": {"subtype": "success", "cost": 0.8, "turns": 9}}])
    job, folder = _mk_job(conn, cfg, company="Mid Co")
    assert wk.run_one(cfg, job.id) == JobState.DONE
    calls = _calls(fake_log)
    assert len(calls) == 1
    argv = calls[0]["argv"]
    assert argv[argv.index("--model") + 1] == "lite-model-x"
    assert "light" in calls[0]["prompt"] and "check lite" in calls[0]["prompt"]
    assert json.loads((folder / "meta.json").read_text(encoding="utf-8"))["lane"] == "light"
    assert dbmod.job_cost(conn, job.id) == pytest.approx(0.8)


def test_light_lane_failure_retries_from_jd_ready(cfg, tiers_on, conn, fake_log, monkeypatch):
    _archetype(cfg)
    _score(conn, "Mid Co", 60, "B")
    cfg.lite.max_fix_rounds = 0
    _script(cfg, monkeypatch, [{"match": {"resume": None}, "result": {"subtype": "error_during_execution", "cost": 0.3}}])
    job, _ = _mk_job(conn, cfg, company="Mid Co")
    assert wk.run_one(cfg, job.id) == JobState.FAILED
    j = dbmod.job_by_id(conn, job.id)
    assert j.failed_stage == "lite"
    from pipeline.statemachine import retry_target
    assert retry_target(j) == JobState.JD_READY


def test_s_tier_still_runs_the_full_pipeline(cfg, tiers_on, conn, fake_log, monkeypatch):
    _archetype(cfg)
    _score(conn, "Top Co", 97, "S")
    _script(cfg, monkeypatch, [{"match": {"resume": None}, "artifacts": str(GOOD), "result": {"subtype": "success", "cost": 2.0}}])
    job, _ = _mk_job(conn, cfg, company="Top Co")
    assert wk.run_one(cfg, job.id, stop_after=wk.Stage.IDEAMAKER) == JobState.IDEAMAKER_DONE
    assert [r.stage for r in dbmod.stage_runs_for_job(conn, job.id)] == ["ideamaker"]


def test_lite_jobs_do_not_wait_for_the_builder(cfg, tiers_on, conn, fake_log):
    _archetype(cfg)
    _score(conn, "Small Co", 30, "D")
    job, _ = _mk_job(conn, cfg, company="Small Co")
    pool = wk.WorkerPool(cfg, cfg.db_path, n_threads=1)
    pool.builder_lock.acquire()  # a build is running
    pool.start()
    try:
        assert pool.enqueue(job.id)
        deadline = time.time() + 20
        while time.time() < deadline and dbmod.job_by_id(conn, job.id).state != JobState.DONE:
            time.sleep(0.1)
        assert dbmod.job_by_id(conn, job.id).state == JobState.DONE
    finally:
        pool.builder_lock.release()
        pool.stop(timeout=5)


def test_recover_rolls_tailoring_back(cfg, tiers_on, conn, fake_log):
    _archetype(cfg)
    _score(conn, "Small Co", 30, "D")
    job, _ = _mk_job(conn, cfg, company="Small Co")
    transition(conn, job.id, JobState.TAILORING, "lite_started")
    rid = dbmod.start_stage_run(conn, job.id, "lite", 1, "log")
    other, _ = _mk_job(conn, cfg, company="Small Co")
    transition(conn, other.id, JobState.TAILORING, "lite_started")  # no run row at all
    pool = wk.WorkerPool(cfg, cfg.db_path, n_threads=1)
    recovered = pool.recover(conn)
    assert sorted(recovered) == sorted([job.id, other.id])
    assert dbmod.stage_run_by_id(conn, rid).status == "aborted"
    assert {dbmod.job_by_id(conn, j).state for j in (job.id, other.id)} == {JobState.JD_READY}
    assert sorted(x[2] for x in pool.lite_q.queue) == sorted([job.id, other.id])


# ----------------------------------------------------------------------------- collector ping gate

def test_d_tier_is_ignored_not_pinged_and_best_pinged_first(cfg, tiers_on, conn, monkeypatch):
    pinged: list[int] = []
    monkeypatch.setattr(collector, "notify_job", lambda c, cf, job: pinged.append(job.id) or True)
    monkeypatch.setattr(collector, "in_quiet_hours", lambda cf: False)
    _score(conn, "Weak Co", 20, "D")
    _score(conn, "Okay Co", 50, "C")
    _score(conn, "Top Co", 97, "S")
    ids = {}
    for company in ("Weak Co", "Okay Co", "Top Co"):
        jid = dbmod.insert_job(conn, Listing(source="s", company=company, title="SWE Intern", url=f"https://x.com/{company}"),
                               f"x.com/{company}", "f")
        dbmod.update_job(conn, jid, flags=["ping_deferred"])
        ids[company] = jid
    rep = collector.CollectReport()
    collector.screen_and_ping(conn, cfg, [], notify=True, seed=False, report=rep)
    assert pinged == [ids["Top Co"], ids["Okay Co"]]
    weak = dbmod.job_by_id(conn, ids["Weak Co"])
    assert weak.state == JobState.IGNORED and weak.decided_at is None and "ping_deferred" not in weak.flags
    assert dbmod.events_for_job(conn, weak.id)[-1]["event"] == "ignored_tier"
    assert rep.tier_ignored == 1 and rep.pinged == 2


# ----------------------------------------------------------------------------- backfill CLI

def test_tiers_backfill_dry_run_then_real(cfg, tiers_on, conn, tmp_project, capsys):
    cfg_path = tmp_project / "config.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data["tiers"] = {"enabled": True}
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    _score(conn, "Weak Co", 20, "D")
    _score(conn, "Top Co", 97, "S")
    weak = dbmod.insert_job(conn, Listing(source="s", company="Weak Co", title="SWE Intern", url="https://x.com/w"), "x.com/w", "f")
    dbmod.update_job(conn, weak, state=JobState.PINGED)
    top = dbmod.insert_job(conn, Listing(source="s", company="Top Co", title="SWE Intern", url="https://x.com/t"), "x.com/t", "f")
    dbmod.update_job(conn, top, state=JobState.PINGED)
    held, _ = _mk_job(conn, cfg, company="Weak Co", state=JobState.APPROVED)
    from pipeline.__main__ import main
    assert main(["--config", str(cfg_path), "tiers", "backfill", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would move 1" in out and "would flag 1" in out
    assert dbmod.job_by_id(conn, weak).state == JobState.PINGED
    assert main(["--config", str(cfg_path), "tiers", "backfill"]) == 0
    assert dbmod.job_by_id(conn, weak).state == JobState.IGNORED
    assert dbmod.job_by_id(conn, top).state == JobState.PINGED and dbmod.job_by_id(conn, top).priority == 97
    assert "awaiting_lane" in dbmod.job_by_id(conn, held.id).flags


# ----------------------------------------------------------------------------- archetype library

def test_check_and_approve_archetype(cfg, conn):
    d = _archetype(cfg, "data", approved=False)
    assert not lite.library_ready(cfg)
    assert lite.check_archetype(cfg, "data") == []
    lite.set_approved(cfg, "data", True)
    assert lite.approved_archetypes(cfg) == ["data"]
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    meta["projects"][0]["relationship"] = "new"
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    assert any(f.code == "meta_invalid" for f in lite.check_archetype(cfg, "data"))
