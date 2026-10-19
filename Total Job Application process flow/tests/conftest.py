"""Shared fixtures: temp project root, config, DB, deterministic today, no-network guard."""

from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.request
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import db as dbmod  # noqa: E402
from pipeline import util  # noqa: E402
from pipeline.config import load_config  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_CLAUDE = Path(__file__).parent / "fake_claude" / "claude.py"
TODAY = date(2026, 8, 22)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr(util, "today", lambda: TODAY)
    yield


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    if request.node.get_closest_marker("network"):
        if not os.environ.get("PIPELINE_NET_TESTS"):
            pytest.skip("set PIPELINE_NET_TESTS=1 to run network tests")
        yield
        return

    def _blocked(*a, **k):
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
    yield


@pytest.fixture
def tmp_project(tmp_path):
    """A temp project root with config.json, playbooks copied, empty data/roles/projects dirs."""
    root = tmp_path / "proj with space"
    root.mkdir()
    shutil.copytree(ROOT / "playbooks", root / "playbooks")
    (root / "roles").mkdir()
    (root / "projects").mkdir()
    (root / "data").mkdir()
    index = ROOT / "roles" / "INDEX.md"
    shutil.copy(index if index.exists() else FIXTURES / "roles_INDEX.md", root / "roles" / "INDEX.md")
    cfg_data = {
        "collector": {"sources": [
            {"name": "simplify", "type": "simplify_json", "url": "https://example.invalid/simplify.json", "enabled": True},
            {"name": "vanshb03", "type": "simplify_json", "url": "https://example.invalid/vanshb03.json", "enabled": True},
            {"name": "speedyapply", "type": "speedyapply_md", "url": "https://example.invalid/README.md", "enabled": True},
        ], "seed_quiet": True},
        "claude": {"cmd": [sys.executable, str(FAKE_CLAUDE)], "model": "claude-opus-5"},
        "verify": {"compile": False, "pdflatex": "pdflatex", "pdfinfo": "pdfinfo"},
        "notify": {"enabled": False},
        "jd": {"browser_enabled": False},
        "worker": {"threads": 2, "governor_enabled": False},
        "rank": {"enabled": False},
        "tiers": {"enabled": False},
        "research": {"enabled": False},
    }
    (root / "config.json").write_text(json.dumps(cfg_data, indent=2), encoding="utf-8")
    return root


@pytest.fixture
def cfg(tmp_project):
    return load_config(tmp_project / "config.json", project_root=tmp_project)


@pytest.fixture
def conn(cfg):
    c = dbmod.connect(cfg.db_path)
    dbmod.init_schema(c)
    yield c
    c.close()


@pytest.fixture
def fixtures_dir():
    return FIXTURES
