# Smart Apply

Fills internship applications from the pipeline's role folders, in a separate Chrome window, and stops before
Submit so Alex reviews and sends each one himself.

## Install (once)

```powershell
python -m pip install -r requirements.txt
python -m smartapply setup          # seeds profile.yaml from the pipeline's ASSETS.md, asks for address + EEO answers
python -m smartapply doctor         # pipeline DB, Archon .env, Chrome, claude, profile
python -m pytest tests -q           # guard tests use a real Chrome on a temp profile
python -m smartapply install-task   # at-logon daemon: dashboard http://127.0.0.1:8770 + fill worker
```

## Everyday

| Command | What it does |
|---|---|
| `python -m smartapply queue --candidates` | pipeline jobs with a finished role folder, not yet applied or queued |
| `python -m smartapply queue --add 2041 2072` | queue jobs (creates `data/applications/<id>/` with the renamed resume) |
| `python -m smartapply fill 2041 --dry-run` | open the job in a background tab, upload the resume, prove all three guard layers refuse a submit, leave the tab |
| `python -m smartapply fill 2041` | prefetch questions, draft answers (profile first, Claude for the rest), fill the form, leave the tab |
| `python -m smartapply batch --n 5` | fill the next 5 pipeline jobs (Workday first, then Greenhouse, Lever, Ashby, then sites the Claude browser agent handles), then toast once; `--list` previews, `--ids` picks jobs, `--ats lever` filters, `--ats agent` only agent sites, `--no-agent` skips them |
| `python -m smartapply account default` | set the password every new account gets (stored in Windows Credential Manager only) |
| `python -m smartapply account list` | Workday tenant accounts Smart Apply created; `account set <tenant>` stores a password you already use, `account delete <tenant>` forgets one; the browser agent's accounts are listed as `agent` with the site host |
| `python -m smartapply doctor --agent` | the usual checks plus one real browser-agent run on a tiny form: proves the tool inventory, the deny hook, the submit refusal and the focus rule (about $0.10) |
| `python -m smartapply review` | bring the SmartApply window on-screen (the only thing that ever does) |
| `python -m smartapply applied 2041` | after you clicked Submit: mark it applied in the pipeline (Archon counts it) and here; the Claude-written answers of that application become confirmed answer memory |
| `python -m smartapply memory` | the confirmed answers reused on later applications (`--forget ID` drops one) |
| `python -m smartapply serve` | foreground daemon (Ctrl+C stops) |
| `python -m smartapply chrome status` | the SmartApply Chrome's port and tabs |

Dashboard: Queue (Fill / Skip), Review (Mark applied / Refill), Needs you, Applied, Costs (by purpose, day and
application), Memory (forget an answer), Doctor. A toast arrives when a batch is ready; its button restores the
SmartApply window. Nothing else ever raises it.

From the pipeline dashboard (127.0.0.1:8765) every live, unapplied job on its Done page and job page has a
"Fill application" button: it posts the job id to `http://127.0.0.1:8770/fill`, which queues the job here (if it
is not already) and starts the fill; Smart Apply accepts that post only from the pipeline dashboard's origin.
The daemon has to be running (`install-task` once, or `serve`).

## How an application flows

`queued -> prefetched -> drafted -> filling -> ready_for_review -> applied`, with `needs_human(reason)` for
CAPTCHAs, logins, verification codes, unanswered required questions and unknown sites, and `failed` / `skipped`.

## Supported forms

| ATS | Questions read from | Filled by | Notes |
|---|---|---|---|
| Greenhouse | Job Board API (`?questions=true`); board token discovered for `?gh_jid=` career sites | `adapters/greenhouse.py` | education/EEO controls vary per board and are discovered in the DOM; custom file questions and consent checkboxes are left for you |
| Lever | `cards[...][baseTemplate]` JSON in the apply page | `adapters/lever.py` | resume first (the parser fills untouched fields), location suggestion clicked, EEO selects; Enter is never pressed |
| Ashby | site GraphQL (`op=ApiJobPosting`) | `adapters/ashby.py` | Yes/No buttons, radio-group selects, location autocomplete; the "Autofill from resume" pane is never touched |
| Workday | job detail + questionnaire JSON (`/wday/cxs/...`, `/wday/calypso/cxs/common/.../questionnaire/<id>`), employer flags, password rules | `adapters/workday.py` | creates the tenant account (password in Windows Credential Manager, verification link read from Gmail over read-only IMAP), then My Information, My Experience (work rows from the role folder's experience file, education, resume, websites), the question pages, Voluntary Disclosures, Self Identify, and stops on Review where the footer button reads Submit; compliance attestations come from `profile.yaml` or are left to you |
| everything else (Oracle HCM, iCIMS, SmartRecruiters, SuccessFactors, Phenom, plain career sites) | nothing ahead of time | `adapters/agent.py`: a `claude -p` run (Sonnet) whose only tools are the page tools of `smartapply/browser/mcp_server.py` | reads the page itself (`snapshot` lists every visible control with a ref), fills text, dropdowns, radios, uploads, follows the posting's Apply link, creates accounts with vault passwords it never sees, reads verification codes from Gmail, and stops on the review page; the guard runs inside the server process, a PreToolUse hook denies every other tool, and `agent_report.json` records each action as it happens. LinkedIn is never automated |

Profile-driven answers (name, contact, address, school, GPA and graduation pickers, work authorization, sponsorship,
permanent-resident and ITAR statements, how-you-heard, EEO declines, consents) cover about 75% of questions on the
pipeline's postings; Claude writes the essays; knock-outs (on-site, salary, relocation, start dates) are yours.

Workday questionnaires also ask compliance questions (relatives at the employer, government employment, convictions,
non-competes, debarment). Those are answered only from the `attestations:` block in `profile.yaml`; while a value is
`null` the question is left for you on the form (Next stays on that page, so you finish the wizard yourself).
Voluntary-disclosure dropdowns take your EEO answers; a required list without a decline option (Amgen's veteran
status) is left for you too.

## Principles

See `CLAUDE.md`. Never submits. Never steals focus. Never writes the pipeline. Never guesses knock-out answers. No em dashes.
