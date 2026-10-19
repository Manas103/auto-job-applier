"""Light and archetype lanes (Alex, 2026-10-04): cheap resumes for B/C/D-tier companies.

- archetype (C, D): copy the best-matching archetype resume into the role folder. No claude call, $0.
- light (B): the same copy, then one short tailoring session (prompts/tailor.md) that may only reorder, reword
  and swap in better-fitting EXISTING repos. No ideamaker, builder or critic.

Archetypes live in archetypes/<name>/ (resume.tex, resume.pdf, experience_descriptions.md, meta.json, README.md)
and are used only after Alex approves them (meta.json "approved": true). Until at least one is approved, lite
jobs wait in their current state flagged `awaiting_lane`.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from . import util as _util
from .config import Config
from .index import ARCHETYPES, classify_archetype
from .models import Failure, Job
from .roles import role_slug
from .statemachine import LITE_STAGE
from .util import now_iso
from .verify import expected_files, verify_lite

ARCHETYPE_FILES = ("resume.tex", "resume.pdf", "experience_descriptions.md", "meta.json")


def archetypes_dir(cfg: Config) -> Path:
    return cfg.project_root / "archetypes"


def archetype_meta(cfg: Config, name: str) -> dict | None:
    p = archetypes_dir(cfg) / name / "meta.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_approved(cfg: Config, name: str) -> bool:
    d = archetypes_dir(cfg) / name
    meta = archetype_meta(cfg, name)
    return bool(meta and meta.get("approved") is True and all((d / f).exists() for f in ARCHETYPE_FILES))


def approved_archetypes(cfg: Config) -> list[str]:
    return [n for n in ARCHETYPES if is_approved(cfg, n)]


def library_ready(cfg: Config) -> bool:
    return bool(approved_archetypes(cfg))


def pick_archetype(cfg: Config, job: Job) -> str | None:
    """The classified archetype when approved, else backend, else any approved one; None when none is approved."""
    ok = approved_archetypes(cfg)
    if not ok:
        return None
    want = classify_archetype(job.title, job.jd_text)
    if want in ok:
        return want
    return "backend" if "backend" in ok else ok[0]


def install_archetype(cfg: Config, job: Job, folder: Path, name: str, lane: str) -> None:
    """Copy the archetype's files into the role folder under this job's slug and write meta.json."""
    src = archetypes_dir(cfg) / name
    slug = role_slug(job)
    exp = expected_files(slug)
    shutil.copyfile(src / "resume.tex", folder / exp["realistic_tex"])
    shutil.copyfile(src / "resume.pdf", folder / Path(exp["realistic_tex"]).with_suffix(".pdf").name)
    shutil.copyfile(src / "experience_descriptions.md", folder / exp["experience"])
    ameta = archetype_meta(cfg, name) or {}
    meta = {
        "company": job.company, "title": job.title, "slug": slug,
        "lane": lane, "policy": lane, "archetype": name, "archetype_version": ameta.get("version"),
        "domain_tags": ameta.get("domain_tags", []),
        "projects": ameta.get("projects", []),
    }
    (folder / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------- the light lane as a stage-like spec

def _build_prompt(archetype: str):
    def build(job: Job, cfg: Config, role_folder: Path, _extra: dict) -> str:
        from .stages import common_vars, render_prompt
        v = common_vars(job, cfg, role_folder, "reconcile")
        v["ARCHETYPE"] = archetype
        return render_prompt("tailor.md", **v)
    return build


def _check(role_folder: Path, job: Job, cfg: Config) -> list[Failure]:
    return verify_lite(role_folder, cfg, role_slug(job)).failures


def _fix_prompt(failures: list[Failure], role_folder: Path, job: Job, cfg: Config) -> str:
    import sys
    from .stages import format_fix_prompt
    py = sys.executable.replace("pythonw.exe", "python.exe")
    return format_fix_prompt(failures, f'Verify with: cd "{cfg.project_root}" && "{py}" -m pipeline check lite "{role_folder}" '
                                       "and finish only when it prints PASS. Only reorder, reword or swap in existing "
                                       "repos; never add a capability or a number.")


def light_spec(archetype: str) -> SimpleNamespace:
    """Duck-typed StageSpec so JobRunner._run_stage_loop runs the tailoring session (caps, fix rounds, heartbeats)."""
    return SimpleNamespace(stage=SimpleNamespace(value=LITE_STAGE), exclusive=False, build_prompt=_build_prompt(archetype),
                           check=_check, fix_prompt=_fix_prompt, on_success=None)


# ----------------------------------------------------------------------------- archetype library maintenance

ARCHETYPE_TAGS = {
    "backend": {"backend", "cloud", "platform", "distributed", "web", "api"},
    "ml": {"ml-systems", "ml-infra", "cuda", "ml", "llm", "inference"},
    "data": {"data-science", "analytics", "data", "etl", "forecasting"},
    "quant": {"trading", "low-latency", "cpp", "quant"},
    "systems": {"embedded", "firmware", "iot", "c", "aerospace", "flight-software", "networking", "robotics"},
}


def _review_ratio(folder: Path) -> tuple[float, str]:
    try:
        rj = json.loads((folder / "review.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0.0, "none"
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", str(rj.get("score") or ""))
    ratio = float(m.group(1)) / float(m.group(2)) if m and float(m.group(2)) else 0.0
    return ratio, f"{rj.get('verdict')} {rj.get('score', '')}".strip()


def seed_candidates(cfg: Config, conn, name: str, *, k: int = 5) -> list[dict]:
    """Finished full-pipeline role folders ranked as a starting point for archetype `name`: critic score (0-10),
    +4 when the role classifies as this archetype, +1 per INDEX tag shared with the archetype tags."""
    from . import db
    from .index import read_rows
    from .models import JobState
    tags_by_folder = {r.folder: {t.lower() for t in r.tags} for r in read_rows(cfg)}
    want = ARCHETYPE_TAGS.get(name, set())
    out = []
    for job in db.jobs_in_states(conn, [JobState.DONE], order="id"):
        if not job.role_folder:
            continue
        folder = Path(job.role_folder)
        exp = expected_files(role_slug(job))
        tex = folder / exp["realistic_tex"]
        if not (tex.exists() and tex.with_suffix(".pdf").exists() and (folder / exp["experience"]).exists()
                and (folder / "review.json").exists()):
            continue
        try:
            if json.loads((folder / "meta.json").read_text(encoding="utf-8")).get("lane", "full") != "full":
                continue
        except (OSError, json.JSONDecodeError):
            continue
        ratio, review = _review_ratio(folder)
        overlap = len(want & tags_by_folder.get(folder.name, set()))
        score = 10 * ratio + (4 if classify_archetype(job.title, job.jd_text) == name else 0) + overlap
        out.append({"score": score, "job_id": job.id, "folder": str(folder), "company": job.company, "title": job.title,
                    "review": review})
    out.sort(key=lambda c: -c["score"])
    return out[:k]


def check_archetype(cfg: Config, name: str, *, refresh_pdf: bool = True) -> list[Failure]:
    """Run the lite checks on an archetype as if installed for a job (slug `arch`), compiling when configured.
    On PASS with compilation, the freshly compiled PDF replaces resume.pdf so the two never drift apart."""
    src = archetypes_dir(cfg) / name
    missing = [f for f in ARCHETYPE_FILES if f != "resume.pdf" and not (src / f).exists()]
    if missing:
        return [Failure("missing_file", f"archetypes/{name}/{m}", None, f"{m} is missing", "seed the archetype") for m in missing]
    ameta = archetype_meta(cfg, name) or {}
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        exp = expected_files("arch")
        shutil.copyfile(src / "resume.tex", d / exp["realistic_tex"])
        if (src / "resume.pdf").exists():
            shutil.copyfile(src / "resume.pdf", d / Path(exp["realistic_tex"]).with_suffix(".pdf").name)
        shutil.copyfile(src / "experience_descriptions.md", d / exp["experience"])
        meta = {"company": "(archetype)", "title": name, "slug": "arch", "projects": ameta.get("projects", [])}
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        res = verify_lite(d, cfg, "arch")
        if res.ok and refresh_pdf and res.compiled:
            shutil.copyfile(res.compiled[exp["realistic_tex"]], src / "resume.pdf")
    failures = list(res.failures)
    for i, pr in enumerate(ameta.get("projects") or []):
        if isinstance(pr, dict) and pr.get("relationship") not in (None, "reuse"):
            failures.append(Failure("meta_invalid", f"archetypes/{name}/meta.json", None,
                                    f"projects[{i}].relationship must be reuse (archetypes only claim built repos)", "set it to reuse"))
    return failures


def set_approved(cfg: Config, name: str, approved: bool) -> None:
    p = archetypes_dir(cfg) / name / "meta.json"
    meta = json.loads(p.read_text(encoding="utf-8"))
    meta["approved"] = bool(approved)
    meta["approved_at"] = now_iso() if approved else None
    p.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def seed_archetype(cfg: Config, name: str, source: Path, *, clean: bool = True) -> str:
    """Copy a finished role folder's realistic resume, experience file and project list into archetypes/<name>/
    (unapproved), then optionally run one cleanup session that strips posting-specific text."""
    if name not in ARCHETYPES:
        return f"FAIL unknown archetype {name!r} (one of {', '.join(ARCHETYPES)})"
    source = Path(source)
    try:
        smeta = json.loads((source / "meta.json").read_text(encoding="utf-8"))
        jj = json.loads((source / "job.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return f"FAIL cannot read {source}: {e}"
    exp = expected_files(smeta.get("slug") or jj.get("slug") or "")
    tex = source / exp["realistic_tex"]
    if not tex.exists() or not (source / exp["experience"]).exists():
        return f"FAIL {source} lacks {exp['realistic_tex']} or {exp['experience']}"
    dst = archetypes_dir(cfg) / name
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(tex, dst / "resume.tex")
    if tex.with_suffix(".pdf").exists():
        shutil.copyfile(tex.with_suffix(".pdf"), dst / "resume.pdf")
    shutil.copyfile(source / exp["experience"], dst / "experience_descriptions.md")
    old = archetype_meta(cfg, name) or {}
    projects = [dict(pr, relationship="reuse") for pr in (smeta.get("projects") or []) if isinstance(pr, dict)]
    meta = {"name": name, "version": int(old.get("version") or 0) + 1, "approved": False, "approved_at": None,
            "source_folder": source.name, "source_company": jj.get("company"), "source_title": jj.get("title"),
            "seeded_at": now_iso(), "domain_tags": smeta.get("domain_tags") or sorted(ARCHETYPE_TAGS.get(name, [])),
            "projects": projects}
    (dst / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not clean:
        return f"OK seeded {name} from {source.name} (no cleanup); review it, then: archetypes check/approve {name}"
    from .stages import render_prompt
    from .stages.claude_runner import run_claude
    prompt = render_prompt("archetype_clean.md", ARCHETYPE=name, ARCHETYPE_DIR=str(dst),
                           SOURCE_COMPANY=str(jj.get("company") or ""), SOURCE_TITLE=str(jj.get("title") or ""),
                           ASSETS=str(cfg.playbooks_dir / "ASSETS.md"), PROJECT_ROOT=str(cfg.project_root),
                           PYTHON=sys.executable.replace("pythonw.exe", "python.exe"), TODAY=_util.today().isoformat())
    log_path = cfg.logs_dir / "archetypes" / f"{name}_v{meta['version']}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    res = run_claude(cfg, prompt=prompt, cwd=dst, log_path=log_path, max_budget_usd=3.0, timeout_s=20 * 60, max_turns=40,
                     add_dirs=[cfg.project_root], model=str(cfg.stage("critic").get("model") or cfg.claude.model),
                     tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep"])
    failures = check_archetype(cfg, name)
    status = "OK" if res.ok and not failures else "FAIL"
    tail = "" if not failures else "\n  " + "\n  ".join(f.format() for f in failures)
    return f"{status} seeded {name} v{meta['version']} from {source.name}; cleanup ${res.cost_usd:.2f} ({res.subtype}){tail}"
