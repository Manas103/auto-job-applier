"""Core: config, paths, db, state machine."""

from __future__ import annotations

import json
import threading

import pytest

from pipeline import db as dbmod
from pipeline import statemachine as sm
from pipeline.config import ConfigError, load_config
from pipeline.models import JobState, Listing, Stage
from pipeline.paths import file_url, short_slug, slugify, to_wsl_path, wsl_quote


# ----------------------------------------------------------------------------- config

def test_config_defaults_and_paths(cfg, tmp_project):
    assert cfg.project_root == tmp_project
    assert cfg.data_dir == tmp_project / "data"
    assert cfg.roles_dir == tmp_project / "roles"
    assert cfg.stages.ideamaker.max_budget_usd == 15
    assert cfg.stage("builder").min_projects == 1
    assert cfg.claude.model == "claude-opus-5"
    assert cfg.verify.compile is False
    assert cfg.get("nonexistent", 7) == 7
    assert "sources" in cfg.collector


def test_config_rejects_bad_stage(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"stages": {"bogus": {}}}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p, project_root=tmp_path)


def test_config_rejects_empty_claude_cmd(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"claude": {"cmd": []}}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p, project_root=tmp_path)


def test_config_overrides(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{}", encoding="utf-8")
    c = load_config(p, project_root=tmp_path, overrides={"worker": {"threads": 5}})
    assert c.worker.threads == 5
    assert c.worker.auto_continue is True


# ----------------------------------------------------------------------------- paths

def test_slugify_and_short_slug():
    assert slugify("Software Engineer Intern, C++ (Summer 2027)") == "Software_Engineer_Intern_C_Summer_2027"
    assert slugify("") == "x"
    assert short_slug("Optiver") == "optiver"
    assert short_slug("GE Appliances") == "geappliances"
    assert short_slug("DRW") == "drw"


def test_wsl_path_translation():
    p = r"C:\Users\you\Downloads\Total Job Application process flow\roles\X"
    assert to_wsl_path(p) == "/mnt/c/Users/you/Downloads/Total Job Application process flow/roles/X"
    assert wsl_quote(p) == "'/mnt/c/Users/you/Downloads/Total Job Application process flow/roles/X'"
    assert to_wsl_path("/tmp/x") == "/tmp/x"
    assert file_url(r"C:\a b\c.pdf").startswith("file:///C:/a b/c.pdf")


# ----------------------------------------------------------------------------- db

def _listing(**kw) -> Listing:
    base = dict(source="simplify", company="Acme", title="Software Engineer Intern", url="https://acme.com/j/1")
    base.update(kw)
    return Listing(**base)


def test_schema_idempotent_and_wal(cfg):
    c = dbmod.connect(cfg.db_path)
    dbmod.init_schema(c)
    dbmod.init_schema(c)
    assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert dbmod.get_setting(c, "schema_version") == "1"
    assert len(dbmod.get_setting(c, "csrf_token")) == 32
    c.close()


def test_insert_update_job_roundtrip(conn):
    jid = dbmod.insert_job(conn, _listing(locations=["Chicago, IL"], terms=["Summer 2027"]), "acme.com/j/1", "acme|software engineer intern|chicago")
    job = dbmod.job_by_id(conn, jid)
    assert job.company == "Acme" and job.locations == ["Chicago, IL"] and job.state == JobState.NEW
    dbmod.update_job(conn, jid, flags=["x"], active=False, state=JobState.PINGED)
    job = dbmod.job_by_id(conn, jid)
    assert job.flags == ["x"] and job.active is False and job.state == JobState.PINGED
    dbmod.add_flag(conn, jid, "y")
    dbmod.add_flag(conn, jid, "y")
    assert dbmod.job_by_id(conn, jid).flags == ["x", "y"]
    assert dbmod.job_by_key(conn, "acme.com/j/1").id == jid
    assert dbmod.count_by_state(conn) == {"pinged": 1}


def test_stage_runs_and_cost(conn):
    jid = dbmod.insert_job(conn, _listing(), "k", "f")
    r1 = dbmod.start_stage_run(conn, jid, "ideamaker", 1, "log1")
    dbmod.finish_stage_run(conn, r1, "succeeded", cost_usd=1.25, num_turns=10, fix_rounds=1)
    r2 = dbmod.start_stage_run(conn, jid, "builder", 1, "log2")
    dbmod.finish_stage_run(conn, r2, "failed", cost_usd=0.5, error="boom")
    assert dbmod.job_cost(conn, jid) == pytest.approx(1.75)
    assert dbmod.last_attempt(conn, jid, "ideamaker") == 1
    assert dbmod.last_attempt(conn, jid, "critic") == 0
    assert [r.status for r in dbmod.stage_runs_for_job(conn, jid)] == ["succeeded", "failed"]
    totals = dbmod.cost_totals(conn)
    assert totals["total"] == pytest.approx(1.75)
    assert all("minutes" in r and r["minutes"] >= 0 for r in totals["per_job"] + totals["per_stage"])


def test_concurrent_writers(cfg):
    c0 = dbmod.connect(cfg.db_path)
    dbmod.init_schema(c0)
    c0.close()
    errors: list[Exception] = []

    def work(n: int) -> None:
        try:
            c = dbmod.connect(cfg.db_path)
            for i in range(25):
                dbmod.insert_job(c, _listing(url=f"https://acme.com/{n}/{i}"), f"k{n}-{i}", "f")
            c.close()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    c = dbmod.connect(cfg.db_path)
    assert c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 100
    c.close()


def test_watchlist_crud(conn):
    wid = dbmod.watchlist_insert(conn, company="Acme", ats="greenhouse", board="acme", added_by="auto")
    assert dbmod.watchlist_find(conn, "greenhouse", "acme", None, None, None).id == wid
    assert dbmod.watchlist_find(conn, "lever", "acme", None, None, None) is None
    dbmod.watchlist_update(conn, wid, enabled=False, last_error="x")
    e = dbmod.watchlist_by_id(conn, wid)
    assert e.enabled is False and e.last_error == "x"
    dbmod.watchlist_delete(conn, wid)
    assert dbmod.watchlist_all(conn) == []


# ----------------------------------------------------------------------------- state machine

def test_every_documented_transition_is_legal(conn):
    for frm, tos in sm.TRANSITIONS.items():
        for to in tos:
            jid = dbmod.insert_job(conn, _listing(url=f"https://x/{frm.value}/{to.value}"), f"{frm.value}->{to.value}", "f")
            dbmod.update_job(conn, jid, state=frm, failed_stage="builder" if frm == JobState.FAILED else None)
            job = sm.transition(conn, jid, to, "test", failed_stage="ideamaker" if to == JobState.FAILED else None)
            assert job.state == to
            ev = dbmod.events_for_job(conn, jid)[-1]
            assert ev["from_state"] == frm.value and ev["to_state"] == to.value


@pytest.mark.parametrize("frm,to", [
    (JobState.NEW, JobState.DONE), (JobState.PINGED, JobState.JD_READY), (JobState.DONE, JobState.NEW),
    (JobState.JD_READY, JobState.BUILDING), (JobState.IGNORED, JobState.PINGED),
])
def test_illegal_transitions_raise(conn, frm, to):
    jid = dbmod.insert_job(conn, _listing(), "k", "f")
    dbmod.update_job(conn, jid, state=frm)
    with pytest.raises(sm.IllegalTransition):
        sm.transition(conn, jid, to, "test")
    assert dbmod.job_by_id(conn, jid).state == frm


def test_expect_from_claim_semantics(conn):
    jid = dbmod.insert_job(conn, _listing(), "k", "f")
    dbmod.update_job(conn, jid, state=JobState.JD_READY)
    sm.transition(conn, jid, JobState.IDEAMAKER_RUNNING, "claim", expect_from=JobState.JD_READY)
    with pytest.raises(sm.IllegalTransition):
        sm.transition(conn, jid, JobState.IDEAMAKER_RUNNING, "claim", expect_from=JobState.JD_READY)


def test_failed_sets_and_clears_failed_stage(conn):
    jid = dbmod.insert_job(conn, _listing(), "k", "f")
    dbmod.update_job(conn, jid, state=JobState.BUILDING)
    job = sm.transition(conn, jid, JobState.FAILED, "stage_failed", failed_stage="builder")
    assert job.failed_stage == "builder"
    assert sm.retry_target(job) == JobState.IDEAMAKER_DONE
    job = sm.transition(conn, jid, sm.retry_target(job), "retry")
    assert job.state == JobState.IDEAMAKER_DONE and job.failed_stage is None


def test_stage_mappings():
    assert sm.stage_after(JobState.JD_READY) == Stage.IDEAMAKER
    assert sm.stage_after(JobState.BUILT) == Stage.RECONCILE
    assert sm.stage_after(JobState.DONE) is None
    assert sm.stage_for_running_state(JobState.REVIEWING) == Stage.CRITIC
    assert sm.done_state_for_stage(Stage.CRITIC) == JobState.DONE
    assert sm.ready_state_for_stage(Stage.BUILDER) == JobState.IDEAMAKER_DONE
