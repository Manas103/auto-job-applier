"""The PreToolUse guard that keeps headless stages away from processes and browsers they do not own."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline import guard
from pipeline.stages import claude_runner as cr

GUARD = Path(guard.__file__)

BLOCKED = [
    "taskkill //F //IM chrome.exe 2>&1",
    "taskkill /F /IM node.exe",
    "taskkill -f -im java.exe",
    "wc -l /tmp/x.csv; cat /tmp/x.csv | taskkill //F //IM node.exe 2>&1 | taskkill //F //IM java.exe 2>&1 | taskkill //F //IM chrome.exe 2>&1",
    "pkill -f dist/server.js",
    "killall node",
    "for pid in 40212 44232 35864; do taskkill //F //PID $pid 2>/dev/null; done",
    "for pid in $(netstat -ano | grep ':4000' | grep LISTENING | awk '{print $5}'); do taskkill //F //PID $pid; done",
    "netstat -ano | grep :4000; taskkill //F //PID 1234",
    "taskkill //F //PID $(cat pid.txt)",
    "Get-Process node | Stop-Process -Force",
    "Stop-Process -Name chrome -Force",
    "wmic process where \"name='node.exe'\" delete",
    "kill -9 -1",
    "where chromedriver; where chrome",
    "tasklist //FI \"IMAGENAME eq chrome.exe\"",
    "ls \"C:\\Program Files\\Google\\Chrome\\Application\"",
    "start chrome http://localhost:4000",
    "mvn test -Dwebdriver.chrome.driver=C:/tools/chromedriver.exe",
    "shutdown /r /t 0",
    "kill $(pgrep node) && echo done",
]

ALLOWED = [
    "taskkill //F //PID 59892 2>&1; taskkill //F //PID 15924 2>&1",
    "taskkill /F /T /PID 4242",
    "taskkill //F //PID $BPID 2>/dev/null",
    "node dist/server.js > /tmp/backend.log 2>&1 & echo $! > /tmp/backend.pid",
    "kill $BPID",
    "kill %1; wait",
    "python -m playwright install chromium",
    "python -m pytest tests -q",
    "grep -rn 'kill switch' src/",
    "git commit -m 'Add kill switch for the ingest loop'",
    "wsl.exe -d Ubuntu-22.04 -- bash -lc 'cd ~/build/x && make -j8 && ./bench'",
    "cat /tmp/flake_results.csv | head",
    "echo 'starting server on a free port'; PORT=0 node server.js",
]


@pytest.mark.parametrize("cmd", BLOCKED)
def test_blocked(cmd):
    assert guard.check_command(cmd) is not None, cmd


@pytest.mark.parametrize("cmd", ALLOWED)
def test_allowed(cmd):
    assert guard.check_command(cmd) is None, cmd


def _run_hook(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload), capture_output=True, text=True, timeout=30)


def test_hook_protocol_blocks_with_exit_2():
    r = _run_hook({"tool_name": "Bash", "tool_input": {"command": "taskkill //F //IM chrome.exe"}})
    assert r.returncode == 2 and "BLOCKED" in r.stderr and "browser" in r.stderr
    r = _run_hook({"tool_name": "PowerShell", "tool_input": {"command": "Stop-Process -Name chrome"}})
    assert r.returncode == 2


def test_hook_protocol_allows_and_ignores_other_tools():
    assert _run_hook({"tool_name": "Bash", "tool_input": {"command": "python -m pytest -q"}}).returncode == 0
    assert _run_hook({"tool_name": "Read", "tool_input": {"file_path": "x"}}).returncode == 0
    assert _run_hook({}).returncode == 0
    r = subprocess.run([sys.executable, str(GUARD)], input="not json", capture_output=True, text=True, timeout=30)
    assert r.returncode == 0


def test_guard_settings_file_and_flag(cfg, monkeypatch):
    p = cr.guard_settings_path(cfg)
    data = json.loads(p.read_text(encoding="utf-8"))
    hooks = data["hooks"]["PreToolUse"]
    assert hooks[0]["matcher"] == "Bash|PowerShell"
    command = hooks[0]["hooks"][0]["command"]
    assert "guard.py" in command and "pythonw" not in command
    cmd = cr.build_cmd(cfg, prompt="hi", model=None, max_budget_usd=1, max_turns=None, resume_session=None, settings_path=p)
    assert cmd[cmd.index("--settings") + 1] == str(p)
    assert "--settings" not in cr.build_cmd(cfg, prompt="hi", model=None, max_budget_usd=1, max_turns=None, resume_session=None)
    # run_claude attaches the settings file whenever the CLI advertises --settings and the knob is on.
    monkeypatch.setattr(cr, "supports_flag", lambda c, flag: flag == "--settings")
    seen = {}
    monkeypatch.setattr(cr, "build_cmd", lambda c, **kw: seen.update(kw) or [sys.executable, "-c", "print('{}')"])
    cr.run_claude(cfg, prompt="x", cwd=cfg.data_dir, log_path=cfg.data_dir / "g.log", max_budget_usd=1, timeout_s=30)
    assert seen["settings_path"] == p
    cfg.claude.guard_hook = False
    seen.clear()
    cr.run_claude(cfg, prompt="x", cwd=cfg.data_dir, log_path=cfg.data_dir / "g.log", max_budget_usd=1, timeout_s=30)
    assert seen["settings_path"] is None
