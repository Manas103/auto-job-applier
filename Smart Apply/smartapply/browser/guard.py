"""The submit guard: three layers, always all three.

1. Tool layer (actions.py): a click is refused when the real element looks like a submit button.
2. Network layer (this file): per-page routes abort the ATS's submit request while armed.
3. DOM layer (this file): an injected script blocks form submission and covers submit buttons with a banner,
   but only while its heartbeat is fresh (TTL), so a crashed daemon can never leave a page permanently blocked.

Handoff order: disarm the DOM script, remove routes, verify, disconnect. After that the page is plain Chrome.
"""

from __future__ import annotations

import re
import time
from typing import Callable

from playwright.sync_api import Page, Route

from ..log import get_logger

log = get_logger("guard")

# Button text that means "send the application". Matched against the whole trimmed label.
SUBMIT_TEXT = re.compile(
    r"^\s*(submit( application| my application| now)?|send( my)? application|apply( now| for this job)?|"
    r"finish( application)?|complete( application| my application)?|sign and submit|submit and continue|"
    r"submit & continue|confirm and submit|i agree and submit|apply and save|apply without saving)\s*$", re.I)

# What a click may hit on a review/summary page. Allowlist, not denylist.
REVIEW_ALLOW = re.compile(r"^\s*(back|previous|save for later|save and close|save & close|edit|change|cancel|close)\s*$", re.I)

# A type=submit control inside a <form> is allowed only when it is plainly navigation or an in-form action.
SAFE_SUBMIT_TYPE = re.compile(
    r"^\s*(next|continue|save and continue|save & continue|back|previous|add|add another|add more|search|"
    r"ok|yes|no|upload|attach|choose file|remove|delete|clear|verify|sign in|log in|login|create account|"
    r"send code|resend( code)?|continue with email|accept( all)?( cookies)?|decline( cookies)?|done|select files?|browse|"
    r"add (work experience|education|language|skill|website)|save|edit|update)\s*$", re.I)

# Per-ATS submit requests (method, URL regex). Intermediate saves must NOT match (Workday saves on every Next).
SUBMIT_URL_PATTERNS: dict[str, list[tuple[str, re.Pattern]]] = {
    "workday": [("POST", re.compile(r"/wday/cxs/[^/]+/jobapplication/[^/]+/finalize"))],
    "ashby": [("POST", re.compile(r"jobs\.ashbyhq\.com/api/non-user-graphql\?op=ApiSubmit"))],
    "greenhouse": [("POST", re.compile(r"job-boards(\.eu)?\.greenhouse\.io/[^/]+/jobs/\d+(/|\?|$)")),
                   ("POST", re.compile(r"boards-api(\.eu)?\.greenhouse\.io/v1/boards/[^/]+/jobs/\d+"))],
    "lever": [("POST", re.compile(r"jobs(\.eu)?\.lever\.co/[^/]+/[0-9a-fA-F-]{36}/apply"))],
    "jobvite": [("POST", re.compile(r"/submitApplication"))],
    "rippling": [("POST", re.compile(r"ats\.rippling\.com/api/v\d+/board/[^/]+/jobs/[^/]+/apply"))],
    "gem": [("POST", re.compile(r"/api/ats/job_posts/[^/]+/apply"))],
    "eightfold": [("POST", re.compile(r"/api/application/v2/submit"))],
    "phenom": [("POST", re.compile(r"/applySubmit"))],
    "smartrecruiters": [("POST", re.compile(r"/postings/[^/]+/candidates")),
                        ("POST", re.compile(r"/oneclick-ui/api/.*/apply"))],
    "oracle_hcm": [("POST", re.compile(r"/recruitingCEJobApplications(\?|$)"))],
    # test fixture server
    "_fixture": [("POST", re.compile(r"/wday/cxs/[^/]+/jobapplication/[^/]+/finalize")),
                 ("POST", re.compile(r"/apply(\?|$)")),
                 ("POST", re.compile(r"op=ApiSubmit"))],
}

GUARD_JS = r"""
(() => {
  if (window.__sa) { window.__sa.beat(); return; }
  const TTL = __TTL__;
  const SUBMIT_RE = __SUBMIT_RE__;
  const sa = { armedAt: Date.now(), ttl: TTL, blocked: 0, active: true };
  const armed = () => sa.active && (Date.now() - sa.armedAt) < sa.ttl;
  const label = (b) => ((b.innerText || b.value || b.getAttribute('aria-label') || '') + '').trim();
  const looksSubmit = (b) => SUBMIT_RE.test(label(b));
  // Workday puts a transparent overlay (data-automation-id=click_filter, sometimes role=button aria-label=Submit)
  // above the real button: judge the button it covers, exactly as the tool layer does. A covered "Submit" stays blocked.
  const judged = (b) => {
    if (b && b.getAttribute && b.getAttribute('data-automation-id') === 'click_filter' && b.parentElement) {
      const all = [...b.parentElement.querySelectorAll('button,[role=button],input[type=submit]')].filter(x => x !== b && !b.contains(x) && !x.contains(b));
      const cov = all.find(x => x.getClientRects().length > 0) || all[0];
      if (cov) return cov;
    }
    return b;
  };
  // A form submit is blocked when the button that sent it looks like "send the application", or when it was
  // implicit (Enter in a field) and the form owns such a button. Sign-in, create-account and "Save and Continue"
  // forms (Workday) submit through buttons that do not match, so they pass; the network layer still covers them.
  const onSubmit = (e) => {
    if (!armed()) return;
    const f = e.target;
    const s = e.submitter || null;
    const owns = (f && f.querySelectorAll) ? [...f.querySelectorAll('button,input[type=submit]')].some(looksSubmit) : false;
    if ((s && looksSubmit(s)) || (!s && owns)) { e.preventDefault(); e.stopImmediatePropagation(); sa.blocked++; }
  };
  const onClick = (e) => {
    if (!armed()) return;
    const t = e.target;
    const b = t && t.closest ? t.closest('button,[role=button],input[type=submit],a') : null;
    if (b && looksSubmit(judged(b))) { e.preventDefault(); e.stopImmediatePropagation(); sa.blocked++; }
  };
  const onKey = (e) => {
    if (!armed()) return;
    if (e.key === 'Enter' && e.target && e.target.tagName === 'INPUT' && e.target.form) {
      e.preventDefault(); e.stopImmediatePropagation(); sa.blocked++;
    }
  };
  document.addEventListener('submit', onSubmit, true);
  document.addEventListener('click', onClick, true);
  document.addEventListener('keydown', onKey, true);
  const banner = document.createElement('div');
  banner.id = '__sa_banner';
  banner.textContent = 'Smart Apply is filling this form. Submit is disabled until handoff.';
  banner.setAttribute('style', 'position:fixed;left:0;right:0;bottom:0;z-index:2147483647;background:#b45309;color:#fff;font:13px system-ui,sans-serif;padding:6px 12px;text-align:center;pointer-events:none;');
  const mount = () => { try { if (document.body && !document.getElementById('__sa_banner')) document.body.appendChild(banner); } catch (_) {} };
  mount();
  const mo = new MutationObserver(mount);
  try { mo.observe(document.documentElement, { childList: true, subtree: true }); } catch (_) {}
  sa.beat = () => { sa.armedAt = Date.now(); };
  sa.disarm = () => {
    sa.active = false;
    document.removeEventListener('submit', onSubmit, true);
    document.removeEventListener('click', onClick, true);
    document.removeEventListener('keydown', onKey, true);
    try { mo.disconnect(); } catch (_) {}
    try { banner.remove(); } catch (_) {}
    clearInterval(tick);
    try { delete window.__sa; } catch (_) { window.__sa = undefined; }
  };
  const tick = setInterval(() => { if (!armed()) sa.disarm(); }, 2000);
  window.__sa = sa;
})();
"""


def _js_regex(rx: re.Pattern) -> str:
    return "/" + rx.pattern.replace("/", r"\/") + "/i"


def guard_script(ttl_ms: int) -> str:
    return GUARD_JS.replace("__TTL__", str(int(ttl_ms))).replace("__SUBMIT_RE__", _js_regex(SUBMIT_TEXT))


class Guard:
    """Owns layers 2 and 3 for one page. The owner must call `beat()` at least every ttl/3 while armed."""

    def __init__(self, page: Page, ats: str | None, *, ttl_ms: int = 60000,
                 on_abort: Callable[[str], None] | None = None):
        self.page = page
        self.ats = ats or ""
        self.ttl_ms = int(ttl_ms)
        self.aborted: list[str] = []
        self.on_abort = on_abort
        self.armed = False
        self._last_beat = 0.0
        self._script = guard_script(self.ttl_ms)
        self._patterns = SUBMIT_URL_PATTERNS.get(self.ats, [])

    # ---------------------------------------------------------------- arm

    def arm(self) -> None:
        self.page.add_init_script(script=self._script)
        self._inject_now()
        for method, rx in self._patterns:
            self.page.route(rx, self._make_handler(method))
        self.armed = True
        self._last_beat = time.time()
        log.info("guard armed for %s (%d network patterns)", self.ats or "unknown ATS", len(self._patterns))

    def _inject_now(self) -> None:
        try:
            self.page.evaluate(self._script)
        except Exception as e:  # noqa: BLE001
            log.debug("guard inject deferred: %s", e)

    def _make_handler(self, method: str):
        def handler(route: Route) -> None:
            req = route.request
            if req.method.upper() == method.upper():
                self.aborted.append(f"{req.method} {req.url}")
                log.warning("guard aborted submit request: %s %s", req.method, req.url)
                if self.on_abort:
                    self.on_abort(req.url)
                route.abort("failed")
            else:
                route.continue_()
        return handler

    # ---------------------------------------------------------------- keep alive

    def beat(self, *, force: bool = False) -> None:
        """Refresh the DOM layer's TTL. Cheap; call it between actions."""
        if not self.armed:
            return
        if not force and time.time() - self._last_beat < self.ttl_ms / 3000.0:
            return
        self._last_beat = time.time()
        try:
            self.page.evaluate("() => { if (window.__sa) window.__sa.beat(); }")
        except Exception:  # noqa: BLE001
            # navigation in flight; the init script re-arms on the next document
            pass
        self._inject_now()

    def suspend(self) -> None:
        """Pause the DOM layer for one verified action (`Actions.open_apply`). The network layer stays armed."""
        try:
            self.page.evaluate("() => { if (window.__sa) window.__sa.active = false; }")
        except Exception:  # noqa: BLE001
            pass

    def resume(self) -> None:
        try:
            self.page.evaluate("() => { if (window.__sa) { window.__sa.active = true; window.__sa.beat(); } }")
        except Exception:  # noqa: BLE001
            pass
        self._last_beat = time.time()
        self._inject_now()

    def dom_blocked_count(self) -> int:
        try:
            return int(self.page.evaluate("() => (window.__sa && window.__sa.blocked) || 0"))
        except Exception:  # noqa: BLE001
            return 0

    def dom_armed(self) -> bool:
        try:
            return bool(self.page.evaluate("() => !!window.__sa"))
        except Exception:  # noqa: BLE001
            return False

    # ---------------------------------------------------------------- disarm (handoff)

    def disarm(self) -> bool:
        """Remove every layer from the page and confirm. Returns True when the page is clean."""
        try:
            self.page.evaluate("() => { if (window.__sa) window.__sa.disarm(); }")
        except Exception as e:  # noqa: BLE001
            log.debug("dom disarm: %s", e)
        try:
            self.page.unroute_all(behavior="wait")
        except Exception as e:  # noqa: BLE001
            log.debug("unroute_all: %s", e)
        self.armed = False
        clean = not self.dom_armed()
        if not clean:
            log.error("guard did not disarm cleanly on %s", self.page.url)
        else:
            log.info("guard disarmed (%d requests aborted while armed)", len(self.aborted))
        return clean


def looks_like_submit(text: str) -> bool:
    return bool(SUBMIT_TEXT.match(text or ""))
