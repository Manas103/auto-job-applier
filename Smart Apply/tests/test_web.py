"""Dashboard routes against an in-process server on a random port: the pipeline's "Fill application" hand-off,
answer memory, and the cost page."""

from __future__ import annotations

import http.client
import json
import threading
import urllib.parse

import pytest

from smartapply import applied, db, intake, memory, tracker
from smartapply.models import AppState
from smartapply.web.server import AppState_, make_server


class FakeRunner:
    def __init__(self):
        self.filled: list[int] = []

    def enqueue_fill(self, app_id: int) -> None:
        self.filled.append(app_id)

    def open_review(self) -> None:
        pass

    def mark_applied(self, app_id: int, *, source: str) -> bool:
        return True


def _serve(state: AppState_):
    srv = make_server(state, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _req(srv, method, path, data: dict | None = None, headers: dict | None = None):
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = urllib.parse.urlencode(data, doseq=True) if data is not None else None
    h = {"Content-Type": "application/x-www-form-urlencoded"} if body is not None else {}
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    text = r.read().decode("utf-8")
    loc = r.getheader("Location")
    c.close()
    return r.status, text, loc


@pytest.fixture
def server(cfg, conn, pipeline):
    runner = FakeRunner()
    state = AppState_(cfg, profile=None, runner=runner)
    srv = _serve(state)
    yield srv, runner
    srv.shutdown()


def test_fill_from_pipeline_queues_and_starts(server, conn, cfg):
    srv, runner = server
    # A form post from the pipeline dashboard (its Origin) queues the pipeline job here and starts the fill.
    st, _, loc = _req(srv, "POST", "/fill", {"job_id": "101"}, {"Origin": "http://127.0.0.1:8765"})
    assert st == 303 and loc.startswith("/app/")
    row = db.application_by_job(conn, 101)
    assert row is not None and row["state"] == "queued" and (row_dir := row["workdir"])
    assert runner.filled == [int(row["id"])]
    assert (cfg.applications_dir / "101" / "job.json").exists() and row_dir.endswith("101")
    # Same job again: no second application row, the fill is simply enqueued again.
    _req(srv, "POST", "/fill", {"job_id": "101"}, {"Origin": "http://127.0.0.1:8765"})
    assert len(db.all_applications(conn)) == 1 and runner.filled == [int(row["id"])] * 2
    # Ready for review goes back to drafted (a refill); applied is left alone.
    tracker.transition(conn, int(row["id"]), AppState.PREFETCHED, "t")
    tracker.transition(conn, int(row["id"]), AppState.DRAFTED, "t")
    tracker.transition(conn, int(row["id"]), AppState.FILLING, "t")
    tracker.transition(conn, int(row["id"]), AppState.READY_FOR_REVIEW, "t")
    _req(srv, "POST", "/fill", {"job_id": "101"}, {"Origin": "http://127.0.0.1:8765"})
    assert tracker.load(conn, int(row["id"])).state == AppState.DRAFTED
    for st_ in (AppState.FILLING, AppState.READY_FOR_REVIEW, AppState.APPLIED):
        tracker.transition(conn, int(row["id"]), st_, "t")
    _req(srv, "POST", "/fill", {"job_id": "101"}, {"Origin": "http://127.0.0.1:8765"})
    assert tracker.load(conn, int(row["id"])).state == AppState.APPLIED and len(runner.filled) == 3


def test_fill_from_pipeline_rejects_other_origins(server, conn):
    srv, runner = server
    for headers in ({}, {"Origin": "https://evil.example"}, {"Origin": "http://127.0.0.1:9999"}):
        st, body, _ = _req(srv, "POST", "/fill", {"job_id": "101"}, headers)
        assert st == 403, headers
    assert db.application_by_job(conn, 101) is None and runner.filled == []
    # Our own CSRF token also works (the dashboard's own forms), unknown jobs are 404.
    tok = db.get_setting(conn, "csrf_token")
    st, _, _ = _req(srv, "POST", "/fill", {"job_id": "999", "csrf": tok})
    assert st == 404
    st, _, _ = _req(srv, "POST", "/fill", {"job_id": "102", "csrf": tok})
    assert st == 303 and runner.filled


def test_fill_from_pipeline_without_daemon_queues_only(cfg, conn, pipeline):
    srv = _serve(AppState_(cfg, profile=None, runner=None))
    try:
        st, body, _ = _req(srv, "POST", "/fill", {"job_id": "104"}, {"Origin": "http://127.0.0.1:8765"})
        assert st == 503 and "queued" in body.lower()
        assert db.application_by_job(conn, 104)["state"] == "queued"
    finally:
        srv.shutdown()


def _queued_app(cfg, conn, job_id=101):
    with intake.open_pipeline_db(cfg) as p:
        job = intake.pipeline_job(p, job_id)
    return intake.enqueue_job(conn, cfg, job, profile_resume_name="Resume.pdf")


def test_answer_memory_from_applied_application(cfg, conn, pipeline, monkeypatch):
    app = _queued_app(cfg, conn)
    questions = [
        {"id": "q1", "label": "Why do you want to work at Acme?", "type": "textarea", "required": True},
        {"id": "q2", "label": "Are you authorized to work in the US and will you need sponsorship?", "type": "select",
         "required": True, "options": ["Yes", "No"]},
        {"id": "first_name", "label": "First name", "type": "text", "required": True},
        {"id": "q4", "label": "Which languages are you fluent in?", "type": "multiselect", "options": ["Python", "C++"]},
        {"id": "q5", "label": "Have you previously interviewed with Acme?", "type": "boolean"},
        {"id": "q6", "label": "Anything else you would like to tell us?", "type": "textarea"},
        {"id": "resume", "label": "Resume upload here please", "type": "file"},
    ]
    answers = {"answers": {
        "q1": {"question_id": "q1", "value": "Because of the crawler work.", "source": "claude"},
        "q2": {"question_id": "q2", "value": "No", "source": "claude"},                  # knock-out: never remembered
        "first_name": {"question_id": "first_name", "value": "Alex", "source": "profile"},   # profile: not remembered
        "q4": {"question_id": "q4", "value": ["Python", "C++"], "source": "claude"},
        "q5": {"question_id": "q5", "value": False, "source": "claude"},
        "q6": {"question_id": "q6", "value": None, "source": "claude", "needs_human": "leave it"},
        "resume": {"question_id": "resume", "value": "__resume__", "source": "profile"},
    }}
    app.questions_path.write_text(json.dumps(questions), encoding="utf-8")
    app.answers_path.write_text(json.dumps(answers), encoding="utf-8")
    monkeypatch.setattr(applied, "mark_applied_in_pipeline", lambda cfg, job_id: True)
    for st in (AppState.PREFETCHED, AppState.DRAFTED, AppState.FILLING, AppState.READY_FOR_REVIEW):
        tracker.transition(conn, app.id, st, "t")
    assert applied.mark_applied(conn, cfg, app.id, source="human") is True
    rows = {r["question"]: r for r in db.recall_answers(conn)}
    assert set(rows) == {"why do you want to work at acme?", "which languages are you fluent in?",
                         "have you previously interviewed with acme?"}
    assert rows["why do you want to work at acme?"]["kind"] == "confirmed"
    mm = memory.memory_map(conn)
    assert mm["which languages are you fluent in?"] == ["Python", "C++"]
    assert mm["have you previously interviewed with acme?"] is False
    assert mm["why do you want to work at acme?"] == "Because of the crawler work."
    ev = [x for x in db.events_for_app(conn, app.id) if x["event"] == "applied"]
    assert ev and ev[-1]["detail"]["remembered_answers"] == 3
    # Marking applied again stores nothing new and does not duplicate.
    applied.mark_applied(conn, cfg, app.id, source="cli")
    assert len(db.recall_answers(conn)) == 3


def test_memory_feeds_the_next_draft(cfg, conn, pipeline):
    from smartapply.answers import deterministic_answers
    from smartapply.models import Question
    from smartapply.profile import seed_from_assets
    db.remember_answer(conn, "Why do you want to work at Acme?", "Because of the crawler work.")
    db.remember_answer(conn, "Which languages are you fluent in?", memory.encode(["Python", "C++"]))
    qs = [Question.from_dict({"id": "a", "label": "Why do you want to work at Acme?", "type": "textarea", "required": True}),
          Question.from_dict({"id": "b", "label": "Which languages are you fluent in?", "type": "multiselect",
                              "options": ["C++", "Python", "Go"]})]
    prof = seed_from_assets(pipeline.parent.parent / "playbooks" / "ASSETS.md")
    out = deterministic_answers(qs, prof, memory.memory_map(conn))
    assert out["a"].source == "memory" and out["a"].value == "Because of the crawler work."
    assert out["b"].source == "memory" and sorted(out["b"].value) == ["C++", "Python"]


def test_memory_page_and_forget(server, conn):
    srv, _ = server
    db.remember_answer(conn, "Why us?", "Because.")
    mid = db.recall_answers(conn)[0]["id"]
    st, body, _ = _req(srv, "GET", "/memory")
    assert st == 200 and "why us?" in body and "Because." in body
    tok = db.get_setting(conn, "csrf_token")
    st, _, _ = _req(srv, "POST", f"/memory/{mid}/forget", {"csrf": "wrong"})
    assert st == 403 and db.recall_answers(conn)
    st, _, loc = _req(srv, "POST", f"/memory/{mid}/forget", {"csrf": tok})
    assert st == 303 and loc == "/memory" and not db.recall_answers(conn)


def test_costs_page_breakdown(server, conn, cfg):
    srv, _ = server
    app = _queued_app(cfg, conn, 102)
    db.record_cost(conn, app.id, purpose="answers", model="claude-opus-5-5", cost_usd=0.4, num_turns=3, session_id=None,
                   subtype="success", duration_s=60)
    db.record_cost(conn, app.id, purpose="agent", model="claude-sonnet-5", cost_usd=1.1, num_turns=100, session_id=None,
                   subtype="success", duration_s=1200)
    totals = db.cost_totals(conn)
    assert totals["total_usd"] == pytest.approx(1.5) and totals["runs"] == 2
    assert [(r["purpose"], r["n"]) for r in totals["per_purpose"]] == [("agent", 1), ("answers", 1)]
    assert totals["per_app"][0]["company"] == "Acme WD" and totals["per_app"][0]["turns"] == 103
    st, body, _ = _req(srv, "GET", "/costs")
    assert st == 200 and "By purpose" in body and "claude-sonnet-5" in body and "Acme WD" in body
