"""Job description fetching: ATS JSON first, generic HTML second, headless browser last."""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from . import browser as browser_mod
from . import db
from .config import Config
from .sources import ashby, greenhouse, lever, workday
from .sources.base import html_to_text
from .urlnorm import detect_ats
from .util import HttpError, http_get, now_iso

log = logging.getLogger("pipeline.jd")
THIN_JD_CHARS = 2500  # an HTML result shorter than this is retried in the browser (embedded postings)
_http_get = http_get

_JD_MARKERS = re.compile(r"responsibilit|qualification|requirement|about the role|about this role|what you.ll do|what you will do|who you are|minimum qualifications|basic qualifications|preferred", re.I)


@dataclass
class JDResult:
    text: str | None
    source: str  # greenhouse_api | lever_api | workday_api | ashby_api | eightfold_api | html | browser | none
    url: str
    error: str | None = None
    attempts: list[dict[str, Any]] | None = None

    @property
    def ok(self) -> bool:
        return bool(self.text)


_BOARD_IN_HTML = re.compile(
    r"boards\.greenhouse\.io/embed/job_board(?:/js)?\?for=([a-z0-9_-]+)"
    r"|greenhouse\.io/embed/job_app\?for=([a-z0-9_-]+)"
    r"|job-boards(?:\.eu)?\.greenhouse\.io/([a-z0-9_-]+)/jobs/\d+"
    r"|boards(?:\.eu)?\.greenhouse\.io/([a-z0-9_-]+)(?:/jobs/\d+|[\"'/?])"
    r"|grnh\.se/([a-z0-9_-]+)"
    r"|Grnhse\.Iframe\.load\([^)]*?['\"]([a-z0-9_-]+)['\"]"
    r"|data-(?:board|gh-board|greenhouse-board)=['\"]([a-z0-9_-]+)['\"]",
    re.I)


def looks_like_jd(text: str | None, min_chars: int = 400) -> bool:
    """A job description has real length and at least two distinct section markers
    (or one marker with substantial length). Page chrome with a stray 'About this role' does not pass."""
    if not text or len(text) < min_chars:
        return False
    markers = {m.group(0).lower()[:12] for m in _JD_MARKERS.finditer(text)}
    if len(markers) >= 2:
        return True
    return len(markers) == 1 and len(text) >= 2500


def board_from_html(html_str: str) -> str | None:
    """Greenhouse board token embedded in a company careers page (embed script, iframe loader, or board links)."""
    for m in _BOARD_IN_HTML.finditer(html_str or ""):
        for g in m.groups():
            if g and g.lower() not in ("embed", "job_board", "js"):
                return g.lower()
    return None


def _raw_html(url: str, cfg: Config) -> str | None:
    try:
        raw = _http_get(url, timeout=int(cfg.jd.http_timeout_s), headers={"Accept": "text/html,application/xhtml+xml"})
    except HttpError as e:
        log.info("html fetch failed for %s: %s", url, e)
        return None
    return raw.decode("utf-8", errors="replace")


_EIGHTFOLD = re.compile(r"^https?://([^/]+)/careers/job/(\d+)", re.I)


def _eightfold_text(url: str, timeout: int) -> str | None:
    """Eightfold career sites (Microsoft, Qualcomm, John Deere, ...) render the posting from JSON; their HTML is a
    page-config blob with no description in it (Microsoft, 2026-10-06: 480K chars of theme settings)."""
    import json as _json
    m = _EIGHTFOLD.match(url)
    if not m:
        return None
    raw = _http_get(f"https://{m.group(1)}/api/apply/v2/jobs/{m.group(2)}", timeout=timeout,
                    headers={"Accept": "application/json"})
    desc = (_json.loads(raw.decode("utf-8", errors="replace")) or {}).get("job_description") or ""
    return html_to_text(desc) or None


def _ats_fetch(job: Any, cfg: Config, html_cache: dict | None = None) -> tuple[str | None, str]:
    kind, params = detect_ats(job.url)
    timeout = int(cfg.jd.http_timeout_s)
    if _EIGHTFOLD.match(job.url or ""):
        return _eightfold_text(job.url, timeout), "eightfold_api"
    if kind == "greenhouse":
        board = params.get("board")
        if not board:
            # Company-domain embed: read the board token out of the page before guessing.
            html_str = _raw_html(job.url, cfg)
            if html_cache is not None:
                html_cache["html"] = html_str
            board = board_from_html(html_str or "")
        if not board:
            board = greenhouse.guess_board(job.company, params["job_id"], timeout=timeout)
        if board and html_cache is not None:
            html_cache["board"] = board
        return greenhouse.get_job_text(board, params["job_id"], timeout=timeout), "greenhouse_api"
    if kind == "lever":
        return lever.get_posting_text(params["company"], params["posting_id"], timeout=timeout), "lever_api"
    if kind == "ashby":
        return ashby.get_job_text(params["org"], params["posting_id"], timeout=timeout), "ashby_api"
    if kind == "workday" and params.get("external_path"):
        return workday.get_job_text(params["host"], params["tenant"], params["site"], params["external_path"],
                                    timeout=timeout), "workday_api"
    return None, "none"


def _html_fetch(url: str, cfg: Config, html_str: str | None = None) -> str | None:
    if html_str is None:
        html_str = _raw_html(url, cfg)
    if html_str is None:
        return None
    text = html_to_text(html_str)
    if looks_like_jd(text, int(cfg.jd.min_chars)):
        return text
    m = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]{600,})"', html_str, re.I)
    if m:
        return html_to_text(m.group(1))
    return None


def fetch_jd(job: Any, cfg: Config, *, allow_browser: bool) -> JDResult:
    """`job` needs .url and .company. Returns the first strategy that yields a plausible JD."""
    attempts: list[dict[str, Any]] = []
    min_chars = int(cfg.jd.min_chars)

    cache: dict = {}
    try:
        text, src = _ats_fetch(job, cfg, cache)
    except Exception as e:  # noqa: BLE001
        text, src = None, "none"
        attempts.append({"strategy": "ats", "ok": False, "error": str(e)})
    else:
        attempts.append({"strategy": src, "ok": bool(text), "board": cache.get("board")})
    if text and len(text.strip()) >= 80:  # ATS JSON is authoritative; only reject near-empty content
        return JDResult(text, src, job.url, attempts=attempts)

    html_text = _html_fetch(job.url, cfg, cache.get("html"))
    attempts.append({"strategy": "html", "ok": bool(html_text), "chars": len(html_text or "")})
    thin = bool(html_text) and len(html_text) < THIN_JD_CHARS
    if html_text and not (thin and allow_browser and bool(cfg.jd.browser_enabled)):
        return JDResult(html_text, "html", job.url, attempts=attempts)

    if allow_browser and bool(cfg.jd.browser_enabled):
        text = browser_mod.render_text(job.url, int(cfg.jd.browser_timeout_s))
        ok = looks_like_jd(text, min_chars) or (text is not None and len(text) >= min_chars * 2)
        attempts.append({"strategy": "browser", "ok": ok, "chars": len(text or "")})
        if ok and (not html_text or len(text or "") > len(html_text)):
            return JDResult(text, "browser", job.url, attempts=attempts)
        if html_text:
            return JDResult(html_text, "html", job.url, attempts=attempts)

    return JDResult(None, "none", job.url, error="no strategy produced a job description", attempts=attempts)


def store_jd(conn: sqlite3.Connection, cfg: Config, job_id: int, result: JDResult) -> None:
    for a in result.attempts or []:
        db.add_event(conn, job_id, "jd_attempt", detail=a)
    if result.ok:
        cfg.jd_dir.mkdir(parents=True, exist_ok=True)
        (cfg.jd_dir / f"{job_id}.txt").write_text(result.text or "", encoding="utf-8")
        db.update_job(conn, job_id, jd_text=result.text, jd_source=result.source, jd_fetched_at=now_iso())
        db.add_event(conn, job_id, "jd_fetched", detail={"source": result.source, "chars": len(result.text or "")})
