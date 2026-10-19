"""Ideamaker stage: produce the five deliverables plus meta.json; verified deterministically."""

from __future__ import annotations

import json
from pathlib import Path

from .. import index as index_mod
from ..config import Config
from ..models import Failure, Job, Stage
from ..roles import role_slug
from ..verify import verify_role_folder
from . import StageSpec, common_vars, format_fix_prompt, render_prompt


def _exemplars_text(job: Job, cfg: Config, role_folder: Path) -> str:
    tags = index_mod.guess_tags(job.title, job.jd_text)
    rows = index_mod.nearest_exemplars(cfg, tags, exclude_folder=role_folder.name, k=2)
    if not rows:
        return "(none yet)"
    return "; ".join(f"{cfg.roles_dir / r.folder} (tags: {', '.join(r.tags)})" for r in rows)


def build_prompt(job: Job, cfg: Config, role_folder: Path, ctx: dict) -> str:
    v = common_vars(job, cfg, role_folder, "ideamaker")
    v["EXEMPLARS"] = _exemplars_text(job, cfg, role_folder)
    return render_prompt("ideamaker.md", **v)


def check(role_folder: Path, job: Job, cfg: Config) -> list[Failure]:
    res = verify_role_folder(role_folder, cfg, slug=role_slug(job))
    return list(res.failures)


def fix_prompt(failures: list[Failure], role_folder: Path, job: Job, cfg: Config) -> str:
    return format_fix_prompt(failures, extra=f"Working folder: {role_folder}. Slug: {role_slug(job)}.")


def on_success(role_folder: Path, job: Job, cfg: Config) -> None:
    """Deterministically ensure the INDEX row exists (the model is also asked to, but this is the guarantee)."""
    meta = index_mod.load_meta(role_folder)
    if not meta:
        return
    row = index_mod.row_from_meta(role_folder.name, meta)
    if not row.company:
        row.company = job.company
    if not row.role:
        row.role = job.title
    index_mod.append_row(cfg, row)


SPEC = StageSpec(stage=Stage.IDEAMAKER, exclusive=False, build_prompt=build_prompt, check=check,
                 fix_prompt=fix_prompt, on_success=on_success)
