"""Simplify-style listings.json (SimplifyJobs and vanshb03 share the schema)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..models import Listing
from .base import attach_ats, normalize_category


def epoch_to_date(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def parse_simplify_listings(raw: bytes, source: str, include_inactive: bool = False, **_) -> list[Listing]:
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{source}: expected a JSON array")
    out: list[Listing] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if not include_inactive and not (item.get("active") and item.get("is_visible", True)):
            continue
        url = (item.get("url") or "").strip()
        if not url:
            continue
        title = (item.get("title") or "").strip()
        terms = item.get("terms")
        if not terms:
            terms = [item["season"]] if item.get("season") else []
        lst = Listing(
            source=source,
            source_id=str(item.get("id") or ""),
            company=(item.get("company_name") or item.get("company") or "").strip(),
            title=title,
            url=url,
            locations=[str(l).strip() for l in (item.get("locations") or []) if l and str(l).strip()],
            category=normalize_category(item.get("category") or "", title),
            sponsorship=str(item.get("sponsorship") or ""),
            terms=[str(t) for t in terms if t],
            degrees=[str(d) for d in (item.get("degrees") or []) if d],
            pay="",
            date_posted=epoch_to_date(item.get("date_posted")),
            active=bool(item.get("active", True)),
        )
        out.append(attach_ats(lst))
    return out
