"""Ashby public job board API."""

from __future__ import annotations

import json

from ..models import Listing
from ..util import HttpError, http_get
from .base import html_to_text, normalize_category

_http_get = http_get

BASE = "https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"


def _to_listing(org: str, j: dict) -> Listing:
    title = (j.get("title") or "").strip()
    pid = str(j.get("id") or "").lower()
    loc = (j.get("location") or "").strip()
    comp = j.get("compensation") or {}
    pay = ""
    if isinstance(comp, dict):
        pay = (comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary") or "").strip()
    return Listing(
        source=f"watch:ashby:{org}",
        source_id=pid,
        company=org,
        title=title,
        url=j.get("jobUrl") or f"https://jobs.ashbyhq.com/{org}/{pid}",
        locations=[loc] if loc else [],
        category=normalize_category("", title),
        sponsorship="",
        terms=[],
        degrees=[],
        pay=pay,
        date_posted=(j.get("publishedAt") or "")[:10],
        active=True,
        jd_text=(j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml") or "")).strip() or None,
        ats="ashby",
        ats_id=pid,
    )


def list_jobs(org: str, *, timeout: int = 60) -> list[Listing]:
    raw = _http_get(BASE.format(org=org), timeout=timeout, headers={"Accept": "application/json"})
    data = json.loads(raw.decode("utf-8"))
    return [_to_listing(org, j) for j in data.get("jobs", []) if isinstance(j, dict) and j.get("isListed", True)]


def get_job_text(org: str, posting_id: str, *, timeout: int = 30) -> str | None:
    try:
        for it in list_jobs(org, timeout=timeout):
            if it.ats_id == posting_id.lower():
                return it.jd_text
    except (HttpError, json.JSONDecodeError):
        return None
    return None
