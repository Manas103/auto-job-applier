"""Greenhouse job board API."""

from __future__ import annotations

import html
import json
import re

from ..models import Listing
from ..util import HttpError, http_get
from .base import html_to_text, normalize_category

_http_get = http_get  # monkeypatch target

BASE = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"


def _content_text(content: str | None) -> str:
    if not content:
        return ""
    return html_to_text(html.unescape(content))


def _to_listing(board: str, job: dict) -> Listing:
    title = (job.get("title") or "").strip()
    loc = (job.get("location") or {}).get("name") if isinstance(job.get("location"), dict) else job.get("location")
    jid = str(job.get("id") or "")
    return Listing(
        source=f"watch:greenhouse:{board}",
        source_id=jid,
        company=board,
        title=title,
        url=job.get("absolute_url") or f"https://job-boards.greenhouse.io/{board}/jobs/{jid}",
        locations=[loc.strip()] if loc and str(loc).strip() else [],
        category=normalize_category("", title),
        sponsorship="",
        terms=[],
        degrees=[],
        pay="",
        date_posted=(job.get("first_published") or job.get("updated_at") or "")[:10],
        active=True,
        jd_text=_content_text(job.get("content")) or None,
        ats="greenhouse",
        ats_id=jid,
    )


def list_jobs(board: str, *, timeout: int = 60) -> list[Listing]:
    raw = _http_get(BASE.format(board=board) + "?content=true", timeout=timeout,
                    headers={"Accept": "application/json"})
    data = json.loads(raw.decode("utf-8"))
    return [_to_listing(board, j) for j in data.get("jobs", []) if isinstance(j, dict)]


def get_job_text(board: str | None, job_id: str, *, timeout: int = 30) -> str | None:
    if not board:
        return None
    try:
        raw = _http_get(BASE.format(board=board) + f"/{job_id}", timeout=timeout, headers={"Accept": "application/json"})
    except HttpError:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    text = _content_text(data.get("content"))
    return text or None


def _board_candidates(company: str) -> list[str]:
    base = re.sub(r"[^a-z0-9 ]+", " ", (company or "").lower())
    toks = [t for t in base.split() if t not in ("inc", "llc", "ltd", "corp", "corporation", "co", "the")]
    cands = []
    if toks:
        cands.append("".join(toks))
        cands.append("-".join(toks))
        cands.append(toks[0])
    raw_toks = base.split()
    if raw_toks:
        cands.append("".join(raw_toks))
        cands.append("-".join(raw_toks))
    out: list[str] = []
    for c in cands:
        if c and c not in out:
            out.append(c)
    return out


def guess_board(company: str, job_id: str, *, timeout: int = 15) -> str | None:
    """Try plausible board tokens for a gh_jid URL on a company domain."""
    for cand in _board_candidates(company):
        try:
            raw = _http_get(BASE.format(board=cand) + f"/{job_id}", timeout=timeout, headers={"Accept": "application/json"})
        except HttpError:
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and str(data.get("id", "")) == str(job_id):
            return cand
    return None
