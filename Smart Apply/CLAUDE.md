# Smart Apply

Fills job applications up to, and never including, Submit. Reads the pipeline's role folders and jobs
(`C:\Users\you\Downloads\Auto Job Applier\Total Job Application process flow`, read-only), opens each application in the
SmartApply Chrome profile (its own window, never focused), fills it, and leaves the tab for Alex to review and
submit in one sitting. Design and evidence: `research/00-RECOMMENDATION.md`.

## Rules that do not bend

1. **Nothing in this project may submit an application.** Three guard layers are always on while filling
   (`smartapply/browser/guard.py` + `actions.py`): tool-level refusal on the real element, per-page network
   aborts of the ATS submit request, and a TTL-bound DOM blocker. Never add a path that clicks a submit button,
   presses Enter in a form, runs `form.submit()`, or exposes raw JavaScript to a model.
2. **The SmartApply window never takes focus.** Tabs are created with CDP `Target.createTarget(background=true)`
   plus an attach nudge (`chrome.open_background_tab`); every application gets its own window, parked off-screen
   in the normal state and never minimized by us (a minimized window renders at 1 fps and restoring it via CDP
   steals focus; see `ensure_parked`). Never call `Target.activateTarget` (it moves the foreground when Chrome was
   started from a terminal; `activate_tab` is a no-op on purpose) and never share a window between applications
   (a non-active tab is throttled). `unpark_all`/`bring_to_front` run only from `Runner.open_review` and
   `smartapply review`, i.e. only because Alex asked. When a fill ends, `Chrome.present` moves its window on-screen
   behind every other window (CDP bounds plus a Win32 `SWP_NOACTIVATE` push to the bottom; measured: the foreground
   window does not change), because a parked window's taskbar button only "activated" it off-screen (2026-10-05). Never call Playwright's `new_page()` on the SmartApply browser.
3. **Never write the pipeline's DB or role folders.** "Applied" is reported through the pipeline dashboard's own
   `POST /job/<id>/applied` (`smartapply/applied.py`).
4. **Never guess a knock-out answer** (sponsorship, citizenship, clearance, relocation, salary): those come from
   `profile.yaml` or are left `needs_human`. Never claim anything the reconciled resume does not support.
5. **No em dashes anywhere**, including generated answers (`util.scan_em_dashes` gates them).
6. **No LinkedIn Easy Apply automation**, no CAPTCHA solving, no third-party CAPTCHA services.
7. **No secrets in the repo or the DB.** Account passwords live in Windows Credential Manager (`vault.py`); Gmail
   comes from Archon's `.env` over read-only IMAP (`BODY.PEEK`).
8. **Every subprocess uses `CREATE_NO_WINDOW`** (`util.no_window_flags`); Chrome uses `util.detached_flags`. The
   scheduled task runs `pythonw`, so `__main__` redirects stdout/stderr to `data/logs/pythonw.log` first.

## Where things live

- `smartapply/` package: `intake` (pipeline jobs, role folders, ATS detection), `profile`, `tracker` + `models`
  (state machine), `browser/` (chrome, guard, actions, later mcp_server), `adapters/` (one per ATS), `prefetch/`
  (question readers), `answers` (Claude answer writer), `claude_runner`, `applied`, `daemon`, `web/` (dashboard on
  127.0.0.1:8770), `notify`.
- `playbooks/`: method files given to Claude runs (`ANSWERS.md`, `BROWSER_AGENT.md`).
- `data/`: SQLite tracker, logs, `applications/<pipeline job id>/` workdirs. Never commit.
- `documents/` (git-ignored): `Alex_Doe_Unofficial_Transcript.pdf` (cleaned: no student ID, birth date or residency line; source `documents/src/transcript.tex`), uploaded on Greenhouse and Ashby when a file question asks for a transcript (`profile.transcript_path`); the unredacted original is in `documents/private/`.
- `research/`: the five research reports and captures behind the design.
- Chrome profile: `%LOCALAPPDATA%\SmartApply\ChromeProfile` (port from `DevToolsActivePort`, never a fixed port).

## Commands

`python -m smartapply setup | doctor [--agent] | queue [--candidates | --add IDS] | fill JOB_ID [--dry-run] | batch [--n N | --ids IDS | --ats agent | --no-agent] |
review | applied JOB_ID | memory [--forget ID] | account list|set|delete | chrome start|status|tabs | serve | install-task`. Tests: `python -m pytest tests -q` (the guard and
adapter tests launch a real Chrome on a temp profile and fill fixture pages under `tests/fixtures/pages/`).

## Adapters (Workday, Greenhouse, Lever, Ashby; the browser agent for the rest)

An adapter fills only what the prefetcher listed plus what a DOM sweep finds (Greenhouse education and EEO controls
differ per board; Ashby adds texting consent), and records every field as filled / failed / left_for_human /
skipped. New rules in `answers.py` must be profile facts or exact option picks (GPA ranges, graduation dates,
school lists with an honest "Other"), never inferences about Alex; optional questions nobody but he can answer
are skipped rather than sent to Claude. Compliance attestations (relatives, government employment, convictions,
non-competes, debarment) come only from `profile.attestations`; `None` means left for him. Logistics Alex stated
once (2026-10-01) live in `profile.defaults` (summer start, driver's licence, essential functions, travel, on-site,
commute city, pay range, salary text, full-time availability, offer deadlines) and `school.high_school_*`;
`answers._logistics` applies them with the job row (title, locations, posted pay) for context. Embargoed-country
questions ("are you a citizen or permanent resident of Iran, Cuba, ...") are answered "No" only when the whole list is in
the label and none of `work_auth.citizenship` (Canada, stated 2026-10-05) is on it; a "confirm you are not ..." wording
stays his. The "a country that is NOT Iran, ..." wording is answered by his U.S. permanent residence (a generic export-control rule once answered "Yes" to the plain one because
its label says "export control laws"; Workday dropdowns cannot be reset to "Select One", only a reload of an unsaved
page clears them). Every fill rebuilds answers.json (free; earlier Claude answers are reused): a refill that reused an old packet kept "decline" answers after Alex set his EEO facts (DraftKings, 2026-10-01). Gender matching knows Male/Man/Cisgender man; the previous-employee rule answers yes/no questions only (it once matched an essay). Fixed 2026-10-09 from Alex's 2026-10-06 report: "If yes / If so" follow-ups to a question answered No stay blank ("N/A" when a required text box) and wait for him when the parent is his; ranked "First/Second/Third location preference" take distinct cities in his order; "current or previous job title" is his most recent non-open-source role title (Software Engineer Intern), never the student line; "Do you require work authorization?" is the sponsorship question (No); race, sexual orientation and transgender come from `profile.eeo` (a South Asian profile falls back to a plain Asian option, never East/Southeast Asian); further education and current offers are No (`profile.defaults`).

Workday (`adapters/workday.py`, measured live on Stryker, Amgen, DraftKings and Barr): wrappers are
`formField-<name>` with `data-fkit-id="<row>--<field>"`; rows are located by that prefix after Add, never by assumed
numbering; a tenant's undeletable default row is reused. Dropdown listboxes stay in the DOM after closing (pick the
visible one), prompt popups stay open after a pick and intercept the next click (close with an outside click on
the job title), date spinbuttons are typed digit by digit behind their `-display` div (one Backspace per digit when
clearing, never more), and long prompt lists only search on Enter: `Actions.press_search` allows Enter solely in
an `<input>` outside any `<form>` marked as a search box. The DOM guard blocks a form submit only when its submitter
looks like "submit the application" (Workday's sign-in and Save and Continue are real form submits). Accounts:
`vault.py` (Credential Manager, key `workday:<tenant>`), password saved before the Create click. Every new account
gets Alex's default password (`vault.new_account_password`: the Credential Manager entry `default:new_account`, set
with `smartapply account default`; a generated one only when a site's rules reject it). Never write that password into
the repo, the DB, logs or memory. verification link
from `mail.py` (`BODY.PEEK`, sender `*@otp.workday.com`, link `/activate/<token>`) opened in a second background
tab. A page is left for Alex when a required answer is his (Next only shows the inline errors), so he finishes
that wizard himself. Learned 2026-09-27 on DraftKings, Amgen, Stryker and Barr: some sign-in pages open with
Apple / Google / "Sign in with email" buttons (`_reveal_email_form` clicks the email one first); the auth overlay
`click_filter` can itself be `div[role=button][aria-label=Submit]`, so both guard layers judge the button it covers
(`AUTH_AUTOMATION_IDS` exempts the two auth button ids from the "submit in the automation id" rule; a covered
"Submit" stays blocked, `tests/fixtures/pages/overlay.html`); reopening `/apply/applyManually` while a saved draft
exists renders "Something went wrong", and the draft is continued from Candidate Home through the application's
Related Actions menu (`_continue_from_candidate_home`, page name `error`); a resumed draft keeps the legal name in
hidden inputs, which count as kept, not failed. Every SmartApply Chrome restart loses the open tabs (target ids die
with the process): refill the rows that point at dead tabs. 2026-10-01 batch of 20: a required default Languages
row (HP) is filled from `profile.defaults.languages` with the strongest proficiency (`_fill_languages`); the
My Information "How Did You Hear About Us?" list falls back through Job Board, Career Site, Company Website, Other
(Cigna); a verification mail is searched back to the account's creation time on a refill (P&G). An existing account
whose password is not in the vault (Salesforce) stops at `login` for `smartapply account set <tenant>`.

Ashby saves every field to its server as it changes (`op=ApiSetFormValue`, each response is the whole form with every stored value) and Submit validates that copy, not the page: Harvey (2026-10-05) showed four filled fields that the server never had, and a date typed key by key was stored as 2002-05-01. So dates are typed MM/DD/YYYY in one step, and `_verify_server` compares every filled field with the last server copy at the end of the fill, re-enters a mismatch once (an unchanged value saves nothing, so text is cleared first and boxes are re-ticked) and fails it otherwise. The "are you based in this location" statement question is answered from the commute city, never by the on-site rule's "Yes" (which once claimed he lived in San Francisco).

Fixed 2026-10-10 from the first autofill round after the reset: Ashby yes/no buttons and option radios toggle, so a refill leaves a pressed or checked answer alone (clicked off and on only when the server copy needs a resave); `server_values` maps a ValueSelect's stored option id to its label; a Workday source category whose sub-list has no honest entry is backed out of so the next candidate is tried (Adobe, Kensho: none fits, left for Alex); Greenhouse degree lists that only search get the first word typed ("Bachelor") and a read-back equal to the picked option counts; master's/PhD-premise questions are No (N/A when offered); `major` ("majority owned"); graduation options "May/June 2028", "Spring/Summer 2028" and month-only lists.

Fixed 2026-10-13: "how did you hear" takes the best non-referral option (`best_how_heard`, Alex 2026-10-11), and named organizations or programs never count (TMCF, NSBE, "Wall Street Bound": membership claims); Workday `_pick_source` backs out of a category whose sub-list has nothing honest and tries the next one; free-text how-heard is "Company careers website". The relatives rule skips citizenship and export-control labels (Intel's note says "family based immigration" and "U.S. Government", and it answered "No" to "are you a U.S. citizen or lawful permanent resident"). A graduation text box with a stated MM/YYYY format gets that format. "Able to live in the country where this role is located" is Yes for a U.S. posting. Later the same day, from Alex: `attestations.owns_intellectual_property` (No) and `relatives_employers` ([Initech]: a relatives question there stays his), "family member of a partner at <firm>" reads the relatives fact, "what year in college" follows from the graduation date, a salary-expectation text box takes the posted pay only when it reads as money, and "Campus Ambassador" is never a how-heard pick. Evening: English proficiency picks "Native" over "Fluent" (he is a native speaker; Workday `PROFICIENCY_PREFERENCE` too); the Workday Skills prompt gets up to 8 skills from the role's tailored resume Skills section (`intake.resume_skills`, exact matches on the tenant list only); the IP rule leaves "bound by any confidentiality/non-compete agreement" to the agreements rule; "SF"/"LA"/"DC"/"NY" count as U.S. locations. Brevan's visa/non-compete textareas only register typed input (`insert_text` plus input/change events), not `fill`. Salary expectations (Alex, 2026-10-13): a posted range is quoted back ("In line with the posted range ($X)", the low number in a number field); without one the question goes to Claude despite KNOCKOUT (`SALARY_ASK`), which names a figure at the low-to-middle of the company's usual pay, and "Open / negotiable" is only the no-Claude fallback. The text degree rule skips "what opportunities / area of interest" questions (Intel typed his degree there). Workday adds a Languages row per profile language when the section is empty (Intel), closes popups before each row field (Adobe's year fields), opens a generic "<Company> Source" folder for how-heard, and the skills step waits for fresh search results (stale lists made 3 refills pick 2 of 8). Fixed 2026-10-14: profile rules for full legal name, "current school or university", "provide your phone number", government-or-public-institution employment, valid work/residency permit (Yes for a U.S. posting), winter-or-summer preference (Summer), and graduation lists "May - Aug 2028" / "2028 academic school year"; LGBTQ+ is "No" only for a heterosexual, non-transgender profile, never the decline option. Workday Candidate Home with several drafts matched nothing (the Related Actions button has no text) and continued the first draft, so the HPE Software Engineer refill filled the Software Intern draft: drafts are now matched on their row's requisition id or title, and an unmatched list stops instead of guessing. Fixed 2026-10-15 (Alex): every fill ends by labeling its tab (`filler.tab_label`): "[SUBMIT] <company> <title>" on Review, "[YOU] <company>: <first questions left>" or the reason (sign in, solve the CAPTCHA) otherwise; an interval keeps the title because single-page ATSs rewrite it. A major or degree question that also asks why or what inspired it goes to Claude, not the one-word rule (`answers.ESSAY_TAIL`). Same day: "Are you younger than 18?" is No (GE HealthCare got Yes from the over-18 rule; "18 or older? If you are under 18, ..." stays the plain question), a notice-period list without "none" takes its "Less than N days" bracket, and "do you require relocation assistance / housing" no longer reads the relocation fact (S&P got Yes).

Refilling a reused tab (2026-10-06, Stripe and Pinterest): when one Playwright connection navigates a tab to a page with
reCAPTCHA iframes, its later `page.route` / `unroute` calls wait minutes or forever. So `filler._prenavigate` loads the
form over the tab's own CDP socket (`Chrome.navigate_raw`) before Playwright attaches, `_same_form` treats
boards.greenhouse.io and its job-boards redirect as the same form, and when Playwright must navigate anyway the guard is
armed first. The Greenhouse question API has one host for both regions (`boards-api.eu.greenhouse.io` does not resolve).

Greenhouse: a career site that only links to the board (Epic Games, Zipline: `?gh_jid=` pages with an "Apply" button
and no `form#application-form`) is left for the hosted board URL built from `ats_params` (same ids). A multi-select
with few options renders as a `fieldset.checkbox` (Rocket Lab, DV Trading): `_checkbox_group` ticks by label.

## The browser agent (every other site)

`adapters/agent.py` + `browser/mcp_server.py` + `playbooks/BROWSER_AGENT.md` + `hooks/deny_all_but_mcp.py`. The filler
opens the tab, disconnects, and spawns `claude -p` (Sonnet) with exactly one MCP server (`--strict-mcp-config`), the
server running under `pythonw.exe` so no console can appear. The server attaches to that one tab over CDP on its
own Playwright thread, arms the guard there (heartbeat every 10 s while idle), and exposes only typed tools built on
`Actions`: `snapshot` (refs like `e12`, stable for the life of the document via `data-sa-ref`; announces a visible
CAPTCHA frame), `fill` (commits a single matching suggestion on a combobox), `fill_many`, `fill_profile` (a profile
fact by field path, so the model never retypes an address or phone), `fill_password` (vault key `agent:<host>`, the
model never sees a password), `select` (native, or a custom widget opened through its `aria-controls` toggle button
with options read inside the controlled popup, Oracle JET included; types the first letter to list options and
matches decline synonyms), `check`, `click`, `upload` (workdir only), `open_application_form`, `navigate` (same site
only), `wait`, `read_verification_mail` (mails since the run started), `report` (`agent_report.json`, rewritten after
every action). After the run, `suspicious_fills` compares every typed value with the profile (case and punctuation
ignored) and turns a near-miss into a failed field for Alex. The prompt hands over `EXPERIENCE.work` and
`EXPERIENCE.projects` separately: projects are never employer rows. Verified live on Oracle HCM (BNY: email code from
Gmail, four sections, final step reached, about $1). Fences, all of them at once: `--tools ""`, `--allowedTools
mcp__smartapply`, `--disallowedTools` of every built-in, `--setting-sources ""`, `--permission-mode dontAsk`, and the
PreToolUse hook passed through `--settings` (verified: a Bash call is denied and shows up in `permission_denials`).
`AgentAdapter.build_report` flags any tool in the run's `system/init` inventory beyond ours; `doctor --agent` runs a
real self-test. `select` on a custom widget clicks the control first and the toggle only if no popup appeared (Oracle
reports `aria-expanded=true` with the popup gone, and its toggle then closes); when nothing matches, the error lists
the widget's unfiltered options so the model leaves a list with no fitting choice (Citizens' Gender: Female / Male
only) instead of probing letters for 120 turns. Tesla's careers site answers automation with an Akamai "Access
Denied": manual apply. The one deliberate exception to "never click an Apply button" is `Actions.open_apply`: on a page
with no fillable field (verified in the DOM) it navigates to the Apply link's href, or clicks a script-driven Apply
button with the DOM layer paused for that click; nothing can be submitted from a page without fields. Never widen
that precondition.

## Neighbours

- Pipeline dashboard: 127.0.0.1:8765. Archon serve: 7788. Smart Apply: 8770.
- The pipeline dashboard's "Fill application" button (its `views.smartapply_fill_button`, URL from its config
  `smartapply.url`) posts `job_id` to our `POST /fill`, which is trusted by the browser's Origin header (pipeline or
  own dashboard), not by our CSRF token. Never make that route a GET.
- Archon counts applied/day from the pipeline's `job_events` with `date(ts, 'localtime')` (fixed 2026-09-27; it used
  the UTC date before). Smart Apply records `ts_local` regardless.
- A pipeline job flagged `do_not_apply` is never a candidate (`intake.candidate_jobs`): set on the pipeline side when
  an application would be a duplicate (HRT Data Scientist 9735, 2026-10-10: HRT takes one application per cycle and
  Alex applied to its program on 2026-08-26). Applications he submitted outside Smart Apply were found in Gmail
  confirmations on 2026-10-10 and marked applied through the dashboard route.
- Answer memory (`memory.py`): only Claude-written answers of an application Alex marked applied, never profile
  facts or knock-outs; `answers.deterministic_answers` takes a remembered answer where the profile has none.
- Claude runs use the Max login through `claude -p` (no API key is configured on this machine). `claude_runner` records each run's weekly-usage reading in `data/usage_latest.json` and starts no Claude call once the week is at 70% (`usage.py`, config `usage_hard_cap_pct`; Alex, 2026-10-09), or once this project (pipeline stages plus our runs) has used the pipeline config's `usage.daily_cap_pct` (6) of the week since local midnight or `project_cap_pct` (40) this week (Alex, 2026-10-10: autofill shares the 6% a day). A batch stops at the brake instead of leaving every written answer blank.

## Do-it-yourself jobs (added 2026-10-16)

`smartapply self <job ids> --reason "..."` moves an application to state `self_apply` (fills and batches skip it)
and writes `self_apply/<Company>_<Title>_<id>/` with APPLY.md (link, reason, his standard answers, drafted answers),
the tailored resume, the transcript, the experience write-ups and the JD. `smartapply self` lists them;
`--undo` puts one back in the fill queue. Use it for captcha-every-time sites (JPMorgan), Tesla and Google (he does
those himself), postings that forbid AI help (Talos), and forms automation cannot render (Cvent iCIMS).
