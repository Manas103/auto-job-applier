"""PreToolUse guard for headless stage runs: blocks shell commands that reach outside the pipeline's own processes.

Installed into every `claude -p` run through a generated settings file (see stages/claude_runner.py). Claude Code
pipes the tool call as JSON on stdin; exit code 2 blocks the call and returns stderr to the model as the reason.

Self-contained on purpose: the hook runs with an arbitrary cwd and must not import the pipeline package.

Why this exists: on 2026-08-25 a builder stage ran `taskkill /F /IM chrome.exe` (and node.exe, java.exe, plus PID
lists pulled from netstat) dozens of times to clean up a Selenium suite, closing Alex's Chrome each time.
"""

from __future__ import annotations

import json
import os
import re
import sys

_I = re.I | re.S

# (pattern, reason). The first match blocks.
RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\btaskkill\b[^\n|;&]*?(?<![\w.])[/-]{1,2}im\b", _I),
     "killing processes by image name (taskkill /IM) is forbidden: it kills processes you did not start, including the user's browser"),
    (re.compile(r"\b(?:pkill|killall)\b", _I),
     "pkill/killall are forbidden: they kill by name across the whole machine; kill only PIDs you started, by PID"),
    (re.compile(r"\bstop-process\b[^\n|;&]*?\s-name\b|\bget-process\b[^\n]*?\|[^\n|]*?\bstop-process\b", _I),
     "Stop-Process by name or over Get-Process output is forbidden; stop only PIDs you started, by -Id"),
    (re.compile(r"\bwmic\b[^\n]*?\bprocess\b[^\n]*?\b(?:delete|terminate)\b", _I),
     "wmic process delete/terminate is forbidden"),
    (re.compile(r"\bkill\b(?:\s+-\w+)*\s+-1\b", _I),
     "kill -1 (every process) is forbidden"),
    (re.compile(r"\bkill\b[^\n]*?(?:\$\(|`)[^\n]*?\b(?:pgrep|pidof|lsof|ps|netstat|tasklist)\b|\b(?:pgrep|pidof|lsof)\b[^\n]*?\|\s*(?:xargs\s+)?kill\b", _I),
     "kill fed from pgrep/pidof/lsof/ps is forbidden: it targets processes by name; kill only PIDs you started and hold in a variable"),
    (re.compile(r"(?:taskkill|\bkill\b|stop-process)[^\n]*?\b(?:chrome|msedge|firefox|brave|opera|explorer\.exe|code\.exe|claude)\b|"
                r"\b(?:chrome|msedge|firefox|brave|opera|explorer\.exe|code\.exe|claude)\b[^\n]*?(?:taskkill|\bkill\b|stop-process)", _I),
     "killing the user's browser, shell, editor, or Claude sessions is forbidden"),
    (re.compile(r"\b(?:chromedriver|msedgedriver|geckodriver|selenium-manager)\b", _I),
     "real-browser drivers are forbidden: browser tests run on Playwright's bundled Chromium, headless, launched and closed by the test process itself"),
    (re.compile(r"\b(?:chrome|msedge|firefox)\.exe\b|Google\\+Chrome|\bstart\s+(?:\"[^\"]*\"\s+)?(?:chrome|msedge|firefox|brave)\b", _I),
     "the user's browsers are off limits (no launching, listing, or locating them); use Playwright's bundled Chromium in headless mode"),
    (re.compile(r"(?:^|[\s;&|])(?:shutdown|restart-computer|logoff)\b|rundll32\s+user32\.dll,\s*LockWorkStation", _I),
     "power and session commands are forbidden"),
]
# Sandbox runs (A/B experiments) must never publish: no pushes, no GitHub repo creation or edits.
SANDBOX_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bgit\b[^\n|;&]*?\bpush\b", _I), "SANDBOX: git push is disabled in this run; commit locally only"),
    (re.compile(r"\bgh\b[^\n|;&]*?\b(?:repo\s+(?:create|delete|edit|rename|fork|archive)|release\s+create|pr\s+create|api)\b", _I),
     "SANDBOX: creating or changing anything on GitHub is disabled in this run; keep the repo local"),
]
_TASKKILL = re.compile(r"\btaskkill\b", _I)
_ENUMERATION = re.compile(r"\bnetstat\b|\btasklist\b|\bfor\s+\w+\s+in\b|\$\(|`|\bget-nettcpconnection\b|\bget-process\b|\bwmic\b|\bxargs\b", _I)
_PID_TOKEN = r"(?:\d+|\$\{?\w+\}?)"
_PID_ONLY_TASKKILL = re.compile(rf"\btaskkill\b(?:\s+(?:[/-]{{1,2}}(?:f|t)\b|[/-]{{1,2}}pid\s+{_PID_TOKEN}))+\s*(?:2>|>|\||;|&&|\)|$)", _I)


def check_command(command: str) -> str | None:
    """Return a reason to block, or None when the command is allowed."""
    cmd = command or ""
    rules = RULES + (SANDBOX_RULES if os.environ.get("PIPELINE_SANDBOX") == "1" else [])
    for pat, reason in rules:
        if pat.search(cmd):
            return reason
    if _TASKKILL.search(cmd):
        # taskkill is allowed only as a literal `taskkill /F /PID <pid>` on PIDs you hold, never fed from an enumeration.
        if _ENUMERATION.search(cmd) or not all(_PID_ONLY_TASKKILL.match(seg.strip()) for seg in _split_taskkill_segments(cmd)):
            return ("taskkill is allowed only as `taskkill /F /PID <pid>` on a PID you started yourself, never on PIDs "
                    "collected from netstat, tasklist, loops, or substitutions; if a port is busy, pick a free port instead")
    return None


def _split_taskkill_segments(cmd: str) -> list[str]:
    """The command segments (split on ; | && || newlines) that contain taskkill."""
    segs = re.split(r"\n|;|\|\||&&|\|", cmd)
    return [s[s.lower().index("taskkill"):] for s in segs if "taskkill" in s.lower()]


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    tool = str(payload.get("tool_name") or "")
    if tool not in ("Bash", "PowerShell"):
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    reason = check_command(command)
    if reason is None:
        return 0
    sys.stderr.write(f"BLOCKED by the pipeline guard: {reason}. Kill only PIDs you started (keep them in variables), "
                     f"use a free port instead of clearing a busy one, and run browsers headless via Playwright's bundled Chromium.\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
