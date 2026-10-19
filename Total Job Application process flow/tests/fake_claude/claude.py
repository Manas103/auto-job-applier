#!/usr/bin/env python3
"""Fake `claude` executable for offline tests.

Behavior is scripted by the JSON file named in FAKE_CLAUDE_SCRIPT:
{
  "calls": [
    {"match": {"resume": null},          # null = first call (no --resume); "*" = any resumed call; "<sid>" = exact
     "artifacts": "<dir to copy into cwd>",   # optional; files named *_t.* are renamed to the slug in the prompt
     "omit": ["no_brainer_hire_t.md"],        # optional; skip these fixture files
     "write": {"build_report.json": {...}, "review.json": {...}, "note.txt": "text"},   # optional extra files
     "result": {"subtype": "success", "cost": 1.25, "turns": 12},
     "sleep_s": 0, "exit_code": 0, "garbage_stdout": false, "no_result_line": false}
  ]
}
Calls are matched in order; the first unused entry whose `match` fits is consumed. If none fits, the last entry is reused.
Every invocation appends {argv, cwd, prompt, resume, env} to FAKE_CLAUDE_LOG (jsonl)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path


def parse_argv(argv: list[str]) -> dict:
    out = {"prompt": None, "resume": None, "model": None, "max_budget": None, "max_turns": None, "output_format": None, "add_dirs": []}
    i = 0
    while i < len(argv):
        a = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if a == "-p":
            out["prompt"] = nxt
            i += 2
        elif a == "--resume":
            out["resume"] = nxt
            i += 2
        elif a == "--model":
            out["model"] = nxt
            i += 2
        elif a == "--max-budget-usd":
            out["max_budget"] = nxt
            i += 2
        elif a == "--max-turns":
            out["max_turns"] = nxt
            i += 2
        elif a == "--output-format":
            out["output_format"] = nxt
            i += 2
        elif a == "--add-dir":
            out["add_dirs"].append(nxt)
            i += 2
        else:
            i += 1
    return out


def slug_from_prompt(prompt: str | None, cwd: Path | None = None) -> str | None:
    if prompt:
        m = re.search(r"ideal_candidate_full_([a-z0-9]+)\.tex", prompt) or re.search(r"Slug: ([a-z0-9]+)", prompt)
        if m:
            return m.group(1)
    if cwd is not None:
        for p in cwd.glob("ideal_candidate_full_*.tex"):
            m = re.match(r"ideal_candidate_full_([a-z0-9]+)\.tex$", p.name)
            if m:
                return m.group(1)
        jj = cwd / "job.json"
        if jj.exists():
            try:
                return json.loads(jj.read_text(encoding="utf-8")).get("slug")
            except Exception:  # noqa: BLE001
                return None
    return None


def main() -> int:
    if "--help" in sys.argv[1:]:
        sys.stdout.write("Usage: claude [options] [command] [prompt]\n\nOptions:\n  -p, --print\n  --output-format <format>\n")
        return 0
    parsed = parse_argv(sys.argv[1:])
    cwd = Path.cwd()
    script_path = os.environ.get("FAKE_CLAUDE_SCRIPT")
    log_path = os.environ.get("FAKE_CLAUDE_LOG")
    script = {"calls": [{"result": {"subtype": "success", "cost": 0.1, "turns": 1}}]}
    if script_path and Path(script_path).exists():
        script = json.loads(Path(script_path).read_text(encoding="utf-8"))
    calls = script.get("calls") or []
    state_path = Path(script_path + ".state") if script_path else None
    used = json.loads(state_path.read_text(encoding="utf-8")) if state_path and state_path.exists() else []
    chosen_idx = None
    for idx, c in enumerate(calls):
        if idx in used:
            continue
        m = c.get("match", {})
        want = m.get("resume", "any")
        if want == "any" or (want is None and parsed["resume"] is None) or (want == "*" and parsed["resume"]) or (want == parsed["resume"]):
            chosen_idx = idx
            break
    if chosen_idx is None:
        chosen_idx = len(calls) - 1 if calls else None
    call = calls[chosen_idx] if chosen_idx is not None else {"result": {"subtype": "success"}}
    if state_path and chosen_idx is not None and not call.get("reusable"):
        used.append(chosen_idx)
        state_path.write_text(json.dumps(used), encoding="utf-8")

    call_no = len(used)
    session_id = parsed["resume"] or f"fake-{call_no}"
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"argv": sys.argv[1:], "cwd": str(cwd), "prompt": parsed["prompt"], "resume": parsed["resume"],
                                "model": parsed["model"], "max_budget": parsed["max_budget"], "max_turns": parsed["max_turns"],
                                "env": {k: os.environ.get(k) for k in ("ARCHON_AGENT", "PIPELINE_JOB_ID", "PIPELINE_STAGE")},
                                "session_id": session_id, "call_index": chosen_idx}) + "\n")

    def emit(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    emit({"type": "system", "subtype": "init", "session_id": session_id, "model": parsed["model"] or "fake"})
    if call.get("garbage_stdout"):
        sys.stdout.write("this is not json\n{broken\n")
        sys.stdout.flush()
    if call.get("sleep_s"):
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "working..."}]}, "session_id": session_id})
        time.sleep(float(call["sleep_s"]))

    slug = slug_from_prompt(parsed["prompt"], cwd)
    art = call.get("artifacts")
    if art:
        src = Path(art)
        omit = set(call.get("omit") or [])
        for p in sorted(src.iterdir()):
            if not p.is_file() or p.name in omit:
                continue
            name = p.name
            if slug and "_t" in name:
                name = re.sub(r"_t(?=[._])", f"_{slug}", name)
            shutil.copy(p, cwd / name)
    for name, content in (call.get("write") or {}).items():
        target = cwd / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, (dict, list)):
            target.write_text(json.dumps(content, indent=2), encoding="utf-8")
        else:
            target.write_text(str(content), encoding="utf-8")

    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}, "session_id": session_id})
    res = call.get("result") or {}
    subtype = res.get("subtype", "success")
    if not call.get("no_result_line"):
        emit({"type": "result", "subtype": subtype, "is_error": subtype != "success", "session_id": session_id,
              "total_cost_usd": float(res.get("cost", 0.1)), "num_turns": int(res.get("turns", 1)),
              "duration_ms": 10, "result": res.get("text", 'PIPELINE_RESULT: {"status": "ok", "notes": "fake"}')})
    return int(call.get("exit_code", 0))


if __name__ == "__main__":
    sys.exit(main())
