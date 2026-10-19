# RUBRIC: the quality standard for a role folder

Version: 1.3.0 (2026-10-03)

This file is the standard. No single prior folder is "the gold standard"; each prior folder was built for one domain and would be the wrong yardstick for another. Nearest-domain prior folders are exemplars for depth and voice only, never templates.

Every criterion is pass/fail with a one-line evidence note citing `file:line` (or `file:heading`). A folder passes when every criterion marked HARD passes and at most two criteria marked SOFT fail, each with a written reason. Anything that fails HARD goes back for a fix round.

No em dashes anywhere in a role folder. That is a HARD criterion (R4) and it applies to this file too.

---

## A. Method

| ID | HARD/SOFT | Criterion |
|---|---|---|
| M1 | HARD | The full ideal candidate is defined from the JD and the company's published evidence first, not by decorating Alex's resume. The full breakdown's school section names at least one rejected alternative and the reason. |
| M2 | HARD | The school is chosen for pipeline specificity into this industry and function, and the argument is evidence-backed (program page, alumni trail, intern write-up), not prestige. |
| M3 | HARD | The realistic breakdown's gap table lists every gap against the full ideal and marks each one closable / unclosable / already closed, with the compensation used on this resume. |
| M4 | HARD | The full ideal candidate's name is new and the school is either new or explicitly justified for reuse (checked against `roles/INDEX.md`). |
| M5 | SOFT | The funnel is researched stage by stage (screen, OA, technical, team match, offer), and the no-brainer says which stage is the binding constraint when the resume is not. |

## B. Research

| ID | HARD/SOFT | Criterion |
|---|---|---|
| R-A | HARD | Every `.md` deliverable ends with a `## Sources` section of URLs. Minimums: full breakdown 8, no-brainer 6, realistic breakdown 5. |
| R-B | HARD | Sources include the posting itself, at least one company-owned page (careers, engineering blog, newsroom, or intern write-up), and at least one independent source (aggregator, Glassdoor, Blind/Reddit, press). |
| R-C | HARD | "The One Thing Most Applicants Get Wrong Here" names something specific to this company and backs it with a cited source. Generic industry advice fails. |
| R-D | SOFT | At least one disconfirming source is recorded: a hypothesis checked and rejected, with what it rejected. |
| R-E | SOFT | Claims treated as signal are cross-referenced by 3+ sources or explicitly marked lower-confidence. |

## C. Honesty

| ID | HARD/SOFT | Criterion |
|---|---|---|
| H1 | HARD | Every number on both resumes is defensible in an interview: it has a stated scope (what was measured, under what condition) and no unverifiable superlative. |
| H2 | HARD | Simulated, synthetic, or assumed quantities are labeled as such on the resume or defended in the breakdown. |
| H3 | HARD | Every language and tool on the skills line is substantiated by a bullet on the page. |
| H4 | HARD | No fabricated credential (no invented volunteer work, certification, competition placement, or authorization claim). Never "U.S. Citizen". |
| H5 | HARD | Fixed facts match `playbooks/ASSETS.md` exactly (dates, titles, school line, header). Acme Labs is never "Present". |
| H6 | SOFT | Preprints carry their bolded searchable titles and the "Independent Research" subheading when included; when cut, the cut is defended. |
| H7 | HARD | `experience_descriptions_<slug>.md` covers every entry on the realistic resume and nothing else; every number, date, title, and organization in it is identical to the resume; blocks are plain prose within the 600 and 1500 character limits. |

## D. Resume craft

| ID | HARD/SOFT | Criterion |
|---|---|---|
| R1 | HARD | Both `.tex` files compile, exactly one page each, zero Overfull/Underfull box warnings. |
| R2 | HARD | No date after today and none in the current month, except the graduation line, real artifacts with real dates, availability lines, and the build month recorded in build_report.json. |
| R3 | HARD | Project months are chronologically plausible with each other and with the rest of the resume. |
| R4 | HARD | No em dashes (U+2014 or `---` in prose) in any file. |
| R5 | HARD | Each asset (Acme Labs, GenAI Lab, Vision decision, open source, preprints) is reframed for this reader, and the reframe is argued in the realistic breakdown against the JD or the company's own vocabulary. |
| R6 | SOFT | The coursework bullet's order and the `\hfill` logistics tag answer the posting's hard gate in zero extra lines. |
| R7 | SOFT | Bullets follow verb + what + technique + measured outcome; no bullet merely describes activity. |
| R8 | SOFT | Project order is an argument (the breakdown says why this order for this reader). |

## E. Domain fit

| ID | HARD/SOFT | Criterion |
|---|---|---|
| D1 | HARD | Framing is re-derived for this role; no prior role's framing is reused wholesale without the breakdown saying why it already fits. |
| D2 | HARD | The realistic projects were designed before the portfolio (LIBRARY.md / PORTFOLIO.md) was consulted, and `meta.json` records `relationship` (new / reuse / extend) honestly per project. A reused repo is justified as at least as strong as the design. |
| D3 | SOFT | Structural Notes state what is new about this role relative to the set and any transferable lesson. |

## F. Completeness

| ID | HARD/SOFT | Criterion |
|---|---|---|
| C1 | HARD | Six deliverables with exact filenames (including `experience_descriptions_<slug>.md`) plus `meta.json` with the documented schema. |
| C2 | HARD | Skeleton headings from `playbooks/IDEAMAKER.md` sections 7 and 8 are present (extra role-specific sections are welcome). |
| C3 | HARD | `roles/INDEX.md` has a row for this role. |

---

## Scoring output (what the critic writes)

`review.md`: one table row per criterion (ID, HARD/SOFT, PASS/FAIL, evidence), then a short narrative comparing depth and voice to the nearest-domain exemplars, then a ranked fix list. `review.json`: `{"verdict": "pass"|"revise", "score": <passed criteria / total>, "criteria": [{"id": "M1", "hard": true, "verdict": "pass", "evidence": "..."}], "fixes": [{"file": "...", "severity": "high|medium|low", "issue": "...", "fix": "..."}]}`. Any HARD failure is severity high.

---

## Changelog

Append a line here whenever Alex corrects something, then apply the correction to the relevant playbook.

- 1.0 (2026-08-22): initial rubric distilled from the ten Resume Tailor Ideamaker role folders. Standing user corrections already folded in: no em dashes (reads as AI-generated); never "Present" for Acme Labs; preprint titles are mandatory; "Independent Research" not an invented program name; a second realistic variant is allowed when a one-page tradeoff makes it worth stating.
- 1.0.1 (2026-08-23): built projects carry their real build month on the resume even when that is the current month (Alex's decision; the verifier accepts `month_built` from `build_report.json`). Found on the first acceptance run (HRT), where the reconciler back-dated three repos built in Aug. 2026 to Jul. 2026 to satisfy the old rule. R2 now reads: no date after today and none in the current month, except the graduation line, real artifacts, availability lines, and the recorded build month.
- 1.0.2 (2026-08-24): screening correction from Alex, no criterion change. Roles located outside the U.S. are a hard gate (a green card is U.S. work authorization only) and are screened out silently when the location is certain; unrecognizable locations are pinged with a `location_unknown` flag. The U.S. detector was also fixed: it had been flagging NYC, SF, Denver, Washington D.C. and bare state names as non-U.S. Recorded in `playbooks/ASSETS.md` section 1.
- 1.1.0 (2026-08-24): new deliverable requested by Alex: `experience_descriptions_<slug>.md`, copy-paste text for application forms' per-experience description fields (Short under 600 characters, Long under 1500, one section per resume entry, numbers identical to the resume). Format in IDEAMAKER.md section 8a; reconcile keeps it in sync; new HARD criterion H7; C1 now counts six deliverables. The six earlier folders were backfilled with `python -m pipeline backfill-experience`.
- 1.1.1 (2026-08-25): screening correction from Alex, no criterion change. Spring, Winter and Fall 2027 co-op terms are targets alongside Summer 2027 (`screen.target_terms`); a title naming one of those seasons no longer screens out. Availability lines follow the posting's term (ASSETS.md section 1). Listings the old rule removed were re-screened into the seed backlog.
- 1.1.2 (2026-08-25): builder process rules after an incident. A builder ran `taskkill /F /IM chrome.exe` (plus node.exe, java.exe, and PID lists from netstat) dozens of times while cleaning up a Selenium suite, closing Alex's Chrome each time. BUILDER.md section 3a: kill only PIDs you started, never by image name or from enumeration; busy port means pick another; never touch the user's browsers or chromedriver; browser tests run headless on Playwright's bundled Chromium. Enforced by `pipeline/guard.py`, a PreToolUse hook installed into every headless run via `--settings` (`claude.guard_hook`).
- 1.1.3 (2026-08-26): screening correction from Alex, no criterion change. A U.S.-citizenship requirement in the JD text is now a hard screen-out on its own (`screen.citizenship_required_out`), matching the existing metadata rule; Alex holds a green card, not citizenship. A clause that admits permanent residents / green-card holders / U.S. nationals / anyone authorized to work is not disqualifying (PR_OK guard), so 'U.S. citizen or permanent resident' still passes. Security clearance, TS/SCI and ITAR remain flags, not auto-outs, since some are negotiable or the source mislabels them.
- 1.1.4 (2026-08-30): resource-starvation fix after a second Chrome incident (new tabs hung forever while the pipeline ran, recovered instantly when stopped: WSL2 was uncapped and builds thrashed RAM/CPU). Three quality-neutral guardrails: (1) `~/.wslconfig` caps WSL to 12 GB RAM and 12 of 16 cores, leaving ~19 GB and 4 cores for Windows and Chrome; (2) every stage subprocess tree runs at BELOW_NORMAL priority (`worker.below_normal_priority`), so Chrome wins CPU under contention while an idle machine still builds at full speed; (3) BUILDER.md 3z caps parallel builds at half the cores. Benchmarks stay honest: the README already states the machine spec, and the measured number is valid at that spec.
- 1.2.0 (2026-10-02): cost correction from Alex. Spend was about $35 per finished role and the builder made over two new repos per role (234 repos for 96 roles). Reuse and reword is now the default: project slots are filled library first (reuse, then extend, then new), with at most one `new` per role unless argued. Rationale: ATS matching reads the resume text, not the repo, so rewording a real project in the JD's vocabulary carries the keywords; a smaller set of deep, well-known projects is also easier to defend in interviews. Honesty rules are unchanged: rewording reframes what a repo does and never adds a capability or inflates a number. D2 rewritten; IDEAMAKER.md section 6 steps 1 and 2 and its checklist updated; CRITIC.md no longer penalizes inventory-chosen projects. The ideamaker and critic stages moved from Opus to Sonnet in config.json to cut spend further.
- 1.3.0 (2026-10-03): 1.2.0 reverted after a blind quality test. Four roles were rebuilt under library-first with Sonnet and judged against the originals by two judge models in both orders: the originals won 16 of 16, overall screen-pass 7.8 vs 5.1, project relevance 8.8 vs 5.2, ATS keyword coverage 7.9 vs 5.3. Reused projects missed each posting's core domain terms, which rewording cannot add honestly. D2, IDEAMAKER.md section 6 and its checklist, CRITIC.md and CLAUDE.md principle 3 are back to design-first. Kept from 1.2.0 (quality-neutral): the portfolio lives in PORTFOLIO.md with a generated LIBRARY.md index, so ASSETS.md is small.
- 2026-10-04 (pipeline, no rubric change): spend by company tier (Alex). The full pipeline and this rubric apply to S/A companies; B gets an approved archetype resume plus one tailoring pass that may only reorder, reword and swap in existing repos; C/D get the archetype as is; D-tier jobs are ignored instead of pinged. Archetypes are cut from finished full-pipeline resumes and used only after Alex approves them. Whether S/A should switch to library-first is being tested with the model held constant (experiments/reuse_vs_design).
