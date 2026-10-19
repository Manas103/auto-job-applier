"""Experience descriptions: the copy-paste document application forms need.

The ideamaker writes it as a regular deliverable; this module holds the deterministic checks and the
backfill for role folders finished before the deliverable existed."""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import Config
from ..models import Failure, Job
from ..roles import role_slug
from ..verify import expected_files, scan_em_dashes, scan_experience_blocks
from . import common_vars, render_prompt
from .claude_runner import ClaudeResult, run_claude

log = logging.getLogger("pipeline.stages.experience")

BACKFILL_BUDGET_USD = 3.0
BACKFILL_TIMEOUT_S = 15 * 60


def experience_file(slug: str) -> str:
    return expected_files(slug)["experience"]


def check(folder: Path, slug: str) -> list[Failure]:
    name = experience_file(slug)
    p = folder / name
    if not p.exists() or p.stat().st_size == 0:
        return [Failure("missing_file", name, None, f"{name} is missing or empty", "write it per IDEAMAKER.md, experience descriptions section")]
    md = p.read_text(encoding="utf-8", errors="replace")
    return scan_em_dashes(md, name, is_tex=False) + scan_experience_blocks(md, name)


def build_backfill_prompt(job: Job, cfg: Config, folder: Path) -> str:
    vars_ = common_vars(job, cfg, folder, "ideamaker")
    vars_["EXPERIENCE_FILE"] = experience_file(role_slug(job))
    return render_prompt("experience_backfill.md", **vars_)


def backfill(cfg: Config, job: Job, folder: Path, log_path: Path, *, budget_usd: float = BACKFILL_BUDGET_USD,
             timeout_s: int = BACKFILL_TIMEOUT_S) -> tuple[ClaudeResult, list[Failure]]:
    """Run one headless pass that writes the file, then check it. The caller records cost and decides what to do
    with failures (a second pass resumes the session with the failure list)."""
    res = run_claude(cfg, prompt=build_backfill_prompt(job, cfg, folder), cwd=folder, log_path=log_path,
                     max_budget_usd=budget_usd, timeout_s=timeout_s, add_dirs=[cfg.project_root])
    return res, check(folder, role_slug(job))
