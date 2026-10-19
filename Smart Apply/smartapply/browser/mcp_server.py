"""stdio MCP server that exposes ONE SmartApply tab to a `claude -p` run (the fallback browser agent).

Started by `adapters/agent.py` through `--mcp-config` (under pythonw.exe, so no console window can ever appear).
It attaches to the SmartApply Chrome over CDP, finds the tab named by SMARTAPPLY_TARGET_ID, arms the submit guard
in this process and answers tool calls by delegating to `Actions` on a dedicated Playwright thread. Nothing here can
submit: every click goes through `Actions.click`, Enter is never pressed, no raw JavaScript is exposed, uploads are
confined to the application workdir, and passwords are filled from the vault without ever reaching the model.

Environment (all set by the adapter in the MCP config):
  SMARTAPPLY_TARGET_ID   CDP target id of the application's tab
  SMARTAPPLY_ATS         ATS kind ("" for unknown career sites)
  SMARTAPPLY_WORKDIR     data/applications/<job id>/ (resume, agent_report.json, agent_server.log)
  SMARTAPPLY_CONFIG      config.json path (Chrome profile, mail settings)
  SMARTAPPLY_JOB_URL     the posting URL; `navigate` stays on its host
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .guard import SUBMIT_TEXT, Guard

log = logging.getLogger("smartapply.mcp")

TOOL_NAMES = ["page_info", "snapshot", "fill", "fill_many", "fill_profile", "fill_password", "select", "check", "click", "upload",
              "open_application_form", "navigate", "wait", "read_verification_mail", "report"]

INSTRUCTIONS = (
    "You control one browser tab holding a job application for Alex Doe. Fill everything you can, never submit. "
    "Call snapshot to see the fields (each has a ref like e12), act with fill/select/check/click/upload, then snapshot "
    "again. Stop at the review or final page and call report. A click on a submit-like button is refused by design."
)

# ---------------------------------------------------------------- snapshot script

SNAPSHOT_JS = r"""
(args) => {
  const scopeSel = args.scope || '';
  const maxItems = args.max || 300;
  let root = document.body;
  if (scopeSel) { try { root = document.querySelector(scopeSel) || document.body; } catch (e) { root = document.body; } }
  const vis = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || el.getAttribute('aria-hidden') === 'true') return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    if (r.right < 0 || r.bottom < 0) return false;
    if (parseFloat(cs.opacity || '1') === 0 && !el.matches('input[type=file]')) return false;
    return true;
  };
  const txt = (el, n) => ((el && (el.innerText || el.textContent) || '') + '').replace(/\s+/g, ' ').trim().slice(0, n || 80);
  const byIds = (ids) => (ids || '').split(/\s+/).filter(Boolean).map(i => document.getElementById(i)).filter(Boolean).map(e => txt(e, 120)).join(' ');
  const labelOf = (el) => {
    let s = el.getAttribute('aria-label') || '';
    if (!s && el.getAttribute('aria-labelledby')) s = byIds(el.getAttribute('aria-labelledby'));
    if (!s && el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) s = txt(l, 120); }
    if (!s) { const l = el.closest('label'); if (l) s = txt(l, 120); }
    if (!s && (el.tagName === 'BUTTON' || el.getAttribute('role') === 'button' || el.getAttribute('role') === 'option' ||
               el.getAttribute('role') === 'tab' || el.getAttribute('role') === 'menuitem' || el.tagName === 'A')) s = txt(el, 100) || el.value || el.title || '';
    if (!s) s = el.getAttribute('placeholder') || el.getAttribute('title') || '';
    if (!s && (el.type === 'radio' || el.type === 'checkbox' || el.getAttribute('role') === 'radio' || el.getAttribute('role') === 'checkbox')) {
      const p = el.parentElement; if (p) s = txt(p, 120);
    }
    if (!s) { const fs = el.closest('fieldset'); const lg = fs && fs.querySelector('legend'); if (lg) s = txt(lg, 120); }
    if (!s) {
      // nearest preceding text in the wrapper (Workday-style formField wrappers, Oracle labels above the control)
      let p = el.parentElement, depth = 0;
      while (p && depth < 4 && !s) {
        const lab = p.querySelector('label, [id*="label" i], legend, .label, [class*="label" i]');
        if (lab && vis(lab)) s = txt(lab, 120);
        p = p.parentElement; depth++;
      }
    }
    return (s || '').slice(0, 120);
  };
  const groupOf = (el) => {
    const fs = el.closest('fieldset,[role=group],[role=radiogroup]');
    if (!fs) return '';
    const lg = fs.querySelector('legend') || (fs.getAttribute('aria-labelledby') ? document.getElementById(fs.getAttribute('aria-labelledby').split(/\s+/)[0]) : null);
    return lg ? txt(lg, 100) : (fs.getAttribute('aria-label') || '');
  };
  const errText = (el) => {
    const ids = el.getAttribute('aria-describedby') || el.getAttribute('aria-errormessage') || '';
    const t = byIds(ids);
    return t.slice(0, 160);
  };
  const HONEY = /beecatcher|honey|honeypot|hp_|bot-?field/i;
  const LINK_RX = /apply|continue|next|start|begin|sign in|log ?in|create account|register|resume|upload|i'm interested|submit|proceed/i;
  const sel = 'h1,h2,h3,h4,legend,[role=heading],input:not([type=hidden]),textarea,select,button,[role=button],[role=combobox],[role=listbox],[role=option],[role=radio],[role=checkbox],[role=menuitem],[role=tab],[role=link],[role=switch],[role=textbox],[contenteditable="true"],a[href]';
  const items = [];
  let fields = 0, headings = 0, truncated = false;
  for (const el of root.querySelectorAll(sel)) {
    if (items.length >= maxItems) { truncated = true; break; }
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || '';
    const isHeading = /^(h1|h2|h3|h4|legend)$/.test(tag) || role === 'heading';
    if (!vis(el)) continue;
    if (isHeading) { const t = txt(el, 100); if (t) { items.push({kind: 'heading', text: t}); headings++; } continue; }
    if (el.closest('#__sa_banner')) continue;
    const name = el.getAttribute('name') || '';
    const aid = el.getAttribute('data-automation-id') || '';
    if (HONEY.test(name) || HONEY.test(aid) || HONEY.test(el.id || '')) continue;
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'a' && !role) { const t = txt(el, 80); if (!scopeSel && !LINK_RX.test(t)) continue; }
    if ((tag === 'input' && (type === 'submit' || type === 'button' || type === 'image')) || tag === 'button' || role === 'button') {
      const t = txt(el, 80) || el.value || el.getAttribute('aria-label') || '';
      if (!t && !aid) continue;
    }
    // a <label> wrapping a native control would double count; skip the control's own wrapper roles
    if (role === 'combobox' && tag === 'input') { /* keep: the input is the control */ }
    // refs are stable for the life of the document: an element keeps its ref across snapshots, new ones count on
    let ref = el.getAttribute('data-sa-ref') || '';
    if (!/^e\d+$/.test(ref)) {
      window.__saRefN = (window.__saRefN || 0) + 1;
      ref = 'e' + window.__saRefN;
      try { el.setAttribute('data-sa-ref', ref); } catch (e) {}
    }
    const item = {kind: 'field', ref, tag, type, role, id: el.id || '', name, aid, label: labelOf(el), group: groupOf(el)};
    if (tag === 'select') {
      const opts = [...el.options].map(o => (o.text || '').trim()).filter(Boolean);
      item.options = opts.slice(0, 40); item.optionCount = opts.length;
      item.value = el.options[el.selectedIndex] ? (el.options[el.selectedIndex].text || '').trim() : '';
    } else if (type === 'password') {
      item.value = el.value ? '(set, ' + el.value.length + ' chars)' : '';
    } else if (type === 'checkbox' || type === 'radio' || role === 'checkbox' || role === 'radio' || role === 'switch') {
      item.checked = el.checked === true || el.getAttribute('aria-checked') === 'true';
      item.value = el.value && el.value !== 'on' ? el.value.slice(0, 60) : '';
    } else if (type === 'file') {
      item.value = el.files && el.files.length ? [...el.files].map(f => f.name).join(', ') : '';
      item.accept = el.getAttribute('accept') || '';
    } else if (tag === 'input' || tag === 'textarea') {
      item.value = (el.value || '').slice(0, 160);
      item.maxlength = el.getAttribute('maxlength') || '';
    } else if (role === 'combobox' || role === 'listbox' || role === 'textbox' || el.isContentEditable) {
      item.value = (el.value !== undefined ? el.value : txt(el, 120)) || '';
      item.expanded = el.getAttribute('aria-expanded') || '';
    } else if (tag === 'a') {
      item.href = (el.getAttribute('href') || '').slice(0, 200);
      item.text = txt(el, 80);
    } else {
      item.text = txt(el, 80) || el.value || '';
      item.expanded = el.getAttribute('aria-expanded') || '';
      item.selected = el.getAttribute('aria-selected') || '';
    }
    item.required = el.required === true || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(item.label);
    item.disabled = el.disabled === true || el.getAttribute('aria-disabled') === 'true';
    item.invalid = el.getAttribute('aria-invalid') === 'true';
    item.error = item.invalid ? errText(el) : '';
    item.inForm = !!el.closest('form');
    items.push(item); fields++;
  }
  const errors = [];
  for (const el of document.querySelectorAll('[role=alert],[aria-live=assertive],.error,.errors,.field-error,.help-block,[class*="error" i],[class*="invalid" i]')) {
    if (errors.length >= 12) break;
    if (!vis(el)) continue;
    const t = txt(el, 160);
    if (t && !errors.includes(t) && !/^error:?$/i.test(t)) errors.push(t);
  }
  const CAPTCHA_RX = /captcha-delivery|recaptcha|hcaptcha|turnstile|arkose|funcaptcha|challenges\.cloudflare|geo\.captcha|perimeterx|px-captcha/i;
  let captcha = '';
  for (const fr of document.querySelectorAll('iframe')) {
    const src = fr.getAttribute('src') || '';
    if (CAPTCHA_RX.test(src) && vis(fr)) { captcha = (fr.getAttribute('title') || 'iframe') + ' ' + src.slice(0, 80); break; }
  }
  if (!captcha) {
    const el = document.querySelector('.g-recaptcha, .h-captcha, .cf-turnstile, [data-sitekey], #px-captcha, [id^="ddChallenge"]');
    if (el && vis(el)) captcha = el.id || el.className || 'captcha widget';
  }
  return {url: location.href, title: document.title, items, errors, fields, headings, truncated, captcha,
          hasForm: !!document.querySelector('form'), fileInputs: document.querySelectorAll('input[type=file]').length};
}
"""

REVIEW_JS = r"""
() => {
  const vis = (el) => { const cs = getComputedStyle(el); if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect(); return (r.width > 0 || r.height > 0) && r.right >= 0 && r.bottom >= 0; };
  const re = __SUBMIT_RE__;
  const label = (b) => ((b.innerText || b.value || b.getAttribute('aria-label') || '') + '').trim();
  const submit = [...document.querySelectorAll('button,input[type=submit],[role=button],a')].some(b => vis(b) && re.test(label(b)));
  const steps = [...document.querySelectorAll('h1,h2,h3,[role=heading],[class*="step" i],[class*="progress" i],[aria-current=step],[aria-current=page]')]
      .filter(vis).map(e => (e.innerText || '').trim()).join(' | ').slice(0, 600);
  const review = /review|summary|confirm/i.test(steps) || /review|summary|confirm/i.test(location.href);
  return {submit, review, steps};
}
"""


def _js_regex(rx: re.Pattern) -> str:
    return "/" + rx.pattern.replace("/", r"\/") + "/i"


def generic_review_reached(page) -> bool:
    """Unknown ATS: a visible submit-like button on a page that calls itself review/summary/confirm."""
    try:
        d = page.evaluate(REVIEW_JS.replace("__SUBMIT_RE__", _js_regex(SUBMIT_TEXT)))
    except Exception:  # noqa: BLE001
        return False
    return bool(d.get("submit") and d.get("review"))


# ---------------------------------------------------------------- snapshot formatting

def format_snapshot(d: dict[str, Any]) -> str:
    lines = [f"URL: {d.get('url', '')}", f"TITLE: {d.get('title', '')}"]
    if d.get("captcha"):
        lines.append(f"CAPTCHA_PRESENT: {d['captcha']} (never solve it: call report with status blocked, reason captcha, and finish)")
    if d.get("errors"):
        lines.append("ERRORS ON PAGE: " + " || ".join(d["errors"][:8]))
    lines.append("")
    for it in d.get("items", []):
        if it.get("kind") == "heading":
            lines.append(f"## {it['text']}")
            continue
        tag, typ, role = it.get("tag", ""), it.get("type", ""), it.get("role", "")
        kind = tag
        if tag == "input":
            kind = f"input[{typ or 'text'}]"
        if role and role not in ("button",) or (role == "button" and tag != "button"):
            kind = f"{kind}<{role}>" if tag not in ("button",) else kind
        parts = [it["ref"], kind]
        label = it.get("label") or it.get("text") or ""
        if label:
            parts.append(json.dumps(label, ensure_ascii=False))
        if it.get("group") and it.get("group") != label:
            parts.append(f"group={json.dumps(it['group'], ensure_ascii=False)}")
        if it.get("required"):
            parts.append("required")
        if it.get("disabled"):
            parts.append("disabled")
        if "checked" in it:
            parts.append("checked" if it["checked"] else "unchecked")
            if it.get("value"):
                parts.append(f"value={json.dumps(it['value'], ensure_ascii=False)}")
        elif it.get("value"):
            parts.append(f"value={json.dumps(it['value'], ensure_ascii=False)}")
        if it.get("options") is not None:
            opts = it["options"]
            more = int(it.get("optionCount", len(opts))) - len(opts)
            parts.append("options=" + json.dumps(opts, ensure_ascii=False) + (f" (+{more} more)" if more > 0 else ""))
        if it.get("accept"):
            parts.append(f"accept={it['accept']}")
        if it.get("maxlength"):
            parts.append(f"maxlength={it['maxlength']}")
        if it.get("expanded"):
            parts.append(f"expanded={it['expanded']}")
        if it.get("selected"):
            parts.append(f"selected={it['selected']}")
        if it.get("href") and tag == "a":
            parts.append(f"href={it['href'][:120]}")
        if it.get("invalid"):
            parts.append("INVALID" + (f": {it['error']}" if it.get("error") else ""))
        if it.get("aid"):
            parts.append(f"aid={it['aid']}")
        elif it.get("name") and tag in ("input", "select", "textarea"):
            parts.append(f"name={it['name']}")
        lines.append(" ".join(parts))
    if d.get("truncated"):
        lines.append("(truncated: pass a narrower scope selector)")
    lines.append("")
    lines.append(f"{d.get('fields', 0)} interactive element(s); {d.get('fileInputs', 0)} file input(s); form present: {d.get('hasForm')}")
    return "\n".join(lines)


# ---------------------------------------------------------------- the tab worker (one Playwright thread)

@dataclass
class TabState:
    target_id: str
    ats: str
    workdir: Path
    job_url: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    refs: dict[str, dict[str, str]] = field(default_factory=dict)      # ref -> fallback selectors
    accounts: list[dict[str, str]] = field(default_factory=list)      # accounts created/used (no secrets)
    reports: list[dict[str, Any]] = field(default_factory=list)


class Tab:
    """Owns the Playwright connection on its own thread. `call(fn)` runs fn(self) there and returns the result."""

    def __init__(self, cfg, state: TabState):
        self.cfg = cfg
        self.state = state
        self.q: "queue.Queue[tuple[Callable, concurrent.futures.Future] | None]" = queue.Queue()
        self.ready = threading.Event()
        self.error: str | None = None
        self.page = None
        self.guard: Guard | None = None
        self.act = None
        self.chrome = None
        self.browser = None
        self.thread = threading.Thread(target=self._main, name="sa-tab", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        """Final report, disarm, disconnect: all on the Playwright thread (its objects are thread-bound)."""
        if self.ready.is_set() and not self.error:
            fut: concurrent.futures.Future = concurrent.futures.Future()
            self.q.put((lambda t: t.write_report(), fut))
            try:
                fut.result(timeout=10)
            except Exception:  # noqa: BLE001
                pass
        self.q.put(None)
        self.thread.join(timeout=15)

    # ---- thread body
    def _main(self) -> None:
        from playwright.sync_api import sync_playwright

        from .actions import Actions
        from .chrome import Chrome
        try:
            with sync_playwright() as pw:
                self.chrome = Chrome(self.cfg)
                self.browser = self.chrome.attach(pw)
                self.page = self.chrome.page_for_target(self.browser, self.state.target_id)
                if self.page is None:
                    self.error = f"tab {self.state.target_id} not found in the SmartApply Chrome"
                    self.ready.set()
                    return
                self.guard = Guard(self.page, self.state.ats or None, ttl_ms=int(self.cfg.guard.ttl_ms))
                self.guard.arm()
                self.act = Actions(self.page, ats=self.state.ats or None, guard=self.guard,
                                   review_reached=generic_review_reached, upload_dir=self.state.workdir)
                self.ready.set()
                log.info("attached to %s (%s)", self.state.target_id, self.page.url)
                while True:
                    try:
                        job = self.q.get(timeout=10)
                    except queue.Empty:
                        self.guard.beat(force=True)
                        continue
                    if job is None:
                        break
                    fn, fut = job
                    try:
                        fut.set_result(fn(self))
                    except Exception as e:  # noqa: BLE001
                        log.warning("tool error: %s", e)
                        fut.set_exception(e)
                    try:
                        self.write_report()      # after every call, so a killed run still leaves its trail
                    except Exception as e:  # noqa: BLE001
                        log.debug("report write: %s", e)
                try:
                    self.guard.disarm()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self.browser.close()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            log.exception("tab thread failed")
            self.ready.set()

    async def call(self, fn: Callable[["Tab"], Any], *, timeout: float = 120.0) -> Any:
        if not self.ready.is_set():
            await asyncio.get_running_loop().run_in_executor(None, self.ready.wait, 40)
        if self.error:
            raise RuntimeError(self.error)
        if not self.ready.is_set():
            raise RuntimeError("browser attach timed out")
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self.q.put((fn, fut))
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout)

    # ---- helpers used on the thread
    def locator(self, ref: str):
        ref = (ref or "").strip()
        if not re.fullmatch(r"e\d{1,4}", ref):
            raise ValueError(f"bad ref {ref!r}: refs look like e12 and come from snapshot")
        loc = self.page.locator(f'[data-sa-ref="{ref}"]')
        if loc.count() == 1:
            return loc
        fb = self.state.refs.get(ref) or {}
        for sel in (fb.get("id"), fb.get("aid"), fb.get("name")):
            if not sel:
                continue
            cand = self.page.locator(sel)
            if cand.count() == 1:
                return cand
        raise LookupError(f"{ref} is stale (the page re-rendered or navigated): call snapshot again")

    def remember_refs(self, d: dict[str, Any]) -> None:
        self.state.refs = {}
        for it in d.get("items", []):
            if it.get("kind") != "field":
                continue
            fb: dict[str, str] = {}
            if it.get("id"):
                fb["id"] = f'[id="{it["id"]}"]'
            if it.get("aid"):
                fb["aid"] = f'[data-automation-id="{it["aid"]}"]'
            if it.get("name") and it.get("tag") in ("input", "select", "textarea"):
                fb["name"] = f'{it["tag"]}[name="{it["name"]}"]'
            self.state.refs[it["ref"]] = fb

    def profile_atoms(self) -> dict[str, str]:
        if not hasattr(self, "_atoms"):
            from ..adapters.agent import profile_atoms
            from ..profile import load_profile
            self._atoms = profile_atoms(load_profile(self.cfg.profile_path)) if self.cfg.profile_path.exists() else {}
        return self._atoms

    def write_report(self) -> Path:
        path = self.state.workdir / "agent_report.json"
        actions = list(self.act.log) if self.act is not None else []
        data = {"target_id": self.state.target_id, "url": self.page.url if self.page else "", "actions": actions,
                "accounts": self.state.accounts, "reports": self.state.reports,
                "guard": {"aborted": list(self.guard.aborted) if self.guard else [],
                          "dom_blocked": self.guard.dom_blocked_count() if self.guard else 0},
                "updated_at": datetime.now(timezone.utc).isoformat()}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return path


# ---------------------------------------------------------------- tools

_ELEMENT_FACTS_JS = "e => ({tag: e.tagName.toLowerCase(), type: (e.getAttribute('type') || '').toLowerCase(), role: e.getAttribute('role') || ''})"


def _facts(loc) -> dict[str, str]:
    return dict(loc.evaluate(_ELEMENT_FACTS_JS))


OPTION_SEL = '[role=option], [role=listbox] li, [role=menuitem], [data-automation-id=promptOption]'


WIDGET_OPTION_SEL = '[role=option], [role=gridcell], [role=treeitem], [role=menuitem], [role=row], li'


def _popup_for(t: "Tab", loc):
    """The element a combobox says it controls (aria-controls / aria-owns), once it exists and is visible."""
    try:
        cid = loc.get_attribute("aria-controls") or loc.get_attribute("aria-owns") or ""
    except Exception:  # noqa: BLE001
        cid = ""
    if not cid:
        return None
    pop = t.page.locator(f'[id="{cid.split()[0]}"]')
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            if pop.count() and pop.first.is_visible():
                return pop.first
        except Exception:  # noqa: BLE001
            pass
        t.page.wait_for_timeout(150)
    return None


def _open_widget(t: "Tab", loc) -> None:
    """Open a custom dropdown so its popup is visible. Order matters (Oracle HCM, Citizens 2026-10-01): the control
    may say aria-expanded=true while its popup is gone, and the toggle button then closes instead of opening. So:
    a visible popup is left alone; else the control itself is clicked; else the toggle bound to the same popup."""
    try:
        cid = (loc.get_attribute("aria-controls") or "").split()
    except Exception:  # noqa: BLE001
        cid = []
    pop = t.page.locator(f'[id="{cid[0]}"]') if cid else None

    def popup_visible() -> bool:
        try:
            return bool(pop is not None and pop.count() and pop.first.is_visible())
        except Exception:  # noqa: BLE001
            return False

    if popup_visible():
        return
    try:
        t.act.click(loc)
        t.page.wait_for_timeout(400)
    except Exception as e:  # noqa: BLE001
        log.debug("control click failed: %s", e)
    if pop is None or popup_visible():
        return
    tog = t.page.locator(f'button[aria-controls="{cid[0]}"], [role=button][aria-controls="{cid[0]}"]')
    if tog.count():
        try:
            t.act.click(tog.first)
            t.page.wait_for_timeout(400)
        except Exception as e:  # noqa: BLE001
            log.debug("toggle click failed: %s", e)


def _visible_options(t: "Tab", scope, limit: int = 200) -> list[tuple[Any, str]]:
    """(locator, text) of the visible option-like elements, innermost first so a row's cell wins over the row."""
    out: list[tuple[Any, str]] = []
    seen: set[str] = set()
    opts = (scope.locator(WIDGET_OPTION_SEL) if scope is not None else t.page.locator(OPTION_SEL))
    n = opts.count()
    for i in range(min(n, limit)):
        o = opts.nth(i)
        try:
            if not o.is_visible():
                continue
            tx = o.inner_text(timeout=300).strip()
        except Exception:  # noqa: BLE001
            continue
        if not tx or ("\n" in tx and len(tx) > 60):
            continue           # a container row holding several cells
        key = tx.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((o, tx))
    return out


def _match_option(cands: list[tuple[Any, str]], wanted: str):
    w = wanted.strip().lower()
    for o, tx in cands:
        if tx.lower() == w:
            return o
    starts = [(o, tx) for o, tx in cands if tx.lower().startswith(w)]
    if len(starts) == 1:
        return starts[0][0]
    if len(starts) > 1 and w:
        return starts[0][0]
    return None


def _pick_custom_option(t: "Tab", loc, f: dict[str, str], option: str):
    _open_widget(t, loc)
    t.page.wait_for_timeout(350)
    scope = _popup_for(t, loc)
    cands = _visible_options(t, scope)
    if not cands and scope is None:
        deadline = time.time() + 3
        while time.time() < deadline and not cands:
            t.page.wait_for_timeout(200)
            cands = _visible_options(t, None)
    hit = _match_option(cands, option)
    offered = list(cands)          # what the widget listed before any probe typing filtered it down
    if hit is None and (f["tag"] == "input" or f["role"] in ("combobox", "textbox")):
        # a searchable combobox: type, then look again inside its popup
        t.act.fill(loc, option, slow=True)
        t.page.wait_for_timeout(700)
        scope = _popup_for(t, loc)
        cands = _visible_options(t, scope)
        hit = _match_option(cands, option)
    if hit is None and not cands and (f["tag"] == "input" or f["role"] in ("combobox", "textbox")):
        # nothing listed for the full text: type its first letter so the widget shows what it has, then match
        # loosely (a "decline" wish matches "Prefer not to say" on Oracle, "I don't wish to answer" elsewhere)
        t.act.fill(loc, option.strip()[:1], slow=True)
        t.page.wait_for_timeout(700)
        scope = _popup_for(t, loc)
        cands = _visible_options(t, scope)
        hit = _match_option(cands, option) or _loose_option(cands, option)
    if hit is None:
        # Report the unfiltered list (Citizens 2026-10-01: a Gender list of Female / Male has no decline option; the
        # agent must see that and leave the field, not keep probing letters).
        seen = {tx for _o, tx in offered}
        listing = [tx for _o, tx in offered] + [tx for _o, tx in cands if tx not in seen]
        shown = ", ".join(json.dumps(tx, ensure_ascii=False) for tx in listing[:30])
        try:
            t.act.fill(loc, "")       # leave no stray probe text behind
        except Exception:  # noqa: BLE001
            pass
        try:
            t.page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass
        if shown:
            raise LookupError(f"option {option!r} not found; the widget offers only: {shown}. "
                              "If none of these is right, leave the field and report it in left_for_human.")
        raise LookupError(f"option {option!r} not found; the widget offers: nothing visible (try fill with slow=true, then snapshot)")
    return hit, [tx for _o, tx in cands]


_DECLINE_WORDS = re.compile(r"decline|prefer not|not to (say|answer|disclose|self)|do not wish|don't wish|rather not|not specified|choose not", re.I)


def _loose_option(cands: list[tuple[Any, str]], wanted: str):
    """Synonym match for the one case that recurs on every EEO form: a decline-style wish."""
    if not _DECLINE_WORDS.search(wanted):
        return None
    for o, tx in cands:
        if _DECLINE_WORDS.search(tx):
            return o
    return None


def _commit_combobox(t: "Tab", loc, value: str) -> str | None:
    """After typing into a combobox (Oracle JET, react-select, MUI): if exactly one visible option matches the
    typed text, click it so the widget keeps the value (these widgets discard free text on blur)."""
    try:
        if _facts(loc)["role"] != "combobox" and not loc.get_attribute("aria-autocomplete"):
            return None
    except Exception:  # noqa: BLE001
        return None
    wanted = (value or "").strip().lower()
    if not wanted:
        return None
    t.page.wait_for_timeout(500)
    cands = _visible_options(t, _popup_for(t, loc), limit=60)
    hits = [(o, tx) for o, tx in cands if tx.lower() == wanted] or [(o, tx) for o, tx in cands if tx.lower().startswith(wanted)]
    if len(hits) != 1:
        return None
    try:
        t.act.click(hits[0][0])
        t.page.wait_for_timeout(200)
        return hits[0][1]
    except Exception:  # noqa: BLE001
        return None


def _same_site(a: str, b: str) -> bool:
    ha = urllib.parse.urlsplit(a).netloc.lower().split(":")[0]
    hb = urllib.parse.urlsplit(b).netloc.lower().split(":")[0]
    if ha == hb:
        return True
    ra = ".".join(ha.split(".")[-2:])
    rb = ".".join(hb.split(".")[-2:])
    return ra == rb and ra != ""


def _settle(tab: Tab, ms: int = 600) -> None:
    try:
        tab.page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    tab.page.wait_for_timeout(ms)


def build_server(cfg, state: TabState):
    from mcp.server.mcpserver import MCPServer

    from .actions import Refused

    tab = Tab(cfg, state)
    server = MCPServer("smartapply", instructions=INSTRUCTIONS)

    def _ok(msg: str) -> str:
        return msg

    def _err(e: Exception) -> str:
        if isinstance(e, Refused):
            return f"REFUSED: {e}. This action can never be performed by Smart Apply; leave it for Alex and move on."
        return f"ERROR: {type(e).__name__}: {str(e)[:400]}"

    @server.tool()
    async def page_info() -> str:
        """Current URL, title, whether the review/final page was reached, visible error texts, and the guard state."""
        def fn(t: Tab):
            d = t.page.evaluate(SNAPSHOT_JS, {"scope": "", "max": 0})
            rv = t.page.evaluate(REVIEW_JS.replace("__SUBMIT_RE__", _js_regex(SUBMIT_TEXT)))
            return (f"URL: {t.page.url}\nTITLE: {d.get('title', '')}\nreview_or_final_page: {bool(rv.get('submit') and rv.get('review'))}\n"
                    f"submit_button_visible: {bool(rv.get('submit'))}\nstep_text: {rv.get('steps', '')[:300]}\n"
                    f"errors: {' || '.join(d.get('errors', [])[:8]) or 'none'}\ncaptcha: {d.get('captcha') or 'none'}\n"
                    f"guard_armed: {t.guard.dom_armed()}\n"
                    f"file_inputs: {d.get('fileInputs', 0)}")
        try:
            return await tab.call(fn)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @server.tool()
    async def snapshot(scope: str = "") -> str:
        """List the visible interactive elements with refs (e1, e2, ...), labels, values, options and errors.
        `scope` is an optional CSS selector to narrow the listing (for example "form" or "#application")."""
        def fn(t: Tab):
            d = t.page.evaluate(SNAPSHOT_JS, {"scope": scope or "", "max": 300})
            t.remember_refs(d)
            t.guard.beat(force=True)
            return format_snapshot(d)
        try:
            return await tab.call(fn)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @server.tool()
    async def fill(ref: str, value: str, slow: bool = False) -> str:
        """Type `value` into the text-like field `ref`. Use slow=true for autocomplete boxes that need real keystrokes
        (then snapshot to see the suggestions and click one)."""
        def fn(t: Tab):
            loc = t.locator(ref)
            observed = t.act.fill(loc, value, slow=slow)
            msg = f"filled {ref}: observed value {json.dumps(observed[:200], ensure_ascii=False)}"
            committed = _commit_combobox(t, loc, value)
            if committed:
                msg += f"; committed the matching option {json.dumps(committed, ensure_ascii=False)}"
            elif _facts(loc)["role"] == "combobox":
                msg += " (this is a combobox: if the value does not stick, use select(ref, option) instead)"
            return msg
        try:
            return await tab.call(fn)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @server.tool()
    async def fill_many(items: list[dict[str, str]]) -> str:
        """Fill several text fields at once: items = [{"ref": "e3", "value": "Alex"}, ...]. Returns one line per item."""
        def fn(t: Tab):
            out = []
            for it in items:
                ref, value = str(it.get("ref", "")), str(it.get("value", ""))
                try:
                    observed = t.act.fill(t.locator(ref), value, slow=bool(it.get("slow", False)))
                    out.append(f"{ref}: ok ({json.dumps(observed[:80], ensure_ascii=False)})")
                except Exception as e:  # noqa: BLE001
                    out.append(f"{ref}: {_err(e)}")
            return "\n".join(out)
        try:
            return await tab.call(fn, timeout=300)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @server.tool()
    async def fill_profile(ref: str, field: str, slow: bool = False) -> str:
        """Type a PROFILE fact into `ref` without retyping it yourself: field is its path in PROFILE, for example
        first_name, last_name, email, phone, phone_digits, address.street, address.street2, address.city,
        address.state, address.state_name, address.zip, address.country, linkedin, github, school.name, school.degree.
        Always prefer this over fill for profile facts (no transcription mistakes)."""
        def fn(t: Tab):
            atoms = t.profile_atoms()
            key = (field or "").strip()
            if key not in atoms:
                close = [k for k in atoms if key.lower() in k.lower()][:12]
                raise LookupError(f"unknown profile field {key!r}; known: " + ", ".join(close or sorted(atoms)[:40]))
            observed = t.act.fill(t.locator(ref), atoms[key], slow=slow)
            return f"filled {ref} from profile {key}: observed {json.dumps(observed[:200], ensure_ascii=False)}"
        try:
            return await tab.call(fn)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @server.tool()
    async def fill_password(ref: str, account: str, confirm_ref: str = "") -> str:
        """Fill a password field from Smart Apply's vault for `account` (use the site's host, e.g. "jpmc.fa.oraclecloud.com").
        A new password is generated and stored when none exists. The password is never shown to you. Pass the
        "confirm password" field as confirm_ref when the form has one."""
        def fn(t: Tab):
            from .. import vault
            acct = (account or "").strip().lower()
            if not acct:
                raise ValueError("account is required (the site host)")
            pw = vault.get_password("agent", acct)
            created = False
            if not pw:
                pw = vault.new_account_password(None)
                vault.set_password("agent", acct, pw)
                created = True
            for r in [ref] + ([confirm_ref] if confirm_ref else []):
                loc = t.locator(r)
                f = _facts(loc)
                if not (f["tag"] == "input" and f["type"] == "password"):
                    raise ValueError(f"{r} is not a password input (<{f['tag']} type={f['type']}>)")
                t.act.fill(loc, pw)
            t.state.accounts.append({"account": acct, "created": created, "at": datetime.now(timezone.utc).isoformat()})
            t.write_report()
            return f"password {'generated and stored' if created else 'from vault'} for {acct}; filled {ref}" + (f" and {confirm_ref}" if confirm_ref else "")
        try:
            return await tab.call(fn)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @server.tool()
    async def select(ref: str, option: str) -> str:
        """Choose `option` (exact visible text, or a unique prefix) in the dropdown `ref`: a native <select>, or a
        custom combobox/listbox that opens on click."""
        def fn(t: Tab):
            loc = t.locator(ref)
            f = _facts(loc)
            info = t.act.describe(loc)
            if f["tag"] == "select":
                observed = t.act.select_option(loc, option)
                return f"selected {json.dumps(observed, ensure_ascii=False)} in {ref}"
            # custom widget: open it (its toggle button when it has one, else the control), then pick the option
            # inside the popup it controls (aria-controls); options may be role=option, gridcell, row or plain li
            hit, texts = _pick_custom_option(t, loc, f, option)
            t.act.click(hit)
            t.page.wait_for_timeout(300)
            observed = t.act._read_value(loc) if f["tag"] in ("input", "select", "textarea") else ""
            return f"picked option in {ref}" + (f": observed {json.dumps(observed[:100], ensure_ascii=False)}" if observed else "")
        try:
            return await tab.call(fn)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @server.tool()
    async def check(ref: str, checked: bool = True) -> str:
        """Set a checkbox, radio or switch `ref` to checked (true) or unchecked (false)."""
        def fn(t: Tab):
            loc = t.locator(ref)
            f = _facts(loc)
            if f["tag"] == "input" and f["type"] in ("checkbox", "radio"):
                observed = t.act.set_checked(loc, bool(checked))
                return f"{ref} checked={observed}"
            # ARIA widgets: click toggles
            state = loc.get_attribute("aria-checked")
            if (state == "true") != bool(checked):
                t.act.click(loc)
                t.page.wait_for_timeout(200)
            return f"{ref} aria-checked={loc.get_attribute('aria-checked')}"
        try:
            return await tab.call(fn)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @server.tool()
    async def click(ref: str) -> str:
        """Click the button, link, option or tab `ref`. Submit-like buttons are refused; Next/Continue/Save are fine."""
        def fn(t: Tab):
            info = t.act.click(t.locator(ref))
            _settle(t, 500)
            return f"clicked {info.summary()}; now at {t.page.url}"
        try:
            return await tab.call(fn)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @server.tool()
    async def upload(ref: str, filename: str = "") -> str:
        """Attach the resume PDF (the only file available; `filename` defaults to it) to the file input `ref`."""
        def fn(t: Tab):
            wd = t.state.workdir
            pdfs = sorted(wd.glob("*.pdf"))
            if filename:
                path = wd / Path(filename).name
            elif pdfs:
                path = pdfs[0]
            else:
                raise FileNotFoundError("no resume PDF in the workdir")
            name = t.act.upload(t.locator(ref), path)
            t.page.wait_for_timeout(1200)
            return f"uploaded {name} into {ref}"
        try:
            return await tab.call(fn)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @server.tool()
    async def open_application_form() -> str:
        """On a job POSTING page (no fillable fields), follow its Apply link/button to reach the application form.
        Refused when the page already shows fillable fields."""
        def fn(t: Tab):
            info = t.act.open_apply()
            _settle(t, 1200)
            return f"followed {info.summary()}; now at {t.page.url}. Call snapshot."
        try:
            return await tab.call(fn, timeout=90)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @server.tool()
    async def navigate(url: str) -> str:
        """Load a URL on the same site as the job posting (for example the posting URL again, or an apply link seen in
        snapshot). Other hosts are refused."""
        def fn(t: Tab):
            if not _same_site(url, t.state.job_url) and not _same_site(url, t.page.url):
                raise ValueError(f"navigate refused: {url} is not on the job's site")
            t.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            _settle(t, 800)
            t.guard.beat(force=True)
            return f"now at {t.page.url}"
        try:
            return await tab.call(fn, timeout=90)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @server.tool()
    async def wait(seconds: float = 2.0) -> str:
        """Wait up to 10 seconds for the page to settle (after a click that triggers loading)."""
        def fn(t: Tab):
            s = max(0.2, min(float(seconds), 10.0))
            t.page.wait_for_timeout(int(s * 1000))
            t.guard.beat(force=True)
            return f"waited {s:.1f}s; now at {t.page.url}"
        try:
            return await tab.call(fn)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @server.tool()
    async def read_verification_mail(match: str = "", wait_seconds: int = 45) -> str:
        """Read Alex's inbox (read-only) for a verification mail that arrived since this run started, optionally
        matching `match` (a word from the sender or subject). Returns the subject, any numeric codes and links."""
        def fn(t: Tab):
            from .. import mail as _mail
            rx = re.compile(re.escape(match), re.I) if match else None
            since = t.state.started_at - timedelta(minutes=3)
            deadline = time.time() + max(0, min(int(wait_seconds), 50))
            while True:
                mails = _mail.fetch_recent(t.cfg, days=1, limit=40,
                                           want=lambda f, s: (rx is None or bool(rx.search(f) or rx.search(s))))
                fresh = [m for m in mails if m.received_at and m.received_at >= since and (rx is None or rx.search(m.from_addr) or rx.search(m.subject))]
                if fresh:
                    m = max(fresh, key=lambda x: x.received_at)
                    codes = sorted(set(re.findall(r"(?<![\d-])\d{4,8}(?![\d-])", (m.subject or "") + "\n" + (m.body or ""))))
                    links = [l for l in (m.links or []) if not re.search(r"unsubscribe|privacy|terms|facebook|twitter|linkedin\.com/company", l, re.I)][:8]
                    return (f"from: {m.from_addr}\nsubject: {m.subject}\nreceived: {m.received_at.isoformat()}\n"
                            f"codes: {', '.join(codes) or 'none'}\nlinks:\n" + "\n".join(links) + f"\n\nbody (first 600 chars):\n{(m.body or '')[:600]}")
                if time.time() >= deadline:
                    return "no matching mail arrived yet; call again after a short wait, or leave this step for Alex"
                time.sleep(8)
        try:
            return await tab.call(fn, timeout=70)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @server.tool()
    async def report(status: str, reason: str = "", left_for_human: list[dict[str, str]] | None = None,
                     notes: list[str] | None = None) -> str:
        """Record progress (call it at least once before finishing, and whenever something is left for Alex).
        status: ready_for_review | needs_human | blocked. left_for_human: [{"label": ..., "why": ...}]."""
        def fn(t: Tab):
            t.state.reports.append({"status": status, "reason": reason, "left_for_human": left_for_human or [],
                                    "notes": notes or [], "url": t.page.url, "at": datetime.now(timezone.utc).isoformat()})
            p = t.write_report()
            return f"recorded ({len(t.state.reports)} report(s)) in {p.name}"
        try:
            return await tab.call(fn)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    return server, tab


# ---------------------------------------------------------------- entry point

def main() -> int:
    target = os.environ.get("SMARTAPPLY_TARGET_ID", "")
    workdir = Path(os.environ.get("SMARTAPPLY_WORKDIR") or ".").resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(workdir / "agent_server.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # stdout is the MCP transport: nothing else may ever print there
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    from ..config import load_config
    cfg_path = os.environ.get("SMARTAPPLY_CONFIG")
    cfg = load_config(Path(cfg_path) if cfg_path else None)
    state = TabState(target_id=target, ats=os.environ.get("SMARTAPPLY_ATS", ""), workdir=workdir,
                     job_url=os.environ.get("SMARTAPPLY_JOB_URL", ""))
    if not target:
        log.error("SMARTAPPLY_TARGET_ID missing")
    server, tab = build_server(cfg, state)
    tab.start()
    try:
        server.run(transport="stdio")
    finally:
        tab.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
