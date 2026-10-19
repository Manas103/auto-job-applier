"""Typed page actions with the tool-layer submit refusal (guard layer 1).

Every click resolves the real element first and refuses anything that could send the application. Adapters and
the MCP server both go through this class; nothing in the project clicks a raw locator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import Locator, Page

from ..log import get_logger
from .guard import REVIEW_ALLOW, SAFE_SUBMIT_TYPE, SUBMIT_TEXT, Guard

log = get_logger("actions")

_DESCRIBE_JS = """
(el) => {
  let t = el;
  const aid = el.getAttribute && el.getAttribute('data-automation-id');
  if (aid === 'click_filter' && el.parentElement) {
    // Workday: a transparent overlay above the real button; judge the button it covers (the overlay itself may
    // carry role=button aria-label=Submit, so it is skipped explicitly).
    const all = [...el.parentElement.querySelectorAll('button,[role=button],input[type=submit]')].filter(b => b !== el && !el.contains(b) && !b.contains(el));
    const cov = all.find(b => b.getClientRects().length > 0) || all[0];
    if (cov) t = cov;
  } else {
    const c = el.closest ? el.closest('button,[role=button],input[type=submit],input[type=button],a,[role=option],[role=menuitem],label') : null;
    if (c) t = c;
  }
  const cs = window.getComputedStyle(t);
  const r = t.getBoundingClientRect();
  return {
    tag: t.tagName.toLowerCase(),
    type: t.getAttribute('type') || '',
    text: ((t.innerText || t.value || '') + '').trim().slice(0, 160),
    aria: t.getAttribute('aria-label') || '',
    automationId: t.getAttribute('data-automation-id') || '',
    role: t.getAttribute('role') || '',
    id: t.id || '',
    name: t.getAttribute('name') || '',
    inForm: !!t.closest('form'),
    hidden: cs.visibility === 'hidden' || cs.display === 'none' || t.getAttribute('aria-hidden') === 'true' || (r.width === 0 && r.height === 0),
    offscreen: r.right < 0 || r.bottom < 0,
    disabled: !!t.disabled || t.getAttribute('aria-disabled') === 'true',
  };
}
"""

_HONEYPOT_NAMES = re.compile(r"beecatcher|honey|honeypot|hp_|bot-?field|website$", re.I)

# The link or button on a job POSTING page that opens the application form. Only `open_apply` uses it, and only
# after verifying the page has nothing fillable (a page without fields cannot submit an application).
APPLY_RX = re.compile(
    r"^\s*(apply( now| for this job| for this position| to this job| to this position| online| here| today)?|"
    r"i'?m interested|start( your| my)? application|begin( your)? application|apply for job|apply to job)\s*$", re.I)

_FILLABLE_COUNT_JS = """
() => {
  const vis = (el) => { const cs = getComputedStyle(el); if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect(); return (r.width > 0 || r.height > 0) && r.right >= 0 && r.bottom >= 0; };
  let n = 0;
  for (const el of document.querySelectorAll('input,textarea,select,[role=combobox],[role=textbox],[contenteditable="true"]')) {
    const t = (el.getAttribute('type') || '').toLowerCase();
    if (['hidden', 'search', 'submit', 'button', 'image', 'reset'].includes(t)) continue;
    if (/search|keyword/i.test(el.getAttribute('placeholder') || '') || /search|keyword/i.test(el.getAttribute('aria-label') || '')) continue;
    if (!vis(el)) continue;
    n++;
  }
  return n;
}
"""


class Refused(Exception):
    """The tool layer refused an action. Message says why; callers report and stop."""


@dataclass
class ElementInfo:
    tag: str
    type: str
    text: str
    aria: str
    automation_id: str
    role: str
    id: str
    name: str
    in_form: bool
    hidden: bool
    offscreen: bool
    disabled: bool

    @property
    def label(self) -> str:
        return self.text or self.aria or ""

    def summary(self) -> str:
        bits = [self.tag]
        if self.type:
            bits.append(f"type={self.type}")
        if self.automation_id:
            bits.append(f"data-automation-id={self.automation_id}")
        if self.id:
            bits.append(f"#{self.id}")
        return f"<{' '.join(bits)}> '{self.label[:60]}'"


# Workday's account form buttons carry "Submit" in their automation id but only sign in or create the candidate
# account (their labels are "Sign In" / "Create Account"; the DOM guard lets those form submits through as well).
# An application submit on Workday is the footer Next button whose label becomes "Submit", refused by label.
AUTH_AUTOMATION_IDS = frozenset({"signInSubmitButton", "createAccountSubmitButton"})


class Actions:
    def __init__(self, page: Page, *, ats: str | None, guard: Guard | None = None,
                 review_reached: Callable[[Page], bool] | None = None, upload_dir: Path | None = None):
        self.page = page
        self.ats = ats or ""
        self.guard = guard
        self.review_reached = review_reached
        self.upload_dir = Path(upload_dir).resolve() if upload_dir else None
        self.log: list[dict[str, Any]] = []

    # ---------------------------------------------------------------- inspection

    def describe(self, locator: Locator) -> ElementInfo:
        d = locator.evaluate(_DESCRIBE_JS)
        return ElementInfo(tag=d["tag"], type=d["type"], text=d["text"], aria=d["aria"], automation_id=d["automationId"],
                           role=d["role"], id=d["id"], name=d["name"], in_form=bool(d["inForm"]), hidden=bool(d["hidden"]),
                           offscreen=bool(d["offscreen"]), disabled=bool(d["disabled"]))

    def is_review_page(self) -> bool:
        if self.review_reached is None:
            return False
        try:
            return bool(self.review_reached(self.page))
        except Exception:  # noqa: BLE001
            return False

    def refusal_reason(self, info: ElementInfo, *, review: bool | None = None) -> str | None:
        """Why a click on this element is refused, or None. Allowlists, never denylists, for anything on a review page."""
        label = info.label
        if SUBMIT_TEXT.match(label):
            return f"submit-like label {label!r}"
        if re.search(r"submit|finalize|sendapplication", info.automation_id, re.I) and info.automation_id not in AUTH_AUTOMATION_IDS:
            return f"submit-like automation id {info.automation_id!r}"
        review = self.is_review_page() if review is None else review
        if review and info.tag in ("button", "input", "a") and not REVIEW_ALLOW.match(label):
            return f"review page: only {REVIEW_ALLOW.pattern} may be clicked, not {label!r}"
        if info.tag == "input" and info.type == "submit":
            if not SAFE_SUBMIT_TYPE.match(label):
                return f"<input type=submit> {label!r}"
        if info.tag == "button" and info.type == "submit" and info.in_form and not SAFE_SUBMIT_TYPE.match(label):
            return f"<button type=submit> inside a form with label {label!r}"
        return None

    # ---------------------------------------------------------------- actions

    def _one(self, locator: Locator, nth: int | None) -> Locator:
        n = locator.count()
        if n == 0:
            raise LookupError("no element matches")
        if n > 1 and nth is None:
            cands = []
            for i in range(min(n, 6)):
                try:
                    cands.append(self.describe(locator.nth(i)).summary())
                except Exception:  # noqa: BLE001
                    cands.append("?")
            raise LookupError(f"{n} elements match; pass nth. Candidates: " + " | ".join(cands))
        return locator.nth(nth or 0)

    def click(self, locator: Locator, *, nth: int | None = None, timeout: float = 10000) -> ElementInfo:
        el = self._one(locator, nth)
        info = self.describe(el)
        reason = self.refusal_reason(info)
        if reason:
            self._record("click", info, "refused", reason)
            raise Refused(f"click refused: {reason}")
        if info.disabled:
            raise Refused(f"click refused: element disabled {info.summary()}")
        el.click(timeout=timeout)
        self._record("click", info, "ok")
        self._beat()
        return info

    def fill(self, locator: Locator, value: str, *, nth: int | None = None, slow: bool = False,
             timeout: float = 10000) -> str:
        el = self._one(locator, nth)
        info = self.describe(el)
        if _HONEYPOT_NAMES.search(info.name) or _HONEYPOT_NAMES.search(info.automation_id) or info.hidden:
            self._record("fill", info, "refused", "hidden or honeypot field")
            raise Refused(f"fill refused: hidden or honeypot field {info.summary()}")
        value = "" if value is None else str(value)
        if slow:
            el.click(timeout=timeout)
            el.fill("")
            el.type(value, delay=25)
        else:
            el.fill(value, timeout=timeout)
        observed = self._read_value(el)
        self._record("fill", info, "ok" if observed == value else "mismatch", intended=value, observed=observed)
        self._beat()
        return observed

    def type_chars(self, locator: Locator, value: str, *, nth: int | None = None, delay_ms: int = 40) -> str:
        """For widgets that re-render per keystroke (Workday dates): focus, then type slowly."""
        el = self._one(locator, nth)
        el.click()
        el.press("Control+A")
        el.press("Backspace")
        el.type(str(value), delay=delay_ms)
        observed = self._read_value(el)
        self._beat()
        return observed

    def select_option(self, locator: Locator, value: str, *, nth: int | None = None) -> str:
        """Native <select> only. Custom listboxes are handled by adapters with click()."""
        el = self._one(locator, nth)
        info = self.describe(el)
        try:
            el.select_option(label=value)
        except Exception:
            el.select_option(value=value)
        observed = self._read_value(el)
        self._record("select", info, "ok", intended=value, observed=observed)
        self._beat()
        return observed

    def set_checked(self, locator: Locator, checked: bool, *, nth: int | None = None) -> bool:
        el = self._one(locator, nth)
        info = self.describe(el)
        el.set_checked(checked)
        observed = bool(el.is_checked())
        self._record("check", info, "ok" if observed == checked else "mismatch", intended=checked, observed=observed)
        self._beat()
        return observed

    def upload(self, locator: Locator, path: Path, *, nth: int | None = None) -> str:
        path = Path(path).resolve()
        if self.upload_dir is not None and self.upload_dir not in path.parents and path.parent != self.upload_dir:
            raise Refused(f"upload refused: {path} is outside {self.upload_dir}")
        if not path.exists():
            raise FileNotFoundError(path)
        el = self._one(locator, nth)
        info = self.describe(el)
        el.set_input_files(str(path))
        self._record("upload", info, "ok", intended=path.name)
        self._beat()
        return path.name

    def press_safe(self, locator: Locator, key: str) -> None:
        """Only navigation keys inside widgets. Enter and Space are never allowed (Lever submits on Enter)."""
        if key.lower() in ("enter", "space", " ", "return"):
            raise Refused(f"key {key!r} is not allowed")
        locator.press(key)
        self._beat()

    def press_search(self, locator: Locator, *, nth: int | None = None) -> None:
        """Enter inside a search box that belongs to no <form> (Workday prompt widgets search on Enter). The real
        element is checked first: an <input> outside any form, marked as a search field. Anything else is refused,
        so this can never submit a form."""
        el = self._one(locator, nth)
        info = self.describe(el)
        is_search = bool(el.evaluate("e => (e.getAttribute('enterkeyhint') || '').toLowerCase() === 'search' || "
                                     "/search/i.test(e.getAttribute('placeholder') || '') || (e.getAttribute('type') || '') === 'search'"))
        if info.tag != "input" or info.in_form or not is_search:
            self._record("press_search", info, "refused", "not a form-less search box")
            raise Refused(f"Enter refused on {info.summary()}: not a form-less search box")
        el.press("Enter")
        self._record("press_search", info, "ok")
        self._beat()

    def open_apply(self) -> ElementInfo:
        """Follow the Apply link/button of a job POSTING page to reach its application form.

        Precondition, verified on the real page: no fillable field is visible, so nothing can be submitted from
        here. An `<a href>` is followed by navigating to its URL (never clicked: a target=_blank link would open a
        window). A script-driven button is clicked with the DOM layer paused for that one click, because the DOM
        layer blocks every "Apply"-labelled click; the tool-layer refusal is bypassed only inside this method, only
        for a label matching APPLY_RX, and only when the precondition holds."""
        n = int(self.page.evaluate(_FILLABLE_COUNT_JS))
        if n > 0:
            raise Refused(f"open_apply refused: the page already shows {n} fillable field(s); fill them instead")
        cands = self.page.locator("a, button, [role=button], input[type=button], input[type=submit]")
        chosen: Locator | None = None
        info: ElementInfo | None = None
        for i in range(min(cands.count(), 400)):
            el = cands.nth(i)
            try:
                if not el.is_visible():
                    continue
                d = self.describe(el)
            except Exception:  # noqa: BLE001
                continue
            if APPLY_RX.match(d.label) and not d.disabled:
                chosen, info = el, d
                break
        if chosen is None or info is None:
            raise LookupError("no Apply link or button is visible on this page")
        href = chosen.get_attribute("href") if info.tag == "a" else None
        if href and not href.strip().lower().startswith(("javascript:", "#")):
            from urllib.parse import urljoin
            url = urljoin(self.page.url, href.strip())
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            self._record("open_apply", info, "ok", intended=url)
            self._beat()
            return info
        if self.guard is not None:
            self.guard.suspend()
        try:
            chosen.click(timeout=10000)
        finally:
            if self.guard is not None:
                self.guard.resume()
        self._record("open_apply", info, "ok")
        self._beat()
        return info

    # ---------------------------------------------------------------- helpers

    def _read_value(self, el: Locator) -> str:
        try:
            return str(el.input_value())
        except Exception:  # noqa: BLE001
            try:
                return str(el.evaluate("e => e.value ?? e.textContent ?? ''"))
            except Exception:  # noqa: BLE001
                return ""

    def _beat(self) -> None:
        if self.guard is not None:
            self.guard.beat()

    def _record(self, action: str, info: ElementInfo, status: str, reason: str | None = None,
                intended: Any = None, observed: Any = None) -> None:
        self.log.append({"action": action, "element": info.summary(), "status": status, "reason": reason,
                         "intended": intended, "observed": observed})
        if status == "refused":
            log.warning("%s refused on %s: %s", action, info.summary(), reason)
