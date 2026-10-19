"""Verifier: pure scans, expected files, meta.json; real pdflatex compile marked real_latex."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from pipeline import verify
from pipeline.latex import compile_tex, scan_log_warnings, tool_available

TODAY = date(2026, 8, 22)


def test_strip_tex_comments():
    lines = verify.strip_tex_comments("a %comment\n%----HEADER----\nb \\% not comment % yes\n")
    assert lines == ["a ", "", "b \\% not comment "]


def test_scan_em_dashes_tex_and_md():
    tex = "%---- banner ----\nA \u2014 B\nC --- D\nJan. 2025 -- Feb. 2026\n"
    codes = [f.code for f in verify.scan_em_dashes(tex, "x.tex", is_tex=True)]
    assert codes == ["em_dash_unicode", "em_dash_tex"]
    md = "# T\n\n---\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode --- ok\n```\nprose --- bad\nfoo \u2014 bar\n"
    fails = verify.scan_em_dashes(md, "x.md", is_tex=False)
    assert [(f.code, f.line) for f in fails] == [("em_dash_tex", 12), ("em_dash_unicode", 13)]
    assert verify.scan_em_dashes("every `---` hit is a markdown rule", "y.md", is_tex=False) == []


def test_scan_dates_rules():
    tex = ("{B.S. Computer Science; GPA: 3.50}{Aug. 2024 -- May 2028}\n"
           "{Project}{Jan. 2031}\n"
           "{Other}{Aug. 2026}\n"
           "{Fine}{Jun. 2026}\n"
           "{OSS}{Feb. 2025 -- Present}\n"
           "bare year 2029 here\n"
           "CUDA 12.4 and $2027 and 2027-08 and v2030.1\n"
           "{Grad}{Sep. 2024 -- Jun. 2028} with Graduating Jun. 2028\n")
    fails = verify.scan_dates(tex, "x.tex", TODAY, {"May 2028"})
    assert [(f.code, f.line) for f in fails] == [("future_date", 2), ("current_month_date", 3), ("future_date", 6)]
    relaxed = verify.scan_dates(tex, "ideal_candidate_full_x.tex", TODAY, {"May 2028"}, strict_current_month=False)
    assert [(f.code, f.line) for f in relaxed] == [("future_date", 2), ("future_date", 6)]


def test_build_month_allowed_in_current_month(cfg, tmp_path, fixtures_dir):
    """A built project's real build month (from build_report.json) passes even when it is the current month."""
    import json
    dst = tmp_path / "r"
    shutil.copytree(fixtures_dir / "tex" / "good", dst)
    tex = dst / "ideal_candidate_realistic_t.tex"
    tex.write_text(tex.read_text(encoding="utf-8").replace("{C++, Python}{Feb. 2026}", "{C++, Python}{Aug. 2026}"), encoding="utf-8")
    res = verify.verify_role_folder(dst, cfg, today=TODAY, compile=False)
    assert any(f.code == "current_month_date" for f in res.failures)
    (dst / "build_report.json").write_text(json.dumps({"status": "ok", "month_built": "Aug. 2026", "projects": []}), encoding="utf-8")
    res = verify.verify_role_folder(dst, cfg, today=TODAY, compile=False)
    assert not any(f.code == "current_month_date" for f in res.failures), verify.format_failures(res.failures)


def test_scan_sources_section():
    assert verify.scan_sources_section("# x\nno sources", "a.md", 3)[0].code == "sources_section"
    two = "# x\n\n## Sources\n\n1. https://a.com\n2. https://b.com\n"
    assert verify.scan_sources_section(two, "a.md", 3)[0].message.startswith("Sources section has 2")
    three = two + "3. https://c.com\n"
    assert verify.scan_sources_section(three, "a.md", 3) == []
    nested = "# x\n\n### Sources\n- https://a.com\n- https://b.com\n- https://c.com\n\n## Next\nhttps://d.com\n"
    assert verify.scan_sources_section(nested, "a.md", 3) == []
    capped = "# x\n\n## Sources\nhttps://a.com\n\n## Next\nhttps://b.com https://c.com\n"
    assert verify.scan_sources_section(capped, "a.md", 3)[0].message.startswith("Sources section has 1")


def test_expected_files_and_infer_slug(fixtures_dir):
    assert verify.expected_files("gea")["no_brainer"] == "no_brainer_hire_gea.md"
    assert verify.infer_slug(fixtures_dir / "tex" / "good") == "t"
    assert verify.infer_slug(fixtures_dir / "tex") is None


def _bad_folder(tmp_path, fixtures_dir) -> Path:
    dst = tmp_path / "bad"
    shutil.copytree(fixtures_dir / "tex" / "bad", dst)
    p = dst / "ideal_candidate_full_t.tex"
    p.write_text(p.read_text(encoding="utf-8").replace("{{CURRENT_MONTH}}", "Aug. 2026"), encoding="utf-8")
    return dst


def test_verify_bad_folder_without_compile(cfg, tmp_path, fixtures_dir):
    res = verify.verify_role_folder(_bad_folder(tmp_path, fixtures_dir), cfg, today=TODAY, compile=False)
    codes = sorted({f.code for f in res.failures})
    assert not res.ok
    assert {"missing_file", "em_dash_unicode", "em_dash_tex", "future_date", "sources_section"} <= set(codes)
    missing = {f.file for f in res.failures if f.code == "missing_file"}
    assert missing == {"ideal_candidate_realistic_t.tex", "no_brainer_hire_t.md", "meta.json", "experience_descriptions_t.md"}
    assert any(f.code == "future_date" and "Jan. 2031" in f.message for f in res.failures)
    # Full-ideal file: current month is allowed (a fictional internship may end this month).
    assert not any(f.code == "current_month_date" for f in res.failures)
    assert sum(1 for f in res.failures if f.code == "sources_section") == 2
    text = verify.format_failures(res.failures)
    assert "[em_dash_unicode] ideal_candidate_full_t.tex:" in text


def test_verify_good_folder_without_compile(cfg, fixtures_dir):
    res = verify.verify_role_folder(fixtures_dir / "tex" / "good", cfg, today=TODAY, compile=False)
    assert res.ok, verify.format_failures(res.failures)


def test_check_meta(tmp_path):
    (tmp_path / "meta.json").write_text('{"company": "x", "title": "t", "slug": "x", "projects": [{"title": "p"}]}', encoding="utf-8")
    fails = verify.check_meta(tmp_path)
    assert {f.message for f in fails} >= {"projects[0] lacks 'stack'", "projects[0] lacks 'month'", "projects[0] lacks 'claims'"}
    (tmp_path / "meta.json").write_text("{not json", encoding="utf-8")
    assert verify.check_meta(tmp_path)[0].code == "meta_invalid"


def test_scan_log_warnings():
    log = "Overfull \\hbox (12.3pt too wide) in paragraph at lines 101--103\nfoo\nUnderfull \\vbox (badness 10000) has occurred while \\output is active\n"
    w = scan_log_warnings(log)
    assert [x[1] for x in w] == [101, None] and w[0][0].startswith("Overfull")


# ----------------------------------------------------------------------------- real pdflatex

def _real_cfg(cfg):
    import json
    from pipeline.paths import PROJECT_ROOT
    real = json.loads((PROJECT_ROOT / "config.json" if (PROJECT_ROOT / "config.json").exists() else PROJECT_ROOT / "config.example.json").read_text(encoding="utf-8"))["verify"]
    cfg.verify.pdflatex = real["pdflatex"]
    cfg.verify.pdfinfo = real["pdfinfo"]
    cfg.verify.compile = True
    if not (tool_available(cfg.verify.pdflatex) and tool_available(cfg.verify.pdfinfo)):
        pytest.skip("pdflatex/pdfinfo not available")
    return cfg


@pytest.mark.real_latex
def test_compile_minimal_in_path_with_spaces(cfg, tmp_path, fixtures_dir):
    cfg = _real_cfg(cfg)
    d = tmp_path / "dir with spaces"
    d.mkdir()
    shutil.copy(fixtures_dir / "tex" / "minimal.tex", d / "minimal.tex")
    r = compile_tex(d / "minimal.tex", cfg)
    assert r.ok and r.pages == 1 and r.warnings == [], r.log_tail


@pytest.mark.real_latex
def test_verify_good_folder_real(cfg, tmp_path, fixtures_dir):
    cfg = _real_cfg(cfg)
    dst = tmp_path / "good"
    shutil.copytree(fixtures_dir / "tex" / "good", dst)
    res = verify.verify_role_folder(dst, cfg, today=TODAY)
    assert res.ok, verify.format_failures(res.failures)
    assert res.pages == {"ideal_candidate_full_t.tex": 1, "ideal_candidate_realistic_t.tex": 1}


@pytest.mark.real_latex
def test_verify_bad_folder_real_reports_page_count_and_box_warning(cfg, tmp_path, fixtures_dir):
    cfg = _real_cfg(cfg)
    dst = _bad_folder(tmp_path, fixtures_dir)
    # Add an unbreakable line to force an overfull hbox.
    p = dst / "ideal_candidate_full_t.tex"
    p.write_text(p.read_text(encoding="utf-8").replace("\\section{Skills}", "\\section{Skills}\n" + "A" * 140 + "\n", 1), encoding="utf-8")
    res = verify.verify_role_folder(dst, cfg, today=TODAY)
    codes = {f.code for f in res.failures}
    assert "page_count" in codes and "box_warning" in codes
    pc = next(f for f in res.failures if f.code == "page_count")
    assert "2 pages" in pc.message


@pytest.mark.real_latex
def test_compile_error_reports_line(cfg, tmp_path):
    cfg = _real_cfg(cfg)
    p = tmp_path / "broken.tex"
    p.write_text("\\documentclass{article}\n\\begin{document}\nHello \\undefinedmacro\n\\end{document}\n", encoding="utf-8")
    r = compile_tex(p, cfg)
    assert not r.ok and r.error_line == 3 and "undefinedmacro" in r.log_tail


def test_experience_blocks_scanner(fixtures_dir):
    good = (fixtures_dir / "tex" / "good" / "experience_descriptions_t.md").read_text(encoding="utf-8")
    assert verify.scan_experience_blocks(good, "x.md") == []
    too_long = good.replace("I owned the search engine", "I owned the search engine " + "and more " * 80, 1)
    codes = [f.code for f in verify.scan_experience_blocks(too_long, "x.md")]
    assert "experience_block_too_long" in codes
    bullets = good.replace("I owned the search engine", "- I owned the search engine", 1)
    assert "experience_block_markup" in [f.code for f in verify.scan_experience_blocks(bullets, "x.md")]
    assert [f.code for f in verify.scan_experience_blocks("# nothing here\n", "x.md")] == ["experience_no_blocks"]
    # Unlabeled fences (a code sample in Notes) are ignored.
    assert verify.scan_experience_blocks("Example:\n```\nx\n```\nShort (under 600 characters):\n```\nok\n```\n", "x.md") == []


def test_experience_file_checked_in_folder(cfg, tmp_path, fixtures_dir):
    import shutil
    dst = tmp_path / "role"
    shutil.copytree(fixtures_dir / "tex" / "good", dst)
    p = dst / "experience_descriptions_t.md"
    p.write_text(p.read_text(encoding="utf-8").replace("Short (under 600 characters):\n```\nI owned",
                                                       "Short (under 600 characters):\n```\nI owned " + "x" * 700, 1), encoding="utf-8")
    res = verify.verify_role_folder(dst, cfg, today=TODAY, compile=False)
    assert "experience_block_too_long" in {f.code for f in res.failures}



@pytest.mark.real_latex
def test_verify_lite_real_compiles_only_the_realistic_resume(cfg, tmp_path, fixtures_dir):
    cfg = _real_cfg(cfg)
    dst = tmp_path / "lite folder"
    dst.mkdir()
    good = fixtures_dir / "tex" / "good"
    for name in ("ideal_candidate_realistic_t.tex", "experience_descriptions_t.md", "meta.json"):
        shutil.copy(good / name, dst / name)
    res = verify.verify_lite(dst, cfg, "t", today=TODAY)
    assert res.ok, verify.format_failures(res.failures)
    assert res.pages == {"ideal_candidate_realistic_t.tex": 1}
    assert (dst / "ideal_candidate_realistic_t.pdf").exists()
