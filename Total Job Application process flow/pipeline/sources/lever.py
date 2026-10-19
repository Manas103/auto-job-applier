"""Lever postings API."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..models import Listing
from ..util import HttpError, http_get
from .base import html_to_text, normalize_category

_http_get = http_get

BASE = "https://api.lever.co/v0/postings/{company}?mode=json"


def _posting_text(p: dict) -> str:
    parts = [p.get("descriptionPlain") or html_to_text(p.get("description") or "")]
    for lst in p.get("lists") or []:
        if not isinstance(lst, dict):
            continue
        head = (lst.get("text") or "").strip()
        body = html_to_text(lst.get("content") or "")
        if head:
            parts.append(head)
        if body:
            parts.append(body)
    tail = p.get("additionalPlain") or html_to_text(p.get("additional") or "")
    if tail:
        parts.append(tail)
    return "\n\n".join(x for x in parts if x).strip()


def _to_listing(company: str, p: dict) -> Listing:
    title = (p.get("text") or "").strip()
    cats = p.get("categories") or {}
    loc = cats.get("location") if isinstance(cats, dict) else None
    pid = str(p.get("id") or "").lower()
    created = p.get("createdAt")
    date_posted = ""
    if isinstance(created, (int, float)):
        date_posted = datetime.fromtimestamp(created / 1000, tz=timezone.utc).date().isoformat()
    return Listing(
        source=f"watch:lever:{company}",
        source_id=pid,
        company=company,
        title=title,
        url=p.get("hostedUrl") or f"https://jobs.lever.co/{company}/{pid}",
        locations=[loc.strip()] if loc and str(loc).strip() else [],
        category=normalize_category("", title),
        sponsorship="",
        terms=[],
        degrees=[],
        pay="",
        date_posted=date_posted,
        active=True,
        jd_text=_posting_text(p) or None,
        ats="lever",
        ats_id=pid,
    )


def list_postings(company: str, *, timeout: int = 60) -> list[Listing]:
    raw = _http_get(BASE.format(company=company), timeout=timeout, headers={"Accept": "application/json"})
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        return []
    return [_to_listing(company, p) for p in data if isinstance(p, dict)]


def get_posting_text(company: str, posting_id: str, *, timeout: int = 30) -> str | None:
    try:
        raw = _http_get(f"https://api.lever.co/v0/postings/{company}/{posting_id}", timeout=timeout,
                        headers={"Accept": "application/json"})
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict) and data.get("id"):
            return _posting_text(data) or None
    except (HttpError, json.JSONDecodeError):
        pass
    try:
        for p in list_postings(company, timeout=timeout):
            if p.ats_id == posting_id.lower():
                return p.jd_text
    except (HttpError, json.JSONDecodeError):
        return None
    return None
