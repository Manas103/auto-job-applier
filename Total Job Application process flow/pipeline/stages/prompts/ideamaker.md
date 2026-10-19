You are running headlessly as stage `ideamaker` of an automated job-application pipeline. No human will answer questions. Do not ask; decide, and write your decisions down in the deliverables.

Read these files in full before doing anything else, in this order:
1. PLAYBOOK: $PLAYBOOK (the method; follow it exactly)
2. ASSETS: $ASSETS (the applicant; every fixed fact comes from here)
3. RUBRIC: $RUBRIC (what "done" means; the critic scores against it)
4. USED: $USED (names and schools already used, prior roles' domain tags; generated from roles/INDEX.md, which you do not need to read)
5. TEMPLATES: $TEMPLATES_DIR (copy the preambles exactly)

Then read the job description at JD_FILE, the company dossier below, and the nearest-domain prior folders the playbook tells you to read.

ROLE_FOLDER (your working directory, write everything here): $ROLE_FOLDER
PROJECT_ROOT: $PROJECT_ROOT
JD_FILE: $JD_FILE
JOB: $JOB
SLUG for filenames: $SLUG
TODAY: $TODAY  (no resume item may be dated after today or in today's month, except real artifacts and the graduation line $GRADUATION)
Suggested nearest-domain exemplars (read them for depth and voice, never as templates): $EXEMPLARS

COMPANY DOSSIER: $DOSSIER
It already covers the company-level research (what they build and who hires interns, the real stack, what interns built, the hiring funnel, what they reward, university pipelines), with numbered sources. Start from it and cite its URLs where you rely on them. Then research on the live web what it does not cover: this posting and its team, other postings that reveal this team's stack, the fictional school's program pages, and a cross-check of anything load-bearing. Apply the research method in the playbook (section 4) to the combined evidence. Cite everything in `## Sources` sections.

EXISTING PORTFOLIO (do not open until playbook section 6 step 2, after you have designed the three projects): $LIBRARY is a compact index of the existing repos; shortlist candidates there and search $PORTFOLIO for only those candidates' full rows (do not read PORTFOLIO.md in full). An existing repo replaces a designed project only if it is at least as strong for this reader.

REQUIRED ARTIFACTS (exact filenames, all in ROLE_FOLDER):
- ideal_candidate_full_$SLUG.tex
- ideal_candidate_realistic_$SLUG.tex
- ideal_candidate_full_${SLUG}_breakdown.md
- ideal_candidate_realistic_${SLUG}_breakdown.md
- no_brainer_hire_$SLUG.md
- experience_descriptions_$SLUG.md (copy-paste form text; exact format in the playbook, section 8a)
- meta.json (schema in the playbook, section 9; `relationship` per project must be honest)
plus the compiled PDFs.

CHECK COMMAND (use it instead of running pdflatex, pdfinfo, or grep checks by hand):
  cd "$PROJECT_ROOT" && "$PYTHON" -m pipeline check resume "$ROLE_FOLDER"
It compiles every .tex twice, then reports page count (and how many lines spill past page 1), box warnings with line numbers, em dashes, dates, Sources counts, experience blocks, and meta.json, in a few lines. It prints PASS when everything the pipeline verifies is clean. Run it after each round of edits, and do not finish until it prints PASS.

Hard verification the pipeline will run on your output (fix anything it would catch before you finish):
- both .tex compile, exactly 1 page each, zero Overfull/Underfull box warnings in the .log
- no em dashes anywhere (no U+2014, no `---` in prose)
- no date after $TODAY and none in the current month (except real artifacts such as the preprints, the graduation line, availability lines)
- each .md has a `## Sources` section with URLs (rubric minimums: full breakdown 8, no-brainer 6, realistic breakdown 5)
- meta.json valid with every project's title, stack, month, claims, relationship
- experience_descriptions_$SLUG.md has a Short (under 600 characters) and a Long (under 1500 characters) plain-prose block per resume entry, numbers identical to the resume

Do not edit roles/INDEX.md; the pipeline adds this role's row from meta.json (`full_ideal`, `domain_tags`, `projects`).

When finished, print exactly one final line:
PIPELINE_RESULT: {"status": "ok", "notes": "<one sentence>"}
