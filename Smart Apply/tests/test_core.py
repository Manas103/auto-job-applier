"""Config, DB, tracker, intake, profile: the parts that run without a browser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smartapply import db, intake, tracker
from smartapply.intake import detect_ats, resolve_role_folder
from smartapply.models import Application, AppState
from smartapply.profile import Profile, load_profile, save_profile, seed_from_assets
from smartapply.util import scan_em_dashes


def test_detect_ats_kinds():
    assert detect_ats("https://job-boards.greenhouse.io/advancedspace/jobs/4324855009")[0] == "greenhouse"
    k, p = detect_ats("https://albemarle.wd5.myworkdayjobs.com/en-US/ketjenexternal/job/Houston/Ketjen-Data_REQ-31411")
    assert k == "workday" and p["tenant"] == "albemarle" and p["req_id"] == "REQ-31411" and p["site"] == "ketjenexternal"
    k, p = detect_ats("https://wd3.myworkdaysite.com/en-US/recruiting/brevanhoward/BH_ExternalCareers/job/New-York/Quant-Intern_JR104")
    assert k == "workday" and p["tenant"] == "brevanhoward" and p["site"] == "BH_ExternalCareers"
    assert p["external_path"] == "/job/New-York/Quant-Intern_JR104" and p["req_id"] == "JR104"
    assert detect_ats("https://jobs.lever.co/palantir/75cc1c09-8ebd-44c8-b3bc-d122cd1fecb3")[0] == "lever"
    assert detect_ats("https://jobs.ashbyhq.com/ibotta/3130669e-16aa-4f63-834d-b83571c8d269/application?embed=true")[0] == "ashby"
    assert detect_ats("https://eofe.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/81239")[0] == "oracle_hcm"
    assert detect_ats("https://jobs.smartrecruiters.com/Bosch/744000000")[0] == "smartrecruiters"
    assert detect_ats("https://careersus-shure.icims.com/jobs/1/x/job")[0] == "icims"
    assert detect_ats("https://johndeere.eightfold.ai/careers/job/1")[0] == "eightfold"
    assert detect_ats("https://ats.rippling.com/x/jobs/1")[0] == "rippling"
    assert detect_ats("https://www.linkedin.com/jobs/view/1")[0] == "linkedin"
    assert detect_ats("https://higher.gs.com/roles/170675")[0] is None


def test_db_and_tracker(cfg, conn):
    app_id = db.insert_application(conn, job_id=1, company="A", title="T", url="https://x", ats="greenhouse", ats_id="1",
                                   ats_params={"board": "a"}, role_folder="r", slug="a", workdir="w")
    app = tracker.load(conn, app_id)
    assert app.state == AppState.QUEUED and app.ats_params == {"board": "a"}
    tracker.transition(conn, app_id, AppState.PREFETCHED, "prefetched")
    tracker.transition(conn, app_id, AppState.DRAFTED, "drafted")
    tracker.transition(conn, app_id, AppState.FILLING, "filling")
    with pytest.raises(tracker.IllegalTransition):
        tracker.transition(conn, app_id, AppState.APPLIED, "applied")
    app = tracker.transition(conn, app_id, AppState.READY_FOR_REVIEW, "ready")
    assert app.ready_at
    app = tracker.transition(conn, app_id, AppState.APPLIED, "applied")
    assert app.applied_at
    ev = db.events_for_app(conn, app_id)
    assert [e["event"] for e in ev] == ["prefetched", "drafted", "filling", "ready", "applied"]
    assert all("+" in e["ts_local"] or "-" in e["ts_local"][10:] for e in ev)
    db.record_cost(conn, app_id, purpose="answers", model="m", cost_usd=0.5, num_turns=3, session_id="s", subtype="success", duration_s=1)
    assert db.cost_totals(conn)["total_usd"] == 0.5


def test_intake_candidates_and_enqueue(cfg, conn, pipeline):
    with intake.open_pipeline_db(cfg) as p:
        rows = intake.candidate_jobs(p)
        assert [r["id"] for r in rows] == [101, 102, 104, 105]      # 103 already applied
        assert intake.pipeline_csrf_token(p) == "testtoken"
        job = intake.pipeline_job(p, 101)
    app = intake.enqueue_job(conn, cfg, job, profile_resume_name="Alex_Doe_Resume.pdf")
    assert app.ats == "greenhouse" and app.slug == "acme" and app.state == AppState.QUEUED
    assert (app.workdir / "Alex_Doe_Resume.pdf").exists()
    assert (app.workdir / "job.json").exists()
    again = intake.enqueue_job(conn, cfg, job, profile_resume_name="Alex_Doe_Resume.pdf")
    assert again.id == app.id
    role = resolve_role_folder(app.role_folder)
    assert role.experience_md and role.realistic_pdf and not role.problems


def test_pipeline_db_is_read_only(cfg, pipeline):
    import sqlite3
    with intake.open_pipeline_db(cfg) as p:
        with pytest.raises(sqlite3.OperationalError):
            p.execute("UPDATE jobs SET flags='[\"applied\"]'")


def test_profile_seed_and_roundtrip(tmp_path: Path, pipeline):
    prof = seed_from_assets(pipeline.parent.parent / "playbooks" / "ASSETS.md")
    assert prof.first_name == "Alex" and prof.last_name == "Doe" and prof.school.gpa == "3.70"
    assert prof.phone_digits == "5550100142" and prof.resume_filename == "Alex_Doe_Resume.pdf"
    assert "address" in prof.missing()
    save_profile(prof, tmp_path / "p.yaml")
    back = load_profile(tmp_path / "p.yaml")
    assert back == prof
    assert back.work_auth.sponsorship_required is False and back.work_auth.citizen is False


def test_em_dash_scan():
    assert scan_em_dashes("fine, really\n---\n| a | b |\n|---|---|\n```\nx --- y\n```\n") == []
    assert scan_em_dashes("bad — here")[0][0] == 1
    assert scan_em_dashes("also --- bad")[0][1] == "'---' in prose"


def test_new_accounts_use_the_default_password_when_the_rules_allow():
    from smartapply import vault
    vault.use_memory()
    assert vault.new_account_password(None) != vault.new_account_password(None)      # generated while no default
    vault.set_default_password("Example1@pw")
    assert vault.new_account_password(None) == "Example1@pw"
    strict = [{"name": "MIN_LENGTH", "value": 16}]
    pw = vault.new_account_password(strict)                                           # default too short: generated
    assert pw != "Example1@pw" and len(pw) >= 16


def test_recheck_upload_reuploads_a_dropped_file_or_fails_it():
    """Harvey (Ashby), 2026-10-05: the resume was read back during the fill, then the form dropped it."""
    from smartapply.adapters.base import Adapter
    from smartapply.models import FieldOutcome, FillReport

    class _Page:
        def wait_for_timeout(self, ms):
            pass

    def adapter():
        a = object.__new__(Adapter)
        a.page, a.report = _Page(), FillReport(status="filling")
        a.report.fields.append(FieldOutcome("application", "resume", "Resume", "r.pdf", "r.pdf", "filled"))
        return a

    shown = iter([False, True])                       # dropped at the end, shown after the re-upload
    a = adapter()
    a.recheck_upload("resume", lambda: next(shown), lambda: "r.pdf")
    assert a.report.fields[0].status == "filled" and "re-uploaded" in a.report.fields[0].note
    a = adapter()                                     # never comes back: failed, so the fill is not "ready"
    a.recheck_upload("resume", lambda: False, lambda: "r.pdf")
    assert a.report.fields[0].status == "failed"
    a = adapter()                                     # still shown: untouched
    a.recheck_upload("resume", lambda: True, lambda: (_ for _ in ()).throw(AssertionError("no re-upload")))
    assert a.report.fields[0].note is None


def test_refill_does_not_reload_a_redirected_greenhouse_form_and_eu_uses_the_global_api():
    from smartapply.filler import _same_form
    from smartapply.prefetch.greenhouse import api_url
    assert _same_form("https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=8128745",
                      "https://boards.greenhouse.io/embed/job_app?for=stripe&token=8128745")
    assert not _same_form("https://stripe.com/jobs/search?gh_jid=8128745",
                          "https://boards.greenhouse.io/embed/job_app?for=stripe&token=8128745")
    assert api_url("imc", "4823924101", eu=True).startswith("https://boards-api.greenhouse.io/v1/boards/imc/jobs/4823924101")


def test_answer_memory_reads_the_list_packet(tmp_path):
    import json
    from smartapply import db as sdb, memory
    from smartapply.models import Application
    conn = sdb.connect(tmp_path / "t.sqlite3")
    wd = tmp_path / "app"
    wd.mkdir()
    label = "Why are you interested in working on payments infrastructure at this company?"
    (wd / "questions.json").write_text(json.dumps([{"id": "q1", "label": label, "type": "textarea", "required": True}]))
    (wd / "answers.json").write_text(json.dumps({"answers": [{"question_id": "q1", "value": "Because of the scale.",
                                                              "source": "claude", "needs_human": None}]}))
    from smartapply.models import AppState
    app = Application(id=1, job_id=1, company="Acme", title="Intern", url="u", ats="greenhouse", ats_id=None,
                      ats_params={}, role_folder=wd, slug="acme", workdir=wd, state=AppState.APPLIED)
    assert memory.remember_from_application(conn, app) == 1


def test_tab_label_by_fill_outcome():
    from types import SimpleNamespace
    from smartapply.filler import tab_label
    from smartapply.models import FieldOutcome, FillReport
    app = SimpleNamespace(company="Verkada", title="Mobile Software Engineer Intern - Summer 2027")
    assert tab_label(app, FillReport(status="ready_for_review")) == "[SUBMIT] Verkada Mobile Software Engineer Intern"
    left = FillReport(status="needs_human", reason="unanswered_required", fields=[
        FieldOutcome(page="1", name="q1", label="Which office?", intended=None, observed=None, status="left_for_human"),
        FieldOutcome(page="2", name="page", label="page", intended=None, observed=None, status="left_for_human")])
    assert tab_label(app, left) == "[YOU] Verkada Mobile Software Engineer Intern: Which office?"
    assert tab_label(app, FillReport(status="needs_human", reason="captcha")).endswith(": solve the CAPTCHA")


def test_self_apply_marks_skips_and_writes_a_packet(cfg, conn, pipeline):
    # Alex, 2026-10-16: JPMorgan/Tesla-style jobs go to a do-it-yourself list with everything he needs on hand
    from smartapply import selfapply
    from smartapply.profile import Profile
    app, folder = selfapply.mark_self_apply(cfg, conn, Profile(email="m@x.com"), 101, "captcha every time")
    assert app.state == AppState.SELF_APPLY and app.reason == "captcha every time"
    text = (folder / "APPLY.md").read_text(encoding="utf-8")
    assert app.url in text and "captcha every time" in text and "smartapply applied 101" in text
    assert (folder / "Alex_Doe_Resume.pdf").exists()
    assert [a.job_id for a in selfapply.self_apply_list(conn)] == [101]
    tracker.transition(conn, app.id, AppState.APPLIED, "applied")      # he submits it himself later
