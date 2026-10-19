"""Fake `claude` for the browser-agent tests. Behaves like `claude -p --output-format stream-json` with one MCP
server: it reads the prompt from stdin, opens the MCP server named in --mcp-config, snapshots the page, fills the
first-name field, tries the submit button (expects a refusal), records a report and prints a stream-json init plus a
result object with structured_output. Scripted through FAKE_AGENT_MODE: "fill" (default), "max_turns" (no result
object beyond an error subtype), "blocked" (structured output says captcha)."""

from __future__ import annotations

import asyncio
import json
import os
import sys


def _arg(argv: list[str], name: str) -> str | None:
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
    return None


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


async def drive(server: dict, mode: str) -> tuple[list[str], dict]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env.update(server.get("env") or {})
    params = StdioServerParameters(command=server["command"], args=server.get("args") or [], env=env)
    filled: list[dict] = []
    refused = False
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = sorted("mcp__smartapply__" + t.name for t in (await s.list_tools()).tools)

            def text(res) -> str:
                return "\n".join(c.text for c in res.content if getattr(c, "text", None))

            await s.call_tool("page_info", {})
            snap = text(await s.call_tool("snapshot", {}))
            if "Apply now" in snap and "First name" not in snap:
                await s.call_tool("open_application_form", {})
                snap = text(await s.call_tool("snapshot", {}))
            refs: dict[str, str] = {}
            for line in snap.splitlines():
                parts = line.split(" ", 2)
                if len(parts) >= 3 and parts[0].startswith("e") and '"' in parts[2]:
                    refs.setdefault(parts[2].split('"')[1], parts[0])
            if mode == "blocked":
                await s.call_tool("report", {"status": "blocked", "reason": "captcha", "left_for_human": [], "notes": ["captcha wall"]})
                return names, {"status": "blocked", "reason": "captcha", "stopped_at": "captcha", "filled": [], "left_for_human": [], "notes": []}
            fn = refs.get("First name *")
            if fn:
                out = text(await s.call_tool("fill", {"ref": fn, "value": "Alex"}))
                filled.append({"label": "First name", "value": "Alex" if "observed" in out else out})
            em = refs.get("Email *")
            if em:
                await s.call_tool("fill", {"ref": em, "value": "alex@example.com"})
            for label, ref in refs.items():
                if label.startswith("Submit"):
                    refused = "REFUSED" in text(await s.call_tool("click", {"ref": ref}))
            left = [{"label": "Are you legally authorized to work in the United States?", "why": "knock-out, left for Alex"}]
            await s.call_tool("report", {"status": "needs_human", "reason": "unanswered_required", "left_for_human": left, "notes": []})
            if mode == "max_turns":
                return names, {}
            return names, {"status": "needs_human", "reason": "unanswered_required", "stopped_at": "step 1",
                           "filled": filled, "left_for_human": left, "notes": [f"submit refused: {refused}"]}


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv:
        print("0.0.0 (fake)")
        return 0
    prompt = sys.stdin.read()
    log = os.environ.get("FAKE_CLAUDE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"argv": argv, "prompt_len": len(prompt), "cwd": os.getcwd()}) + "\n")
    mode = os.environ.get("FAKE_AGENT_MODE", "fill")
    cfg_path = _arg(argv, "--mcp-config")
    settings = _arg(argv, "--settings")
    server = json.loads(open(cfg_path, encoding="utf-8").read())["mcpServers"]["smartapply"] if cfg_path else None
    tools: list[str] = []
    structured: dict = {}
    if server is not None:
        tools, structured = asyncio.run(drive(server, mode))
    if "--json-schema" in argv:
        tools = ["StructuredOutput"] + tools
    if settings and "hooks" not in settings:
        tools.append("Bash")   # would be a drift; the test asserts it is not reported
    _emit({"type": "system", "subtype": "init", "session_id": "fake-session", "tools": tools, "model": _arg(argv, "--model")})
    if mode == "max_turns":
        _emit({"type": "result", "subtype": "error_max_turns", "is_error": True, "session_id": "fake-session",
               "total_cost_usd": 0.05, "num_turns": int(_arg(argv, "--max-turns") or 0), "result": "", "permission_denials": []})
        return 0
    _emit({"type": "result", "subtype": "success", "is_error": False, "session_id": "fake-session", "total_cost_usd": 0.02,
           "num_turns": 5, "result": "done", "structured_output": structured, "permission_denials": []})
    return 0


if __name__ == "__main__":
    sys.exit(main())
