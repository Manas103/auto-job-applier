# Smart Apply: research conclusion and recommended design

Date: 2026-09-24. Consolidates five research reports in this folder (01 frameworks, 02 existing autofill projects, 03 ATS mechanics, 04 browser handoff, 05 Claude integration) plus two checks run on this PC.

## The one-paragraph answer

Build Smart Apply as a Python daemon that owns a dedicated Chrome profile, fills each application with a hand-written adapter per ATS (Workday, Greenhouse, Lever, Ashby first), and uses Claude in two narrow places: writing the open-ended answers before the browser opens, and driving the browser only for sites that have no adapter. Claude never gets a raw "click anything" tool, and submitting is blocked at three layers, one of them the network. The filled tab is left open in that Chrome window for Alex to review and submit. No existing framework or product does this end-to-end; the pieces that do exist (public question endpoints, Workday selectors, Playwright CDP attach) are enough to build it well.

## What the research settled

1. **No off-the-shelf tool fits.** Simplify Copilot is the closest product (never submits, ~70% on Workday per third-party reviews) but is closed source, cannot upload a per-job local PDF, and writes generic answers. Open-source job bots are LinkedIn-only and dead (AIHawk), auto-submit and pay for CAPTCHA solving (ApplyPilot), or are LLM-only and self-described "unverified" (job-apply-plugin). The best ideas to borrow are OfferOS (field metadata from React props, sensitive-question guards), career-ops (ATS quirks), and Sambhav101's 52 Workday widget lessons. See 02.

2. **Meta Muse is irrelevant.** It is a consumer agent app running in Meta's cloud VM (iOS/Android/web, Mac "computer work" coming) plus a proprietary model family with no browser or computer-use API. It cannot touch a local tab. See 01 §3.

3. **browser-use and the other agent frameworks are the wrong first tool.** browser-use is alive (116k stars, pure CDP, Claude supported) but has zero Workday evidence, no submit guard, and its only independent benchmark was 30%. Skyvern has real Workday work but it lives in their cloud code and their flow submits. Magnitude is stale, Notte is SSPL, workflow-use says "don't use in production", Lightpanda can't render. On write-heavy tasks the best agent in the only form-filling benchmark (WebBench 2025) scored 46.6%. Deterministic adapters plus a bounded LLM fallback is what every reliable project converged on. See 01, 02 §6.

4. **Questions can be read before opening the page on almost every platform**, including Workday: `GET /wday/calypso/cxs/common/{tenant}/questionnaire/{questionnaireId}` is public, and the `questionnaireId` comes from the public job-detail JSON. Greenhouse (`?questions=true`, verified live on Advanced Space and DV Trading, 17 and 23 questions), Ashby (site GraphQL), Lever (JSON embedded in the apply HTML), Rippling, Gem, Jobvite and SmartRecruiters all expose them too. So Claude drafts every answer offline, from the role folder, and the filler only types approved text. See 03.

5. **The handoff mechanism is proven on this machine.** A separate `--user-data-dir` Chrome launched with `--remote-debugging-port`, attached from Playwright over CDP, loaded a real Greenhouse form with `navigator.webdriver = false`, and stayed open after Playwright disconnected. Chrome 136+ forbids the debug port on the default profile, so a dedicated profile is required anyway. Playwright's own `launch_persistent_context` dies with the script, which loses the filled form; Claude in Chrome (GA on Max) has the best handoff but only a model-level submit guard, 45-second tool timeouts, a tab-closing behaviour, and refuses API-key auth. See 04.

6. **Claude integration: Agent SDK for the browser fallback, `claude -p` for answers.** In-process tools let the submit guard inspect the real DOM element, which a hook alone cannot. Anthropic's docs say Agent SDK products should use an API key; personal `claude -p` on Max is fine and already how the pipeline runs. Sonnet 5 drives forms (~$1–1.70 per 6-page Workday application with caching, far less when adapters do most of the work); Opus 5.5 writes answers. See 05.

7. **Workday is where the effort goes.** 55% of jobs. One account per employer, some with an email verification link (readable in advance from `approot.featureFlags.accountVerification`), a hidden honeypot field (`beecatcher`), a click-overlay on buttons, server-side drafts on every "Save and Continue" (visible to the employer), and the Next button that becomes Submit on Review. Two markup generations must be supported. See 03 §1, 02 §4.

8. **Do not touch LinkedIn Easy Apply.** ToS explicitly bans it, LinkedIn scans for 6,000+ extension IDs, and none of Alex's jobs come from it. Gem and Rippling run fraud detection on typing speed; fill at human pace there.

## Recommended architecture

```
Total Job Application Process Flow (read-only)        Archon v2 (read-only)
  pipeline.sqlite3: jobs where state='done'             .env: GMAIL_ADDRESS / GMAIL_APP_PASSWORD
  roles/<folder>/: realistic PDF, no_brainer_hire,
                   breakdown, reconciliation, jd.txt
            |                                                  |
            v                                                  v
 +----------------------------------------------------------------------------+
 |  Smart Apply daemon (pythonw, Task Scheduler at logon)                     |
 |                                                                            |
 |  1. intake     pick approved jobs, detect ATS from URL, resolve real form   |
 |  2. prefetch   read questions via public endpoints (WD/GH/Lever/Ashby/...) |
 |  3. answers    Claude (claude -p, Opus) drafts open-ended answers from the  |
 |                role folder into answers.json; standard fields from profile |
 |  4. fill       per-ATS adapter (Playwright over CDP into the SmartApply    |
 |                Chrome profile); Claude Agent SDK fallback for unknown sites|
 |  5. guard      typed tools only; submit-pattern refusal; network abort on  |
 |                each ATS's submit endpoint; overlay banner on the button    |
 |  6. handoff    disconnect CDP, leave tab open, toast; wait for the human   |
 |  7. track      applications.sqlite3: queued -> prefetched -> drafted ->     |
 |                filling -> ready_for_review | needs_human -> applied        |
 +----------------------------------------------------------------------------+
            |                                     |
            v                                     v
   SmartApply Chrome window                 Archon reads applied/day
   (Alex reviews, edits, clicks Submit)    (its "5 submitted/day" metric)
```

### Components

**Profile and answer bank** (`profile.yaml`): built once from the pipeline's `playbooks/ASSETS.md` (its single source of truth about Alex). Contact, school, GPA, graduation May 2028, links, work authorization (U.S. permanent resident: authorized, no sponsorship), and the voluntary EEO answers chosen once. Also the reusable answers to questions that recur ("available to start", "how did you hear", "commutable to X"). A per-question memory grows as answers are approved, as job-apply-plugin does.

**Resume handling:** copy `roles/<folder>/ideal_candidate_realistic_*.pdf` to `applications/<job>/<First>_<Last>_Resume.pdf` (recruiters see the filename; the "full ideal" PDF uses a fictional candidate and is never uploaded). Verify the upload succeeded on the page (`file-upload-successful` on Workday, filename shown elsewhere).

**Prefetchers** (pure HTTP, no browser): Workday job detail + questionnaire + approot flags; Greenhouse `?questions=true`; Lever apply-page `baseTemplate` JSON; Ashby GraphQL; Rippling, Gem, Jobvite, SmartRecruiters as adapters are added. Output is one normalized `questions.json` per job (label, type, required, options).

**Answer writer:** a `claude -p` run (Opus 5.5, Max plan, playbook-style like the pipeline's stages) that reads `questions.json`, `jd.txt`, `no_brainer_hire_*.md`, the breakdown and `reconciliation.md`, and writes `answers.json`. Rules: never claim more than the reconciled resume; knock-out questions (sponsorship, citizenship, clearance, relocation) come from the profile, never invented; anything it cannot answer is marked `needs_human` rather than guessed. Deterministic checks afterwards (no em dashes, length limits, every required question has an answer or a `needs_human` flag).

**Browser layer:** the daemon launches `chrome.exe --user-data-dir=%LOCALAPPDATA%\SmartApply\ChromeProfile --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1` detached (`CREATE_NO_WINDOW | DETACHED_PROCESS`) and attaches with `connect_over_cdp`. One tab per application, few tabs open at once (Playwright #42882). Before handoff the daemon disconnects, so at submit time Chrome has no automation client attached (this also sidesteps the Lever hCaptcha report). The profile is signed into nothing except the ATS accounts it creates.

**Adapters** (`adapters/workday.py`, `greenhouse.py`, `lever.py`, `ashby.py`, later `smartrecruiters.py`, `oracle_hcm.py`, `icims.py`, `rippling.py`): scan the page first, then act; use role/label locators with the researched `data-automation-id` / field-id maps as accelerators; real clicks and typing (never injected values); commit every custom dropdown by clicking the option; never fill hidden or off-screen inputs (honeypots); after each page, read back and diff against the intended values; stop on the review page. Each adapter reports `filled`, `failed`, `left_for_human` per field.

**Claude browser fallback** (Agent SDK, Python, Sonnet 5, API key from Archon's `.env`, `max_budget_usd` ~3, `max_turns` ~80): used only when no adapter matches, or an adapter reports failed fields on a page. Tools exposed: `snapshot`, `fill`, `select`, `upload`, `click` (role + name), `report`. No `evaluate`, no key presses, no coordinate clicks. The `click` tool resolves the real element and refuses anything whose text, aria-label, `type=submit` on a final page, or known selector matches a submit pattern. A PreToolUse hook is the second check. Structured output at the end: status, pages completed, open questions.

**Submit guard, three layers, all on:** (1) tool-level refusal on the real element; (2) `context.route` aborting the ATS submit request while "armed" (Workday `**/jobapplication/*/finalize*`, Ashby `op=ApiSubmit*`, Greenhouse apply POST, Jobvite `*/submitApplication*`, Rippling `*/jobs/*/apply`, Gem `/api/ats/job_posts/*/apply`, Eightfold `/api/application/v2/submit`, Phenom `/applySubmit`; `service_workers="block"`), disarmed only at handoff; (3) an injected banner over the submit button plus a capturing `submit` listener that `preventDefault()`s, removed at handoff. Every aborted request is logged so endpoint drift is noticed.

**Workday accounts:** a local vault (`keyring`, DPAPI on Windows) keyed by employer tenant; one generated password per tenant that satisfies that tenant's `account/configuration` rules. Before applying, read `approot.featureFlags.accountVerification`; when true, the daemon watches Gmail over IMAP (Archon's app password, `BODY.PEEK` read-only) for the verification link and opens it in the same profile. Duplicate accounts can disqualify, so the vault is checked first and "sign in" is preferred over "create".

**Gmail (IMAP, read-only):** verification links (Workday, Eightfold), PINs (Oracle), post-submit security codes (Greenhouse), and confirmation emails. Reuses Archon's `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` from `C:\Users\you\Downloads\Archon v2\.env`; same `BODY.PEEK` discipline as `archon/mail.py`.

**Tracker and handoff:** `applications.sqlite3` with states `queued, prefetched, drafted, filling, ready_for_review, needs_human (reason), applied, failed, skipped`. On `ready_for_review`: Windows toast (via `~\.claude\hooks\toast-lib.ps1` as Archon does) and the window brought forward. "Applied" detection, in order of reliability: the tab's URL reaching a confirmation path (polled from CDP's plain `/json` list, which needs no attachment: Greenhouse `/confirmation`, Lever `/thanks`, Jobvite `/applyConfirmation`, SmartRecruiters `/success`, Phenom `/applythankyou`); Workday `alreadyAppliedPage` on next visit; a confirmation email (Archon already classifies `confirmation` mails); and a manual "mark applied" fallback. Archon's "5 submitted/day" reads from this table.

**Batch mode:** the daemon fills a batch (say 5–10) while Alex is away, each in its own tab, then stops. He sits down, goes tab by tab, edits, submits. Workday applications also survive as server-side drafts, so nothing is lost if Chrome is closed. Greenhouse/Lever/Ashby only live in the open tab, so those are filled last or in smaller batches.

## What this costs

- Adapter path: only the answer-writer run, on the Max plan (same as the pipeline's stages). Roughly $0.10–0.50 of API-equivalent per application; zero marginal dollars on Max.
- Fallback agent: ~$1–2 per application on Sonnet 5 with prompt caching, capped by `max_budget_usd`. Expected on a minority of jobs.
- No cloud browser, no CAPTCHA services, no subscriptions.

## Build order

Each phase ends with something usable.

0. **Foundation** (profile.yaml from ASSETS.md, vault, Chrome profile launcher + CDP attach, guard layer, tracker DB, toast, intake from the pipeline DB, resume copy/rename). Verify: a dry run opens a job, attaches, uploads the resume, refuses a fake submit, disconnects, tab stays.
1. **Greenhouse** end to end (18 jobs with role folders, simplest form, questions API verified). This is the first real application reviewed and submitted by hand.
2. **Lever and Ashby** (5 jobs, similar single-page forms; watch the Ashby Yes/No buttons and Lever's Enter key).
3. **Workday** (55 jobs): account vault + verification via Gmail, resume upload, My Information, My Experience, questionnaire pages from prefetched answers, voluntary disclosures from profile, stop at Review with a read-back diff. Test on two tenants with different markup generations first.
4. **Claude fallback agent** for the unknown sites (Goldman, Jane Street, Tesla, TikTok etc.), then adapters for SmartRecruiters, Oracle HCM and iCIMS as their counts justify.
5. **Integration**: "Fill application" button and `applied` state in the pipeline dashboard; Archon reads applied/day; per-question answer memory.

## Decisions needed from Alex

1. **Workday accounts and Gmail.** Default in this design: the daemon creates accounts with vault passwords and reads verification links from Gmail over IMAP (read-only, Archon's app password). Alternative: the daemon pauses at account creation and he does that step.
2. **Review style.** Default: batch of open tabs reviewed in one sitting, with a toast when a batch is ready. Alternative: one at a time while he watches.
3. **Spending cap** for the Claude fallback agent per application (suggested $3) and per day.
4. **Voluntary EEO answers**, decided once in `profile.yaml` (any option including "decline" is fine; the filler will not guess).

## Risks and how the design handles them

- **Markup drift** (Workday especially): adapters scan-then-act with label fallbacks; each field reports its outcome; failures route to the fallback agent or to `needs_human`, never to a guess.
- **Drafts visible to employers**: the adapter completes a Workday application in one sitting or not at all; a page is saved only after its read-back diff passes.
- **Bot detection**: real Chrome, no automation flags, human-paced typing on Gem/Rippling, CDP detached before the human submits, CAPTCHAs always left to the human.
- **Prompt injection from job pages**: the fallback agent gets typed tools only, no JavaScript, and the guard checks real elements.
- **Wrong answers**: knock-out questions come only from the profile; open-ended answers are bounded by the reconciled resume; everything is reviewed before submit anyway.
- **A research note for honesty**: during the ATS research one shared test browser mis-clicked a "Create an account" text match on an open OpenAI Ashby application page. Nothing was submitted. It is the reason the design gives every run its own tab and forbids loose text-matching for clicks.
