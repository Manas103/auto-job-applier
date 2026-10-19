"""Reconcile stage: measured truth flows into the resume; verified by re-running the verifier and claim checks."""

from __future__ import annotations

import json
from pathlib import Path

from ..config import Config
from ..models import Failure, Job, Stage
from ..roles import role_slug
from ..verify import verify_role_folder
from . import StageSpec, common_vars, format_fix_prompt, render_prompt


def build_prompt(job: Job, cfg: Config, role_folder: Path, ctx: dict) -> str:
    v = common_vars(job, cfg, role_folder, "reconcile")
    return render_prompt("reconcile.md", **v)


def unmet_claims(role_folder: Path) -> list[tuple[str, str]]:
    """(project title, claim text) for every benchmark that did not meet its claim."""
    p = role_folder / "build_report.json"
    if not p.exists():
        return []
    try:
        rep = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    out = []
    for pr in rep.get("projects", []):
        if not isinstance(pr, dict):
            continue
        for b in pr.get("benchmarks") or []:
            if isinstance(b, dict) and b.get("meets_claim") is False and b.get("claim"):
                out.append((str(pr.get("title", "")), str(b["claim"])))
    return out


def check(role_folder: Path, job: Job, cfg: Config) -> list[Failure]:
    out: list[Failure] = []
    if not (role_folder / "reconciliation.md").exists():
        out.append(Failure("missing_file", "reconciliation.md", None, "reconciliation.md missing", "write the before/after table"))
    res = verify_role_folder(role_folder, cfg, slug=role_slug(job))
    out.extend(res.failures)
    slug = role_slug(job)
    tex_files = sorted(role_folder.glob(f"ideal_candidate_realistic_{slug}*.tex"))
    texts = {t.name: t.read_text(encoding="utf-8", errors="replace") for t in tex_files}
    for title, claim in unmet_claims(role_folder):
        for name, text in texts.items():
            if claim and claim in text:
                out.append(Failure("unreconciled_claim", name, None, f"'{claim}' ({title}) still appears although measured below the claim",
                                   "rewrite the bullet to the measured value from build_report.json"))
    return out


def fix_prompt(failures: list[Failure], role_folder: Path, job: Job, cfg: Config) -> str:
    return format_fix_prompt(failures, extra=f"Role folder: {role_folder}. Measurements: {role_folder / 'build_report.json'}.")


SPEC = StageSpec(stage=Stage.RECONCILE, exclusive=False, build_prompt=build_prompt, check=check, fix_prompt=fix_prompt)
