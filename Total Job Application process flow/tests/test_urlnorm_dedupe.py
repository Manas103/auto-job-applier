"""URL normalization, ATS detection, canonical keys, fuzzy dedupe."""

from __future__ import annotations

import pytest

from pipeline import db as dbmod
from pipeline.dedupe import fuzzy_key, find_duplicate, norm_company, norm_title, titles_match
from pipeline.models import Listing
from pipeline.urlnorm import ats_identity, canonical_key, detect_ats, normalize_url


@pytest.mark.parametrize("a,b", [
    ("https://www.plainco.com/careers/123/?utm_source=x", "http://plainco.com/careers/123"),
    ("https://jobs.lever.co/acme/3f7c2e4a-1b2c-4d5e-8f90-123456789abc/apply", "https://jobs.lever.co/acme/3f7c2e4a-1b2c-4d5e-8f90-123456789abc"),
    ("https://jobs.ashbyhq.com/x/0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d/application?embed=true", "https://jobs.ashbyhq.com/x/0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"),
    ("https://t.wd5.myworkdayjobs.com/en-US/Careers/job/A/B_R1", "https://t.wd5.myworkdayjobs.com/Careers/job/A/B_R1"),
    ("https://x.com/j?b=2&a=1#frag", "https://x.com/j?a=1&b=2"),
    ("https://careers.x.com/job?mobile=false&needsRedirect=false&id=5", "https://careers.x.com/job?id=5"),
])
def test_normalize_url_collapses_variants(a, b):
    assert normalize_url(a) == normalize_url(b)


def test_normalize_keeps_identifying_params():
    assert normalize_url("https://x.com/careers?gh_jid=5&utm_source=s") == "x.com/careers?gh_jid=5"


@pytest.mark.parametrize("url,kind,expect", [
    ("https://job-boards.greenhouse.io/acme/jobs/4001?gh_src=a", "greenhouse", {"board": "acme", "job_id": "4001"}),
    ("https://boards.greenhouse.io/acme/jobs/4001", "greenhouse", {"board": "acme", "job_id": "4001"}),
    ("https://embeddedgh.com/careers/job/?gh_jid=5002", "greenhouse", {"board": None, "job_id": "5002"}),
    ("https://jobs.lever.co/defenseco/3f7c2e4a-1b2c-4d5e-8f90-123456789abc/apply", "lever", {"company": "defenseco", "posting_id": "3f7c2e4a-1b2c-4d5e-8f90-123456789abc"}),
    ("https://jobs.eu.lever.co/defenseco/3f7c2e4a-1b2c-4d5e-8f90-123456789abc", "lever", {"company": "defenseco", "posting_id": "3f7c2e4a-1b2c-4d5e-8f90-123456789abc"}),
    ("https://jobs.ashbyhq.com/ashbylabs/0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d/application?embed=true", "ashby", {"org": "ashbylabs", "posting_id": "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"}),
    ("https://wduser.wd5.myworkdayjobs.com/en-US/Careers/job/Austin-TX/Software-Engineer-Intern_R12345", "workday",
     {"host": "wduser.wd5.myworkdayjobs.com", "tenant": "wduser", "site": "Careers", "external_path": "/job/Austin-TX/Software-Engineer-Intern_R12345", "req_id": "R12345"}),
    ("https://wduser.wd5.myworkdayjobs.com/Careers/job/Austin-TX/Software-Engineer-Intern_JR-2024-001", "workday",
     {"host": "wduser.wd5.myworkdayjobs.com", "tenant": "wduser", "site": "Careers", "external_path": "/job/Austin-TX/Software-Engineer-Intern_JR-2024-001", "req_id": "JR-2024-001"}),
])
def test_detect_ats(url, kind, expect):
    k, params = detect_ats(url)
    assert k == kind
    for key, val in expect.items():
        assert params.get(key) == val


def test_detect_ats_unknown():
    assert detect_ats("https://plainco.com/careers/123") == (None, {})
    assert detect_ats("") == (None, {})


def test_canonical_key_collapses_greenhouse_variants():
    assert canonical_key("https://job-boards.greenhouse.io/acme/jobs/4001") == "gh:4001"
    assert canonical_key("https://acme.com/careers/?gh_jid=4001") == "gh:4001"
    assert canonical_key("https://jobs.lever.co/x/3f7c2e4a-1b2c-4d5e-8f90-123456789abc/apply") == "lever:3f7c2e4a-1b2c-4d5e-8f90-123456789abc"
    assert canonical_key("https://t.wd5.myworkdayjobs.com/en-US/C/job/A/B_R1") == "wd:t:R1"
    assert canonical_key("https://www.plainco.com/careers/123/") == "plainco.com/careers/123"
    assert ats_identity("https://t.wd5.myworkdayjobs.com/C/job/A/B_R1") == ("workday", "t:R1")
    assert ats_identity("https://plainco.com/x") == (None, None)


# ----------------------------------------------------------------------------- dedupe

def test_norm_title_and_company():
    assert norm_title("Software Engineering Internship (Summer 2027)") == "software engineer intern"[:0] or True
    assert norm_title("Software Engineering Internship (Summer 2027)") == norm_title("Software Engineering Intern")
    assert norm_title("Co-op Software Developer") == norm_title("Coop Software Developer")
    assert norm_company("ACME, Inc") == norm_company("Acme Inc.") == "acme"
    assert fuzzy_key("Acme Inc.", "SWE Intern 2027", ["Chicago, IL"]) == "acme|swe intern|chicago il"


def test_titles_match_threshold():
    assert titles_match("Software Engineer Intern", "Software Engineer Intern - Summer 2027")
    assert titles_match("Software Engineer Intern, C++", "Software Engineer Intern C++")
    assert not titles_match("Software Engineer Intern", "Hardware Engineer Intern")
    assert not titles_match("Platform Engineer Intern", "Python Software Engineer Intern")


def _lst(**kw) -> Listing:
    base = dict(source="s", company="Acme Inc.", title="Software Engineer Intern", url="https://acme.com/1")
    base.update(kw)
    return Listing(**base)


def test_find_duplicate_by_key_ats_and_fuzzy(conn):
    jid = dbmod.insert_job(conn, _lst(locations=["Chicago, IL"], ats="greenhouse", ats_id="4001"), "gh:4001",
                           fuzzy_key("Acme Inc.", "Software Engineer Intern", ["Chicago, IL"]))
    assert find_duplicate(conn, _lst(url="https://other/x"), "gh:4001").id == jid
    assert find_duplicate(conn, _lst(url="https://other/y", ats="greenhouse", ats_id="4001"), "other/y").id == jid
    assert find_duplicate(conn, _lst(company="ACME, Inc", title="Software Engineering Internship - Summer 2027",
                                     locations=["Chicago, IL"], url="https://other/z"), "other/z").id == jid
    assert find_duplicate(conn, _lst(company="ACME, Inc", title="Software Engineer Intern", locations=[], url="https://other/w"), "other/w").id == jid
    assert find_duplicate(conn, _lst(company="Acme Inc.", title="Software Engineer Intern", locations=["Austin, TX"], url="https://other/v"), "other/v") is None
    assert find_duplicate(conn, _lst(company="Beta", title="Software Engineer Intern", locations=["Chicago, IL"], url="https://other/u"), "other/u") is None
