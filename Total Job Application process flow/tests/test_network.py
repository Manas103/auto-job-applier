"""Live network checks (PIPELINE_NET_TESTS=1): the three community lists and one board per ATS, chosen from live data."""

from __future__ import annotations

import json

from pathlib import Path

import pytest

from pipeline.config import load_config
from pipeline.jd import fetch_jd
from pipeline.sources import ashby, greenhouse, lever, workday
from pipeline.sources.simplify import parse_simplify_listings
from pipeline.sources.speedyapply import parse_speedyapply_readme
from pipeline.urlnorm import detect_ats
from pipeline.util import http_get

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def live():
    root = Path(__file__).resolve().parents[1]
    cfg = load_config() if (root / "config.json").exists() else load_config(root / "config.example.json", project_root=root)
    srcs = {s["name"]: s for s in cfg.collector.sources}
    simplify = parse_simplify_listings(http_get(srcs["simplify"]["url"]), "simplify")
    speedy = parse_speedyapply_readme(http_get(srcs["speedyapply"]["url"]), "speedyapply")
    return cfg, simplify, speedy


def test_lists_parse(live):
    cfg, simplify, speedy = live
    assert len(simplify) > 500 and len(speedy) > 30
    assert sum(1 for l in simplify if l.terms) > 100


def _candidates(listings, kind, n=6):
    out = []
    for l in listings:
        k, params = detect_ats(l.url)
        if k == kind:
            out.append((l, params))
        if len(out) >= n:
            break
    if not out:
        pytest.skip(f"no live {kind} listing found")
    return out


def _first(listings, kind):
    return _candidates(listings, kind, 1)[0]


def test_greenhouse_live(live):
    """Community lists keep stale postings marked active, so try a few until the API answers."""
    cfg, simplify, _ = live
    ok = 0
    for l, p in _candidates(simplify, "greenhouse"):
        board = p.get("board") or greenhouse.guess_board(l.company, p["job_id"])
        if not board:
            continue
        text = greenhouse.get_job_text(board, p["job_id"])
        if text and len(text) > 200:
            items = greenhouse.list_jobs(board)
            assert items and all(i.ats == "greenhouse" for i in items)
            ok += 1
            break
    assert ok, "no live greenhouse posting answered"


def test_lever_live(live):
    cfg, simplify, _ = live
    l, p = _first(simplify, "lever")
    items = lever.list_postings(p["company"])
    assert items and any(i.ats_id == p["posting_id"] for i in items) or lever.get_posting_text(p["company"], p["posting_id"])


def test_ashby_live(live):
    cfg, simplify, _ = live
    l, p = _first(simplify, "ashby")
    items = ashby.list_jobs(p["org"])
    assert items and all(i.jd_text for i in items[:3])


def test_workday_live(live):
    cfg, simplify, _ = live
    l, p = _first(simplify, "workday")
    items = workday.list_jobs(p["host"], p["tenant"], p["site"], search_text="intern", max_pages=1)
    assert isinstance(items, list)
    if p.get("external_path"):
        text = workday.get_job_text(p["host"], p["tenant"], p["site"], p["external_path"])
        assert text is None or len(text) > 100


def test_fetch_jd_chain_live(live):
    cfg, simplify, _ = live
    results = [fetch_jd(l, cfg, allow_browser=False) for l, _ in _candidates(simplify, "greenhouse", 4)]
    assert any(r.ok and r.source in ("greenhouse_api", "html") for r in results), [r.attempts for r in results]
