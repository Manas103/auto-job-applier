"""Compact project index (playbooks/LIBRARY.md), generated from playbooks/PORTFOLIO.md.

The full portfolio is ~240 KB and was being read in full by every stage, several times per run. The ideamaker
now shortlists reuse/extend candidates from this one-line-per-repo index and then searches PORTFOLIO.md for only
the candidates' full rows. Never edit LIBRARY.md by hand; it is rebuilt whenever PORTFOLIO.md is newer.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROW = re.compile(r"^\|\s*([a-z0-9][a-z0-9._-]*)\s*\|(.*?)\|?\s*$")  # cells may contain "|"; trailing pipe optional
_EXT = re.compile(r"^`([a-z0-9][a-z0-9._-]*)` was extended(.*)$")

HEADER = (
    "# LIBRARY: compact index of existing repos\n\n"
    "Generated from `playbooks/PORTFOLIO.md`; do not edit by hand. One line per repo: what it is, then [stack].\n"
    "Use it to shortlist reuse and extend candidates for a project slot, then search PORTFOLIO.md for each\n"
    "candidate's full row (measured numbers, disclosures) before writing a bullet. Numbers are deliberately\n"
    "omitted here: always take them from the full row.\n\n"
)


def _clip(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s.replace("`", "")).strip()
    if len(s) <= n:
        return s
    return s[: n - 3].rsplit(" ", 1)[0] + "..."


def build_library(portfolio_text: str) -> str:
    repos: dict[str, dict] = {}
    for line in portfolio_text.splitlines():
        m = _ROW.match(line)
        if m:
            name, rest = m.groups()
            cells = [c.strip() for c in rest.split("|")]
            stack, what = (cells + ["", ""])[:2]
            repos.setdefault(name, {"stack": stack, "what": what, "ext": []}).update(stack=stack, what=what)
            continue
        e = _EXT.match(line)
        if e and e.group(1) in repos:
            repos[e.group(1)]["ext"].append(e.group(2))
    out = [HEADER]
    for name, r in repos.items():
        line = f"- {name}: {_clip(r['what'], 150)} [{_clip(r['stack'], 60)}]"
        if r["ext"]:
            line += " (extended: " + "; ".join(_clip(x, 80) for x in r["ext"]) + ")"
        out.append(line + "\n")
    return "".join(out)


def ensure_library(playbooks_dir: Path) -> Path:
    """Rebuild LIBRARY.md when PORTFOLIO.md is newer (or LIBRARY.md is missing). Returns the LIBRARY.md path."""
    portfolio = playbooks_dir / "PORTFOLIO.md"
    library = playbooks_dir / "LIBRARY.md"
    if portfolio.exists() and (not library.exists() or library.stat().st_mtime < portfolio.stat().st_mtime):
        library.write_text(build_library(portfolio.read_text(encoding="utf-8")), encoding="utf-8")
    return library
