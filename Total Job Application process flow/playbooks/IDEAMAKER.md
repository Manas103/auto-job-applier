# IDEAMAKER playbook

You are producing the strongest possible internship application for Alex Doe for one specific role. Read `playbooks/ASSETS.md` (who he is, what is true) and `playbooks/RUBRIC.md` (what "done" means) before anything else. This document is the method.

You are blind to everything downstream. Do not think about whether a project is easy to build, cheap, or convenient. Design what the ideal candidate would have built. Another stage, which you know nothing about, takes care of reality later.

No em dashes anywhere in anything you write (U+2014, and no `---` in prose). Use commas or restructure.

---

## 1. Purpose

Define what the ideal candidate for this role looks like from scratch, completely independent of Alex's current resume, then work backward to close every gap that can actually be closed. The goal is never to polish the existing resume. It is to define "perfect" for this role first, then gap-analyze against it.

## 2. The six deliverables (plus meta.json)

All files go in the role folder (your working directory). Exact filenames, where `<slug>` is the short lowercase company token given in the prompt:

1. `ideal_candidate_full_<slug>.tex`: the Full Ideal Candidate. A fictional person with a distinct name and school (check `roles/USED_IDENTITIES.md`, generated from `roles/INDEX.md`; never reuse a name, and avoid reusing a school unless the pipeline argument genuinely points there again). Best school for this specific domain (the school with the most specific pipeline into this industry and function, not generic prestige; name the rejected alternatives in the breakdown). Best-fit internships. Everything maxed. This is the absolute benchmark; it reveals which signals Alex must compensate for.
2. `ideal_candidate_realistic_<slug>.tex`: the Realistic Ideal Candidate. Alex, with the fixed constraints in ASSETS.md, and every other variable (research framing, projects, skills, open source, coursework order, logistics tag) maximized for this role. Every remaining gap against the full ideal must be a closable one or an explicitly named unclosable one.
3. `ideal_candidate_full_<slug>_breakdown.md`: research-backed justification for every single item on the full ideal resume.
4. `ideal_candidate_realistic_<slug>_breakdown.md`: the same for the realistic resume, including the argument for every reframe.
5. `no_brainer_hire_<slug>.md`: what makes a candidate an automatic yes for this role, and what to do about it.
6. `experience_descriptions_<slug>.md`: copy-paste text for the application form's per-experience description fields, derived from the realistic resume (format in section 8a).
7. `meta.json`: machine-readable summary (schema in section 9).

Check both `.tex` files with the pipeline's check command (the prompt gives the exact line; it compiles twice and reports in a few lines): exactly one page each, zero Overfull/Underfull box warnings in the log, no em dashes, no date after today or in the current month (except real artifacts with real dates and the graduation date). It runs the same checks the pipeline re-verifies with, so finish only on PASS.

## 3. Read the posting correctly before anything else

1. Read the JD at `JD_FILE` closely for the actual skill and domain the role tests for. Do not assume it is like a prior role because the company or industry sounds similar. DRW and Optiver are both Chicago prop shops; the resumes built for them disagree on emphasis, ordering, and what the open-source work is for, because the postings select for different things. That divergence is the method working.
2. Identify the hard gates (graduation window, term, location, on-site, authorization, degree). Alex is a U.S. permanent resident: citizenship-required roles are out, and a "U.S. Citizen" line is never written. The honest tag is "Authorized to work in the U.S. without sponsorship".
3. Identify the funnel. Research each hiring stage separately (ATS screen, recruiter screen, OA, technical screen, team matching, offer) because the ideal candidate looks different at each stage, and sometimes the resume is not the binding constraint (Optiver's aptitude OA with negative marking and an 8-month lockout; Roblox and TikTok where the OA decides whether a human ever reads the resume). Say so when it is true.
4. Find the "one thing most applicants get wrong here". It must be specific to this company, supported by evidence, and not generic industry advice.

## 4. Research method

Primary sources first, in this order of trust: the job posting itself (cite with req ID when present); the company's careers and student-program pages; the company's own engineering blog, newsroom, and published intern-project write-ups (the single most load-bearing source type: what interns actually built tells you what the benchmark resume should contain); the company's other job postings, used to reverse-engineer the real stack; recruiting-team posts; LinkedIn profiles of people who landed offers; Glassdoor and named interview aggregators (NodeFlair, TechPrep, InterviewQuery, Exponent, JoinTaro, LinkJob); Blind and Reddit insider accounts; individual candidate blog posts; third-party press (The Pragmatic Engineer and similar); university program pages to justify the fictional school; academic citations where a metric is claimed.

Rules:
- Cross-reference across 3+ sources before treating something as reliable signal. Generic blog advice alone is low confidence.
- Record at least one disconfirming source: a hypothesis you checked and rejected, with the URL and what it rejected (Axon: the ASU "partnership" that turned out to be an athletics sponsorship, not an engineering pipeline).
- Every `.md` deliverable ends with a `## Sources` section of numbered URLs. Targets: 8+ in the full breakdown, 6+ in the no-brainer, 5+ in the realistic breakdown. More is normal for a well-researched role (Optiver: 17 / 13 / 10).
- Every claim or number on a resume must be plausible and defensible in an interview. No unverifiable superlatives. A latency number without the network condition it was measured under is not a claim. Simulated is stated as simulated.

## 5. Building the full ideal candidate

- School: chosen for the most specific pipeline into this industry and function. Write down the rejected alternatives and why (Waterloo over Utah EAE for Roblox because four of five named teams are not engine work; UChicago over Northwestern for Optiver because only one offers undergrad-accessible market coursework; Cincinnati over Purdue for GEA because Cincinnati mandates co-op). Use that school's own conventions (Waterloo states a percentage average, not a converted GPA, because converting reads as someone trying to look American).
- Internships: the two most specific ones for this function, with bullets that mirror the company's own published intern projects where those exist.
- Projects: three, ranked, each with stack and month, each a small version of something the company actually builds.
- Leadership and involvement: include when it carries a credential that cannot be manufactured (Virginia Tech Rescue Squad for Axon, trading competition for Optiver).
- Include any lines that answer the JD's hard gates (clearance, graduation window, co-op rotations) because the benchmark should show what answering them looks like.
- The full breakdown closes with "What This Benchmark Reveals": which signals Alex cannot replicate and which he can.

## 6. Building the realistic ideal candidate

Fixed: header, SU line, Acme Labs dates, lab dates, open-source dates. Everything else is re-derived for this reader.

Order of operations (this order matters):
1. From the no-brainer analysis, design the three projects the ideal version of Alex would have built for this role, with stack, month, and the one measured result each must demonstrate. Do this before looking at the existing portfolio (`playbooks/LIBRARY.md`, `playbooks/PORTFOLIO.md`).
2. Now open the portfolio: shortlist candidates from `playbooks/LIBRARY.md` and read only those candidates' full rows in `playbooks/PORTFOLIO.md` (search, do not read it in full). For each designed project, an existing repo may take its place only if it is at least as strong for this reader. If an existing repo covers part of a design, claim the extended version (the Optiver tick-capture pipeline extended the DRW decoder; that was right). If the design is stronger, keep the design. Never let a good existing repo displace a great idea.
3. Re-derive the Acme Labs framing, the GenAI Lab framing, the Vision include/exclude decision, the open-source framing, and the preprint decision for this reader. Do not reuse a prior role's framing wholesale; read the one or two nearest-domain prior folders (from INDEX.md) so you know what has been done and can say what is new. When the honest description of past work already matches the target, spend the effort on projects instead of framing (Roblox lesson: framing has diminishing returns, projects do not).
4. Coursework order and the `\hfill` logistics tag answer the posting's hard gate in zero lines.
5. Skills line: only what the page substantiates; first language matches how the posting classifies the candidate.
6. Dates: project months are in the past, not in the current month, and chronologically plausible with each other. Real artifacts (preprints) keep their real dates.
7. Space: at one page, every line is paid for by cutting another. When you cut something strong (the preprints for GEA, the Triton bullet for Praxis), write the defense in the breakdown. Where a real tradeoff only becomes visible at one page, you may produce a second variant (`ideal_candidate_realistic_<slug>_<variant>.tex`) and say which to send and when.

Bullet formula: `[strong verb] + [what was built] + [named technique] + [measured outcome]`. Weak bullets describe what you did; strong ones describe what changed because of it.

## 7. The no-brainer hire document

Skeleton (headings in this order; add role-specific sections where the research demands them):

```
# No-Brainer Hire: <Company> <Role> (<Term>)
## Read the Posting Correctly Before Anything Else      (numbered findings, each evidence-backed)
## The Immediate-Yes Checklist                          (checkbox list)
## Table Stakes vs. Differentiators                     (two tables; then "Do Not Over-Index On")
## The Highest-Leverage Projects to Build               (ranked, "why first", "do not skip the boring parts")
## Coursework and Credentials                           (priority order; say "skip" when certs do not matter)
## Gap Analysis for Alex                               (table: gap / closable? / what to do)
## The One Thing Most Applicants Get Wrong Here
## Interview Prep Angles                                (one per funnel stage, in funnel order)
## Realistic Assessment                                 (where he is strong, where he is behind, what decides it)
## Sources
```

## 8. The breakdown documents

Realistic breakdown skeleton:

```
# Realistic Ideal Candidate Breakdown: Alex Doe (<Role>)
## The Strategic Frame: <one-line thesis for this resume>
## Education                                   (coursework order, the logistics tag, why)
## Experience: Acme Labs                        (the reframe, argued against the JD and the company's vocabulary)
## Experience: SU Generative AI Lab
## SU Vision Lab: Included | Excluded           (the decision and why, even when it is easy)
## Projects                                    (each: stack, month, the one number to defend, why this order)
## Open Source & Research                      (which framing, which preprint decision, what it cost)
## Skills
## Gap Analysis: Realistic Ideal vs. Full Ideal (table: gap / status closable-unclosable-closed / compensation on this resume)
## Structural Notes                            (what is new about this role relative to the set; transferable lessons)
## Sources
```

Full-ideal breakdown skeleton:

```
# Full Ideal Candidate Breakdown: <Fictional Name> (<Role>)
## First: What This Role Actually Is
## School: <School>, <Degree>                  (the pipeline argument; rejected alternatives by name)
## Experience: <the two internships>           (why these, why these bullets)
## Projects                                    (each justified against what the company builds)
## Leadership and Involvement                  (when present)
## Skills Section Construction
## What This Benchmark Reveals                 (unreplicable signals vs. compensable ones)
## Sources
```

Every item on each resume must be justified somewhere in its breakdown. No unexplained inclusions.

## 8a. The experience descriptions document

`experience_descriptions_<slug>.md` exists because application forms ask for a free-text description of each experience, separately from the resume upload, usually with a character limit. Alex copies from this file straight into the form. It is derived from the realistic resume and must never say more than the resume does.

Format (exact; the verifier parses it):

```
# Experience Descriptions: <Company> <Role> (<Term>)

<one line: derived from ideal_candidate_realistic_<slug>.tex; every number and date matches the resume>

## <Entry title exactly as on the resume> (<Mon. YYYY> to <Mon. YYYY>)

Form fields: <Organization> | <Title> | <Location> | <Start Mon. YYYY> | <End Mon. YYYY or Present>

Short (under 600 characters):
```
<plain prose>
```

Long (under 1500 characters):
```
<plain prose>
```

... one section per entry ...

## Notes

<which three entries to pick when the form allows only three, and a two-sentence "why this company" answer if the research suggests it will be asked>
```

Rules:

- One section per entry on the realistic resume, in resume order: every Experience entry, the Open Source entry (or entries), the Independent Research entry, every Project. Skip nothing that is on the resume; add nothing that is not.
- Every number, date, title, and organization is identical to the resume. The reconcile stage updates this file whenever it changes a number or a date on the resume.
- Blocks are plain first-person prose: no bullets, no markdown, no LaTeX, no em dashes. Paragraph breaks are allowed inside the long block. The verifier rejects a short block over 600 characters and a long block over 1500.
- Use this posting's vocabulary where the work honestly matches it, exactly the reframing the realistic breakdown argues for. The reader is a recruiter skimming a form, so lead with what it was and for whom, then what Alex built, the technique, the measured outcome, and in the long version what it taught or what came next.
- Projects use `Personal project` as the organization and the build month for both dates unless the resume shows a range. Open source uses the project name (PyTorch, Triton) as the organization and `Contributor` as the title.
- No Sources section is required; sources live in the breakdowns.

## 9. meta.json

```json
{
  "company": "Optiver",
  "title": "Software Engineer Intern (Summer 2027, Chicago)",
  "slug": "optiver",
  "term": "Summer 2027",
  "domain_tags": ["trading", "platform", "tooling", "cpp", "python"],
  "full_ideal": {"name": "Mira Kaplan", "school": "University of Chicago"},
  "hard_gates": ["graduation between Dec 2027 and Jun 2028"],
  "projects": [
    {"title": "Market Data Decoder and Tick Capture Pipeline", "stack": "C++, Python", "month": "Feb. 2026",
     "claims": ["1M+ messages/sec", "500+ instruments", "9x replay storage reduction", "bit-for-bit reproducible sessions"],
     "existing_repo": "market-data-tick-capture", "relationship": "extend"},
    {"title": "...", "stack": "...", "month": "...", "claims": ["..."], "existing_repo": null, "relationship": "new"}
  ],
  "variants": ["ideal_candidate_realistic_optiver_nopapers.tex"],
  "sources_count": {"full_breakdown": 17, "realistic_breakdown": 10, "no_brainer": 13}
}
```

`relationship` is one of `new`, `reuse`, `extend`. `claims` lists every number or measurable property the bullet asserts, verbatim, so it can be checked later.

## 10. LaTeX rules

Templates: `playbooks/templates/full_ideal_template.tex` and `playbooks/templates/realistic_template.tex` (Sourabh Bajaj template, hardened: `[T1]{fontenc}`, `hidelinks`, `\bfseries` section titles, never `\scshape`). Copy the preamble exactly; replace the placeholder body.

- The page-clipping trap: `fullpage[empty]` sets `\topmargin=0` and the page origin is already 1in from the edge, so pushing `\addtolength{\topmargin}` past -1.0in clips the name off the top. Safe values: -0.75in (full ideal) and -0.8in (realistic, which carries more content). Need more room? Increase `\addtolength{\textheight}` (+1.5in / +1.8in in use), trim `\vspace`, or cut a line; never push topmargin further.
- The `geometry` package was tried once and abandoned (`fancyhdr`'s headheight ate the text area and pushed both resumes to two pages). Stay with `fullpage` + `addtolength`.
- Exactly one page, zero Overfull/Underfull hbox warnings. Check with the pipeline's check command (it compiles twice, counts pages, reports how many lines spill past page 1, and lists box warnings with line numbers).
- Dates: `Mon. YYYY -- Mon. YYYY` (two hyphens inside LaTeX date ranges are correct and are not em dashes). No item dated in the future or in the current month (the prompt tells you today's date). Acme Labs ended Jun. 2026; never "Present" there.
- Escape `&`, `%`, `#`, `$` in text. Use `\&` in section titles.
- Never leave `.aux`, `.log`, `.out`, `.synctex.gz` concerns to anyone else: they are disposable; the role folder may contain them.

## 11. Finish checklist

- [ ] Six files plus `meta.json`, exact names (including `experience_descriptions_<slug>.md`, section 8a).
- [ ] Both PDFs compiled, one page, no box warnings.
- [ ] No em dashes in any file.
- [ ] No future or current-month dates except real artifacts and the graduation line.
- [ ] Full ideal name and school not already in `roles/USED_IDENTITIES.md`.
- [ ] Every resume line justified in its breakdown; Sources sections present with URLs.
- [ ] Gap tables mark each gap closable / unclosable / already closed with the compensation used.
- [ ] "The One Thing Most Applicants Get Wrong Here" is specific to this company.
- [ ] Projects were designed before the portfolio was consulted; `relationship` recorded honestly in meta.json.
- [ ] Structural Notes say what is new about this role relative to the set.
- [ ] meta.json carries `full_ideal`, `domain_tags` and `projects`; the pipeline writes this role's `roles/INDEX.md` row from them.
