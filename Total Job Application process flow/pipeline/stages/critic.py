"""Critic stage: score the folder against RUBRIC.md; verified via review.json."""

from __future__ import annotations

import json
from pathlib import Path

from .. import index as index_mod
from ..config import Config
from ..models import Failure, Job, Stage
from . import StageSpec, common_vars, format_fix_prompt, render_prompt


def _exemplars_text(role_folder: Path, cfg: Config, job: Job) -> str:
    meta = index_mod.load_meta(role_folder)
    tags = [str(t) for t in meta.get("domain_tags", [])] or index_mod.guess_tags(job.title, job.jd_text)
    rows = index_mod.nearest_exemplars(cfg, tags, exclude_folder=role_folder.name, k=2)
    return "; ".join(str(cfg.roles_dir / r.folder) for r in rows) or "(none)"


def build_prompt(job: Job, cfg: Config, role_folder: Path, ctx: dict) -> str:
    v = common_vars(job, cfg, role_folder, "critic")
    v["EXEMPLARS"] = _exemplars_text(role_folder, cfg, job)
    return render_prompt("critic.md", **v)


def load_review(role_folder: Path) -> dict | None:
    p = role_folder / "review.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def check(role_folder: Path, job: Job, cfg: Config) -> list[Failure]:
    out: list[Failure] = []
    if not (role_folder / "review.md").exists():
        out.append(Failure("missing_file", "review.md", None, "review.md missing", "write the review"))
    rev = load_review(role_folder)
    if rev is None:
        out.append(Failure("missing_file", "review.json", None, "review.json missing or invalid", "write valid review.json"))
        return out
    verdict = rev.get("verdict")
    if verdict not in ("pass", "revise"):
        out.append(Failure("review_invalid", "review.json", None, f"verdict {verdict!r} must be pass|revise", "fix"))
        return out
    if verdict == "revise":
        fixes = rev.get("fixes") or []
        highs = [f for f in fixes if isinstance(f, dict) and f.get("severity") == "high"]
        listing = "; ".join(f"{f.get('file')}: {f.get('issue')} -> {f.get('fix')}" for f in (highs or fixes)[:12]) or "no fixes listed"
        out.append(Failure("review_revise", "review.json", None, f"critic verdict is revise ({len(highs)} high): {listing}",
                           "apply the fixes listed in review.json to the deliverables, re-verify, then rewrite review.md and review.json"))
    return out


def fix_prompt(failures: list[Failure], role_folder: Path, job: Job, cfg: Config) -> str:
    extra = ("You are now also the editor: apply the fixes from review.json to the deliverables in the role folder "
             f"({role_folder}), recompile any edited .tex, then re-score against the rubric and rewrite review.md and review.json. "
             "Do not lower the rubric to pass; fix the work.")
    return format_fix_prompt(failures, extra=extra)


SPEC = StageSpec(stage=Stage.CRITIC, exclusive=False, build_prompt=build_prompt, check=check, fix_prompt=fix_prompt)
