"""Lever: the apply page (jobs.lever.co/{company}/{posting}/apply) embeds every custom question as JSON in hidden
`cards[<uuid>][baseTemplate]` inputs; field N of a card is the form control `cards[<uuid>][fieldN]`. Standard
fields (name, email, phone, location, org, urls[...]) and the EEO selects are plain server-rendered controls.
Verified live 2026-09-24 on Palantir (capture) and Xcimer (plain HTTP fetch works with a browser User-Agent).
"""

from __future__ import annotations

import html as htmllib
import json
import re

from ..log import get_logger
from ..models import Application, Question
from ..util import http_get
from . import register

log = get_logger("prefetch.lever")

_TYPE = {"text": "text", "textarea": "textarea", "dropdown": "select", "multiple-choice": "select",
         "multiple-select": "multiselect", "file": "file", "university": "select", "date": "text", "number": "number"}
_WIDGET = {"dropdown": "select", "multiple-choice": "radio", "multiple-select": "checkbox", "university": "select"}

STANDARD = [("name", "Full name", "text"), ("email", "Email", "text"), ("phone", "Phone", "text"),
            ("location", "Current location", "text"), ("org", "Current company", "text"),
            ("urls[LinkedIn]", "LinkedIn URL", "text"), ("urls[GitHub]", "GitHub URL", "text"),
            ("urls[Portfolio]", "Portfolio URL", "text"), ("urls[Twitter]", "Twitter URL", "text"),
            ("urls[Other]", "Other website", "text"), ("comments", "Additional information", "textarea")]

EEO_LABELS = {"gender": "Gender", "race": "Race", "veteran": "Veteran status", "disability": "Disability status"}


def apply_url(company: str, posting_id: str, *, eu: bool = False) -> str:
    host = "jobs.eu.lever.co" if eu else "jobs.lever.co"
    return f"https://{host}/{company}/{posting_id}/apply"


def _http_get(url: str, **kw) -> bytes:   # monkeypatch target for tests
    return http_get(url, **kw)


def fetch_html(company: str, posting_id: str, *, eu: bool = False) -> str:
    return _http_get(apply_url(company, posting_id, eu=eu), timeout=45).decode("utf-8", "replace")


def _attr(tag: str, name: str) -> str | None:
    m = re.search(r'\b' + re.escape(name) + r'\s*=\s*"([^"]*)"', tag)
    if m:
        return m.group(1)
    m = re.search(r"\b" + re.escape(name) + r"\s*=\s*'([^']*)'", tag)
    return m.group(1) if m else None


def _has_flag(tag: str, name: str) -> bool:
    return re.search(r"(\s|^)" + re.escape(name) + r"(\s|=|/|>|$)", tag) is not None


def parse_apply_html(text: str) -> list[Question]:
    out: list[Question] = []
    tags = {m.group(0): _attr(m.group(0), "name") for m in re.finditer(r"<(?:input|select|textarea)\b[^>]*>", text)}
    by_name: dict[str, str] = {}
    for tag, name in tags.items():
        if name and name not in by_name:
            by_name[name] = tag
    if "resume" in by_name or "resume-upload-input" in text:
        out.append(Question(id="resume", label="Resume/CV", type="file", required=True, section="application"))
    for name, label, qtype in STANDARD:
        tag = by_name.get(name)
        if tag is None:
            continue
        out.append(Question(id=name, label=label, type=qtype, required=_has_flag(tag, "required"), section="application",
                            extra={"widget": "location" if name == "location" else "text"}))
    for m in re.finditer(r"<input\b[^>]*name=\"cards\[([^\]]+)\]\[baseTemplate\]\"[^>]*>", text):
        tag = m.group(0)
        card_id = m.group(1)
        raw = _attr(tag, "value")
        if not raw:
            continue
        try:
            card = json.loads(htmllib.unescape(raw))
        except json.JSONDecodeError:
            log.warning("lever card %s: bad JSON", card_id)
            continue
        section = str(card.get("text") or "")[:80]
        for i, f in enumerate(card.get("fields") or []):
            kind = str(f.get("type") or "text")
            opts = [str(o.get("text", "")) for o in (f.get("options") or [])]
            out.append(Question(id=f"cards[{card_id}][field{i}]", label=_clean(f.get("text")), type=_TYPE.get(kind, "text"),
                                required=bool(f.get("required")), options=opts, section=section or "application",
                                extra={"kind": kind, "widget": _WIDGET.get(kind, "text"), "description": _clean(f.get("description")),
                                       "field_id": f.get("id")}))
    for m in re.finditer(r"<select\b[^>]*name=\"eeo\[(\w+)\]\"[^>]*>(.*?)</select>", text, re.S):
        key, body = m.group(1), m.group(2)
        opts = []
        for om in re.finditer(r"<option\b[^>]*value=\"([^\"]*)\"[^>]*>(.*?)</option>", body, re.S):
            if om.group(1).strip():
                opts.append(_clean(htmllib.unescape(om.group(2))))
        out.append(Question(id=f"eeo[{key}]", label=EEO_LABELS.get(key, key), type="select", required=False, options=opts,
                            section="eeoc", extra={"widget": "select"}))
    if "eeo[disabilitySignature]" in by_name:
        out.append(Question(id="eeo[disabilitySignature]", label="Disability form signature (name)", type="text", required=False,
                            section="eeoc", extra={"widget": "text", "dom_optional": True}))
    if "eeo[disabilitySignatureDate]" in by_name:
        out.append(Question(id="eeo[disabilitySignatureDate]", label="Disability form date", type="text", required=False,
                            section="eeoc", extra={"widget": "text", "dom_optional": True}))
    return out


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", htmllib.unescape(s or ""))).strip()


@register("lever")
def prefetch(app: Application) -> list[Question]:
    company = app.ats_params.get("company")
    posting = app.ats_params.get("posting_id") or app.ats_id
    if not company or not posting:
        raise ValueError("lever company or posting id unknown")
    eu = bool(app.ats_params.get("eu"))
    qs = parse_apply_html(fetch_html(company, str(posting), eu=eu))
    for q in qs:
        q.extra.setdefault("hosted_url", apply_url(company, str(posting), eu=eu))
    return qs
