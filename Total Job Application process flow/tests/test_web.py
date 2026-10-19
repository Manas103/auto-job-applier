"""Dashboard routes against an in-process server on a random port."""

from __future__ import annotations

import http.client
import json
import threading
import urllib.parse
from pathlib import Path

import pytest

from pipeline import db as dbmod
from pipeline.models import JobState, Listing
from pipeline.web.server import AppState, make_server


class FakePool:
    def __init__(self):
        self.queued: list[tuple[int, object]] = []
        self.active: dict[int, int] = {}
        self.status: dict[int, str] = {}
        self.job_object = type("JO", (), {"handle": None})()

    def enqueue(self, job_id, *, priority=0, stop_after=None):
        self.queued.append((job_id, stop_after))
        return True

    def queue_snapshot(self):
        return [j for j, _ in self.queued]

    def snapshot(self):
        return {"threads": 1, "queued": self.queue_snapshot(), "active": {}, "status": {}}


@pytest.fixture
def server(cfg, conn):
    pool = FakePool()
    state = AppState(cfg, collector=None, worker=pool)  # type: ignore[arg-type]
    srv = make_server(state, "127.0.0.1", 0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, pool
    srv.shutdown()


def _req(srv, method, path, data: dict | None = None):
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = urllib.parse.urlencode(data, doseq=True) if data is not None else None
    headers = {"Content-Type": "application/x-www-form-urlencoded"} if body is not None else {}
    c.request(method, path, body=body, headers=headers)
    r = c.getresponse()
    text = r.read().decode("utf-8")
    loc = r.getheader("Location")
    c.close()
    return r.status, text, loc


def _job(conn, **kw):
    base = dict(source="simplify", company="Akuna Capital", title="Software Engineer Intern, C++",
                url="https://akunacapital.com/careers/job/1?gh_jid=1", locations=["Chicago, IL"], category="Software", terms=["Summer 2027"])
    base.update(kw)
    from pipeline.urlnorm import canonical_key
    jid = dbmod.insert_job(conn, Listing(**base), canonical_key(base["url"]), "f")
    return jid


def test_pages_render(server, conn, cfg):
    srv, pool = server
    jid = _job(conn)
    dbmod.update_job(conn, jid, state=JobState.PINGED, decide_token="tok", flags=["jd_citizenship_required"])
    j2 = _job(conn, url="https://x.com/2", company="Beta")
    dbmod.update_job(conn, j2, state=JobState.JD_READY)
    j3 = _job(conn, url="https://x.com/3", company="Gamma")
    dbmod.update_job(conn, j3, state=JobState.DONE, role_folder=str(cfg.roles_dir / "Gamma_X"))
    (cfg.roles_dir / "Gamma_X").mkdir()
    (cfg.roles_dir / "Gamma_X" / "review.json").write_text(json.dumps({"verdict": "pass", "score": "29/29"}), encoding="utf-8")
    j4 = _job(conn, url="https://x.com/4", company="Delta")
    dbmod.update_job(conn, j4, state=JobState.SCREENED_OUT, screen_reason="citizenship_required_metadata")
    rid = dbmod.start_stage_run(conn, j2, "ideamaker", 1, str(cfg.logs_dir / "x.log"))
    dbmod.watchlist_insert(conn, company="Akuna", ats="greenhouse", board="akuna", added_by="auto")
    for path, needle in [("/pending", "Akuna Capital"), ("/backlog", "Beta"), ("/running", "Beta"), ("/done", "Gamma"),
                         ("/screened", "Delta"), ("/add", "Apply URL"), ("/watchlist", "akuna"), ("/costs", "% wk"),
                         (f"/job/{jid}", "Software Engineer Intern, C++"), ("/static/style.css", "body"), ("/api/status", '"counts"')]:
        status, text, _ = _req(srv, "GET", path)
        assert status == 200, path
        assert needle in text, path
    assert "flag red" in _req(srv, "GET", "/pending")[1]
    assert "review pass 29/29" in _req(srv, "GET", "/done")[1]
    assert _req(srv, "GET", "/")[0] == 303
    assert _req(srv, "GET", "/nope")[0] == 404
    assert _req(srv, "GET", f"/job/{j2}/log?stage=ideamaker&attempt=1")[0] == 200
    assert _req(srv, "GET", "/job/999")[0] == 404


def test_done_applied_toggle(server, conn, cfg):
    srv, pool = server
    import json as _json
    jid = _job(conn, url="https://x.com/applied", company="Zeta")
    folder = cfg.roles_dir / "Zeta_X"; folder.mkdir(parents=True, exist_ok=True)
    (folder / "review.json").write_text(_json.dumps({"verdict": "pass", "score": "30/30"}), encoding="utf-8")
    dbmod.update_job(conn, jid, state=JobState.DONE, role_folder=str(folder))
    tok = dbmod.get_setting(conn, "csrf_token")
    body = _req(srv, "GET", "/done")[1]
    assert "mark applied" in body and "applied-row" not in body
    st, _, _ = _req(srv, "POST", f"/job/{jid}/applied", data={"csrf": tok})
    assert st == 303
    assert "applied" in dbmod.job_by_id(conn, jid).flags
    body = _req(srv, "GET", "/done")[1]
    assert "applied-row" in body and "mark applied" not in body
    _req(srv, "POST", f"/job/{jid}/unapplied", data={"csrf": tok})
    assert "applied" not in dbmod.job_by_id(conn, jid).flags


def test_smartapply_fill_button(server, conn, cfg):
    srv, _ = server
    tok = dbmod.get_setting(conn, "csrf_token")
    jid = _job(conn, url="https://x.com/fill", company="Omega")
    dbmod.update_job(conn, jid, state=JobState.DONE, role_folder=str(Path(cfg.roles_dir) / "omega"))
    _, body, _ = _req(srv, "GET", "/done")
    assert 'action="http://127.0.0.1:8770/fill"' in body and f'name="job_id" value="{jid}"' in body
    _, body, _ = _req(srv, "GET", f"/job/{jid}")
    assert "Fill application" in body
    _req(srv, "POST", f"/job/{jid}/applied", data={"csrf": tok})
    _, body, _ = _req(srv, "GET", "/done")
    assert "Fill application" not in body          # applied: nothing left to fill
    _, body, _ = _req(srv, "GET", f"/job/{jid}")
    assert "Fill application" not in body


def test_decide_flow(server, conn):
    srv, pool = server
    jid = _job(conn)
    dbmod.update_job(conn, jid, state=JobState.PINGED, decide_token="tok")
    assert _req(srv, "GET", f"/decide?id={jid}&token=bad&yes=1")[0] == 403
    assert _req(srv, "GET", "/decide?id=x&token=tok&yes=1")[0] == 400
    status, text, _ = _req(srv, "GET", f"/decide?id={jid}&token=tok&yes=1")
    assert status == 200 and "Approved" in text and "Queued" in text
    assert dbmod.job_by_id(conn, jid).state == JobState.APPROVED and pool.queued == [(jid, None)]
    status, text, _ = _req(srv, "GET", f"/decide?id={jid}&token=tok&yes=0")
    assert status == 200 and "Already recorded" in text
    assert dbmod.job_by_id(conn, jid).state == JobState.APPROVED
    j2 = _job(conn, url="https://x.com/2")
    dbmod.update_job(conn, j2, state=JobState.NEW, decide_token="t2")
    assert "Ignored" in _req(srv, "GET", f"/decide?id={j2}&token=t2&yes=0")[1]
    assert dbmod.job_by_id(conn, j2).state == JobState.IGNORED


def test_csrf_and_actions(server, conn, cfg):
    srv, pool = server
    token = dbmod.get_setting(conn, "csrf_token")
    jid = _job(conn)
    dbmod.update_job(conn, jid, state=JobState.PINGED)
    assert _req(srv, "POST", f"/job/{jid}/approve", {"csrf": "wrong"})[0] == 403
    status, _, loc = _req(srv, "POST", f"/job/{jid}/approve", {"csrf": token})
    assert status == 303 and loc == f"/job/{jid}" and dbmod.job_by_id(conn, jid).state == JobState.APPROVED
    assert pool.queued[-1][0] == jid
    # ignore from new, override from screened, retry from failed, requeue, jd paste
    j2 = _job(conn, url="https://x.com/2")
    _req(srv, "POST", f"/job/{j2}/ignore", {"csrf": token})
    assert dbmod.job_by_id(conn, j2).state == JobState.IGNORED
    j3 = _job(conn, url="https://x.com/3")
    dbmod.update_job(conn, j3, state=JobState.SCREENED_OUT, screen_reason="term_mismatch:x")
    _req(srv, "POST", f"/job/{j3}/override", {"csrf": token})
    assert dbmod.job_by_id(conn, j3).state == JobState.APPROVED
    j4 = _job(conn, url="https://x.com/4")
    dbmod.update_job(conn, j4, state=JobState.FAILED, failed_stage="builder")
    _req(srv, "POST", f"/job/{j4}/retry", {"csrf": token})
    assert dbmod.job_by_id(conn, j4).state == JobState.IDEAMAKER_DONE and pool.queued[-1][0] == j4
    j5 = _job(conn, url="https://x.com/5")
    dbmod.update_job(conn, j5, state=JobState.NEEDS_JD)
    _req(srv, "POST", f"/job/{j5}/jd", {"csrf": token, "jd": "Responsibilities: build. " * 30})
    j = dbmod.job_by_id(conn, j5)
    assert j.state == JobState.JD_READY and j.role_folder and Path(j.role_folder).exists() and j.jd_source == "manual"
    # backlog run with stop_after; failed jobs are reset first
    j6 = _job(conn, url="https://x.com/6")
    dbmod.update_job(conn, j6, state=JobState.FAILED, failed_stage="ideamaker")
    status, _, loc = _req(srv, "POST", "/backlog/run", {"csrf": token, "ids": [str(j5), str(j6)], "stop_after": "ideamaker"})
    assert status == 303 and loc == "/backlog"
    assert dbmod.job_by_id(conn, j6).state == JobState.JD_READY
    from pipeline.models import Stage
    assert [(jid_, s) for jid_, s in pool.queued[-2:]] == [(j5, Stage.IDEAMAKER), (j6, Stage.IDEAMAKER)]
    assert _req(srv, "POST", "/backlog/run", {"ids": [str(j5)]})[0] == 403


def test_add_job_and_watchlist(server, conn, cfg):
    srv, pool = server
    token = dbmod.get_setting(conn, "csrf_token")
    status, _, loc = _req(srv, "POST", "/add", {"csrf": token, "company": "NewCo", "title": "SWE Intern", "url": "https://jobs.lever.co/newco/3f7c2e4a-1b2c-4d5e-8f90-123456789abc",
                                                 "location": "Austin, TX", "category": "Software", "jd": "Responsibilities: x. " * 40})
    assert status == 303 and loc.startswith("/job/")
    jid = int(loc.rsplit("/", 1)[1])
    j = dbmod.job_by_id(conn, jid)
    assert j.state == JobState.APPROVED and j.jd_text and j.ats == "lever" and j.sources == ["manual"] and pool.queued[-1][0] == jid
    # Same URL again: no duplicate.
    _req(srv, "POST", "/add", {"csrf": token, "company": "NewCo", "title": "SWE Intern", "url": "https://jobs.lever.co/newco/3f7c2e4a-1b2c-4d5e-8f90-123456789abc/apply"})
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert _req(srv, "POST", "/add", {"csrf": token, "company": "", "title": "x", "url": "https://x"})[2].startswith("/add?msg=")
    # Watchlist CRUD.
    _req(srv, "POST", "/watchlist/add", {"csrf": token, "company": "Acme", "ats": "workday", "host": "acme.wd5.myworkdayjobs.com", "tenant": "acme", "site": "Careers", "poll_minutes": "30"})
    entries = dbmod.watchlist_all(conn)
    assert len(entries) == 1 and entries[0].poll_minutes == 30 and entries[0].host == "acme.wd5.myworkdayjobs.com"
    wid = entries[0].id
    _req(srv, "POST", f"/watchlist/{wid}/toggle", {"csrf": token})
    assert dbmod.watchlist_by_id(conn, wid).enabled is False
    _req(srv, "POST", f"/watchlist/{wid}/delete", {"csrf": token})
    assert dbmod.watchlist_all(conn) == []


def test_api_status_shape(server, conn):
    srv, _ = server
    status, text, _ = _req(srv, "GET", "/api/status")
    data = json.loads(text)
    assert status == 200 and data["ok"] and "counts" in data and "worker" in data and "job_object" in data and data["collector"] is None


def test_upgrade_lite_resume_to_full(server, conn, cfg):
    srv, pool = server
    tok = dbmod.get_setting(conn, "csrf_token")
    jid = _job(conn, company="Small Co", url="https://x.com/small")
    folder = cfg.roles_dir / "SmallCo_Intern"
    folder.mkdir()
    (folder / "meta.json").write_text(json.dumps({"lane": "archetype"}), encoding="utf-8")
    dbmod.update_job(conn, jid, state=JobState.DONE, role_folder=str(folder), jd_text="jd text")
    st, text, _ = _req(srv, "GET", f"/job/{jid}")
    assert st == 200 and "Upgrade to full pipeline" in text and "Re-review" not in text
    st, _, loc = _req(srv, "POST", f"/job/{jid}/upgrade-full", data={"csrf": tok})
    assert st == 303
    j = dbmod.job_by_id(conn, jid)
    assert j.state == JobState.JD_READY and "lane:full" in j.flags
    assert j.role_folder == str(cfg.roles_dir / "SmallCo_Intern_full") and (Path(j.role_folder) / "jd.txt").exists()
    assert (folder / "meta.json").exists()  # the lite resume that may already have been sent stays
    assert pool.queued[-1][0] == jid
    assert dbmod.events_for_job(conn, jid)[-1]["event"] == "upgrade_full"


def test_pending_bulk_actions_and_best_first_order(server, conn, cfg):
    srv, pool = server
    tok = dbmod.get_setting(conn, "csrf_token")
    for company, score, tier in [("Low Co", 40, "C"), ("Top Co", 95, "S"), ("Mid Co", 60, "B")]:
        conn.execute("INSERT INTO company_scores(company, score, tier, reason, scored_at, model) VALUES (?,?,?,?,?,?)",
                     (company, score, tier, "", "2026-10-01T00:00:00+00:00", "test"))
    ids = {}
    for company in ("Low Co", "Top Co", "Mid Co"):
        ids[company] = _job(conn, company=company, url=f"https://x.com/{company.replace(' ', '')}")
        dbmod.update_job(conn, ids[company], state=JobState.PINGED, decide_token=company)
    text = _req(srv, "GET", "/pending")[1]
    assert text.index("Top Co") < text.index("Mid Co") < text.index("Low Co")
    assert 'form="bulk"' in text and 'action="/pending/bulk"' in text
    st, _, loc = _req(srv, "POST", "/pending/bulk", data={"csrf": tok, "action": "approve", "ids": [ids["Top Co"], ids["Mid Co"]]})
    assert st == 303 and loc.endswith("/pending")
    assert dbmod.job_by_id(conn, ids["Top Co"]).state == JobState.APPROVED
    assert {j for j, _ in pool.queued} == {ids["Top Co"], ids["Mid Co"]}
    _req(srv, "POST", "/pending/bulk", data={"csrf": tok, "action": "ignore", "ids": [ids["Low Co"], ids["Top Co"]]})
    assert dbmod.job_by_id(conn, ids["Low Co"]).state == JobState.IGNORED
    assert dbmod.job_by_id(conn, ids["Top Co"]).state == JobState.APPROVED  # an approved job is not un-approved by ignore
    assert _req(srv, "POST", "/pending/bulk", data={"csrf": "bad", "action": "ignore", "ids": [ids["Mid Co"]]})[0] == 403
