# Auto Job Applier

Two cooperating tools for internship applications, driven by Claude Code (`claude -p`) on Windows:

| Folder | What it does |
|---|---|
| `Total Job Application process flow/` | **Resume pipeline.** Collects new internship postings from public lists, screens them, ranks companies into tiers, and for each approved job writes a tailored one-page LaTeX resume (ideamaker, builder, reconcile and critic stages) plus copy-paste experience descriptions. Local dashboard on `127.0.0.1:8765`. |
| `Smart Apply/` | **Form filler.** Takes a finished resume from the pipeline, opens the job's application in its own Chrome window, and fills it (Workday, Greenhouse, Lever and Ashby adapters, plus a fenced browser agent for other sites). It never submits: you review each tab and click Submit yourself. Dashboard on `127.0.0.1:8770`. |

Each folder has its own README (setup and commands) and CLAUDE.md (design rules and hard-won ATS quirks).

## Setup

1. Python 3.12, Google Chrome, the Claude Code CLI on `PATH` (logged in), and for the pipeline a LaTeX install (`pdflatex`, `pdfinfo`).
2. In each folder: `pip install -r requirements.txt`, then copy `config.example.json` to `config.json` and adjust.
3. Describe yourself:
   - `Total Job Application process flow/playbooks/ASSETS.md` and `PORTFOLIO.md` hold a **fictional example applicant** (Alex Doe). Replace them with your own facts and repos.
   - `Smart Apply/profile.example.yaml` -> `Smart Apply/profile.yaml`, or run `python -m smartapply setup`.
4. Optional: Gmail confirmation and verification-code reading uses read-only IMAP with an app password in `Smart Apply/mail.env` (`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`). Never commit it.

Everything personal (`profile.yaml`, `config.json`, `data/`, role folders, generated resumes, documents) is git-ignored.

## Tests

```
cd "Total Job Application process flow" && python -m pytest tests -q
cd "Smart Apply" && python -m pytest tests -q
```

The Smart Apply guard and adapter tests launch a real Chrome on a temporary profile against local fixture pages.

## Ground rules baked into the code

- Nothing submits an application. Three guard layers block submit clicks, submit requests and form submits while filling.
- Knock-out answers (sponsorship, citizenship, clearance, salary, relocation) come only from your profile or are left for you.
- No CAPTCHA solving, no LinkedIn Easy Apply automation, no em dashes in generated text.
- No secrets in the repo, the database or logs; site passwords live in Windows Credential Manager.
