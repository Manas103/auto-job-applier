"""RUBRIC 1.3.0 token work: tool allowlist, compact identity list, company dossier, self-check wiring."""

from __future__ import annotations

import json
import os

from pipeline import index as index_mod, research
from pipeline.stages import builder, critic, ideamaker, reconcile
from pipeline.stages.claude_runner import ClaudeResult, build_cmd

from test_stages_worker import _calls, _full_script, _fake_repo, _mk_job, fake_env  # noqa: F401 (fixture)
from pipeline import worker as wk
from pipeline.models import JobState


def test_tool_allowlist_reaches_argv(cfg):
    cmd = build_cmd(cfg, prompt="x", model=None, max_budget_usd=1, max_turns=None, resume_session=None,
                    tools=["Bash", "Read", "WebSearch"])
    assert cmd[cmd.index("--tools") + 1] == "Bash,Read,WebSearch" and "--strict-mcp-config" in cmd
    assert "--tools" not in build_cmd(cfg, prompt="x", model=None, max_budget_usd=1, max_turns=None, resume_session=None)


def test_stage_tools_config_reaches_every_call(cfg, conn, fake_env, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(_full_script(cfg)))
    _fake_repo(cfg)
    cfg.stages.ideamaker.tools = ["Bash", "Read", "WebSearch", "WebFetch"]
    job, folder = _mk_job(conn, cfg)
    assert wk.run_one(cfg, job.id) == JobState.DONE
    argv = {c["env"]["PIPELINE_STAGE"]: c["argv"] for c in _calls(fake_env)}
    assert argv["ideamaker"][argv["ideamaker"].index("--tools") + 1] == "Bash,Read,WebSearch,WebFetch"
    assert "--tools" not in argv["builder"]


def test_used_identities_is_compact_and_complete(cfg):
    rows = index_mod.read_rows(cfg)
    assert rows, "fixture INDEX has rows"
    p = index_mod.write_used_identities(cfg)
    text = p.read_text(encoding="utf-8")
    for r in rows:
        if r.name:
            assert r.name in text
        assert r.folder in text
    assert "—" not in text
    assert index_mod._short_school("University of Pennsylvania, B.S.E. Computer Science (chosen because x, y)") == "University of Pennsylvania"
    assert index_mod._short_school("University of California, Berkeley, B.S. EECS") == "University of California, Berkeley"


def test_prompts_point_at_check_command_used_list_and_dossier(cfg, conn):
    job, folder = _mk_job(conn, cfg)
    p = ideamaker.build_prompt(job, cfg, folder, {})
    assert "-m pipeline check resume" in p and "USED_IDENTITIES.md" in p and "COMPANY DOSSIER: (none" in p
    assert "Do not edit roles/INDEX.md" in p
    assert "-m pipeline check builder" in builder.build_prompt(job, cfg, folder, {})
    assert "-m pipeline check reconcile" in reconcile.build_prompt(job, cfg, folder, {})
    assert "-m pipeline check resume" in critic.build_prompt(job, cfg, folder, {})
    # with a fresh dossier on disk, the ideamaker is pointed at it
    d = research.dossier_path(cfg, job.company)
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("# dossier\n" + "x" * 600, encoding="utf-8")
    assert str(d) in ideamaker.build_prompt(job, cfg, folder, {})


def _dossier_text(n_sources=7):
    body = "\n".join(f"## {h}\ntext [1]\n" for h in ("What they build and who hires interns", "Real tech stack",
                                                       "What interns actually built", "Hiring funnel"))
    return body + "\n## Sources\n" + "\n".join(f"{i}. https://example.com/{i}" for i in range(1, n_sources + 1))


def test_dossier_researched_once_per_company_and_cost_recorded(cfg, conn, monkeypatch):
    cfg.research.enabled = True
    calls = []

    def fake_run(cfg_, **kw):
        calls.append(kw)
        return ClaudeResult(True, "success", "s1", 0.42, 12, _dossier_text().replace("text", "a — b"), 0, False,
                            kw["log_path"])

    monkeypatch.setattr(research, "run_claude", fake_run)
    job, _ = _mk_job(conn, cfg)
    p = research.ensure_dossier(cfg, job, conn)
    assert p and p.exists() and "—" not in p.read_text(encoding="utf-8")
    assert calls[0]["tools"] == ["WebSearch", "WebFetch"] and calls[0]["model"] == "claude-sonnet-5"
    job2, _ = _mk_job(conn, cfg, url="https://job-boards.greenhouse.io/acme/jobs/4002")
    assert research.ensure_dossier(cfg, job2, conn) == p and len(calls) == 1  # second role at Acme reuses it
    run = conn.execute("SELECT stage, status, cost_usd FROM stage_runs WHERE job_id=?", (job.id,)).fetchone()
    assert tuple(run) == ("research", "succeeded", 0.42)


def test_unusable_dossier_is_not_cached(cfg, conn, monkeypatch):
    cfg.research.enabled = True
    monkeypatch.setattr(research, "run_claude", lambda cfg_, **kw: ClaudeResult(
        True, "success", "s1", 0.3, 5, _dossier_text(n_sources=2), 0, False, kw["log_path"]))
    job, _ = _mk_job(conn, cfg)
    assert research.ensure_dossier(cfg, job, conn) is None
    assert not research.dossier_path(cfg, job.company).exists()
