"""Builder stage: build every claimed project as a real repo; verified via build_report.json."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from .. import index as index_mod
from ..config import Config
from ..models import Failure, Job, Stage
from . import StageSpec, common_vars, format_fix_prompt, render_prompt


def build_prompt(job: Job, cfg: Config, role_folder: Path, ctx: dict) -> str:
    v = common_vars(job, cfg, role_folder, "builder")
    prompt = render_prompt("builder.md", **v)
    if cfg.get("sandbox"):
        prompt += ("\n\nSANDBOX RUN (overrides the push instructions above): this is an offline experiment. Do NOT push, "
                   "do NOT run gh repo create, and do not touch GitHub in any way (the guard blocks it). Commit locally "
                   "only. In build_report.json set each project's repo_url to \"sandbox://<repo-name>\".\n")
    return prompt


def _repo_reachable(url: str, cfg: Config) -> bool | None:
    """True/False when gh answered; None when gh is unavailable (treated as unknown, not failure)."""
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        proc = subprocess.run([gh, "repo", "view", url, "--json", "name"], capture_output=True, text=True, timeout=60,
                              creationflags=flags)
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None


def check(role_folder: Path, job: Job, cfg: Config) -> list[Failure]:
    p = role_folder / "build_report.json"
    if not p.exists():
        return [Failure("missing_file", "build_report.json", None, "build_report.json missing", "write it per BUILDER.md section 5")]
    try:
        rep = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [Failure("report_invalid", "build_report.json", None, f"not valid JSON: {e}", "fix the JSON")]
    out: list[Failure] = []
    if rep.get("status") != "ok":
        out.append(Failure("build_status", "build_report.json", None, f"status is {rep.get('status')!r}, expected 'ok'",
                           "finish every project (build_ok and tests_ok) or explain in notes and retry"))
    projects = rep.get("projects") or []
    min_projects = int(cfg.stage("builder").get("min_projects", 1))
    if len(projects) < min_projects:
        out.append(Failure("build_status", "build_report.json", None, f"{len(projects)} projects reported, minimum {min_projects}",
                           "report every project from meta.json"))
    meta = index_mod.load_meta(role_folder)
    wanted = {pr.get("title") for pr in meta.get("projects", []) if isinstance(pr, dict)}
    have = {pr.get("title") for pr in projects if isinstance(pr, dict)}
    for t in sorted(wanted - have):
        if t:
            out.append(Failure("build_missing_project", "build_report.json", None, f"meta.json project not in report: {t}",
                               "build it (or record reuse) and add it to projects[]"))
    verify_remote = bool(cfg.stage("builder").get("verify_remote", True))
    for i, pr in enumerate(projects):
        if not isinstance(pr, dict):
            out.append(Failure("report_invalid", "build_report.json", None, f"projects[{i}] is not an object", "fix"))
            continue
        path = pr.get("path")
        if not path:
            out.append(Failure("report_invalid", "build_report.json", None, f"projects[{i}] has no path", "add path relative to PROJECT_ROOT"))
        else:
            full = Path(path) if Path(path).is_absolute() else cfg.project_root / path
            if not full.exists() or not any(full.iterdir()):
                out.append(Failure("build_path_missing", "build_report.json", None, f"projects[{i}].path {path} does not exist or is empty",
                                   "point path at the repo folder under projects/"))
            elif not (full / "README.md").exists():
                out.append(Failure("build_readme_missing", "build_report.json", None, f"{path} has no README.md",
                                   "write the README in the canonical order"))
        if not pr.get("build_ok"):
            out.append(Failure("build_failed", "build_report.json", None, f"projects[{i}] build_ok is false", "fix the build"))
        if not pr.get("tests_ok"):
            out.append(Failure("tests_failed", "build_report.json", None, f"projects[{i}] tests_ok is false", "fix or add tests"))
        if pr.get("relationship") not in ("new", "reuse", "extend"):
            out.append(Failure("report_invalid", "build_report.json", None, f"projects[{i}].relationship must be new|reuse|extend", "fix"))
        benches = pr.get("benchmarks")
        if not isinstance(benches, list) or not benches:
            out.append(Failure("benchmarks_missing", "build_report.json", None, f"projects[{i}] has no benchmarks[]",
                               "measure every claim from meta.json and record it"))
        else:
            for j, b in enumerate(benches):
                if not isinstance(b, dict) or "claim" not in b or "meets_claim" not in b or "value" not in b:
                    out.append(Failure("report_invalid", "build_report.json", None, f"projects[{i}].benchmarks[{j}] needs claim, value, meets_claim", "fix"))
        url = pr.get("repo_url")
        if cfg.get("sandbox") and str(url or "").startswith("sandbox://"):
            continue  # offline experiment: local repo only, nothing to verify remotely
        if not url:
            out.append(Failure("repo_missing", "build_report.json", None, f"projects[{i}] has no repo_url", "push the repo and record its URL"))
        elif verify_remote and _repo_reachable(str(url), cfg) is False:
            out.append(Failure("repo_unreachable", "build_report.json", None, f"gh cannot view {url}", "push the repo (public) and check the URL"))
    if not rep.get("month_built"):
        out.append(Failure("report_invalid", "build_report.json", None, "month_built missing", "set month_built like 'Aug. 2026'"))
    return out


def fix_prompt(failures: list[Failure], role_folder: Path, job: Job, cfg: Config) -> str:
    return format_fix_prompt(failures, extra=f"Role folder: {role_folder}. Projects dir: {cfg.projects_dir}.")


def on_success(role_folder: Path, job: Job, cfg: Config) -> None:
    rep = json.loads((role_folder / "build_report.json").read_text(encoding="utf-8"))
    repos = [str(pr.get("name") or Path(str(pr.get("path", ""))).name) for pr in rep.get("projects", []) if isinstance(pr, dict)]
    index_mod.update_repos(cfg, role_folder.name, [r for r in repos if r])


SPEC = StageSpec(stage=Stage.BUILDER, exclusive=True, build_prompt=build_prompt, check=check, fix_prompt=fix_prompt,
                 on_success=on_success)
