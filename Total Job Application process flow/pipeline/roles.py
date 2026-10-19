"""Role folders: naming, creation, metadata snapshot."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Config
from .models import Job
from .paths import short_slug, slugify
from .util import now_iso

_DROP = re.compile(r"\b(summer|fall|spring|winter|20\d\d|the|a|an|of|and|us|u\.s\.)\b|[()\[\],:;/-]", re.I)


def role_slug(job: Job) -> str:
    return short_slug(job.company)


def _short_title(title: str, company: str) -> str:
    t = title
    if company:
        t = re.sub(re.escape(company), " ", t, flags=re.I)
    t = _DROP.sub(" ", t)
    toks = [w for w in re.findall(r"[A-Za-z0-9+#]+", t) if w]
    seen: list[str] = []
    for w in toks:
        lw = w.lower()
        if lw in ("intern", "internship", "internships"):
            w = "Intern"
        if lw in ("co", "op", "coop"):
            w = "Coop"
        if w not in seen:
            seen.append(w)
    toks = seen[:4]
    if not any(w.lower() in ("intern", "coop") for w in toks):
        toks.append("Intern")
    return "_".join(toks) if toks else "Intern"


def term_token(job: Job, cfg: Config) -> str:
    for t in job.terms or []:
        m = re.search(r"(summer|fall|spring|winter)\s*(20\d\d)", t, re.I)
        if m:
            return f"{m.group(1).title()}{m.group(2)}"
    tgt = cfg.screen.target_terms[0] if cfg.screen.target_terms else "Summer 2027"
    return re.sub(r"\s+", "", tgt)


def role_folder_name(job: Job, cfg: Config) -> str:
    comp = slugify(job.company, 24)
    title = slugify(_short_title(job.title, job.company), 40)
    return f"{comp}_{title}_{term_token(job, cfg)}"


def ensure_role_folder(job: Job, cfg: Config) -> Path:
    """Create roles/<name>/ (unique suffix on collision), write job.json and jd.txt. Idempotent if job.role_folder is set."""
    if job.role_folder and Path(job.role_folder).exists():
        folder = Path(job.role_folder)
    else:
        base = role_folder_name(job, cfg)
        folder = cfg.roles_dir / base
        n = 2
        while folder.exists() and not (folder / "job.json").exists():
            folder = cfg.roles_dir / f"{base}_{n}"
            n += 1
        if folder.exists():
            try:
                existing = json.loads((folder / "job.json").read_text(encoding="utf-8"))
                if existing.get("id") != job.id:
                    n = 2
                    while (cfg.roles_dir / f"{base}_{n}").exists():
                        n += 1
                    folder = cfg.roles_dir / f"{base}_{n}"
            except (OSError, json.JSONDecodeError):
                pass
        folder.mkdir(parents=True, exist_ok=True)
    write_job_json(folder, job, cfg)
    (folder / "jd.txt").write_text(job.jd_text or "", encoding="utf-8")
    return folder


def write_job_json(folder: Path, job: Job, cfg: Config) -> None:
    d = job.to_public_dict()
    d["slug"] = role_slug(job)
    d["role_folder"] = str(folder)
    d["written_at"] = now_iso()
    (folder / "job.json").write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
