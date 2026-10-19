You are running headlessly to prepare an ARCHETYPE resume for an automated job-application pipeline. No human will answer questions. Do not ask; decide.

An archetype is a finished, truthful resume that gets sent, as is or lightly tailored, to many companies in the `$ARCHETYPE` area. The files in the working directory were copied from a resume written for one posting ($SOURCE_COMPANY: $SOURCE_TITLE). Make them fit any $ARCHETYPE internship instead of that one posting.

Working directory: $ARCHETYPE_DIR (edit only files here)
Files: resume.tex, experience_descriptions.md, meta.json
Facts about Alex: $ASSETS (every fact must agree with it)
TODAY: $TODAY

Remove or generalize only what is specific to that one posting:
- Any mention of $SOURCE_COMPANY or its products, teams, customers or internal tools, in bullets, the summary, skills or experience_descriptions.md.
- Posting logistics: a line or tag naming that posting's location, term, team, requisition or start date. Keep the graduation line and contact header.
- A coursework line chosen for that posting: replace it with the courses from ASSETS that matter most for $ARCHETYPE roles in general.
- Skills that are listed only because that JD asked for them and that no project or experience on the page uses: drop them. Keep everything the page demonstrates.

Never:
- Add a capability, tool, number, project, employer or date. Never raise a number. This pass only removes or generalizes.
- Write anything in the future or in the current month (graduation line excepted). No "Present".
- Use em dashes (the Unicode character or `---`). Use commas or restructure.

Then:
- Keep experience_descriptions.md consistent with the resume (Short blocks under 600 characters, Long under 1500, plain prose).
- In meta.json, leave "approved" false and do not touch "projects" except to remove any project you removed from the resume.
- Write README.md: two or three sentences on what kind of role this archetype fits, then a short bullet list of what you changed from the source resume.
- Verify with one command: cd "$PROJECT_ROOT" && "$PYTHON" -m pipeline archetypes check $ARCHETYPE
  The resume must compile to exactly 1 page with zero Overfull/Underfull box warnings. Fix anything it reports and do not finish until it prints PASS.

When finished, print exactly one final line:
PIPELINE_RESULT: {"status": "ok", "notes": "<one sentence>"}
