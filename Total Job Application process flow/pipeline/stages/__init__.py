"""Stage registry: each stage is a StageSpec with a prompt builder, a deterministic check, and a fix-prompt builder."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Callable

from ..config import Config
from ..models import Failure, Job, Stage
from ..paths import PROJECT_ROOT, to_wsl_path
from ..roles import role_slug
from .. import util

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def render_prompt(template_name: str, **vars: str) -> str:
    tpl = (PROMPTS_DIR / template_name).read_text(encoding="utf-8")
    return Template(tpl).safe_substitute(**vars)


def common_vars(job: Job, cfg: Config, role_folder: Path, stage: str) -> dict[str, str]:
    import sys
    from ..index import write_used_identities
    from ..library import ensure_library
    from ..research import current_dossier
    pb = cfg.playbooks_dir
    dossier = current_dossier(cfg, job.company) if job.company else None
    job_json = json.dumps({
        "id": job.id, "company": job.company, "title": job.title, "url": job.url, "locations": job.locations,
        "category": job.category, "pay": job.pay, "sponsorship": job.sponsorship, "terms": job.terms,
        "flags": job.flags, "date_posted": job.date_posted,
    }, ensure_ascii=False)
    return {
        "STAGE": stage,
        "PLAYBOOK": str(pb / {"ideamaker": "IDEAMAKER.md", "builder": "BUILDER.md", "reconcile": "RECONCILE.md",
                              "critic": "CRITIC.md"}[stage]),
        "ASSETS": str(pb / "ASSETS.md"),
        "PORTFOLIO": str(pb / "PORTFOLIO.md"),
        "LIBRARY": str(ensure_library(pb)),
        "RUBRIC": str(pb / "RUBRIC.md"),
        "IDEAMAKER_PLAYBOOK": str(pb / "IDEAMAKER.md"),
        "TEMPLATES_DIR": str(pb / "templates"),
        "INDEX": str(cfg.roles_dir / "INDEX.md"),
        "USED": str(write_used_identities(cfg)),
        "DOSSIER": str(dossier) if dossier else "(none available for this run; do the company research yourself)",
        "PYTHON": sys.executable.replace("pythonw.exe", "python.exe"),
        "ROLE_FOLDER": str(role_folder),
        "ROLE_FOLDER_WSL": to_wsl_path(role_folder),
        "PROJECT_ROOT": str(cfg.project_root),
        "PROJECT_ROOT_WSL": to_wsl_path(cfg.project_root),
        "PROJECTS_DIR": str(cfg.projects_dir),
        "PROJECTS_DIR_WSL": to_wsl_path(cfg.projects_dir),
        "JOB": job_json,
        "JD_FILE": str(role_folder / "jd.txt"),
        "SLUG": role_slug(job),
        "TODAY": util.today().isoformat(),
        "GRADUATION": "May 2028",
        "WSL_DISTRO": str(cfg.wsl.distro),
        "CUDA_BIN": str(cfg.wsl.cuda_bin),
        "VCVARS": str(cfg.msvc.get("vcvars") or ""),
        "PDFLATEX": str(cfg.verify.pdflatex),
        "PDFINFO": str(cfg.verify.pdfinfo),
        "COMPANY": job.company,
        "TITLE": job.title,
    }


def format_fix_prompt(failures: list[Failure], extra: str = "") -> str:
    lines = "\n".join("- " + f.format() for f in failures)
    return render_prompt("fix_round.md", FAILURES=lines, EXTRA=extra)


@dataclass(frozen=True)
class StageSpec:
    stage: Stage
    exclusive: bool
    build_prompt: Callable[[Job, Config, Path, dict], str]
    check: Callable[[Path, Job, Config], list[Failure]]
    fix_prompt: Callable[[list[Failure], Path, Job, Config], str]
    on_success: Callable[[Path, Job, Config], None] | None = None

    @property
    def name(self) -> str:
        return self.stage.value


def get_specs() -> dict[Stage, StageSpec]:
    from . import builder, critic, ideamaker, reconcile
    return {
        Stage.IDEAMAKER: ideamaker.SPEC,
        Stage.BUILDER: builder.SPEC,
        Stage.RECONCILE: reconcile.SPEC,
        Stage.CRITIC: critic.SPEC,
    }


STAGE_ORDER = [Stage.IDEAMAKER, Stage.BUILDER, Stage.RECONCILE, Stage.CRITIC]

__all__ = ["StageSpec", "get_specs", "STAGE_ORDER", "render_prompt", "common_vars", "format_fix_prompt", "PROJECT_ROOT"]
