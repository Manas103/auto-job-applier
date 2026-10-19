"""speedyapply README markdown tables."""

from __future__ import annotations

import html
import re
from datetime import date, timedelta

from ..models import Listing
from ..util import today as _today
from .base import attach_ats, normalize_category, strip_tags


def parse_speedyapply_readme(raw: bytes, source: str, today: date | None = None, **_) -> list[Listing]:
    """Tables look like:
      | Company | Position | Location | Salary | Posting | Age |   (FAANG+, Quant)
      | Company | Position | Location | Posting | Age |            (Other)
    Columns are mapped by header so both shapes work."""
    text = raw.decode("utf-8")
    today = today or _today()
    out: list[Listing] = []
    headers: list[str] | None = None
    section = ""
    for line in text.splitlines():
        if line.startswith("#"):
            section = line.lstrip("#").strip()
            headers = None
            continue
        if not line.startswith("|"):
            headers = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if headers is None:
            headers = [strip_tags(c).lower() for c in cells]
            if "posting" not in headers or "position" not in headers:
                headers = None  # not a listings table; skip it entirely
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        if len(cells) != len(headers):
            continue
        row = dict(zip(headers, cells))
        m = re.search(r'href="([^"]+)"', row.get("posting", ""))
        if not m:
            continue
        url = html.unescape(m.group(1))
        age_txt = strip_tags(row.get("age", ""))
        m_age = re.match(r"(\d+)\s*([dhwm])", age_txt)
        date_posted = ""
        if m_age:
            n, unit = int(m_age.group(1)), m_age.group(2)
            days = {"h": 0, "d": n, "w": n * 7, "m": n * 30}[unit]
            date_posted = (today - timedelta(days=days)).isoformat()
        company = strip_tags(row.get("company", ""))
        if company in ("↳", "", "↳"):
            company = out[-1].company if out else ""
        # The section heading is a hint, not an answer. This source used to label
        # every non-quant row "Software" outright, bypassing normalize_category
        # entirely - which is how mechanical and civil roles reached the pipeline
        # already wearing a Software badge. Outside a quant section, let the title
        # decide.
        _title = strip_tags(row.get("position", ""))
        category = normalize_category(
            "Quant" if "quant" in section.lower() else "", _title)
        lst = Listing(
            source=source,
            source_id="",
            company=company,
            title=strip_tags(row.get("position", "")),
            url=url,
            locations=[strip_tags(row.get("location", ""))] if strip_tags(row.get("location", "")) else [],
            category=category,
            sponsorship="",
            terms=[],
            degrees=[],
            pay=strip_tags(row.get("salary", "")),
            date_posted=date_posted,
            active=True,
        )
        out.append(attach_ats(lst))
    return out
