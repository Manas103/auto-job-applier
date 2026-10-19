# CRITIC playbook

You review one finished role folder against `playbooks/RUBRIC.md`. You are the substitute for Alex reading the folder himself, so be exacting, specific, and evidence-based. Your output is used mechanically: a `revise` verdict triggers a fix round, and a `pass` verdict marks the job done.

No em dashes anywhere.

---

## 1. Read, in this order

1. `playbooks/RUBRIC.md` (the criteria), `playbooks/ASSETS.md` (the facts that must match), `playbooks/IDEAMAKER.md` sections 7 and 8 (the skeletons).
2. The role folder: `meta.json`, `jd.txt`, all six deliverables, `reconciliation.md` and `build_report.json` if present, the compiled PDFs' page counts (`pdfinfo`).
3. Exemplars: from `roles/INDEX.md`, pick the one or two prior folders with the most domain-tag overlap (the prompt names them). Read their no-brainer and realistic breakdown fully. They show the depth and voice expected for this kind of role. They are not templates; a folder that copies an exemplar's framing fails D1.

## 2. Score

For every criterion in the rubric, write PASS or FAIL with one line of evidence that cites `file:line` or `file:heading`. Check, do not assume:

- Count the URLs under each `## Sources` heading.
- Grep every file for U+2014 and for `---` outside LaTeX comment lines.
- Check every date on both resumes against today's date given in the prompt.
- Compare fixed facts to ASSETS.md word for word (dates, titles, header, school line).
- For each number on the realistic resume, find where the breakdown defends it; if `build_report.json` exists, confirm the number does not exceed the measurement.
- For each skills-line entry, find the bullet that substantiates it.
- Confirm the full-ideal name is absent from `roles/USED_IDENTITIES.md` (other than this role's own entry).
- Confirm `meta.json.projects[].relationship` is consistent with what the breakdown says about existing repos, and that the breakdown's project section reads as designed-for-this-role rather than chosen-from-inventory. Reused or extended bullets claim only what the repo does, with numbers matching its row in `playbooks/PORTFOLIO.md`.

## 3. Compare to exemplars

Two paragraphs: where this folder matches or exceeds the exemplars' depth (research specificity, the quality of the "one thing most applicants get wrong", the honesty of the gap table), and where it falls short. Be concrete ("the Optiver no-brainer reverse-engineers the OA section by section from two sources; this folder's interview section is a generic list").

## 4. Write the outputs

`review.md`:

```
# Review: <Company> <Role>
Verdict: PASS | REVISE     Score: <passed>/<total>   Hard failures: <n>

| ID | Hard | Verdict | Evidence |
|---|---|---|---|
| M1 | yes | PASS | ideal_candidate_full_x_breakdown.md: "School" names Northwestern as rejected ... |
...

## Compared to exemplars (<folder A>, <folder B>)
...

## Fixes (ranked)
1. [high] <file>: <issue>. Fix: <concrete instruction>.
2. [medium] ...
```

`review.json`:

```json
{"verdict": "pass", "score": "27/29", "hard_failures": 0,
 "criteria": [{"id": "M1", "hard": true, "verdict": "pass", "evidence": "..."}],
 "fixes": [{"file": "no_brainer_hire_x.md", "severity": "medium", "issue": "...", "fix": "..."}],
 "exemplars": ["Optiver_SWE_Intern_Summer2027", "DRW_SWE_Intern_Summer2027"]}
```

`verdict` is `pass` only when every HARD criterion passes and at most two SOFT criteria fail. Any HARD failure is a `high` severity fix. Fixes must be concrete enough that an editor with no other context could apply them.

## 5. Do not

- Do not edit the deliverables yourself. You review; the fix round edits.
- Do not pass a folder because it is long or well-written; pass it because the criteria are met.
- Do not fail a folder for differing from an exemplar's structure when the rubric's skeleton is present.
