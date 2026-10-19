"""Ashby: the site's own GraphQL, no auth (jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting).

Verified live 2026-09-24 on Northwood Space and Ramp. `field` is a JSON scalar carrying `path`, `title`, `type`
(String, Email, Phone, Location, File, LongText, Boolean, Date, Number, ValueSelect, MultiValueSelect) and, for
selects, `selectableValues`. `surveyForms` hold the EEOC questions (`_systemfield_eeoc_*`). Undocumented; the
capture in research/captures/ashby_gql_northwood.json is the test fixture.
"""

from __future__ import annotations

import json
import re

from ..log import get_logger
from ..models import Application, Question
from ..util import http_post
from . import register

log = get_logger("prefetch.ashby")

GRAPHQL_URL = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting"
QUERY = ("query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) { "
         "jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) { "
         "id title recaptchaAction shouldAskForTextingConsent "
         "applicationForm { id sections { title descriptionHtml fieldEntries { ... on FormFieldEntry { id field isRequired descriptionHtml isHidden } } } } "
         "surveyForms { id sections { title descriptionHtml fieldEntries { ... on FormFieldEntry { id field isRequired } } } } } }")

_TYPE = {"String": "text", "Email": "text", "Phone": "text", "LongText": "textarea", "Boolean": "select",
         "ValueSelect": "select", "MultiValueSelect": "multiselect", "File": "file", "Location": "text",
         "Date": "date", "Number": "number"}


def hosted_url(org: str, posting_id: str) -> str:
    return f"https://jobs.ashbyhq.com/{org}/{posting_id}/application"


def _http_post(url: str, data: bytes, **kw) -> bytes:   # monkeypatch target for tests
    return http_post(url, data, **kw)


def fetch_raw(org: str, posting_id: str) -> dict:
    body = {"operationName": "ApiJobPosting", "variables": {"organizationHostedJobsPageName": org, "jobPostingId": posting_id},
            "query": QUERY}
    raw = _http_post(GRAPHQL_URL, json.dumps(body).encode("utf-8"),
                     headers={"Content-Type": "application/json", "Accept": "application/json"}, timeout=30)
    d = json.loads(raw.decode("utf-8", "replace"))
    if d.get("errors"):
        raise ValueError("ashby graphql: " + "; ".join(str(e.get("message")) for e in d["errors"])[:300])
    return d


def _strip_html(s: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def _entry_question(fe: dict, section: str) -> Question | None:
    f = fe.get("field") or {}
    if isinstance(f, str):
        try:
            f = json.loads(f)
        except json.JSONDecodeError:
            return None
    if fe.get("isHidden"):
        return None
    kind = str(f.get("type") or "String")
    path = str(f.get("path") or f.get("id") or "")
    if not path:
        return None
    opts: list[str] = []
    if kind == "Boolean":
        opts = ["Yes", "No"]
    for v in f.get("selectableValues") or []:
        if v.get("isArchived"):
            continue
        opts.append(str(v.get("label", v.get("value", ""))))
    title = _strip_html(f.get("title")).replace("Â ", " ").replace(" ", " ").strip()
    sec = section
    if path.startswith("_systemfield_eeoc") or (section == "survey" and re.search(r"gender|race|ethnic|veteran|disab", title, re.I)):
        sec = "eeoc"
    return Question(id=path, label=title, type=_TYPE.get(kind, "text"), required=bool(fe.get("isRequired")), options=opts,
                    section=sec, extra={"kind": kind, "description": _strip_html(fe.get("descriptionHtml")),
                                        "entry_id": fe.get("id")})


def normalize(raw: dict) -> list[Question]:
    jp = ((raw.get("data") or {}).get("jobPosting")) or {}
    out: list[Question] = []
    for s in ((jp.get("applicationForm") or {}).get("sections")) or []:
        for fe in s.get("fieldEntries") or []:
            q = _entry_question(fe, "application")
            if q is not None:
                out.append(q)
    for sf in jp.get("surveyForms") or []:
        for s in sf.get("sections") or []:
            for fe in s.get("fieldEntries") or []:
                q = _entry_question(fe, "survey")
                if q is not None:
                    out.append(q)
    if jp.get("shouldAskForTextingConsent"):
        out.append(Question(id="_texting_consent", label="Consent to receive text messages about your application",
                            type="select", required=False, options=["Yes", "No"], section="application",
                            extra={"kind": "TextingConsent", "dom_optional": True}))
    return out


@register("ashby")
def prefetch(app: Application) -> list[Question]:
    org = app.ats_params.get("org")
    posting = app.ats_params.get("posting_id") or app.ats_id
    if not org or not posting:
        raise ValueError("ashby org or posting id unknown")
    qs = normalize(fetch_raw(org, str(posting)))
    for q in qs:
        q.extra.setdefault("hosted_url", hosted_url(org, str(posting)))
    return qs
