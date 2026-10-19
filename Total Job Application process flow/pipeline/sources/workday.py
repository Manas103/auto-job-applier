"""Workday candidate experience (cxs) JSON endpoints."""

from __future__ import annotations

import json
import re

from ..models import Listing
from ..util import HttpError, http_get, http_post_json
from .base import html_to_text, normalize_category

_http_get = http_get
_http_post_json = http_post_json


def _headers(host: str) -> dict:
    return {"Accept": "application/json", "Origin": f"https://{host}", "Referer": f"https://{host}/",
            "Content-Type": "application/json"}


def _req_id(posting: dict) -> str | None:
    for b in posting.get("bulletFields") or []:
        if isinstance(b, str) and re.fullmatch(r"[A-Za-z]*[-_]?\d[\w-]*", b):
            return b
    ext = posting.get("externalPath") or ""
    m = re.search(r"_([A-Za-z0-9-]+)$", ext)
    return m.group(1) if m else None


def _to_listing(host: str, tenant: str, site: str, p: dict) -> Listing:
    title = (p.get("title") or "").strip()
    ext = p.get("externalPath") or ""
    req = _req_id(p) or ""
    loc = (p.get("locationsText") or "").strip()
    return Listing(
        source=f"watch:workday:{tenant}",
        source_id=req,
        company=tenant,
        title=title,
        url=f"https://{host}/{site}{ext}",
        locations=[loc] if loc else [],
        category=normalize_category("", title),
        sponsorship="",
        terms=[],
        degrees=[],
        pay="",
        date_posted="",
        active=True,
        jd_text=None,
        ats="workday",
        ats_id=f"{tenant}:{req}" if req else None,
    )


def list_jobs(host: str, tenant: str, site: str, *, search_text: str = "intern", limit: int = 20,
              max_pages: int = 5, timeout: int = 60) -> list[Listing]:
    out: list[Listing] = []
    url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    for page in range(max_pages):
        body = {"appliedFacets": {}, "limit": limit, "offset": page * limit, "searchText": search_text}
        raw = _http_post_json(url, body, timeout=timeout, headers=_headers(host))
        data = json.loads(raw.decode("utf-8"))
        postings = data.get("jobPostings") or []
        for p in postings:
            if isinstance(p, dict):
                out.append(_to_listing(host, tenant, site, p))
        total = int(data.get("total") or 0)
        if not postings or (page + 1) * limit >= total:
            break
    return out


def get_job_text(host: str, tenant: str, site: str, external_path: str, *, timeout: int = 30) -> str | None:
    if not external_path:
        return None
    url = f"https://{host}/wday/cxs/{tenant}/{site}{external_path}"
    try:
        raw = _http_get(url, timeout=timeout, headers=_headers(host))
        data = json.loads(raw.decode("utf-8"))
    except (HttpError, json.JSONDecodeError):
        return None
    info = data.get("jobPostingInfo") or {}
    text = html_to_text(info.get("jobDescription") or "")
    return text or None
