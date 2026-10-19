"""URL normalization, ATS detection, and canonical job keys."""

from __future__ import annotations

import re
import urllib.parse

TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "source", "src",
    "gh_src", "lever-source", "lever_source", "embed", "mobile", "needsredirect", "in_iframe", "trk",
    "trackingtag", "referrer", "refid", "campaign", "sourcetype", "fbclid", "gclid",
})

_LOCALE_SEG = re.compile(r"^[a-z]{2}-[A-Za-z]{2}$")
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


def normalize_url(url: str) -> str:
    """Canonical form used as the cross-source dedup key when no ATS id is known."""
    url = (url or "").strip()
    p = urllib.parse.urlsplit(url)
    host = p.netloc.lower()
    if "@" in host:
        host = host.split("@", 1)[1]
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(":443") or host.endswith(":80"):
        host = host.rsplit(":", 1)[0]
    segs = [s for s in p.path.split("/") if s]
    # Workday locale segment (/en-US/, /fr-CA/) carries no identity.
    if "myworkdayjobs.com" in host and segs and _LOCALE_SEG.match(segs[0]):
        segs = segs[1:]
    # Lever/Ashby trailing apply/application segments.
    while segs and segs[-1].lower() in ("apply", "application"):
        segs.pop()
    path = "/" + "/".join(segs) if segs else ""
    keep = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS]
    keep.sort()
    query = urllib.parse.urlencode(keep)
    return host + path + ("?" + query if query else "")


def detect_ats(url: str) -> tuple[str | None, dict]:
    """Identify the applicant tracking system behind an apply URL and extract its identifiers."""
    url = (url or "").strip()
    p = urllib.parse.urlsplit(url)
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    segs = [s for s in p.path.split("/") if s]
    q = dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))

    # Greenhouse hosted boards
    if host in ("job-boards.greenhouse.io", "boards.greenhouse.io", "job-boards.eu.greenhouse.io", "boards.eu.greenhouse.io"):
        # /<board>/jobs/<id>
        if len(segs) >= 3 and segs[1] == "jobs" and segs[2].isdigit():
            return "greenhouse", {"board": segs[0], "job_id": segs[2]}
        if "gh_jid" in q and q["gh_jid"].isdigit():
            return "greenhouse", {"board": segs[0] if segs else None, "job_id": q["gh_jid"]}
    # Greenhouse embedded on a company domain
    if "gh_jid" in q and q["gh_jid"].isdigit():
        return "greenhouse", {"board": None, "job_id": q["gh_jid"]}

    # Lever
    if re.fullmatch(r"jobs(\.[a-z]+)?\.lever\.co", host):
        if len(segs) >= 2 and re.fullmatch(_UUID, segs[1]):
            return "lever", {"company": segs[0], "posting_id": segs[1].lower()}

    # Ashby
    if host == "jobs.ashbyhq.com":
        if len(segs) >= 2 and re.fullmatch(_UUID, segs[1]):
            return "ashby", {"org": segs[0], "posting_id": segs[1].lower()}

    # Workday
    m = re.fullmatch(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com", host)
    if m:
        tenant = m.group(1)
        s = list(segs)
        if s and _LOCALE_SEG.match(s[0]):
            s = s[1:]
        if len(s) >= 2 and s[1] == "job":
            site = s[0]
            tail = s[-1]
            rm = re.search(r"_([A-Za-z0-9-]+)$", tail)
            req_id = rm.group(1) if rm else None
            external_path = "/" + "/".join(s[1:])
            return "workday", {"host": host, "tenant": tenant, "site": site, "external_path": external_path,
                               "req_id": req_id}
        if len(s) >= 1:
            return "workday", {"host": host, "tenant": tenant, "site": s[0], "external_path": None, "req_id": None}

    return None, {}


def canonical_key(url: str) -> str:
    kind, params = detect_ats(url)
    if kind == "greenhouse" and params.get("job_id"):
        return f"gh:{params['job_id']}"
    if kind == "lever" and params.get("posting_id"):
        return f"lever:{params['posting_id']}"
    if kind == "ashby" and params.get("posting_id"):
        return f"ashby:{params['posting_id']}"
    if kind == "workday" and params.get("req_id"):
        return f"wd:{params['tenant']}:{params['req_id']}"
    return normalize_url(url)


def ats_identity(url: str) -> tuple[str | None, str | None]:
    """(ats, ats_id) for the jobs table."""
    kind, params = detect_ats(url)
    if kind == "greenhouse" and params.get("job_id"):
        return "greenhouse", params["job_id"]
    if kind == "lever" and params.get("posting_id"):
        return "lever", params["posting_id"]
    if kind == "ashby" and params.get("posting_id"):
        return "ashby", params["posting_id"]
    if kind == "workday" and params.get("req_id"):
        return "workday", f"{params['tenant']}:{params['req_id']}"
    return None, None
