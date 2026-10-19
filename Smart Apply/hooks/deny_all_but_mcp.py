"""PreToolUse hook for the fallback browser agent: deny every tool except the Smart Apply MCP server's tools and
StructuredOutput. A second fence behind `--tools ""` / `--allowedTools mcp__smartapply` / `--disallowedTools`, so a
Claude Code upgrade that changes a default cannot hand the agent a shell, the file system or the web.

Reads the hook JSON on stdin, writes the decision on stdout. Exit code 0 always (a hook crash must not become an
allow), so the decision is carried by the JSON."""

from __future__ import annotations

import json
import sys

ALLOWED_PREFIX = "mcp__smartapply__"
ALLOWED_EXACT = {"StructuredOutput"}


def decide(tool_name: str) -> dict:
    if tool_name in ALLOWED_EXACT or tool_name.startswith(ALLOWED_PREFIX):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                       "permissionDecisionReason": "Smart Apply browser tool"}}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": f"Smart Apply agent may only use its browser tools, not {tool_name}"}}


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        data = {}
    name = str(data.get("tool_name") or "")
    sys.stdout.write(json.dumps(decide(name)))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
