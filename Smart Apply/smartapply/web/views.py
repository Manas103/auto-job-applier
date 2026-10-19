"""HTML rendering for the dashboard. f-strings plus html.escape, like the pipeline's views."""

from __future__ import annotations

import html
import json
from typing import Any, Iterable

from ..models import Application

NAV = [("/queue", "Queue"), ("/review", "Review"), ("/needs-human", "Needs you"), ("/done", "Applied"), ("/costs", "Costs"),
       ("/memory", "Memory"), ("/doctor", "Doctor")]


def e(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def page(title: str, body: str, *, active: str = "", counts: dict[str, int] | None = None, refresh: int | None = None) -> str:
    nav = "".join(f'<a href="{p}" class="{"active" if p == active else ""}">{e(label)}</a>' for p, label in NAV)
    cnt = " | ".join(f"{e(k)} {v}" for k, v in (counts or {}).items())
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{e(title)}</title>{meta}'
            f'<link rel="stylesheet" href="/static/style.css"><meta name="viewport" content="width=device-width, initial-scale=1"></head>'
            f'<body><header><span class="brand">Smart Apply</span>{nav}<span class="counts">{cnt}</span></header>'
            f'<main><h1>{e(title)}</h1>{body}</main></body></html>')


def state_html(state: str) -> str:
    return f'<span class="state {e(state)}">{e(state)}</span>'


def csrf_field(token: str) -> str:
    return f'<input type="hidden" name="csrf" value="{e(token)}">'


def action_button(app_id: int, action: str, label: str, token: str, *, cls: str = "") -> str:
    return (f'<form class="inline" method="post" action="/app/{app_id}/{action}">{csrf_field(token)}'
            f'<button class="{cls}" type="submit">{e(label)}</button></form> ')


def app_row(app: Application, token: str, *, actions: Iterable[tuple[str, str, str]] = ()) -> str:
    acts = "".join(action_button(app.id, a, l, token, cls=c) for a, l, c in actions)
    tab = f'<a href="{e(app.tab_url or app.url)}" target="_blank">tab</a>' if app.tab_url else ""
    return (f'<tr><td><a href="/app/{app.id}">{e(app.company)}</a><br><span class="muted">{e(app.title)}</span></td>'
            f'<td>{e(app.ats or "unknown")}<br><span class="muted">{tab}</span></td>'
            f'<td>{state_html(app.state.value)}<br><span class="muted">{e(app.reason or "")}</span></td>'
            f'<td><span class="muted">{e(app.updated_at[:16].replace("T", " "))}</span></td><td>{acts}</td></tr>')


def table(rows: Iterable[str], headers: Iterable[str]) -> str:
    rows = list(rows)
    if not rows:
        return '<p class="muted">Nothing here.</p>'
    th = "".join(f"<th>{e(h)}</th>" for h in headers)
    return f'<div class="card"><table><thead><tr>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def list_page(title: str, apps: list[Application], token: str, *, active: str, counts: dict[str, int],
              actions_for: Any, banner: str = "", refresh: int | None = None, footer: str = "") -> str:
    rows = [app_row(a, token, actions=actions_for(a)) for a in apps]
    body = ((f'<div class="banner">{e(banner)}</div>' if banner else "")
            + table(rows, ["Job", "ATS", "State", "Updated", "Actions"]) + footer)
    return page(title, body, active=active, counts=counts, refresh=refresh)


def candidates_page(rows: list[dict], token: str, counts: dict[str, int]) -> str:
    trs = []
    for r in rows:
        trs.append(f'<tr><td>{e(r["company"])}<br><span class="muted">{e(r["title"])}</span></td><td>{e(r.get("ats") or "?")}</td>'
                   f'<td><a href="{e(r["url"])}" target="_blank">posting</a></td>'
                   f'<td><form class="inline" method="post" action="/queue/add">{csrf_field(token)}'
                   f'<input type="hidden" name="job_id" value="{r["id"]}"><button type="submit">Queue</button></form></td></tr>')
    body = ('<p class="muted">Pipeline jobs with a finished role folder that are not yet applied or queued here.</p>'
            + table(trs, ["Job", "ATS", "Link", ""]))
    return page("Candidates from the pipeline", body, active="/queue", counts=counts)


def app_page(app: Application, events: list[dict], fields: list[Any], token: str, counts: dict[str, int],
             files: dict[str, str], actions: Iterable[tuple[str, str, str]]) -> str:
    acts = "".join(action_button(app.id, a, l, token, cls=c) for a, l, c in actions)
    ev = "".join(f'<tr><td class="muted">{e(x["ts_local"][:19].replace("T", " "))}</td><td>{e(x["event"])}</td>'
                 f'<td>{e(x.get("from_state") or "")} → {e(x.get("to_state") or "")}</td>'
                 f'<td><span class="muted">{e(json.dumps(x["detail"], ensure_ascii=False) if x.get("detail") else "")}</span></td></tr>'
                 for x in reversed(events))
    fl = "".join(f'<tr><td>{e(f["page"])}</td><td>{e(f["label"] or f["name"])}</td><td>{e(f["intended"])}</td>'
                 f'<td>{e(f["observed"])}</td><td>{e(f["status"])}</td><td class="muted">{e(f["note"] or "")}</td></tr>'
                 for f in fields)
    fsec = "".join(f"<h2>{e(name)}</h2><pre>{e(text)}</pre>" for name, text in files.items())
    body = (f'<div class="card"><p>{state_html(app.state.value)} {e(app.reason or "")}<br>'
            f'<a href="{e(app.url)}" target="_blank">{e(app.url)}</a><br>'
            f'<span class="muted">ATS {e(app.ats)} | folder {e(app.role_folder)} | workdir {e(app.workdir)}</span></p>'
            f'<p>{acts}</p></div>'
            f'<h2>Fields</h2>{table([fl], ["Page", "Field", "Intended", "Observed", "Status", "Note"]) if fl else "<p class=muted>No fields recorded yet.</p>"}'
            f'<h2>Events</h2>{table([ev], ["When", "Event", "Transition", "Detail"])}{fsec}')
    return page(f"{app.company}: {app.title}", body, active="", counts=counts)


def costs_page(totals: dict, counts: dict[str, int]) -> str:
    days = "".join(f'<tr><td>{e(r["day"])}</td><td>${r["c"]:.2f}</td><td>{r["n"]}</td></tr>' for r in totals["per_day"])
    apps = "".join(f'<tr><td><a href="/app/{r["app_id"]}">{e(r["company"] or r["app_id"])}</a> '
                   f'<span class="muted">{e(r["job_id"] or "")}</span></td><td>{e(r["ats"] or "")}</td>'
                   f'<td>${r["c"]:.2f}</td><td>{r["n"]}</td><td>{r["turns"] or 0}</td></tr>'
                   for r in totals["per_app"])
    kinds = "".join(f'<tr><td>{e(r["purpose"])}</td><td>{e(r["model"])}</td><td>${r["c"]:.2f}</td><td>{r["n"]}</td>'
                    f'<td>${(r["c"] / r["n"]) if r["n"] else 0:.2f}</td><td>{r["turns"] or 0}</td><td>{(r["secs"] or 0) / 60:.0f}</td></tr>'
                    for r in totals.get("per_purpose", []))
    body = (f'<div class="card"><span class="kpi"><b>${totals["total_usd"]:.2f}</b>total</span>'
            f'<span class="kpi"><b>{totals["runs"]}</b>claude runs</span></div>'
            f'<p class="muted">Every claude -p run is logged here. No spending cap is set; this page is the evidence for one.</p>'
            f'<h2>By purpose</h2>{table([kinds] if kinds else [], ["Purpose", "Model", "USD", "Runs", "USD per run", "Turns", "Minutes"])}'
            f'<h2>Per day</h2>{table([days] if days else [], ["Day", "USD", "Runs"])}'
            f'<h2>Per application</h2>{table([apps] if apps else [], ["Application", "ATS", "USD", "Runs", "Turns"])}')
    return page("Costs", body, active="/costs", counts=counts)


def memory_page(rows: list[dict], token: str, counts: dict[str, int]) -> str:
    trs = "".join(f'<tr><td>{e(r["question"])}</td><td>{e(r["answer"])}</td><td>{e(r["kind"])}</td>'
                  f'<td class="muted">{e(r["created_at"][:10])}</td>'
                  f'<td><form class="inline" method="post" action="/memory/{r["id"]}/forget">{csrf_field(token)}'
                  f'<button class="danger" type="submit">Forget</button></form></td></tr>' for r in rows)
    body = ('<p class="muted">Answers Claude wrote that you confirmed by submitting an application. The next application '
            'that asks the same question takes the stored answer before Claude is asked, and the browser agent sees the '
            'list too. Forget anything you would not want reused.</p>'
            + table([trs] if trs else [], ["Question", "Answer", "Kind", "Since", ""]))
    return page("Answer memory", body, active="/memory", counts=counts)


def doctor_page(checks: list[tuple[str, bool, str]], counts: dict[str, int]) -> str:
    rows = "".join(f'<tr><td>{e(n)}</td><td class="{"status-ok" if ok else "status-bad"}">{"ok" if ok else "FAIL"}</td><td class="muted">{e(d)}</td></tr>'
                   for n, ok, d in checks)
    return page("Doctor", table([rows], ["Check", "Status", "Detail"]), active="/doctor", counts=counts)


def message_page(title: str, text: str) -> str:
    return page(title, f"<p>{e(text)}</p>")
