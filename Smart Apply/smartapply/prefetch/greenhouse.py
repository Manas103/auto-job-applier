"""Greenhouse Job Board API: GET /v1/boards/{board}/jobs/{id}?questions=true (public, no key).

Verified live 2026-09-24 on 12 boards (research/03-ats-mechanics.md and the survey in this session). Field names in
the JSON equal the DOM ids on job-boards.greenhouse.io, so the adapter locates every control by its id. Two things
the API does not tell you, both handled here and in the adapter:

- The education block (`education: education_required|education_optional`) renders a different subset of the
  `school--0 / degree--0 / discipline--0 / start-* / end-*` controls on every board, so those questions are marked
  `dom_optional` and an absent control is "not on this form", not a failure.
- The EEOC block lists `race` and `gender`, but the form shows `hispanic_ethnicity` first and `race` only after
  "No" to it; `hispanic_ethnicity` is added here as `dom_optional` and the adapter also sweeps the DOM.

Company career sites embed postings as `?gh_jid=<id>` without naming the board; `discover_board` finds it.
"""

from __future__ import annotations

import json
import re
import urllib.parse

from ..log import get_logger
from ..models import Application, Question
from ..util import HttpError, http_get
from . import register

log = get_logger("prefetch.greenhouse")

_TYPE = {"input_text": "text", "textarea": "textarea", "input_file": "file", "input_hidden": "hidden",
         "multi_value_single_select": "select", "multi_value_multi_select": "multiselect"}

EDUCATION_QUESTIONS = [
    Question(id="school--0", label="School", type="select", required=False, section="education", extra={"dom_optional": True, "async": True}),
    Question(id="degree--0", label="Degree", type="select", required=True, section="education", extra={"dom_optional": True}),
    Question(id="discipline--0", label="Discipline", type="select", required=True, section="education", extra={"dom_optional": True}),
    Question(id="start-month--0", label="Start date month", type="select", required=False, section="education", extra={"dom_optional": True}),
    Question(id="start-year--0", label="Start date year", type="number", required=False, section="education", extra={"dom_optional": True}),
    Question(id="end-month--0", label="End date month", type="select", required=True, section="education", extra={"dom_optional": True}),
    Question(id="end-year--0", label="End date year", type="number", required=True, section="education", extra={"dom_optional": True}),
]

_BOARD_RE = [
    re.compile(r"greenhouse\.io/embed/job_(?:board|app)[^\"'\s]*?[?&]for=([A-Za-z0-9_-]+)"),
    re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/([A-Za-z0-9_-]+)/jobs/\d+"),
    re.compile(r"boards-api(?:\.eu)?\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)"),
    re.compile(r"[\"'](?:board_token|boardToken|gh_board|greenhouse_board)[\"']\s*:\s*[\"']([A-Za-z0-9_-]+)[\"']"),
]


def api_url(board: str, job_id: str, *, eu: bool = False) -> str:
    # One API host serves both regions: boards-api.eu.greenhouse.io does not resolve (IMC, 2026-10-06), while
    # boards-api.greenhouse.io returns EU-hosted postings with their questions. `eu` is kept for callers.
    return f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}?questions=true"


def hosted_url(board: str, job_id: str, *, eu: bool = False) -> str:
    host = "job-boards.eu.greenhouse.io" if eu else "job-boards.greenhouse.io"
    return f"https://{host}/{board}/jobs/{job_id}"


def _http_get(url: str, **kw) -> bytes:   # monkeypatch target for tests
    return http_get(url, **kw)


def fetch_raw(board: str, job_id: str, *, eu: bool = False) -> dict:
    return json.loads(_http_get(api_url(board, job_id, eu=eu), timeout=30).decode("utf-8", "replace"))


def _strip_html(s: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


# ---------------------------------------------------------------- board discovery for ?gh_jid= sites

def _board_guesses(page_url: str, company: str) -> list[str]:
    host = urllib.parse.urlsplit(page_url).netloc.lower()
    host = re.sub(r"^www\.", "", host)
    label = host.split(".")[0] if host else ""
    comp = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    comp_words = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|the)\b", "", (company or "").lower())
    comp_first = re.sub(r"[^a-z0-9]", "", comp_words.split()[0]) if comp_words.split() else ""
    raw = [label, comp, comp_first, label + "careers", comp + "careers", label + "jobs", comp + "jobs",
           label.replace("-", ""), comp + "inc", "fly" + label, label + "hq"]
    out: list[str] = []
    for g in raw:
        if g and g not in out:
            out.append(g)
    return out


def _board_from_html(html: str) -> str | None:
    for rx in _BOARD_RE:
        m = rx.search(html)
        if m:
            return m.group(1)
    return None


def _board_from_browser(page_url: str, timeout_s: float = 20.0) -> str | None:
    """Last resort for JS-rendered career pages: load the page in a headless Chromium (Playwright's own, never the
    SmartApply profile) and read the board token off the Greenhouse requests it makes."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        return None
    found: list[str] = []

    def on_request(req):
        b = _board_from_html(req.url)
        if b and b not in found:
            found.append(b)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.on("request", on_request)
            try:
                page.goto(page_url, timeout=int(timeout_s * 1000), wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                if not found:
                    found_html = _board_from_html(page.content())
                    if found_html:
                        found.append(found_html)
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001
        log.info("browser board discovery failed for %s: %s", page_url, e)
    return found[0] if found else None


def discover_board(page_url: str, job_id: str, *, company: str = "", eu: bool = False) -> str | None:
    """Find the board token for a posting embedded on a company site. Order: page HTML, API guesses, headless sniff."""
    html = ""
    try:
        html = _http_get(page_url, timeout=30).decode("utf-8", "replace")
    except HttpError as e:
        log.info("career page fetch failed (%s); trying guesses", e)
    b = _board_from_html(html) if html else None
    if b and _board_has_job(b, job_id, eu=eu):
        return b
    for g in _board_guesses(page_url, company):
        if _board_has_job(g, job_id, eu=eu):
            return g
    b = _board_from_browser(page_url)
    if b and _board_has_job(b, job_id, eu=eu):
        return b
    return None


def _board_has_job(board: str, job_id: str, *, eu: bool) -> bool:
    try:
        raw = fetch_raw(board, job_id, eu=eu)
        return str(raw.get("id") or "") == str(job_id) or bool(raw.get("questions"))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- normalize

def normalize(raw: dict) -> list[Question]:
    out: list[Question] = []
    for q in raw.get("questions") or []:
        for f in q.get("fields") or []:
            t = _TYPE.get(f.get("type", ""), "text")
            values = [{"label": v.get("label", ""), "value": str(v.get("value", ""))} for v in (f.get("values") or [])]
            out.append(Question(id=str(f["name"]), label=_strip_html(q.get("label")), type=t, required=bool(q.get("required")),
                                options=[v["label"] for v in values], section="application",
                                extra={"values": values, "description": _strip_html(q.get("description"))}))
    for q in raw.get("location_questions") or []:
        for f in q.get("fields") or []:
            if f.get("type") == "input_hidden":
                continue
            out.append(Question(id=str(f["name"]), label=_strip_html(q.get("label")), type=_TYPE.get(f.get("type", ""), "text"),
                                required=bool(q.get("required")), section="location", extra={"async": True}))
    if raw.get("education") in ("education_required", "education_optional"):
        required = raw.get("education") == "education_required"
        for q in EDUCATION_QUESTIONS:
            out.append(Question(id=q.id, label=q.label, type=q.type, required=q.required and required, section=q.section,
                                extra=dict(q.extra)))
    eeoc_ids: list[str] = []
    for block in raw.get("compliance") or []:
        for q in block.get("questions") or []:
            for f in q.get("fields") or []:
                values = [{"label": v.get("label", ""), "value": str(v.get("value", ""))} for v in (f.get("values") or [])]
                extra = {"values": values, "block": block.get("type")}
                if f["name"] == "race":
                    extra["dom_optional"] = True      # shown only after "No" to the Hispanic/Latino question
                out.append(Question(id=str(f["name"]), label=_strip_html(q.get("label")), type=_TYPE.get(f.get("type", ""), "select"),
                                    required=bool(q.get("required")), options=[v["label"] for v in values],
                                    section="eeoc", extra=extra))
                eeoc_ids.append(str(f["name"]))
    if ("race" in eeoc_ids or "gender" in eeoc_ids) and "hispanic_ethnicity" not in eeoc_ids:
        out.append(Question(id="hispanic_ethnicity", label="Are you Hispanic/Latino?", type="select", required=False,
                            section="eeoc", extra={"dom_optional": True}))
    demo = raw.get("demographic_questions") or {}
    for q in demo.get("questions") or []:
        opts = [o.get("label", "") for o in (q.get("answer_options") or [])]
        out.append(Question(id=f"demographic_{q.get('id')}", label=_strip_html(q.get("label")), type="select",
                            required=bool(q.get("required")), options=opts, section="demographic",
                            extra={"by_label": True, "answer_options": q.get("answer_options")}))
    return out


@register("greenhouse")
def prefetch(app: Application) -> list[Question]:
    board = app.ats_params.get("board")
    job_id = app.ats_params.get("job_id") or app.ats_id
    eu = bool(app.ats_params.get("eu"))
    if not job_id:
        raise ValueError("greenhouse job id unknown")
    if not board:
        board = discover_board(app.url, str(job_id), company=app.company, eu=eu)
        if not board:
            raise ValueError("greenhouse board token not found for this career site (embedded posting)")
        app.ats_params["board"] = board
        log.info("discovered greenhouse board %r for job %s", board, job_id)
    raw = fetch_raw(board, str(job_id), eu=eu)
    qs = normalize(raw)
    for q in qs:
        q.extra.setdefault("hosted_url", hosted_url(board, str(job_id), eu=eu))
    return qs
