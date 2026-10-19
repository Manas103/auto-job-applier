"""Lever: the apply-page parser on the Palantir capture and the fixture, and the adapter on the fixture page
(native selects, radios, checkboxes, the location suggestion box, resume-first upload, EEO selects).
Enter is never pressed: the fixture submits the form on Enter and the counting server must see nothing."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from smartapply import intake
from smartapply.adapters.lever import LeverAdapter
from smartapply.answers import build_packet
from smartapply.browser.actions import Actions
from smartapply.browser.guard import Guard
from smartapply.prefetch import lever as lv
from smartapply.profile import Profile, seed_from_assets

CAPTURE = Path(__file__).resolve().parents[1] / "research" / "captures" / "lever_apply_us.html"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pages" / "lever.html"


def _profile() -> Profile:
    p = seed_from_assets(None)
    p.address.city, p.address.state, p.address.state_name, p.address.zip, p.address.street = "Springfield", "IL", "Illinois", "12345", "1 Main St"
    p.eeo.veteran = "I am not a protected veteran"
    p.eeo.disability = "I do not want to answer"
    return p


def test_parse_captured_palantir_page():
    if not CAPTURE.exists():
        pytest.skip("capture missing")
    qs = lv.parse_apply_html(CAPTURE.read_text(encoding="utf-8", errors="replace"))
    ids = [q.id for q in qs]
    assert ids[:6] == ["resume", "name", "email", "phone", "location", "org"]
    assert next(q for q in qs if q.id == "name").required and not next(q for q in qs if q.id == "phone").required
    auth = next(q for q in qs if "legally authorized" in q.label)
    assert auth.type == "select" and auth.extra["widget"] == "radio" and auth.options == ["Yes", "No"] and auth.required
    lang = next(q for q in qs if q.label.startswith("Language Skill"))
    assert lang.type == "multiselect" and lang.extra["widget"] == "checkbox" and "English (ENG)" in lang.options
    uni = next(q for q in qs if "university" in q.label.lower())
    assert uni.type == "select" and uni.extra["widget"] == "select" and len(uni.options) > 100
    vet = next(q for q in qs if q.id == "eeo[veteran]")
    assert vet.section == "eeoc" and any("not a protected veteran" in o for o in vet.options)
    assert "eeo[disabilitySignature]" in ids and "surveysResponses" not in " ".join(ids)


def test_parse_fixture_page():
    qs = lv.parse_apply_html(FIXTURE.read_text(encoding="utf-8"))
    by = {q.id: q for q in qs}
    assert by["cards[c1][field0]"].type == "select" and by["cards[c1][field1]"].extra["widget"] == "radio"
    assert by["cards[c1][field2]"].type == "multiselect" and by["cards[c1][field3]"].type == "textarea"
    assert by["location"].required and by["org"].required and by["urls[Twitter]"].required is False
    assert by["eeo[disability]"].options[2] == "I do not want to answer"


def test_adapter_fills_fixture_form(cfg, conn, pipeline, chrome, fixture_server):
    fixture_server.reset()
    p = _profile()
    with intake.open_pipeline_db(cfg) as pconn:
        job = intake.pipeline_job(pconn, 104)
    app = intake.enqueue_job(conn, cfg, job, profile_resume_name=p.resume_filename)
    qs = lv.parse_apply_html(FIXTURE.read_text(encoding="utf-8"))
    packet = build_packet(cfg, conn, app, p, qs, use_claude=False)
    a = packet.answers
    assert a["name"].value == "Alex Doe" and a["org"].value == "State University"
    assert a["cards[c1][field0]"].value == "Yes" and a["cards[c1][field1]"].value == "No"
    assert a["cards[c1][field2]"].value == ["Computer Science"] and a["cards[c1][field3]"].value == "3.70"
    assert "cards[c1][field4]" in packet.unanswered_required and a["urls[Twitter]"].source == "skip"
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page, tid = chrome.open_background_tab(b, fixture_server.url("lever.html"))
        page.wait_for_load_state("domcontentloaded")
        guard = Guard(page, "_fixture", ttl_ms=60000)
        guard.arm()
        act = Actions(page, ats="lever", guard=guard, upload_dir=app.workdir)
        adapter = LeverAdapter(cfg, conn, app, p, page, act)
        report = adapter.fill(qs, packet)
        by = {f.name: f for f in report.fields}
        assert by["resume"].status == "filled" and by["resume"].observed == p.resume_filename
        assert by["name"].observed == "Alex Doe"          # overwrote the parser's guess
        assert by["email"].observed == p.email and by["phone"].observed == "5550100142"
        assert by["location"].status == "filled" and by["location"].observed == "Springfield, Illinois, United States"
        assert page.locator("#selected-location").input_value().startswith("{")
        assert by["org"].observed == "State University" and by["urls[GitHub]"].observed == p.github
        assert by["cards[c1][field0]"].observed == "Yes" and by["cards[c1][field1]"].observed == "No"
        assert by["cards[c1][field2]"].observed == "Computer Science" and by["cards[c1][field3]"].observed == "3.70"
        assert by["cards[c1][field4]"].status == "left_for_human"
        assert by["eeo[gender]"].observed == "Decline to self-identify"
        assert by["eeo[veteran]"].observed == "I am not a protected veteran"
        assert by["eeo[disability]"].observed == "I do not want to answer"
        assert by["eeo[disabilitySignature]"].observed == "Alex Doe"
        assert "urls[Twitter]" not in by and page.locator('[name="urls[Twitter]"]').input_value() == ""   # moot: untouched
        assert report.status == "needs_human" and report.reason == "unanswered_required"
        assert fixture_server.hits("/apply") == 0, "the adapter must never submit"
        assert guard.disarm()
        page.close()
        b.close()
