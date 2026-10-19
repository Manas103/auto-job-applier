# Job-application autofill tools: what exists, what to reuse, and what breaks (as of 2026-09-24)

All sources checked on **2026-09-24** unless another date is given. GitHub metadata (stars, last push, license, open issues) from `gh api` that day. Reviews on sites like wobo.ai, autoapplier.com, loopcv, jobhire, jobcopilot.com and fastapply are written by competitors; read their numbers as weak evidence.

## Bottom line

- **Nothing open source does exactly what you want.** No project fills all of Workday, Greenhouse, Lever and Ashby with your own per-job PDF and stops before submit.
- **Simplify Copilot comes closest commercially, and none of its extension code is public.** It never submits, but it uploads resumes saved in your Simplify account, not a local PDF per job.
- **Build on Playwright with one hand-written adapter per ATS.** Use an LLM only to answer custom questions, and generate those answers before opening the browser.
- **You can read the questions ahead of time for three of the four ATSs.** Greenhouse and Ashby return them as JSON, and Lever embeds them in the apply page's HTML. Workday does not expose them without signing in.
- **Your ATS mix matches Simplify's own list.** Simplify's `Summer2027-Internships` data has 17,197 listings. By URL: Workday 8,477 (49%), other 4,466, Greenhouse 1,569, Ashby 778, iCIMS 754, SmartRecruiters 683, Lever 470. Workday is the adapter to invest in.

---

## 1. Simplify (simplify.jobs / github.com/SimplifyJobs)

**What is open source:** https://github.com/SimplifyJobs has:
- **Summer2027-Internships** (47.6k stars, pushed 2026-09-24, no license file). The useful part is `.github/scripts/listings.json`. Each entry has `company_name, title, url, locations, terms, active, sponsorship, degrees, date_posted, id`.
- **New-Grad-Positions**.
- **webpack-ext-reloader**, a dev tool for hot-reloading browser extensions (MIT).
- **fastapi-gcp-tasks**, backend-take-home, some Flutter widgets.
- **A fork of browser-use** (created 2026-09-03). `main` has no changes of its own; a `compat` branch has one commit ("fix(deps): allow compatible downstream versions"). Suggests Simplify is experimenting with LLM browser agents, nothing published.
- **The Copilot extension itself is closed source.** Chrome Web Store: v3.1.6, updated 2026-09-12, 500k users, 4.9 stars (https://chromewebstore.google.com/detail/simplify-copilot-autofill/pbanhockgagggenencehbnadejlgchfc).

**How it behaves** (https://help.simplify.jobs/articles/2415391-using-copilot-to-autofill-applications and https://help.simplify.jobs/articles/8686025-manage-autofill-settings-in-the-simplify-extension):
- You click "Autofill This Page". It fills resume, cover letter, contact, education, work history, work authorization, demographics and links.
- It never submits: "review the completed application… and submit the application yourself."
- Settings: "Continuously autofill multipage forms", "Autofill all fields with AI" (answers unique questions from your profile), per-field toggles (LinkedIn, Disability, Location, Phone, Education, Work Authorization, Salary). Skills autofill off by default; recommended on for Workday.
- Remembers answers to unique questions and reuses them.
- Where unsupported, falls back to a copy-and-paste profile tab (https://help.simplify.jobs/articles/8717287-autofill-not-supported).

**What it does not do:**
- Can't upload a local, per-job PDF. Uploads resumes stored in Simplify, switched by hand.
- Tailoring is paid (Simplify+) with Simplify's own generator.
- No documentation says it creates Workday accounts.
- Third-party accuracy estimates: Greenhouse ~90%, Lever ~85%, Workday ~70%, iCIMS ~50% (wobo.ai, 2026-07-10, competitor). Essay questions "consistently" missed.
- Pricing per wobo.ai: free tier with unlimited autofill; Simplify+ $19.99/week or $39.99/month.

---

## 2. Projects table

| Name | URL | ATS coverage | Method | Last push | License | Verdict |
|---|---|---|---|---|---|---|
| **neonwatty/job-apply-plugin** | github.com/neonwatty/job-apply-plugin | LinkedIn, Greenhouse, Ashby, Lever, Rippling, Workday | LLM (Claude Code / Codex) drives visible Chrome, guided by per-ATS notes in a skill file | 2026-09-24 (112★, 4 issues) | MIT | **Closest philosophy.** Never submits, never creates accounts, never handles credentials. README marks most ATS flows "unverified." Borrow the answer-memory and consent design. |
| **ApplyPilot** (Pickle-Pixel) | github.com/Pickle-Pixel/ApplyPilot | Workday (48 employer portals), job boards via JobSpy, ~30 company sites | Claude Code + Playwright MCP, driven by a large prompt (`src/applypilot/apply/prompt.py`) | 2026-03-08 (1.6k★, 77 issues) | AGPL-3.0 | Auto-submits (`--dry-run` flag exists). Issues mostly setup, tailoring, quota (#103, #74, #107, #91: Workday 404s without `/en-US/`). Pays CapSolver to solve CAPTCHAs, ToS-hostile. Read, don't adopt. |
| **AIHawk** (feder-cr) | github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk (redirects) | Was LinkedIn Easy Apply only | Selenium + LLM | Renamed; now `invisible_playwright_mcp`, a stealth-Firefox MCP server | MIT | **Dead as a job bot.** Old issues describe LinkedIn Easy Apply limits (#573). |
| **OfferOS** (averatec0773) | github.com/averatec0773/offeros | Greenhouse, Lever, Ashby prefetch; Workday field metadata | Chrome side panel + local Next.js/SQLite. Deterministic engine fills; LLM only *suggests* answers with evidence | 2026-09-21 (44★, pre-alpha) | Apache-2.0 | **Best engineering ideas to borrow.** Reads the ATS's own field metadata, guards against auto-answering sensitive questions, stops at submit. |
| **career-ops** | github.com/career-ops-hq/career-ops | Ashby, Greenhouse, Lever, Workable (apply mode) | Claude Code agent; `prepare-application.mjs` drafts answers | 2026-09-24 (72.6k★, 604 issues) | MIT | Human-in-the-loop ("never POSTs"). `docs/APPLY_AUTOFILL.md` full of ATS quirks. No Workday. |
| **AkbarDevop/ai-job-agent** | github.com/AkbarDevop/ai-job-agent | LinkedIn, Greenhouse, Lever, Jobvite, Ashby | Deterministic Playwright scripts per ATS (`scripts/*-apply.js`) | 2026-04-26 (57★) | MIT | Good reference selectors; dry-run mode. Cautions: injects reCAPTCHA tokens; falls back to first dropdown option. No Workday. |
| **ubangura/Workday-Application-Automator** | github.com/ubangura/Workday-Application-Automator | Workday only | Puppeteer + hard-coded `data-automation-id` selectors incl. sign-in and account creation | 2026-06-19 (77★); README says superseded, unmaintained | **None** | Clear map of older Workday markup (`apply.js`). No license, learn only. |
| **kessenma/ai-job-bot** | docs/workday-handler.md | Workday + generic | Playwright handler ported from ubangura; creates account and polls Gmail for the verification link | — | — | **Only documented automation of Workday account creation plus email verification.** |
| **Sambhav101/workday-autofill** | github.com/Sambhav101/workday-autofill | Workday | Playwright; scans page first, then fills (`src/widgets.py`, `experience.py`) | 2026-06-20 (1★) | None | **`docs/widget-patterns.md` has 52 hard-won Workday lessons. Single most valuable file found.** |
| **kitfunso/jobie-agent** | extension/content/workday-selectors.js | Workday | Extension; selectors checked on live tenant 2026-09-15 | 2026-09-15 | MIT | **Up-to-date Workday selectors, old and new markup.** |
| **privacydied/job-application-skill** | sites/myworkdayjobs/NOTES.md | Workday + others | Agent skill + `atsform.py` | 2026-09-24 | MIT | Excellent Workday failure notes. |
| **GodsScion/Auto_job_applier_linkedIn** | github.com/GodsScion/Auto_job_applier_linkedIn | LinkedIn Easy Apply | Selenium | 2026-09-11 (2.9k★) | MIT | Has `stop_before_submit`. LinkedIn-only; #80, #93 report bot detection. |
| **wodsuz/EasyApplyJobsBot** | github.com/wodsuz/EasyApplyJobsBot | LinkedIn, Glassdoor | Selenium | 2026-05-18 | NOASSERTION | Not relevant. |
| **sentient-engineering/jobber** | github.com/sentient-engineering/jobber | Any (LLM agent) | LLM browser agent | 2024-09-09 | MIT | Abandoned. |
| **browser-use** `examples/use-cases/apply_to_job.py` | github.com/browser-use/browser-use | Any | LLM agent (o3), custom upload tool | 2026-09-24 (116k★) | MIT | Demo only. Prompt says "make your best guess" and clicks submit. Unsafe as written. |
| **speedyapply/JobSpy** | github.com/speedyapply/JobSpy | Scraper | HTTP scraping | 2026-02-18 (4.3k★) | MIT | Finds jobs only. |
| **FormFilla / OpenJobAutofill / autograph / form-pilot** | various | Workday / Greenhouse / Lever, generic | Extensions: rules + optional LLM | 2026 | mixed | FormFilla uploads via synthetic `DataTransfer`. OpenJobAutofill fills one page per click, never submits. Low-signal. |
| **Job_OS PR #21** | github.com/brucebanner010198-commits/Job_OS/pull/21 | Workday/Greenhouse/Lever via browser-use | LLM + screenshots | merged 2026-09-21 (0★) | — | Example of the 2026 "browser-use for ATS" trend; unproven. |

Note https://github.com/jasonchen270/workday-autofill appeared in search ("Playwright + CDP, pausing before submit") but returns 404.

Code search shows dozens of small 2026 Workday adapters (`vinayakawac/FillForge`, `hemnaath04/job-os`, `ci-jy/ferry` with saved Workday page HTML fixtures, `darielgu/automa-app`). Most 0–2 stars, look AI-generated.

---

## 3. Commercial tools

| Tool | Coverage | Leaves form unsubmitted? | Price | Known problems |
|---|---|---|---|---|
| **Simplify Copilot** | "100+ portals" incl. Workday, Greenhouse, Lever, iCIMS, SmartRecruiters | **Yes, always** | Free; Simplify+ $19.99/wk or $39.99/mo | Weaker on Workday/iCIMS; AI answers generic |
| **Jobright Autofill** | 90% of ATSs incl. Workday, Greenhouse, Lever, Ashby, iCIMS | Yes | Freemium | — |
| **Huntr** | Extension autofill | Yes | Free; Pro $40/mo | — |
| **Careerflow** | Extension autofill | Yes | Free / $19 / $25 per mo | — |
| **Teal** | Mostly tracker and resume builder | n/a | $13/wk, $29/30 days | — |
| **LazyApply** | Bulk auto-submit | **No, submits** | — | Trustpilot ~2.1; wrong answers on complex forms |
| **JobCopilot** | Company career pages; "autopilot" | Optional review mode | ~$28–56/mo | Billing complaints; generic content |
| **Massive** | Applies for you | No | $49–99/mo | Trustpilot 2.3; invented resume details; fails on Workday |
| **AIApply** | Auto-apply | No | $49/100 or $99/250 per month | Irrelevant applications, slow |
| **Sonara** | Auto-apply | No | $2.95 trial, then $23.95/4 wks | Shut down Feb 2024; relaunched by BOLD; availability inconsistent |
| **Wonsulting** | No autofill extension | — | — | — |

**ToS and ban risk:**
- LinkedIn's prohibited-software policy bans automation extensions; users report Easy Apply limits and restrictions.
- Greenhouse "Real Talent" (2026) detects bots, mass applications and fraud using IP, email and phone signals (https://www.greenhouse.com/product-features/greenhouse-real-talent).
- Hosted Greenhouse forms load reCAPTCHA (confirmed live). Lever apply pages carry hCaptcha (confirmed live).
- A human-paced, review-then-submit tool you run yourself is the low-risk path.

---

## 4. Field-level knowledge worth taking

### Workday (~50% of volume)

Workday has **two kinds of markup** in use today. Support both.
- **Old:** `input[data-automation-id="legalNameSection_firstName"]`, `addressSection_city`, `addressSection_postalCode`, `button[data-automation-id="addressSection_countryRegion"]`, `phone-device-type`, `phone-number`.
- **New (live tenant 2026-09-15):** each control sits inside a wrapper `[data-automation-id="formField-<name>"]` with no automation id of its own:
  - `formField-legalName--firstName input`
  - `formField-city`, `formField-postalCode`, `formField-country button`
  - `formField-phoneType`, `formField-countryPhoneCode`, `formField-phoneNumber`
  - `formField-linkedInAccount`, `formField-source` ("How did you hear about us")
  - `formField-candidateIsPreviousWorker` (radios with `value=true/false`)
  - `formField-school` / `formField-schoolName`, `formField-disabilityStatus`, `formField-hispanicOrLatino`, `formField-ethnicityMulti`
  - Element ids follow a path pattern such as `name--legalName--firstName` or `workExperience-<N>--startDate-dateSectionMonth-input`.
  - Sources: jobie-agent `extension/content/workday-selectors.js`; Sambhav101 `docs/widget-patterns.md`; OfferOS `packages/autofill/src/field-meta.ts` (maps the last id segment, e.g. `firstname`, `countryregion`, `phonenumber`, `source`, to a field type).

**Page flow:**
1. `a[data-automation-id="adventureButton"]` (Apply).
2. Popup offering `autofillWithResume` / `applyManually` / `useMyLastApplication`.
3. Sign in (`utilityButtonSignIn`, `email`, `password`, `signInSubmitButton`) or create account (`createAccountLink`, `verifyPassword`, `createAccountCheckbox`, `createAccountSubmitButton`).
4. Wizard: `applyFlowMyInfoPage` / `contactInformationPage`, then `myExperiencePage`, then application questions, then `voluntaryDisclosuresPage`, then `selfIdentificationPage`, then Review.

**Resume upload:** `setInputFiles` on `input[data-automation-id="file-upload-input-ref"]`, then confirm `[data-automation-id="file-upload-successful"]` appears.

**Dropdowns:**
- Most are `button[aria-haspopup=listbox]` → `ul[role=listbox] li`. Older code clicks, types the value, presses Enter.
- Searchable multiselects: type into `[data-automation-id="monikerSearchBox"]`, then click only the `promptLeafNode` result. `menuItem` and `promptOption` are duplicates.
- Chosen values show as chips in `selectedItemList` / `selectedItem`.
- "How did you hear" is often two-level: "Job Board" then "LinkedIn".

**EEO fields:** `gender`, `hispanicOrLatino`, `ethnicityDropdown` (or `formField-ethnicityMulti` checkboxes), `veteranStatus`, `agreementCheckbox`, disability checkbox group. ubangura hard-coded the disability options' GUID ids; match on label text instead.

**Detecting the Review page:**
- `[data-automation-id="progressBarActiveStep"]` shows "current step N of N" plus the step name. When the name is Review, or N equals total, stop.
- **Trap:** `pageFooterNextButton` (older: `bottom-navigation-next-button`) keeps the same id on every step, so on Review it *is* the Submit button. Never click "next" blindly on the last step.

**Preview of questions:** `GET https://{tenant}.wdN.myworkdayjobs.com/wday/cxs/{tenant}/{site}/job/{path}` returns `jobPostingInfo` with `questionnaireId` and `secondaryQuestionnaireId` (tested live). Question text requires sign-in, so Workday questions can't be pre-generated without starting the application.

### Greenhouse (live on job-boards.greenhouse.io/graphcore/jobs/8841941002)

- **DOM ids equal the API field names:** `#first_name`, `#last_name`, `#preferred_name`, `#email`, `#phone`, `#country`, `#resume` / `#cover_letter` (`input[type=file]`), `#question_<id>`, EEO `#gender`, `#hispanic_ethnicity`, `#veteran_status` (and `#disability_status` / `#race` when enabled).
- **Selects are react-select comboboxes** (`role=combobox`). Options render as `react-select-<id>-option-N`, with a hidden `input[required][aria-hidden]` shadow input. Type the value, click the exact-match option, verify the hidden input. AkbarDevop's `scripts/greenhouse-apply.js` `selectReactOption` shows the pattern; drop its `options[0]` fallback.
- **Stop point:** single `button[type=submit]`. Never click it. reCAPTCHA runs on submit, so the human gets the normal flow.

### Lever (live on a jobs.lever.co …/apply page)

- **Fixed fields:** `name`, `email`, `phone`, `location`, `org`, `urls[LinkedIn]`, `urls[GitHub]`, `urls[Portfolio]`, `resume` (file), `pronouns`.
- **EEO:** `eeo[gender]`, `eeo[race]`, `eeo[veteran]`, `eeo[disability]`, `eeo[disabilitySignature]`, `eeo[disabilitySignatureDate]`.
- **Custom questions** named `cards[<uuid>][field<N>]`. Each card has a hidden `cards[<uuid>][baseTemplate]` whose value is JSON listing the questions: `fields[].type` (multiple-choice, textarea, file-upload…), `text`, `required`, `options[].text`.
- **hCaptcha present.** career-ops reports programmatic clicks on checkboxes/radios can trigger hCaptcha, so it leaves those to the user.

### Ashby

- **Field ids are the form's `path` values:** `#_systemfield_name`, `_systemfield_email`, `#_systemfield_resume`, `_systemfield_education_history`. Custom fields use UUIDs.
- **Two file inputs:** a separate "autofill from resume" input exists. Upload to the resume field, not that one.
- **Controls:** Boolean questions are Yes/No buttons; location is a combobox (type, click matching `[role=option]`).
- **Duplicate applications:** Ashby merges candidates by email; check prior applications to that company.

### Resume upload in general

- Playwright `setInputFiles` on the hidden `<input type=file>` is reliable everywhere.
- Extensions can't read local files directly; they build a `DataTransfer`. With 100 local PDFs, **strong reason to use Playwright over a Chrome extension.**
- Always check the displayed filename or Workday's `file-upload-successful`.

---

## 5. Reading the questions ahead of time

| ATS | Source | Tested |
|---|---|---|
| Greenhouse | `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}?questions=true` returns `questions[]` (label, required, `fields[{name,type,values}]`, types `input_text / input_file / textarea / multi_value_single_select / multi_value_multi_select / input_hidden`), plus `compliance` (EEOC), `demographic_questions`, `location_questions`. Only *submitting* via API needs a key (https://docs.greenhouse.io/job-board.html). | ✅ `question_38508524002` = "Do you have the legal right to work in the USA?" |
| Ashby | Public posting API (`api.ashbyhq.com/posting-api/job-board/{name}`) does **not** include the form. The page's undocumented GraphQL does: `POST https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting` with `jobPosting(organizationHostedJobsPageName, jobPostingId){ applicationForm{ sections{ fieldEntries{ ... on FormFieldEntry{ id field isRequired }}}} surveyForms{...} }` | ✅ Ramp intern posting returned Name/Email/Phone/Resume/Education plus sponsorship, Kotlin and essay prompts. Undocumented, may change. |
| Lever | Official postings API does **not** expose custom questions (https://github.com/lever/postings-api). Fetch `jobs.lever.co/{co}/{id}/apply` and parse `cards[*][baseTemplate]` JSON. | ✅ Kitware posting returned visa, proficiency textareas, transcript upload, qualification MCQs |
| Workday | Only `questionnaireId` visible before sign-in | ⚠️ Questions captured during the application |

**Recommended flow:** prefetch questions, LLM drafts answers into a per-job file next to the tailored PDF, filler only *types* approved answers, then stops.

**Ideas from other projects:**
- **OfferOS** reads each ATS's field definitions from the page's React props (Ashby `props.field`, Greenhouse props, Workday `props.property`). Question text accuracy went from 37.5% to 100%.
- **career-ops** runs a "knock-out pre-scan" flagging sponsorship, degree or experience questions that could auto-reject.

---

## 6. Common failure modes

1. **Changing markup.** Workday moving from `legalNameSection_*` to `formField-*`; tenants differ in which fields exist on which page. Scan first, then act.
2. **Picking the wrong option is worse than leaving it blank.** Never fall back to the first option. OfferOS marks each field filled, failed, or left for you.
3. **Workday ignores fake clicks.** `pageFooterNextButton` and `signInSubmitButton` ignore JS `.click()`; use real Playwright clicks. Invisible `click_filter` overlay on submit buttons; `wd-popup-glass` overlay after Back.
4. **"Save and Continue" does nothing** when a required field is empty and the error shows inline. Detect by scanning `[data-automation-id^=formField-]` for "Error:".
5. **Workday re-draws date fields on every interaction**; element references go stale. Set each work-history entry in one step, fire `input`, `change`, `blur`, then read values back.
6. **"Autofill with Resume" can silently revert parsed dates on the Review page.** Always read Review back and compare to the profile before handoff.
7. **Honeypot field:** Workday sign-in/sign-up forms contain `data-automation-id="beecatcher"`. Never fill it.
8. **Workday accounts:** one per employer, often with email verification. Only kessenma automates this (Gmail polling). Some tenants skip verification. "Verify Password" label matches a naive `/verify/` regex.
9. **react-select dropdowns (Greenhouse, Ashby) re-draw on each keystroke.** Type with a small delay and query options fresh.
10. **Playwright `fill()` drops some special characters** (the `+` in "C/C++"). Use `keyboard.type`.
11. **CAPTCHAs.** Greenhouse (reCAPTCHA) and Lever (hCaptcha). Solving them (ApplyPilot via CapSolver) is ToS-hostile. Stop-before-submit avoids the problem.
12. **LLM agents on their own are unreliable.** Every LLM-only project marks flows "unverified" or collects issues about bad output. Dependable projects put deterministic adapters first and use the LLM for free text.
13. **Job metadata disappears.** After Apply, Workday's URL changes to `/apply/applyManually`, after submit to `/jobTasks/completed/application`. Record job id, title and tenant at the start.

---

## 7. What to borrow, with file pointers

- **Workday selectors (old and new):** `kitfunso/jobie-agent/extension/content/workday-selectors.js` (MIT); `ubangura/Workday-Application-Automator/apply.js` (unlicensed, read only); `shriyansh24/Job-Radar_2/backend/app/auto_apply/workday/constants.py` (step detection, shadow-DOM helper).
- **Workday widget playbook:** `Sambhav101/workday-autofill/docs/widget-patterns.md`; `privacydied/job-application-skill/sites/myworkdayjobs/NOTES.md`.
- **Workday account creation plus email verification:** `kessenma/ai-job-bot/docs/workday-handler.md`.
- **Field-type lookup instead of guessing from labels:** `averatec0773/offeros/packages/autofill/src/field-meta.ts`.
- **Refusing sensitive, truth-only and policy questions:** `offeros/packages/autofill/src/guards.ts` (Apache-2.0).
- **Greenhouse react-select plus file upload:** `AkbarDevop/ai-job-agent/scripts/greenhouse-apply.js` `selectReactOption`; `ashby-apply.js` (`#_systemfield_resume`, `[role=option]`).
- **Per-ATS notes and stop-at-review workflow:** `neonwatty/job-apply-plugin/skills/job-apply/SKILL.md`; `scripts/job-apply-store.py` answer-memory model (confirmed / inferred / sensitive, separate consent to remember).
- **ATS quirks:** `career-ops-hq/career-ops/docs/APPLY_AUTOFILL.md`.
- **Selector map for several ATSs:** `vasu-devs/JustHireMe/backend/data/selectors.json` (AGPL, read only).
- **Job feed:** `SimplifyJobs/Summer2027-Internships/.github/scripts/listings.json` on branch `dev`.

**Suggested design for Windows:**
- Python + Playwright driving a persistent Chrome profile so Workday sessions and cookies survive between runs.
- Detect the ATS from the URL and prefetch questions (Greenhouse/Ashby/Lever) so answers are generated before the browser opens.
- One deterministic adapter per ATS. LLM label-to-answer mapping only on unknown sites, only with approved answers.
- `setInputFiles` with each job's own PDF.
- Hard stop: Workday when `progressBarActiveStep` shows Review; Greenhouse, Lever and Ashby when all fields are filled. Never click submit. Read the page back and print a summary of every field and any gaps.
- Leave the window open for review and Submit.
