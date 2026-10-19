"""Tier-1 parsers (Simplify-style JSON, speedyapply README) and tier-2 ATS parsers."""

from __future__ import annotations

import json
from datetime import date

import pytest

from pipeline.sources import ashby, greenhouse, lever, workday
from pipeline.sources.base import html_to_text, normalize_category
from pipeline.sources.simplify import parse_simplify_listings
from pipeline.sources.speedyapply import parse_speedyapply_readme


def test_simplify_parser(fixtures_dir):
    raw = (fixtures_dir / "listings_simplify.json").read_bytes()
    items = parse_simplify_listings(raw, "simplify")
    ids = [i.source_id for i in items]
    assert "a3" not in ids and "a4" not in ids  # inactive / invisible skipped
    assert len(items) == 6
    by = {i.source_id: i for i in items}
    assert by["a1"].category == "Software" and by["a1"].ats == "greenhouse" and by["a1"].ats_id == "4001"
    assert by["a2"].ats == "lever" and by["a2"].sponsorship == "U.S. Citizenship is Required"
    assert by["a5"].ats == "workday" and by["a5"].ats_id == "wduser:R12345" and by["a5"].terms == ["Fall 2026"]
    assert by["a6"].category == "AI/ML/Data" and by["a6"].degrees == ["PhD"] and by["a6"].ats == "ashby"
    assert by["a7"].ats == "greenhouse" and by["a7"].ats_id == "5002"
    assert by["a8"].category == "Hardware" and by["a8"].date_posted == "2025-08-12"
    assert parse_simplify_listings(raw, "simplify", include_inactive=True)[2].active is False


def test_simplify_parser_rejects_non_array():
    with pytest.raises(ValueError):
        parse_simplify_listings(b'{"jobs": []}', "simplify")


def test_speedyapply_parser(fixtures_dir):
    raw = (fixtures_dir / "speedyapply_README.md").read_bytes()
    items = parse_speedyapply_readme(raw, "speedyapply", today=date(2026, 8, 22))
    assert [i.company for i in items] == ["BigCo", "BigCo", "QuantCo", "OtherCo"]
    assert items[0].pay == "$60/hr" and items[0].date_posted == "2026-08-22"
    assert items[1].title == "SWE Intern, Infrastructure" and items[1].date_posted == "2026-08-01"
    assert items[2].category == "Quant" and items[2].url == "https://quantco.com/careers?gh_jid=9100&src=sa"
    assert items[2].ats == "greenhouse" and items[2].ats_id == "9100" and items[2].date_posted == "2026-07-23"
    assert items[3].pay == "" and items[3].category == "Software" and items[3].locations == ["Denver, CO"]


def test_normalize_category_rules():
    assert normalize_category("Software Engineering", "x") == "Software"
    assert normalize_category("", "Quantitative Trading Intern") == "Quant"
    assert normalize_category("", "Machine Learning Intern") == "AI/ML/Data"
    assert normalize_category("", "FPGA Design Intern") == "Hardware"
    assert normalize_category("", "Product Management Intern") == "Product"
    assert normalize_category("", "Marketing Intern") == "Business"


def test_non_software_disciplines_are_not_labelled_software():
    """The Software rule's bare `engineer` alternative matched every engineering
    discipline, and with no discipline rule these sailed past the category gate in
    screen.py wearing a Software badge."""
    for title in ("Mechanical Engineering Intern (Summer 2027)",
                  "WDI Civil Engineering Intern, Springfield, Spring 2027",
                  "Intern - Process Quality Engineering",
                  "Process Engineering Co-op",
                  "Disneyland Facilities Mechanical Engineering Intern"):
        assert normalize_category("", title) == "Engineering (other)", title

    for title in ("Intern - Marketing Analytics",
                  "Integrated Logistics and Support Intern",
                  "Supply Chain Data & Analytics Intern"):
        assert normalize_category("", title) == "Business", title


def test_software_roles_do_not_regress():
    """Ordering is the fix, so `engineer` stays in the Software rule: an unqualified
    "Engineering Intern" must still reach Software rather than being screened out."""
    for title in ("Software Engineer Intern - C++ or Python",
                  "Engineering Intern",
                  "Software Developer Intern - Engineering",
                  "Robotics Engineer Intern"):
        assert normalize_category("", title) == "Software", title
    assert normalize_category("", "Data Science Intern - Engineering") == "AI/ML/Data"
    # Goldman's "Sales and Trading" must stay Quant despite the Business rule's reach.
    assert normalize_category(
        "", "Summer Analyst Intern - FICC and Equities - Sales and Trading") == "Quant"


def test_source_provided_category_still_wins():
    assert normalize_category("Hardware Engineering", "Mechanical Intern") == "Hardware"


def test_html_to_text(fixtures_dir):
    text = html_to_text((fixtures_dir / "html" / "generic_jd.html").read_text(encoding="utf-8"))
    assert "Responsibilities" in text and "Design and implement backend services" in text
    assert "dataLayer" not in text and "Home" not in text and "Copyright" not in text
    assert "\n\n\n" not in text


def _patch_http(monkeypatch, mapping: dict[str, bytes]):
    def fake_get(url, **kw):
        for k, v in mapping.items():
            if k in url:
                return v
        raise AssertionError(f"unexpected GET {url}")

    def fake_post(url, body, **kw):
        for k, v in mapping.items():
            if k in url:
                return v
        raise AssertionError(f"unexpected POST {url}")

    for mod in (greenhouse, lever, workday, ashby):
        monkeypatch.setattr(mod, "_http_get", fake_get, raising=False)
        monkeypatch.setattr(mod, "_http_post_json", fake_post, raising=False)


def test_greenhouse(monkeypatch, fixtures_dir):
    _patch_http(monkeypatch, {"/boards/acme/jobs/4001": (fixtures_dir / "greenhouse_job.json").read_bytes(),
                              "/boards/acme/jobs": (fixtures_dir / "greenhouse_jobs.json").read_bytes()})
    items = greenhouse.list_jobs("acme")
    assert len(items) == 2
    it = items[0]
    assert it.ats == "greenhouse" and it.ats_id == "4001" and it.locations == ["Chicago, IL"]
    assert "Responsibilities" in it.jd_text and "build & ship" in it.jd_text and "<" not in it.jd_text
    text = greenhouse.get_job_text("acme", "4001")
    assert "Graduating between December 2027 and June 2028" in text
    assert greenhouse.get_job_text(None, "4001") is None


def test_greenhouse_guess_board(monkeypatch, fixtures_dir):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if "/boards/acme-inc/jobs/4001" in url:
            return (fixtures_dir / "greenhouse_job.json").read_bytes()
        from pipeline.util import HttpError
        raise HttpError(url, 404, "HTTP 404")

    monkeypatch.setattr(greenhouse, "_http_get", fake_get)
    assert greenhouse.guess_board("Acme Inc.", "4001") == "acme-inc"
    assert greenhouse.guess_board("Nobody", "1") is None


def test_lever(monkeypatch, fixtures_dir):
    _patch_http(monkeypatch, {"api.lever.co/v0/postings/defenseco": (fixtures_dir / "lever_postings.json").read_bytes()})
    items = lever.list_postings("defenseco")
    assert len(items) == 2
    it = items[0]
    assert it.ats == "lever" and it.ats_id == "3f7c2e4a-1b2c-4d5e-8f90-123456789abc"
    assert "Responsibilities" in it.jd_text and "Build services" in it.jd_text and "Equal opportunity" in it.jd_text
    assert it.locations == ["Arlington, VA"]
    assert "Must be a U.S. citizen" in lever.get_posting_text("defenseco", "3f7c2e4a-1b2c-4d5e-8f90-123456789abc")
    assert lever.get_posting_text("defenseco", "nope") is None


def test_workday(monkeypatch, fixtures_dir):
    _patch_http(monkeypatch, {"/job/Austin-TX/Software-Engineer-Intern_R12345": (fixtures_dir / "workday_job.json").read_bytes(),
                              "/wday/cxs/wduser/Careers/jobs": (fixtures_dir / "workday_jobs.json").read_bytes()})
    items = workday.list_jobs("wduser.wd5.myworkdayjobs.com", "wduser", "Careers", search_text="intern", max_pages=1)
    assert len(items) == 2
    it = items[0]
    assert it.ats == "workday" and it.ats_id == "wduser:R12345"
    assert it.url == "https://wduser.wd5.myworkdayjobs.com/Careers/job/Austin-TX/Software-Engineer-Intern_R12345"
    assert it.locations == ["Austin, TX"]
    text = workday.get_job_text("wduser.wd5.myworkdayjobs.com", "wduser", "Careers", "/job/Austin-TX/Software-Engineer-Intern_R12345")
    assert "Build embedded software" in text and "ITAR" in text


def test_ashby(monkeypatch, fixtures_dir):
    _patch_http(monkeypatch, {"api.ashbyhq.com/posting-api/job-board/ashbylabs": (fixtures_dir / "ashby_jobs.json").read_bytes()})
    items = ashby.list_jobs("ashbylabs")
    assert len(items) == 1
    assert items[0].ats_id == "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d" and "PhD" in items[0].jd_text
    assert ashby.get_job_text("ashbylabs", "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d").startswith("We are looking")
    assert ashby.get_job_text("ashbylabs", "missing") is None
