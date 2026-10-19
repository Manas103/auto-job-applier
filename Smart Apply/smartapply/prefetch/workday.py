"""Workday: everything an application needs is public before sign-in (research/03-ats-mechanics.md §1, verified
live 2026-09-25 on Amgen, DraftKings, P&G, Salesforce, Stryker):

- job detail      GET {base}/wday/cxs/{tenant}/{site}/job/{externalPath}   -> jobPostingInfo.{id, questionnaireId,
                  secondaryQuestionnaireId, supplementaryQuestionnaireId, canApply, includeResumeParsing, externalUrl}
- questionnaire   GET {base}/wday/calypso/cxs/common/{tenant}/questionnaire/{id}   (branching follow-ups nest under
                  possibleAnswers[].branchingQuestion)
- employer flags  GET {base}/wday/cxs/{tenant}/{site}/approot                   -> featureFlags.accountVerification
- password rules  GET {base}/wday/calypso/cxs/account/{tenant}/{site}/account/configuration

Each question renders on the flow page as the control `{primary|secondary|supplementary}Questionnaire--{questionId}`,
which is the Question id here. My Information / My Experience / Voluntary Disclosures / Self Identify are not
questions: the adapter fills them from the profile. Some tenants front their site with Cloudflare that blocks plain
HTTP (Target: 403); those are read through a headless Chromium instead.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from ..log import get_logger
from ..models import Application, Question
from ..util import HttpError, http_get
from . import register

log = get_logger("prefetch.workday")

# Type descriptors are localized text; the ids are hard-coded in Workday's front-end (build 2026.38) and stable.
_TYPE_BY_ID = {
    "c4d9bd57996c10002583f04b9fc30042": "select",
    "c4d9bd57996c100025ab7d705b150045": "multiselect",
    "c4d9bd57996c100025ab7d7f5aed0046": "textarea",
    "53d73448d8de1000063827b5cb68003f": "number",
    "85fb160ed47910001589e3a427670070": "date",
    "287357c143e210001cddbc6df0719cba": "file",
}
_TYPE_BY_DESC = [
    (re.compile(r"multi[- ]?select", re.I), "multiselect"),
    (re.compile(r"single[- ]?select|multiple choice", re.I), "select"),
    (re.compile(r"numeric|number", re.I), "number"),
    (re.compile(r"date", re.I), "date"),
    (re.compile(r"upload|attach|file", re.I), "file"),
    (re.compile(r"text", re.I), "textarea"),
]
QUESTIONNAIRES = (("questionnaireId", "primary", "applyFlowPrimaryQuestionsPage"),
                  ("secondaryQuestionnaireId", "secondary", "applyFlowSecondaryQuestionsPage"),
                  ("supplementaryQuestionnaireId", "supplementary", "applyFlowSupplementaryQuestionsPage"))


class PostingGone(Exception):
    """The job posting no longer exists or no longer accepts applications."""


# ---------------------------------------------------------------- HTTP (plain first, headless Chromium on 403)

def _http_get_json(url: str, **kw) -> Any:   # monkeypatch target for tests
    return json.loads(http_get(url, headers={"Accept": "application/json", "Accept-Language": "en-US"}, **kw).decode("utf-8", "replace"))


def _browser_get_json(base: str, paths: list[str], timeout_s: float = 30.0) -> dict[str, Any]:
    """Cloudflare-fronted tenants: load the site root in Playwright's own headless Chromium (never the SmartApply
    profile) and fetch the JSON endpoints from inside that page. Returns {path: json | None}."""
    out: dict[str, Any] = {p: None for p in paths}
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        return out
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            page = browser.new_page(locale="en-US")
            try:
                page.goto(base + "/", timeout=int(timeout_s * 1000), wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                for p in paths:
                    res = page.evaluate("""async (p) => { const r = await fetch(p, {headers: {accept: 'application/json'}});
                        const t = await r.text(); return {status: r.status, text: t}; }""", p)
                    if int(res.get("status", 0)) == 200:
                        try:
                            out[p] = json.loads(res["text"])
                        except ValueError:
                            out[p] = None
                    elif int(res.get("status", 0)) == 404:
                        out[p] = {"__status__": 404}
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001
        log.info("browser fetch failed for %s: %s", base, e)
    return out


class _Site:
    def __init__(self, params: dict[str, Any], url: str):
        self.host = params.get("host") or re.sub(r"^https?://", "", url).split("/")[0]
        self.tenant = params["tenant"]
        self.site = params["site"]
        self.external_path = params.get("external_path") or ""
        self.base = f"https://{self.host}"
        self._browser_cache: dict[str, Any] | None = None

    def path_job(self) -> str:
        return f"/wday/cxs/{self.tenant}/{self.site}{self.external_path}"

    def path_questionnaire(self, qid: str) -> str:
        return f"/wday/calypso/cxs/common/{self.tenant}/questionnaire/{qid}"

    def path_approot(self) -> str:
        return f"/wday/cxs/{self.tenant}/{self.site}/approot"

    def path_account_config(self) -> str:
        return f"/wday/calypso/cxs/account/{self.tenant}/{self.site}/account/configuration"

    def get(self, path: str) -> Any:
        try:
            return _http_get_json(self.base + path)
        except HttpError as e:
            if e.status == 404:
                raise PostingGone(f"{path}: not found") from e
            if e.status not in (403, 429, 503):
                raise
            log.info("%s: HTTP %s on plain fetch; using a headless browser", self.host, e.status)
            res = _browser_get_json(self.base, [path])
            val = res.get(path)
            if isinstance(val, dict) and val.get("__status__") == 404:
                raise PostingGone(f"{path}: not found") from e
            if val is None:
                raise
            return val


# ---------------------------------------------------------------- fetch

def fetch_raw(app: Application) -> dict[str, Any]:
    params = dict(app.ats_params or {})
    if not params.get("tenant") or not params.get("site"):
        from ..intake import detect_ats
        kind, params = detect_ats(app.url)
        if kind != "workday":
            raise ValueError(f"not a Workday URL: {app.url}")
    site = _Site(params, app.url)
    job = site.get(site.path_job())
    jpi = job.get("jobPostingInfo") or {}
    if not jpi:
        raise PostingGone("job detail has no jobPostingInfo")
    raw: dict[str, Any] = {"job": job, "questionnaires": {}, "approot": None, "account": None, "site": params}
    for key, name, _page in QUESTIONNAIRES:
        qid = jpi.get(key)
        if qid:
            raw["questionnaires"][name] = site.get(site.path_questionnaire(str(qid)))
    try:
        raw["approot"] = site.get(site.path_approot())
    except (HttpError, PostingGone) as e:
        log.info("approot unavailable for %s: %s", site.host, e)
    try:
        raw["account"] = site.get(site.path_account_config())
    except (HttpError, PostingGone) as e:
        log.info("account configuration unavailable for %s: %s", site.host, e)
    return raw


# ---------------------------------------------------------------- normalize

def _clean(body: str) -> str:
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", body or "", flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def _qtype(q: dict[str, Any]) -> str:
    t = q.get("type") or {}
    tid = str(t.get("id") or "")
    if tid in _TYPE_BY_ID:
        return _TYPE_BY_ID[tid]
    desc = str(t.get("descriptor") or "")
    for rx, kind in _TYPE_BY_DESC:
        if rx.search(desc):
            return kind
    return "textarea"


def _walk(q: dict[str, Any], prefix: str, page: str, section: str, out: list[Question], *, parent: str | None = None,
          when: str | None = None, seen: set[str] | None = None) -> None:
    qid = str(q.get("id") or "")
    if not qid:
        return
    seen = seen if seen is not None else set()
    if qid in seen:
        # Tenants such as HP wire the same follow-up under several answers (179 occurrences of 46 questions):
        # one Question per id, with the first parent that reveals it.
        return
    seen.add(qid)
    kind = _qtype(q)
    answers = q.get("possibleAnswers") or []
    options = [_clean(str(a.get("answerText") or "")) for a in answers]
    extra: dict[str, Any] = {"questionnaire": section, "page": page, "wd_type": str((q.get("type") or {}).get("descriptor") or ""),
                             "answer_ids": {_clean(str(a.get("answerText") or "")): str(a.get("id") or "") for a in answers}}
    if parent:
        extra.update({"branch_of": parent, "branch_when": when, "dom_optional": True})
    out.append(Question(id=f"{prefix}--{qid}", label=_clean(str(q.get("body") or "")), type=kind,
                        required=bool(q.get("required")), options=[o for o in options if o], section="questions", extra=extra))
    for a in answers:
        child = a.get("branchingQuestion")
        if isinstance(child, dict):
            _walk(child, prefix, page, section, out, parent=f"{prefix}--{qid}", when=_clean(str(a.get("answerText") or "")), seen=seen)
        elif isinstance(child, list):
            for c in child:
                if isinstance(c, dict):
                    _walk(c, prefix, page, section, out, parent=f"{prefix}--{qid}", when=_clean(str(a.get("answerText") or "")), seen=seen)


def normalize(raw: dict[str, Any]) -> list[Question]:
    out: list[Question] = []
    for _key, name, page in QUESTIONNAIRES:
        qn = (raw.get("questionnaires") or {}).get(name)
        if not qn:
            continue
        prefix = f"{name}Questionnaire"
        qs = sorted(qn.get("questions") or [], key=lambda x: str(x.get("order") or ""))
        # Top-level questions first, so a question that is both listed and branched is plain, not conditional.
        seen: set[str] = set()
        for q in qs:
            _walk({**q, "possibleAnswers": [{**a, "branchingQuestion": None} for a in (q.get("possibleAnswers") or [])]},
                  prefix, page, name, out, seen=seen)
        for q in qs:
            for a in q.get("possibleAnswers") or []:
                child = a.get("branchingQuestion")
                kids = child if isinstance(child, list) else ([child] if isinstance(child, dict) else [])
                for c in kids:
                    _walk(c, prefix, page, name, out, parent=f"{prefix}--{q.get('id')}", when=_clean(str(a.get("answerText") or "")), seen=seen)
    return out


def flow_params(raw: dict[str, Any]) -> dict[str, Any]:
    """What the adapter needs beyond the questions; stored in app.ats_params."""
    jpi = (raw.get("job") or {}).get("jobPostingInfo") or {}
    ff = ((raw.get("approot") or {}).get("featureFlags")) or {}
    acct = raw.get("account") or {}
    ext = str(jpi.get("externalUrl") or "").rstrip("/")
    return {
        "job_posting_id": jpi.get("id"),
        "job_req_id": jpi.get("jobReqId"),
        "job_title": jpi.get("title"),
        "can_apply": bool(jpi.get("canApply", True)),
        "include_resume_parsing": bool(jpi.get("includeResumeParsing")),
        "questionnaire_ids": {name: jpi.get(key) for key, name, _p in QUESTIONNAIRES if jpi.get(key)},
        "external_url": ext or None,
        "apply_url": (ext + "/apply/applyManually") if ext else None,
        "account_verification": bool(ff.get("accountVerification")),
        "require_candidate_accounts": bool(ff.get("requireCandidateAccounts", True)),
        "password_rules": acct.get("passwordRules") or [],
        "consent_required": bool((acct.get("consentInfo") or {}).get("message")),
    }


@register("workday")
def prefetch(app: Application) -> list[Question]:
    raw = fetch_raw(app)
    params = flow_params(raw)
    if not params["can_apply"]:
        raise PostingGone("posting no longer accepts applications (canApply=false)")
    app.ats_params = {**(app.ats_params or {}), **params}
    app.tenant = raw["site"].get("tenant") or app.tenant
    qs = normalize(raw)
    log.info("workday %s: %d question(s) across %s; verification=%s", app.tenant, len(qs),
             ",".join(params["questionnaire_ids"]) or "no questionnaire", params["account_verification"])
    return qs
