# Total Job Application Process Flow

One always-on local system that collects new Summer 2027 internship listings, screens out hard gates, pings you with a Yes/No toast, and for every Yes produces a complete role folder at Resume Tailor Ideamaker quality, builds every project the realistic resume claims as a real public repo, reconciles the resume to measured truth, and reviews the result against a rubric.

```
sources -> collector -> eligibility screen -> toast (Yes/No) + dashboard backlog
                                                      | approve
                                                      v
                        role folder -> ideamaker -> verify -> builder -> reconcile -> critic -> done
```

## Install (once)

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium          # JS-rendered career pages
python -m pytest tests -q                      # offline suite, fake claude
python -m pipeline collect --no-notify         # first run seeds the store quietly
python -m pipeline notify-test                 # a real toast with Yes/No buttons
.\scripts\install_service.ps1                  # at-logon service: dashboard + collector + worker
```

Dashboard: http://127.0.0.1:8765 (pending, backlog with "run selected", running with live logs, done with links, screened with override, add a job by pasting a JD, watchlist, costs).

## Everyday commands

| Command | What it does |
|---|---|
| `python -m pipeline serve` | foreground service (Ctrl+C stops); `--port`, `--no-collector`, `--no-worker`, `--workers N` |
| `python -m pipeline collect` | one collection pass; pings for new listings |
| `python -m pipeline run <job id>` | run one job through every stage (approves it if needed); `--stop-after ideamaker` |
| `python -m pipeline verify roles/<folder>` | the deterministic checks on a role folder (compile, 1 page, no box warnings, dates, em dashes, Sources) |
| `python -m pipeline jd <job id> --print` | fetch a job description (ATS JSON, HTML, then headless browser) |
| `python -m pipeline seed` | copy the original role folders and repos into this workspace (idempotent) |
| `python -m pipeline install-task [--uninstall]` | register or remove the scheduled task |
| `python -m pipeline tiers backfill [--dry-run]` | write `jobs.priority` from company scores, auto-ignore pinged jobs in tiers that do not ping, hold lite jobs while no archetype is approved |
| `python -m pipeline archetypes list\|candidates\|seed\|check\|approve <name>` | the archetype resumes the light and archetype lanes use (`archetypes/`); `seed <name> --from roles/<folder>` copies a finished resume and runs one cleanup session; only approved archetypes are used |
| `python -m pipeline check lite <folder>` | the light lane's self-check (realistic resume, experience file, meta.json) |

## How a job flows

`new -> pinged -> approved -> jd_ready -> ideamaker_running -> ideamaker_done -> building -> built -> reconciling -> reconciled -> reviewing -> done`. Any running stage can go to `failed` (retry from the dashboard resumes at the failed stage). Hard gates (`citizenship required`, wrong term, graduation window, category, PhD-only) go to `screened_out` silently, with an override button.

Company tiers pick the lane (config `tiers`, scores from `pipeline/rank.py`): S and A run the full pipeline above; B runs the light lane (an approved archetype resume plus one short Opus tailoring session that may only reorder, reword and swap in existing repos, about $1.50-2); C and D get the matching archetype resume as is, with no claude call. Lite jobs go `jd_ready -> tailoring -> done` on their own worker thread, so they never wait behind a build. D-tier jobs are not pinged; they are ignored automatically (event `ignored_tier`, reversible from /screened). Pings go out best company first, and `jobs.priority` (the company score) also orders Smart Apply's fill queue. "Upgrade to full pipeline" on a job page reruns a lite job through every stage in a fresh `<folder>_full` folder.

Every stage is a headless `claude -p` run (Opus 5) with a playbook from `playbooks/`, bounded by a dollar budget and a wall-clock timeout. Success is decided only by deterministic checks (`pipeline/verify.py` and per-stage checks); failures go back to the same session as a fix list, up to `max_fix_rounds`, then the job fails visibly. A crash mid-stage is recovered at startup (the run is marked aborted, the job returns to the stage's ready state, and the next attempt resumes the session).

## Configuration

`config.json` (merged over defaults in `pipeline/config.py`): sources, watchlist polling, screen rules (applicant is a U.S. permanent resident graduating May 2028, targeting Summer 2027), notify (toast AppId, quiet hours, ping cap), claude (command, model, env), per-stage budgets, fix rounds, and an optional per-stage `model` override (the builder runs claude-sonnet-5; every other stage uses the default claude-opus-5), worker threads and the per-job dollar cap, verify tool paths, WSL and MSVC toolchains.

## Tests

`python -m pytest tests -q` runs offline against a fake `claude` (`tests/fake_claude/claude.py`) and fixtures for every source and ATS. `-m real_latex` tests need MiKTeX (run automatically when present). `PIPELINE_NET_TESTS=1 python -m pytest -m network` hits the live lists.

## Principles

See `CLAUDE.md`: stages are blind downstream, never lower the bar, design before comparing, you are the filter. No em dashes anywhere.
