"""Run `claude -p` headlessly, stream its JSON output to a log, and return a structured result."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .. import usage
from ..config import Config

log = logging.getLogger("pipeline.claude")

MAX_PROMPT_CHARS = 30000
CONTINUE_PROMPT = ("The previous run of this stage was interrupted before it finished. Inspect the working folder, "
                   "keep everything that is already correct, finish the required artifacts, and stop.")

_feature_cache: dict[str, bool] = {}


@dataclass
class ClaudeResult:
    ok: bool
    subtype: str
    session_id: str | None
    cost_usd: float
    num_turns: int
    result_text: str
    exit_code: int
    timed_out: bool
    log_path: Path
    raw_last: dict | None = None
    stderr_tail: str = ""
    duration_s: float = 0.0
    all_results: list[dict] = field(default_factory=list)


def supports_flag(cfg: Config, flag: str) -> bool:
    """Feature-detect a CLI flag from `claude --help` (cached per flag)."""
    if flag in _feature_cache:
        return _feature_cache[flag]
    cmd = list(cfg.claude.cmd)
    try:
        proc = subprocess.run(cmd + ["--help"], capture_output=True, text=True, timeout=30,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0)
        ok = flag in (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.SubprocessError):
        ok = False
    _feature_cache[flag] = ok
    return ok


def guard_settings_path(cfg: Config) -> Path:
    """Write (refresh) the settings file that installs pipeline/guard.py as a PreToolUse hook on shell tools.
    Regenerated on every call so a moved Python or project root never leaves a stale path behind."""
    python = sys.executable.replace("pythonw.exe", "python.exe")
    guard = Path(__file__).resolve().parents[1] / "guard.py"
    data = {"hooks": {"PreToolUse": [{"matcher": "Bash|PowerShell",
                                       "hooks": [{"type": "command", "command": f'"{python}" "{guard}"', "timeout": 15}]}]}}
    path = cfg.data_dir / "claude-settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return path


def build_cmd(cfg: Config, *, prompt: str, model: str | None, max_budget_usd: float, max_turns: int | None,
              resume_session: str | None, add_dirs: Iterable[Path] = (), use_max_turns: bool = False,
              settings_path: Path | None = None, tools: Iterable[str] | None = None) -> list[str]:
    cmd = list(cfg.claude.cmd) + ["-p", prompt, "--output-format", "stream-json", "--verbose",
                                  "--dangerously-skip-permissions", "--model", model or str(cfg.claude.model),
                                  "--max-budget-usd", str(max_budget_usd)]
    if settings_path is not None:
        cmd += ["--settings", str(settings_path)]
    fb = cfg.claude.get("fallback_model")
    if fb:
        cmd += ["--fallback-model", str(fb)]
    if use_max_turns and max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if tools:
        # Only the tools the stage needs, and no MCP servers: every offered tool's schema is resent on every call,
        # and stray tools (scheduling, sub-agents, docs connectors) have no place in an unattended run.
        cmd += ["--tools", ",".join(tools), "--strict-mcp-config"]
    for d in add_dirs:
        cmd += ["--add-dir", str(d)]
    if resume_session:
        cmd += ["--resume", resume_session]
    cmd += [str(x) for x in (cfg.claude.get("extra_args") or [])]
    return cmd


def parse_stream_json(lines: Iterable[str]) -> tuple[dict | None, str | None, list[dict]]:
    """Return (last result object, session_id seen, all result objects). Tolerates garbage lines."""
    last: dict | None = None
    sid: str | None = None
    results: list[dict] = []
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
        if obj.get("type") == "result":
            last = obj
            results.append(obj)
    return last, sid, results


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            proc.kill()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        pass


def creation_flags(cfg: Config) -> int:
    """Windows process-creation flags for a stage subprocess: no console window, and (by default) BELOW_NORMAL
    priority so the whole build tree yields CPU to the user's foreground apps (Chrome) under contention.
    Priority is quality-neutral: on an otherwise idle machine a below-normal process still gets every core."""
    if sys.platform != "win32":
        return 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if bool(cfg.worker.get("below_normal_priority", True)):
        flags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)
    return flags


def run_claude(cfg: Config, *, prompt: str, cwd: Path, log_path: Path, max_budget_usd: float, timeout_s: int,
               max_turns: int | None = None, resume_session: str | None = None, model: str | None = None,
               add_dirs: Iterable[Path] = (), env_extra: dict | None = None,
               on_pid: Callable[[int], None] | None = None, heartbeat: Callable[[], None] | None = None,
               job_object=None, on_session: Callable[[str], None] | None = None,
               tools: Iterable[str] | None = None) -> ClaudeResult:
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt_file = Path(cwd) / "PROMPT.md"
        prompt_file.write_text(prompt, encoding="utf-8")
        prompt = f"Read the file {prompt_file} in full and follow it exactly as your instructions."
    use_mt = bool(max_turns) and supports_flag(cfg, "--max-turns")
    settings_path = None
    if bool(cfg.claude.get("guard_hook", True)) and supports_flag(cfg, "--settings"):
        settings_path = guard_settings_path(cfg)
    cmd = build_cmd(cfg, prompt=prompt, model=model, max_budget_usd=max_budget_usd, max_turns=max_turns,
                    resume_session=resume_session, add_dirs=add_dirs, use_max_turns=use_mt, settings_path=settings_path,
                    tools=tools)
    env = dict(os.environ)
    env.update({k: str(v) for k, v in (cfg.claude.get("env") or {}).items()})
    env.update({k: str(v) for k, v in (env_extra or {}).items()})
    env.setdefault("CI", "1")
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    lines: list[str] = []
    stderr_chunks: list[str] = []
    timed_out = False
    flags = creation_flags(cfg)
    with log_path.open("a", encoding="utf-8") as lf:
        lf.write(f"\n==== claude run {time.strftime('%Y-%m-%d %H:%M:%S')} cwd={cwd}\n==== cmd: {' '.join(cmd[:1])} -p <prompt {len(prompt)} chars> {' '.join(cmd[3:])}\n")
        lf.flush()
        try:
            proc = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=env,
                                    creationflags=flags)
        except OSError as e:
            lf.write(f"==== failed to start: {e}\n")
            return ClaudeResult(False, "spawn_error", None, 0.0, 0, str(e), -1, False, log_path, stderr_tail=str(e))
        if job_object is not None:
            try:
                job_object.assign(proc.pid)
            except Exception as e:  # noqa: BLE001
                log.debug("job object assign failed: %s", e)
        if on_pid:
            on_pid(proc.pid)

        def _drain_stderr() -> None:
            assert proc.stderr is not None
            for ln in proc.stderr:
                stderr_chunks.append(ln)
                if len(stderr_chunks) > 400:
                    del stderr_chunks[:200]

        t_err = threading.Thread(target=_drain_stderr, daemon=True)
        t_err.start()
        last_hb = 0.0
        announced_sid: str | None = None
        deadline = t0 + timeout_s
        assert proc.stdout is not None
        # Read stdout line by line with a timeout check between lines.
        reader_done = threading.Event()
        q: list[str] = []
        qlock = threading.Lock()

        def _drain_stdout() -> None:
            for ln in proc.stdout:  # type: ignore[union-attr]
                with qlock:
                    q.append(ln)
            reader_done.set()

        t_out = threading.Thread(target=_drain_stdout, daemon=True)
        t_out.start()
        while True:
            with qlock:
                batch, q[:] = q[:], []
            for ln in batch:
                lines.append(ln)
                lf.write(ln)
                if '"rate_limit_event"' in ln:
                    usage.note_line(cfg, ln, source=log_path.name)
                if on_session and announced_sid is None and '"session_id"' in ln:
                    try:
                        sid_now = json.loads(ln).get("session_id")
                    except (json.JSONDecodeError, AttributeError):
                        sid_now = None
                    if sid_now:
                        announced_sid = str(sid_now)
                        on_session(announced_sid)
            if batch:
                lf.flush()
                if heartbeat and time.time() - last_hb > 15:
                    heartbeat()
                    last_hb = time.time()
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
    last, sid, results = parse_stream_json(lines)
    subtype = (last or {}).get("subtype") or ("timeout" if timed_out else ("no_result" if exit_code == 0 else "error"))
    cost = float((last or {}).get("total_cost_usd") or 0.0)
    turns = int((last or {}).get("num_turns") or 0)
    text = str((last or {}).get("result") or "")
    ok = (not timed_out) and exit_code == 0 and last is not None and subtype == "success"
    return ClaudeResult(ok, subtype, sid or resume_session, cost, turns, text, exit_code, timed_out, log_path,
                        raw_last=last, stderr_tail="".join(stderr_chunks[-20:]), duration_s=time.time() - t0,
                        all_results=results)


def session_cost(results: Iterable[dict]) -> float:
    """True spend of a stage's runs. A resumed session (`--resume`, used by fix rounds) reports the session's running
    total, not the cost of that invocation, so summing every run's cost double-counts. Take each session's highest
    reported total and sum across sessions."""
    best: dict[str, float] = {}
    for i, r in enumerate(results):
        key = str(r.get("session_id") or f"_anon{i}")
        best[key] = max(best.get(key, 0.0), float(r.get("cost_usd") or 0.0))
    return round(sum(best.values()), 6)


def claude_available(cfg: Config) -> bool:
    exe = str(cfg.claude.cmd[0])
    return Path(exe).exists() or shutil.which(exe) is not None
