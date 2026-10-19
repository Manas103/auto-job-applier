You are running headlessly as a backfill step of an automated job-application pipeline. No human will answer questions. Do not ask; decide.

Task: this role folder was completed before the experience descriptions deliverable existed. Write it now, from the finished (reconciled) realistic resume, without changing any other file.

Read these files in full first, in this order:
1. PLAYBOOK: $PLAYBOOK, section "The experience descriptions document" (the exact format and rules; follow it exactly)
2. ASSETS: $ASSETS (every fixed fact about the applicant)
3. In ROLE_FOLDER: jd.txt, ideal_candidate_realistic_$SLUG.tex (and any variants), ideal_candidate_realistic_${SLUG}_breakdown.md, no_brainer_hire_$SLUG.md, and reconciliation.md if present (the reconciled numbers are the truth; the .tex already carries them)

ROLE_FOLDER (your working directory, write here): $ROLE_FOLDER
JOB: $JOB
SLUG: $SLUG
TODAY: $TODAY

Write exactly one new file: $EXPERIENCE_FILE

Rules the pipeline verifies deterministically (fix anything it would catch before you finish):
- one section per entry on the realistic resume, in resume order, nothing added, nothing skipped
- every number, date, title, and organization identical to the resume
- each entry has a Short block (under 600 characters) and a Long block (under 1500 characters), plain first-person prose, no bullets, no markdown or LaTeX inside the blocks
- no em dashes anywhere (no U+2014, no `---` in prose)
- the posting's own vocabulary where the work honestly matches it, exactly as the realistic breakdown argues

Do not edit any other file in the folder. Do not touch roles/INDEX.md.

When finished, print exactly one final line:
PIPELINE_RESULT: {"status": "ok", "notes": "<one sentence>"}
