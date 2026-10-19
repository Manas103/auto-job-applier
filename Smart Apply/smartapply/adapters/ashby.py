"""Ashby application form (jobs.ashbyhq.com/{org}/{id}/application): React, no <form> element.

Measured live 2026-09-24 (Northwood Space, Ramp): every question sits in `div[data-field-path="<path>"]` with a
`label[for=<path>]`; text-like fields are `input#<path>` / `textarea#<path>`; Boolean questions are two
`button.ashby-application-form-input-yesno-option[data-option=yes|no]` (typeless, so they are submit buttons in
a form, but there is no form); ValueSelect renders a radio group (`input[type=radio]` + `label[for]`), Location
is an autocomplete (`input[role=combobox]`, results `.ashby-application-form-input-autocomplete-popup-result`),
File is an `input[type=file]` inside the container (the page's first file input is the "Autofill from resume"
pane and is never touched), Date is a react-datepicker text input. The submit is
`button.ashby-application-form-submit-button`; the network guard aborts `op=ApiSubmit*`.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from playwright.sync_api import Locator

from ..answers import MOOT_IDS, AnswerPacket, match_option
from ..util import write_json
from ..models import Answer, FillReport, Question
from .base import Adapter, log


class AshbyAdapter(Adapter):
    ats = "ashby"

    @classmethod
    def start_url(cls, app, questions: list[Question]) -> str:
        for q in questions:
            if q.extra.get("hosted_url"):
                return str(q.extra["hosted_url"])
        u = app.url.split("?")[0].rstrip("/")
        return u if u.endswith("/application") else u + "/application"

    # ---------------------------------------------------------------- helpers

    def _container(self, q: Question) -> Locator | None:
        p = q.id.replace("\\", "\\\\").replace('"', '\\"')
        c = self.page.locator(f'[data-field-path="{p}"]')
        if c.count():
            return c.first
        if q.extra.get("kind") == "TextingConsent":
            c = self.page.locator(".ashby-application-form-field-entry", has_text=re.compile("text message", re.I))
            return c.first if c.count() else None
        return None

    def _text_input(self, c: Locator) -> Locator:
        return c.locator("input:not([type=file]):not([type=radio]):not([type=checkbox]), textarea").first

    def _yesno(self, c: Locator, wanted: str, resave: bool = False) -> str:
        """The buttons toggle: clicking a pressed one clears it (Voleon refill, 2026-10-10: every Yes/No a first
        fill had set came back empty). A pressed button is left alone, or clicked off and on when `resave` needs a
        change so Ashby saves it again."""
        opt = "yes" if str(wanted).strip().lower().startswith("y") else "no"
        btn = c.locator(f"button[data-option={opt}]")
        if btn.count() == 0:
            raise LookupError("no yes/no buttons in this field")
        if btn.first.get_attribute("aria-pressed") == "true":
            if not resave:
                return "Yes" if opt == "yes" else "No"
            self.act.click(btn.first)
            self.page.wait_for_timeout(500)
        self.act.click(btn.first)
        self.page.wait_for_timeout(120)
        pressed = btn.first.get_attribute("aria-pressed")
        return ("Yes" if opt == "yes" else "No") if pressed == "true" else ""

    def _radio(self, c: Locator, wanted: str, options: list[str], resave: bool = False) -> str:
        picked = match_option(options, wanted) or wanted
        labels = c.locator("label[for]").filter(has_text=re.compile("^\\s*" + re.escape(picked) + "\\s*$"))
        if labels.count() == 0:
            labels = c.locator("label", has_text=picked)
        if labels.count() == 0 and c.locator("input[role=combobox]").count():
            return self._combo_select(c, picked)
        if labels.count() == 0:
            raise LookupError(f"no option {picked!r}")
        lab = labels.first
        for_id = lab.get_attribute("for")
        # a checked option clicked again clears on Ashby (Whatnot refill, 2026-10-10: four answers came back empty)
        pre = self.page.locator(f'[id="{for_id}"]') if for_id else None
        if pre is not None and pre.count() and pre.first.is_checked():
            if resave:   # off and on again, so Ashby stores it again
                self.act.click(lab)
                self.page.wait_for_timeout(500)
                self.act.click(lab)
                self.page.wait_for_timeout(120)
        else:
            self.act.click(lab)
            self.page.wait_for_timeout(120)
        if for_id:
            inp = self.page.locator(f'[id="{for_id}"]')
            if inp.count() and inp.first.is_checked():
                return picked
        checked = c.locator("input:checked")
        return picked if checked.count() else ""

    def _checkboxes(self, c: Locator, wanted: list[str], options: list[str]) -> str:
        done = []
        for w in wanted:
            picked = match_option(options, w) or w
            labels = c.locator("label[for]").filter(has_text=re.compile("^\\s*" + re.escape(picked) + "\\s*$"))
            if labels.count() == 0:
                raise LookupError(f"no option {picked!r}")
            lab = labels.first
            for_id = lab.get_attribute("for") or ""
            inp = self.page.locator(f'[id="{for_id}"]') if for_id else lab.locator("input")
            if inp.count() and inp.first.is_checked():
                done.append(picked)
                continue
            self.act.click(lab)
            self.page.wait_for_timeout(100)
            if inp.count() and inp.first.is_checked():
                done.append(picked)
        return "; ".join(done)

    def _combo_select(self, c: Locator, picked: str) -> str:
        """A long ValueSelect renders as a searchable combobox, not radios (Sierra's 102 universities, 2026-10-06):
        type the option and click the exact match in the popup."""
        inp = c.locator("input[role=combobox]").first
        self.act.fill(inp, picked, slow=True)
        results = self.page.locator("[role=option]")
        deadline = time.time() + 5.0
        while time.time() < deadline:
            self.page.wait_for_timeout(300)
            exact = results.filter(has_text=re.compile("^\\s*" + re.escape(picked) + "\\s*$"))
            if exact.count():
                self.act.click(exact.first)
                self.page.wait_for_timeout(300)
                return picked if picked in (inp.input_value() or "") or c.inner_text().find(picked) >= 0 else ""
        raise LookupError(f"no option {picked!r} in the searchable list")

    def _location(self, c: Locator, wanted: str) -> str:
        inp = c.locator("input[role=combobox], input").first
        self.act.fill(inp, wanted, slow=True)     # typed: the autocomplete queries on key events
        results = self.page.locator(".ashby-application-form-input-autocomplete-popup-result, [role=option]")
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
        if picked is None:
            raise LookupError(f"no location suggestion for {wanted!r} (got {labels[:5]})")
        target = results.filter(has_text=re.compile("^" + re.escape(picked) + "$")).first
        if target.count() == 0:
            target = results.nth(labels.index(picked))
        self.act.click(target)
        self.page.wait_for_timeout(200)
        return inp.input_value() or picked

    def _upload(self, c: Locator, q: Question, path: Path | None = None) -> str:
        path = path or self.resume_path
        inp = c.locator("input[type=file]")
        if inp.count() == 0:
            raise LookupError("no file input in this field")
        self.act.upload(inp, path)
        deadline = time.time() + 8.0
        while time.time() < deadline:
            self.page.wait_for_timeout(400)
            txt = c.inner_text()
            if path.name.lower() in txt.lower() or path.stem.lower() in txt.lower():
                return path.name
        return ""

    # ---------------------------------------------------------------- fill

    def fill(self, questions: list[Question], packet: AnswerPacket) -> FillReport:
        page = self.page
        page.locator("[data-field-path]").first.wait_for(state="attached", timeout=30000)
        self._server_forms: list[dict] = []
        page.on("response", self._on_response)
        for q in questions:
            if q.type == "hidden" or q.id in MOOT_IDS:
                continue
            a = packet.answers.get(q.id)
            if a is None or a.needs_human or a.value in (None, "", []):
                if q.type == "file" and q.id != "_systemfield_resume":
                    self.record("application", q, q.id, None, None, "skipped" if not q.required else "left_for_human",
                                "attach this file yourself")
                elif a is not None and a.source == "skip":
                    self.record("application", q, q.id, None, None, "skipped", "optional, nothing to say")
                else:
                    self.record("application", q, q.id, None, None, "left_for_human" if q.required else "skipped",
                                (a.needs_human if a else "no answer"))
                continue
            self._fill_one(q, a)
        self._sweep(questions)
        rq = next((x for x in questions if x.id == "_systemfield_resume"), None)
        c = self._container(rq) if rq is not None else None
        if c is not None:
            self.recheck_upload("_systemfield_resume", lambda: self.resume_path.name.lower() in c.inner_text().lower(),
                                lambda: self._upload(c, rq))
        self._verify_server(questions, packet)
        return self.finish(packet)

    # ---------------------------------------------------------------- server copy
    #
    # Ashby saves every field to its server as it changes (op=ApiSetFormValue; each response carries the whole form
    # with every field's stored value) and Submit validates that copy, not the page. Harvey, 2026-10-05: four fields
    # showed their values on the page (React held them too) but the server copy did not have them, so Submit said
    # "Missing entry for required field"; a date typed key by key was stored as "2002-05-01" from a half-typed value.

    def _on_response(self, r) -> None:
        if "op=ApiSetFormValue" not in r.url:
            return
        try:
            fr = (r.json().get("data") or {}).get("setFormValue")
        except Exception:  # noqa: BLE001
            return
        if fr:
            self._server_forms.append(fr)

    def _verify_server(self, questions: list[Question], packet: AnswerPacket) -> None:
        if not self._server_forms:
            self.report.notes.append("no Ashby save responses seen: the server copy was not checked")
            return
        try:   # kept for diagnosis: which forms the page saved, and what each one stored
            write_json(self.app.workdir / "ashby_server_forms.json", self._server_forms[-6:])
        except Exception:  # noqa: BLE001
            pass
        self.page.wait_for_timeout(1500)
        by = {q.id: q for q in questions}
        fixed, failed = [], []
        for f in list(self.report.fields):
            q, a = by.get(f.name), packet.answers.get(f.name)
            if f.status != "filled" or q is None or a is None or q.type == "file":
                continue
            state = self._server_state(f.name)
            if f.name not in state or server_matches(q, a.value, state[f.name]):
                continue
            self.guarded(self._resync, q, a)
            self.page.wait_for_timeout(1500)
            got = self._server_state(f.name).get(f.name)
            if server_matches(q, a.value, got):
                f.note = "re-entered: Ashby's server copy had not saved it"
                fixed.append(f.label)
            else:
                f.status, f.note = "failed", f"Ashby's server copy is {str(got)[:60]!r}, not the filled value"
                failed.append(f.label)
        if fixed or failed:
            self.report.notes.append(f"server copy check: re-entered {fixed[:5]}, still wrong {failed[:5]}")

    def _server_state(self, path: str) -> dict:
        """The latest saved copy of the form that holds `path`. A page can save more than one form (Sierra,
        2026-10-06: an events survey saved last, and reading the application's fields from it failed all 15).
        Some boards answer each save with only the field just saved and every other value null (Sierra), others
        with the whole form (Harvey), so values are merged over every response of that form, newest non-empty
        value winning; a field the server never stored stays empty either way."""
        merged: dict = {}
        form_id = None
        for fr in reversed(self._server_forms):
            state = server_values(fr)
            if path in state:
                form_id = fr.get("id")
                break
        if form_id is None and not any(path in server_values(fr) for fr in self._server_forms):
            return {}
        for fr in self._server_forms:
            if form_id is not None and fr.get("id") != form_id:
                continue
            for k, v in server_values(fr).items():
                if v not in (None, "", []) or k not in merged:
                    merged[k] = v
        return merged

    def _resync(self, q: Question, a: Answer) -> None:
        """Enter a field again so it changes (an unchanged value saves nothing)."""
        c = self._container(q)
        if c is None:
            raise LookupError("field not found")
        values = [str(v) for v in (a.value if isinstance(a.value, list) else [a.value])]
        kind = q.extra.get("kind", "String")
        if q.type == "multiselect":
            for w in values:
                picked = match_option(q.options, w) or w
                lab = c.locator("label[for]").filter(has_text=re.compile("^\\s*" + re.escape(picked) + "\\s*$")).first
                inp = self.page.locator(f'[id="{lab.get_attribute("for")}"]')
                if inp.is_checked():
                    self.act.click(lab)
                    self.page.wait_for_timeout(500)
                self.act.click(lab)
                self.page.wait_for_timeout(500)
        elif q.type == "select" and kind not in ("Boolean", "TextingConsent"):
            self._radio(c, values[0], q.options, resave=True)
        elif kind in ("Boolean", "TextingConsent"):
            self._yesno(c, values[0], resave=True)
        else:
            inp = self._text_input(c)
            self.act.fill(inp, "")
            self.page.wait_for_timeout(500)
            self.act.fill(inp, _mdy(values[0]) if kind == "Date" else values[0])
            if kind == "Date":
                self.act.press_safe(inp, "Escape")

    def _fill_one(self, q: Question, a: Answer) -> None:
        c = self._container(q)
        if c is None:
            if q.extra.get("dom_optional"):
                self.record("application", q, q.id, a.value, None, "skipped", "not on this form")
            else:
                self.record("application", q, q.id, a.value, None, "failed" if q.required else "skipped", "field not found")
            return
        kind = q.extra.get("kind", "String")
        values = a.value if isinstance(a.value, list) else [a.value]
        want = "; ".join(str(v) for v in values)
        if q.type == "file":
            if a.value == "__resume__" or q.id == "_systemfield_resume":
                (obs, err) = self.guarded(self._upload, c, q)
                want = self.profile.resume_filename
            elif a.value == "__transcript__":
                path = self.transcript_path()
                (obs, err) = self.guarded(self._upload, c, q, path)
                want = path.name
            else:
                self.record("application", q, q.id, None, None, "left_for_human", "attach this file yourself")
                return
        elif kind in ("Boolean", "TextingConsent") or (q.type == "select" and c.locator("button[data-option]").count()):
            (obs, err) = self.guarded(self._yesno, c, str(values[0]))
        elif q.type == "multiselect":
            (obs, err) = self.guarded(self._checkboxes, c, [str(v) for v in values], q.options)
        elif q.type == "select":
            (obs, err) = self.guarded(self._radio, c, str(values[0]), q.options)
        elif kind == "Location":
            (obs, err) = self.guarded(self._location, c, str(values[0]))
            if err:
                self.record("application", q, q.id, want, None, "left_for_human", err + "; type the city and pick the suggestion")
                return
        else:
            if kind == "Phone" or re.fullmatch(r"\s*phone( number)?\s*", q.label, re.I):
                want = re.sub(r"\D", "", want)
            if kind == "Date":
                want = _mdy(want)
            (obs, err) = self.guarded(self.act.fill, self._text_input(c), want)
            if kind == "Date" and not err:
                self.guarded(self.act.press_safe, self._text_input(c), "Escape")
        if err:
            self.record("application", q, q.id, want, None, "failed" if q.required else "left_for_human", err)
            return
        ok = bool(obs) and (_same(str(obs), want) or (kind == "Phone" and re.sub(r"\D", "", str(obs)).endswith(want[-10:])))
        self.record("application", q, q.id, want, obs, "filled" if ok else "failed", None if ok else "read-back mismatch")

    def _sweep(self, questions: list[Question]) -> None:
        """Report yes/no groups and required text fields the GraphQL form did not list (texting consent etc.)."""
        known = {q.id for q in questions}
        recorded = {f.name for f in self.report.fields}
        try:
            entries = self.page.evaluate("""() => [...document.querySelectorAll('[data-field-path]')].map(e => {
                const lab = e.querySelector('label'); const yn = e.querySelector('button[data-option]');
                const pressed = !![...e.querySelectorAll('button[data-option]')].find(b => b.getAttribute('aria-pressed') === 'true');
                const inp = e.querySelector('input:not([type=file]):not([type=radio]):not([type=checkbox]), textarea');
                const req = !!(inp && inp.required) || !!(lab && /required/.test(lab.className));
                return {path: e.getAttribute('data-field-path'), label: lab ? lab.innerText.trim().slice(0, 120) : '',
                        yesno: !!yn, pressed, value: inp ? inp.value : '', required: req,
                        checked: !!e.querySelector('input:checked')};
            })""")
        except Exception as e:  # noqa: BLE001
            log.debug("ashby sweep failed: %s", e)
            return
        for e in entries:
            path = e.get("path") or ""
            if path in known or path in recorded:
                continue
            empty = (e.get("yesno") and not e.get("pressed")) or (not e.get("yesno") and not e.get("value") and not e.get("checked"))
            if empty and (e.get("required") or e.get("yesno")):
                q = Question(id=path, label=e.get("label") or path, type="select" if e.get("yesno") else "text", required=bool(e.get("required")))
                self.record("application", q, path, None, None, "left_for_human" if e.get("required") else "skipped",
                            "on the form but not in the prefetch")


def _same(observed: str, wanted: str) -> bool:
    n = lambda x: re.sub(r"[^a-z0-9]+", " ", x.lower()).strip()  # noqa: E731
    return n(observed) == n(wanted) or (n(wanted) and n(wanted) in n(observed))


def _mdy(v: str) -> str:
    """"2028-05" / "2028-05-01" -> "05/01/2028", the format the react-datepicker parses as a local date."""
    m = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", str(v).strip())
    return f"{m.group(2)}/{m.group(3) or '01'}/{m.group(1)}" if m else str(v)


def _key(v) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(v).lower())   # dash and quote encodings differ between the API and the page


def server_values(form_render: dict) -> dict:
    """path -> stored value, from an ApiSetFormValue response's FormRender."""
    out: dict = {}

    def walk(n):
        if isinstance(n, dict):
            if n.get("__typename") == "FormFieldEntry":
                field = n.get("field") or {}
                path = field.get("path")
                fv = n.get("fieldValue")
                if path:
                    val = None if fv is None else (fv.get("value") if "value" in fv else fv.get("filename"))
                    # a ValueSelect stores the picked option's id, not its label (Voleon, 2026-10-10)
                    labels = {o.get("value"): o.get("label") for o in field.get("selectableValues") or []
                              if isinstance(o, dict)}
                    if labels and val is not None:
                        val = [labels.get(v, v) for v in val] if isinstance(val, list) else labels.get(val, val)
                    out[path] = val
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(form_render)
    return out


def server_matches(q: Question, intended, stored) -> bool:
    if stored in (None, "", [], {}):
        return False
    kind = q.extra.get("kind", "String")
    if kind == "Date":
        return str(stored).startswith(str(intended)[:7])
    if isinstance(stored, bool):
        return stored == str(intended).strip().lower().startswith("y")
    if isinstance(stored, dict):
        return bool(stored.get("text") or stored)
    if isinstance(stored, list):
        want = intended if isinstance(intended, list) else [intended]
        return {_key(x) for x in stored} == {_key(x) for x in want}
    if kind == "Phone" or re.fullmatch(r"\s*phone( number)?\s*", q.label, re.I):
        return re.sub(r"\D", "", str(stored)).endswith(re.sub(r"\D", "", str(intended))[-10:])
    return _key(stored) == _key(intended)
