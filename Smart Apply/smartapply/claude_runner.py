"""Run `claude -p` headlessly (Max plan login), stream its JSON to a log, return a structured result.

Adapted from the pipeline's stages/claude_runner.py. Differences: the prompt goes in on stdin, tools are
restricted per run, `--json-schema` gives a validated structured_output, and MCP servers can be attached with
`--strict-mcp-config` so nothing from ~/.claude leaks into the run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import usage
from .config import Config
from .log import get_logger
from .util import no_window_flags

log = get_logger("claude")

BUILTIN_TOOLS = ["Bash", "PowerShell", "Edit", "Write", "Read", "Glob", "Grep", "WebFetch", "WebSearch", "Agent",
                 "Task", "TodoWrite", "NotebookEdit", "MultiEdit", "LS", "KillShell", "BashOutput"]


@dataclass
class ClaudeResult:
    ok: bool
    subtype: str
    session_id: str | None
    cost_usd: float
    num_turns: int
    result_text: str
    structured_output: Any
    exit_code: int
    timed_out: bool
    log_path: Path
    duration_s: float = 0.0
    stderr_tail: str = ""
    raw_last: dict | None = None
    init: dict | None = None
    permission_denials: list = field(default_factory=list)


def build_cmd(cfg: Config, *, model: str, max_turns: int | None, max_budget_usd: float | None,
              json_schema: dict | None, allowed_tools: list[str] | None, disallowed_tools: list[str] | None,
              tools: list[str] | None, mcp_config: Path | None, strict_mcp: bool, permission_mode: str | None,
              add_dirs: Iterable[Path], setting_sources: str | None, resume_session: str | None,
              skip_permissions: bool, settings: dict | None = None) -> list[str]:
    cmd = list(cfg.claude.cmd) + ["-p", "--output-format", "stream-json", "--verbose", "--model", model]
    if settings:
        # inline settings (hooks for the browser agent); they apply even with --setting-sources ""
        cmd += ["--settings", json.dumps(settings, separators=(",", ":"))]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    if permission_mode:
        cmd += ["--permission-mode", permission_mode]
    if max_budget_usd is not None:
        cmd += ["--max-budget-usd", str(max_budget_usd)]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if json_schema is not None:
        cmd += ["--json-schema", json.dumps(json_schema, separators=(",", ":"))]
    if tools is not None:
        cmd += ["--tools", ",".join(tools) if tools else ""]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    if disallowed_tools:
        cmd += ["--disallowedTools", ",".join(disallowed_tools)]
    if mcp_config is not None:
        cmd += ["--mcp-config", str(mcp_config)]
        if strict_mcp:
            cmd.append("--strict-mcp-config")
    if setting_sources is not None:
        cmd += ["--setting-sources", setting_sources]
    for d in add_dirs:
        cmd += ["--add-dir", str(d)]
    if resume_session:
        cmd += ["--resume", resume_session]
    return cmd


def parse_stream_json(lines: Iterable[str]) -> tuple[dict | None, str | None, dict | None]:
    """(last result object, session_id, system/init object)."""
    last: dict | None = None
    sid: str | None = None
    init: dict | None = None
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("session_id"):
            sid = obj["session_id"]
        if obj.get("type") == "system" and obj.get("subtype") == "init":
            init = obj
        if obj.get("type") == "result":
            last = obj
    return last, sid, init


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30,
                           creationflags=no_window_flags())
        else:
            proc.kill()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        pass


def run_claude(cfg: Config, *, prompt: str, cwd: Path, log_path: Path, model: str, timeout_s: int,
               max_turns: int | None = None, max_budget_usd: float | None = None, json_schema: dict | None = None,
               allowed_tools: list[str] | None = None, disallowed_tools: list[str] | None = None,
               tools: list[str] | None = None, mcp_config: Path | None = None, strict_mcp: bool = True,
               permission_mode: str | None = None, add_dirs: Iterable[Path] = (), setting_sources: str | None = None,
               resume_session: str | None = None, env_extra: dict | None = None,
               skip_permissions: bool = False, settings: dict | None = None) -> ClaudeResult:
    cmd = build_cmd(cfg, model=model, max_turns=max_turns, max_budget_usd=max_budget_usd, json_schema=json_schema,
                    allowed_tools=allowed_tools, disallowed_tools=disallowed_tools, tools=tools, mcp_config=mcp_config,
                    strict_mcp=strict_mcp, permission_mode=permission_mode, add_dirs=add_dirs,
                    setting_sources=setting_sources, resume_session=resume_session, skip_permissions=skip_permissions,
                    settings=settings)
    blocked = usage.brake_reason(cfg)
    if blocked:
        log.info("claude run skipped: %s", blocked)
        return ClaudeResult(False, "usage_brake", resume_session, 0.0, 0, blocked, None, -1, False, Path(log_path),
                            stderr_tail=blocked)
    env = dict(os.environ)
    env_cfg = cfg.claude.get("env") or {}
    if hasattr(env_cfg, "to_dict"):
        env_cfg = env_cfg.to_dict()
    env.update({k: str(v) for k, v in env_cfg.items()})
    env.update({k: str(v) for k, v in (env_extra or {}).items()})
    env.setdefault("CI", "1")
    env.setdefault("MCP_TIMEOUT", "60000")
    Path(cwd).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    lines: list[str] = []
    stderr_chunks: list[str] = []
    timed_out = False
    with log_path.open("a", encoding="utf-8") as lf:
        shown = [c if not c.startswith("{") else "<json>" for c in cmd[1:]]
        lf.write(f"\n==== claude run {time.strftime('%Y-%m-%d %H:%M:%S')} cwd={cwd}\n==== cmd: {cmd[0]} {' '.join(shown)}\n"
                 f"==== prompt: {len(prompt)} chars\n")
        lf.flush()
        try:
            proc = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=env,
                                    creationflags=no_window_flags())
        except OSError as e:
            lf.write(f"==== failed to start: {e}\n")
            return ClaudeResult(False, "spawn_error", None, 0.0, 0, str(e), None, -1, False, log_path, stderr_tail=str(e))

        def _feed() -> None:
            try:
                assert proc.stdin is not None
                proc.stdin.write(prompt)
                proc.stdin.close()
            except (OSError, ValueError):
                pass

        def _drain_stderr() -> None:
            assert proc.stderr is not None
            for ln in proc.stderr:
                stderr_chunks.append(ln)
                if len(stderr_chunks) > 400:
                    del stderr_chunks[:200]

        threading.Thread(target=_feed, daemon=True).start()
        t_err = threading.Thread(target=_drain_stderr, daemon=True)
        t_err.start()
        q: list[str] = []
        qlock = threading.Lock()
        reader_done = threading.Event()

        def _drain_stdout() -> None:
            for ln in proc.stdout:  # type: ignore[union-attr]
                with qlock:
                    q.append(ln)
            reader_done.set()

        threading.Thread(target=_drain_stdout, daemon=True).start()
        deadline = t0 + timeout_s
        while True:
            with qlock:
                batch, q[:] = q[:], []
            for ln in batch:
                lines.append(ln)
                lf.write(ln)
                if '"rate_limit_event"' in ln:
                    usage.note_line(cfg, ln)
            if batch:
                lf.flush()
            if reader_done.is_set() and not q:
                break
            if time.time() > deadline:
                timed_out = True
                lf.write("==== TIMEOUT: killing process tree\n")
                _kill_tree(proc)
                break
            time.sleep(0.2)
        try:
            exit_code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            exit_code = proc.returncode if proc.returncode is not None else -9
        t_err.join(timeout=5)
        if stderr_chunks:
            lf.write("==== stderr:\n" + "".join(stderr_chunks[-50:]))
        lf.write(f"==== exit={exit_code} timed_out={timed_out} elapsed={time.time() - t0:.0f}s\n")
    last, sid, init = parse_stream_json(lines)
    subtype = (last or {}).get("subtype") or ("timeout" if timed_out else ("no_result" if exit_code == 0 else "error"))
    cost = float((last or {}).get("total_cost_usd") or 0.0)
    turns = int((last or {}).get("num_turns") or 0)
    text = str((last or {}).get("result") or "")
    structured = (last or {}).get("structured_output")
    ok = (not timed_out) and exit_code == 0 and last is not None and subtype == "success" and not (last or {}).get("is_error")
    if ok and json_schema is not None and structured is None:
        ok = False
        subtype = "no_structured_output"
    return ClaudeResult(ok, subtype, sid or resume_session, cost, turns, text, structured, exit_code, timed_out,
                        log_path, duration_s=time.time() - t0, stderr_tail="".join(stderr_chunks[-20:]),
                        raw_last=last, init=init, permission_denials=list((last or {}).get("permission_denials") or []))


def claude_available(cfg: Config) -> bool:
    exe = str(cfg.claude.cmd[0])
    return Path(exe).exists() or shutil.which(exe) is not None


def claude_version(cfg: Config) -> str:
    try:
        p = subprocess.run(list(cfg.claude.cmd) + ["--version"], capture_output=True, text=True, timeout=30,
                           creationflags=no_window_flags())
        return (p.stdout or p.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as e:
        return f"error: {e}"
