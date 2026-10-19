"""Eligibility screen for a U.S. permanent resident targeting Summer 2027."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pipeline.screen import parse_grad_window, screen_jd, screen_metadata


def J(**kw):
    base = dict(sponsorship="", terms=["Summer 2027"], title="Software Engineer Intern", category="Software",
                degrees=["Bachelor's"], locations=["Chicago, IL"])
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("sp,expect", [
    ("U.S. Citizenship is Required", "screen_out"), ("u.s. citizenship required", "screen_out"),
    ("Citizenship Required", "screen_out"), ("Offers Sponsorship", "pass"), ("Does Not Offer Sponsorship", "pass"),
    ("Other", "pass"), ("", "pass"),
])
def test_sponsorship(cfg, sp, expect):
    r = screen_metadata(J(sponsorship=sp), cfg)
    assert r.verdict == expect
    if expect == "screen_out":
        assert r.reason == "citizenship_required_metadata"


@pytest.mark.parametrize("terms,verdict,flag", [
    (["Summer 2027"], "pass", None), (["Summer"], "pass", "term_unknown"), ([], "pass", "term_unknown"),
    (["N/A"], "pass", "term_unknown"), (["Fall 2026"], "screen_out", None), (["Summer 2026", "Fall 2026"], "screen_out", None),
    (["Summer 2027", "Fall 2027"], "pass", None), (["Spring 2027"], "pass", None), (["Fall 2027"], "pass", None),
    (["Winter 2027"], "pass", None), (["Winter 2026", "Spring 2027"], "pass", None), (["Spring 2028"], "screen_out", None),
    (["Fall"], "pass", "term_unknown"),
    (["Spring/Summer 2027"], "pass", None), (["Summer/Fall"], "pass", "term_unknown"), (["Spring/Summer 2026"], "screen_out", None),
])
def test_terms(cfg, terms, verdict, flag):
    r = screen_metadata(J(terms=terms), cfg)
    assert r.verdict == verdict
    if flag:
        assert flag in r.flags


@pytest.mark.parametrize("title,verdict", [
    ("Software Engineer Intern - Summer 2026", "screen_out"), ("Software Engineer Intern (Fall 2027)", "pass"),
    ("Software Engineering Co-op Spring 2027", "pass"), ("Winter 2027 Software Co-op", "pass"), ("Fall 2026 Co-op", "screen_out"),
    ("Software Engineer Intern, Summer 2027", "pass"), ("Software Engineer Intern", "pass"),
    ("Software Engineer, New Grad", "screen_out"), ("Software Engineer Intern (PhD)", "screen_out"),
    ("Full-Time Software Engineer", "screen_out"),
])
def test_title_rules(cfg, title, verdict):
    assert screen_metadata(J(title=title), cfg).verdict == verdict


@pytest.mark.parametrize("cat,verdict", [("Software", "pass"), ("AI/ML/Data", "pass"), ("Quant", "pass"),
                                         ("Hardware", "screen_out"), ("Product", "screen_out"), ("Other", "screen_out")])
def test_category(cfg, cat, verdict):
    assert screen_metadata(J(category=cat), cfg).verdict == verdict


@pytest.mark.parametrize("deg,verdict", [(["PhD"], "screen_out"), (["Master's", "PhD"], "screen_out"),
                                         (["Bachelor's", "Master's"], "pass"), ([], "pass")])
def test_degrees(cfg, deg, verdict):
    assert screen_metadata(J(degrees=deg), cfg).verdict == verdict


@pytest.mark.parametrize("loc", ["London, UK", "Toronto, ON, Canada", "Remote in Canada", "Paris, France",
                                 "Seoul, South Korea", "Greater Manchester, UK", "Montr\u00e9al", "Cayman",
                                 "Dubai - United Arab Emirates", "Neukirchen-Vluyn, Germany", "St. John's, NL, Canada"])
def test_certain_non_us_is_screened_out(cfg, loc):
    r = screen_metadata(J(locations=[loc]), cfg)
    assert r.verdict == "screen_out" and r.reason == "non_us_location"


@pytest.mark.parametrize("loc", ["NYC", "New York", "Washington, D.C.", "Denver", "Minnesota", "SF", "South SF",
                                 "San Jose", "Remote", "Remote in USA", "Chicago, IL", "Paris, TX", "Birmingham, AL",
                                 "West Palm Beach, IL", "United States"])
def test_us_locations_pass_without_flag(cfg, loc):
    r = screen_metadata(J(locations=[loc]), cfg)
    assert r.verdict == "pass" and "non_us_location" not in r.flags and "location_unknown" not in r.flags


def test_location_mixed_and_unknown(cfg):
    # Any U.S. option passes clean.
    r = screen_metadata(J(locations=["Toronto, ON", "Austin, TX"]), cfg)
    assert r.verdict == "pass" and "non_us_location" not in r.flags
    # Unrecognizable location: pinged with a flag, never silently removed.
    r = screen_metadata(J(locations=["JIJONA PLANT"]), cfg)
    assert r.verdict == "pass" and "location_unknown" in r.flags
    # Non-U.S. plus unrecognizable: Alex decides, with the stronger flag.
    r = screen_metadata(J(locations=["Toronto, ON, Canada", "Mequinenza"]), cfg)
    assert r.verdict == "pass" and "non_us_location" in r.flags
    # Empty location list: no location verdict at all.
    r = screen_metadata(J(locations=[]), cfg)
    assert r.verdict == "pass" and "non_us_location" not in r.flags and "location_unknown" not in r.flags
    # The knob turns the hard gate back into a flag.
    cfg.screen.us_locations_only = False
    r = screen_metadata(J(locations=["London, UK"]), cfg)
    assert r.verdict == "pass" and "non_us_location" in r.flags


def _jd(fixtures_dir, name: str) -> str:
    return (fixtures_dir / "jd" / name).read_text(encoding="utf-8")


def test_jd_citizenship_requirement_screens_out(cfg, fixtures_dir):
    r = screen_jd(J(), _jd(fixtures_dir, "citizen_required.txt"), cfg)
    assert r.verdict == "screen_out" and r.reason == "jd_citizenship_required"
    assert "U.S. citizen" in r.evidence["snippet"]
    # With the knob off it reverts to a flag the user decides.
    cfg.screen.citizenship_required_out = False
    r = screen_jd(J(), _jd(fixtures_dir, "citizen_required.txt"), cfg)
    assert r.verdict == "pass" and "jd_citizenship_required" in r.flags


@pytest.mark.parametrize("phrase,verdict", [
    ("Applicants must be a U.S. citizen.", "screen_out"),
    ("U.S. citizenship is required for this role.", "screen_out"),
    ("This position is open to U.S. citizens only.", "screen_out"),
    ("Must be a U.S. citizen or permanent resident.", "pass"),
    ("Open to U.S. citizens and green card holders.", "pass"),
    ("Candidates must be authorized to work in the U.S.; citizenship is not required.", "pass"),
    ("We welcome all applicants.", "pass"),
])
def test_jd_citizenship_pr_guard(cfg, phrase, verdict):
    body = "Responsibilities: build tools. Qualifications: student. " * 8 + phrase
    r = screen_jd(J(), body, cfg)
    assert r.verdict == verdict, phrase
    if verdict == "pass":
        assert not (r.reason or "").startswith("jd_citizenship")


def test_jd_soft_mentions(cfg, fixtures_dir):
    r = screen_jd(J(), _jd(fixtures_dir, "clearance_mentioned.txt"), cfg)
    assert r.verdict == "pass" and "jd_mentions_citizenship_or_clearance" in r.flags and "jd_citizenship_required" not in r.flags
    r = screen_jd(J(), _jd(fixtures_dir, "us_person_itar.txt"), cfg)
    assert r.verdict == "pass" and "itar_us_person" in r.flags


def test_grad_window(cfg, fixtures_dir):
    assert parse_grad_window("graduating between December 2027 and June 2028")[0:2] == ((2027, 12), (2028, 6))
    assert parse_grad_window("must be graduating by May 2027")[1] == (2027, 5)
    assert parse_grad_window("graduation date of December 2027 or later")[0] == (2027, 12)
    assert parse_grad_window("Class of 2027 students only")[0:2] == ((2027, 1), (2027, 12))
    assert parse_grad_window("we love summer 2027") is None
    assert screen_jd(J(), _jd(fixtures_dir, "grad_window_ok.txt"), cfg).verdict == "pass"
    r = screen_jd(J(), _jd(fixtures_dir, "grad_window_excludes.txt"), cfg)
    assert r.verdict == "screen_out" and r.reason.startswith("grad_window")
    assert screen_jd(J(), _jd(fixtures_dir, "class_of_2027.txt"), cfg).verdict == "screen_out"
    assert screen_jd(J(), _jd(fixtures_dir, "us_person_itar.txt"), cfg).verdict == "pass"


def test_jd_term_mismatch(cfg, fixtures_dir):
    r = screen_jd(J(), _jd(fixtures_dir, "summer_2026.txt"), cfg)
    assert r.verdict == "screen_out" and r.reason.startswith("jd_term_mismatch")
    assert screen_jd(J(), _jd(fixtures_dir, "plain.txt"), cfg).verdict == "pass"
    assert screen_jd(J(), "Summer 2026 and Summer 2027 cohorts", cfg).verdict == "pass"
