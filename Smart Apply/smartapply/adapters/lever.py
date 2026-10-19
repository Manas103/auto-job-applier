"""Lever apply page: one server-rendered `form#application-form`.

Measured 2026-09-24 (Palantir capture, Xcimer live): resume `#resume-upload-input` (hidden file input; the parser
then fills untouched fields, so it goes first); `name`, `email`, `phone`, `org`, `urls[...]` are plain inputs; the
location box `#location-input` fills a hidden `#selected-location` only when a suggestion under `.dropdown-results`
is clicked; custom questions are native `<select>`, radio, checkbox, text and textarea controls named
`cards[uuid][fieldN]`; EEO is `select[name="eeo[...]"]` plus a typed name and date for the disability form.
Pressing Enter in any text field submits the form, so nothing here ever sends a key.
"""

from __future__ import annotations

import re
import time

from playwright.sync_api import Locator

from ..answers import MOOT_IDS, AnswerPacket, match_option
from ..models import Answer, FillReport, Question
from .base import Adapter, log


class LeverAdapter(Adapter):
    ats = "lever"

    @classmethod
    def start_url(cls, app, questions: list[Question]) -> str:
        for q in questions:
            if q.extra.get("hosted_url"):
                return str(q.extra["hosted_url"])
        u = app.url.rstrip("/")
        return u if u.endswith("/apply") else u + "/apply"

    # ---------------------------------------------------------------- helpers

    def _by_name(self, name: str) -> Locator:
        n = name.replace("\\", "\\\\").replace('"', '\\"')
        return self.page.locator(f'form#application-form [name="{n}"]')

    def _upload_resume(self) -> str:
        inp = self.page.locator("#resume-upload-input")
        if inp.count() == 0:
            inp = self.page.locator('form#application-form input[type=file][name="resume"]')
        self.act.upload(inp, self.resume_path)
        deadline = time.time() + 8.0
        while time.time() < deadline:
            self.page.wait_for_timeout(400)
            ok = self.page.locator(".resume-upload-success").first
            try:
                if ok.count() and ok.is_visible():
                    break
            except Exception:  # noqa: BLE001
                pass
        name = self.page.locator(".resume-upload-label .filename, .filename").first
        try:
            txt = name.inner_text().strip() if name.count() else ""
        except Exception:  # noqa: BLE001
            txt = ""
        return txt or self.resume_path.name

    def _location(self, wanted: str) -> str:
        inp = self.page.locator("#location-input")
        # Lever's widget listens for key events, so the text is typed, never set (and never followed by Enter).
        self.act.fill(inp, wanted, slow=True)
        results = self.page.locator(".dropdown-results > *")
        deadline = time.time() + 5.0
        labels: list[str] = []
        while time.time() < deadline:
            self.page.wait_for_timeout(300)
            try:
                labels = [results.nth(i).inner_text().strip() for i in range(min(results.count(), 20))]
            except Exception:  # noqa: BLE001
                labels = []
            labels = [x for x in labels if x]
            if labels:
                break
        picked = match_option(labels, wanted) if labels else None
        if picked is None and labels:
            picked = next((x for x in labels if wanted.split(",")[0].lower() in x.lower()), None)
        if picked is None:
            raise LookupError(f"no location suggestion for {wanted!r} (got {labels[:5]})")
        target = results.filter(has_text=re.compile("^" + re.escape(picked) + "$")).first
        if target.count() == 0:
            target = results.nth(labels.index(picked))
        self.act.click(target)
        self.page.wait_for_timeout(200)
        hidden = self.page.locator("#selected-location")
        sel = hidden.input_value() if hidden.count() else ""
        if not sel:
            raise LookupError("location suggestion clicked but #selected-location stayed empty")
        return inp.input_value() or picked

    def _select(self, ctl: Locator, wanted: str) -> str:
        labels = ctl.evaluate("e => [...e.options].map(o => o.text.trim())")
        picked = match_option([x for x in labels if x and not re.match(r"^(select|choose|click here)", x, re.I)], wanted)
        if picked is None:
            raise LookupError(f"no option matches {wanted!r} among {labels[:8]}")
        self.act.select_option(ctl, picked)
        return ctl.evaluate("e => e.options[e.selectedIndex] ? e.options[e.selectedIndex].text.trim() : ''")

    def _radio(self, name: str, wanted: str, options: list[str]) -> str:
        picked = match_option(options, wanted) or wanted
        n = name.replace('"', '\\"')
        radio = self.page.locator(f'form#application-form input[type=radio][name="{n}"][value="{picked.replace(chr(34), "")}"]')
        if radio.count() == 0:
            lab = self.page.locator(f'form#application-form label:has(input[type=radio][name="{n}"])').filter(
                has_text=re.compile("^\\s*" + re.escape(picked) + "\\s*$"))
            if lab.count() == 0:
                raise LookupError(f"no radio {picked!r} for {name}")
            self.act.click(lab.first)
            radio = lab.first.locator("input[type=radio]")
        else:
            try:
                self.act.set_checked(radio, True)
            except Exception:  # noqa: BLE001
                self.act.click(radio.first.locator("xpath=ancestor::label[1]"))
        return picked if radio.first.is_checked() else ""

    def _checkboxes(self, name: str, wanted: list[str], options: list[str]) -> str:
        done = []
        n = name.replace('"', '\\"')
        for w in wanted:
            picked = match_option(options, w) or w
            box = self.page.locator(f'form#application-form input[type=checkbox][name="{n}"][value="{picked.replace(chr(34), "")}"]')
            if box.count() == 0:
                raise LookupError(f"no checkbox {picked!r} for {name}")
            try:
                self.act.set_checked(box, True)
            except Exception:  # noqa: BLE001
                self.act.click(box.first.locator("xpath=ancestor::label[1]"))
            if box.first.is_checked():
                done.append(picked)
        return "; ".join(done)

    # ---------------------------------------------------------------- fill

    def fill(self, questions: list[Question], packet: AnswerPacket) -> FillReport:
        page = self.page
        page.locator("form#application-form").wait_for(state="attached", timeout=30000)
        by_id = {q.id: q for q in questions}
        # 1. resume first: Lever's parser only fills untouched fields
        rq = by_id.get("resume")
        if rq is not None:
            (obs, err) = self.guarded(self._upload_resume)
            self.record("application", rq, "resume", self.profile.resume_filename, obs, "failed" if err else "filled", err)
            page.wait_for_timeout(1500)
        for q in questions:
            if q.id == "resume" or q.type == "hidden" or q.id in MOOT_IDS:
                continue
            a = packet.answers.get(q.id)
            if a is None or a.needs_human or a.value in (None, "", []):
                if q.type == "file":
                    self.record("application", q, q.id, None, None, "left_for_human", "attach this file yourself")
                elif a is not None and a.source == "skip":
                    self.record("application", q, q.id, None, None, "skipped", "optional, nothing to say")
                else:
                    self.record("application", q, q.id, None, None, "left_for_human" if q.required else "skipped",
                                (a.needs_human if a else "no answer"))
                continue
            self._fill_one(q, a)
        return self.finish(packet)

    def _fill_one(self, q: Question, a: Answer) -> None:
        widget = q.extra.get("widget", "text")
        if q.type == "file":
            self.record("application", q, q.id, None, None, "left_for_human", "attach this file yourself")
            return
        if widget == "location" or q.id == "location":
            (obs, err) = self.guarded(self._location, str(a.value))
            self.record("application", q, q.id, a.value, obs, "left_for_human" if err else "filled",
                        (err + "; type the city and pick the suggestion") if err else None)
            return
        ctl = self._by_name(q.id)
        if ctl.count() == 0:
            if q.extra.get("dom_optional"):
                self.record("application", q, q.id, a.value, None, "skipped", "not on this form")
            else:
                self.record("application", q, q.id, a.value, None, "failed" if q.required else "skipped", "control not found")
            return
        values = a.value if isinstance(a.value, list) else [a.value]
        if q.type == "multiselect" or widget == "checkbox":
            (obs, err) = self.guarded(self._checkboxes, q.id, [str(v) for v in values], q.options)
            want = "; ".join(str(v) for v in values)
        elif widget == "radio":
            (obs, err) = self.guarded(self._radio, q.id, str(values[0]), q.options)
            want = str(values[0])
        elif widget == "select" or q.type == "select":
            (obs, err) = self.guarded(self._select, ctl.first, str(values[0]))
            want = str(values[0])
        else:
            want = re.sub(r"\D", "", str(a.value)) if q.id == "phone" else str(a.value)
            (obs, err) = self.guarded(self.act.fill, ctl.first, want)
        if err:
            self.record("application", q, q.id, want, None, "failed" if q.required else "left_for_human", err)
            return
        ok = bool(obs) and (_same(str(obs), want) or (q.id == "phone" and re.sub(r"\D", "", str(obs)).endswith(want[-10:])))
        self.record("application", q, q.id, want, obs, "filled" if ok else "failed", None if ok else "read-back mismatch")


def _same(observed: str, wanted: str) -> bool:
    n = lambda x: re.sub(r"[^a-z0-9]+", " ", x.lower()).strip()  # noqa: E731
    return n(observed) == n(wanted) or (n(wanted) and n(wanted) in n(observed))
