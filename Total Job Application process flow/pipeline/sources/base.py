"""Shared source helpers: category normalization, HTML to text, ATS identity attachment."""

from __future__ import annotations

import html
import re

from ..models import Listing
from ..urlnorm import ats_identity

CATEGORY_ALIASES = {
    "software engineering": "Software",
    "software": "Software",
    "data science, ai & machine learning": "AI/ML/Data",
    "ai/ml/data": "AI/ML/Data",
    "hardware engineering": "Hardware",
    "hardware": "Hardware",
    "product management": "Product",
    "product": "Product",
    "quant": "Quant",
    "quantitative finance": "Quant",
}

TITLE_CATEGORY_RULES = [
    (r"quant|trading|trader", "Quant"),
    (r"product manag|\bpm\b|product intern", "Product"),
    # Non-software engineering disciplines must be matched BEFORE the Software rule.
    # That rule's bare `engineer` alternative matches "Mechanical Engineering Intern",
    # and with no discipline rule anywhere, every non-software discipline landed in
    # Software and sneaked past the category gate in screen.py. Ordering is the fix:
    # `engineer` stays in the Software rule so an unqualified "Engineering Intern"
    # still reaches Software rather than being screened out.
    (r"\bmechanical\b|civil engineer|\bchemical\b|aerospace|astronautic|structural engineer"
     r"|industrial engineer|manufacturing engineer|process engineer|quality engineer"
     r"|environmental engineer|petroleum|\bnuclear\b|biomedical|materials engineer"
     r"|\bfacilities\b", "Engineering (other)"),
    # Business functions, ahead of AI/ML so "Marketing Analytics" is not read as data work.
    (r"\bmarketing\b|human resources|\brecruit(?:ing|er)\b|\blegal\b|paralegal"
     r"|supply chain|logistic|workforce management|\baccounting\b", "Business"),
    (r"machine learning|\bml\b|\bai\b|data scien|data analy|analytics|research scientist", "AI/ML/Data"),
    (r"hardware|electrical|fpga|asic|\brtl\b|embedded|firmware|silicon|chip", "Hardware"),
    (r"software|engineer|developer|\bswe\b|\bsde\b|programm|full.?stack|front.?end|back.?end|devops|\bsre\b|security|platform|infrastructure|ios|android|web", "Software"),
]


def normalize_category(category: str, title: str) -> str:
    c = (category or "").strip()
    if c:
        return CATEGORY_ALIASES.get(c.lower(), c)
    t = (title or "").lower()
    for pattern, cat in TITLE_CATEGORY_RULES:
        if re.search(pattern, t):
            return cat
    return "Other"


def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


_BLOCK_TAGS = r"p|div|br|li|ul|ol|h[1-6]|tr|td|th|section|article|header|footer|table|blockquote|pre|dd|dt|dl"


def html_to_text(html_str: str) -> str:
    """Reasonable plain text from HTML without third-party deps."""
    s = html_str or ""
    s = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|head)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)
    s = re.sub(rf"(?i)</?(?:{_BLOCK_TAGS})\b[^>]*>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = s.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines()]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if ln:
            out.append(ln)
            blank = 0
        else:
            blank += 1
            if blank == 1:
                out.append("")
    return "\n".join(out).strip()


def attach_ats(listing: Listing) -> Listing:
    if listing.ats is None:
        ats, ats_id = ats_identity(listing.url)
        listing.ats, listing.ats_id = ats, ats_id
    return listing
