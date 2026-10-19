"""Ashby: the GraphQL normalizer on the Northwood capture and the adapter on a fixture page (no <form>, typeless
Yes/No buttons, radio-group selects, an autocomplete location, a resume input next to an autofill input that must
never be touched). The fixture's submit posts to op=ApiSubmit...; the counting server must see nothing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from smartapply import intake
from smartapply.adapters.ashby import AshbyAdapter
from smartapply.answers import build_packet
from smartapply.browser.actions import Actions
from smartapply.browser.guard import Guard
from smartapply.models import Question
from smartapply.prefetch import ashby as ab
from smartapply.profile import Profile, seed_from_assets

CAPTURE = Path(__file__).resolve().parents[1] / "research" / "captures" / "ashby_gql_northwood.json"


def _profile() -> Profile:
    p = seed_from_assets(None)
    p.address.city, p.address.state, p.address.state_name, p.address.zip, p.address.street = "Springfield", "IL", "Illinois", "12345", "1 Main St"
    return p


def test_normalize_captured_graphql():
    if not CAPTURE.exists():
        pytest.skip("capture missing")
    qs = ab.normalize(json.loads(CAPTURE.read_text(encoding="utf-8")))
    by = {q.id: q for q in qs}
    assert by["_systemfield_name"].type == "text" and by["_systemfield_name"].required
    assert by["_systemfield_resume"].type == "file"
    onsite = next(q for q in qs if q.extra["kind"] == "Boolean")
    assert onsite.type == "select" and onsite.options == ["Yes", "No"]
    itar = next(q for q in qs if "export control" in q.label.lower())
    assert itar.type == "select" and any("permanent residence" in o for o in itar.options)
    assert by["_systemfield_eeoc_gender"].section == "eeoc" and "Decline to self-identify" in by["_systemfield_eeoc_gender"].options
    assert all(q.extra.get("hosted_url") is None for q in qs)   # added by prefetch(), not normalize()


def _fixture_questions() -> list[Question]:
    return [
        Question("_systemfield_name", "Legal Name (First Name Last Name)", "text", True, extra={"kind": "String"}),
        Question("_systemfield_email", "Email", "text", True, extra={"kind": "Email"}),
        Question("p1", "Phone Number", "text", True, extra={"kind": "String"}),
        Question("_systemfield_location", "Where do you plan on working from?", "text", True, extra={"kind": "Location"}),
        Question("_systemfield_resume", "Resume", "file", True, extra={"kind": "File"}),
        Question("li", "LinkedIn Profile", "text", False, extra={"kind": "String"}),
        Question("essay", "Share a project you're especially proud of", "textarea", True, extra={"kind": "LongText"}),
        Question("onsite", "This role requires working in person at our office five days per week. Does this work for you?", "select", True,
                 ["Yes", "No"], extra={"kind": "Boolean"}),
        Question("auth", "Are you authorized to work in the U.S.?", "select", True, ["Yes", "No"], extra={"kind": "ValueSelect"}),
        Question("itar", "To assist us in assessing export compliance (ITAR), please identify which statement best applies to you:", "select", True,
                 ["A United States citizen or national", 'A person lawfully admitted for permanent residence of the United States (i.e. "Green Card" holder)',
                  "None of the Above"], extra={"kind": "ValueSelect"}),
        Question("_systemfield_eeoc_gender", "Gender", "select", False, ["Male", "Female", "Decline to self-identify"], section="eeoc",
                 extra={"kind": "ValueSelect"}),
        Question("_texting_consent", "Consent to receive text messages about your application", "select", False, ["Yes", "No"],
                 extra={"kind": "TextingConsent", "dom_optional": True}),
    ]


def test_adapter_fills_fixture_form(cfg, conn, pipeline, chrome, fixture_server):
    fixture_server.reset()
    p = _profile()
    with intake.open_pipeline_db(cfg) as pconn:
        job = intake.pipeline_job(pconn, 105)
    app = intake.enqueue_job(conn, cfg, job, profile_resume_name=p.resume_filename)
    qs = _fixture_questions()
    packet = build_packet(cfg, conn, app, p, qs, use_claude=False)
    a = packet.answers
    assert a["p1"].value == "5550100142" and a["auth"].value == "Yes"
    assert a["itar"].value.startswith("A person lawfully admitted")   # permanent resident, from the profile
    assert a["onsite"].needs_human                                     # knock-out: on-site commitment is Alex's call
    assert "essay" in packet.unanswered_required
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page, tid = chrome.open_background_tab(b, fixture_server.url("ashby.html"))
        page.wait_for_load_state("domcontentloaded")
        guard = Guard(page, "_fixture", ttl_ms=60000)
        guard.arm()
        act = Actions(page, ats="ashby", guard=guard, upload_dir=app.workdir)
        adapter = AshbyAdapter(cfg, conn, app, p, page, act)
        report = adapter.fill(qs, packet)
        by = {f.name: f for f in report.fields}
        assert by["_systemfield_name"].observed == "Alex Doe" and by["_systemfield_email"].observed == p.email
        assert by["p1"].observed == "5550100142"
        assert by["_systemfield_location"].status == "filled" and by["_systemfield_location"].observed == "Springfield, Illinois, United States"
        assert by["_systemfield_resume"].status == "filled" and by["_systemfield_resume"].observed == p.resume_filename
        assert page.locator("#autofill-input").evaluate("e => e.files.length") == 0   # the autofill pane was never touched
        assert by["li"].observed == p.linkedin
        assert by["essay"].status == "left_for_human" and by["onsite"].status == "left_for_human"
        assert by["auth"].observed == "Yes" and page.locator("#auth-r0").is_checked()
        assert by["itar"].observed.startswith("A person lawfully admitted") and page.locator("#itar-r1").is_checked()
        assert by["_systemfield_eeoc_gender"].observed == "Decline to self-identify"
        assert by["_texting_consent"].observed == "Yes"     # found by label, answered from profile defaults
        # a refill over a pressed Yes keeps it (the buttons toggle; Voleon, 2026-10-10)
        tc = adapter._container(next(q for q in qs if q.id == "_texting_consent"))
        assert adapter._yesno(tc, "Yes") == "Yes" and tc.locator("button[data-option=yes]").get_attribute("aria-pressed") == "true"
        assert report.status == "needs_human" and report.reason == "unanswered_required"
        assert fixture_server.hits("ApiSubmit") == 0, "the adapter must never submit"
        assert guard.disarm()
        page.close()
        b.close()


def test_server_copy_check_against_harvey_shapes():
    """Harvey, 2026-10-05: Ashby's server copy (op=ApiSetFormValue responses) is what Submit validates."""
    from smartapply.adapters.ashby import _mdy, server_matches, server_values
    from smartapply.models import Question as Q

    def entry(path, value=None, filename=None):
        fv = None if value is None and filename is None else ({"value": value} if filename is None else {"filename": filename})
        return {"__typename": "FormFieldEntry", "field": {"path": path}, "fieldValue": fv}
    fr = {"sections": [{"fieldEntries": [entry("uni"), entry("grad", "2002-05-01"), entry("reloc", ["New York, NY"]),
                                         entry("auth", True), entry("ai", "PyTorch, Triton"),
                                         entry("_systemfield_resume", filename="Alex_Doe_Resume.pdf")]}]}
    st = server_values(fr)
    assert st["uni"] is None and st["_systemfield_resume"] == "Alex_Doe_Resume.pdf"
    assert not server_matches(Q("uni", "University", "text", True), "State University", st["uni"])
    date_q = Q("grad", "Graduation Date", "date", True, extra={"kind": "Date"})
    assert not server_matches(date_q, "2028-05", st["grad"]) and server_matches(date_q, "2028-05", "2028-05-01")
    reloc_q = Q("reloc", "Relocate to", "multiselect", True, ["New York, NY", "San Francisco, CA"])
    assert not server_matches(reloc_q, ["New York, NY", "San Francisco, CA"], st["reloc"])
    assert server_matches(Q("auth", "Authorized?", "select", True, extra={"kind": "Boolean"}), "Yes", st["auth"])
    assert server_matches(Q("c", "Cohort", "select", True), "May 25, 2027 � August", "May 25, 2027 – August")
    assert _mdy("2028-05") == "05/01/2028"


def test_value_select_stores_option_ids():
    """Voleon, 2026-10-10: a ValueSelect's server copy holds the picked option's id; it was reported as wrong."""
    from smartapply.adapters.ashby import server_matches, server_values
    from smartapply.models import Question as Q
    opts = [{"label": "Yes", "value": "097135b6"}, {"label": "No", "value": "4b5c6890"}]
    fr = {"fieldEntries": [{"__typename": "FormFieldEntry", "field": {"path": "auth", "type": "ValueSelect",
                                                                      "selectableValues": opts},
                            "fieldValue": {"value": "097135b6"}}]}
    st = server_values(fr)
    q = Q("auth", "Are you legally authorized to work in the US?", "select", True, ["Yes", "No"])
    assert st["auth"] == "Yes" and server_matches(q, "Yes", st["auth"]) and not server_matches(q, "No", st["auth"])
