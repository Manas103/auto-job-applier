"""Headless claude runner against the fake claude executable."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from pipeline.stages import claude_runner as cr


def _script(tmp_path, calls):
    p = tmp_path / "script.json"
    p.write_text(json.dumps({"calls": calls}), encoding="utf-8")
    return p


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    logp = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(logp))
    return logp


def _calls(logp: Path) -> list[dict]:
    if not logp.exists():
        return []
    return [json.loads(l) for l in logp.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_build_cmd(cfg):
    cmd = cr.build_cmd(cfg, prompt="hi", model=None, max_budget_usd=5, max_turns=10, resume_session=None, add_dirs=[Path("x")])
    assert cmd[:2] == list(cfg.claude.cmd) and cmd[2:4] == ["-p", "hi"]
    assert "--output-format" in cmd and cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--dangerously-skip-permissions" in cmd and cmd[cmd.index("--model") + 1] == "claude-opus-5"
    assert cmd[cmd.index("--max-budget-usd") + 1] == "5" and "--max-turns" not in cmd and "--resume" not in cmd
    assert cmd[cmd.index("--add-dir") + 1] == "x"
    cmd2 = cr.build_cmd(cfg, prompt="hi", model="m", max_budget_usd=1, max_turns=3, resume_session="sid", use_max_turns=True)
    assert cmd2[cmd2.index("--max-turns") + 1] == "3" and cmd2[cmd2.index("--resume") + 1] == "sid" and cmd2[cmd2.index("--model") + 1] == "m"


def test_parse_stream_json_tolerates_garbage():
    lines = ["not json", '{"type":"system","session_id":"s1"}', "{broken", '{"type":"result","subtype":"success","total_cost_usd":1.5,"num_turns":3,"session_id":"s1"}',
             '{"type":"result","subtype":"success","total_cost_usd":0.5,"num_turns":1,"session_id":"s1"}']
    last, sid, all_ = cr.parse_stream_json(lines)
    assert sid == "s1" and last["total_cost_usd"] == 0.5 and len(all_) == 2
    assert cr.parse_stream_json([]) == (None, None, [])


def test_run_success_fields_and_env(cfg, tmp_path, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_script(tmp_path, [{"result": {"subtype": "success", "cost": 1.25, "turns": 7},
                                                                      "write": {"out.txt": "hello"}}])))
    beats = []
    pids = []
    res = cr.run_claude(cfg, prompt="do the thing ideal_candidate_full_abc.tex", cwd=tmp_path, log_path=tmp_path / "run.log",
                        max_budget_usd=3, timeout_s=60, env_extra={"PIPELINE_JOB_ID": "9"}, on_pid=pids.append,
                        heartbeat=lambda: beats.append(1))
    assert res.ok and res.subtype == "success" and res.cost_usd == 1.25 and res.num_turns == 7 and res.session_id == "fake-1"
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hello"
    assert pids and beats
    log = (tmp_path / "run.log").read_text(encoding="utf-8")
    assert '"type": "result"' in log and "exit=0" in log
    call = _calls(fake_env)[0]
    assert call["env"]["ARCHON_AGENT"] == "1" and call["env"]["PIPELINE_JOB_ID"] == "9"
    assert call["max_budget"] == "3" and call["resume"] is None


def test_run_error_subtypes_and_exit_code(cfg, tmp_path, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_script(tmp_path, [{"result": {"subtype": "error_max_budget_usd", "cost": 9}},
                                                                      {"exit_code": 1, "result": {"subtype": "success"}},
                                                                      {"garbage_stdout": True, "result": {"subtype": "success", "cost": 0.2}}])))
    r1 = cr.run_claude(cfg, prompt="a", cwd=tmp_path, log_path=tmp_path / "l.log", max_budget_usd=1, timeout_s=60)
    assert not r1.ok and r1.subtype == "error_max_budget_usd" and r1.cost_usd == 9
    r2 = cr.run_claude(cfg, prompt="b", cwd=tmp_path, log_path=tmp_path / "l.log", max_budget_usd=1, timeout_s=60)
    assert not r2.ok and r2.exit_code == 1
    r3 = cr.run_claude(cfg, prompt="c", cwd=tmp_path, log_path=tmp_path / "l.log", max_budget_usd=1, timeout_s=60)
    assert r3.ok and r3.cost_usd == 0.2


def test_run_timeout_kills_process(cfg, tmp_path, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_script(tmp_path, [{"sleep_s": 30, "result": {"subtype": "success"}}])))
    t0 = time.time()
    res = cr.run_claude(cfg, prompt="slow", cwd=tmp_path, log_path=tmp_path / "l.log", max_budget_usd=1, timeout_s=3)
    assert res.timed_out and not res.ok and time.time() - t0 < 25
    assert "TIMEOUT" in (tmp_path / "l.log").read_text(encoding="utf-8")


def test_resume_passes_session(cfg, tmp_path, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_script(tmp_path, [{"match": {"resume": "*"}, "result": {"subtype": "success"}}])))
    res = cr.run_claude(cfg, prompt="fix", cwd=tmp_path, log_path=tmp_path / "l.log", max_budget_usd=1, timeout_s=60, resume_session="abc")
    assert res.ok and res.session_id == "abc"
    assert _calls(fake_env)[0]["resume"] == "abc"


def test_long_prompt_goes_to_file(cfg, tmp_path, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_script(tmp_path, [{"result": {"subtype": "success"}}])))
    res = cr.run_claude(cfg, prompt="x" * 40000, cwd=tmp_path, log_path=tmp_path / "l.log", max_budget_usd=1, timeout_s=60)
    assert res.ok and (tmp_path / "PROMPT.md").exists()
    assert "PROMPT.md" in _calls(fake_env)[0]["prompt"]


def test_supports_flag_detection(cfg):
    # The fake claude's --help output has no flags at all; detection must return False and cache.
    assert cr.supports_flag(cfg, "--max-turns") is False
    assert cr._feature_cache["--max-turns"] is False


def test_creation_flags_priority(cfg, monkeypatch):
    import pipeline.stages.claude_runner as _cr
    monkeypatch.setattr(_cr.sys, "platform", "win32")
    BN = 0x00004000
    cfg.worker._data["below_normal_priority"] = True
    assert _cr.creation_flags(cfg) & BN, "below-normal priority should be set by default"
    cfg.worker._data["below_normal_priority"] = False
    assert not (_cr.creation_flags(cfg) & BN), "knob off must drop below-normal priority"
    monkeypatch.setattr(_cr.sys, "platform", "linux")
    assert _cr.creation_flags(cfg) == 0
