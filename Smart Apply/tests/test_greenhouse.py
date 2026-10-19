"""Greenhouse: prefetch normalizer on the captured JSON, deterministic answers, and the adapter on a fixture
form that imitates react-select (including async School/Location lists, a conditional Race question, a custom
file question and a required consent checkbox). No Claude call: everything here is answered from the profile."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from smartapply import db, intake
from smartapply.adapters.greenhouse import GreenhouseAdapter
from smartapply.answers import AnswerPacket, build_packet, deterministic_answers, match_option
from smartapply.browser.actions import Actions
from smartapply.browser.guard import Guard
from smartapply.models import Application, Question
from smartapply.prefetch import greenhouse as gh
from smartapply.profile import DECLINE, Profile, seed_from_assets

CAPTURE = Path(__file__).resolve().parents[1] / "research" / "captures" / "gh_job.json"


def test_normalize_captured_json():
    if not CAPTURE.exists():
        pytest.skip("capture missing")
    raw = json.loads(CAPTURE.read_text(encoding="utf-8"))
    qs = gh.normalize(raw)
    ids = [q.id for q in qs]
    assert ids[:5] == ["first_name", "last_name", "preferred_name", "email", "phone"]
    assert "resume" in ids and any(i.startswith("question_") for i in ids)
    auth = next(q for q in qs if "authorized" in q.label.lower())
    assert auth.type == "select" and auth.options == ["Yes", "No"] and auth.required
    assert "veteran_status" in ids and next(q for q in qs if q.id == "veteran_status").section == "eeoc"
    assert "latitude" not in ids  # hidden location fields are not questions
    if raw.get("education"):
        edu = [q for q in qs if q.section == "education"]
        assert edu and all(q.extra.get("dom_optional") for q in edu)   # which controls exist varies per board
    if "race" in ids:
        assert next(q for q in qs if q.id == "race").extra.get("dom_optional")
        assert "hispanic_ethnicity" in ids       # shown on the form although the API omits it
    if raw.get("demographic_questions"):
        assert any(q.section == "demographic" for q in qs)


def test_board_discovery_from_html(monkeypatch):
    html = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=flyzipline"></script>'
    monkeypatch.setattr(gh, "_http_get", lambda url, **kw: html.encode() if "zipline.com" in url else b'{"id": 7929354003, "questions": []}')
    assert gh.discover_board("https://www.zipline.com/open-roles?gh_jid=7929354003", "7929354003", company="Zipline") == "flyzipline"
    # guesses from the host name when the HTML says nothing
    calls = []

    def fake(url, **kw):
        calls.append(url)
        if "epicgames.com" in url:
            return b"<html>nothing here</html>"
        if "/boards/epicgames/jobs/6173862004" in url:
            return b'{"id": 6173862004, "questions": [{}]}'
        raise gh.HttpError(url, 404, "HTTP 404")
    monkeypatch.setattr(gh, "_http_get", fake)
    monkeypatch.setattr(gh, "_board_from_browser", lambda url, **kw: None)
    assert gh.discover_board("https://epicgames.com/careers/jobs/6173862004?gh_jid=6173862004", "6173862004", company="Epic Games") == "epicgames"


def test_match_option():
    assert match_option(["Yes", "No"], True) == "Yes"
    assert match_option(["Yes, I am authorized", "No"], "yes") == "Yes, I am authorized"
    assert match_option(["Decline To Self Identify", "Male"], DECLINE) == "Decline To Self Identify"
    assert match_option(["I don't wish to answer", "I am not a protected veteran"], DECLINE) == "I don't wish to answer"
    assert match_option(["I don't wish to answer", "I am not a protected veteran"], "I am not a protected veteran") == "I am not a protected veteran"
    assert match_option(["Bachelor's Degree", "Master's"], "Bachelor of Science") is None
    assert match_option(["United States +1", "Canada +1"], "US") is None
    assert match_option(["Springfield, Illinois, United States", "Springfield, Oklahoma, United States"], "Springfield, Illinois") == "Springfield, Illinois, United States"


def _profile() -> Profile:
    p = seed_from_assets(None)
    p.address.city, p.address.state, p.address.state_name, p.address.zip, p.address.street = "Springfield", "IL", "Illinois", "12345", "1 Main St"
    p.eeo.veteran = "I am not a protected veteran"
    return p


def test_deterministic_answers_cover_standard_and_knockouts():
    p = _profile()
    qs = [Question("first_name", "First Name", "text", True), Question("email", "Email", "text", True),
          Question("phone", "Phone", "text", True), Question("resume", "Resume/CV", "file", True),
          Question("candidate-location", "Location (City)", "text", True, section="location"),
          Question("question_2", "Are you legally authorized to work in the United States?", "select", True, ["Yes", "No"]),
          Question("question_3", "Will you now or in the future require sponsorship?", "select", True, ["Yes", "No"]),
          Question("question_4", "LinkedIn Profile", "text", False),
          Question("question_5", "Why do you want to work here?", "textarea", True),
          Question("question_6", "What are your preferred pronouns?", "text", False),
          Question("gender", "Gender", "select", False, ["Male", "Female", "Decline To Self Identify"], section="eeoc"),
          Question("veteran_status", "Veteran Status", "select", False, ["I don't wish to answer", "I am not a protected veteran"], section="eeoc"),
          Question("degree--0", "Degree", "select", True, ["Bachelor's Degree", "Master's Degree"], section="education"),
          Question("end-year--0", "End date year", "number", True, section="education")]
    a = deterministic_answers(qs, p, {})
    assert a["first_name"].value == "Alex" and a["phone"].value == "5550100142"
    assert a["candidate-location"].value == "Springfield, Illinois"
    assert a["question_2"].value == "Yes" and a["question_3"].value == "No"
    assert a["question_4"].value.startswith("https://www.linkedin.com/")
    assert "question_5" not in a                      # open-ended: not deterministic
    assert a["question_6"].source == "skip"           # optional pronouns: skipped, not sent to Claude
    assert a["gender"].value == "Decline To Self Identify"
    assert a["veteran_status"].value == "I am not a protected veteran"
    assert a["degree--0"].value == "Bachelor's Degree"   # the dropdown label, not the resume wording
    assert a["end-year--0"].value == "2028"
    qs2 = [Question("degree--0", "Degree", "select", True, ["Bachelor of Arts", "Master's Degree"], section="education")]
    assert deterministic_answers(qs2, p, {})["degree--0"].needs_human   # no exact option: never guess
    p2 = seed_from_assets(None)   # no address
    loc = deterministic_answers([Question("candidate-location", "Location (City)", "text", True)], p2, {})["candidate-location"]
    assert loc.needs_human


def test_adapter_fills_fixture_form(cfg, conn, pipeline, chrome, fixture_server):
    fixture_server.reset()
    p = _profile()
    with intake.open_pipeline_db(cfg) as pconn:
        job = intake.pipeline_job(pconn, 101)
    app = intake.enqueue_job(conn, cfg, job, profile_resume_name=p.resume_filename)
    edu = {q.id: q for q in gh.EDUCATION_QUESTIONS}
    qs = [Question("first_name", "First Name", "text", True), Question("last_name", "Last Name", "text", True),
          Question("preferred_name", "Preferred First Name", "text", False), Question("email", "Email", "text", True),
          Question("country", "Country", "select", True, ["United States +1", "Canada +1", "Canada +91"]),
          Question("phone", "Phone", "text", True),
          Question("candidate-location", "Location (City)", "text", True, section="location", extra={"async": True}),
          Question("resume", "Resume/CV", "file", True),
          edu["school--0"], edu["degree--0"], edu["discipline--0"], edu["start-month--0"], edu["end-year--0"],
          Question("question_1", "Why do you want to work at Acme?", "textarea", True),
          Question("question_2", "Are you legally authorized to work in the United States?", "select", True, ["Yes", "No"]),
          Question("question_3", "Will you now or in the future require sponsorship?", "select", True, ["Yes", "No"]),
          Question("question_4", "LinkedIn Profile", "text", False),
          Question("question_5", "What is your GPA?", "select", True, ['Below 2', '2 - 2.49', '2.5 - 2.99', '3 - 3.49', '3.5 - 3.74', '3.75 - 4']),
          Question("question_6", "When do you expect to graduate?", "select", True, ['December 2026', 'May 2027', 'December 2027', 'May 2028', 'August 2028']),
          Question("question_9", "Transcript", "file", False),
          Question("question_20[]", "Undergrad Discipline(s)", "multiselect", True, ["Mathematics", "Computer Science", "Statistics"]),
          Question("gender", "Gender", "select", False, ["Male", "Female", "Decline To Self Identify"], section="eeoc"),
          Question("race", "Race", "select", False, ["White", "Asian", "Decline To Self Identify"], section="eeoc", extra={"dom_optional": True}),
          Question("veteran_status", "Veteran Status", "select", False,
                   ["I don't wish to answer", "I identify as one or more of the classifications of a protected veteran", "I am not a protected veteran"], section="eeoc")]
    p.school.degree = "Bachelor's Degree"
    transcript = cfg.data_dir / "Transcript.pdf"
    transcript.write_bytes(b"%PDF-1.4 fixture")
    p.transcript_path = str(transcript)
    packet = build_packet(cfg, conn, app, p, qs, use_claude=False)
    assert "question_1" in packet.unanswered_required   # no Claude in this test
    assert packet.answers["question_5"].value == "3.5 - 3.74" and packet.answers["question_6"].value == "May 2028"
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page, tid = chrome.open_background_tab(b, fixture_server.url("greenhouse.html"))
        page.wait_for_load_state("domcontentloaded")
        guard = Guard(page, "_fixture", ttl_ms=60000)
        guard.arm()
        act = Actions(page, ats="greenhouse", guard=guard, upload_dir=app.workdir)
        adapter = GreenhouseAdapter(cfg, conn, app, p, page, act)
        report = adapter.fill(qs, packet)
        by = {f.name: f for f in report.fields}
        assert by["first_name"].status == "filled" and by["email"].observed == p.email
        assert by["country"].observed == "United States +1"
        assert by["candidate-location"].status == "filled" and by["candidate-location"].observed == "Springfield, Illinois, United States"
        assert by["school--0"].status == "filled" and by["school--0"].observed == "State University"   # async list
        assert by["degree--0"].observed == "Bachelor's Degree" and by["end-year--0"].observed == "2028"
        assert by["discipline--0"].status == "skipped" and by["start-month--0"].status == "skipped"   # not on this board
        assert by["question_2"].observed == "Yes" and by["question_3"].observed == "No"
        assert by["question_5"].observed == "3.5 - 3.74" and by["question_6"].observed == "May 2028"
        assert by["question_9"].status == "filled" and by["question_9"].observed == "Transcript.pdf"   # profile transcript
        assert by["gender"].observed == "Decline To Self Identify"
        assert by["hispanic_ethnicity"].observed == "Decline To Self Identify"   # not in the API list: DOM sweep
        assert by["race"].status == "skipped"                                     # conditional, never shown
        assert by["veteran_status"].observed == "I am not a protected veteran"
        assert by["resume"].status == "filled" and by["resume"].observed == p.resume_filename
        assert by["question_1"].status == "left_for_human"
        assert by["privacy_consent"].status == "left_for_human"                   # required checkbox reported
        # a ticked checkbox group answers its question; its other options are not reported as required boxes (DV Trading)
        assert by["question_20[]"].observed == "Computer Science"
        assert not [f for f in report.fields if f.name.startswith("question_20[]_")]
        assert report.status == "needs_human" and report.reason == "unanswered_required"
        assert fixture_server.hits("/acme/jobs") == 0, "the adapter must never submit"
        assert guard.disarm()
        page.close()
        b.close()
