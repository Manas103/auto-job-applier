"""Cross-source duplicate detection: exact key, ATS identity, then fuzzy company+title+location."""

from __future__ import annotations

import difflib
import re
import sqlite3

from . import db
from .models import Job, Listing

_COMPANY_STOP = {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "technologies", "technology",
                 "the", "plc", "limited", "group", "holdings"}
_TITLE_DROP = re.compile(r"\b(summer|fall|spring|winter|autumn)\b|\b20\d\d\b|\(|\)|\[|\]", re.I)
_NONWORD = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")

FUZZY_THRESHOLD = 0.92


def norm_company(s: str) -> str:
    s = _NONWORD.sub(" ", (s or "").lower())
    toks = [t for t in s.split() if t not in _COMPANY_STOP]
    return " ".join(toks)


def norm_title(s: str) -> str:
    s = _TITLE_DROP.sub(" ", (s or "").lower())
    s = _NONWORD.sub(" ", s)
    s = re.sub(r"\binternships?\b", "intern", s)
    s = re.sub(r"\bco\s*-?\s*op\b", "coop", s)
    return _SPACES.sub(" ", s).strip()


def norm_location(loc: str | list[str] | None) -> str:
    if isinstance(loc, list):
        loc = loc[0] if loc else ""
    s = (loc or "").lower()
    s = re.sub(r"\b(usa|united states|us)\b", "", s)
    s = _NONWORD.sub(" ", s)
    return _SPACES.sub(" ", s).strip()


def fuzzy_key(company: str, title: str, locations: list[str] | str | None) -> str:
    return f"{norm_company(company)}|{norm_title(title)}|{norm_location(locations)}"


def titles_match(a: str, b: str) -> bool:
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= FUZZY_THRESHOLD


def locations_compatible(a: list[str] | None, b: list[str] | None) -> bool:
    la, lb = norm_location(a), norm_location(b)
    if not la or not lb:
        return True
    return la == lb


def find_duplicate(conn: sqlite3.Connection, listing: Listing, key: str) -> Job | None:
    job = db.job_by_key(conn, key)
    if job:
        return job
    if listing.ats and listing.ats_id:
        job = db.job_by_ats(conn, listing.ats, listing.ats_id)
        if job:
            return job
    fk = fuzzy_key(listing.company, listing.title, listing.locations)
    row = conn.execute("SELECT * FROM jobs WHERE fuzzy_key=?", (fk,)).fetchone()
    if row:
        return Job.from_row(row)
    comp = norm_company(listing.company)
    if not comp:
        return None
    first_tok = comp.split()[0]
    rows = conn.execute("SELECT * FROM jobs WHERE LOWER(company) LIKE ?", (f"%{first_tok}%",)).fetchall()
    for r in rows:
        j = Job.from_row(r)
        if norm_company(j.company) != comp:
            continue
        if titles_match(j.title, listing.title) and locations_compatible(j.locations, listing.locations):
            return j
    return None
