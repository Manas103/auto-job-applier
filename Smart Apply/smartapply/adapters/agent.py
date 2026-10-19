"""The Claude fallback adapter for sites without a deterministic adapter (Oracle HCM, iCIMS, SmartRecruiters,
Phenom, SuccessFactors, plain career sites).

It does not run in the daemon's Playwright session. The filler opens the tab and disconnects; this adapter spawns
`claude -p` (Sonnet, Max login) with one MCP server, `smartapply.browser.mcp_server`, which attaches to that tab,
arms the guard in its own process and exposes typed page tools. The model never gets a shell, a file system or the
web: `--tools ""`, an explicit `--disallowedTools` list, `--allowedTools mcp__smartapply`, `--strict-mcp-config`,
`--setting-sources ""` (no ~/.claude hooks or CLAUDE.md), and a PreToolUse hook that denies everything else.
The result is read from the server's `agent_report.json` (every action, written as it happens) and the model's
structured output."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .. import db, intake
from ..answers import AnswerPacket
from ..claude_runner import BUILTIN_TOOLS, ClaudeResult, run_claude
from ..config import Config
from ..log import app_log_path, get_logger
from ..models import Application, FieldOutcome, FillReport, Question
from ..paths import PACKAGE_DIR, PLAYBOOKS_DIR, PROJECT_ROOT, local_app_dir
from ..profile import Profile, public_view
from .base import Adapter

log = get_logger("agent")

AGENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ready_for_review", "needs_human", "blocked"]},
        "reason": {"type": "string"},
        "stopped_at": {"type": "string"},
        "filled": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "value": {"type": "string"}},
                                              "required": ["label", "value"]}},
        "left_for_human": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "why": {"type": "string"}},
                                                      "required": ["label", "why"]}},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "reason", "stopped_at", "filled", "left_for_human", "notes"],
}

MCP_SERVER_NAME = "smartapply"
EXPECTED_TOOL_PREFIX = f"mcp__{MCP_SERVER_NAME}__"
HOOK_PATH = PROJECT_ROOT / "hooks" / "deny_all_but_mcp.py"


def _python_for_server() -> str:
    """pythonw.exe when it sits next to the interpreter: no console window can appear, and piped stdio works."""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    if exe.name.lower() == "pythonw.exe" or not pyw.exists():
        return str(exe)
    return str(pyw)


def _python_console() -> str:
    """The hook must write to stdout: python.exe (pythonw is fine too, but keep the plain one for clarity)."""
    exe = Path(sys.executable)
    py = exe.with_name("python.exe")
    return str(py if py.exists() else exe)


def _q(path: str | Path) -> str:
    return '"' + str(path).replace("\\", "/") + '"'


def mcp_config(cfg: Config, app: Application, target_id: str) -> dict[str, Any]:
    env = {
        "SMARTAPPLY_TARGET_ID": target_id,
        "SMARTAPPLY_ATS": app.ats or "",
        "SMARTAPPLY_WORKDIR": str(app.workdir),
        "SMARTAPPLY_CONFIG": str(cfg.project_root / "config.json"),
        "SMARTAPPLY_JOB_URL": app.url,
        "PYTHONPATH": str(PROJECT_ROOT),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    return {"mcpServers": {MCP_SERVER_NAME: {"type": "stdio", "command": _python_for_server(),
                                             "args": ["-m", "smartapply.browser.mcp_server"], "env": env}}}


def hook_settings() -> dict[str, Any]:
    cmd = f"{_q(_python_console())} {_q(HOOK_PATH)}"
    return {"hooks": {"PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": cmd, "timeout": 20}]}]}}


def _read(path: Path | None, limit: int) -> str:
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def build_prompt(cfg: Config, conn: sqlite3.Connection, app: Application, profile: Profile, questions: list[Question],
                 packet: AnswerPacket) -> str:
    playbook = _read(PLAYBOOKS_DIR / "BROWSER_AGENT.md", 30000)
    role = intake.resolve_role_folder(app.role_folder)
    all_entries = intake.parse_experience_entries(role.experience_md)
    entries = {"work": [e.__dict__ for e in all_entries if not e.is_project],
               "projects": [e.__dict__ for e in all_entries if e.is_project]}
    by_id = {q.id: q for q in questions}
    answers = []
    for qid, a in packet.answers.items():
        q = by_id.get(qid)
        if a.needs_human or a.value in (None, "", []):
            continue
        answers.append({"question": q.label if q else qid, "value": a.value})
    memory = [{"question": r["question"], "answer": r["answer"]} for r in db.recall_answers(conn)][:60]
    parts = [playbook, "\n---\n",
             "# JOB\n```json\n" + json.dumps({"company": app.company, "title": app.title, "url": app.url, "ats": app.ats or "unknown"}, indent=1) + "\n```\n",
             "# PROFILE\n```json\n" + json.dumps(public_view(profile), indent=1, ensure_ascii=False) + "\n```\n",
             f"# RESUME_FILENAME\n{profile.resume_filename}\n",
             "# EXPERIENCE\n`work` entries go into employment / work-history sections (one row each, exactly these companies, "
             "titles, locations and dates). `projects` are NOT employment: use them only where a form asks for projects, "
             "portfolio or additional experience, never as an employer row.\n```json\n"
             + json.dumps(entries, indent=1, ensure_ascii=False) + "\n```\n"
             + "Notes and descriptions written for this role's forms:\n" + _read(role.experience_md, 14000),
             "\n# ANSWERS\n```json\n" + json.dumps(answers, indent=1, ensure_ascii=False) + "\n```\n",
             "# MEMORY\n```json\n" + json.dumps(memory, indent=1, ensure_ascii=False) + "\n```\n",
             "\nStart now: call page_info, then snapshot. Finish with the JSON object from section 5 of the playbook."]
    return "\n".join(parts)


def profile_atoms(profile: Profile) -> dict[str, str]:
    """Flat `field.path -> value` of every string fact in the profile (what `fill_profile` may type)."""
    out: dict[str, str] = {}

    def walk(prefix: str, obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(f"{prefix}.{k}" if prefix else str(k), v)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(f"{prefix}[{i}]", v)
        elif isinstance(obj, (str, int, float)) and not isinstance(obj, bool):
            v = str(obj).strip()
            if v:
                out[prefix] = v
    walk("", public_view(profile))
    out.pop("never_claim", None)
    return out


def suspicious_fills(actions: list[dict[str, Any]], atoms: dict[str, str]) -> list[tuple[str, str, str]]:
    """Typed values that look like a profile fact but differ from it (a transcription slip such as "Apt 110" for
    "Apt 101"). Returns (typed, field, expected)."""
    import difflib
    import re as _re

    def norm(v: str) -> str:
        return _re.sub(r"[^a-z0-9]+", " ", v.lower()).strip()    # case, hyphens and spacing are not slips
    exact = {norm(v) for v in atoms.values()}
    out: list[tuple[str, str, str]] = []
    for a in actions:
        if a.get("action") != "fill" or not isinstance(a.get("intended"), str):
            continue
        typed = a["intended"].strip()
        if len(typed) < 4 or norm(typed) in exact:
            continue
        # a full date whose year-month is a profile date ("2028-05-01" for graduation "2028-05"; Amazon, 2026-10-06)
        m = _re.fullmatch(r"(\d{4})-(\d{2})-\d{2}", typed)
        if m and any(norm(v).startswith(f"{m.group(1)} {m.group(2)}") for v in atoms.values()):
            continue
        best: tuple[float, str, str] | None = None
        for key, exp in atoms.items():
            if len(exp) < 4 or abs(len(exp) - len(typed)) > 2 or exp[:3].lower() != typed[:3].lower():
                continue
            r = difflib.SequenceMatcher(None, norm(typed), norm(exp)).ratio()
            if r >= 0.75 and (best is None or r > best[0]):
                best = (r, key, exp)
        if best is not None:
            out.append((typed, best[1], best[2]))
    return out


def tool_inventory_ok(init: dict | None) -> tuple[bool, list[str]]:
    """The system/init event lists the tools the run can use. Anything beyond ours is a configuration drift."""
    tools = [str(t) for t in ((init or {}).get("tools") or [])]
    extra = [t for t in tools if not (t.startswith(EXPECTED_TOOL_PREFIX) or t == "StructuredOutput")]
    return (not extra) and any(t.startswith(EXPECTED_TOOL_PREFIX) for t in tools), extra


class AgentAdapter(Adapter):
    ats = "agent"
    single_page = True      # its state lives in the open tab, like Greenhouse: filled last in a batch
    external = True         # the filler opens the tab and disconnects before `run` spawns the model

    def fill(self, questions: list[Question], packet: AnswerPacket) -> FillReport:  # pragma: no cover
        raise RuntimeError("AgentAdapter runs out of process: use AgentAdapter.run")

    @classmethod
    def run(cls, cfg: Config, conn: sqlite3.Connection, app: Application, profile: Profile, target_id: str,
            questions: list[Question], packet: AnswerPacket, *, max_turns: int | None = None,
            timeout_s: int | None = None) -> tuple[FillReport, ClaudeResult]:
        prompt = build_prompt(cfg, conn, app, profile, questions, packet)
        (app.workdir / "agent_prompt.md").write_text(prompt, encoding="utf-8")
        mcp_path = app.workdir / "mcp_config.json"
        mcp_path.write_text(json.dumps(mcp_config(cfg, app, target_id), indent=1), encoding="utf-8")
        report_path = app.workdir / "agent_report.json"
        if report_path.exists():
            report_path.unlink()
        cwd = local_app_dir() / "agent-cwd"
        cwd.mkdir(parents=True, exist_ok=True)
        model = str(cfg.claude.agent_model)
        res = run_claude(cfg, prompt=prompt, cwd=cwd, log_path=app_log_path(cfg.logs_dir, app.id, "agent"), model=model,
                         timeout_s=timeout_s or int(cfg.claude.get("agent_timeout_min", 45)) * 60,
                         max_turns=max_turns or int(cfg.claude.agent_max_turns), max_budget_usd=cfg.claude.get("max_budget_usd"),
                         json_schema=AGENT_SCHEMA, tools=[], allowed_tools=[f"mcp__{MCP_SERVER_NAME}"],
                         disallowed_tools=list(BUILTIN_TOOLS), mcp_config=mcp_path, strict_mcp=True,
                         permission_mode="dontAsk", setting_sources="", settings=hook_settings(),
                         env_extra={"MCP_TIMEOUT": "60000"})
        db.record_cost(conn, app.id, purpose="agent", model=model, cost_usd=res.cost_usd, num_turns=res.num_turns,
                       session_id=res.session_id, subtype=res.subtype, duration_s=res.duration_s)
        report = cls.build_report(app, res, report_path, profile=profile)
        for acct in (json.loads(report_path.read_text(encoding="utf-8")).get("accounts") if report_path.exists() else []) or []:
            try:
                db.upsert_account(conn, "agent", str(acct.get("account", "")), profile.email, last_login_at=acct.get("at"),
                                  note="created by the browser agent" if acct.get("created") else None)
            except Exception as e:  # noqa: BLE001
                log.debug("account upsert: %s", e)
        return report, res

    @classmethod
    def build_report(cls, app: Application, res: ClaudeResult, report_path: Path, *,
                     profile: Profile | None = None) -> FillReport:
        rep = FillReport(status="filling")
        server: dict[str, Any] = {}
        if report_path.exists():
            try:
                server = json.loads(report_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                server = {}
        page = "agent"
        for a in server.get("actions") or []:
            st = a.get("status")
            status = "filled" if st in ("ok", "mismatch") else ("failed" if st == "refused" else "skipped")
            if a.get("action") in ("click", "open_apply", "press_search") and st == "ok":
                continue   # navigation, not a field
            rep.fields.append(FieldOutcome(page=page, name=str(a.get("element", ""))[:120], label=str(a.get("element", ""))[:120],
                                           intended=a.get("intended"), observed=a.get("observed"), status=status,
                                           note=a.get("reason")))
        out = res.structured_output if isinstance(res.structured_output, dict) else None
        last_report = (server.get("reports") or [{}])[-1] if server.get("reports") else {}
        # the model's final answer wins; the server's last `report` call only stands in when the run was cut off
        left = list(out.get("left_for_human") or []) if out else list(last_report.get("left_for_human") or [])
        for item in left:
            label = str(item.get("label", ""))[:160]
            rep.fields.append(FieldOutcome(page=page, name=label, label=label, intended=None, observed=None,
                                           status="left_for_human", note=str(item.get("why", ""))[:200]))
        slips = suspicious_fills(server.get("actions") or [], profile_atoms(profile)) if profile is not None else []
        for typed, key, expected in slips:
            rep.fields.append(FieldOutcome(page=page, name=key, label=f"typed {typed!r}", intended=expected, observed=typed,
                                           status="failed", note=f"differs from profile {key} = {expected!r}; fix before submitting"))
        ok_tools, extra = tool_inventory_ok(res.init)
        if res.init is not None and not ok_tools:
            rep.notes.append(f"WARNING: tool inventory drift: {extra[:6]}")
        if res.permission_denials:
            rep.notes.append(f"{len(res.permission_denials)} tool call(s) denied by the hook")
        g = server.get("guard") or {}
        rep.notes.append(f"guard aborted {len(g.get('aborted') or [])} request(s), DOM blocked {g.get('dom_blocked', 0)}")
        rep.notes.append(f"claude {res.subtype}, {res.num_turns} turns, ${res.cost_usd:.2f}")
        status = (out or {}).get("status") or last_report.get("status") or ""
        reason = (out or {}).get("reason") or last_report.get("reason") or ""
        if out and out.get("stopped_at"):
            rep.notes.append("stopped at: " + str(out["stopped_at"])[:200])
        for n in ((out or {}).get("notes") or [])[:6]:
            rep.notes.append(str(n)[:200])
        if not res.ok and not out:
            rep.status = "needs_human"
            rep.reason = "adapter_error"
            rep.notes.append(f"agent run failed: {res.subtype}" + (f" ({res.stderr_tail[-200:].strip()})" if res.stderr_tail.strip() else ""))
            if last_report.get("status"):
                rep.notes.append(f"last server report: {last_report.get('status')} {last_report.get('reason', '')}")
            return rep
        if slips:
            rep.status = "needs_human"
            rep.reason = "unanswered_required"
            rep.notes.append("check these typed values against the profile: " + ", ".join(f"{t!r} vs {e!r}" for t, _k, e in slips[:6]))
        elif status == "ready_for_review" and not left:
            rep.status = "ready_for_review"
        elif status == "blocked":
            rep.status = "needs_human"
            rep.reason = reason if reason in ("captcha", "login", "verification_pending", "unknown_site") else "unknown_site"
        else:
            rep.status = "needs_human"
            rep.reason = reason if reason in ("captcha", "login", "verification_pending", "unanswered_required", "adapter_error") else "unanswered_required"
            if left:
                rep.notes.append("left for you: " + ", ".join(str(i.get("label", ""))[:60] for i in left[:8]))
        return rep


# ---------------------------------------------------------------- self-test (doctor --agent)

SELFTEST_HTML = """<!doctype html><html><head><title>Smart Apply agent self-test</title></head><body>
<h1>Self-test application</h1>
<form action="/apply" method="post" onsubmit="event.preventDefault(); document.getElementById('out').textContent='SUBMITTED';">
<label for="fn">First name *</label> <input id="fn" name="first_name" required>
<label for="ln">Last name *</label> <input id="ln" name="last_name" required>
<label for="em">Email *</label> <input id="em" name="email" type="email" required>
<label for="src">How did you hear about us?</label> <select id="src" name="source"><option value="">Select</option><option>LinkedIn</option><option>Company website</option><option>Other</option></select>
<label><input type="checkbox" id="tc" name="consent"> I agree to the terms</label>
<button type="submit">Submit application</button>
</form><div id="out"></div></body></html>"""


def self_test(cfg: Config, *, keep_tab: bool = False) -> dict[str, Any]:
    """One real `claude -p` run against a tiny data: URL form in the SmartApply Chrome. Proves the whole chain: MCP
    server under pythonw, tool inventory, hook, guard refusal of the submit button, structured output."""
    import tempfile
    import urllib.parse

    from playwright.sync_api import sync_playwright

    from ..browser.chrome import Chrome, foreground_window_handle
    from ..models import AppState

    chrome = Chrome(cfg)
    chrome.ensure()
    out: dict[str, Any] = {"ok": False}
    fg_before = foreground_window_handle()
    url = "data:text/html," + urllib.parse.quote(SELFTEST_HTML)
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page, tid = chrome.open_background_tab(b, url)
        page.wait_for_load_state("domcontentloaded")
        b.close()
    workdir = Path(tempfile.mkdtemp(prefix="sa-selftest-", dir=str(cfg.data_dir)))
    (workdir / "Alex_Doe_Resume.pdf").write_bytes(b"%PDF-1.4 selftest\n")
    app = Application(id=0, job_id=0, company="Self-test", title="Agent self-test", url="http://127.0.0.1/selftest",
                      ats=None, ats_id=None, ats_params={}, role_folder=workdir, slug="selftest", workdir=workdir,
                      state=AppState.DRAFTED)
    prompt = (_read(PLAYBOOKS_DIR / "BROWSER_AGENT.md", 30000) + "\n---\n# SELF-TEST\nThis is a self-test on a tiny form. "
              "Call page_info, then snapshot, then fill First name with 'Alex', Last name with 'Doe', Email with "
              "'alex@example.com', select 'Company website' for the source, check the consent box, then try to "
              "click the Submit application button once (it must be refused), call report with status ready_for_review, "
              "and finish with the JSON object from section 5 of the playbook.")
    mcp_path = workdir / "mcp_config.json"
    mcp_path.write_text(json.dumps(mcp_config(cfg, app, tid), indent=1), encoding="utf-8")
    model = str(cfg.claude.agent_model)
    res = run_claude(cfg, prompt=prompt, cwd=local_app_dir() / "agent-cwd", log_path=cfg.logs_dir / "agent_selftest.log",
                     model=model, timeout_s=600, max_turns=24, json_schema=AGENT_SCHEMA, tools=[],
                     allowed_tools=[f"mcp__{MCP_SERVER_NAME}"], disallowed_tools=list(BUILTIN_TOOLS), mcp_config=mcp_path,
                     strict_mcp=True, permission_mode="dontAsk", setting_sources="", settings=hook_settings(),
                     env_extra={"MCP_TIMEOUT": "60000"})
    out.update({"subtype": res.subtype, "cost_usd": res.cost_usd, "turns": res.num_turns, "structured": res.structured_output,
                "permission_denials": res.permission_denials, "log": str(res.log_path)})
    ok_tools, extra = tool_inventory_ok(res.init)
    out["tools"] = list((res.init or {}).get("tools") or [])
    out["tool_inventory_ok"] = ok_tools
    out["unexpected_tools"] = extra
    rep_path = workdir / "agent_report.json"
    server = json.loads(rep_path.read_text(encoding="utf-8")) if rep_path.exists() else {}
    out["server_actions"] = len(server.get("actions") or [])
    out["submit_refused"] = any(a.get("action") == "click" and a.get("status") == "refused" for a in server.get("actions") or [])
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page = chrome.page_for_target(b, tid)
        if page is not None:
            vals = page.evaluate("() => ({fn: document.getElementById('fn').value, ln: document.getElementById('ln').value, "
                                 "em: document.getElementById('em').value, src: document.getElementById('src').value, "
                                 "tc: document.getElementById('tc').checked, out: document.getElementById('out').textContent, "
                                 "guard: !!window.__sa})")
            out["page"] = vals
            out["not_submitted"] = vals.get("out") != "SUBMITTED"
            out["filled_ok"] = vals.get("fn") == "Alex" and vals.get("ln") == "Doe" and "@" in str(vals.get("em"))
            if not keep_tab:
                page.close()
        else:
            out["page"] = None
            out["not_submitted"] = True
            out["filled_ok"] = False
        b.close()
    out["focus_unchanged"] = fg_before == foreground_window_handle()
    out["ok"] = bool(res.ok and ok_tools and out["submit_refused"] and out["not_submitted"] and out["filled_ok"]
                     and out["focus_unchanged"] and not res.permission_denials)
    return out
