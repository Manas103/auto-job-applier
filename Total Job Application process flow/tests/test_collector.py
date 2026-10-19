"""Collector: seed, new-job ping, screening, dedupe across sources, deactivation, deferral, tier-2 isolation."""

from __future__ import annotations

import json

import pytest

from pipeline import collector, db as dbmod, notify as notify_mod, watchlist as wl
from pipeline.models import JobState, Listing, WatchlistEntry


def _patch_sources(monkeypatch, fixtures_dir, *, simplify="listings_simplify.json", vansh="listings_vanshb03.json",
                   speedy="speedyapply_README.md", fail: set[str] = frozenset()):
    def fake_get(url, **kw):
        if "simplify.json" in url:
            if "simplify" in fail:
                raise RuntimeError("boom")
            return (fixtures_dir / simplify).read_bytes()
        if "vanshb03.json" in url:
            if "vanshb03" in fail:
                raise RuntimeError("boom")
            return (fixtures_dir / vansh).read_bytes()
        if "README.md" in url:
            if "speedyapply" in fail:
                raise RuntimeError("boom")
            return (fixtures_dir / speedy).read_bytes()
        raise AssertionError(url)

    monkeypatch.setattr(collector, "_http_get", fake_get)


@pytest.fixture
def pings(monkeypatch):
    calls: list[int] = []

    def fake_notify(conn, cfg, job):
        calls.append(job.id)
        dbmod.update_job(conn, job.id, pinged_at="now")
        return True

    monkeypatch.setattr(collector, "notify_job", fake_notify)
    monkeypatch.setattr(collector, "in_quiet_hours", lambda cfg: False)
    return calls


def test_seed_is_quiet_then_new_job_pings_once(monkeypatch, fixtures_dir, cfg, conn, pings):
    _patch_sources(monkeypatch, fixtures_dir)
    rep = collector.run_collect(conn, cfg, tier2=False)
    assert rep.seed and rep.pinged == 0
    # 6 simplify + 3 vansh + 4 speedy, with overlaps: plainco (url variant), acme (fuzzy)
    assert rep.new == 11
    assert pings == []
    states = dbmod.count_by_state(conn)
    assert states.get("new", 0) >= 1 and states.get("screened_out", 0) >= 1
    seeded = [j for j in dbmod.all_jobs(conn) if "seed" in j.flags]
    assert seeded and all(j.state == JobState.NEW for j in seeded)

    # Second run: one brand-new listing appears.
    extra = json.loads((fixtures_dir / "listings_simplify.json").read_text(encoding="utf-8"))
    extra.append({"id": "a9", "company_name": "Fresh Co", "title": "Backend Engineer Intern",
                  "url": "https://freshco.com/jobs/1", "locations": ["Austin, TX"], "category": "Software Engineering",
                  "sponsorship": "Other", "terms": ["Summer 2027"], "degrees": ["Bachelor's"], "date_posted": 1755000000,
                  "active": True, "is_visible": True})
    (fixtures_dir.parent / "_tmp_simplify.json").write_text(json.dumps(extra), encoding="utf-8")
    try:
        _patch_sources(monkeypatch, fixtures_dir.parent, simplify="_tmp_simplify.json",
                       vansh="fixtures/listings_vanshb03.json", speedy="fixtures/speedyapply_README.md")
        cfg.screen.fetch_jd_before_ping = False
        rep2 = collector.run_collect(conn, cfg, tier2=False)
        # Third run with the same data: nothing new, nothing pinged.
        rep3 = collector.run_collect(conn, cfg, tier2=False)
    finally:
        (fixtures_dir.parent / "_tmp_simplify.json").unlink()
    assert rep2.new == 1 and rep2.pinged == 1 and not rep2.seed
    assert len(pings) == 1
    job = dbmod.job_by_id(conn, pings[0])
    assert job.company == "Fresh Co" and job.state == JobState.PINGED
    assert rep3.new == 0 and rep3.pinged == 0 and rep3.updated >= 10


def test_screening_and_dedupe_details(monkeypatch, fixtures_dir, cfg, conn, pings):
    _patch_sources(monkeypatch, fixtures_dir)
    collector.run_collect(conn, cfg, tier2=False)
    jobs = {j.company: j for j in dbmod.all_jobs(conn)}
    # Citizenship-required -> screened out silently.
    assert jobs["Defense Co"].state == JobState.SCREENED_OUT and jobs["Defense Co"].screen_reason == "citizenship_required_metadata"
    # Fall 2026 -> out; PhD-only -> out; Hardware -> out (and merged with vansh's PlainCo by URL variant).
    assert jobs["Workday User"].state == JobState.SCREENED_OUT
    assert jobs["Ashby Labs"].state == JobState.SCREENED_OUT
    plain = jobs["PlainCo"]
    assert plain.state == JobState.SCREENED_OUT and set(plain.sources) == {"simplify", "vanshb03"}
    assert plain.terms == ["Summer 2027"]  # blank-filled from the second source
    # Fuzzy merge: "ACME, Inc" internship merged into Acme Inc. greenhouse job.
    acme = jobs["Acme Inc."]
    assert set(acme.sources) == {"simplify", "vanshb03"} and acme.key == "gh:4001"
    # speedyapply rows present, quant categorized.
    assert jobs["QuantCo"].category == "Quant" and jobs["QuantCo"].ats_id == "9100"
    # Bare "Summer" term passes with a flag.
    assert "term_unknown" in jobs["Embedded GH"].flags


def test_deactivation_only_for_fetched_sources(monkeypatch, fixtures_dir, cfg, conn, pings):
    _patch_sources(monkeypatch, fixtures_dir)
    collector.run_collect(conn, cfg, tier2=False)
    # speedyapply fails this run: its jobs must not be deactivated.
    _patch_sources(monkeypatch, fixtures_dir, fail={"speedyapply"})
    rep = collector.run_collect(conn, cfg, tier2=False)
    assert "speedyapply" in rep.errors
    jobs = {j.company: j for j in dbmod.all_jobs(conn)}
    assert jobs["OtherCo"].active is True
    # Simplify drops a job (use vansh fixture as the simplify payload): a7/Embedded GH vanishes from its only source.
    _patch_sources(monkeypatch, fixtures_dir, simplify="listings_vanshb03.json")
    rep = collector.run_collect(conn, cfg, tier2=False)
    jobs = {j.company: j for j in dbmod.all_jobs(conn)}
    assert jobs["Embedded GH"].active is False and rep.deactivated >= 1
    assert jobs["PlainCo"].active is True  # still listed by vanshb03


def test_ping_cap_defers_and_next_tick_pings(monkeypatch, fixtures_dir, cfg, conn, pings):
    cfg.notify.max_pings_per_run = 1
    cfg.collector.seed_quiet = False
    cfg.screen.fetch_jd_before_ping = False
    _patch_sources(monkeypatch, fixtures_dir)
    rep = collector.run_collect(conn, cfg, tier2=False)
    assert rep.pinged == 1 and rep.deferred >= 1
    deferred = [j for j in dbmod.all_jobs(conn) if "ping_deferred" in j.flags]
    assert deferred
    rep2 = collector.run_collect(conn, cfg, tier2=False)
    assert rep2.pinged == 1 and rep2.new == 0
    assert len(pings) == 2
    j = dbmod.job_by_id(conn, pings[1])
    assert "ping_deferred" not in j.flags and j.state == JobState.PINGED


def test_suspicious_source_flag(monkeypatch, fixtures_dir, cfg, conn, pings):
    _patch_sources(monkeypatch, fixtures_dir)
    collector.run_collect(conn, cfg, tier2=False)
    (fixtures_dir.parent / "_tmp_empty.json").write_text("[]", encoding="utf-8")
    try:
        _patch_sources(monkeypatch, fixtures_dir.parent, simplify="_tmp_empty.json",
                       vansh="fixtures/listings_vanshb03.json", speedy="fixtures/speedyapply_README.md")
        rep = collector.run_collect(conn, cfg, tier2=False)
    finally:
        (fixtures_dir.parent / "_tmp_empty.json").unlink()
    assert "simplify" in rep.suspicious


def test_tier2_error_isolation_and_listing_merge(monkeypatch, fixtures_dir, cfg, conn, pings):
    _patch_sources(monkeypatch, fixtures_dir)
    collector.run_collect(conn, cfg, tier2=False)
    dbmod.watchlist_insert(conn, company="Acme Inc.", ats="greenhouse", board="acme", added_by="auto")
    dbmod.watchlist_insert(conn, company="Broken", ats="lever", board="broken", added_by="manual")

    def fake_poll(conn_, cfg_, entry: WatchlistEntry):
        if entry.ats == "lever":
            dbmod.watchlist_update(conn_, entry.id, last_polled_at="x", last_error="HTTP 500", consecutive_failures=1)
            return []
        dbmod.watchlist_update(conn_, entry.id, last_polled_at="x", last_ok_at="x", last_error=None, last_count=1)
        return [Listing(source=f"watch:{entry.company}", company=entry.company, title="Platform Intern (Summer 2027)",
                        url="https://job-boards.greenhouse.io/acme/jobs/4010", locations=["Chicago, IL"],
                        category="Software", terms=[], jd_text="Responsibilities: build. Qualifications: student. " * 20,
                        ats="greenhouse", ats_id="4010")]

    monkeypatch.setattr(wl, "poll_entry", fake_poll)
    cfg.collector.seed_quiet = True
    rep = collector.run_collect(conn, cfg, tier2=True)
    assert "watch:Broken" in rep.errors and rep.new == 1 and rep.pinged == 1
    j = dbmod.job_by_key(conn, "gh:4010")
    assert j.jd_text and j.jd_source == "ats_api" and j.state == JobState.PINGED


def test_all_sources_fail(monkeypatch, fixtures_dir, cfg, conn, pings):
    _patch_sources(monkeypatch, fixtures_dir, fail={"simplify", "vanshb03", "speedyapply"})
    rep = collector.run_collect(conn, cfg, tier2=False)
    assert len(rep.errors) == 3 and rep.new == 0
