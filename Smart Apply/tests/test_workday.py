"""Workday: prefetch normalization from captured questionnaires, vault password rules, mail link extraction and
polling with a fake IMAP, and the adapter driving the Workday-like fixture (account, wizard pages, Review stop)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smartapply import db, mail, vault
from smartapply.answers import AnswerPacket, build_packet
from smartapply.intake import ExperienceEntry, parse_experience_entries, parse_month
from smartapply.models import Answer, Application, AppState, Question
from smartapply.prefetch import workday as wd
from smartapply.profile import Profile

CAPTURES = Path(__file__).resolve().parents[1] / "research" / "captures" / "workday"


# ---------------------------------------------------------------- prefetch

def _raw(tenant: str) -> dict:
    job = json.loads((CAPTURES / f"job_{tenant}.json").read_text(encoding="utf-8"))
    qs = {}
    for key, name, _p in wd.QUESTIONNAIRES:
        f = CAPTURES / f"q_{tenant}_{key}.json"
        if f.exists():
            qs[name] = json.loads(f.read_text(encoding="utf-8"))
    approot = json.loads((CAPTURES / f"approot_{tenant}.json").read_text(encoding="utf-8"))
    acct = json.loads((CAPTURES / f"acct_{tenant}.json").read_text(encoding="utf-8"))
    return {"job": job, "questionnaires": qs, "approot": approot, "account": acct, "site": {"tenant": tenant}}


def test_normalize_stryker_questionnaires():
    qs = wd.normalize(_raw("stryker"))
    ids = [q.id for q in qs]
    assert all(i.startswith(("primaryQuestionnaire--", "secondaryQuestionnaire--")) for i in ids)
    assert len(ids) == len(set(ids)), "one Question per Workday question id"
    gpa = next(q for q in qs if q.label.startswith("What is your current cumulative GPA"))
    assert gpa.type == "textarea" and gpa.required
    auth = next(q for q in qs if q.label.startswith("Are you legally authorized"))
    assert auth.type == "select" and auth.options == ["Yes", "No"] and auth.extra["answer_ids"]["Yes"]
    assert all("<" not in q.label and "&amp;" not in q.label for q in qs)


def test_normalize_branching_follow_ups():
    """A follow-up nested under an answer (and not listed at top level) becomes a conditional question; one that is
    also listed at top level (HP wires the same question under several answers) stays plain and appears once."""
    t = {"id": "c4d9bd57996c10002583f04b9fc30042", "descriptor": "Multiple Choice - Single Select"}
    raw = {"questionnaires": {"primary": {"questions": [
        {"id": "p1", "body": "<p>Relatives at Acme?</p>", "order": "a", "required": True, "type": t, "possibleAnswers": [
            {"id": "y", "answerText": "Yes", "order": "a", "branchingQuestion": {"id": "c1", "body": "Their name?", "required": True,
                                                                                    "type": {"descriptor": "Text"}, "possibleAnswers": []}},
            {"id": "n", "answerText": "No", "order": "b", "branchingQuestion": {"id": "p2", "body": "dup", "type": t, "possibleAnswers": []}}]},
        {"id": "p2", "body": "Also top level", "order": "b", "required": False, "type": t, "possibleAnswers": [{"id": "a", "answerText": "A"}]},
    ]}}}
    qs = wd.normalize(raw)
    assert [q.id for q in qs] == ["primaryQuestionnaire--p1", "primaryQuestionnaire--p2", "primaryQuestionnaire--c1"]
    kid = qs[2]
    assert kid.type == "textarea" and kid.extra["branch_of"] == "primaryQuestionnaire--p1" and kid.extra["branch_when"] == "Yes" \
        and kid.extra["dom_optional"]
    assert "branch_of" not in qs[1].extra and qs[1].label == "Also top level"


def test_normalize_dedupes_branches_and_orders_pages():
    qs = wd.normalize(_raw("pg"))
    assert [q.extra["questionnaire"] for q in qs] == sorted([q.extra["questionnaire"] for q in qs],
                                                            key=["primary", "secondary", "supplementary"].index)
    multi = next(q for q in qs if "source(s) of information" in q.label)
    assert multi.type == "multiselect" and "LinkedIn" in multi.options


def test_flow_params():
    fp = wd.flow_params(_raw("draftkings"))
    assert fp["account_verification"] is True and fp["job_posting_id"] and fp["apply_url"].endswith("/apply/applyManually")
    assert fp["password_rules"] and fp["questionnaire_ids"] == {"primary": fp["questionnaire_ids"]["primary"]}
    fp2 = wd.flow_params(_raw("stryker"))
    assert fp2["account_verification"] is False and set(fp2["questionnaire_ids"]) == {"primary", "secondary"}


def test_prefetch_marks_gone_postings(monkeypatch):
    def fake(url, **kw):
        raise wd.HttpError(url, 404, "HTTP 404")
    monkeypatch.setattr(wd, "_http_get_json", fake)
    app = Application(id=1, job_id=1, company="X", title="t", url="https://acme.wd5.myworkdayjobs.com/en-US/ext/job/Austin-TX/SWE-Intern_R-1",
                      ats="workday", ats_id=None, ats_params={}, role_folder=Path("."), slug="x", workdir=Path("."), state=AppState.QUEUED)
    with pytest.raises(wd.PostingGone):
        wd.prefetch(app)


def test_deterministic_answers_on_workday_questions(cfg, conn, tmp_path):
    qs = wd.normalize(_raw("stryker"))
    profile = Profile()
    profile.attestations.non_compete_bound = "No"
    profile.attestations.relatives_in_government = "No"
    app = Application(id=1, job_id=1, company="Stryker", title="t", url="https://stryker.wd1.myworkdayjobs.com/x/job/a/b_R1", ats="workday",
                      ats_id=None, ats_params={}, role_folder=tmp_path, slug="x", workdir=tmp_path, state=AppState.PREFETCHED)
    packet = build_packet(cfg, conn, app, profile, qs, use_claude=False)

    class _ByLabel:
        def __getitem__(self, prefix):
            q = next(x for x in qs if x.label.startswith(prefix))
            return packet.answers.get(q.id)
    by_label = _ByLabel()
    assert by_label["Are you legally authorized to work in the"].value == "Yes"
    assert by_label["Will you now or in the future require spo"].value == "No"
    assert by_label["Do you currently hold a Visa (including a"].value == "No"          # permanent resident holds no visa
    assert by_label["Is your current cumulative GPA 3.0 or abo"].value == "Yes"
    assert by_label["What is your current cumulative GPA?"].value == "3.70"
    assert by_label["Are you currently in process of completin"].value == "Yes"
    assert by_label["Are you 18 years or older? If you are und"].value == "Yes"
    assert by_label["Are you bound by any confidentiality, non"].value == "No"
    assert by_label["Have you met with a Stryker representativ"].value == "No"
    # "Which college or university?" is listed at top level (Workday hides it until "met a representative" = Yes);
    # the answer is ready from the profile and the adapter skips it when the control is not rendered
    kid = next(q for q in qs if q.label.startswith("Which college"))
    assert packet.answers[kid.id].value == "State University"


def test_attestations_unset_are_left_to_manas(cfg, conn, tmp_path):
    qs = wd.normalize(_raw("stryker"))
    app = Application(id=1, job_id=1, company="Stryker", title="t", url="https://stryker.wd1.myworkdayjobs.com/x/job/a/b_R1", ats="workday",
                      ats_id=None, ats_params={}, role_folder=tmp_path, slug="x", workdir=tmp_path, state=AppState.PREFETCHED)
    packet = build_packet(cfg, conn, app, Profile(), qs, use_claude=False)
    nc = next(q for q in qs if q.label.startswith("Are you bound by any confidentiality"))
    assert packet.answers[nc.id].needs_human and "attestations" in packet.answers[nc.id].needs_human


# ---------------------------------------------------------------- vault

def test_password_generator_honours_rules():
    rules = json.loads((CAPTURES / "acct_stryker.json").read_text(encoding="utf-8"))["passwordRules"]
    for _ in range(20):
        pw = vault.generate_password(rules)
        assert not vault.violations(pw, rules) and len(pw) >= 16
    assert vault.violations("alllowercase", rules) == ["MUST_SPECIAL", "MUST_UPPERCASE", "MUST_NUMERIC"]
    assert "MAX_LENGTH" in vault.violations("A" * 30 + "a1!", [{"name": "MAX_LENGTH", "value": 20}])


def test_vault_memory_backend_roundtrip():
    vault.use_memory()
    assert vault.get_password("workday", "acme") is None
    vault.set_password("workday", "Acme", "s3cret!A1")
    assert vault.get_password("WORKDAY", "acme") == "s3cret!A1"
    vault.delete_password("workday", "acme")
    assert vault.get_password("workday", "acme") is None


# ---------------------------------------------------------------- mail

def test_extract_links_prefers_hrefs_and_dedupes():
    html = '<p>Verify: <a href="https://acme.wd5.myworkdayjobs.com/verify?token=abc&amp;x=1">here</a></p>'
    text = "Verify your account: https://acme.wd5.myworkdayjobs.com/verify?token=abc&x=1 ."
    links = mail.extract_links(text, html)
    assert links == ["https://acme.wd5.myworkdayjobs.com/verify?token=abc&x=1"]


def test_wait_for_polls_until_match(cfg):
    now = datetime.now(timezone.utc)
    calls = {"n": 0}

    def fake_fetch(cfg_, *, days, limit, want):
        calls["n"] += 1
        mails = [mail.RawMail(uid="1", imap_uid="1", from_addr="noreply@newsletter.example", subject="Weekly digest",
                              received_at=now, body="", links=[])]
        if calls["n"] >= 2:
            mails.append(mail.RawMail(uid="2", imap_uid="2", from_addr="acme@myworkday.com", subject="Verify your account",
                                      received_at=now + timedelta(seconds=5), body="click https://acme.wd5.myworkdayjobs.com/verify?t=1",
                                      links=["https://acme.wd5.myworkdayjobs.com/verify?t=1"]))
        return [m for m in mails if want(m.from_addr, m.subject)]
    m = mail.wait_for(cfg, sender=re.compile(r"myworkday", re.I), subject="verify", since=now - timedelta(minutes=1),
                      timeout_s=5, poll_s=0.05, fetch=fake_fetch)
    assert m is not None and m.uid == "2" and calls["n"] == 2
    assert mail.pick_link(m, "myworkday", re.compile(r"verif")) == "https://acme.wd5.myworkdayjobs.com/verify?t=1"
    none = mail.wait_for(cfg, sender="nobody", since=now, timeout_s=0.2, poll_s=0.05, fetch=lambda *a, **k: [])
    assert none is None


def test_mail_not_configured(cfg):
    with pytest.raises(mail.MailNotConfigured):
        mail.credentials(cfg)


# ---------------------------------------------------------------- experience entries

def test_parse_experience_entries(tmp_path):
    md = ("## Acme Labs (Feb. 2026 to Jun. 2026)\n\nForm fields: Acme Labs | Software Engineer Intern | Austin, TX | Feb. 2026 | Jun. 2026\n\n"
          "Short (under 600 characters):\n```\nI built things.\n```\n\n## Personal project\n\nForm fields: Personal project | Merge Gate | College Town, IL | Aug. 2026 | Aug. 2026\n\n"
          "## OSS (Feb. 2025 to Present)\n\nForm fields: PyTorch | Contributor | Remote | Feb. 2025 | Present\n")
    p = tmp_path / "experience_descriptions_x.md"
    p.write_text(md, encoding="utf-8")
    es = parse_experience_entries(p)
    assert [e.company for e in es] == ["Acme Labs", "Personal project", "PyTorch"]
    assert es[0].start == "2026-02" and es[0].end == "2026-06" and es[0].short == "I built things."
    assert es[1].is_project and es[2].end is None and parse_month("May 2025") == "2025-05"


# ---------------------------------------------------------------- adapter on the fixture (real Chrome)

@pytest.fixture
def wd_app(cfg, conn, pipeline, tmp_project, fixture_server):
    from smartapply import intake
    profile = Profile()
    profile.address.street, profile.address.city, profile.address.state, profile.address.state_name, profile.address.zip = \
        "100 Example Street", "Springfield", "IL", "Illinois", "12345"
    profile.attestations.non_compete_bound = "No"
    with intake.open_pipeline_db(cfg) as pconn:
        job = intake.pipeline_job(pconn, 102)
    app = intake.enqueue_job(conn, cfg, job, profile_resume_name=profile.resume_filename)
    (app.workdir / profile.resume_filename).write_bytes(b"%PDF-1.4 fake\n")
    role = intake.resolve_role_folder(app.role_folder)
    role.experience_md.write_text(
        "## Acme Labs\n\nForm fields: Acme Labs | Software Engineer Intern | Austin, TX | Feb. 2026 | Jun. 2026\n\nShort (under 600 characters):\n```\nI built search.\n```\n"
        "## OSS\n\nForm fields: PyTorch | Contributor | Remote | Feb. 2025 | Present\n", encoding="utf-8")
    questions = [
        Question(id="primaryQuestionnaire--q1", label="Are you legally authorized to work in the country in which the job is located?", type="select", required=True, options=["Yes", "No"], section="questions", extra={"questionnaire": "primary", "page": "applyFlowPrimaryQuestionsPage"}),
        Question(id="primaryQuestionnaire--q2", label="Will you now or in the future require sponsorship?", type="select", required=True, options=["Yes", "No"], section="questions", extra={"questionnaire": "primary"}),
        Question(id="primaryQuestionnaire--q3", label="What is your current cumulative GPA?", type="textarea", required=True, section="questions", extra={"questionnaire": "primary"}),
        Question(id="primaryQuestionnaire--q4", label="What is your projected graduation date?", type="date", required=True, section="questions", extra={"questionnaire": "primary"}),
        Question(id="primaryQuestionnaire--q5", label="Type of employment desired. Please select all that apply:", type="multiselect", required=False, options=["Summer / Internship", "Full-Time"], section="questions", extra={"questionnaire": "primary"}),
    ]
    app.ats_params = {**app.ats_params, "apply_url": fixture_server.url("workday.html"), "password_rules": [{"name": "MIN_LENGTH", "value": 8}],
                      "account_verification": False}
    db.update_application(conn, app.id, ats_params=app.ats_params, tenant="acme")
    packet = build_packet(cfg, conn, app, profile, questions, use_claude=False)
    return app, profile, questions, packet


def _run_fixture(cfg, conn, chrome, fixture_server, app, profile, questions, packet, *, ats="_fixture"):
    from playwright.sync_api import sync_playwright
    from smartapply.adapters.workday import WorkdayAdapter
    from smartapply.browser.actions import Actions
    from smartapply.browser.guard import Guard
    fixture_server.reset()
    with sync_playwright() as pw:
        browser = chrome.attach(pw)
        page, tid = chrome.open_background_tab(browser, app.ats_params["apply_url"])
        chrome.activate_tab(browser, tid)
        page.wait_for_load_state("domcontentloaded")
        guard = Guard(page, ats, ttl_ms=60000)
        guard.arm()
        adapter = WorkdayAdapter(cfg, conn, app, profile, page, Actions(page, ats=ats, guard=guard, upload_dir=app.workdir))
        adapter.act.review_reached = adapter.review_reached
        report = adapter.fill(questions, packet)
        log = page.locator("#log").inner_text()
        if report.status == "ready_for_review":
            # on Review the footer button reads Submit: the tool layer must refuse it, finalize never fires
            from smartapply.browser.actions import Refused
            with pytest.raises(Refused):
                adapter.act.click(page.locator("[data-automation-id=pageFooterNextButton]"))
        guard.disarm()
        page.close()
        browser.close()
    return report, log


@pytest.mark.usefixtures("pipeline")
def test_adapter_fills_fixture_to_review(cfg, conn, chrome, fixture_server, wd_app):
    vault.use_memory()
    app, profile, questions, packet = wd_app
    report, log = _run_fixture(cfg, conn, chrome, fixture_server, app, profile, questions, packet)
    by = {f.name: f for f in report.fields}
    assert report.status == "ready_for_review", (report.notes, [(f.name, f.status, f.note) for f in report.fields if f.status != "filled"])
    assert vault.get_password("workday", "acme"), "the created account's password is in the vault"
    assert by["legalName--firstName"].observed == "Alex" and by["countryRegion"].observed == "Illinois"
    assert by["source"].observed == "LinkedIn" and by["candidateIsPreviousWorker"].observed == "No"
    assert by["countryPhoneCode"].status == "filled" and by["phoneNumber"].observed == "5550100142"
    assert by["resume"].status == "filled"
    assert by["work_experience_1.startDate"].observed == "02/2026" and by["work_experience_1.endDate"].observed == "06/2026"
    assert by["work_experience_2.currentlyWorkHere"].intended is True and "work_experience_2.endDate" not in by
    assert by["education.schoolName"].observed == "State University" and by["education.degree"].observed == "Bachelor's Degree"
    assert by["education.fieldOfStudy"].observed == "Computer and Information Sciences" and "prompt-search:Computer Science" in log
    assert by["website_1.url"].observed.startswith("https://www.linkedin.com") and by["website_2.url"].observed.startswith("https://github.com")
    assert by["primaryQuestionnaire--q1"].observed == "Yes" and by["primaryQuestionnaire--q2"].observed == "No"
    assert by["primaryQuestionnaire--q3"].observed == "3.70" and by["primaryQuestionnaire--q4"].observed == "05/01/2028"
    assert by["primaryQuestionnaire--q5"].observed == "Summer / Internship"
    assert by["ethnicity"].observed.startswith("Not Specified") and by["gender"].observed == "Not Declared"
    assert by["veteranStatus"].observed == "I don't wish to answer" and by["acceptTermsAndAgreements"].status == "filled"
    assert report.pages_completed >= 4 and "finalize" not in log
    assert fixture_server.hits("/finalize") == 0 and fixture_server.hits("/jobapplication/app-1/name") == 1
    row = db.application_by_id(conn, app.id)
    assert row["application_id"] == "app-1" and row["last_step"] >= 4


@pytest.mark.usefixtures("pipeline")
def test_adapter_signs_in_with_vault_password_and_reuses_default_rows(cfg, conn, chrome, fixture_server, wd_app):
    """Second visit: the vault password signs in (the fixture remembers accounts per page load, so this run creates
    then signs in within one flow), and an undeletable default Education row is reused instead of duplicated."""
    vault.use_memory()
    app, profile, questions, packet = wd_app
    report, log = _run_fixture(cfg, conn, chrome, fixture_server, app, profile, questions, packet)
    assert report.status == "ready_for_review"
    edu = [f for f in report.fields if f.name.startswith("education.")]
    assert len([f for f in edu if f.name == "education.schoolName"]) == 1
