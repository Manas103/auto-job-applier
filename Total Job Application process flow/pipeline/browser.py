"""Playwright fallback for JavaScript-rendered job pages. Imported lazily; absent package degrades to None.

The browser is pooled. This used to open a `sync_playwright()` context and launch a
complete Chromium per job-description fetch: several hundred MB, a cold start, and a
few hundred times a week. Against Alex's own browser (34 processes) that made the
machine feel like the pipeline was fighting Chrome, which is why he switched the
pipeline off on Aug 30. It never was a conflict -- this launches Playwright's own
bundled Chromium with a throwaway profile and cannot touch his profile, session or
cookies -- it was contention. One reused browser, one page at a time, and an idle
teardown so nothing is held while the pipeline sleeps between collector ticks.
"""

from __future__ import annotations

import atexit
import logging
import threading
import time

log = logging.getLogger("pipeline.browser")
_warned = False

# One browser, one page at a time. JD fetches are already serialized by the worker,
# and a second concurrent Chromium is what made this noticeable in the first place.
_LOCK = threading.Lock()
_IDLE_TEARDOWN_S = 180.0

_pw = None
_browser = None
_last_used = 0.0

_JD_SELECTORS = [
    "[data-automation-id='jobPostingDescription']",  # Workday
    "#job-description", ".job-description", "[class*='job-description']", "[class*='jobDescription']",
    "[class*='posting-description']", "[class*='description']", "article", "main",
]


def _shutdown() -> None:
    """Close the pooled browser. Safe to call twice; never raises."""
    global _pw, _browser, _last_used
    try:
        if _browser is not None:
            _browser.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        if _pw is not None:
            _pw.stop()
    except Exception:  # noqa: BLE001
        pass
    _browser = None
    _pw = None
    _last_used = 0.0


atexit.register(_shutdown)


def shutdown() -> None:
    """Public teardown, for a worker that wants to release memory explicitly."""
    with _LOCK:
        _shutdown()


def _get_browser():
    """The pooled browser, started on first use and replaced if it has died."""
    global _pw, _browser, _last_used

    # Drop a browser that has been idle long enough to be worth its memory.
    if _browser is not None and _last_used and (time.monotonic() - _last_used) > _IDLE_TEARDOWN_S:
        _shutdown()

    if _browser is not None:
        if getattr(_browser, "is_connected", lambda: True)():
            return _browser
        _shutdown()  # crashed or was killed; start a fresh one

    from playwright.sync_api import sync_playwright  # type: ignore

    _pw = sync_playwright().start()
    # Headless JD scraping never plays or records audio. Without these flags Chromium initializes the
    # Windows audio endpoint on every (cold) launch, which briefly interrupts other apps' audio -- the
    # video keeps playing but sound drops for a moment. The idle teardown above makes those cold launches
    # frequent during active processing, so disable audio entirely here (Alex, 2026-09-09).
    _browser = _pw.chromium.launch(headless=True, args=[
        "--mute-audio",
        "--disable-audio-output",
        "--disable-audio-input",
        "--disable-features=AudioServiceOutOfProcess,AudioServiceSandbox",
    ])
    return _browser


def render_text(url: str, timeout_s: int = 45) -> str | None:
    global _warned, _last_used
    try:
        import playwright.sync_api  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001
        if not _warned:
            log.warning("playwright is not installed; JS-rendered pages cannot be fetched (pip install playwright; playwright install chromium)")
            _warned = True
        return None

    with _LOCK:
        try:
            browser = _get_browser()
            # A fresh context per fetch keeps cookies and storage from leaking between
            # postings, which the old launch-per-fetch got for free.
            context = browser.new_context(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"))
            try:
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(15000, timeout_s * 1000))
                except Exception:  # noqa: BLE001
                    pass
                best = ""
                for sel in _JD_SELECTORS:
                    try:
                        el = page.query_selector(sel)
                        if el:
                            t = (el.inner_text() or "").strip()
                            if len(t) > len(best):
                                best = t
                            if len(best) > 800:
                                break
                    except Exception:  # noqa: BLE001
                        continue
                # Embedded ATS postings (Greenhouse, Lever) live in iframes; read those too.
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        for sel in _JD_SELECTORS + ["body"]:
                            el = frame.query_selector(sel)
                            if el:
                                t = (el.inner_text() or "").strip()
                                if len(t) > len(best):
                                    best = t
                                break
                    except Exception:  # noqa: BLE001
                        continue
                if len(best) < 400:
                    try:
                        body = (page.inner_text("body") or "").strip()
                        if len(body) > len(best):
                            best = body
                    except Exception:  # noqa: BLE001
                        pass
                return best or None
            finally:
                # Close the context, not the browser: the browser is the pool.
                try:
                    context.close()
                except Exception:  # noqa: BLE001
                    pass
                _last_used = time.monotonic()
        except Exception as e:  # noqa: BLE001
            log.warning("browser fetch failed for %s: %s", url, e)
            _shutdown()  # a failure may have left the browser unusable
            return None
