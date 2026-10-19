"""JD fetch chain and watchlist behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from pipeline import db as dbmod, jd as jdmod, watchlist as wl
from pipeline import browser as browser_mod
from pipeline.models import Listing
from pipeline.sources import greenhouse
from pipeline.util import HttpError


def J(url, company="Acme Inc."):
    return SimpleNamespace(url=url, company=company, id=1)


def test_html_chain_and_login_wall(monkeypatch, fixtures_dir, cfg):
    good = (fixtures_dir / "html" / "generic_jd.html").read_bytes()
    wall = (fixtures_dir / "html" / "not_a_jd.html").read_bytes()
    monkeypatch.setattr(jdmod, "_http_get", lambda url, **kw: good if "good" in url else wall)
    r = jdmod.fetch_jd(J("https://plainco.com/good"), cfg, allow_browser=False)
    assert r.ok and r.source == "html" and "Responsibilities" in r.text
    r = jdmod.fetch_jd(J("https://plainco.com/wall"), cfg, allow_browser=False)
    assert not r.ok and r.source == "none" and [a["strategy"] for a in r.attempts] == ["none", "html"]


def test_eightfold_reads_the_json_api_not_the_page_config(monkeypatch, cfg):
    seen = []

    def fake_get(url, **kw):
        seen.append(url)
        return b'{"id": 1970393556951950, "job_description": "<b>Overview</b><p>Build CoreAI tools.</p><b>Qualifications</b><p>Currently pursuing a degree in computer science or a related field.</p>"}'

    monkeypatch.setattr(jdmod, "_http_get", fake_get)
    r = jdmod.fetch_jd(J("https://apply.careers.microsoft.com/careers/job/1970393556951950", "Microsoft"), cfg,
                       allow_browser=False)
    assert r.ok and r.source == "eightfold_api" and "Build CoreAI tools." in r.text and "<b>" not in r.text
    assert seen == ["https://apply.careers.microsoft.com/api/apply/v2/jobs/1970393556951950"]


def test_ats_first_then_browser_only_when_allowed(monkeypatch, fixtures_dir, cfg):
    monkeypatch.setattr(greenhouse, "_http_get", lambda url, **kw: (fixtures_dir / "greenhouse_job.json").read_bytes())
    r = jdmod.fetch_jd(J("https://job-boards.greenhouse.io/acme/jobs/4001"), cfg, allow_browser=False)
    assert r.ok and r.source == "greenhouse_api"

    def boom(url, **kw):
        raise HttpError(url, 403, "HTTP 403")

    monkeypatch.setattr(jdmod, "_http_get", boom)
    calls = []

    def fake_render(url, timeout_s):
        calls.append(url)
        return "Responsibilities: build things. Qualifications: students. " * 30

    monkeypatch.setattr(browser_mod, "render_text", fake_render)
    cfg.jd.browser_enabled = True
    r = jdmod.fetch_jd(J("https://plainco.com/js"), cfg, allow_browser=False)
    assert not r.ok and calls == []
    r = jdmod.fetch_jd(J("https://plainco.com/js"), cfg, allow_browser=True)
    assert r.ok and r.source == "browser" and calls == ["https://plainco.com/js"]


def test_store_jd_writes_file_and_events(cfg, conn):
    jid = dbmod.insert_job(conn, Listing(source="s", company="A", title="T", url="https://a/1"), "a/1", "f")
    res = jdmod.JDResult("Responsibilities: x " * 50, "html", "https://a/1", attempts=[{"strategy": "html", "ok": True}])
    jdmod.store_jd(conn, cfg, jid, res)
    assert (cfg.jd_dir / f"{jid}.txt").exists()
    job = dbmod.job_by_id(conn, jid)
    assert job.jd_source == "html" and job.jd_fetched_at
    assert [e["event"] for e in dbmod.events_for_job(conn, jid)] == ["jd_attempt", "jd_fetched"]


def test_looks_like_jd():
    assert not jdmod.looks_like_jd("short", 400)
    assert not jdmod.looks_like_jd("x" * 500, 400)
    assert jdmod.looks_like_jd("Responsibilities: a. Qualifications: b. " + "x" * 500, 400)
    # Page chrome with one stray marker does not pass; a long single-marker text does.
    assert not jdmod.looks_like_jd("About This Role " + "x" * 500, 400)
    assert jdmod.looks_like_jd("Qualifications " + "x" * 3000, 400)


def test_board_from_html_and_embed_fetch(monkeypatch, fixtures_dir, cfg):
    html = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=wehrtyou"></script><div id="grnhse_app"></div>'
    assert jdmod.board_from_html(html) == "wehrtyou"
    assert jdmod.board_from_html('<a href="https://job-boards.greenhouse.io/acme/jobs/123">x</a>') == "acme"
    assert jdmod.board_from_html("Grnhse.Iframe.load(123, \'acmeco\')") == "acmeco"
    assert jdmod.board_from_html("<p>nothing</p>") is None
    monkeypatch.setattr(jdmod, "_http_get", lambda url, **kw: html.encode("utf-8"))
    seen = {}

    def fake_job_text(board, job_id, **kw):
        seen["board"] = board
        return "Responsibilities: trade. Qualifications: student. " * 10

    monkeypatch.setattr(greenhouse, "get_job_text", fake_job_text)
    monkeypatch.setattr(greenhouse, "guess_board", lambda *a, **k: None)
    r = jdmod.fetch_jd(J("https://www.hudsonrivertrading.com/careers/job/?gh_jid=8052083", "Hudson River Trading"), cfg, allow_browser=False)
    assert r.ok and r.source == "greenhouse_api" and seen["board"] == "wehrtyou"


# ----------------------------------------------------------------------------- watchlist

def _job(conn, url, company="Acme Inc."):
    jid = dbmod.insert_job(conn, Listing(source="s", company=company, title="SWE Intern", url=url), url, "f")
    return dbmod.job_by_id(conn, jid)


@pytest.mark.parametrize("url,ats,board,host", [
    ("https://job-boards.greenhouse.io/acme/jobs/1", "greenhouse", "acme", None),
    ("https://jobs.lever.co/acme/3f7c2e4a-1b2c-4d5e-8f90-123456789abc", "lever", "acme", None),
    ("https://jobs.ashbyhq.com/acme/0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "ashby", "acme", None),
    ("https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/X/Y_R1", "workday", None, "acme.wd5.myworkdayjobs.com"),
])
def test_auto_add_per_ats(cfg, conn, url, ats, board, host):
    job = _job(conn, url)
    e = wl.auto_add_from_url(conn, cfg, job)
    assert e is not None and e.ats == ats and e.board == board and e.host == host and e.added_by == "auto"
    again = wl.auto_add_from_url(conn, cfg, job)
    assert again.id == e.id and len(dbmod.watchlist_all(conn)) == 1


def test_auto_add_greenhouse_guess_board(monkeypatch, cfg, conn):
    monkeypatch.setattr(greenhouse, "guess_board", lambda company, job_id, **kw: "acme-inc")
    e = wl.auto_add_from_url(conn, cfg, _job(conn, "https://acme.com/careers?gh_jid=77"))
    assert e.board == "acme-inc"
    monkeypatch.setattr(greenhouse, "guess_board", lambda company, job_id, **kw: None)
    assert wl.auto_add_from_url(conn, cfg, _job(conn, "https://beta.com/careers?gh_jid=78", company="Beta")) is None
    assert wl.auto_add_from_url(conn, cfg, _job(conn, "https://plain.com/j/1", company="Plain")) is None


def test_due_entries_and_backoff(conn):
    now = datetime.now(timezone.utc)
    wid = dbmod.watchlist_insert(conn, company="A", ats="lever", board="a", added_by="manual", poll_minutes=60)
    assert [e.id for e in wl.due_entries(conn, now)] == [wid]
    dbmod.watchlist_update(conn, wid, last_polled_at=(now - timedelta(minutes=30)).isoformat())
    assert wl.due_entries(conn, now) == []
    dbmod.watchlist_update(conn, wid, last_polled_at=(now - timedelta(minutes=61)).isoformat())
    assert len(wl.due_entries(conn, now)) == 1
    dbmod.watchlist_update(conn, wid, consecutive_failures=3, last_polled_at=(now - timedelta(minutes=61)).isoformat())
    assert wl.due_entries(conn, now) == []  # backed off to 8h
    dbmod.watchlist_update(conn, wid, enabled=False, consecutive_failures=0, last_polled_at=None)
    assert wl.due_entries(conn, now) == []


def test_poll_entry_filters_titles_and_records_errors(monkeypatch, cfg, conn, fixtures_dir):
    monkeypatch.setattr(greenhouse, "_http_get", lambda url, **kw: (fixtures_dir / "greenhouse_jobs.json").read_bytes())
    wid = dbmod.watchlist_insert(conn, company="Acme", ats="greenhouse", board="acme", added_by="auto")
    items = wl.poll_entry(conn, cfg, dbmod.watchlist_by_id(conn, wid))
    assert [i.title for i in items] == ["Software Engineer Intern - Summer 2027"]
    assert items[0].source == "watch:Acme" and items[0].company == "Acme"
    e = dbmod.watchlist_by_id(conn, wid)
    assert e.last_count == 1 and e.last_error is None

    def boom(url, **kw):
        raise HttpError(url, 500, "HTTP 500")

    monkeypatch.setattr(greenhouse, "_http_get", boom)
    assert wl.poll_entry(conn, cfg, dbmod.watchlist_by_id(conn, wid)) == []
    e = dbmod.watchlist_by_id(conn, wid)
    assert e.consecutive_failures == 1 and "500" in e.last_error
