"""Stage prompts and checks; worker end-to-end with the fake claude; recovery in-process."""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from pipeline import db as dbmod, index as index_mod, worker as wk
from pipeline.models import Failure, JobState, Listing, Stage
from pipeline.stages import builder, critic, get_specs, ideamaker, reconcile
from pipeline.statemachine import transition

GOOD = Path(__file__).parent / "fixtures" / "tex" / "good"


def _mk_job(conn, cfg, *, state=JobState.JD_READY, company="Acme Inc.", url="https://job-boards.greenhouse.io/acme/jobs/4001"):
    jid = dbmod.insert_job(conn, Listing(source="s", company=company, title="Software Engineer Intern", url=url,
                                         locations=["Chicago, IL"], category="Software", terms=["Summer 2027"]), url, "f")
    dbmod.update_job(conn, jid, jd_text="Responsibilities: build trading tools. Qualifications: student. " * 10)
    from pipeline.roles import ensure_role_folder
    job = dbmod.job_by_id(conn, jid)
    folder = ensure_role_folder(job, cfg)
    dbmod.update_job(conn, jid, role_folder=str(folder), state=state)
    return dbmod.job_by_id(conn, jid), folder


def _script(cfg, calls):
    p = cfg.data_dir / "fake_script.json"
    p.write_text(json.dumps({"calls": calls}), encoding="utf-8")
    st = Path(str(p) + ".state")
    if st.exists():
        st.unlink()
    return p


def _fake_repo(cfg, name="fake-repo"):
    d = cfg.projects_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "README.md").write_text("# fake\n", encoding="utf-8")
    return d


def _build_report(meets=True, status="ok", path="projects/fake-repo"):
    return {"status": status, "built_at": "2026-08-23", "machine": "test", "month_built": "Aug. 2026",
            "projects": [{"title": "Market Data Decoder and Tick Capture Pipeline", "name": "fake-repo", "path": path,
                          "relationship": "new", "repo_url": "https://github.com/alexdoe-dev/fake-repo", "commit": "abc",
                          "platform": "WSL2", "build_ok": True, "tests_ok": True, "attempts": 1,
                          "benchmarks": [{"claim": "1M+ messages/sec", "value": 815678 if not meets else 1200000, "unit": "msgs/sec", "meets_claim": meets}]},
                         {"title": "Model Validation and Alerting Framework", "name": "fake-repo", "path": path, "relationship": "reuse",
                          "repo_url": "https://github.com/alexdoe-dev/fake-repo", "commit": "abc", "platform": "Windows", "build_ok": True,
                          "tests_ok": True, "attempts": 1, "benchmarks": [{"claim": "200+ rules per second", "value": 300, "meets_claim": True}]},
                         {"title": "Lock-Free Order Matching Engine", "name": "fake-repo", "path": path, "relationship": "reuse",
                          "repo_url": "https://github.com/alexdoe-dev/fake-repo", "commit": "abc", "platform": "WSL2", "build_ok": True,
                          "tests_ok": True, "attempts": 1, "benchmarks": [{"claim": "500K+ orders/sec", "value": 5000000, "meets_claim": True}]}]}


def _review(verdict="pass", high=0):
    fixes = [{"file": "no_brainer_hire_acme.md", "severity": "high", "issue": "x", "fix": "y"}] * high
    return {"verdict": verdict, "score": "27/29", "hard_failures": high, "criteria": [], "fixes": fixes, "exemplars": []}


def _full_script(cfg, *, omit_first=None, builder_report=None, review_first="pass"):
    calls = [{"match": {"resume": None}, "artifacts": str(GOOD), "omit": omit_first or [], "result": {"subtype": "success", "cost": 2.0, "turns": 20}}]
    if omit_first:
        calls.append({"match": {"resume": "*"}, "artifacts": str(GOOD), "result": {"subtype": "success", "cost": 2.5, "turns": 4}})
    calls.append({"match": {"resume": None}, "write": {"build_report.json": builder_report or _build_report()}, "result": {"subtype": "success", "cost": 5.0, "turns": 50}})
    calls.append({"match": {"resume": None}, "write": {"reconciliation.md": "# Reconciliation\n\n| a |\n"}, "result": {"subtype": "success", "cost": 1.0}})
    calls.append({"match": {"resume": None}, "write": {"review.md": "# Review\nVerdict: PASS\n", "review.json": _review(review_first, 1 if review_first == "revise" else 0)},
                  "result": {"subtype": "success", "cost": 0.7}})
    if review_first == "revise":
        calls.append({"match": {"resume": "*"}, "write": {"review.md": "# Review\nVerdict: PASS\n", "review.json": _review("pass")}, "result": {"subtype": "success", "cost": 1.0}})
    return _script(cfg, calls)


@pytest.fixture
def fake_env(cfg, monkeypatch):
    logp = cfg.data_dir / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(logp))
    cfg.stages.builder.verify_remote = False
    return logp


def _calls(logp):
    return [json.loads(l) for l in logp.read_text(encoding="utf-8").splitlines()] if logp.exists() else []


# ----------------------------------------------------------------------------- prompts and checks

def test_prompts_contain_contract(cfg, conn):
    job, folder = _mk_job(conn, cfg)
    p = ideamaker.build_prompt(job, cfg, folder, {})
    assert str(cfg.playbooks_dir / "IDEAMAKER.md") in p and str(folder / "jd.txt") in p and "TODAY: 2026-08-22" in p
    assert "ideal_candidate_full_acme.tex" in p and "no_brainer_hire_acme.md" in p and "PIPELINE_RESULT" in p
    assert "experience_descriptions_acme.md" in p
    from pipeline.stages.experience import build_backfill_prompt, check as exp_check
    bp = build_backfill_prompt(job, cfg, folder)
    assert "experience_descriptions_acme.md" in bp and "IDEAMAKER.md" in bp and "$" not in bp.replace("$PATH", "")
    assert any(f.code == "missing_file" for f in exp_check(folder, "acme"))
    assert any(f in p for f in ("Optiver_SWE_Intern_Summer2027", "DRW_SWE_Intern_Summer2027", "Hudson_River_Trading")), "no trading exemplar"
    assert "$" not in p.replace("$PATH", "")  # all placeholders substituted
    b = builder.build_prompt(job, cfg, folder, {})
    assert "BUILDER.md" in b and "wsl.exe -d Ubuntu-22.04" in b and "/mnt/" in b and "build_report.json" in b
    assert "no_brainer" not in b.replace("no_brainer document", "")  # builder is blind to the no-brainer
    r = reconcile.build_prompt(job, cfg, folder, {})
    assert "RECONCILE.md" in r and "reconciliation.md" in r and "experience_descriptions_acme.md" in r
    c = critic.build_prompt(job, cfg, folder, {})
    assert "RUBRIC.md" in c and "review.json" in c


def test_ideamaker_check_and_fix_prompt(cfg, conn):
    job, folder = _mk_job(conn, cfg)
    fails = ideamaker.check(folder, job, cfg)
    assert any(f.code == "missing_file" for f in fails)
    for p in GOOD.iterdir():
        shutil.copy(p, folder / p.name.replace("_t", "_acme"))
    assert ideamaker.check(folder, job, cfg) == []
    fp = ideamaker.fix_prompt([Failure("page_count", "x.tex", None, "2 pages", "tighten")], folder, job, cfg)
    assert "[page_count] x.tex: 2 pages. Fix: tighten" in fp and "Fix only these" in fp
    ideamaker.on_success(folder, job, cfg)
    assert index_mod.has_row(cfg, folder.name)
    row = next(r for r in index_mod.read_rows(cfg) if r.folder == folder.name)
    assert row.name == "Mira Kaplan" and "market-data-tick-capture" in row.repos


def test_builder_check(cfg, conn):
    cfg.stages.builder.verify_remote = False
    job, folder = _mk_job(conn, cfg)
    shutil.copy(GOOD / "meta.json", folder / "meta.json")
    assert builder.check(folder, job, cfg)[0].code == "missing_file"
    _fake_repo(cfg)
    (folder / "build_report.json").write_text(json.dumps(_build_report()), encoding="utf-8")
    assert builder.check(folder, job, cfg) == []
    bad = _build_report(status="partial", path="projects/nope")
    bad["projects"][0]["build_ok"] = False
    del bad["projects"][2]
    (folder / "build_report.json").write_text(json.dumps(bad), encoding="utf-8")
    codes = {f.code for f in builder.check(folder, job, cfg)}
    assert {"build_status", "build_path_missing", "build_failed", "build_missing_project"} <= codes
    builder.on_success(folder, job, cfg)


def test_reconcile_check_detects_unmet_claim(cfg, conn):
    job, folder = _mk_job(conn, cfg)
    for p in GOOD.iterdir():
        shutil.copy(p, folder / p.name.replace("_t", "_acme"))
    (folder / "build_report.json").write_text(json.dumps(_build_report(meets=False)), encoding="utf-8")
    fails = reconcile.check(folder, job, cfg)
    codes = [f.code for f in fails]
    assert "missing_file" in codes and "unreconciled_claim" in codes
    (folder / "reconciliation.md").write_text("# R\n", encoding="utf-8")
    tex = folder / "ideal_candidate_realistic_acme.tex"
    tex.write_text(tex.read_text(encoding="utf-8").replace("1M+ messages/sec", "815K messages/sec"), encoding="utf-8")
    assert reconcile.check(folder, job, cfg) == []


def test_critic_check(cfg, conn):
    job, folder = _mk_job(conn, cfg)
    assert {f.file for f in critic.check(folder, job, cfg)} == {"review.md", "review.json"}
    (folder / "review.md").write_text("x", encoding="utf-8")
    (folder / "review.json").write_text(json.dumps(_review("revise", 2)), encoding="utf-8")
    f = critic.check(folder, job, cfg)
    assert len(f) == 1 and f[0].code == "review_revise" and "2 high" in f[0].message
    (folder / "review.json").write_text(json.dumps(_review("pass")), encoding="utf-8")
    assert critic.check(folder, job, cfg) == []


# ----------------------------------------------------------------------------- worker e2e

def test_full_pipeline_happy_path(cfg, conn, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_full_script(cfg)))
    _fake_repo(cfg)
    job, folder = _mk_job(conn, cfg)
    state = wk.run_one(cfg, job.id)
    assert state == JobState.DONE
    runs = dbmod.stage_runs_for_job(conn, job.id)
    assert [r.stage for r in runs] == ["ideamaker", "builder", "reconcile", "critic"]
    assert all(r.status == "succeeded" for r in runs)
    assert dbmod.job_cost(conn, job.id) == pytest.approx(8.7)
    assert (folder / "review.json").exists() and index_mod.has_row(cfg, folder.name)
    calls = _calls(fake_env)
    assert [c["env"]["PIPELINE_STAGE"] for c in calls] == ["ideamaker", "builder", "reconcile", "critic"]
    assert all(c["cwd"] == str(folder) for c in calls)


def test_fix_round_resumes_session(cfg, conn, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_full_script(cfg, omit_first=["no_brainer_hire_t.md"])))
    job, folder = _mk_job(conn, cfg)
    state = wk.run_one(cfg, job.id, stop_after=Stage.IDEAMAKER)
    assert state == JobState.IDEAMAKER_DONE
    run = dbmod.stage_runs_for_job(conn, job.id)[0]
    assert run.fix_rounds == 1 and run.cost_usd == pytest.approx(2.5)
    calls = _calls(fake_env)
    assert calls[0]["resume"] is None and calls[1]["resume"] == "fake-1"
    assert "[missing_file] no_brainer_hire_acme.md" in calls[1]["prompt"]


def test_exhausted_fix_rounds_fail_the_stage(cfg, conn, fake_env, monkeypatch):
    calls = [{"match": {"resume": "any"}, "reusable": True, "artifacts": str(GOOD), "omit": ["no_brainer_hire_t.md"], "result": {"subtype": "success", "cost": 1}}]
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_script(cfg, calls)))
    cfg.stages.ideamaker.max_fix_rounds = 2
    job, folder = _mk_job(conn, cfg)
    assert wk.run_one(cfg, job.id) == JobState.FAILED
    j = dbmod.job_by_id(conn, job.id)
    assert j.failed_stage == "ideamaker"
    run = dbmod.stage_runs_for_job(conn, job.id)[0]
    assert run.status == "failed" and run.fix_rounds == 2 and "checks failed" in run.error
    # Retry moves it back to jd_ready and counts a new attempt; max_attempts=2 then fails permanently.
    cfg.stages.ideamaker.max_attempts = 2
    assert wk.run_one(cfg, job.id) == JobState.FAILED
    assert len(dbmod.stage_runs_for_job(conn, job.id)) == 2
    assert wk.run_one(cfg, job.id) == JobState.FAILED
    ev = dbmod.events_for_job(conn, job.id)[-1]
    assert "max attempts" in json.dumps(ev["detail"])


def test_claude_error_fails_stage_and_budget_guard(cfg, conn, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_script(cfg, [{"exit_code": 1, "result": {"subtype": "success"}}])))
    job, folder = _mk_job(conn, cfg)
    assert wk.run_one(cfg, job.id) == JobState.FAILED
    assert "exit 1" in dbmod.stage_runs_for_job(conn, job.id)[0].error
    cfg.worker.job_budget_usd = 1
    job2, _ = _mk_job(conn, cfg, url="https://x.com/2")
    assert wk.run_one(cfg, job2.id) == JobState.FAILED
    assert "budget" in json.dumps(dbmod.events_for_job(conn, job2.id)[-1]["detail"])


def test_builder_failure_and_critic_revise_then_pass(cfg, conn, fake_env, monkeypatch):
    _fake_repo(cfg)
    bad = _build_report(status="partial")
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_full_script(cfg, builder_report=bad)))
    cfg.stages.builder.max_fix_rounds = 0
    job, folder = _mk_job(conn, cfg)
    assert wk.run_one(cfg, job.id) == JobState.FAILED
    assert dbmod.job_by_id(conn, job.id).failed_stage == "builder"

    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_full_script(cfg, review_first="revise")))
    job2, _ = _mk_job(conn, cfg, url="https://x.com/2")
    assert wk.run_one(cfg, job2.id) == JobState.DONE
    crit = [r for r in dbmod.stage_runs_for_job(conn, job2.id) if r.stage == "critic"][0]
    assert crit.fix_rounds == 1


def test_auto_continue_off_returns_to_backlog(cfg, conn, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_full_script(cfg)))
    cfg.worker.auto_continue = False
    job, _ = _mk_job(conn, cfg)
    assert wk.run_one(cfg, job.id) == JobState.IDEAMAKER_DONE


def test_prepare_approved_job(cfg, conn, fake_env, monkeypatch):
    from pipeline import worker as w
    monkeypatch.setattr(w, "fetch_jd", lambda job, cfg_, allow_browser: __import__("pipeline.jd", fromlist=["JDResult"]).JDResult(
        "Responsibilities: x. Qualifications: graduating between December 2027 and June 2028. " * 5, "html", job.url, attempts=[]))
    jid = dbmod.insert_job(conn, Listing(source="s", company="Acme", title="SWE Intern", url="https://job-boards.greenhouse.io/acme/jobs/5",
                                         locations=["Chicago, IL"], category="Software", terms=["Summer 2027"]), "gh:5", "f")
    dbmod.update_job(conn, jid, state=JobState.APPROVED)
    runner = w.JobRunner(cfg, conn, None, jid)
    job = runner.prepare()
    assert job.state == JobState.JD_READY and job.role_folder and Path(job.role_folder).exists()
    assert dbmod.watchlist_all(conn)[0].board == "acme"
    # Post-approval hard gate.
    monkeypatch.setattr(w, "fetch_jd", lambda job, cfg_, allow_browser: __import__("pipeline.jd", fromlist=["JDResult"]).JDResult(
        "Responsibilities: x. Qualifications: graduating by May 2027. " * 5, "html", job.url, attempts=[]))
    jid2 = dbmod.insert_job(conn, Listing(source="s", company="Beta", title="SWE Intern", url="https://beta.com/1"), "beta.com/1", "f")
    dbmod.update_job(conn, jid2, state=JobState.APPROVED)
    assert w.JobRunner(cfg, conn, None, jid2).prepare().state == JobState.SCREENED_OUT
    # No JD at all.
    monkeypatch.setattr(w, "fetch_jd", lambda job, cfg_, allow_browser: __import__("pipeline.jd", fromlist=["JDResult"]).JDResult(None, "none", job.url, attempts=[]))
    jid3 = dbmod.insert_job(conn, Listing(source="s", company="Gamma", title="SWE Intern", url="https://gamma.com/1"), "gamma.com/1", "f")
    dbmod.update_job(conn, jid3, state=JobState.APPROVED)
    assert w.JobRunner(cfg, conn, None, jid3).prepare().state == JobState.NEEDS_JD


# ----------------------------------------------------------------------------- pool, locks, recovery

def test_pool_serializes_builders_and_overlaps_ideamakers(cfg, conn, fake_env, monkeypatch):
    _fake_repo(cfg)
    calls = [{"match": {"resume": "any"}, "reusable": True, "sleep_s": 1.5, "write": {"build_report.json": _build_report()},
              "result": {"subtype": "success", "cost": 0.1}}]
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_script(cfg, calls)))
    cfg.worker.auto_continue = False
    a, fa = _mk_job(conn, cfg, state=JobState.IDEAMAKER_DONE)
    b, fb = _mk_job(conn, cfg, state=JobState.IDEAMAKER_DONE, url="https://x.com/2")
    for f in (fa, fb):
        shutil.copy(GOOD / "meta.json", f / "meta.json")
    pool = wk.WorkerPool(cfg, cfg.db_path, n_threads=2)
    pool.start()
    assert pool.enqueue(a.id) and pool.enqueue(b.id) and not pool.enqueue(a.id)
    deadline = time.time() + 30
    while time.time() < deadline:
        states = {dbmod.job_by_id(conn, a.id).state, dbmod.job_by_id(conn, b.id).state}
        if states == {JobState.BUILT}:
            break
        time.sleep(0.2)
    pool.stop(timeout=5)
    assert {dbmod.job_by_id(conn, a.id).state, dbmod.job_by_id(conn, b.id).state} == {JobState.BUILT}
    runs = sorted(dbmod.stage_runs_for_job(conn, a.id) + dbmod.stage_runs_for_job(conn, b.id), key=lambda r: r.started_at)
    assert runs[1].started_at >= runs[0].finished_at  # builder lock serialized them

    # Ideamakers overlap: two jobs with a sleeping fake claude finish in ~1 round, not 2.
    calls = [{"match": {"resume": "any"}, "reusable": True, "sleep_s": 2, "artifacts": str(GOOD), "result": {"subtype": "success", "cost": 0.1}}]
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_script(cfg, calls)))
    c, _ = _mk_job(conn, cfg, url="https://x.com/3")
    d, _ = _mk_job(conn, cfg, url="https://x.com/4")
    pool = wk.WorkerPool(cfg, cfg.db_path, n_threads=2)
    pool.start()
    t0 = time.time()
    pool.enqueue(c.id)
    pool.enqueue(d.id)
    while time.time() - t0 < 30:
        if {dbmod.job_by_id(conn, c.id).state, dbmod.job_by_id(conn, d.id).state} == {JobState.IDEAMAKER_DONE}:
            break
        time.sleep(0.2)
    elapsed = time.time() - t0
    pool.stop(timeout=5)
    assert elapsed < 3.8, elapsed


def test_recover_in_process(cfg, conn, fake_env):
    job, folder = _mk_job(conn, cfg)
    transition(conn, job.id, JobState.IDEAMAKER_RUNNING, "stage_started")
    rid = dbmod.start_stage_run(conn, job.id, "ideamaker", 1, "log")
    dbmod.update_stage_run(conn, rid, session_id="sess-1")
    pool = wk.WorkerPool(cfg, cfg.db_path, n_threads=1)
    recovered = pool.recover(conn)
    assert recovered == [job.id]
    assert dbmod.stage_run_by_id(conn, rid).status == "aborted"
    j = dbmod.job_by_id(conn, job.id)
    assert j.state == JobState.JD_READY
    assert dbmod.get_setting(conn, f"resume_hint:{job.id}:ideamaker") == "sess-1"
    assert dbmod.events_for_job(conn, job.id)[-1]["event"] == "recovered"
    assert pool.queue_snapshot() == [job.id]


def test_recover_job_claimed_without_a_run(cfg, conn, fake_env):
    """Claimed `building` while waiting for the builder lock, then the service died: no stage run exists."""
    job, folder = _mk_job(conn, cfg, state=JobState.IDEAMAKER_DONE)
    transition(conn, job.id, JobState.BUILDING, "stage_started")
    other, _ = _mk_job(conn, cfg, company="Other Co", url="https://job-boards.greenhouse.io/other/jobs/4002")
    pool = wk.WorkerPool(cfg, cfg.db_path, n_threads=1)
    recovered = pool.recover(conn)
    assert recovered == [job.id]
    assert dbmod.job_by_id(conn, job.id).state == JobState.IDEAMAKER_DONE
    assert dbmod.events_for_job(conn, job.id)[-1]["event"] == "recovered"
    assert dbmod.job_by_id(conn, other.id).state == JobState.JD_READY  # untouched: not a running state
    # A job waiting in a ready state is requeued too: the queue is in memory, and before 2026-10-03 a restart
    # silently dropped every approved-but-waiting job.
    assert pool.queue_snapshot() == sorted([job.id, other.id])


def test_restart_requeues_ready_jobs_by_company_rank_skipping_shelved_and_stale(cfg, conn, fake_env):
    from pipeline.util import now_iso
    best, _ = _mk_job(conn, cfg, state=JobState.JD_READY, company="Best Co", url="https://x.com/a")
    worst, _ = _mk_job(conn, cfg, state=JobState.JD_READY, company="Worst Co", url="https://x.com/b")
    mid, _ = _mk_job(conn, cfg, state=JobState.IDEAMAKER_DONE, company="Mid Co", url="https://x.com/c")
    shelved, _ = _mk_job(conn, cfg, state=JobState.JD_READY, company="Best Co", url="https://x.com/d")
    stale, _ = _mk_job(conn, cfg, state=JobState.JD_READY, company="Best Co", url="https://x.com/e")
    dbmod.update_job(conn, shelved.id, flags=["shelved"])
    dbmod.update_job(conn, stale.id, active=False)
    for company, score in (("Best Co", 95), ("Mid Co", 60), ("Worst Co", 20)):
        conn.execute("INSERT INTO company_scores(company, score, scored_at) VALUES (?,?,?)", (company, score, now_iso()))
    pool = wk.WorkerPool(cfg, cfg.db_path, n_threads=1)
    pool.recover(conn)
    assert pool.queue_snapshot() == sorted([best.id, worst.id, mid.id])
    order = [pool.q.get_nowait()[2] for _ in range(3)]
    assert order == [best.id, mid.id, worst.id]


def test_aborted_runs_do_not_count_as_attempts(cfg, conn, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_script(cfg, [{"match": {"resume": "any"}, "reusable": True, "artifacts": str(GOOD), "result": {"subtype": "success", "cost": 0.2}}])))
    cfg.stages.ideamaker.max_attempts = 1
    job, folder = _mk_job(conn, cfg)
    for i in (1, 2):
        transition(conn, job.id, JobState.IDEAMAKER_RUNNING, "stage_started")
        rid = dbmod.start_stage_run(conn, job.id, "ideamaker", i, "log")
        dbmod.update_stage_run(conn, rid, status="aborted", error="process restart")
        transition(conn, job.id, JobState.JD_READY, "recovered")
    assert wk.run_one(cfg, job.id, stop_after=Stage.IDEAMAKER) == JobState.IDEAMAKER_DONE
    assert [r.status for r in dbmod.stage_runs_for_job(conn, job.id)] == ["aborted", "aborted", "succeeded"]


def test_resume_hint_used_on_next_attempt(cfg, conn, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_script(cfg, [{"match": {"resume": "*"}, "artifacts": str(GOOD), "result": {"subtype": "success", "cost": 0.2}}])))
    job, folder = _mk_job(conn, cfg)
    dbmod.set_setting(conn, f"resume_hint:{job.id}:ideamaker", "sess-9")
    assert wk.run_one(cfg, job.id, stop_after=Stage.IDEAMAKER) == JobState.IDEAMAKER_DONE
    calls = _calls(fake_env)
    assert calls[0]["resume"] == "sess-9" and "interrupted" in calls[0]["prompt"]
    assert dbmod.get_setting(conn, f"resume_hint:{job.id}:ideamaker") is None


# ----------------------------------------------------------------------------- per-stage model override

def test_per_stage_model_reaches_argv(cfg, conn, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_full_script(cfg)))
    _fake_repo(cfg)
    cfg.stages.builder.model = "claude-sonnet-5"
    job, folder = _mk_job(conn, cfg)
    assert wk.run_one(cfg, job.id) == JobState.DONE
    calls = _calls(fake_env)
    models = {c["env"]["PIPELINE_STAGE"]: c["argv"][c["argv"].index("--model") + 1] for c in calls}
    assert models["builder"] == "claude-sonnet-5"
    assert models["ideamaker"] == models["reconcile"] == models["critic"] == "claude-opus-5"


def test_stage_model_config_validation(tmp_project):
    from pipeline.config import ConfigError, load_config
    cfg2 = load_config(tmp_project / "config.json", project_root=tmp_project,
                       overrides={"stages": {"builder": {"model": "claude-sonnet-5"}}})
    assert cfg2.stages.builder.model == "claude-sonnet-5" and cfg2.stages.ideamaker.get("model") is None
    with pytest.raises(ConfigError):
        load_config(tmp_project / "config.json", project_root=tmp_project,
                    overrides={"stages": {"builder": {"model": ""}}})


def test_repo_config_builder_uses_sonnet():
    from pipeline.config import load_config
    root = Path(__file__).resolve().parents[1]
    real = load_config(root / "config.json" if (root / "config.json").exists() else root / "config.example.json", project_root=root)
    assert real.stages.builder.model == "claude-sonnet-5"
    assert real.stages.reconcile.model == "claude-sonnet-5"
    # RUBRIC 1.3.0: ideamaker and critic back on Opus after the blind quality test.
    assert real.stages.ideamaker.model == "claude-opus-5"
    assert real.stages.critic.model == "claude-opus-5"
    assert real.claude.model == "claude-opus-5"


def test_budget_cap_gets_one_continuation(cfg, conn, fake_env, monkeypatch):
    calls = [
        {"match": {"resume": None}, "artifacts": str(GOOD), "result": {"subtype": "error_max_budget_usd", "cost": 15.0, "turns": 60}, "exit_code": 1},
        {"match": {"resume": "*"}, "artifacts": str(GOOD), "result": {"subtype": "success", "cost": 16.0, "turns": 5}},  # running session total
    ]
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_script(cfg, calls)))
    job, folder = _mk_job(conn, cfg)
    assert wk.run_one(cfg, job.id, stop_after=Stage.IDEAMAKER) == JobState.IDEAMAKER_DONE
    run = dbmod.stage_runs_for_job(conn, job.id)[0]
    assert run.status == "succeeded" and run.fix_rounds == 1 and run.cost_usd == pytest.approx(16.0)
    calls_seen = _calls(fake_env)
    assert calls_seen[1]["resume"] == "fake-1" and "interrupted" in calls_seen[1]["prompt"].lower()


def test_budget_cap_continuation_is_bounded(cfg, conn, fake_env, monkeypatch):
    calls = [{"match": {"resume": "any"}, "reusable": True, "artifacts": str(GOOD),
              "result": {"subtype": "error_max_budget_usd", "cost": 2.0}, "exit_code": 1}]
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_script(cfg, calls)))
    cfg.stages.ideamaker.max_fix_rounds = 2
    job, folder = _mk_job(conn, cfg)
    assert wk.run_one(cfg, job.id, stop_after=Stage.IDEAMAKER) == JobState.FAILED
    assert len(_calls(fake_env)) == 3  # initial + 2 bounded continuations, then fail



def test_session_cost_does_not_double_count_resumed_sessions():
    from pipeline.stages.claude_runner import session_cost
    # A fix round resumes the same session and reports the session's running total (13.26 -> 14.15).
    runs = [{"session_id": "a", "cost_usd": 13.26}, {"session_id": "a", "cost_usd": 14.15},
            {"session_id": "b", "cost_usd": 2.0}, {"session_id": None, "cost_usd": 1.0}]
    assert session_cost(runs) == 17.15
