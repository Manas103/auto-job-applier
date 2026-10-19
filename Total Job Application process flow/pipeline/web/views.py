"""HTML rendering for the dashboard. Plain string building with escaping; no templates, no JS frameworks."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Iterable

from ..models import Job, JobState, StageRun, WatchlistEntry
from ..paths import file_url

NAV = [("/pending", "Pending"), ("/backlog", "Backlog"), ("/running", "Running"), ("/done", "Done"),
       ("/screened", "Screened"), ("/add", "Add job"), ("/watchlist", "Watchlist"), ("/costs", "Usage")]

RED_FLAGS = {"jd_citizenship_required"}
AMBER_FLAGS = {"jd_mentions_citizenship_or_clearance", "itar_us_person", "grad_window_unparsed", "term_unknown", "non_us_location", "toast_failed"}


def e(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


# Run costs are shown as a share of the weekly Claude limit (Alex, 2026-10-09: "I care about my Claude usage for
# the week", not dollars). Set from config `usage.dollars_per_percent` when the server starts.
DOLLARS_PER_PCT = 11.0


def wk(cost: float | None) -> str:
    pct = float(cost or 0) / max(0.01, DOLLARS_PER_PCT)
    return f"{pct:.2f}% wk" if pct < 1 else f"{pct:.1f}% wk"


def usage_card(u: dict | None) -> str:
    if not u or not u.get("enabled"):
        return ""
    from datetime import datetime
    week = "no reading yet" if u.get("week_pct") is None else f"{u['week_pct']:.0f}% used"
    resets = datetime.fromtimestamp(u["week_resets_at"]).strftime("%a %b %d, %I:%M %p") if u.get("week_resets_at") else "unknown"
    five = "" if u.get("five_hour_pct") is None else f"{u['five_hour_pct']:.0f}%"
    state = (f'<span class="flag red">brake on</span> {e(u.get("reason") or "")}' if not u.get("full_ok")
             else '<span class="flag green">brake off</span>')
    return (f'<div class="card"><span class="kpi"><b>{e(week)}</b>this week (resets {e(resets)})</span>'
            f'<span class="kpi"><b>{u["project_pct"]:.1f}%</b>this project, of a {u["project_cap_pct"]:.0f}% share</span>'
            f'<span class="kpi"><b>{u.get("today_pct", 0):.1f}%</b>this project today, of {u.get("daily_cap_pct", 0):.0f}% a day</span>'
            f'<span class="kpi"><b>{u["hard_cap_pct"]:.0f}%</b>hard cap for the week</span>'
            f'<span class="kpi"><b>{five}</b>5-hour window</span>'
            f'<p>{state}</p></div>')


def page(title: str, body: str, *, active: str = "", counts: dict[str, int] | None = None, refresh: int | None = None) -> str:
    nav = "".join(f'<a href="{p}" class="{"active" if p == active else ""}">{e(label)}</a>' for p, label in NAV)
    cnt = ""
    if counts:
        cnt = " | ".join(f"{e(k)} {v}" for k, v in counts.items())
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{e(title)}</title>{meta}'
            f'<link rel="stylesheet" href="/static/style.css"><meta name="viewport" content="width=device-width, initial-scale=1"></head>'
            f'<body><header><span class="brand">Job Pipeline</span>{nav}<span class="counts">{cnt}</span></header>'
            f'<main><h1>{e(title)}</h1>{body}</main></body></html>')


def flags_html(flags: Iterable[str]) -> str:
    out = []
    for f in flags:
        cls = "red" if f in RED_FLAGS else ("amber" if f in AMBER_FLAGS else "")
        out.append(f'<span class="flag {cls}">{e(f)}</span>')
    return "".join(out)


def state_html(state: JobState | str) -> str:
    s = state.value if isinstance(state, JobState) else str(state)
    cls = "done" if s == "done" else ("failed" if s == "failed" else ("running" if s.endswith("running") or s in ("building", "reconciling", "reviewing", "tailoring") else ""))
    return f'<span class="state {cls}">{e(s)}</span>'


def lane_badge(badge: str) -> str:
    """Company tier and lane, e.g. "A full" or "C archetype" (see config `tiers`)."""
    if not badge:
        return ""
    tier = badge.split()[0]
    return f' <span class="flag tier tier-{e(tier.lower())}" title="company tier and lane">{e(badge)}</span>'


def csrf_field(token: str) -> str:
    return f'<input type="hidden" name="csrf" value="{e(token)}">'


def action_button(job_id: int, action: str, label: str, token: str, *, cls: str = "") -> str:
    return (f'<form class="inline" method="post" action="/job/{job_id}/{action}">{csrf_field(token)}'
            f'<button class="{cls}" type="submit">{e(label)}</button></form> ')


def job_row(job: Job, token: str, *, actions: list[tuple[str, str, str]] = (), checkbox: bool = False, extra: str = "",
            badge: str = "", form_id: str = "", group: str = "") -> str:
    # form_id ties the checkbox to a form outside the table, so the row's own button forms are never nested in it
    attrs = (f' form="{form_id}"' if form_id else "") + (f' data-group="{group}"' if group else "")
    cb = f'<td><input type="checkbox" name="ids" value="{job.id}"{attrs}></td>' if checkbox else ""
    acts = "".join(action_button(job.id, a, l, token, cls=c) for a, l, c in actions)
    return (f"<tr>{cb}<td><a href=\"/job/{job.id}\">{e(job.company)}</a><br><span class=\"muted\">{e(job.title)}</span></td>"
            f"<td>{e(job.location_str)}<br><span class=\"muted\">{e(job.category)} {e(job.pay)}</span></td>"
            f"<td>{e(job.sponsorship)}<br>{flags_html(job.flags)}</td>"
            f"<td>{state_html(job.state)}{lane_badge(badge)}{extra}</td>"
            f"<td><a href=\"{e(job.url)}\" target=\"_blank\" rel=\"noopener\">posting</a></td><td>{acts}</td></tr>")


def table(rows: Iterable[str], headers: list[str]) -> str:
    head = "".join(f"<th>{e(h)}</th>" for h in headers)
    body = "".join(rows)
    if not body:
        body = f'<tr><td colspan="{len(headers)}" class="muted">nothing here</td></tr>'
    return f'<div class="card"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def pending_page(pinged: list[Job], deferred: list[Job], seeded: list[Job], token: str, counts: dict,
                 badges: dict[int, str] | None = None) -> str:
    acts = [("approve", "Yes, run it", "primary"), ("ignore", "No", "")]
    badges = badges or {}
    heads = ["", "Job", "Where", "Sponsorship / flags", "State", "Link", ""]

    def section(jobs: list[Job], group: str) -> str:
        rows = [job_row(j, token, actions=acts, checkbox=True, form_id="bulk", group=group, badge=badges.get(j.id, ""))
                for j in jobs]
        allbox = (f'<input type="checkbox" title="select all" '
                  f'onclick="document.querySelectorAll(&quot;input[data-group={group}]&quot;).forEach(c =&gt; c.checked = this.checked)">')
        return table(rows, heads).replace("<th></th>", f"<th>{allbox}</th>", 1)

    body = (f'<form id="bulk" method="post" action="/pending/bulk">{csrf_field(token)}'
            '<p><button class="primary" type="submit" name="action" value="approve">Approve selected</button> '
            '<button type="submit" name="action" value="ignore">Ignore selected</button> '
            '<span class="muted">Best companies first. Ignored jobs can be brought back from Screened.</span></p></form>')
    body += f"<h2>Pinged, awaiting your decision ({len(pinged)})</h2>"
    body += section(pinged, "pinged")
    if deferred:
        body += f"<h2>Missed while paused / deferred, still active ({len(deferred)})</h2>"
        body += '<p class="muted">Jobs discovered while you were away or over the per-run notify cap. Not toasted; review and pick the ones worth running. Stale/expired postings are hidden.</p>'
        body += section(deferred, "deferred")
    body += f"<h2>Seed backlog ({len(seeded)} listings that existed before the pipeline started)</h2>"
    body += '<details><summary>show</summary>'
    body += table([job_row(j, token, actions=acts) for j in seeded[:400]], ["Job", "Where", "Sponsorship / flags", "State", "Link", ""])
    if len(seeded) > 400:
        body += f'<p class="muted">showing 400 of {len(seeded)}</p>'
    body += "</details>"
    return page("Pending", body, active="/pending", counts=counts)


def backlog_page(jobs: list[Job], token: str, counts: dict, queued: list[int], badges: dict[int, str] | None = None) -> str:
    rows = []
    badges = badges or {}
    for j in jobs:
        acts: list[tuple[str, str, str]] = []
        if j.state == JobState.FAILED:
            acts.append(("retry", "Retry", ""))
        if j.state == JobState.NEEDS_JD:
            acts.append(("refetch-jd", "Refetch JD", ""))
        extra = ' <span class="muted">(queued)</span>' if j.id in queued else ""
        if j.failed_stage:
            extra += f' <span class="muted">failed at {e(j.failed_stage)}</span>'
        if "awaiting_lane" in j.flags:
            extra += ' <span class="muted">waits for an approved archetype</span>'
        rows.append(job_row(j, token, actions=acts, checkbox=True, extra=extra, badge=badges.get(j.id, "")))
    body = (f'<form method="post" action="/backlog/run">{csrf_field(token)}'
            f'<p><button class="primary" type="submit">Run selected</button> '
            f'<select name="stop_after"><option value="">through the whole pipeline</option>'
            f'<option value="ideamaker">stop after ideamaker</option><option value="builder">stop after builder</option>'
            f'<option value="reconcile">stop after reconcile</option></select></p>'
            + table(rows, ["", "Job", "Where", "Sponsorship / flags", "State", "Link", ""]) + "</form>")
    return page("Backlog", body, active="/backlog", counts=counts)


def running_page(items: list[dict], pool_status: dict, collector: dict, counts: dict, usage: dict | None = None) -> str:
    rows = []
    for it in items:
        run: StageRun = it["run"]
        job: Job = it["job"]
        tail = "\n".join(it.get("tail", []))
        rows.append(f"<tr><td><a href=\"/job/{job.id}\">{e(job.company)}</a><br><span class=\"muted\">{e(job.title)}</span></td>"
                    f"<td>{e(run.stage)} (attempt {run.attempt}, fix rounds {run.fix_rounds})<br><span class=\"muted\">{e(it.get('status', ''))}</span></td>"
                    f"<td>{e(run.started_at)}<br><span class=\"muted\">heartbeat {e(run.heartbeat_at)}</span></td>"
                    f"<td>{wk(it.get('cost', 0))}</td>"
                    f"<td><a href=\"/job/{job.id}/log?stage={e(run.stage)}&amp;attempt={run.attempt}\">log</a><pre>{e(tail)}</pre></td></tr>")
    gov = pool_status.get("governor") or {}
    banner = ""
    if gov.get("enabled"):
        if gov.get("paused"):
            fg = gov.get("free_gb")
            banner = (f'<div class="card"><span class="flag amber">paused for system load</span> '
                      f'new builds are held while free RAM is low'
                      f'{f" ({fg} GB free)" if fg is not None else ""}; resumes automatically at {gov.get("resume_gb")} GB.</div>')
        else:
            fg = gov.get("free_gb")
            banner = f'<p class="muted">Resource governor active{f" ({fg} GB RAM free)" if fg is not None else ""}.</p>'
    body = usage_card(usage) + banner + table(rows, ["Job", "Stage", "Started", "Usage so far", "Last lines"])
    body += f'<div class="card"><b>Worker</b> {e(json.dumps(pool_status))}<br><b>Collector</b> {e(json.dumps(collector))}</div>'
    return page("Running", body, active="/running", counts=counts, refresh=10)


def smartapply_fill_button(job: Job, smartapply_url: str) -> str:
    """Hands the job to Smart Apply (sibling project on its own port), which queues it, fills the application in
    its own Chrome window and stops before Submit. A plain cross-site form post: Smart Apply trusts the Origin."""
    if not smartapply_url:
        return ""
    return (f'<form class="inline" method="post" action="{e(smartapply_url.rstrip("/"))}/fill" target="_blank" '
            f'title="Queue and fill this application in Smart Apply (it never submits)">'
            f'<input type="hidden" name="job_id" value="{job.id}"><button type="submit">Fill application</button></form>')


def done_page(items: list[dict], token: str, counts: dict, *, smartapply_url: str = "") -> str:
    rows = []
    for it in items:
        job: Job = it["job"]
        links = []
        if job.role_folder:
            links.append(f'<a href="{e(file_url(job.role_folder))}">folder</a>')
            for pdf in it.get("pdfs", []):
                links.append(f'<a href="{e(file_url(pdf))}">{e(Path(pdf).name)}</a>')
            if it.get("review"):
                links.append(f'<a href="{e(file_url(it["review"]))}">review</a>')
        for r in it.get("repos", []):
            links.append(f'<a href="{e(r)}" target="_blank" rel="noopener">{e(r.rsplit("/", 1)[-1])}</a>')
        verdict = it.get("verdict", "")
        applied = it.get("applied")
        action = "unapplied" if applied else "applied"
        date = e((it.get("applied_at") or "")[:10])
        checkbox = (f'<form class="inline" method="post" action="/job/{job.id}/{action}">{csrf_field(token)}'
                    f'<label class="applied"><input type="checkbox" onchange="this.form.submit()"{" checked" if applied else ""}> '
                    f'{"applied " + date if applied else "mark applied"}</label></form>')
        if job.active:
            posting = f'<a href="{e(job.url)}" target="_blank" rel="noopener">apply</a>'
        else:
            seen = e((job.last_seen or "")[:10])
            posting = (f'<span class="flag red" title="listing no longer found in the latest source poll">expired</span> '
                       f'<a href="{e(job.url)}" target="_blank" rel="noopener" class="muted" title="last confirmed live {seen}">check</a>')
        if job.active and not applied:
            posting += " " + smartapply_fill_button(job, smartapply_url)
        rowcls = " ".join(c for c in [("applied-row" if applied else ""), ("expired-row" if not job.active else "")] if c)
        rows.append(f"<tr class=\"{rowcls}\"><td>{checkbox}</td>"
                    f"<td><a href=\"/job/{job.id}\">{e(job.company)}</a><br><span class=\"muted\">{e(job.title)}</span></td>"
                    f"<td>{state_html(job.state)}{lane_badge(it.get('badge', ''))} {e(verdict)}</td><td>{wk(it.get('cost', 0))}</td>"
                    f"<td>{' | '.join(links)}</td><td>{posting}</td></tr>")
    n_live = sum(1 for it in items if it["job"].active)
    n_exp = len(items) - n_live
    summary = f'<p class="muted">{n_live} live · {n_exp} expired (listing no longer found at its source)</p>'
    return page("Done", summary + table(rows, ["Applied", "Job", "Result", "Cost", "Artifacts", "Posting"]), active="/done", counts=counts)


def screened_page(jobs: list[Job], token: str, counts: dict, badges: dict[int, str] | None = None) -> str:
    badges = badges or {}
    rows = [job_row(j, token, actions=[("approve" if j.state == JobState.IGNORED else "override", "Override: run it", "")],
                    badge=badges.get(j.id, ""),
                    extra=f'<br><span class="muted">{e(j.screen_reason or ("ignored automatically (company tier)" if j.state == JobState.IGNORED and not j.decided_at else ""))}</span>')
            for j in jobs]
    return page("Screened out and ignored", table(rows, ["Job", "Where", "Sponsorship / flags", "State / reason", "Link", ""]),
                active="/screened", counts=counts)


def add_page(token: str, counts: dict, message: str = "") -> str:
    msg = f'<div class="banner">{e(message)}</div>' if message else ""
    body = (msg + f'<div class="card"><form method="post" action="/add">{csrf_field(token)}<div class="row">'
            '<label>Company<input type="text" name="company" required></label>'
            '<label>Title<input type="text" name="title" required></label>'
            '<label>Apply URL<input type="url" name="url" required></label>'
            '<label>Location<input type="text" name="location"></label>'
            '<label>Category<select name="category"><option>Software</option><option>AI/ML/Data</option><option>Quant</option><option>Other</option></select></label>'
            '</div><p><label>Job description (paste the full text; leave empty to let the pipeline fetch it)<textarea name="jd"></textarea></label></p>'
            '<p><button class="primary" type="submit">Add and run</button></p></form></div>')
    return page("Add a job", body, active="/add", counts=counts)


def watchlist_page(entries: list[WatchlistEntry], token: str, counts: dict) -> str:
    rows = []
    for w in entries:
        target = w.board or f"{w.host}/{w.site}"
        rows.append(f"<tr><td>{e(w.company)}</td><td>{e(w.ats)}<br><span class=\"muted\">{e(target)}</span></td>"
                    f"<td>{'on' if w.enabled else 'off'} every {w.poll_minutes} min<br><span class=\"muted\">{e(w.added_by)}</span></td>"
                    f"<td>{e(w.last_polled_at or '')}<br><span class=\"muted\">{e(w.last_error or (str(w.last_count) + ' intern listings' if w.last_count is not None else ''))}</span></td>"
                    f"<td><form class=\"inline\" method=\"post\" action=\"/watchlist/{w.id}/poll\">{csrf_field(token)}<button>Poll now</button></form> "
                    f"<form class=\"inline\" method=\"post\" action=\"/watchlist/{w.id}/toggle\">{csrf_field(token)}<button>{'Disable' if w.enabled else 'Enable'}</button></form> "
                    f"<form class=\"inline\" method=\"post\" action=\"/watchlist/{w.id}/delete\">{csrf_field(token)}<button class=\"danger\">Delete</button></form></td></tr>")
    body = table(rows, ["Company", "Board", "Polling", "Last poll", ""])
    body += (f'<div class="card"><h2>Add a board</h2><form method="post" action="/watchlist/add">{csrf_field(token)}<div class="row">'
             '<label>Company<input type="text" name="company" required></label>'
             '<label>ATS<select name="ats"><option>greenhouse</option><option>lever</option><option>ashby</option><option>workday</option></select></label>'
             '<label>Board / company token (greenhouse, lever, ashby)<input type="text" name="board"></label>'
             '<label>Workday host<input type="text" name="host" placeholder="acme.wd5.myworkdayjobs.com"></label>'
             '<label>Workday tenant<input type="text" name="tenant"></label>'
             '<label>Workday site<input type="text" name="site" placeholder="Careers"></label>'
             '<label>Poll minutes<input type="text" name="poll_minutes" value="60"></label>'
             '</div><p><button class="primary">Add</button></p></form></div>')
    return page("Watchlist", body, active="/watchlist", counts=counts)


def fmt_minutes(m: int) -> str:
    m = int(m or 0)
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def costs_page(totals: dict, counts: dict, usage: dict | None = None) -> str:
    body = usage_card(usage)
    body += (f'<p class="muted">Shares of the weekly Claude limit, estimated from the size each run reports '
             f'(about {DOLLARS_PER_PCT:g} units per 1%). Last 7 days of runs: {wk(totals["last_7_days"])}.</p>')
    body += table([f"<tr><td>{e(r['stage'])}</td><td>{r['runs']}</td><td>{wk(r['cost'] / max(1, r['runs']))}</td><td>{fmt_minutes(r.get('minutes'))}</td></tr>"
                   for r in totals["per_stage"]],
                  ["Stage", "Runs", "Usage per run", "Time"])
    body += table([f"<tr><td><a href=\"/job/{r['id']}\">{e(r['company'])}</a> <span class=\"muted\">{e(r['title'])}</span></td>"
                   f"<td>{state_html(r['state'])}</td><td>{r['runs']}</td><td>{wk(r['cost'])}</td><td>{fmt_minutes(r.get('minutes'))}</td></tr>"
                   for r in totals["per_job"]],
                  ["Job", "State", "Runs", "Usage", "Time"])
    return page("Usage", body, active="/costs", counts=counts)


def job_page(job: Job, runs: list[StageRun], events: list[dict], token: str, counts: dict, cost: float,
             artifacts: list[str], queued: bool, *, smartapply_url: str = "", badge: str = "") -> str:
    acts: list[tuple[str, str, str]] = []
    if job.state in (JobState.NEW, JobState.PINGED, JobState.IGNORED):
        acts.append(("approve", "Approve and run", "primary"))
    if job.state in (JobState.NEW, JobState.PINGED):
        acts.append(("ignore", "Ignore", ""))
    if job.state == JobState.SCREENED_OUT:
        acts.append(("override", "Override and run", "primary"))
    if job.state == JobState.FAILED:
        acts.append(("retry", "Retry from failed stage", "primary"))
    if job.state in (JobState.JD_READY, JobState.IDEAMAKER_DONE, JobState.BUILT, JobState.RECONCILED, JobState.APPROVED) and not queued:
        acts.append(("requeue", "Run next stage(s)", "primary"))
    if job.state == JobState.NEEDS_JD:
        acts.append(("refetch-jd", "Refetch JD", ""))
    lane = badge.split()[-1] if badge else "full"
    if job.state == JobState.DONE and lane == "full":
        acts.append(("rereview", "Re-review", ""))
    if lane != "full" and job.state in (JobState.DONE, JobState.APPROVED, JobState.JD_READY, JobState.FAILED):
        acts.append(("upgrade-full", "Upgrade to full pipeline", ""))
    buttons = "".join(action_button(job.id, a, l, token, cls=c) for a, l, c in acts)
    if job.state == JobState.DONE and job.active and "applied" not in job.flags:
        buttons += smartapply_fill_button(job, smartapply_url)
    meta = job.to_public_dict()
    body = f'<div class="card"><p>{state_html(job.state)}{lane_badge(badge)} {"(queued)" if queued else ""} usage so far {wk(cost)}</p><p>{buttons}</p>'
    body += f'<p><a href="{e(job.url)}" target="_blank" rel="noopener">{e(job.url)}</a></p>'
    body += f"<p>{flags_html(job.flags)} <span class=\"muted\">{e(job.screen_reason or '')}</span></p>"
    if job.role_folder:
        body += f'<p>Role folder: <a href="{e(file_url(job.role_folder))}">{e(job.role_folder)}</a></p>'
        if artifacts:
            body += "<p>" + " | ".join(f'<a href="{e(file_url(a))}">{e(Path(a).name)}</a>' for a in artifacts) + "</p>"
    body += f'<details><summary>metadata</summary><pre>{e(json.dumps(meta, indent=2, ensure_ascii=False))}</pre></details></div>'
    if job.state == JobState.NEEDS_JD or not job.jd_text:
        body += (f'<div class="card"><h2>Paste the job description</h2><form method="post" action="/job/{job.id}/jd">{csrf_field(token)}'
                 '<textarea name="jd" required></textarea><p><button class="primary">Save and run</button></p></form></div>')
    else:
        body += f'<div class="card"><details><summary>job description ({len(job.jd_text)} chars, via {e(job.jd_source)})</summary><pre>{e(job.jd_text)}</pre></details></div>'
    rows = []
    for r in runs:
        rows.append(f"<tr><td>{e(r.stage)}</td><td>{r.attempt}</td><td>{e(r.status)}</td><td>{r.fix_rounds}</td><td>{wk(r.cost_usd)}</td>"
                    f"<td>{e(r.started_at)}<br>{e(r.finished_at or '')}</td><td>{e(r.session_id or '')}</td>"
                    f"<td><a href=\"/job/{job.id}/log?stage={e(r.stage)}&amp;attempt={r.attempt}\">log</a><br><span class=\"muted\">{e((r.error or '')[:300])}</span></td></tr>")
    body += "<h2>Stage runs</h2>" + table(rows, ["Stage", "Attempt", "Status", "Fix rounds", "Usage", "Started / finished", "Session", "Log / error"])
    ev_rows = [f"<tr><td>{e(ev['ts'])}</td><td>{e(ev['event'])}</td><td>{e(ev.get('from_state') or '')} &rarr; {e(ev.get('to_state') or '')}</td>"
               f"<td><span class=\"muted\">{e(json.dumps(ev.get('detail'), ensure_ascii=False)[:400] if ev.get('detail') else '')}</span></td></tr>" for ev in events]
    body += "<h2>Events</h2>" + table(ev_rows, ["When", "Event", "Transition", "Detail"])
    return page(f"{job.company}: {job.title}", body, counts=counts)


def decide_page(job: Job, decision: str, already: bool) -> str:
    verb = {"approved": "Approved", "ignored": "Ignored"}.get(decision, decision)
    note = "Already recorded." if already else ("Queued." if decision == "approved" else "Will not be run.")
    return ('<!doctype html><meta charset="utf-8"><title>Recorded</title>'
            '<body style="font:16px system-ui;padding:2em">'
            f'<h2>{e(verb)}: {e(job.company)}, {e(job.title)}</h2><p>{e(note)} <a href="/job/{job.id}">Open job</a> | <a href="/backlog">Backlog</a></p>'
            '<script>setTimeout(function(){window.close();},1500);</script></body>')


def message_page(title: str, msg: str, back: str = "/") -> str:
    return page(title, f'<div class="card"><p>{e(msg)}</p><p><a href="{e(back)}">back</a></p></div>')
