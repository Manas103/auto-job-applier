"""Test rig: temp project, fake pipeline DB, a counting fixture server, and (when Chrome exists) a session-scoped
real SmartApply Chrome on a temp profile so the guard is tested against the real thing."""

from __future__ import annotations

import http.server
import json
import shutil
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from smartapply import db
from smartapply.config import load_config

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PAGES = FIXTURES / "pages"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, request):
    """Tests never touch the internet. Local 127.0.0.1 fixture servers are allowed."""
    real = urllib.request.urlopen

    def guarded(req, *a, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "127.0.0.1" in url or "localhost" in url:
            return real(req, *a, **kw)
        raise AssertionError(f"network access blocked in tests: {url}")
    monkeypatch.setattr(urllib.request, "urlopen", guarded)


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "config.json").write_text(json.dumps({
        "data_dir": str(tmp_path / "data"),
        "pipeline_root": str(tmp_path / "pipeline"),
        "archon_env": str(tmp_path / "archon.env"),
        "profile_path": str(tmp_path / "profile.yaml"),
        "notify": {"enabled": False},
        "chrome": {"profile_dir": str(tmp_path / "chrome-profile")},
    }), encoding="utf-8")
    return tmp_path


@pytest.fixture
def cfg(tmp_project: Path):
    return load_config(tmp_project / "config.json", project_root=tmp_project)


@pytest.fixture
def conn(cfg):
    c = db.connect(cfg.db_path)
    yield c
    c.close()


def make_pipeline(root: Path, jobs: list[dict]) -> Path:
    """A minimal pipeline.sqlite3 plus role folders with a realistic PDF, mirroring pipeline/db.py's schema."""
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "playbooks").mkdir(exist_ok=True)
    (root / "playbooks" / "ASSETS.md").write_text(
        "# ASSETS\n\n| Field | Value |\n|---|---|\n| Name | Alex Doe |\n| Email | alex@example.com |\n"
        "| Phone | +1-555-010-0142 |\n| LinkedIn | linkedin.com/in/alex-doe |\n"
        "| Degree | Bachelor of Science in Computer Science, GPA 3.70, Aug. 2024 to May 2028 |\n", encoding="utf-8")
    dbp = root / "data" / "pipeline.sqlite3"
    c = sqlite3.connect(dbp)
    c.executescript("""
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE jobs (id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, url TEXT NOT NULL, company TEXT NOT NULL,
      title TEXT NOT NULL, locations TEXT NOT NULL DEFAULT '[]', category TEXT NOT NULL DEFAULT 'Other',
      sponsorship TEXT NOT NULL DEFAULT '', terms TEXT NOT NULL DEFAULT '[]', degrees TEXT NOT NULL DEFAULT '[]',
      pay TEXT NOT NULL DEFAULT '', date_posted TEXT NOT NULL DEFAULT '', sources TEXT NOT NULL DEFAULT '[]',
      source_ids TEXT NOT NULL DEFAULT '{}', ats TEXT, ats_id TEXT, fuzzy_key TEXT NOT NULL, first_seen TEXT NOT NULL,
      last_seen TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL DEFAULT 'new',
      state_updated_at TEXT NOT NULL, failed_stage TEXT, screen_reason TEXT, flags TEXT NOT NULL DEFAULT '[]',
      jd_text TEXT, jd_source TEXT, jd_fetched_at TEXT, role_folder TEXT, decide_token TEXT, pinged_at TEXT,
      decided_at TEXT, priority INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '');
    CREATE TABLE job_events (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, ts TEXT NOT NULL, event TEXT NOT NULL,
      from_state TEXT, to_state TEXT, detail TEXT);
    INSERT INTO settings VALUES('csrf_token', 'testtoken');
    """)
    for j in jobs:
        folder = root / "roles" / j["folder"]
        folder.mkdir(parents=True, exist_ok=True)
        slug = j.get("slug", "acme")
        (folder / f"ideal_candidate_realistic_{slug}.pdf").write_bytes(b"%PDF-1.4 fake\n")
        (folder / f"experience_descriptions_{slug}.md").write_text("## Acme Labs (Feb. 2026 to Jun. 2026)\nForm fields: Acme Labs | Software Engineer Intern | Austin, TX | Feb. 2026 | Jun. 2026\n", encoding="utf-8")
        (folder / "meta.json").write_text(json.dumps({"slug": slug, "company": j["company"], "hard_gates": []}), encoding="utf-8")
        (folder / "jd.txt").write_text("Job description", encoding="utf-8")
        c.execute("INSERT INTO jobs(id, key, url, company, title, ats, ats_id, fuzzy_key, first_seen, last_seen, state, "
                  "state_updated_at, flags, role_folder) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (j["id"], f"k{j['id']}", j["url"], j["company"], j.get("title", "SWE Intern"), j.get("ats"), j.get("ats_id"),
                   "fz", "2026-09-01", "2026-09-01", j.get("state", "done"), "2026-09-01", j.get("flags", "[]"), str(folder)))
    c.commit()
    c.close()
    return dbp


@pytest.fixture
def pipeline(tmp_project: Path) -> Path:
    return make_pipeline(tmp_project / "pipeline", [
        {"id": 101, "url": "https://job-boards.greenhouse.io/acme/jobs/4324855009", "company": "Acme", "ats": "greenhouse",
         "ats_id": "4324855009", "folder": "Acme_SWE_Intern_Summer2027", "slug": "acme"},
        {"id": 102, "url": "https://acme.wd5.myworkdayjobs.com/en-US/ext/job/Austin-TX/SWE-Intern_R-1", "company": "Acme WD",
         "ats": "workday", "ats_id": "acme:R-1", "folder": "AcmeWD_SWE_Intern_Summer2027", "slug": "acmewd"},
        {"id": 103, "url": "https://job-boards.greenhouse.io/old/jobs/1", "company": "Old", "ats": "greenhouse", "ats_id": "1",
         "folder": "Old_Summer2027", "slug": "old", "flags": '["applied"]'},
        {"id": 104, "url": "https://jobs.lever.co/xcimer/fee9965c-8040-4614-8fd1-10bddfe3b911/apply", "company": "Xcimer",
         "ats": "lever", "ats_id": "fee9965c-8040-4614-8fd1-10bddfe3b911", "folder": "Xcimer_Summer2027", "slug": "xcimer"},
        {"id": 105, "url": "https://jobs.ashbyhq.com/NorthwoodSpace/ce3d4b73-461e-4128-a6f1-f933897e8119/application?embed=true",
         "company": "Northwood Space", "ats": "ashby", "ats_id": "ce3d4b73-461e-4128-a6f1-f933897e8119",
         "folder": "Northwood_Summer2027", "slug": "northwood"},
    ])


# ---------------------------------------------------------------- fixture HTTP server with counting endpoints

class _Counting(http.server.SimpleHTTPRequestHandler):
    hits: dict[str, int] = {}
    lock = threading.Lock()

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(PAGES), **kw)

    def log_message(self, *a):  # silence
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        with self.lock:
            self.hits[self.path] = self.hits.get(self.path, 0) + 1
        # Workday answers the draft-creation POST with the application id; the adapter captures it from the response
        body = b'{"ok":true,"id":"app-1"}' if "/jobapplications" in self.path else b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FixtureServer:
    def __init__(self):
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Counting)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def url(self, page: str) -> str:
        return f"http://127.0.0.1:{self.port}/{page}"

    def hits(self, path_fragment: str) -> int:
        with _Counting.lock:
            return sum(v for k, v in _Counting.hits.items() if path_fragment in k)

    def reset(self) -> None:
        with _Counting.lock:
            _Counting.hits.clear()

    def stop(self) -> None:
        self.srv.shutdown()


@pytest.fixture(scope="session")
def fixture_server():
    s = FixtureServer()
    yield s
    s.stop()


# ---------------------------------------------------------------- real Chrome (session)

@pytest.fixture(scope="session")
def chrome(tmp_path_factory):
    from smartapply.browser.chrome import Chrome
    from smartapply.config import load_config as _lc
    exe = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    if not exe.exists():
        pytest.skip("chrome.exe not installed")
    prof = tmp_path_factory.mktemp("chrome-profile")
    cfg = _lc(None, overrides={"chrome": {"profile_dir": str(prof), "exe": str(exe)}, "data_dir": str(tmp_path_factory.mktemp("data"))})
    ch = Chrome(cfg)
    ch.launch()
    # The first window is the only one that can ever appear; open it once here, minimized, so every test's
    # background tab lands in an existing minimized window and the focus assertion is meaningful.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = ch.attach(pw)
        ch.open_background_tab(b, "about:blank")
        b.close()
    yield ch
    with sync_playwright() as pw:
        try:
            b = ch.attach(pw)
            s = b.new_browser_cdp_session()
            s.send("Browser.close")
        except Exception:  # noqa: BLE001
            pass
    time.sleep(1.0)
    shutil.rmtree(prof, ignore_errors=True)
