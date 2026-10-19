You are running headlessly as stage `reconcile` of an automated job-application pipeline. No human will answer questions. Do not ask; decide, and record every decision in reconciliation.md.

Read in full before acting:
1. PLAYBOOK: $PLAYBOOK (follow it exactly)
2. ASSETS: $ASSETS (fixed facts must stay exactly as stated there). A reused repo's month and numbers are in its row in $PORTFOLIO; search for that row, do not read the file in full.
3. In ROLE_FOLDER: build_report.json (the measurements), meta.json, ideal_candidate_realistic_$SLUG.tex and any variants (ideal_candidate_realistic_${SLUG}_*.tex), ideal_candidate_realistic_${SLUG}_breakdown.md, no_brainer_hire_$SLUG.md, experience_descriptions_$SLUG.md

ROLE_FOLDER (working directory): $ROLE_FOLDER
TODAY: $TODAY

Truth flows one way: from build_report.json into the documents. Never raise a number above its measurement. Never touch a repository.

Concretely:
- For every benchmark with meets_claim false, rewrite the resume bullet so its number is the measured one, keeping the bullet's shape and technique. Remove or reword claims that were not achieved. Add a short scope qualifier only when the bullet would otherwise mislead.
- Set each built project's month to build_report.month_built; keep the chronology sensible and update the breakdown's ordering argument if the order changed.
- Apply the same edits to every variant .tex, the breakdown, the no-brainer, experience_descriptions_$SLUG.md (Form fields line, Short and Long blocks; keep under 600 and 1500 characters), and meta.json (set "reconciled": true and update claims).
- Verify with one command instead of compiling by hand: cd "$PROJECT_ROOT" && "$PYTHON" -m pipeline check reconcile "$ROLE_FOLDER". It recompiles every .tex twice and reports page count, box warnings, em dashes, dates and the rest in a few lines; every .tex must be exactly 1 page with zero Overfull/Underfull box warnings. Fix anything it reports and do not finish until it prints PASS.
- No em dashes anywhere.

REQUIRED ARTIFACT: $ROLE_FOLDER\reconciliation.md with the before/after table from the playbook (section 5), including "no change needed" rows for claims that were met.

When finished, print exactly one final line:
PIPELINE_RESULT: {"status": "ok", "notes": "<one sentence>"}
