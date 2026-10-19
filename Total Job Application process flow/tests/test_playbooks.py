"""Playbooks, templates, prompts, and the index: present, consistent, and free of em dashes."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from pipeline import index as index_mod
from pipeline.latex import compile_tex, tool_available
from pipeline.paths import PROJECT_ROOT

PLAYBOOKS = PROJECT_ROOT / "playbooks"
PROMPTS = PROJECT_ROOT / "pipeline" / "stages" / "prompts"


def _no_em_dash(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "\u2014" not in text, f"em dash in {path.name}"
    for i, line in enumerate(text.splitlines(), start=1):
        line = re.sub(r"`[^`]*`", "", line)
        if "---" in line and not line.strip().startswith("---") and not re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", line) and "--enable" not in line:
            raise AssertionError(f"'---' in {path.name}:{i}")


@pytest.mark.parametrize("name", ["IDEAMAKER.md", "RUBRIC.md", "ASSETS.md", "PORTFOLIO.md", "LIBRARY.md", "BUILDER.md", "RECONCILE.md", "CRITIC.md"])
def test_playbooks_exist_and_clean(name):
    p = PLAYBOOKS / name
    assert p.exists() and p.stat().st_size > (500 if name == "LIBRARY.md" else 2000)
    _no_em_dash(p)


def test_rubric_and_assets_content():
    rubric = (PLAYBOOKS / "RUBRIC.md").read_text(encoding="utf-8")
    assert re.search(r"^Version: \d+\.\d+", rubric, re.M) and "## Changelog" in rubric
    assert rubric.count("HARD") >= 15
    assets = (PLAYBOOKS / "ASSETS.md").read_text(encoding="utf-8")
    portfolio = (PLAYBOOKS / "PORTFOLIO.md").read_text(encoding="utf-8")
    assert len(assets) < 40_000, "ASSETS.md holds only the facts about Alex; the portfolio lives in PORTFOLIO.md"
    assert re.search(r"^\| [a-z0-9][a-z0-9._-]* \|", portfolio, re.M), "PORTFOLIO.md needs a repo table"
    assert "## 1. Fixed facts" in assets and "| Name |" in assets


def test_prompts_and_templates():
    for name in ["ideamaker.md", "builder.md", "reconcile.md", "critic.md", "fix_round.md"]:
        p = PROMPTS / name
        assert p.exists()
        _no_em_dash(p)
        text = p.read_text(encoding="utf-8")
        assert "PIPELINE_RESULT" in text
        # Every $placeholder uses braces when followed by an underscore.
        assert not re.search(r"\$[A-Z]+_[a-z]", text), name
    for t in ["full_ideal_template.tex", "realistic_template.tex"]:
        p = PLAYBOOKS / "templates" / t
        text = p.read_text(encoding="utf-8")
        assert "\\bfseries" in text and "[T1]{fontenc}" in text and "hidelinks" in text and "\\scshape" not in text


def test_root_claude_and_readme():
    for name in ["CLAUDE.md", "README.md"]:
        _no_em_dash(PROJECT_ROOT / name)
    assert "playbooks" in (PROJECT_ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def test_index_parses_ten_rows():
    if not (PROJECT_ROOT / "roles" / "INDEX.md").exists():
        pytest.skip("no roles/INDEX.md yet (it is built from your own role folders)")
    rows = index_mod.parse_rows((PROJECT_ROOT / "roles" / "INDEX.md").read_text(encoding="utf-8"))
    assert len(rows) >= 10
    names = [r.name for r in rows]
    assert "Mira Kaplan" in names and len(set(names)) == len(names)
    folders = {r.folder for r in rows}
    for f in folders:
        assert (PROJECT_ROOT / "roles" / f).exists(), f


def test_index_exemplars_and_append(cfg):
    rows = index_mod.nearest_exemplars(cfg, ["trading", "cpp"], k=2)
    trading = {r.folder for r in index_mod.read_rows(cfg) if "trading" in r.tags}
    assert rows and {r.folder for r in rows} <= trading
    assert "embedded" in index_mod.nearest_exemplars(cfg, ["embedded"], k=1)[0].tags
    row = index_mod.IndexRow("New_Role", "New", "Intern", "Summer 2027", ["backend"], "Pat Doe", "Some U", "P1 (Jan. 2026)", "(not built yet)")
    assert index_mod.append_row(cfg, row) and not index_mod.append_row(cfg, row)
    assert index_mod.has_row(cfg, "New_Role")
    assert index_mod.update_repos(cfg, "New_Role", ["repo-x", "repo-y"])
    assert next(r for r in index_mod.read_rows(cfg) if r.folder == "New_Role").repos == "repo-x; repo-y"
    assert index_mod.guess_tags("Quantitative Developer Intern", "low latency C++ trading systems")[:1] == ["trading"]


@pytest.mark.real_latex
def test_templates_compile(cfg, tmp_path):
    import json
    real = json.loads((PROJECT_ROOT / "config.json" if (PROJECT_ROOT / "config.json").exists() else PROJECT_ROOT / "config.example.json").read_text(encoding="utf-8"))["verify"]
    cfg.verify.pdflatex, cfg.verify.pdfinfo = real["pdflatex"], real["pdfinfo"]
    if not (tool_available(cfg.verify.pdflatex) and tool_available(cfg.verify.pdfinfo)):
        pytest.skip("no pdflatex")
    for t in ["full_ideal_template.tex", "realistic_template.tex"]:
        dst = tmp_path / t
        shutil.copy(PLAYBOOKS / "templates" / t, dst)
        r = compile_tex(dst, cfg)
        assert r.ok and r.pages == 1, (t, r.log_tail)


def test_library_index_covers_portfolio():
    from pipeline.library import build_library
    portfolio = (PLAYBOOKS / "PORTFOLIO.md").read_text(encoding="utf-8")
    lib = build_library(portfolio)
    repos = set(re.findall(r"^\| ([a-z0-9][a-z0-9._-]*) \|", portfolio, re.M))
    indexed = set(re.findall(r"^- ([a-z0-9][a-z0-9._-]*):", lib, re.M))
    assert repos and repos == indexed
    assert len(lib) < len(portfolio) / 3


def test_library_parser_tolerates_malformed_rows():
    from pipeline.library import build_library
    text = "\n".join([
        "| Repo | Stack | What | Numbers |",
        "|---|---|---|---|",
        "| alpha | Py | does a | 1 |",
        "| beta | Go | has a | pipe inside | 2 |",
        "| gamma | C++ | no trailing pipe | 3",
        "`alpha` was extended (Oct. 2026) with b",
    ])
    lib = build_library(text)
    for name in ("alpha", "beta", "gamma"):
        assert f"- {name}:" in lib, name
    assert "extended: (Oct. 2026) with b" in lib
    assert "- Repo:" not in lib
