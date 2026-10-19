"""Greenhouse hosted application form (job-boards.greenhouse.io/{board}/jobs/{id}).

Measured on live forms 2026-09-24 (Advanced Space, Hudl; API survey of 12 boards): element ids equal the API
field names; selects are react-select inputs (`input.select__input[role=combobox]`) that open on a real click into
`#react-select-<id>-listbox` with options `#react-select-<id>-option-N`; the chosen value reads back from
`.select__single-value`; School and Location are async selects (options appear only after typing); the resume
input `#resume` is replaced by a filename chip after upload; the only submit is `button[type=submit]`
"Submit application". Demographic control ids are bare numbers, so every lookup uses `[id="..."]`.

What varies between boards and is handled generically: which education controls exist, whether
`hispanic_ethnicity`/`race` are present (race is conditional), custom file questions (transcripts: left to Alex),
required consent checkboxes (left to Alex), and the location autocomplete.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from playwright.sync_api import Locator, Page

from ..answers import MOOT_IDS, AnswerPacket, deterministic_answers, match_option
from ..models import Answer, FillReport, Question
from .base import Adapter, log

# Controls the API may not list but the form may show; answered from the profile when found (DOM sweep).
SWEEP_EEOC = {"gender", "hispanic_ethnicity", "race", "veteran_status", "disability_status"}
SWEEP_STANDARD = {"country", "candidate-location"}   # the phone widget's country select is not an API question
SWEEP_EDU = {"school--0", "degree--0", "discipline--0", "start-month--0", "start-year--0", "end-month--0", "end-year--0"}
_NOISE_OPTIONS = re.compile(r"^(loading|no options|type to search|start typing|no results)", re.I)


class GreenhouseAdapter(Adapter):
    ats = "greenhouse"

    @classmethod
    def start_url(cls, app, questions: list[Question]) -> str:
        params = dict(getattr(app, "ats_params", None) or {})
        if params.get("board") and params.get("job_id") and "greenhouse.io" not in str(app.url):
            # career-site wrapper: the hosted board would redirect back to it, and its cross-origin form iframe made
            # arming the guard hang (Zipline, 2026-10-01); the embed page is the same form as a top-level page
            return embed_url(str(params["board"]), str(params["job_id"]), eu=bool(params.get("eu")))
        for q in questions:
            if q.extra.get("hosted_url"):
                return str(q.extra["hosted_url"])
        # A career site that only wraps the board (Epic Games, Zipline: `?gh_jid=` pages) may sit behind a Cloudflare
        # check and never embeds the form; the hosted board carries the same ids the prefetcher listed, so go there.
        # The hosted board redirects back to the career site on custom-domain boards (Zipline, Epic Games), whose page
        # carries the form in an iframe (`#grnhse_iframe`); the embed URL serves that same form as a top-level page.
        params = dict(getattr(app, "ats_params", None) or {})
        if params.get("board") and params.get("job_id") and "greenhouse.io" not in str(app.url):
            return embed_url(str(params["board"]), str(params["job_id"]), eu=bool(params.get("eu")))
        return app.url

    # ---------------------------------------------------------------- widgets

    def _control(self, q: Question) -> Locator | None:
        if q.extra.get("by_label"):
            lab = self.page.locator("form label", has_text=q.label[:60])
            if lab.count() == 0:
                return None
            for_id = lab.first.get_attribute("for")
            return _by_id(self.page, for_id) if for_id else None
        loc = _by_id(self.page, q.id)
        if loc.count() == 0 and q.id in ("location", "candidate-location"):
            # the API names the question "location"; the form's react-select is "candidate-location"
            loc = _by_id(self.page, "candidate-location" if q.id == "location" else "location")
        return loc if loc.count() else None

    def _listbox_labels(self, listbox: Locator) -> tuple[Locator, list[str]]:
        options = listbox.locator("[role=option]")
        labels = [options.nth(i).inner_text().strip() for i in range(min(options.count(), 400))]
        labels = [x for x in labels if not _NOISE_OPTIONS.match(x)]
        return options, labels

    def _checkbox_group(self, fieldset: Locator, wanted: list[str]) -> str:
        """Tick the boxes whose labels match the wanted options (exact option names from the prefetch), untick the
        rest, and read back the checked labels."""
        boxes = fieldset.locator("input[type=checkbox]")
        labels: list[str] = []
        for i in range(boxes.count()):
            bid = boxes.nth(i).get_attribute("id") or ""
            lab = self.page.locator(f'label[for="{bid}"]') if bid else boxes.nth(i).locator("xpath=ancestor::label[1]")
            if bid and not lab.count():  # a wrapping label instead of label[for]
                lab = boxes.nth(i).locator("xpath=ancestor::label[1]")
            labels.append(lab.first.inner_text().strip() if lab.count() else (boxes.nth(i).get_attribute("value") or ""))
        picks = []
        for w in wanted:
            hit = match_option(labels, w)
            if hit is None:
                raise LookupError(f"no checkbox matches {w!r} among {labels[:8]}")
            picks.append(hit)
        for i, lab in enumerate(labels):
            box = boxes.nth(i)
            want_on = lab in picks
            if box.is_checked() != want_on:
                self.act.set_checked(box, want_on)
        self.page.wait_for_timeout(100)
        return "; ".join(lab for i, lab in enumerate(labels) if boxes.nth(i).is_checked())

    def _react_select(self, q: Question, control: Locator, wanted: str) -> str:
        """Open, pick the exact option, read back the displayed value. Async lists (school, location) are searched
        by typing; the list is re-read until options arrive or 4 s pass."""
        control.scroll_into_view_if_needed()
        self.act.click(control)
        control_id = control.get_attribute("id") or q.id     # demographic ids differ from the question id
        listbox = _by_id(self.page, f"react-select-{control_id}-listbox")
        try:
            listbox.wait_for(state="visible", timeout=4000)
        except Exception:  # noqa: BLE001
            self.act.click(control)
            listbox.wait_for(state="visible", timeout=4000)
        options, labels = self._listbox_labels(listbox)
        picked = match_option(labels, wanted) if labels else None
        # searchable and async lists (school, location, country): type, then wait for the results. A list that only
        # searches shows nothing for the whole wanted text when its option is shorter (Epic Games, 2026-10-10:
        # "Bachelor's Degree" listed no options), so for the degree the first word is typed next and matched too. Only
        # the degree: a stem of a school or city name ("University", "New") would match the wrong entry.
        stem = (re.sub(r"'s$", "", wanted.split()[0]) if q.id.startswith("degree") and len(wanted.split()) > 1 else None)
        for typed in [wanted[:60]] + ([stem] if stem else []):
            if picked is not None:
                break
            control.fill(typed)
            deadline = time.time() + 4.0
            while time.time() < deadline:
                self.page.wait_for_timeout(300)
                if listbox.count() == 0:
                    listbox = _by_id(self.page, f"react-select-{control_id}-listbox")
                options, labels = self._listbox_labels(listbox)
                picked = (match_option(labels, wanted) or (match_option(labels, stem) if typed == stem else None)) if labels else None
                if picked is not None:
                    break
        if picked is None:
            self.page.keyboard.press("Escape")
            raise LookupError(f"no option matches {wanted!r} among {labels[:8]}")
        self._picks = [*getattr(self, "_picks", []), picked]
        target = options.filter(has_text=re.compile("^" + re.escape(picked) + "$")).first
        if target.count() == 0:
            target = options.nth(labels.index(picked))
        self.act.click(target)
        self.page.wait_for_timeout(150)
        observed = self._react_value(control)
        # The country select shows only the flag and dial code ("+1") for "United States +1": a partial echo of
        # the picked label counts as that label.
        if observed and observed != picked and observed in picked:
            return picked
        return observed

    def _react_value(self, control: Locator) -> str:
        ctl = control.locator("xpath=ancestor::div[contains(@class,'select__control')][1]")
        single = ctl.locator(".select__single-value")
        if single.count():
            return single.first.inner_text().strip()
        multi = ctl.locator(".select__multi-value__label")
        if multi.count():
            return "; ".join(multi.nth(i).inner_text().strip() for i in range(multi.count()))
        return ""

    def _upload_resume(self, q: Question) -> tuple[str, str]:
        inp = self.page.locator("#resume")
        if inp.count() == 0:
            chip = self._resume_chip()
            if chip:
                return chip, "already attached"
            raise LookupError("no #resume input and no attached file")
        self.act.upload(inp, self.resume_path)
        self.page.wait_for_timeout(800)
        chip = self._resume_chip()
        return chip, ""

    def _attached_name(self, qid: str) -> str:
        """The filename shown in an upload group (`aria-labelledby=upload-label-<id>`); Greenhouse drops the file
        input once a file is attached (Stripe transcript, 2026-10-06: attached, then reported "file field not found")."""
        grp = self.page.locator(f"[aria-labelledby='upload-label-{qid}']")
        if grp.count() and grp.first.locator("button[aria-label='Remove file']").count():
            m = re.search(r"([\w.\-]+\.(pdf|docx?|txt|rtf))", grp.first.inner_text(), re.I)
            if m:
                return m.group(1)
        return ""

    def _upload_file(self, q: Question, path: Path) -> str:
        control = self._control(q)
        if control is None:
            shown = self._attached_name(q.id)
            if shown:
                return shown
            raise LookupError("file field not found")
        inp = control if (control.first.get_attribute("type") or "").lower() == "file" else control.locator("input[type=file]")
        if inp.count() == 0:
            raise LookupError("no file input in this field")
        return self.act.upload(inp, path)

    def _resume_chip(self) -> str:
        # The resume field group: the label "Resume/CV" followed by the attached filename and a Remove button.
        grp = self.page.locator("form").locator("xpath=.//*[contains(text(),'Resume')]/ancestor::div[.//button[@aria-label='Remove file']][1]")
        if grp.count():
            txt = grp.first.inner_text()
            m = re.search(r"([\w.\-]+\.(pdf|docx?|txt|rtf))", txt, re.I)
            if m:
                return m.group(1)
        return ""

    # ---------------------------------------------------------------- fill

    def fill(self, questions: list[Question], packet: AnswerPacket) -> FillReport:
        page = self.page
        form = page.locator("form#application-form")
        try:
            form.wait_for(state="attached", timeout=12000)
        except Exception:  # noqa: BLE001
            if "error=true" in page.url or re.search(r"/jobs?/?(\?|$)", page.url.split("greenhouse.io", 1)[-1] or ""):
                # Greenhouse redirects a closed posting to the board's job list with ?error=true (Hudl, 2026-10-01)
                self.report.notes.append(f"the posting is closed: Greenhouse redirected to {page.url[:90]}")
                self.report.status, self.report.reason = "needs_human", "adapter_error"
                return self.report
            if _security_check(page):
                self.report.notes.append(f"a security check (Cloudflare) blocks {page.url[:80]}; pass it in the tab, then refill")
                self.report.status, self.report.reason = "needs_human", "captcha"
                return self.report
            # A career site that only links to Greenhouse (Epic Games, 2026-10-01: "Apply for this position" buttons,
            # no embedded form) is left for the hosted board, which carries the same ids the prefetcher listed.
            params = dict(getattr(self.app, "ats_params", None) or {})
            if params.get("board") and params.get("job_id") and "greenhouse.io" not in page.url:
                target = embed_url(str(params["board"]), str(params["job_id"]), eu=bool(params.get("eu")))
                self.report.notes.append(f"no form on {page.url[:80]}; using the Greenhouse embed page {target}")
                page.goto(target, wait_until="domcontentloaded", timeout=60000)
            form.wait_for(state="attached", timeout=30000)
        for q in questions:
            # A REQUIRED cover letter is not moot (Varda, 2026-10-16: labeled ready with the required upload empty).
            # Alex has no cover letter; he wants the transcript there. Greenhouse takes the file or the text box.
            if q.id == "cover_letter" and q.required:
                if self.profile.transcript_path and Path(self.profile.transcript_path).exists():
                    self._fill_one(q, Answer(q.id, "__transcript__", source="profile"))
                else:
                    self.record("application", q, q.id, None, None, "left_for_human", "required cover letter: attach a file")
                continue
            if q.type == "hidden" or q.id in MOOT_IDS:
                continue
            a = packet.answers.get(q.id)
            if a is None or a.needs_human or a.value in (None, "", []):
                if q.type == "file":
                    self.record("application", q, q.id, None, None, "left_for_human", "attach this file yourself")
                    continue
                if a is not None and a.source == "skip":
                    self.record("application", q, q.id, None, None, "skipped", "optional, nothing to say")
                    continue
                self.record("application", q, q.id, None, None, "left_for_human" if q.required else "skipped",
                            (a.needs_human if a else "no answer"))
                continue
            self._fill_one(q, a)
        # race renders only after "Hispanic/Latino?" is answered, so the first pass finds no control and records it as
        # not on the form (2026-10-16: every Greenhouse fill that day left race empty, Verkada's required one included)
        retry = [f for f in self.report.fields if f.note == "not on this form" and f.name in SWEEP_EEOC]
        if retry:
            page.wait_for_timeout(500)
        for fo in retry:
            q = next((x for x in questions if x.id == fo.name), None)
            if q is not None and self._control(q) is not None:
                self.report.fields.remove(fo)
                self._fill_one(q, packet.answers[q.id])
        self._sweep(questions, packet)
        rq = next((x for x in questions if x.id == "resume"), None)
        if rq is not None:
            self.recheck_upload("resume", lambda: bool(self._resume_chip()), lambda: self._upload_resume(rq)[0])
        return self.finish(packet)

    def _fill_one(self, q: Question, a: Answer) -> None:
        if q.type == "file":
            if q.id == "resume" or a.value == "__resume__":
                (res, err) = self.guarded(self._upload_resume, q)
                if err:
                    self.record("application", q, q.id, self.profile.resume_filename, None, "failed", err)
                else:
                    chip, note = res
                    ok = chip.lower() == self.profile.resume_filename.lower() or bool(chip)
                    self.record("application", q, q.id, self.profile.resume_filename, chip, "filled" if ok else "failed", note or None)
            elif a.value == "__transcript__":
                (name, err) = self.guarded(self._upload_file, q, self.transcript_path())
                self.record("application", q, q.id, Path(self.profile.transcript_path).name, name or None,
                            "failed" if err or not name else "filled", err)
            else:
                self.record("application", q, q.id, None, None, "left_for_human", "attach this file yourself")
            return
        control = self._control(q)
        if control is None:
            if q.extra.get("dom_optional"):
                self.record("application", q, q.id, a.value, None, "skipped", "not on this form")
            else:
                self.record("application", q, q.id, a.value, None, "failed" if q.required else "skipped", "control not found")
            return
        # A multi-select with few options renders as a fieldset of checkboxes (Rocket Lab, 2026-10-01), not a react-select.
        if q.type in ("select", "multiselect") and control.first.locator("input[type=checkbox]").count():
            values = [str(v) for v in (a.value if isinstance(a.value, list) else [a.value])]
            (obs, err) = self.guarded(self._checkbox_group, control.first, values)
            want = "; ".join(values)
            if err:
                self.record("application", q, q.id, want, obs or None, "failed" if q.required else "left_for_human", err)
            else:
                self.record("application", q, q.id, want, obs, "filled" if obs and _same(obs, want) else "failed")
            return
        # The API calls Location "input_text", but the form renders a react-select autocomplete; trust the DOM.
        is_combobox = q.type in ("select", "multiselect") or (control.first.get_attribute("role") == "combobox")
        if is_combobox:
            values = a.value if isinstance(a.value, list) else [a.value]
            observed_parts = []
            err = None
            self._picks = []
            for v in values:
                (obs, err) = self.guarded(self._react_select, q, control, str(v))
                if err:
                    break
                observed_parts.append(str(obs))
            observed = "; ".join(observed_parts)
            if err:
                self.record("application", q, q.id, a.value, observed or None, "failed" if q.required else "left_for_human", err)
            else:
                want = "; ".join(str(v) for v in values)
                # the read-back may show the option chosen for the answer rather than its wording ("Bachelors" for
                # "Bachelor's Degree", Epic Games 2026-10-10): what counts is that the page holds what was picked
                ok = bool(observed) and (_same(observed, want) or _same(observed, "; ".join(self._picks)))
                self.record("application", q, q.id, want, observed, "filled" if ok else "failed")
            return
        if q.type == "number":
            (obs, err) = self.guarded(self.act.fill, control, str(a.value))
        elif q.id == "phone":
            (obs, err) = self.guarded(self.act.fill, control, re.sub(r"\D", "", str(a.value)))
        else:
            (obs, err) = self.guarded(self.act.fill, control, str(a.value))
        if err:
            self.record("application", q, q.id, a.value, None, "failed" if q.required else "left_for_human", err)
        else:
            want = re.sub(r"\D", "", str(a.value)) if q.id == "phone" else str(a.value)
            ok = (obs or "").strip() == want.strip() or (q.id == "phone" and re.sub(r"\D", "", obs or "").endswith(want[-10:]))
            self.record("application", q, q.id, want, obs, "filled" if ok else "failed", None if ok else "read-back mismatch")

    # ---------------------------------------------------------------- DOM sweep

    _SWEEP_JS = """
    () => {
      const out = [];
      const form = document.querySelector('form#application-form') || document;
      const labelFor = (el) => { const l = el.id ? form.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null;
                                 return l ? l.innerText.replace(/\\s+/g, ' ').trim() : ''; };
      for (const el of form.querySelectorAll('input, textarea')) {
        const type = (el.getAttribute('type') || 'text').toLowerCase();
        if (type === 'hidden' || type === 'search') continue;
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        const helper = el.getAttribute('aria-hidden') === 'true' || el.getAttribute('tabindex') === '-1' || cs.opacity === '0';
        const visible = (r.width > 0 && r.height > 0) && cs.visibility !== 'hidden' && !(helper && !el.getAttribute('role'));
        const combobox = el.getAttribute('role') === 'combobox';
        let value = el.value || '';
        if (combobox) {
          const ctl = el.closest('.select__control');
          const sv = ctl && ctl.querySelector('.select__single-value, .select__multi-value__label');
          value = sv ? sv.innerText.trim() : '';
        }
        out.push({id: el.id || '', type: combobox ? 'combobox' : type, tag: el.tagName.toLowerCase(),
                  required: el.getAttribute('aria-required') === 'true' || el.required,
                  value, checked: !!el.checked, visible, label: labelFor(el) || (el.closest('label') ? el.closest('label').innerText.trim() : ''),
                  name: el.getAttribute('name') || ''});
      }
      return out;
    }
    """

    def _sweep(self, questions: list[Question], packet: AnswerPacket) -> None:
        """Fill known EEO/education controls the API did not list, and report every visible required control that
        is still empty so nothing on the form is a surprise at review time."""
        try:
            controls = self.page.evaluate(self._SWEEP_JS)
        except Exception as e:  # noqa: BLE001
            log.debug("sweep failed: %s", e)
            return
        known = {q.id for q in questions}
        if "location" in known:
            known.add("candidate-location")
        recorded = {f.name for f in self.report.fields}
        for c in controls:
            cid = c.get("id") or ""
            if not c.get("visible") or cid in known or not cid:
                continue
            if cid in SWEEP_EEOC or cid in SWEEP_EDU or cid in SWEEP_STANDARD:
                section = "eeoc" if cid in SWEEP_EEOC else ("education" if cid in SWEEP_EDU else "application")
                qtype = "select" if c["type"] == "combobox" else ("number" if c["type"] == "number" else "text")
                q = Question(id=cid, label=c.get("label") or cid, type=qtype, required=bool(c.get("required")),
                             section=section, extra={"dom_optional": True})
                ans = deterministic_answers([q], self.profile, {}).get(cid)
                if ans is None or ans.value in (None, "", []):
                    continue
                if c.get("value"):
                    continue
                packet.answers[cid] = ans
                self._fill_one(q, ans)
        # second pass: anything visible, required and still empty that nobody recorded
        try:
            controls = self.page.evaluate(self._SWEEP_JS)
        except Exception:  # noqa: BLE001
            return
        recorded = {f.name for f in self.report.fields}
        # a checkbox group (one name, many options) is answered once any option is ticked; its other options are not
        # separate required boxes (DV Trading's "Undergrad Discipline(s)" reported 82 of them, 2026-10-01)
        checked_groups = {c.get("name") for c in controls if c["type"] == "checkbox" and c.get("checked") and c.get("name")}
        for c in controls:
            cid = c.get("id") or ""
            if not c.get("visible") or not c.get("required"):
                continue
            if c["type"] == "checkbox":
                name = c.get("name") or ""
                if name and (name in checked_groups or name in recorded):
                    continue
                if not c.get("checked") and cid not in recorded and (cid or c.get("name")):
                    q = Question(id=cid or c.get("name", ""), label=c.get("label") or "consent checkbox", type="checkbox", required=True)
                    self.record("application", q, q.id, None, None, "left_for_human", "required checkbox: tick it yourself")
                continue
            if c.get("value"):
                continue
            if cid in recorded or cid in ("resume", "cover_letter") or cid in MOOT_IDS or not (cid or c.get("name")):
                continue
            if cid == "candidate-location" and "location" in recorded:
                continue
            q = Question(id=cid or c.get("name", "?"), label=c.get("label") or cid, type=c["type"], required=True)
            self.record("application", q, q.id, None, None, "left_for_human", "required on the form, no answer available")


def embed_url(board: str, job_id: str, *, eu: bool = False) -> str:
    host = "boards.eu.greenhouse.io" if eu else "boards.greenhouse.io"
    return f"https://{host}/embed/job_app?for={board}&token={job_id}"


def _security_check(page: Page) -> bool:
    """Cloudflare's interstitial ("Just a moment...", "complete a security check") in place of the site."""
    try:
        title = page.title() or ""
        body = page.locator("body").inner_text(timeout=2000)[:600]
    except Exception:  # noqa: BLE001
        return False
    return bool(re.search(r"just a moment|security check|verify you are human|checking your browser", title + " " + body, re.I))


def _by_id(page: Page, element_id: str | None) -> Locator:
    """Attribute selector, because Greenhouse ids can start with a digit (demographic questions), which `#id` cannot express."""
    eid = (element_id or "").replace("\\", "\\\\").replace('"', '\\"')
    return page.locator(f'[id="{eid}"]')


def _same(observed: str, wanted: str) -> bool:
    n = lambda x: re.sub(r"[^a-z0-9]+", " ", x.lower()).strip()  # noqa: E731
    return n(observed) == n(wanted) or n(wanted) in n(observed)
