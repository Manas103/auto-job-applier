"""Deterministic verification of a role folder. Pure checks; pdflatex only when compile=True."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import Config
from .latex import compile_tex, overflow_lines
from .models import Failure
from . import util as _util

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
           "oct": 10, "nov": 11, "dec": 12}
DATE_RE = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(20\d\d)\b")
YEAR_RE = re.compile(r"(?<![\w.$/-])(20\d\d)(?![\w%.-])")
URL_RE = re.compile(r"https?://[^\s)>\]]+")
EM_DASH = "—"


@dataclass
class VerifyResult:
    ok: bool
    failures: list[Failure] = field(default_factory=list)
    compiled: dict[str, str] = field(default_factory=dict)  # tex name -> pdf path
    pages: dict[str, int | None] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "failures": [f.to_dict() for f in self.failures], "compiled": self.compiled,
                "pages": self.pages}


def expected_files(slug: str) -> dict[str, str]:
    sfx = f"_{slug}" if slug else ""
    return {
        "full_tex": f"ideal_candidate_full{sfx}.tex",
        "realistic_tex": f"ideal_candidate_realistic{sfx}.tex",
        "full_md": f"ideal_candidate_full{sfx}_breakdown.md",
        "realistic_md": f"ideal_candidate_realistic{sfx}_breakdown.md",
        "no_brainer": f"no_brainer_hire{sfx}.md",
        "experience": f"experience_descriptions{sfx}.md",
    }


EXPERIENCE_LIMITS = {"short": 600, "long": 1500}
_FENCE_RE = re.compile(r"^([^\n]*)\n```[^\n]*\n(.*?)\n```", re.S | re.M)


def scan_experience_blocks(md: str, fname: str) -> list[Failure]:
    """Every fenced block labeled Short/Long must respect its character limit; at least one pair must exist."""
    failures: list[Failure] = []
    n_blocks = 0
    for m in _FENCE_RE.finditer(md):
        label, body = m.group(1).strip().lower(), m.group(2).strip()
        kind = "short" if label.startswith("short") else "long" if label.startswith("long") else None
        if kind is None:
            continue
        n_blocks += 1
        line = md.count("\n", 0, m.start(2)) + 1
        if not body:
            failures.append(Failure("experience_block_empty", fname, line, f"{kind} block is empty", "write the description"))
        elif len(body) > EXPERIENCE_LIMITS[kind]:
            failures.append(Failure("experience_block_too_long", fname, line,
                                    f"{kind} block is {len(body)} characters (limit {EXPERIENCE_LIMITS[kind]})",
                                    "shorten it; application forms cut off longer text"))
        if re.search(r"^\s*[-*]\s|\\[a-zA-Z]+\{|\*\*", body, re.M):
            failures.append(Failure("experience_block_markup", fname, line, f"{kind} block contains bullets, markdown, or LaTeX",
                                    "plain prose only; forms render it verbatim"))
    if n_blocks == 0:
        failures.append(Failure("experience_no_blocks", fname, None, "no Short/Long fenced blocks found",
                                "follow the format in IDEAMAKER.md, experience descriptions section"))
    return failures


def infer_slug(folder: Path) -> str | None:
    if (folder / "ideal_candidate_full.tex").exists():
        return ""
    for p in sorted(folder.glob("ideal_candidate_full_*.tex")):
        m = re.match(r"ideal_candidate_full_([a-z0-9]+)\.tex$", p.name)
        if m:
            return m.group(1)
    for p in sorted(folder.glob("ideal_candidate_realistic_*.tex")):
        m = re.match(r"ideal_candidate_realistic_([a-z0-9]+)(?:_[a-z0-9]+)?\.tex$", p.name)
        if m:
            return m.group(1)
    return None


def strip_tex_comments(tex: str) -> list[str]:
    """Per line, remove from the first unescaped % to end of line."""
    out = []
    for line in tex.splitlines():
        buf = []
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "\\" and i + 1 < len(line):
                buf.append(line[i:i + 2])
                i += 2
                continue
            if ch == "%":
                break
            buf.append(ch)
            i += 1
        out.append("".join(buf))
    return out


def scan_em_dashes(text: str, fname: str, *, is_tex: bool) -> list[Failure]:
    lines = strip_tex_comments(text) if is_tex else text.splitlines()
    out: list[Failure] = []
    in_code = False
    for i, line in enumerate(lines, start=1):
        if not is_tex:
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if line.strip().startswith("---") and line.strip().strip("-") == "":
                continue  # markdown horizontal rule
            if re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", line) and "-" in line:
                continue  # table separator row
        if EM_DASH in line:
            out.append(Failure("em_dash_unicode", fname, i, "em dash (U+2014) found", "replace with a comma or restructure the sentence"))
        if not is_tex:
            line = re.sub(r"`[^`]*`", "", line)  # code spans may legitimately mention `---`
        if "---" in line and not in_code:
            out.append(Failure("em_dash_tex", fname, i, "'---' (LaTeX em dash) found in prose", "replace with a comma or restructure; '--' is fine inside date ranges"))
    return out


_GRAD_LINE = re.compile(r"GPA|Graduat|Expected|B\.S\.|Bachelor|M\.S\.|Master|BCS|Average|Availab|Coursework", re.I)
_REAL_ARTIFACT_LINE = re.compile(r"arXiv|Preprint|Present", re.I)


def scan_dates(tex: str, fname: str, today: date, allowed_future: set[str], *, strict_current_month: bool = True,
               allowed_current: set[str] | None = None) -> list[Failure]:
    """Flag dates after today and (when strict) in the current month.
    Exceptions: literals in `allowed_future` (the graduation), and any date on an education line
    (a line mentioning GPA/degree/graduation), since a graduation date is necessarily in the future."""
    out: list[Failure] = []
    allowed_norm = {re.sub(r"\s+", " ", a.strip().lower().replace(".", "")) for a in allowed_future}
    allowed_cur = {re.sub(r"\s+", " ", a.strip().lower().replace(".", "")) for a in (allowed_current or set())}
    for i, line in enumerate(strip_tex_comments(tex), start=1):
        education_line = bool(_GRAD_LINE.search(line))
        real_artifact = bool(_REAL_ARTIFACT_LINE.search(line))
        consumed: list[tuple[int, int]] = []
        for m in DATE_RE.finditer(line):
            consumed.append(m.span())
            mon_txt = m.group(1).lower()
            mon = _MONTHS.get("sept" if mon_txt.startswith("sept") else mon_txt[:3], 1)
            year = int(m.group(2))
            literal = re.sub(r"\s+", " ", m.group(0).lower().replace(".", ""))
            if literal in allowed_norm:
                continue
            if (year, mon) > (today.year, today.month):
                if education_line:
                    continue
                out.append(Failure("future_date", fname, i, f"'{m.group(0)}' is after today ({today.isoformat()})",
                                   "date every item in the past; real artifacts keep real dates"))
            elif ((year, mon) == (today.year, today.month) and strict_current_month and not education_line and not real_artifact
                  and literal not in allowed_cur):
                out.append(Failure("current_month_date", fname, i, f"'{m.group(0)}' is the current month",
                                   "move the month earlier; nothing is dated in the current month"))
        for m in YEAR_RE.finditer(line):
            if any(a <= m.start() < b for a, b in consumed):
                continue
            year = int(m.group(1))
            if year > today.year and not education_line and str(year) not in {a.split()[-1] for a in allowed_norm}:
                out.append(Failure("future_date", fname, i, f"year {year} is after today", "use a past date"))
    return out


def scan_sources_section(md: str, fname: str, min_urls: int) -> list[Failure]:
    m = re.search(r"^#{1,3}\s*(?:\d+\.\s*)?Sources\b.*$", md, re.M | re.I)
    if not m:
        return [Failure("sources_section", fname, None, "no '## Sources' heading", f"add a Sources section with at least {min_urls} URLs")]
    tail = md[m.end():]
    level = len(m.group(0)) - len(m.group(0).lstrip("#"))
    nxt = re.search(rf"^#{{1,{level}}}\s", tail, re.M)
    if nxt:
        tail = tail[:nxt.start()]
    urls = URL_RE.findall(tail)
    if len(urls) < min_urls:
        return [Failure("sources_section", fname, None, f"Sources section has {len(urls)} URLs (minimum {min_urls})",
                        f"cite at least {min_urls} sources with URLs")]
    return []


def check_meta(folder: Path) -> list[Failure]:
    p = folder / "meta.json"
    if not p.exists():
        return [Failure("missing_file", "meta.json", None, "meta.json missing", "write meta.json per IDEAMAKER.md section 9")]
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [Failure("meta_invalid", "meta.json", None, f"meta.json is not valid JSON: {e}", "fix the JSON")]
    out: list[Failure] = []
    for k in ("company", "title", "slug", "projects"):
        if k not in meta:
            out.append(Failure("meta_invalid", "meta.json", None, f"meta.json lacks '{k}'", "add the field"))
    projects = meta.get("projects") or []
    if not isinstance(projects, list) or not projects:
        out.append(Failure("meta_invalid", "meta.json", None, "meta.json has no projects", "list every project on the realistic resume"))
    else:
        for i, pr in enumerate(projects):
            for k in ("title", "stack", "month", "claims"):
                if not isinstance(pr, dict) or k not in pr:
                    out.append(Failure("meta_invalid", "meta.json", None, f"projects[{i}] lacks '{k}'", "add the field"))
            if isinstance(pr, dict) and pr.get("relationship") not in (None, "new", "reuse", "extend"):
                out.append(Failure("meta_invalid", "meta.json", None, f"projects[{i}].relationship must be new|reuse|extend", "fix the value"))
    return out


def verify_role_folder(folder: Path, cfg: Config, *, today: date | None = None, slug: str | None = None,
                       compile: bool | None = None, check_meta_json: bool = True) -> VerifyResult:
    folder = Path(folder)
    today = today or _util.today()
    do_compile = bool(cfg.verify.get("compile", True)) if compile is None else compile
    min_urls = int(cfg.verify.min_source_urls)
    allowed_future = set(cfg.verify.allowed_future_dates or [])
    failures: list[Failure] = []
    res = VerifyResult(ok=True)

    # A built project legitimately carries its real build month, even when that is the current month.
    allowed_current: set[str] = set()
    br = folder / "build_report.json"
    if br.exists():
        try:
            mb = json.loads(br.read_text(encoding="utf-8")).get("month_built")
            if mb:
                allowed_current.add(str(mb))
        except (json.JSONDecodeError, OSError):
            pass
    slug = infer_slug(folder) if slug is None else slug
    if slug is None:
        failures.append(Failure("missing_file", None, None, "no ideal_candidate_full_<slug>.tex found", "produce the six deliverables"))
        res.failures = failures
        res.ok = False
        return res
    exp = expected_files(slug)
    for name in exp.values():
        p = folder / name
        if not p.exists() or p.stat().st_size == 0:
            failures.append(Failure("missing_file", name, None, f"{name} is missing or empty", "produce it with exactly this filename"))

    for tex in sorted(folder.glob("*.tex")):
        text = tex.read_text(encoding="utf-8", errors="replace")
        failures.extend(scan_em_dashes(text, tex.name, is_tex=True))
        failures.extend(scan_dates(text, tex.name, today, allowed_future, strict_current_month="realistic" in tex.name,
                                   allowed_current=allowed_current))
        if do_compile:
            cr = compile_tex(tex, cfg)
            if not cr.ok:
                failures.append(Failure("compile_error", tex.name, cr.error_line, "pdflatex failed: " + (cr.log_tail[-600:] or cr.stderr[-300:]),
                                        "fix the LaTeX error and recompile"))
                continue
            res.compiled[tex.name] = str(cr.pdf)
            res.pages[tex.name] = cr.pages
            if cr.pages != 1:
                over = overflow_lines(cr.pdf, cfg) if cr.pages and cr.pages > 1 and cr.pdf else None
                hint = f" ({over} lines of text spill past page 1)" if over else ""
                failures.append(Failure("page_count", tex.name, None, f"compiles to {cr.pages} pages, must be exactly 1{hint}",
                                        "tighten content or spacing (never push topmargin past -1.0in)"))
            for w in cr.warnings:
                lm = re.search(r"at lines (\d+)", w)
                failures.append(Failure("box_warning", tex.name, int(lm.group(1)) if lm else None, w[:160],
                                        "reword the line so it breaks cleanly or adjust the layout"))

    for key in ("full_md", "realistic_md", "no_brainer"):
        p = folder / exp[key]
        if not p.exists():
            continue
        md = p.read_text(encoding="utf-8", errors="replace")
        failures.extend(scan_em_dashes(md, p.name, is_tex=False))
        failures.extend(scan_sources_section(md, p.name, min_urls))

    p = folder / exp["experience"]
    if p.exists():
        md = p.read_text(encoding="utf-8", errors="replace")
        failures.extend(scan_em_dashes(md, p.name, is_tex=False))
        failures.extend(scan_experience_blocks(md, p.name))

    for extra in sorted(folder.glob("*.md")):
        if extra.name in exp.values() or extra.name in ("README.md",):
            continue
        failures.extend(scan_em_dashes(extra.read_text(encoding="utf-8", errors="replace"), extra.name, is_tex=False))

    if check_meta_json:
        failures.extend(check_meta(folder))

    res.failures = failures
    res.ok = not failures
    return res


def verify_lite(folder: Path, cfg: Config, slug: str, *, today: date | None = None,
                compile: bool | None = None) -> VerifyResult:
    """The light/archetype lanes' deliverable: one realistic resume (1 page, no box warnings, no em dashes, valid
    dates), the experience file, and meta.json. No full ideal, breakdowns, no-brainer or Sources (those lanes
    never write them)."""
    folder = Path(folder)
    today = today or _util.today()
    do_compile = bool(cfg.verify.get("compile", True)) if compile is None else compile
    allowed_future = set(cfg.verify.allowed_future_dates or [])
    exp = expected_files(slug)
    res = VerifyResult(ok=True)
    failures: list[Failure] = []
    for key in ("realistic_tex", "experience"):
        p = folder / exp[key]
        if not p.exists() or p.stat().st_size == 0:
            failures.append(Failure("missing_file", exp[key], None, f"{exp[key]} is missing or empty",
                                    "produce it with exactly this filename"))
    tex = folder / exp["realistic_tex"]
    if tex.exists():
        text = tex.read_text(encoding="utf-8", errors="replace")
        failures.extend(scan_em_dashes(text, tex.name, is_tex=True))
        failures.extend(scan_dates(text, tex.name, today, allowed_future))
        if do_compile:
            cr = compile_tex(tex, cfg)
            if not cr.ok:
                failures.append(Failure("compile_error", tex.name, cr.error_line,
                                        "pdflatex failed: " + (cr.log_tail[-600:] or cr.stderr[-300:]),
                                        "fix the LaTeX error and recompile"))
            else:
                res.compiled[tex.name] = str(cr.pdf)
                res.pages[tex.name] = cr.pages
                if cr.pages != 1:
                    failures.append(Failure("page_count", tex.name, None, f"compiles to {cr.pages} pages, must be exactly 1",
                                            "tighten content or spacing (never push topmargin past -1.0in)"))
                for w in cr.warnings:
                    lm = re.search(r"at lines (\d+)", w)
                    failures.append(Failure("box_warning", tex.name, int(lm.group(1)) if lm else None, w[:160],
                                            "reword the line so it breaks cleanly or adjust the layout"))
        elif not (folder / tex.with_suffix(".pdf").name).exists():
            failures.append(Failure("missing_file", tex.with_suffix(".pdf").name, None, "the realistic PDF is missing",
                                    "compile the resume"))
    p = folder / exp["experience"]
    if p.exists():
        md = p.read_text(encoding="utf-8", errors="replace")
        failures.extend(scan_em_dashes(md, p.name, is_tex=False))
        failures.extend(scan_experience_blocks(md, p.name))
    failures.extend(check_meta(folder))
    res.failures = failures
    res.ok = not failures
    return res


def format_failures(failures: list[Failure]) -> str:
    return "\n".join(f.format() for f in failures)
