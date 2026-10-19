# Total Job Application Process Flow

An always-on local pipeline that collects new Summer 2027 internship listings, screens out hard gates, pings Alex with a Yes/No toast, and for every Yes produces a complete role folder (Resume Tailor Ideamaker quality) and then builds every project the realistic resume claims as a real public repo, reconciles the resume to measured truth, and reviews the result against a rubric.

## Four principles (do not violate)

1. Stages are blind downstream. The ideamaker never knows a builder exists; the builder never chooses projects; the reconciler never invents claims.
2. Never lower the bar. No stage shrinks scope for convenience. When reality undershoots a claim, the claim is corrected honestly; the target is not lowered in advance.
3. Design before comparing. Where existing work could be reused (repos, prior framings), design the ideal first, then compare. Reuse wins only ties or better.
4. Alex is the filter, the system is the screen. Hard gates he cannot pass (citizenship, clearance, wrong term) are removed silently; D-tier companies are ignored automatically (reversible from /screened); everything else is pinged, best companies first.

## Where things live

- `playbooks/`: the method. `ASSETS.md` (single source of truth about Alex), `PORTFOLIO.md` (every existing repo with measured numbers), `LIBRARY.md` (generated compact index of PORTFOLIO.md), `IDEAMAKER.md`, `RUBRIC.md` (the standard, versioned), `BUILDER.md`, `RECONCILE.md`, `CRITIC.md`, `templates/`.
- `roles/`: one folder per role; `roles/INDEX.md` is the cross-role memory (names, schools, tags, projects, repos).
- `projects/`: one repo per project (13 seeded from the original portfolio; each has its own `.git` and `origin` on github.com/alexdoe-dev).
- `pipeline/`: the Python package. `python -m pipeline serve` runs everything; see README.md.
- `archetypes/`: approved archetype resumes (backend, ml, data, quant, systems) for the light and archetype lanes. Full-pipeline quality spend goes to S/A companies only (config `tiers`).
- `experiments/`: kept experiment kits (e.g. `reuse_vs_design/`, the S/A project-policy test).
- `data/`: SQLite store, logs, fetched JDs, toast XML. Never commit.
- Usage brake (`pipeline/usage.py`, config `usage`, set by Alex 2026-10-09): every `claude -p` run's `rate_limit_event` is saved to `data/usage_latest.json`. Full-lane stages wait while this project's estimated share of the week is at `project_cap_pct` (40), its share since local midnight is at `daily_cap_pct` (6), or the account's week is at `hard_cap_pct` (70); light tailoring and company scoring stop only at the hard cap. The dashboard shows usage as % of the week, never dollars (Alex cares about the weekly limit, not cost).
- `tests/`: pytest suite, offline by default (`python -m pytest tests -q`).

## Rules for anyone editing here (humans or headless runs)

- No em dashes anywhere (resumes, docs, READMEs, dashboard copy, this file). Alex flags them as AI-sounding. Use commas or restructure.
- Dates on resumes: never in the future, never in the current month (except real artifacts and the graduation line). Acme Labs ended Jun. 2026; never "Present".
- A headless run with cwd inside `roles/<folder>` sees this file automatically. Follow the playbook named in your prompt and the completion protocol it states. Do not ask questions; no human is watching.
- When Alex corrects something: add a line to the changelog in `playbooks/RUBRIC.md`, then apply the correction to the relevant playbook. Corrections compound; that is how quality is preserved across unattended runs.
- The three original folders under `C:\Users\you\Downloads` (Resume Tailor Ideamaker, 3 Projects, job notification) are never modified by this project.

## Toolchain

Python 3.12 (`C:\Users\you\AppData\Local\Programs\Python\Python312\python.exe`), MiKTeX pdflatex, Poppler pdfinfo, `claude` CLI (headless `-p`), `gh` (logged in as alexdoe-dev), WSL2 Ubuntu-22.04 (g++ 11.4, CUDA 12.4 at /usr/local/cuda/bin), MSVC 2019 Build Tools, JDK 21, Windows PowerShell 5.1 for toasts.
