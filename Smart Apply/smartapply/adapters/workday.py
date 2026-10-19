"""Workday apply flow (myworkdayjobs.com): sign in or create the tenant account, then the wizard pages
My Information, My Experience, Application Questions (1 to 3 pages), Voluntary Disclosures, [Self Identify],
Review. Stops on Review; the footer button there reads "Submit" and is never clicked (tool layer), the
`/jobapplication/{id}/finalize` request is aborted (network layer) and the DOM script covers it (DOM layer).

Selectors (research/03-ats-mechanics.md §1, live 2026-09-25): every field sits in
`div[data-automation-id="formField-<name>"]` with its label; the control inside is a text input, a
`button[aria-haspopup=listbox]` dropdown (`[role=option]`), a radio group, a checkbox, a search prompt
(`[data-automation-id=promptOption]`), month/day/year spinbuttons (`dateSection*-input`), a textarea or a file
input (`file-upload-input-ref`, success `file-upload-successful`). Only structural ids are hardcoded; My Information,
Voluntary Disclosures and Self Identify are filled by wrapper id suffix and label text, so the two markup
generations (`applyFlowMyInfoPage` / `contactInformationPage`) share one code path. Questionnaire controls are
named `{primary|secondary|supplementary}Questionnaire--<questionId>` and joined to the prefetched answers.
Sign-in and create-account buttons sit under a transparent `click_filter` overlay: that overlay is what gets
clicked. The hidden `beecatcher` input is never touched (Actions refuses honeypots).

Every page is saved on Workday's servers when Next succeeds, so a crash resumes from the draft; the page is
saved only after its read-back passed (the fields were verified before Next).
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import time
from datetime import date, timezone
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import Locator, Page

from .. import db, mail, vault
from ..answers import DECLINE, AnswerPacket, best_how_heard, heard_rank, how_heard_candidates, match_option
from ..browser.guard import SUBMIT_TEXT
from ..intake import ExperienceEntry, parse_experience_entries, resolve_role_folder, resume_skills
from ..models import Answer, FillReport, Question
from ..profile import Profile
from ..util import read_json
from .base import Adapter, log

ATS = "workday"
# _dropdown sentinel: take best_how_heard of the listed options instead of a named value
_BEST_HEARD = "<best how heard>"

PAGE_IDS = {
    "applyFlowMyInfoPage": "my_information", "contactInformationPage": "my_information",
    "applyFlowMyExpPage": "my_experience", "myExperiencePage": "my_experience",
    "applyFlowPrimaryQuestionsPage": "questions_primary", "applyFlowSecondaryQuestionsPage": "questions_secondary",
    "applyFlowSupplementaryQuestionsPage": "questions_supplementary",
    "applyFlowVoluntaryDisclosuresPage": "voluntary_disclosures", "voluntaryDisclosuresPage": "voluntary_disclosures",
    "applyFlowSelfIdentifyPage": "self_identify", "selfIdentificationPage": "self_identify",
    "applyFlowReviewPage": "review", "reviewSubmitPage": "review",
    "alreadyAppliedPage": "already_applied",
    "signInContent": "account",
}
STEP_TEXT = [("review", re.compile(r"review", re.I)), ("my_information", re.compile(r"my information|contact information", re.I)),
             ("my_experience", re.compile(r"my experience|experience", re.I)),
             ("voluntary_disclosures", re.compile(r"voluntary|disclosure", re.I)),
             ("self_identify", re.compile(r"self[- ]identif", re.I)),
             ("questions_primary", re.compile(r"question", re.I)), ("account", re.compile(r"create account|sign in", re.I))]
QUESTION_PAGES = {"questions_primary": "primary", "questions_secondary": "secondary", "questions_supplementary": "supplementary"}
NEXT_BTN = "[data-automation-id=pageFooterNextButton], [data-automation-id=bottom-navigation-next-button]"
BACK_BTN = "[data-automation-id=pageFooterBackButton], [data-automation-id=bottom-navigation-back-button]"
MAX_PAGES = 12
FIELD_JS = """() => [...document.querySelectorAll('[data-automation-id^="formField-"]')].map(e => {
    const lab = e.querySelector('label'); const r = e.getBoundingClientRect();
    const ctl = e.querySelector('input:not([type=hidden]), textarea, button[aria-haspopup=listbox], button, [role=listbox]');
    const legend = e.querySelector('legend, [data-automation-id=richText], span[data-automation-id]');
    return {id: e.getAttribute('data-automation-id').slice(10), label: (lab ? lab.innerText : (legend ? legend.innerText : e.innerText)).trim().slice(0, 200),
            ctl: ctl ? ctl.tagName.toLowerCase() + (ctl.type ? ':' + ctl.type : '') + (ctl.getAttribute('aria-haspopup') ? ':' + ctl.getAttribute('aria-haspopup') : '') : '',
            ctl_id: ctl ? (ctl.id || '') : '', ctl_aid: ctl ? (ctl.getAttribute('data-automation-id') || '') : '',
            required: !!(lab && /\\*/.test(lab.innerText)) || !!(ctl && ctl.required) || !!(ctl && ctl.getAttribute('aria-required') === 'true'),
            visible: r.width > 0 && r.height > 0,
            value: ctl ? (ctl.type === 'password' ? (ctl.value ? '***' : '') : (ctl.value || ctl.innerText || '').trim().slice(0, 80)) : '',
            radios: [...e.querySelectorAll('input[type=radio]')].map(x => ((x.labels && x.labels[0]) ? x.labels[0].innerText : x.value).trim()),
            checked: !!e.querySelector('input:checked') || !!e.querySelector('[data-automation-id=selectedItem]'),
            error: /Error:/i.test(e.innerText) };
})"""


class WorkdayAdapter(Adapter):
    ats = ATS
    single_page = False

    # ---------------------------------------------------------------- contract

    @classmethod
    def start_url(cls, app, questions: list[Question]) -> str:
        p = app.ats_params or {}
        if p.get("apply_url"):
            return str(p["apply_url"])
        u = app.url.split("?")[0].rstrip("/")
        return u if u.endswith("/apply/applyManually") else u + "/apply/applyManually"

    def review_reached(self, page: Page) -> bool:
        try:
            rev = page.locator("[data-automation-id=applyFlowReviewPage], [data-automation-id=reviewSubmitPage]")
            if rev.count() and rev.first.is_visible():
                return True
            step = page.locator("[data-automation-id=progressBarActiveStep]")
            if step.count() and re.search(r"review", step.first.inner_text(), re.I):
                return True
            nxt = page.locator(NEXT_BTN)
            if nxt.count() and nxt.first.is_visible() and SUBMIT_TEXT.match(nxt.first.inner_text().strip()):
                return True
        except Exception:  # noqa: BLE001
            return False
        return False

    # ---------------------------------------------------------------- state

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.tenant = str(self.app.tenant or (self.app.ats_params or {}).get("tenant") or "")
        self.params = dict(self.app.ats_params or {})
        self.generation: str | None = None
        self.application_id: str | None = self.app.application_id
        self.pages_done = 0
        self.page_dumps: list[dict[str, Any]] = []
        self.page.on("response", self._on_response)

    def _on_response(self, resp) -> None:
        try:
            if resp.request.method == "POST" and re.search(r"/jobpostings/[^/]+/jobapplications(\?|$)", resp.url):
                data = resp.json()
                aid = str(data.get("id") or data.get("jobApplicationId") or "")
                if aid:
                    self.application_id = aid
                    db.update_application(self.conn, self.app.id, application_id=aid)
        except Exception:  # noqa: BLE001
            pass

    def _persist(self, step: int) -> None:
        self.pages_done = max(self.pages_done, step)
        db.update_application(self.conn, self.app.id, last_step=self.pages_done, generation=self.generation,
                              application_id=self.application_id, tenant=self.tenant)

    # ---------------------------------------------------------------- page inventory

    def fields(self) -> list[dict[str, Any]]:
        try:
            return [f for f in self.page.evaluate(FIELD_JS) if f.get("visible")]
        except Exception as e:  # noqa: BLE001
            log.debug("field inventory failed: %s", e)
            return []

    def _dump(self, name: str) -> None:
        d = {"page": name, "url": self.page.url, "fields": self.fields()}
        try:
            d["buttons"] = self.page.evaluate("""() => [...document.querySelectorAll('button')].filter(b => b.getBoundingClientRect().width > 0)
                .map(b => (b.getAttribute('data-automation-id') || '') + '|' + b.innerText.trim().slice(0, 40))""")
            d["sections"] = self.page.evaluate("""() => [...document.querySelectorAll('[data-automation-id$="Section"], [data-automation-id$="section"]')]
                .map(e => e.getAttribute('data-automation-id') + '|' + (e.querySelector('h3,h2,h4') ? e.querySelector('h3,h2,h4').innerText.trim() : ''))""")
        except Exception:  # noqa: BLE001
            pass
        self.page_dumps.append(d)
        try:
            (self.app.workdir / "wd_pages.json").write_text(json.dumps(self.page_dumps, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def _visible(self, selector: str) -> bool:
        loc = self.page.locator(selector)
        try:
            return any(loc.nth(i).is_visible() for i in range(min(loc.count(), 6)))
        except Exception:  # noqa: BLE001
            return False

    def wait_rendered(self, timeout_s: float = 12.0) -> None:
        """After Next the new page arrives asynchronously: wait until a known container or its fields exist."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                if self._visible("[data-automation-id=applyFlowReviewPage], [data-automation-id=reviewSubmitPage], "
                                 "[data-automation-id=alreadyAppliedPage], [data-automation-id=signInContent]"):
                    return
                if self._visible('[data-automation-id^="formField-"]'):
                    self.page.wait_for_timeout(400)
                    return
            except Exception:  # noqa: BLE001
                pass
            self.page.wait_for_timeout(300)

    def detect_page(self) -> str:
        page = self.page
        for aid, name in PAGE_IDS.items():
            loc = page.locator(f"[data-automation-id={aid}]")
            try:
                if loc.count() and loc.first.is_visible():
                    if self.generation is None and aid in ("applyFlowMyInfoPage", "contactInformationPage"):
                        self.generation = "new" if aid.startswith("applyFlow") else "old"
                    return name
            except Exception:  # noqa: BLE001
                continue
        if self._something_went_wrong():
            return "error"
        step = page.locator("[data-automation-id=progressBarActiveStep]")
        if step.count():
            txt = step.first.inner_text()
            for name, rx in STEP_TEXT:
                if rx.search(txt):
                    if name == "questions_primary":
                        # live: wrappers are formField-<questionId>; the control id carries the questionnaire kind
                        for pg, kind in QUESTION_PAGES.items():
                            if page.locator(f'[id^="{kind}Questionnaire--"]').count():
                                return pg
                        return "unknown"
                    return name
        if page.locator("[data-automation-id=email]").count() and page.locator("[data-automation-id=password]").count():
            return "account"
        if page.locator("[data-automation-id=applyManually], [data-automation-id=autofillWithResume]").count():
            return "start"
        # Signed in, on .../apply, and the page body is empty (NiSource, 2026-10-01): Workday's other way of saying a
        # draft exists; it is continued from Candidate Home exactly like "Something went wrong".
        if re.search(r"/apply(/|$|\?)", page.url) and page.locator('[data-automation-id="navigationItem-Candidate Home"]').count()                 and not page.locator('[data-automation-id^="formField-"], [data-automation-id=progressBar]').count():
            return "error"
        return "unknown"

    def _something_went_wrong(self) -> bool:
        try:
            heads = self.page.locator("h1, h2, h3, [role=heading]")
            for i in range(min(heads.count(), 8)):
                h = heads.nth(i)
                if h.is_visible() and re.search(r"something went wrong", h.inner_text(), re.I):
                    return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _continue_from_candidate_home(self) -> tuple[bool, str]:
        """Open Candidate Home and continue this job's saved draft through its Related Actions menu."""
        page = self.page
        home = page.locator('[data-automation-id="navigationItem-Candidate Home"]')
        if home.count() and home.first.is_visible():
            self.act.click(home.first)
        else:
            u = urllib.parse.urlsplit(page.url)
            parts = [p for p in u.path.split("/") if p]
            # /{lang}/{site}/job/..., /{site}/job/... or (myworkdaysite) /{lang}/recruiting/{tenant}/{site}/job/...:
            # Candidate Home sits under everything that precedes "job", minus the locale
            if "job" in parts and parts.index("job") > 0:
                base = [x for x in parts[:parts.index("job")] if not re.fullmatch(r"[a-z]{2}-[A-Za-z]{2}", x)]
            else:
                base = parts[:1]
            page.goto(f"{u.scheme}://{u.netloc}/{'/'.join(base)}/userHome", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        menus = page.locator("[data-automation-id=actionMenuTarget]")
        if menus.count() == 0:
            return False, "Candidate Home lists no application to continue"
        chosen = menus.first
        if menus.count() > 1:
            # the menu button has no text; its table row reads "Software Intern 1215275 Not Submitted ..." (HPE,
            # 2026-10-14: with two drafts the first was always continued, so one job's refill filled the other)
            req = (self.app.ats_id or "").split(":")[-1]
            title = re.sub(r"\s+", " ", self.app.title or "").strip().lower()
            chosen = None
            words = set(re.findall(r"[a-z0-9]{3,}", title)) - {"intern", "internship", "the", "and", "for", "summer"}
            scored = []
            for i in range(menus.count()):
                try:
                    txt = menus.nth(i).evaluate(
                        "e=>{const r=e.closest('tr,li,[role=row]'); return (r||e).innerText.replace(/\\s+/g,' ')}")
                except Exception:  # noqa: BLE001
                    continue
                if (req and re.search(rf"\b{re.escape(req)}\b", txt)) or (title and txt.lower().startswith(title + " ")):
                    chosen = menus.nth(i)
                    break
                scored.append((len(words & set(re.findall(r"[a-z0-9]{3,}", txt.lower()))), i))
            # no requisition id (Brevan): the one row sharing the most title words, when it is a clear winner
            if chosen is None and scored:
                scored.sort(reverse=True)
                if scored[0][0] >= 2 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                    chosen = menus.nth(scored[0][1])
            if chosen is None:
                return False, "Candidate Home lists several drafts and none matches this job"
        self.act.click(chosen)
        page.wait_for_timeout(800)
        cont = page.locator("[data-automation-id=continueApplication]")
        if cont.count() == 0 or not cont.first.is_visible():
            return False, "no Continue Application action for this job on Candidate Home"
        self.act.click(cont.first)
        page.wait_for_timeout(2500)
        self.wait_rendered()
        return True, ""

    # ---------------------------------------------------------------- widgets

    def _wrapper(self, name: str) -> Locator | None:
        n = name.replace("\\", "\\\\").replace('"', '\\"')
        w = self.page.locator(f'[data-automation-id="formField-{n}"]')
        if w.count():
            return w.first
        ctl = self.page.locator(f'[id="{n}"], [data-automation-id="{n}"]')
        if ctl.count():
            anc = ctl.first.locator('xpath=ancestor::*[starts-with(@data-automation-id, "formField-")][1]')
            return anc.first if anc.count() else ctl.first
        return None

    def _wrapper_by_label(self, rx: re.Pattern, root: Locator | None = None) -> Locator | None:
        base = root if root is not None else self.page
        wraps = base.locator('[data-automation-id^="formField-"]')
        for i in range(wraps.count()):
            w = wraps.nth(i)
            try:
                lab = w.locator("label, legend").first
                text = lab.inner_text() if lab.count() else w.inner_text()[:120]
            except Exception:  # noqa: BLE001
                continue
            if rx.search(text or ""):
                return w
        return None

    def _kind(self, w: Locator) -> str:
        if w.locator("input[type=file]").count():
            return "file"
        if w.locator("[data-automation-id*=dateSection]").count():
            return "date"
        if w.locator("button[aria-haspopup=listbox]").count():
            return "dropdown"
        if w.locator("input[type=radio]").count():
            return "radio"
        if w.locator("input[type=checkbox]").count():
            return "checkbox"
        if w.locator("[data-automation-id=multiselectInputContainer], [data-automation-id=multiSelectContainer], input[placeholder=Search], [data-automation-id=searchBox]").count():
            return "prompt"
        if w.locator("textarea").count():
            return "textarea"
        if w.locator("input").count():
            return "text"
        return "unknown"

    def _text(self, w: Locator, value: str) -> str:
        ctl = w.locator("textarea, input:not([type=hidden]):not([type=file]):not([type=radio]):not([type=checkbox])").first
        return self.act.fill(ctl, value)

    def _dropdown(self, w: Locator, wanted: str, *, fallback_decline: bool = False, exact: bool = False) -> str:
        btn = w.locator("button[aria-haspopup=listbox]").first
        self._close_popups()
        # Closed listboxes stay in the DOM (hidden), so the open one is the visible one, never `.first`. A first
        # click sometimes only blurs the previous widget, so the button is clicked again when nothing opened.
        listbox = None
        for attempt in range(3):
            self.act.click(btn)
            deadline = time.time() + (2.5 if attempt < 2 else 6.0)
            while time.time() < deadline and listbox is None:
                self.page.wait_for_timeout(150)
                all_lb = self.page.locator("[role=listbox]")
                for i in range(all_lb.count() - 1, -1, -1):
                    lb = all_lb.nth(i)
                    try:
                        if lb.is_visible() and lb.locator("[role=option]").count():
                            listbox = lb
                            break
                    except Exception:  # noqa: BLE001
                        continue
            if listbox is not None:
                break
        if listbox is None:
            raise LookupError("dropdown did not open")
        opts = listbox.locator("[role=option]")
        labels = [opts.nth(i).inner_text().strip() for i in range(opts.count())]
        if wanted == _BEST_HEARD:
            picked = best_how_heard(labels)
        else:
            picked = _exact_in(labels, wanted) if exact else match_option(labels, wanted)
        if picked is None and fallback_decline:
            picked = _decline_like(labels)
        if picked is None:
            self.page.keyboard.press("Escape")
            raise LookupError(f"no option matches {wanted!r} among {labels[:10]}")
        self.act.click(opts.nth(labels.index(picked)))
        self.page.wait_for_timeout(150)
        return btn.inner_text().strip()

    def _radio(self, w: Locator, wanted: str, *, fallback_decline: bool = False) -> str:
        self._close_popups()
        radios = w.locator("input[type=radio]")
        labels: list[str] = []
        for i in range(radios.count()):
            r = radios.nth(i)
            rid = r.get_attribute("id") or ""
            lab = self.page.locator(f'label[for="{rid}"]') if rid else r.locator("xpath=ancestor::label[1]")
            labels.append((lab.first.inner_text().strip() if lab.count() else (r.get_attribute("value") or "")))
        picked = match_option(labels, wanted) or (_decline_like(labels) if fallback_decline else None)
        if picked is None:
            raise LookupError(f"no radio matches {wanted!r} among {labels}")
        idx = labels.index(picked)
        r = radios.nth(idx)
        rid = r.get_attribute("id") or ""
        target = self.page.locator(f'label[for="{rid}"]').first if rid and self.page.locator(f'label[for="{rid}"]').count() else r
        self.act.click(target)
        self.page.wait_for_timeout(150)
        return picked if r.is_checked() else ""

    def _checkbox(self, w: Locator, checked: bool) -> bool:
        box = w.locator("input[type=checkbox]").first
        if box.is_checked() == checked:
            return checked
        try:
            return self.act.set_checked(box, checked)
        except Exception:  # noqa: BLE001
            bid = box.get_attribute("id") or ""
            lab = self.page.locator(f'label[for="{bid}"]')
            self.act.click(lab.first if lab.count() else box)
            self.page.wait_for_timeout(120)
            return box.is_checked()

    def _prompt(self, w: Locator, wanted: str, *, multi: bool = False, exact: bool = False) -> str:
        """Search prompt: type, wait for promptOption rows, click the best match (never Enter)."""
        # already selected? (Country Phone Code comes pre-filled with the account's country)
        selected = w.locator("[data-automation-id=selectedItem]")
        try:
            have = [selected.nth(i).inner_text().strip() for i in range(selected.count())]
        except Exception:  # noqa: BLE001
            have = []
        have = [h for h in have if h]
        if have:
            # keep a selection only when it is the wanted value or starts with it ("United States of America (+1)"
            # for "United States"); "Applied Computer Science" is not "Computer Science" (HP refill, 2026-10-05)
            wn = re.sub(r"[^a-z0-9]+", " ", str(wanted).lower()).strip()
            hit = next((h for h in have if re.sub(r"[^a-z0-9]+", " ", h.lower()).strip().startswith(wn)), None)
            if hit:
                return hit
            if not multi:
                for i in range(selected.count()):
                    charm = selected.nth(i).locator("[data-automation-id=DELETE_charm]")
                    try:
                        self.act.click(charm.first if charm.count() else selected.nth(i))
                        self.page.wait_for_timeout(200)
                    except Exception:  # noqa: BLE001
                        pass
        container = w.locator("[data-automation-id=multiselectInputContainer]")
        if container.count():
            try:
                container.first.click(timeout=3000)
            except Exception:  # noqa: BLE001
                pass
        inp = w.locator("input[placeholder=Search], [data-automation-id=searchBox], input:not([type=hidden])").first
        self.act.fill(inp, wanted, slow=True)
        opts = self.page.locator("[data-automation-id=promptOption]")

        def _labels() -> list[str]:
            try:
                got = [opts.nth(i).inner_text().strip() for i in range(min(opts.count(), 150))]
            except Exception:  # noqa: BLE001
                got = []
            return [x for x in got if x and not re.match(r"^(partial list|search results|\d+ items?|no matches|all$)", x, re.I)]

        # Prompts with a long list (schools, fields of study, phone codes) only search on Enter: the typed text
        # alone shows the category tree ("Partial List", "All"). Enter here is inside a form-less search box.
        labels: list[str] = []
        picked = None
        def _exact(ls: list[str]) -> str | None:
            w = re.sub(r"[^a-z0-9]+", " ", str(wanted).lower()).strip()
            # a trailing qualifier is the same item: "Python (Programming Language)" for "Python" (skills, 2026-10-13)
            return next((x for x in ls if re.sub(r"[^a-z0-9]+", " ", x.lower()).strip() == w
                         or re.sub(r"[^a-z0-9]+", " ", re.sub(r"\s*\([^)]*\)\s*$", "", x).lower()).strip() == w), None)

        deadline = time.time() + 2.0
        while time.time() < deadline:
            self.page.wait_for_timeout(350)
            labels = _labels()
            picked = _exact(labels) if labels else None    # fuzzy picks wait for the full search results below
            if picked:
                break
        if picked is None:
            try:
                self.act.press_search(inp)
            except Exception as e:  # noqa: BLE001
                log.debug("press_search refused: %s", e)
            deadline = time.time() + 8.0
            while time.time() < deadline:
                self.page.wait_for_timeout(400)
                labels = _labels()
                picked = match_option(labels, wanted) if labels else None
                if picked:
                    break
        if picked is None and labels and len(wanted.split()) > 1:
            # taxonomy lists (Field of Study: "Computer and Information Sciences" for "Computer Science")
            first = wanted.split()[0].lower()
            picked = next((x for x in labels if x.lower().startswith(first)), None)
        if picked is None and labels:
            # two-level prompts (How did you hear: category -> detail): open the first category and search again
            cat = next((x for x in labels if re.search(r"job board|online|social|website|other", x, re.I)), None)
            if cat and not multi:
                self.act.click(opts.nth(labels.index(cat)))
                self.page.wait_for_timeout(600)
                labels2 = [opts.nth(i).inner_text().strip() for i in range(min(opts.count(), 60))]
                picked2 = match_option(labels2, wanted)
                if picked2:
                    self.act.click(opts.nth(labels2.index(picked2)))
                    self.page.wait_for_timeout(200)
                    self._close_popups()
                    return picked2
        if picked is None:
            self.page.keyboard.press("Escape")
            raise LookupError(f"no prompt option matches {wanted!r} (got {labels[:6]})")
        if _exact([picked]) is None:
            if exact:   # a later candidate may match exactly ("Computer and Information Science" on HP's list)
                self.page.keyboard.press("Escape")
                raise LookupError(f"no exact option {wanted!r} (closest {picked!r})")
            self.report.notes.append(f"{wanted!r}: no exact option, picked {picked!r} from {labels[:15]}")
        self.act.click(opts.nth(labels.index(picked)))
        self.page.wait_for_timeout(500)
        after = _labels()
        if after and picked not in after and after != labels:
            # the pick was a category whose sub-list opened instead of a selection (TEL, 2026-10-01: "Job Board /
            # Social Network" -> "Job Board"); choose the same answer inside it, else its "Other"
            # exact names only: a loose "Online" picked the Brazilian board "Deficiente Online" (HP, 2026-10-05).
            # Simplify / GitHub first: the pipeline finds list jobs on Simplify's GitHub internship list.
            sub = next((m for c in ("Simplify", "SimplifyJobs", "GitHub", wanted, "Other", "Other Job Board", "Others",
                                    "Career Site", "Company Website", "Company Career Site", "Website", "Internet", "Online")
                        if (m := _exact_in(after, c))), None)
            if sub is None and re.search(r"website|career", picked, re.I):
                # a "Website" category that holds only the company's own site (Kensho, 2026-10-11: "Kensho Careers")
                sub = next((x for x in after if x != picked and re.search(r"\bcareers?\b|\bwebsite\b", x, re.I)), None)
            if sub is None:
                self.report.notes.append(f"{wanted!r}: no fitting entry under {picked!r}: {after[:20]}")
                # back out so the caller tries its next candidate; returning the category left the field empty and
                # Next failed with "How Did You Hear About Us?: error" (Adobe, 2026-10-10: "Job Board" holds only
                # named boards like Glassdoor and Handshake)
                self.page.keyboard.press("Escape")
                self._close_popups()
                raise LookupError(f"no fitting entry under {picked!r}")
            # (DraftKings, 2026-10-01: "Job Boards" lists only named boards, no "Other"; its "Career Site" is true)
            if sub:
                self.act.click(opts.nth(after.index(sub)))
                self.page.wait_for_timeout(250)
                self._close_popups()
                return sub
        self._close_popups()
        return picked

    def _pick_source(self, w: Locator) -> str:
        """How did you hear: open the full list (no search text) and walk down the category tree, each level taking
        best_how_heard. Clears an existing pick first. "" when nothing on the list ranks."""
        selected = w.locator("[data-automation-id=selectedItem]")
        for i in range(selected.count() - 1, -1, -1):
            charm = selected.nth(i).locator("[data-automation-id=DELETE_charm]")
            try:
                self.act.click(charm.first if charm.count() else selected.nth(i))
                self.page.wait_for_timeout(200)
            except Exception:  # noqa: BLE001
                pass
        container = w.locator("[data-automation-id=multiselectInputContainer]")
        try:
            self.act.click(container.first if container.count() else w.locator("input:not([type=hidden])").first)
        except Exception:  # noqa: BLE001
            pass
        opts = self.page.locator("[data-automation-id=promptOption]")
        idx: dict[str, int] = {}

        def _labels() -> list[str]:
            # selected pills elsewhere on the page are promptOptions too (Kensho, 2026-10-11: the phone code
            # "United States of America (+1)" was read as a source); only the open list counts
            try:
                got = opts.evaluate_all("""es => es.map(e => (e.closest('[data-automation-id^=formField]') ||
                    e.offsetParent === null) ? '' : e.innerText.trim())""")
            except Exception:  # noqa: BLE001
                got = []
            idx.clear()
            for i, x in enumerate(got):
                if x and x not in idx and not re.match(r"^(partial list|search results|\d+ items?|no matches|all$)", x, re.I):
                    idx[x] = i
            return list(idx)

        def _open_top() -> list[str]:
            got: list[str] = []
            deadline = time.time() + 4.0
            while time.time() < deadline and not got:
                self.page.wait_for_timeout(300)
                got = _labels()
            return got

        # a category can hold only named programs the candidate never attended (Kensho, 2026-10-13: "Campus
        # Recruiting Event" > "Wall Street Bound"); then back out and try the next best category
        tried: set[str] = set()
        labels = _open_top()
        for _attempt in range(3):
            left = [x for x in labels if x not in tried]
            # a top level of bare folders (Adobe, 2026-10-13: "Adobe Source", "Contingent Worker-Specific") ranks
            # nothing; open the generic source folder and rank inside it
            choice = best_how_heard(left) or next(
                (x for x in left if re.search(r"\bsources?\b", x, re.I)
                 and not re.search(r"contingent|employee|internal|referr|agency|worker", x, re.I)), None)
            if not choice:
                self.report.notes.append(f"how heard: nothing ranks in {labels[:20]}")
                break
            tried.add(choice)
            for _depth in range(3):
                self.act.click(opts.nth(idx[choice]))
                self.page.wait_for_timeout(600)
                if selected.count():
                    self._close_popups()
                    return choice
                after = _labels()
                if not after or after == labels:
                    self._close_popups()
                    return choice if selected.count() else ""
                labels = after
                # a leaf can repeat its category's name ("Professional Conference/Networking Event" twice)
                choice = best_how_heard(after)
                if not choice:
                    self.report.notes.append(f"how heard: nothing ranks under {sorted(tried)[-1]!r}: {after[:10]}")
                    break
            self.page.keyboard.press("Escape")
            self._close_popups()
            try:
                self.act.click(container.first if container.count() else w.locator("input:not([type=hidden])").first)
            except Exception:  # noqa: BLE001
                pass
            labels = _open_top()
        self.page.keyboard.press("Escape")
        self._close_popups()
        return ""

    def _close_popups(self) -> None:
        """Prompt result lists stay open after a pick (a popper with click-outside-close) and intercept clicks on
        the fields below. Escape closes them and keeps the selection."""
        for attempt in range(3):
            pops = self.page.locator("[data-popper-placement], [data-behavior-click-outside-close]")
            try:
                if not any(pops.nth(i).is_visible() for i in range(min(pops.count(), 4))):
                    return
                if attempt == 0:
                    # the popper closes on a click outside: the job title heading is a safe, non-interactive target
                    head = self.page.locator("[data-automation-id=jobTitleHeading], [data-automation-id=applyFlowPage] h2, h2").first
                    if head.count():
                        self.act.click(head)
                    else:
                        self.page.keyboard.press("Escape")
                else:
                    self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(250)
            except Exception:  # noqa: BLE001
                try:
                    self.page.keyboard.press("Escape")
                except Exception:  # noqa: BLE001
                    return

    def _date(self, w: Locator, ym: str, *, day: str | None = None) -> str:
        """Month/day/year spinbuttons. Live: the <input role=spinbutton> is 0.1 px wide; clicking its `-display`
        sibling focuses it, typed digits fill it and focus moves on to the next section by itself. Never Enter."""
        year, month = ym[:4], ym[5:7]
        # The focused section must belong to THIS widget: every date widget on the page uses the same automation ids,
        # so a page-wide check let digits land in the previous row's month (HP refill, 2026-10-01: "MM/2026").
        active_js = "e => (e.contains(document.activeElement) && document.activeElement.getAttribute('data-automation-id')) || ''"

        def focus(key: str) -> None:
            if w.evaluate(active_js) != f"dateSection{key}-input":
                disp = w.locator(f"[data-automation-id=dateSection{key}-display]")
                (disp.first if disp.count() else w.locator(f"[data-automation-id=dateSection{key}-input]").first).click(timeout=5000)
                self.page.wait_for_timeout(120)

        def value(key: str) -> str:
            try:
                return w.locator(f"[data-automation-id=dateSection{key}-input]").first.input_value().strip()
            except Exception:  # noqa: BLE001
                return ""

        for key, val in (("Month", month), ("Day", day), ("Year", year)):
            if val is None or w.locator(f"[data-automation-id=dateSection{key}-input]").count() == 0:
                continue
            for attempt in range(3):
                focus(key)
                have = value(key)
                if have:
                    # refill or a wrong earlier value: Backspace until this section is empty, checking after every
                    # press. Some tenants clear a digit per press, others (HP, 2026-10-01) the whole section at once;
                    # a Backspace on an empty section jumps to the previous section and eats its digit.
                    for _ in range(len(have)):
                        self.page.keyboard.press("Backspace")
                        self.page.wait_for_timeout(80)
                        if not value(key) or w.evaluate(active_js) != f"dateSection{key}-input":
                            break
                    focus(key)
                for ch in str(val):
                    self.page.keyboard.type(ch, delay=0)
                    self.page.wait_for_timeout(90)
                self.page.wait_for_timeout(150)
                self.act._beat()
                got = value(key)
                if got.isdigit() and int(got) == int(val):
                    break
                log.debug("date %s: wanted %s got %r (attempt %d)", key, val, got, attempt + 1)
        try:
            self.page.keyboard.press("Tab")     # leave the widget so it commits; Tab never submits
        except Exception:  # noqa: BLE001
            pass
        self.page.wait_for_timeout(200)
        # read back after everything settled
        out = []
        for key in ("Month", "Day", "Year"):
            sec = w.locator(f"[data-automation-id=dateSection{key}-input]")
            if sec.count():
                try:
                    out.append(sec.first.input_value().strip())
                except Exception:  # noqa: BLE001
                    out.append("")
        return "/".join(x for x in out if x)

    def _upload(self, w: Locator | None) -> str:
        inp = (w.locator("input[type=file]") if w is not None else self.page.locator("input[data-automation-id=file-upload-input-ref], input[type=file]"))
        self.act.upload(inp, self.resume_path, nth=0)
        deadline = time.time() + 20.0
        while time.time() < deadline:
            self.page.wait_for_timeout(500)
            if self._visible("[data-automation-id=file-upload-successful]"):
                return self.resume_path.name
            if self.page.locator("[data-automation-id=file-upload-error], [data-automation-id=errorMessage]").count():
                break
        names = self.page.locator("[data-automation-id=file-upload-item], [data-automation-id=file-upload-item-name]")
        try:
            txt = names.first.inner_text() if names.count() else ""
        except Exception:  # noqa: BLE001
            txt = ""
        return self.resume_path.name if self.resume_path.stem.lower() in txt.lower() else ""

    def _set(self, w: Locator, value: Any, *, kind: str | None = None, fallback_decline: bool = False,
             exact: bool = False) -> str:
        kind = kind or self._kind(w)
        vals = value if isinstance(value, list) else [value]
        if kind == "dropdown":
            return self._dropdown(w, str(vals[0]), fallback_decline=fallback_decline, exact=exact)
        if kind == "radio":
            return self._radio(w, str(vals[0]), fallback_decline=fallback_decline)
        if kind == "checkbox":
            want = str(vals[0]).strip().lower() in ("yes", "true", "1", "y")
            return "Yes" if self._checkbox(w, want) == want and want else ("No" if not want else "")
        if kind == "prompt":
            done = [self._prompt(w, str(v), multi=len(vals) > 1, exact=exact) for v in vals]
            return "; ".join(done)
        if kind == "date":
            v = str(vals[0])
            return self._date(w, v[:7], day=(v[8:10] if len(v) >= 10 else ("01" if w.locator("[data-automation-id=dateSectionDay-input]").count() else None)))
        if kind == "file":
            return self._upload(w)
        return self._text(w, "; ".join(str(v) for v in vals))

    # ---------------------------------------------------------------- navigation

    def _accept_cookies(self) -> None:
        btn = self.page.locator("[data-automation-id=legalNoticeAcceptButton]")
        try:
            if btn.count() and btn.first.is_visible():
                self.act.click(btn.first)
                self.page.wait_for_timeout(300)
        except Exception as e:  # noqa: BLE001
            log.debug("cookie banner: %s", e)

    def _errors(self) -> list[str]:
        out: list[str] = []
        try:
            for f in self.fields():
                if f.get("error"):
                    out.append(f"{f['label'][:60]}: error")
            banner = self.page.locator("[data-automation-id=errorMessage], [data-automation-id=pageErrorMessage], [role=alert]")
            for i in range(min(banner.count(), 5)):
                t = banner.nth(i).inner_text().strip()
                if t and t not in out and re.search(r"error|required|invalid|must|fix|correct", t, re.I) \
                        and not re.search(r"success", t, re.I):
                    out.append(t[:160])
        except Exception:  # noqa: BLE001
            pass
        return out

    def _step_text(self) -> str:
        s = self.page.locator("[data-automation-id=progressBarActiveStep]")
        try:
            return s.first.inner_text().strip() if s.count() else ""
        except Exception:  # noqa: BLE001
            return ""

    def _next(self, current: str) -> tuple[bool, list[str]]:
        """Click Save and Continue. True when the page advanced; otherwise the inline errors."""
        btn = self.page.locator(NEXT_BTN)
        if btn.count() == 0:
            return False, ["no Next button on this page"]
        label = btn.first.inner_text().strip()
        if SUBMIT_TEXT.match(label):
            return False, [f"footer button reads {label!r}: this is the review page"]
        before = self._step_text()
        self.act.click(btn.first)
        deadline = time.time() + 25.0
        while time.time() < deadline:
            self.page.wait_for_timeout(400)
            try:
                if self._step_text() != before and self.detect_page() != current:
                    self.page.wait_for_timeout(600)
                    return True, []
                errs = self._errors()
                if errs:
                    self.page.wait_for_timeout(500)
                    return False, self._errors() or errs
            except Exception:  # noqa: BLE001
                continue
        errs = self._errors()
        return False, errs or ["page did not advance within 25 s"]

    # ---------------------------------------------------------------- account

    def _on_signin_form(self) -> bool:
        return self.page.locator("[data-automation-id=signInSubmitButton]").count() > 0 and self._visible("[data-automation-id=signInContent]")

    def _reveal_email_form(self) -> bool:
        """Some tenants (DraftKings) open with Apple / Google / "Sign in with email" buttons; the email form and the
        Create Account link only render after the email button. Returns True when it clicked."""
        btn = self.page.locator("[data-automation-id=SignInWithEmailButton]")
        if btn.count() and btn.first.is_visible() and not (self._on_signin_form() or self._on_create_form()):
            self.act.click(btn.first)
            self.page.wait_for_timeout(1000)
            return True
        return False

    def _on_create_form(self) -> bool:
        return self.page.locator("[data-automation-id=createAccountSubmitButton]").count() > 0 and self._visible("[data-automation-id=signInContent]")

    def _submit_auth(self, button_id: str) -> None:
        wrap = self.page.locator("[data-automation-id=noCaptchaWrapper]")
        overlay = wrap.locator("[data-automation-id=click_filter]") if wrap.count() else self.page.locator("[data-automation-id=click_filter]")
        if overlay.count():
            self.act.click(overlay.first)
        else:
            self.act.click(self.page.locator(f"[data-automation-id={button_id}]").first)

    def _auth_error(self) -> str:
        loc = self.page.locator("[data-automation-id=errorMessage], [data-automation-id=alertMessage], [role=alert]")
        try:
            for i in range(min(loc.count(), 4)):
                t = loc.nth(i).inner_text().strip()
                if t:
                    return t[:200]
        except Exception:  # noqa: BLE001
            pass
        return ""

    VERIFY_RX = re.compile(r"verification (e-?mail|link)|e-?mail has been sent|verify your (e-?mail|account)|check your (e-?mail|inbox)|"
                           r"not (been )?verified|activate your account", re.I)

    def _verify_wording(self) -> str:
        try:
            for sel in ("[data-automation-id=alertMessage]", "[data-automation-id=signInContent]", "[data-automation-id=pageMainContent]"):
                loc = self.page.locator(sel)
                if loc.count():
                    txt = loc.first.inner_text()
                    if self.VERIFY_RX.search(txt):
                        return txt.strip()[:200]
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _wait_leave_auth(self, timeout_s: float = 25.0) -> tuple[bool, str]:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self.page.wait_for_timeout(500)
            if not self._visible("[data-automation-id=signInContent]"):
                # left the auth form: either the flow, or a redirect to /login with a "verify your account" alert
                self.page.wait_for_timeout(1500)
                self.wait_rendered(8.0)
                v = self._verify_wording()
                if v:
                    return False, "VERIFY:" + v
                if not self._visible("[data-automation-id=signInContent]"):
                    return True, ""
            v = self._verify_wording()
            if v:
                return False, "VERIFY:" + v
            err = self._auth_error()
            if err:
                return False, err
        try:
            snippet = re.sub(r"\s+", " ", self.page.locator("[data-automation-id=signInContent]").first.inner_text())[:240]
        except Exception:  # noqa: BLE001
            snippet = ""
        return False, self._auth_error() or f"still on the sign-in page ({snippet})"

    def _sign_in(self, password: str) -> tuple[bool, str]:
        deadline = time.time() + 12.0
        while time.time() < deadline and not (self._on_signin_form() or self._on_create_form()):
            if self._reveal_email_form():
                continue
            self.page.wait_for_timeout(400)      # the /login page renders its form after the charms
        if not self._on_signin_form():
            link = self.page.locator("[data-automation-id=signInLink]")
            if link.count():
                self.act.click(link.first)
                self.page.wait_for_timeout(800)
        if not self._on_signin_form():
            return False, "no sign-in form"
        self.act.fill(self.page.locator("[data-automation-id=email]").first, self.profile.email)
        self.act.fill(self.page.locator("[data-automation-id=password]").first, password)
        self._submit_auth("signInSubmitButton")
        return self._wait_leave_auth()

    def _create_account(self) -> tuple[bool, str]:
        if not self._on_create_form():
            self._reveal_email_form()
            link = self.page.locator("[data-automation-id=createAccountLink]")
            if link.count():
                self.act.click(link.first)
                self.page.wait_for_timeout(800)
        if not self._on_create_form():
            return False, "no create-account form"
        password = vault.get_password(ATS, self.tenant) or vault.new_account_password(self.params.get("password_rules"))
        vault.set_password(ATS, self.tenant, password)          # saved before the click: a crash never loses it
        db.upsert_account(self.conn, ATS, self.tenant, self.profile.email, note="created by smartapply")
        self.act.fill(self.page.locator("[data-automation-id=email]").first, self.profile.email)
        self.act.fill(self.page.locator("[data-automation-id=password]").first, password)
        vp = self.page.locator("[data-automation-id=verifyPassword]")
        if vp.count():
            self.act.fill(vp.first, password)
        cb = self.page.locator("[data-automation-id=createAccountCheckbox]")
        if cb.count():
            try:
                box = cb.first if (cb.first.get_attribute("type") == "checkbox") else cb.first.locator("input[type=checkbox]").first
                if not box.is_checked():
                    self.act.set_checked(box, True)
            except Exception:  # noqa: BLE001
                self.act.click(cb.first)
        self._submit_auth("createAccountSubmitButton")
        return self._wait_leave_auth()

    def _verify_by_mail(self, started: float) -> tuple[bool, str]:
        from datetime import datetime, timedelta
        since = datetime.fromtimestamp(started).astimezone() - timedelta(minutes=30)
        # A refill after a "no mail arrived in time" stop must still see the mail the first attempt triggered:
        # look back to the account's creation, however long ago that was.
        row = db.account_for(self.conn, ATS, self.tenant)
        if row is not None and row["created_at"] and not row["verified_at"]:
            try:
                created = datetime.fromisoformat(str(row["created_at"]))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                since = min(since, created.astimezone() - timedelta(minutes=5))
            except ValueError:
                pass
        m = mail.wait_for(self.cfg, sender=re.compile(r"myworkday|workday|" + re.escape(self.tenant), re.I),
                          subject=re.compile(r"verif|activat|confirm", re.I), since=since,
                          body_match=lambda x: any(re.search(r"activate|verif|confirm|token", u, re.I) for u in x.links))
        if m is None:
            return False, "no verification email arrived in time"
        link = mail.pick_link(m, re.compile(r"/activate/|verif|confirm|token", re.I))
        if not link:
            return False, f"verification mail had no usable link ({m.subject[:60]})"
        # Open the link in a second background tab of the same profile, then close it.
        from ..browser.chrome import Chrome
        chrome = Chrome(self.cfg)
        browser = self.page.context.browser
        vpage, vtid = chrome.open_background_tab(browser, link)
        try:
            vpage.wait_for_load_state("domcontentloaded", timeout=60000)
            vpage.wait_for_timeout(4000)
        finally:
            try:
                vpage.close()
            except Exception:  # noqa: BLE001
                chrome.close_tab(vtid)
        db.upsert_account(self.conn, ATS, self.tenant, self.profile.email, verified_at=_now())
        return True, ""

    def account_step(self) -> tuple[bool, str]:
        """Sign in with the vault password, else create the account (and verify by mail when the tenant asks)."""
        started = time.time()
        pw = vault.get_password(ATS, self.tenant)
        row = db.account_for(self.conn, ATS, self.tenant)
        logged_in_before = bool(row and (row["last_login_at"] or row["verified_at"]))
        if pw:
            ok, why = self._sign_in(pw)
            if ok:
                db.upsert_account(self.conn, ATS, self.tenant, self.profile.email, last_login_at=_now())
                return True, ""
            if why.startswith("VERIFY:"):
                ok2, why2 = self._verify_by_mail(started)
                if not ok2:
                    return False, "verification_pending: " + why2
                self.page.goto(self.start_url(self.app, []), wait_until="domcontentloaded", timeout=60000)
                self.page.wait_for_timeout(1500)
                ok3, why3 = self._sign_in(pw)
                return (True, "") if ok3 else (False, "login: " + why3)
            if logged_in_before or not re.search(r"invalid|incorrect|wrong (e-?mail|password|username)|not (found|recogni)|does not exist|no account", why, re.I):
                return False, f"login: sign-in with the vault password failed ({why}); fix it with `smartapply account set {self.tenant}`"
            # a vault password that never signed in: the earlier creation did not go through; create with it
            log.info("workday %s: vault password never signed in (%s); creating the account", self.tenant, why)
        ok, why = self._create_account()
        if ok:
            db.upsert_account(self.conn, ATS, self.tenant, self.profile.email, last_login_at=_now())
            return True, ""
        if why.startswith("VERIFY:"):
            ok2, why2 = self._verify_by_mail(started)
            if not ok2:
                return False, "verification_pending: " + why2
            self.page.goto(self.start_url(self.app, []), wait_until="domcontentloaded", timeout=60000)
            self.page.wait_for_timeout(1500)
            ok3, why3 = self._sign_in(vault.get_password(ATS, self.tenant) or "")
            return (True, "") if ok3 else (False, "login: " + why3)
        if re.search(r"already (exists|in use|registered)|account exists|an account with", why, re.I):
            return False, (f"login: an account for {self.profile.email} already exists on {self.tenant} but its password is not "
                           f"in the vault; run `smartapply account set {self.tenant}` and refill")
        return False, "login: " + why

    # ---------------------------------------------------------------- pages

    def page_my_information(self) -> None:
        p, a = self.profile, self.profile.address
        d = p.defaults
        country = re.sub(r"\s+of America$", "", a.country, flags=re.I)
        rules: list[tuple[re.Pattern, re.Pattern | None, Any, str]] = [
            # (wrapper-id regex, label regex, value, note)  ordered: ids first, labels as fallback
            (re.compile(r"source", re.I), re.compile(r"how did you hear", re.I), how_heard_candidates(read_json(self.app.workdir / "job.json", {}) or {}), "how heard"),
            (re.compile(r"candidateIsPreviousWorker|previousWorker", re.I), re.compile(r"previously worked|former (employee|worker)", re.I), "No" if not d.previous_employee_default else "Yes", ""),
            (re.compile(r"firstName", re.I), re.compile(r"^first name", re.I), p.first_name, ""),
            (re.compile(r"lastName", re.I), re.compile(r"^last name", re.I), p.last_name, ""),
            (re.compile(r"addressLine1|addressLine_1", re.I), re.compile(r"address line 1", re.I), a.street, ""),
            (re.compile(r"addressLine2|addressLine_2", re.I), re.compile(r"address line 2", re.I), a.street2, ""),
            (re.compile(r"city", re.I), re.compile(r"^city", re.I), a.city, ""),
            (re.compile(r"postalCode|postal", re.I), re.compile(r"postal code|zip", re.I), a.zip, ""),
            (re.compile(r"countryRegion|regionSubdivision|--region", re.I), re.compile(r"^(state|province|region)", re.I), [a.state_name, a.state], ""),
            (re.compile(r"address--country$|countryDropdown|^country$", re.I), re.compile(r"^country", re.I), [a.country, country], ""),
            (re.compile(r"phoneType|deviceType", re.I), re.compile(r"phone (device )?type", re.I), [p.phone_type, "Mobile", "Home"], ""),
            (re.compile(r"countryPhoneCode|phoneCode", re.I), re.compile(r"country phone code", re.I), [f"{a.country} ({p.phone_country_code})", a.country, country, p.phone_country_code], ""),
            (re.compile(r"phoneNumber--phoneNumber|phoneNumber$|--phone$", re.I), re.compile(r"^phone number", re.I), p.phone_digits, ""),
            (re.compile(r"emailAddress|^email$", re.I), re.compile(r"^e-?mail( address)?\*?$", re.I), p.email, ""),
        ]
        skip = re.compile(r"preferredName|extension|middleName|sms|whatsapp|optOut|alternate", re.I)
        seen: set[str] = set()
        for f in self.fields():
            fid = f["id"]
            if fid in seen or skip.search(fid):
                continue
            seen.add(fid)
            hit = next((r for r in rules if r[0].search(fid)), None) or next((r for r in rules if r[1] and r[1].search(f["label"] or "")), None)
            if hit is None:
                if f.get("required") and not f.get("value") and not f.get("checked"):
                    self.record("my_information", None, fid, None, None, "left_for_human", f"unrecognized required field: {f['label'][:60]}")
                continue
            self._fill_rule("my_information", fid, f, hit[2])

    def _fill_rule(self, page_name: str, fid: str, f: dict[str, Any], value: Any) -> None:
        w = self._wrapper(fid)
        if w is None:
            self.page.wait_for_timeout(800)          # a re-render (country, state) can drop the wrapper for a moment
            w = self._wrapper(fid)
        if w is None:
            held = f.get("value")
            if held and _same(str(held), str(value)):
                self.record(page_name, None, fid, value, held, "filled", "kept from the draft (wrapper re-rendered)")
            else:
                self.record(page_name, None, fid, value, held, "left_for_human", "wrapper vanished")
            return
        kind = self._kind(w)
        try:
            ctl = w.locator("input:not([type=hidden]), textarea, button").first
            if ctl.count() and ctl.evaluate("e => e.disabled || e.readOnly || e.getAttribute('aria-disabled') === 'true' || e.getAttribute('aria-readonly') === 'true'"):
                self.record(page_name, None, fid, value, f.get("value"), "skipped", "read-only on this form")
                return
            if ctl.count() and kind not in ("dropdown", "radio", "prompt") and not ctl.is_visible():
                # a resumed draft shows the saved legal name as text and keeps the inputs hidden: nothing to type
                held = ctl.input_value() if ctl.evaluate("e => 'value' in e") else ""
                if held and (value in (None, "") or _same(str(held), str(value))):
                    self.record(page_name, None, fid, value, held, "filled", "kept from the draft (hidden on this form)")
                else:
                    self.record(page_name, None, fid, value, held or None, "left_for_human", "hidden on this form")
                return
        except Exception:  # noqa: BLE001
            pass
        cands = value if isinstance(value, list) else [value]
        cands = [c for c in cands if c not in (None, "")]
        if not cands:
            self.record(page_name, None, fid, None, None, "skipped", "nothing to fill")
            return
        if kind == "prompt" and re.search(r"source", fid, re.I):
            # "How did you hear" already answered (a resumed draft, or picked by hand when no listed source fit):
            # keep it unless it claims a referral or an employee relationship that never happened
            try:
                held = [x.inner_text().strip() for x in w.locator("[data-automation-id=selectedItem]").all()]
            except Exception:  # noqa: BLE001
                held = []
            held = [h for h in held if h]
            if held and heard_rank(held[0]) == 0:
                self.record(page_name, None, fid, cands[0], held[0], "filled", "kept the existing answer")
                return
            # the best listed option that is not a referral (2026-10-11, Alex: an event over "Job Board" or "Other")
            obs, err = self.guarded(self._pick_source, w)
            if not err and obs:
                self.record(page_name, None, fid, obs, obs, "filled", "best non-referral source listed")
                return
            if held and heard_rank(held[0]) is not None:
                self.record(page_name, None, fid, cands[0], held[0], "filled", "kept the existing answer")
                return
        if kind == "dropdown" and re.search(r"source", fid, re.I):
            cands = [_BEST_HEARD] + cands
        if kind in ("dropdown", "radio", "prompt"):
            last_err = ""
            # "How did you hear": exact names only. A loose "Online" picked the board "Deficiente Online" (HP, 2026-10-05).
            exact = kind in ("prompt", "dropdown") and bool(re.search(r"source", fid, re.I))
            for c in cands:
                obs, err = self.guarded(self._set, w, c, kind=kind, exact=exact)
                if not err and obs:
                    self.record(page_name, None, fid, c, obs, "filled" if _same(str(obs), str(c)) or kind == "prompt" else "filled", None)
                    return
                last_err = err or "no read-back"
            self.record(page_name, None, fid, cands[0], None, "left_for_human" if f.get("required") else "skipped", last_err)
            return
        want = str(cands[0])
        obs, err = self.guarded(self._set, w, want, kind=kind)
        if err:
            self.record(page_name, None, fid, want, None, "failed" if f.get("required") else "left_for_human", err)
            return
        ok = _same(str(obs), want) or (re.search(r"phone", fid, re.I) and re.sub(r"\D", "", str(obs)).endswith(want[-10:]))
        self.record(page_name, None, fid, want, obs, "filled" if ok else "failed", None if ok else "read-back mismatch")

    # ---- My Experience
    #
    # Live structure (Stryker, 2026-09-25): the page container's direct children are `div[role=group]` sections
    # labelled by their heading (Work Experience, Education, Languages, Resume/CV, Websites, Social Network URLs).
    # Each repeating row is a `[role=group]` panel ("Work Experience 1") whose wrappers carry
    # `data-fkit-id="workExperience-<n>--<field>"`; the section's `add-button` reads Add / Add Another and every
    # panel has a trash-icon Delete button. Rows are located by that fkit prefix, never by assumed numbering.

    def _exp_page(self) -> Locator:
        return self.page.locator("[data-automation-id=applyFlowMyExpPage], [data-automation-id=myExperiencePage]").first

    def _section(self, rx: re.Pattern) -> Locator | None:
        root = self._exp_page()
        secs = root.locator(":scope > div[role=group], :scope > div > div[role=group]")
        for i in range(secs.count()):
            sec = secs.nth(i)
            try:
                title = sec.evaluate("""e => { const id = e.getAttribute('aria-labelledby'); const h = id ? document.getElementById(id) : e.querySelector('h2,h3,h4,h5');
                    return (h ? h.innerText : (e.getAttribute('aria-label') || '')).trim(); }""")
            except Exception:  # noqa: BLE001
                continue
            if rx.search(title or ""):
                return sec
        return None

    def _add_button(self, section: Locator) -> Locator | None:
        btn = section.locator("[data-automation-id=add-button], [data-automation-id=Add]")
        if btn.count() == 0:
            btn = section.locator("button").filter(has_text=re.compile(r"^\s*add( another)?\s*$", re.I))
        return btn.first if btn.count() else None

    def _prefixes(self, section: Locator) -> list[str]:
        try:
            ids = section.evaluate("e => [...e.querySelectorAll('[data-fkit-id]')].map(x => x.getAttribute('data-fkit-id'))")
        except Exception:  # noqa: BLE001
            return []
        out: list[str] = []
        for fid in ids:
            pre = str(fid).split("--")[0]
            if pre and pre not in out:
                out.append(pre)
        return out

    def _delete_existing_rows(self, section: Locator) -> int:
        """Delete every deletable row so the section is rebuilt from the profile. A tenant's default first row has
        no Delete button (Amgen's Education); it survives and is reused by `_rows_for`."""
        n = 0
        for _ in range(12):
            dels = section.locator("[role=group] button").filter(has_text=re.compile(r"^\s*delete\s*$", re.I))
            if dels.count() == 0:
                dels = section.locator("button[aria-label*=Delete i], button:has(svg.wd-icon-trash)")
            if dels.count() == 0:
                break
            try:
                self.act.click(dels.first)
                self.page.wait_for_timeout(600)
                n += 1
            except Exception as e:  # noqa: BLE001
                log.debug("delete row: %s", e)
                break
        return n

    def _rows_for(self, section: Locator, count: int, what: str) -> list[str]:
        """Prefixes for `count` rows: surviving (undeletable) rows first, then Add for the rest."""
        have = self._prefixes(section)
        out: list[str] = list(have[:count])
        for n in range(len(out) + 1, count + 1):
            prefix = self._add_row(section, f"{what}_{n}")
            if prefix is None:
                break
            out.append(prefix)
        return out

    def _add_row(self, section: Locator, what: str) -> str | None:
        """Click Add / Add Another and return the new row's fkit prefix."""
        add = self._add_button(section)
        if add is None:
            self.record("my_experience", None, what, None, None, "left_for_human", "no Add button")
            return None
        before = set(self._prefixes(section))
        (_, err) = self.guarded(self.act.click, add)
        if err:
            self.record("my_experience", None, what, None, None, "left_for_human", err)
            return None
        deadline = time.time() + 6.0
        while time.time() < deadline:
            self.page.wait_for_timeout(300)
            new = [p for p in self._prefixes(section) if p not in before]
            if new:
                return new[-1]
        self.record("my_experience", None, what, None, None, "left_for_human", "Add did not create a row")
        return None

    def _row_field(self, section: Locator, prefix: str, field: str) -> Locator | None:
        w = section.locator(f'[data-fkit-id="{prefix}--{field}"]')
        return w.first if w.count() else None

    def _fill_row(self, section: Locator, prefix: str, what: str, spec: list[tuple[str, Any]]) -> None:
        """spec: (field suffix, value | [candidates] | bool for checkboxes)."""
        for field, value in spec:
            if value is None or value == "" or value == []:
                continue
            w = self._row_field(section, prefix, field)
            if w is None:
                continue
            # a prompt list left open by the previous field covers the next one (Adobe, 2026-10-13: Field of Study's
            # popper intercepted the clicks on First/Last Year Attended, which stayed empty)
            self._close_popups()
            kind = self._kind(w)
            if kind == "checkbox":
                (obs, err) = self.guarded(self._checkbox, w, bool(value))
                self.record("my_experience", None, f"{what}.{field}", bool(value), obs, "filled" if not err else "left_for_human", err)
                continue
            cands = value if isinstance(value, list) else [value]
            last_err = None
            for n, c in enumerate(cands):
                # every candidate but the last must match exactly, so a close match on the first one cannot win over
                # an exact later one (HP: "Applied Computer Science" for "Computer Science")
                (obs, err) = self.guarded(self._set, w, c, kind=kind, exact=(kind == "prompt" and n < len(cands) - 1))
                if not err and obs and (kind != "date" or _date_matches(str(obs), str(c))):
                    self.record("my_experience", None, f"{what}.{field}", c, obs, "filled", None)
                    break
                last_err = err or (f"read-back {obs!r} is not {c!r}" if obs else "no read-back")
            else:
                self.record("my_experience", None, f"{what}.{field}", cands[0], None, "left_for_human", last_err)

    def page_my_experience(self) -> None:
        p = self.profile
        role = resolve_role_folder(self.app.role_folder)
        entries = [e for e in parse_experience_entries(role.experience_md) if not e.is_project and e.company and e.title]
        if not entries:
            entries = [ExperienceEntry(company=x.company, title=x.title, location=x.location, start=x.start, end=x.end)
                       for x in p.experiences if x.kind != "open_source"]
        # 1. resume (the attachment; "Autofill with Resume" is never used)
        if self._visible("[data-automation-id=file-upload-successful]"):
            self.record("my_experience", None, "resume", p.resume_filename, p.resume_filename, "filled", "already uploaded")
        else:
            (obs, err) = self.guarded(self._upload, None)
            self.record("my_experience", None, "resume", p.resume_filename, obs, "failed" if err or not obs else "filled", err)
        # 2. work experience: one row per entry of the role folder's experience file (matches the resume)
        sec = self._section(re.compile(r"work experience|employment", re.I))
        if sec is not None:
            removed = self._delete_existing_rows(sec)
            if removed:
                self.report.notes.append(f"removed {removed} pre-existing work experience row(s)")
            prefixes = self._rows_for(sec, len(entries), "work_experience")
            for n, (prefix, e) in enumerate(zip(prefixes, entries), 1):
                current = e.end is None
                self._fill_row(sec, prefix, f"work_experience_{n}", [
                    ("jobTitle", e.title), ("companyName", e.company), ("location", e.location),
                    ("currentlyWorkHere", current), ("startDate", e.start), ("endDate", None if current else e.end),
                    ("roleDescription", e.short),
                ])
        else:
            self.report.notes.append("no Work Experience section found")
        # 3. education
        sec = self._section(re.compile(r"education", re.I))
        if sec is not None:
            removed = self._delete_existing_rows(sec)
            if removed:
                self.report.notes.append(f"removed {removed} pre-existing education row(s)")
            prefixes = self._rows_for(sec, 1, "education")
            prefix = prefixes[0] if prefixes else None
            if prefix:
                s = p.school
                self._fill_row(sec, prefix, "education", [
                    ("schoolName", [s.name, "Other"]),
                    ("school", [s.name, "Other"]),          # P&G names the prompt `school`, not `schoolName`
                    ("degree", [s.degree_select, "Bachelor", s.degree_short, "Undergraduate"]),   # Salesforce lists BS / BA / MS
                    # HP's taxonomy has no plain "Computer Science": the federal CIP name is the honest equivalent
                    ("fieldOfStudy", [s.major, "Computer and Information Sciences", "Computer and Information Science",
                                      "Computer Science"]),
                    ("gradeAverage", s.gpa), ("gpa", s.gpa),
                    ("firstYearAttended", s.start[:4]), ("lastYearAttended", s.graduation[:4]),
                    ("startDate", s.start), ("endDate", s.graduation),
                ])
        # 4. websites: one row per URL
        sec = self._section(re.compile(r"website", re.I))
        if sec is not None:
            removed = self._delete_existing_rows(sec)
            if removed:
                self.report.notes.append(f"removed {removed} pre-existing website row(s)")
            urls = [x for x in (p.linkedin, p.github, p.portfolio) if x]
            prefixes = self._rows_for(sec, len(urls), "website")
            for n, (prefix, u) in enumerate(zip(prefixes, urls), 1):
                self._fill_row(sec, prefix, f"website_{n}", [("url", u)])
        # 5. social network URLs (plain inputs)
        sec = self._section(re.compile(r"social", re.I))
        if sec is not None:
            for fid, val in (("linkedInAccount", p.linkedin), ("githubAccount", p.github), ("portfolioAccount", p.portfolio)):
                w = sec.locator(f'[data-fkit-id$="--{fid}"]')
                if w.count() and val:
                    (obs, err) = self.guarded(self._set, w.first, val, kind="text")
                    self.record("my_experience", None, fid, val, obs, "filled" if not err and obs else "left_for_human", err)
        # 5b. languages: a tenant that renders a required default Languages row (HP, 2026-10-01) gets the profile's
        #     languages; proficiency dropdowns take the strongest option. Rows are added up to the profile's languages.
        sec = self._section(re.compile(r"language", re.I))
        if sec is not None and p.defaults.languages:
            try:
                self._fill_languages(sec, list(p.defaults.languages))
            except Exception as e:  # noqa: BLE001
                self.report.notes.append(f"languages: {type(e).__name__}: {str(e)[:120]}")
        # 5c. skills (Alex, 2026-10-13): the Skills prompt gets the skills on this role's tailored resume, exact
        #     matches on the tenant's list only, so nothing goes in that the resume does not already claim.
        sec = self._section(re.compile(r"skill", re.I))
        if sec is not None:
            try:
                self._fill_skills(sec)
            except Exception as e:  # noqa: BLE001
                self.report.notes.append(f"skills: {type(e).__name__}: {str(e)[:120]}")
        # 6. anything required and still empty is reported (languages, skills, extra attachments...)
        for f in self.fields():
            if f.get("required") and not f.get("value") and not f.get("checked") and not f.get("radios"):
                name = f["id"] or f["label"][:40]
                if any(x.name == name for x in self.report.fields) or re.search(r"file|resume|upload", name + f["label"], re.I):
                    continue
                self.record("my_experience", None, name, None, None, "left_for_human", f"required and empty: {f['label'][:60]}")

    SKILLS_WANTED = 8

    def _fill_skills(self, sec: Locator) -> None:
        """Add up to SKILLS_WANTED skills from the tailored resume's Skills section to the Skills prompt. A skill
        the tenant's list does not hold verbatim is skipped; skills already selected count toward the total."""
        w = next((sec.locator(f'[data-fkit-id="{fid}"]').first for fid in
                  sec.evaluate("e => [...e.querySelectorAll('[data-fkit-id]')].map(x => x.getAttribute('data-fkit-id'))")
                  if self._kind(sec.locator(f'[data-fkit-id="{fid}"]').first) == "prompt"), None)
        if w is None:
            return
        role = resolve_role_folder(self.app.role_folder)
        selected = w.locator("[data-automation-id=selectedItem]")
        have_names = [selected.nth(i).inner_text().strip().lower() for i in range(selected.count())]
        added: list[str] = []
        inp = w.locator("input[placeholder=Search], [data-automation-id=searchBox]").first
        opts = self.page.locator("[data-automation-id=promptOption]")
        norm = lambda x: re.sub(r"[^a-z0-9+#]+", " ", re.sub(r"\s*\([^)]*\)\s*$", "", x).lower()).strip()

        def _results() -> list[tuple[int, str]]:
            # the open result list only: selected pills are promptOptions too, inside the form field
            try:
                got = opts.evaluate_all("""es => es.map(e => (e.closest('[data-automation-id^=formField]') ||
                    e.offsetParent === null) ? '' : e.innerText.trim())""")
            except Exception:  # noqa: BLE001
                got = []
            return [(i, x) for i, x in enumerate(got) if x and not re.match(r"^no items", x, re.I)]

        # The list only refreshes on Enter, so a search read before its results arrive sees the previous skill's
        # list (Adobe, 2026-10-13: three refills picked 2 of 8 skills). Wait for the list to change.
        for skill in resume_skills(role.realistic_tex, limit=20):
            if len(have_names) + len(added) >= self.SKILLS_WANTED:
                break
            if norm(skill) in {norm(h) for h in have_names + added}:
                continue
            try:
                before = _results()
                self.act.fill(inp, skill, slow=True)
                self.act.press_search(inp)
                deadline, got = time.time() + 8.0, before
                while time.time() < deadline:
                    self.page.wait_for_timeout(400)
                    got = _results()
                    if got and got != before:
                        break
                hit = next(((i, x) for i, x in got if norm(x) == norm(skill)), None) if got != before else None
                if hit:
                    self.act.click(opts.nth(hit[0]))
                    self.page.wait_for_timeout(500)
                    added.append(hit[1])
            except Exception as e:  # noqa: BLE001
                log.debug("skill %r: %s", skill, e)
        try:
            box = w.locator("input[placeholder=Search], [data-automation-id=searchBox]").first
            if box.count() and box.input_value():
                self.act.fill(box, "")
        except Exception:  # noqa: BLE001
            pass
        self._close_popups()
        if added:
            self.record("my_experience", None, "skills", ", ".join(added), ", ".join(added), "filled", None)
        elif not have_names:
            self.report.notes.append("skills: no resume skill matched the tenant's list")

    PROFICIENCY_PREFERENCE = ["Native or Bilingual", "Native", "Mother Tongue", "Fluent", "Full Professional", "Advanced", "Expert",
                              "Professional", "Proficient", "Excellent"]

    def _fill_languages(self, sec: Locator, langs: list[str]) -> None:
        """Fill the Languages rows the tenant already shows (one per profile language, in order): the language
        dropdown, the "I am fluent" checkbox, and every proficiency dropdown of the row (Overall, Speaking, ...)."""
        rows = [pre for pre in self._prefixes(sec) if pre.lower().startswith("language")]
        # an optional, empty Languages section gets one row per profile language (Alex, 2026-10-13: Intel's was
        # left blank; he wants English, Native, on every form that asks)
        for n in range(len(rows) + 1, len(langs) + 1):
            pre = self._add_row(sec, f"language_{n}")
            if pre is None:
                break
            rows.append(pre)
        for pre, lang in zip(rows, langs):
            lw = sec.locator(f'[data-fkit-id="{pre}--language"]')
            if lw.count():
                try:
                    current = lw.first.locator("button[aria-haspopup=listbox]").first.inner_text().strip()
                except Exception:  # noqa: BLE001
                    current = ""
                if not current or re.match(r"^\s*select( one)?\s*$", current, re.I):
                    obs, err = self.guarded(self._set, lw.first, lang, kind="dropdown")
                    self.record("my_experience", None, f"{pre}--language", lang, obs,
                                "filled" if not err and obs else "left_for_human", err)
                    if err:
                        continue
            box = sec.locator(f'[data-fkit-id="{pre}--native"] input[type=checkbox]')
            if box.count():
                try:
                    if not box.first.is_checked():
                        self.act.set_checked(box.first, True)
                    self.record("my_experience", None, f"{pre}--native", "Yes", "Yes", "filled", None)
                except Exception as e:  # noqa: BLE001
                    self.record("my_experience", None, f"{pre}--native", "Yes", None, "left_for_human", str(e)[:120])
            try:
                ids = sec.evaluate(f"""e => [...e.querySelectorAll('[data-fkit-id^="{pre}--"]')].map(x => x.getAttribute('data-fkit-id'))""")
            except Exception:  # noqa: BLE001
                ids = []
            for fid in ids:
                if fid.endswith("--language") or fid.endswith("--native"):
                    continue
                w = sec.locator(f'[data-fkit-id="{fid}"]').first
                if self._kind(w) != "dropdown":
                    continue
                try:
                    current = w.locator("button[aria-haspopup=listbox]").first.inner_text().strip()
                except Exception:  # noqa: BLE001
                    current = ""
                if current and not re.match(r"^\s*select( one)?\s*$", current, re.I):
                    continue
                last_err = ""
                for want in self.PROFICIENCY_PREFERENCE:
                    obs, err = self.guarded(self._set, w, want, kind="dropdown")
                    if not err and obs:
                        self.record("my_experience", None, fid, want, obs, "filled", None)
                        break
                    last_err = err or "no read-back"
                else:
                    self.record("my_experience", None, fid, self.PROFICIENCY_PREFERENCE[0], None, "left_for_human", last_err)

    # ---- questionnaire pages

    def page_questions(self, kind: str, questions: list[Question], packet: AnswerPacket) -> None:
        page_name = f"questions_{kind}"
        prefix = f"{kind}Questionnaire--"
        qs = [q for q in questions if q.id.startswith(prefix)]
        # parents before children (children only exist after the parent's answer)
        qs.sort(key=lambda q: (1 if q.extra.get("branch_of") else 0))
        hidden: list[Question] = []
        for q in qs:
            w = self._wrapper(q.id)
            if w is None:
                hidden.append(q)
                continue
            self._question_on_page(page_name, q, packet.answers.get(q.id), w)
        # Follow-ups render only after their parent's answer, and the questionnaire does not always say which parent
        # (HP, 2026-10-05: "Are you located in US?" appeared after "existing HP employee: No"), so look again.
        for _ in range(3):
            if not hidden:
                break
            self.page.wait_for_timeout(800)
            still = [q for q in hidden if self._wrapper(q.id) is None]
            for q in [q for q in hidden if q not in still]:
                self._question_on_page(page_name, q, packet.answers.get(q.id), self._wrapper(q.id))
            if len(still) == len(hidden):
                break
            hidden = still
        for q in hidden:
            # Workday renders only the questions that apply (follow-ups of a different answer stay hidden even
            # when the questionnaire lists them at top level), so an absent control is never a gap.
            a = packet.answers.get(q.id)
            self.record(page_name, q, q.id, a.value if a else None, None, "skipped", "not shown on this page")

    def _question_on_page(self, page_name: str, q: Question, a: Answer | None, w: Locator) -> None:
        if a is None or a.needs_human or a.value in (None, "", []):
            if a is not None and a.source == "skip":
                self.record(page_name, q, q.id, None, None, "skipped", "optional, nothing to say")
            elif q.type == "file":
                self.record(page_name, q, q.id, None, None, "left_for_human" if q.required else "skipped", "attach this file yourself")
            else:
                self.record(page_name, q, q.id, None, None, "left_for_human" if q.required else "skipped", (a.needs_human if a else "no answer"))
            return
        self._fill_question(page_name, q, a, w)

    def _fill_question(self, page_name: str, q: Question, a: Answer, w: Locator) -> None:
        kind = self._kind(w)
        values = a.value if isinstance(a.value, list) else [a.value]
        want = "; ".join(str(v) for v in values)
        if q.type == "file":
            self.record(page_name, q, q.id, None, None, "left_for_human", "attach this file yourself")
            return
        if q.type == "multiselect" and kind == "prompt":
            (obs, err) = self.guarded(self._set, w, [str(v) for v in values], kind="prompt")
        elif q.type == "multiselect" and kind == "checkbox":
            done = []
            boxes = w.locator("input[type=checkbox]")
            for v in values:
                labs = [self._label_for(boxes.nth(i)) for i in range(boxes.count())]
                picked = match_option(labs, str(v))
                if picked is None:
                    continue
                (ok, err) = self.guarded(self.act.set_checked, boxes.nth(labs.index(picked)), True)
                if not err:
                    done.append(picked)
            obs, err = "; ".join(done), (None if done else "no checkbox matched")
        elif q.type == "date":
            v = str(values[0])
            (obs, err) = self.guarded(self._date, w, v[:7], day=(v[8:10] if len(v) >= 10 else ("01" if w.locator("[data-automation-id=dateSectionDay-input]").count() else None)))
            if not err and len(v) < 10 and w.locator("[data-automation-id=dateSectionDay-input]").count():
                self.report.notes.append(f"{q.label[:50]}: day set to 01 (profile knows the month only)")
        else:
            (obs, err) = self.guarded(self._set, w, str(values[0]) if q.type != "multiselect" else [str(v) for v in values], kind=kind)
        if err:
            self.record(page_name, q, q.id, want, None, "left_for_human" if q.required else "skipped", err)
            return
        ok = bool(obs) and (_same(str(obs), want) or q.type in ("date", "multiselect") or kind == "prompt")
        self.record(page_name, q, q.id, want, obs, "filled" if ok else "failed", None if ok else "read-back mismatch")

    def _label_for(self, ctl: Locator) -> str:
        try:
            cid = ctl.get_attribute("id") or ""
            lab = self.page.locator(f'label[for="{cid}"]') if cid else ctl.locator("xpath=ancestor::label[1]")
            return lab.first.inner_text().strip() if lab.count() else (ctl.get_attribute("value") or "")
        except Exception:  # noqa: BLE001
            return ""

    # ---- voluntary disclosures / self identify

    def page_voluntary_disclosures(self) -> None:
        eeo = self.profile.eeo
        rules = [
            (re.compile(r"hispanic|latino", re.I), eeo.hispanic_latino),
            (re.compile(r"transgender", re.I), eeo.transgender),
            (re.compile(r"orientation", re.I), eeo.sexual_orientation),
            (re.compile(r"gender|sex\b", re.I), eeo.gender),
            (re.compile(r"ethnic|race", re.I), eeo.ethnicity),
            (re.compile(r"veteran", re.I), eeo.veteran),
            (re.compile(r"disab", re.I), eeo.disability),
        ]
        for f in self.fields():
            fid, label = f["id"], f["label"] or ""
            w = self._wrapper(fid)
            if w is None:
                continue
            kind = self._kind(w)
            if kind == "checkbox" and re.search(r"terms|agree|acknowledg|accept", fid + " " + label, re.I):
                (obs, err) = self.guarded(self._checkbox, w, True)
                self.record("voluntary_disclosures", None, fid, "checked", "checked" if obs else "", "filled" if obs else "left_for_human", err)
                continue
            hit = next((v for rx, v in rules if rx.search(fid) or rx.search(label)), None)
            if hit is None:
                if f.get("required") and not f.get("value") and not f.get("checked"):
                    self.record("voluntary_disclosures", None, fid, None, None, "left_for_human", f"unrecognized: {label[:60]}")
                continue
            if kind in ("dropdown", "radio"):
                (obs, err) = self.guarded(self._set, w, hit, kind=kind, fallback_decline=True)
                self.record("voluntary_disclosures", None, fid, hit, obs, "filled" if obs and not err else "left_for_human", err)
            else:
                self.record("voluntary_disclosures", None, fid, hit, None, "left_for_human", f"unexpected control {kind}")

    def _checkbox_choice(self, w: Locator, wanted: str) -> str:
        """A single-answer question rendered as checkboxes: tick the matching one, untick the others."""
        boxes = w.locator("input[type=checkbox]")
        labels = []
        for i in range(boxes.count()):
            bid = boxes.nth(i).get_attribute("id") or ""
            lab = self.page.locator(f'label[for="{bid}"]') if bid else boxes.nth(i).locator("xpath=ancestor::label[1]")
            labels.append(lab.first.inner_text().strip() if lab.count() else (boxes.nth(i).get_attribute("aria-label") or ""))
        picked = match_option(labels, wanted) or _decline_like(labels)
        if picked is None:
            raise LookupError(f"no checkbox matches {wanted!r} among {labels}")
        for i, lab in enumerate(labels):
            if boxes.nth(i).is_checked() != (lab == picked):
                self.act.set_checked(boxes.nth(i), lab == picked)
        return picked if boxes.nth(labels.index(picked)).is_checked() else ""

    def page_self_identify(self) -> None:
        p = self.profile
        want_status = p.eeo.disability
        for f in self.fields():
            fid, label = f["id"], f["label"] or ""
            w = self._wrapper(fid)
            if w is None:
                continue
            kind = self._kind(w)
            if re.search(r"name", fid + " " + label, re.I) and kind == "text":
                v = p.eeo.signature_name or p.full_name
                (obs, err) = self.guarded(self._set, w, v, kind="text")
                self.record("self_identify", None, fid, v, obs, "filled" if obs and not err else "left_for_human", err)
            elif re.search(r"date", fid + " " + label, re.I) and kind in ("date", "text"):
                today = date.today()
                v = today.strftime("%Y-%m-%d") if kind == "date" else today.strftime("%m/%d/%Y")
                (obs, err) = self.guarded(self._set, w, v, kind=kind)
                self.record("self_identify", None, fid, v, obs, "filled" if obs and not err else "left_for_human", err)
            elif re.search(r"language", label, re.I) or fid == "disabilityForm":
                # the form's language picker (TEL, 2026-10-01: id "disabilityForm", already "English"): not a question
                continue
            elif kind in ("radio", "dropdown") and re.search(r"disab|status", fid + " " + label, re.I):
                wanted = "I do not want to answer" if want_status == DECLINE else want_status
                (obs, err) = self.guarded(self._set, w, wanted, kind=kind, fallback_decline=True)
                self.record("self_identify", None, fid, wanted, obs, "filled" if obs and not err else "left_for_human", err)
            elif kind == "checkbox" and re.search(r"disab|check one of the boxes", fid + " " + label, re.I) \
                    and w.locator("input[type=checkbox]").count() > 1:
                # the status as a group of checkboxes, tick exactly one (TEL). LexisNexis and S&P label the group only
                # "Please check one of the boxes below:" (2026-10-16), so the label alone does not say disability
                wanted = "I do not want to answer" if want_status == DECLINE else want_status
                (obs, err) = self.guarded(self._checkbox_choice, w, wanted)
                self.record("self_identify", None, fid, wanted, obs, "filled" if obs and not err else "left_for_human", err)
            elif kind == "checkbox" and re.search(r"terms|agree|acknowledg|accept", fid + " " + label, re.I):
                (obs, err) = self.guarded(self._checkbox, w, True)
                self.record("self_identify", None, fid, "checked", "checked" if obs else "", "filled" if obs else "left_for_human", err)

    # ---------------------------------------------------------------- flow

    def fill(self, questions: list[Question], packet: AnswerPacket) -> FillReport:
        page = self.page
        page.wait_for_timeout(1500)
        self._accept_cookies()
        stop_at = os.environ.get("SMARTAPPLY_WD_STOP_AT", "")
        visited: list[str] = []
        for _ in range(MAX_PAGES):
            self.wait_rendered()
            name = self.detect_page()
            self._dump(name)
            visited.append(name)
            if name == "account":
                ok, why = self.account_step()
                if not ok:
                    reason = "verification_pending" if why.startswith("verification_pending") else "login"
                    self.report.notes.append(why)
                    self.report.status, self.report.reason = "needs_human", reason
                    return self.report
                page.wait_for_timeout(1500)
                if self.detect_page() in ("unknown", "start"):
                    page.goto(self.start_url(self.app, questions), wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(2000)
                    self._accept_cookies()
                    if self.detect_page() == "start":
                        btn = page.locator("[data-automation-id=applyManually]")
                        if btn.count():
                            self.act.click(btn.first)
                            page.wait_for_timeout(2000)
                continue
            if name == "start":
                btn = page.locator("[data-automation-id=applyManually]")
                if btn.count():
                    self.act.click(btn.first)
                    page.wait_for_timeout(2000)
                    continue
            if name == "error":
                if "resume_draft" in visited:
                    self.report.notes.append("Workday keeps showing 'Something went wrong' after continuing the draft")
                    self.report.status, self.report.reason = "needs_human", "adapter_error"
                    return self.report
                ok, why = self._continue_from_candidate_home()
                visited.append("resume_draft")
                if not ok:
                    # No draft to continue (Walmart, 2026-10-01: "You have no applications"): the error was not about
                    # a draft, so start a fresh application from the apply page once, signed in this time.
                    if "no application" in why.lower() and "fresh_start" not in visited:
                        visited.append("fresh_start")
                        page.goto(self.start_url(self.app, questions), wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(2500)
                        self._accept_cookies()
                        if self.detect_page() == "start":
                            btn = page.locator("[data-automation-id=applyManually]")
                            if btn.count():
                                self.act.click(btn.first)
                                page.wait_for_timeout(2000)
                        if self.detect_page() != "error":
                            continue
                    self.report.notes.append(f"'Something went wrong' on the apply page and {why}")
                    self.report.status, self.report.reason = "needs_human", "adapter_error"
                    return self.report
                continue
            if name == "already_applied":
                self.report.notes.append("Workday says this job was already applied to")
                self.report.status, self.report.reason = "needs_human", "unanswered_required"
                return self.report
            if name == "review" and not [v for v in visited[:-1] if v not in ("account", "start")]:
                # a refill of an application that already reached Review: re-enter the draft from its first page
                # so every value is re-applied and verified (Workday keeps the draft; nothing is lost)
                page.goto(self.start_url(self.app, questions), wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)
                self._accept_cookies()
                visited.append("restart")
                continue
            if name == "review":
                self._persist(self.pages_done + 1)
                self.report.pages_completed = self.pages_done
                self.report.notes.append("stopped on the Review page; the footer button is Submit")
                break
            if name == "my_information":
                self.page_my_information()
            elif name == "my_experience":
                self.page_my_experience()
            elif name in QUESTION_PAGES:
                self.page_questions(QUESTION_PAGES[name], questions, packet)
            elif name == "voluntary_disclosures":
                self.page_voluntary_disclosures()
            elif name == "self_identify":
                self.page_self_identify()
            else:
                self.report.notes.append(f"unknown page ({self._step_text() or page.url[-60:]}); fields: "
                                         + ", ".join(f["id"] for f in self.fields()[:12]))
                self.report.status, self.report.reason = "needs_human", "adapter_error"
                return self.report
            if stop_at and name == stop_at:
                self.report.notes.append(f"stopped before Next on {name} (SMARTAPPLY_WD_STOP_AT)")
                self.report.status, self.report.reason = "needs_human", "adapter_error"
                return self.report
            # never leave a page with a blocking gap: Next would only show the inline errors
            advanced, errs = self._next(name)
            if not advanced:
                for e in errs:
                    self.record(name, None, "page", None, None, "left_for_human", e)
                self.report.notes.append(f"{name}: Next did not advance ({'; '.join(errs)[:200]})")
                break
            self._persist(self.pages_done + 1)
        else:
            self.report.notes.append("page limit reached")
        self.report.pages_completed = self.pages_done
        self.report.notes.append("pages: " + " > ".join(visited))
        if visited and visited[-1] == "review":
            # Workday let every page through to Review, so a "required" question that was never shown (a follow-up
            # of a different answer: TEL's military branch after "No") is not a gap
            unseen = {f.name for f in self.report.fields if f.status == "skipped" and (f.note or "").startswith("not shown")}
            packet.unanswered_required = [qid for qid in packet.unanswered_required if qid not in unseen]
        return self.finish(packet)


# ---------------------------------------------------------------- helpers

def _date_matches(observed: str, wanted: str) -> bool:
    """`observed` is what the spinbuttons hold ("2/2026", "02/15/2026", "2026"); `wanted` is YYYY-MM[-DD]. Every
    section the widget shows must hold the wanted number: a year alone is not a filled month/year date."""
    parts = [int(x) for x in re.findall(r"\d+", observed)]
    year, month = int(wanted[:4]), int(wanted[5:7]) if len(wanted) >= 7 else None
    if year not in parts:
        return False
    if month is not None and len(parts) >= 2 and month not in parts[:-1]:
        return False
    return len(parts) >= (2 if month is not None else 1)


def _same(observed: str, wanted: str) -> bool:
    n = lambda x: re.sub(r"[^a-z0-9]+", " ", x.lower()).strip()  # noqa: E731
    return n(observed) == n(wanted) or (bool(n(wanted)) and n(wanted) in n(observed))


def _decline_like(options: list[str]) -> str | None:
    for o in options:
        if re.search(r"decline|prefer not|do not wish|don't wish|wish not|choose not|rather not|not to (answer|disclose|say)|i do not want|"
                     r"not declared|undeclared|no response|not disclosed|not specified|withhold", o, re.I):
            return o
    return None


def _now() -> str:
    from ..util import read_json, now_iso
    return now_iso()


def _exact_in(labels: list[str], wanted: str) -> str | None:
    """The label equal to `wanted` ignoring case and punctuation; never a label that merely contains it."""
    w = re.sub(r"[^a-z0-9]+", " ", str(wanted).lower()).strip()
    return next((x for x in labels if re.sub(r"[^a-z0-9]+", " ", x.lower()).strip() == w), None)
