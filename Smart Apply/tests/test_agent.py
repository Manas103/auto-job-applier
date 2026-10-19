"""The Claude fallback browser agent: registry fallback, the deny hook, the MCP server driven over stdio against a
real Chrome tab (no Claude), `Actions.open_apply`, and the whole adapter flow with a fake `claude` that acts as an
MCP client. The live run (`python -m smartapply doctor --agent`) is the only test that spends money."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from smartapply import db, intake, tracker
from smartapply.adapters import for_ats, is_agent, supported
from smartapply.adapters.agent import AgentAdapter, hook_settings, mcp_config, tool_inventory_ok
from smartapply.answers import AnswerPacket
from smartapply.browser.actions import Actions, Refused
from smartapply.browser.guard import Guard
from smartapply.claude_runner import ClaudeResult
from smartapply.models import Application, AppState
from smartapply.profile import seed_from_assets

ROOT = Path(__file__).resolve().parents[1]
FAKE_CLAUDE = Path(__file__).resolve().parent / "fake_claude.py"


# ---------------------------------------------------------------- pure

def test_registry_falls_back_to_agent_except_linkedin():
    assert for_ats("workday").ats == "workday"
    assert for_ats("oracle_hcm") is AgentAdapter and for_ats("icims") is AgentAdapter and for_ats(None) is AgentAdapter
    assert for_ats("linkedin") is None
    assert is_agent("smartrecruiters") and not is_agent("greenhouse") and not is_agent("linkedin")
    assert "agent" not in supported() and AgentAdapter.external and AgentAdapter.single_page


def test_deny_hook_decisions():
    spec = importlib.util.spec_from_file_location("deny_hook", ROOT / "hooks" / "deny_all_but_mcp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # type: ignore[union-attr]
    assert mod.decide("mcp__smartapply__fill")["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert mod.decide("StructuredOutput")["hookSpecificOutput"]["permissionDecision"] == "allow"
    for name in ("Bash", "PowerShell", "Read", "WebFetch", "mcp__playwright__browser_click", ""):
        assert mod.decide(name)["hookSpecificOutput"]["permissionDecision"] == "deny", name
    cmd = hook_settings()["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "deny_all_but_mcp.py" in cmd and "\\" not in cmd


def test_tool_inventory_check():
    ok, extra = tool_inventory_ok({"tools": ["StructuredOutput", "mcp__smartapply__fill", "mcp__smartapply__snapshot"]})
    assert ok and not extra
    ok, extra = tool_inventory_ok({"tools": ["StructuredOutput", "mcp__smartapply__fill", "Bash"]})
    assert not ok and extra == ["Bash"]
    ok, _ = tool_inventory_ok({"tools": ["StructuredOutput"]})
    assert not ok   # the MCP server did not come up


def _res(**kw) -> ClaudeResult:
    base = dict(ok=True, subtype="success", session_id="s", cost_usd=0.1, num_turns=3, result_text="", structured_output=None,
                exit_code=0, timed_out=False, log_path=Path("x.log"), init={"tools": ["StructuredOutput", "mcp__smartapply__fill"]})
    base.update(kw)
    return ClaudeResult(**base)


def test_build_report_variants(tmp_path: Path):
    app = Application(id=1, job_id=1, company="A", title="T", url="https://x.example/job", ats=None, ats_id=None, ats_params={},
                      role_folder=tmp_path, slug="a", workdir=tmp_path, state=AppState.FILLING)
    rp = tmp_path / "agent_report.json"
    rp.write_text(json.dumps({"actions": [
        {"action": "fill", "element": "<input #fn> ''", "status": "ok", "intended": "Alex", "observed": "Alex"},
        {"action": "click", "element": "<button> 'Submit application'", "status": "refused", "reason": "submit-like label"},
        {"action": "click", "element": "<button> 'Continue'", "status": "ok"}],
        "reports": [{"status": "needs_human", "reason": "unanswered_required", "left_for_human": [{"label": "Relocate?", "why": "knock-out"}]}],
        "guard": {"aborted": [], "dom_blocked": 1}}), encoding="utf-8")
    # success with structured output
    out = {"status": "needs_human", "reason": "unanswered_required", "stopped_at": "review", "filled": [],
           "left_for_human": [{"label": "Relocate?", "why": "knock-out"}], "notes": ["n"]}
    rep = AgentAdapter.build_report(app, _res(structured_output=out), rp)
    assert rep.status == "needs_human" and rep.reason == "unanswered_required"
    assert [f.status for f in rep.fields] == ["filled", "failed", "left_for_human"]
    assert any("DOM blocked 1" in n for n in rep.notes)
    # ready
    out2 = dict(out, status="ready_for_review", left_for_human=[])
    assert AgentAdapter.build_report(app, _res(structured_output=out2), rp).status == "ready_for_review"
    # blocked by a captcha
    out3 = dict(out, status="blocked", reason="captcha", left_for_human=[])
    rep3 = AgentAdapter.build_report(app, _res(structured_output=out3), rp)
    assert rep3.status == "needs_human" and rep3.reason == "captcha"
    # max turns: no structured output, the server's last report still tells what happened
    rep4 = AgentAdapter.build_report(app, _res(ok=False, subtype="error_max_turns", structured_output=None), rp)
    assert rep4.status == "needs_human" and rep4.reason == "adapter_error"
    assert any("error_max_turns" in n for n in rep4.notes) and any("last server report" in n for n in rep4.notes)
    # tool drift is flagged
    rep5 = AgentAdapter.build_report(app, _res(structured_output=out2, init={"tools": ["StructuredOutput", "mcp__smartapply__fill", "Bash"]}), rp)
    assert any("tool inventory drift" in n for n in rep5.notes)


def test_transcription_slips_are_flagged():
    from smartapply.adapters.agent import profile_atoms, suspicious_fills
    prof = seed_from_assets(None)
    prof.address.street, prof.address.street2, prof.address.city, prof.address.zip = "100 Example Street", "Apt 101", "Springfield", "12345"
    atoms = profile_atoms(prof)
    assert atoms["address.street2"] == "Apt 101" and atoms["first_name"] == "Alex" and "never_claim" not in atoms
    acts = [{"action": "fill", "intended": "Apt 110"}, {"action": "fill", "intended": "Apt 101"},
            {"action": "fill", "intended": "100 Example Street"}, {"action": "fill", "intended": "Springfield"},
            {"action": "fill", "intended": "Rockets are cool"}, {"action": "fill", "intended": "+1-555-010-0142"},
            {"action": "fill", "intended": "407-555-0124"}, {"action": "click", "intended": None},
            {"action": "fill", "intended": "Decline to Self Identify"}]     # case/hyphen only: not a slip
    prof.eeo.gender = "Decline to self-identify"
    atoms = profile_atoms(prof)
    slips = suspicious_fills(acts, atoms)
    assert [(t, e) for t, _k, e in slips] == [("Apt 110", "Apt 101"), ("407-555-0124", "555-010-0142")] or            [(t, e) for t, _k, e in slips][:1] == [("Apt 110", "Apt 101")]


def test_mcp_config_shape(cfg, tmp_path: Path):
    app = Application(id=1, job_id=1, company="A", title="T", url="https://x.example/job", ats="oracle_hcm", ats_id=None,
                      ats_params={}, role_folder=tmp_path, slug="a", workdir=tmp_path, state=AppState.DRAFTED)
    c = mcp_config(cfg, app, "TID")["mcpServers"]["smartapply"]
    assert c["args"] == ["-m", "smartapply.browser.mcp_server"] and c["command"].lower().endswith(("pythonw.exe", "python.exe"))
    assert c["env"]["SMARTAPPLY_TARGET_ID"] == "TID" and c["env"]["SMARTAPPLY_ATS"] == "oracle_hcm"
    assert Path(c["env"]["PYTHONPATH"]) == ROOT


# ---------------------------------------------------------------- real Chrome

def _text(res) -> str:
    return "\n".join(c.text for c in res.content if getattr(c, "text", None))


def _refs(snap: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in snap.splitlines():
        parts = line.split(" ", 2)
        if len(parts) >= 3 and parts[0].startswith("e") and '"' in parts[2]:
            out.setdefault(parts[2].split('"')[1], parts[0])
    return out


def _server_cfg_path(chrome, tmp_path: Path) -> Path:
    """A config.json the server process can load: it must find the session Chrome's DevToolsActivePort."""
    p = tmp_path / "config.json"
    from smartapply.profile import save_profile
    save_profile(seed_from_assets(None), tmp_path / "profile.yaml")
    p.write_text(json.dumps({"chrome": {"profile_dir": str(chrome.profile_dir)}, "data_dir": str(tmp_path / "data"),
                             "archon_env": str(tmp_path / "archon.env"), "profile_path": str(tmp_path / "profile.yaml"),
                             "notify": {"enabled": False}}), encoding="utf-8")
    return p


@pytest.mark.usefixtures("chrome")
def test_mcp_server_fills_fixture_and_never_submits(chrome, fixture_server, tmp_path: Path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from smartapply.config import load_config

    fixture_server.reset()
    cfg_path = _server_cfg_path(chrome, tmp_path)
    cfg = load_config(cfg_path)
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page, tid = chrome.open_background_tab(b, fixture_server.url("agent.html"))
        page.wait_for_load_state("domcontentloaded")
        b.close()
    workdir = tmp_path / "app"
    workdir.mkdir()
    (workdir / "Alex_Doe_Resume.pdf").write_bytes(b"%PDF-1.4 fake\n")
    app = Application(id=1, job_id=1, company="Acme", title="SWE", url=fixture_server.url("agent.html"), ats="_fixture",
                      ats_id=None, ats_params={}, role_folder=workdir, slug="acme", workdir=workdir, state=AppState.DRAFTED)
    conf = mcp_config(cfg, app, tid)["mcpServers"]["smartapply"]
    conf["env"]["SMARTAPPLY_CONFIG"] = str(cfg_path)
    env = dict(os.environ)
    env.update(conf["env"])

    async def drive() -> dict:
        got: dict = {}
        params = StdioServerParameters(command=conf["command"], args=conf["args"], env=env)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                got["tools"] = sorted(t.name for t in (await s.list_tools()).tools)
                got["info0"] = _text(await s.call_tool("page_info", {}))
                snap0 = _text(await s.call_tool("snapshot", {}))
                got["snap0"] = snap0
                got["open"] = _text(await s.call_tool("open_application_form", {}))
                got["open_again"] = _text(await s.call_tool("open_application_form", {}))
                refs = _refs(_text(await s.call_tool("snapshot", {})))
                got["refs"] = refs
                got["profile_fill"] = _text(await s.call_tool("fill_profile", {"ref": refs["First name *"], "field": "first_name"}))
                got["profile_bad"] = _text(await s.call_tool("fill_profile", {"ref": refs["First name *"], "field": "shoe_size"}))
                got["fill_many"] = _text(await s.call_tool("fill_many", {"items": [
                    {"ref": refs["First name *"], "value": "Alex"}, {"ref": refs["Last name *"], "value": "Doe"},
                    {"ref": refs["Email *"], "value": "m@x.com"}]}))
                got["select"] = _text(await s.call_tool("select", {"ref": refs["Country *"], "option": "United States"}))
                got["school"] = _text(await s.call_tool("fill", {"ref": refs["School"], "value": "State Univ", "slow": True}))
                refs2 = _refs(_text(await s.call_tool("snapshot", {})))
                got["stable"] = refs2["First name *"] == refs["First name *"]
                # a combobox with one matching suggestion is committed by fill itself; select() also works on it
                got["pick"] = _text(await s.call_tool("select", {"ref": refs["School"], "option": "Stanford"}))
                got["pick2"] = _text(await s.call_tool("select", {"ref": refs["School"], "option": "State University"}))
                got["upload"] = _text(await s.call_tool("upload", {"ref": refs["Resume (PDF) *"]}))
                got["yes"] = _text(await s.call_tool("check", {"ref": refs["Yes"], "checked": True}))
                got["consent"] = _text(await s.call_tool("check", {"ref": refs["I agree to the privacy policy *"], "checked": True}))
                got["why"] = _text(await s.call_tool("fill", {"ref": refs["Why Acme?"], "value": "Rockets."}))
                got["next"] = _text(await s.call_tool("click", {"ref": refs["Continue"]}))
                snap3 = _text(await s.call_tool("snapshot", {}))
                refs3 = _refs(snap3)
                got["info_review"] = _text(await s.call_tool("page_info", {}))
                got["submit"] = _text(await s.call_tool("click", {"ref": refs3["Submit application"]}))
                got["navigate"] = _text(await s.call_tool("navigate", {"url": "https://evil.example/x"}))
                got["stale"] = _text(await s.call_tool("fill", {"ref": "e999", "value": "x"}))
                got["report"] = _text(await s.call_tool("report", {"status": "ready_for_review", "reason": "", "left_for_human": [], "notes": ["n1"]}))
        return got

    got = asyncio.run(drive())
    assert got["tools"] == sorted(["page_info", "snapshot", "fill", "fill_many", "fill_profile", "fill_password", "select", "check",
                                   "click", "upload", "open_application_form", "navigate", "wait", "read_verification_mail", "report"])
    assert got["profile_fill"].startswith("filled") and "Alex" in got["profile_fill"] and "unknown profile field" in got["profile_bad"]
    assert "guard_armed: True" in got["info0"] and 'button "Apply now"' in got["snap0"] and "First name" not in got["snap0"]
    assert got["open"].startswith("followed") and got["open_again"].startswith("REFUSED")   # fields now visible
    assert got["fill_many"].count(": ok") == 3 and 'selected "United States"' in got["select"] and got["stable"]
    assert "committed the matching option \"State University\"" in got["school"]
    assert got["pick"].startswith("picked option") and got["pick2"].startswith("picked option") and got["upload"].startswith("uploaded")
    assert "checked=True" in got["yes"] and "checked=True" in got["consent"]
    assert "review_or_final_page: True" in got["info_review"]
    assert got["submit"].startswith("REFUSED") and "refused" in got["navigate"] and "stale" in got["stale"]
    assert got["report"].startswith("recorded")
    rep = json.loads((workdir / "agent_report.json").read_text(encoding="utf-8"))
    assert rep["reports"][0]["status"] == "ready_for_review"
    assert any(a["action"] == "click" and a["status"] == "refused" for a in rep["actions"])
    assert any(a["action"] == "open_apply" for a in rep["actions"])
    assert fixture_server.hits("/apply") == 0
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page = chrome.page_for_target(b, tid)
        assert page is not None
        assert page.evaluate("() => document.getElementById('fn').value") == "Alex"
        assert page.evaluate("() => document.getElementById('school').value") == "State University"
        assert page.evaluate("() => document.querySelector('input[name=auth]:checked').value") == "Yes"
        assert page.evaluate("() => !!window.__sa") is False     # the server disarmed on exit
        assert page.evaluate("() => document.getElementById('done').classList.contains('hidden')")
        page.close()
        b.close()


@pytest.mark.usefixtures("chrome")
def test_snapshot_announces_a_captcha(chrome, fixture_server):
    from smartapply.browser.mcp_server import SNAPSHOT_JS, format_snapshot
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page, tid = chrome.open_background_tab(b, fixture_server.url("agent.html#captcha"))
        page.wait_for_load_state("domcontentloaded")
        d = page.evaluate(SNAPSHOT_JS, {"scope": "", "max": 300})
        assert "captcha-delivery" in d["captcha"] and "DataDome" in d["captcha"]
        assert format_snapshot(d).splitlines()[2].startswith("CAPTCHA_PRESENT:")
        page.goto(fixture_server.url("agent.html"))
        page.wait_for_load_state("domcontentloaded")
        assert page.evaluate(SNAPSHOT_JS, {"scope": "", "max": 300})["captcha"] == ""
        page.close()
        b.close()


@pytest.mark.usefixtures("chrome")
def test_open_apply_only_on_posting_pages(chrome, fixture_server):
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page, tid = chrome.open_background_tab(b, fixture_server.url("agent.html"))
        page.wait_for_load_state("domcontentloaded")
        guard = Guard(page, "_fixture", ttl_ms=60000)
        guard.arm()
        act = Actions(page, ats="_fixture", guard=guard)
        # the tool layer refuses the Apply button as a plain click (it looks like submit) ...
        with pytest.raises(Refused):
            act.click(page.locator("#applyBtn"))
        # ... and open_apply follows it because nothing on the page is fillable
        info = act.open_apply()
        assert info.label == "Apply now"
        assert page.locator("#fn").is_visible()
        assert guard.dom_armed()          # re-armed after the paused click
        with pytest.raises(Refused):
            act.open_apply()               # fields visible now: refused
        page.close()
        b.close()


@pytest.mark.usefixtures("chrome")
def test_agent_adapter_flow_with_fake_claude(chrome, fixture_server, tmp_project: Path, monkeypatch):
    """filler.stage_fill with the agent adapter: opens the tab, spawns the fake claude (an MCP client), which fills
    through the real server, then reads back and records the outcome."""
    from smartapply.config import load_config
    from smartapply.filler import stage_fill

    fixture_server.reset()
    conf = json.loads((tmp_project / "config.json").read_text(encoding="utf-8"))
    conf["chrome"]["profile_dir"] = str(chrome.profile_dir)
    conf["claude"] = {"cmd": [sys.executable, str(FAKE_CLAUDE)], "agent_model": "fake-model", "agent_max_turns": 9}
    (tmp_project / "config.json").write_text(json.dumps(conf), encoding="utf-8")
    cfg = load_config(tmp_project / "config.json", project_root=tmp_project)
    conn = db.connect(cfg.db_path)
    profile = seed_from_assets(None)
    workdir = cfg.applications_dir / "9001"
    workdir.mkdir(parents=True)
    role = tmp_project / "role"
    role.mkdir()
    (role / "meta.json").write_text(json.dumps({"slug": "acme"}), encoding="utf-8")
    (role / "experience_descriptions_acme.md").write_text(
        "## Acme Labs\nForm fields: Acme Labs | Software Engineer Intern | Austin, TX | Feb. 2026 | Jun. 2026\n", encoding="utf-8")
    (workdir / profile.resume_filename).write_bytes(b"%PDF-1.4 fake\n")
    app_id = db.insert_application(conn, job_id=9001, company="Acme", title="SWE Intern", url=fixture_server.url("agent.html"),
                                   ats="_fixture", ats_id=None, ats_params={}, role_folder=str(role), slug="acme",
                                   workdir=str(workdir), state=AppState.DRAFTED.value)
    app = tracker.load(conn, app_id)
    log_path = tmp_project / "fake_claude.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log_path))
    monkeypatch.setenv("FAKE_AGENT_MODE", "fill")
    report = stage_fill(cfg, conn, app, profile, chrome, [], AnswerPacket(answers={}, unanswered_required=[]))
    assert report.status == "needs_human" and report.reason == "unanswered_required", report.notes
    labels = {f.label: f.status for f in report.fields}
    assert any(v == "left_for_human" for v in labels.values())
    assert any(f.status == "filled" and f.intended == "Alex" for f in report.fields)
    assert not any("drift" in n for n in report.notes)
    assert any("submit refused:" in n for n in report.notes)     # the refused-click path itself is covered above
    app = tracker.load(conn, app_id)
    assert app.state == AppState.NEEDS_HUMAN and app.target_id
    assert (workdir / "agent_report.json").exists() and (workdir / "mcp_config.json").exists() and (workdir / "agent_prompt.md").exists()
    prompt = (workdir / "agent_prompt.md").read_text(encoding="utf-8")
    assert "BROWSER AGENT playbook" in prompt and "Acme Labs" in prompt and "# PROFILE" in prompt
    assert '"work": [' in prompt and '"projects": []' in prompt
    call = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    argv = call["argv"]
    assert "--strict-mcp-config" in argv and argv[argv.index("--tools") + 1] == "" and "mcp__smartapply" in argv[argv.index("--allowedTools") + 1]
    assert "Bash" in argv[argv.index("--disallowedTools") + 1] and argv[argv.index("--setting-sources") + 1] == ""
    assert "deny_all_but_mcp.py" in argv[argv.index("--settings") + 1] and argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert db.cost_totals(conn)["total_usd"] > 0 and db.cost_totals(conn)["runs"] == 1
    assert fixture_server.hits("/apply") == 0
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page = chrome.page_for_target(b, app.target_id)
        assert page is not None and page.evaluate("() => document.getElementById('fn').value") == "Alex"
        assert page.evaluate("() => !!window.__sa") is False
        page.close()
        b.close()
    # a blocked run (captcha) is recorded with its reason
    tracker.transition(conn, app_id, AppState.DRAFTED, "retry")
    monkeypatch.setenv("FAKE_AGENT_MODE", "blocked")
    report2 = stage_fill(cfg, conn, tracker.load(conn, app_id), profile, chrome, [], AnswerPacket(answers={}, unanswered_required=[]))
    assert report2.status == "needs_human" and report2.reason == "captcha"
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page = chrome.page_for_target(b, tracker.load(conn, app_id).target_id)
        if page is not None:
            page.close()
        b.close()
    conn.close()
