"""Filling one application: prefetch questions, draft answers, open a background tab, arm the guard, run the
adapter, record every field, hand off. Also the phase-0 dry run that proves the guard on a live page."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Any

from playwright.sync_api import Browser, Page, sync_playwright

from . import db, tracker
from .adapters import for_ats
from .answers import AnswerPacket, build_packet
from .browser.actions import Actions, Refused
from .browser.chrome import Chrome, foreground_window_handle
from .browser.guard import SUBMIT_TEXT, Guard
from .config import Config
from .log import get_logger
from .models import Application, AppState, FillReport, Question
from .prefetch import prefetch_questions
from .profile import Profile
from .util import read_json, write_json

log = get_logger("filler")


# ---------------------------------------------------------------- stages

def stage_prefetch(cfg: Config, conn, app: Application) -> list[Question] | None:
    from .prefetch.workday import PostingGone
    try:
        qs = prefetch_questions(app)
    except PostingGone as e:
        tracker.fail(conn, app.id, "posting_gone", {"stage": "prefetch", "error": str(e)[:300]})
        raise
    except Exception as e:  # noqa: BLE001
        tracker.needs_human(conn, app.id, "adapter_error", {"stage": "prefetch", "error": str(e)[:300]})
        raise
    if qs is not None:
        write_json(app.questions_path, [q.to_dict() for q in qs])
    # a prefetcher may have discovered parameters (Greenhouse board token for an embedded posting)
    db.update_application(conn, app.id, ats_params=app.ats_params, tenant=app.tenant)
    tracker.transition(conn, app.id, AppState.PREFETCHED, "prefetched",
                       detail={"questions": len(qs) if qs is not None else None})
    return qs


def load_questions(app: Application) -> list[Question]:
    return [Question.from_dict(d) for d in (read_json(app.questions_path, []) or [])]


def stage_draft(cfg: Config, conn, app: Application, profile: Profile, questions: list[Question], *,
                use_claude: bool = True) -> AnswerPacket:
    packet = build_packet(cfg, conn, app, profile, questions, use_claude=use_claude)
    tracker.transition(conn, app.id, AppState.DRAFTED, "drafted",
                       detail={"answers": len(packet.answers), "unanswered_required": packet.unanswered_required,
                               "claude_cost_usd": packet.claude_cost_usd})
    return packet


def _same_form(current: str, wanted: str) -> bool:
    """Greenhouse redirects boards.greenhouse.io to job-boards.greenhouse.io: the same form, not a reason to reload."""
    def norm(u: str) -> str:
        return re.sub(r"^https?://(www\.|job-)?", "", u or "").split("?")[0][:40]
    return norm(current).startswith(norm(wanted)[:30])


def _prenavigate(chrome: Chrome, app: Application, url: str) -> None:
    """Load the form into a reused tab before Playwright attaches (see Chrome.navigate_raw)."""
    t = chrome.tab(app.target_id) if app.target_id else None
    if t and not _same_form(t.get("url", ""), url):
        try:
            chrome.navigate_raw(app.target_id, url)
        except Exception as e:  # noqa: BLE001
            log.info("app %s: raw navigation failed (%s); Playwright navigates instead", app.id, e)


def _open_or_reuse(chrome: Chrome, browser: Browser, app: Application, url: str,
                   before_nav: Callable[[Page], None] | None = None) -> tuple[Page, str]:
    """Reuse the application's tab when it still exists in a window we may use; otherwise a new background tab.
    `before_nav` runs on a reused tab before it is navigated (the guard is armed there, see stage_fill)."""
    if app.target_id and chrome.tab(app.target_id):
        page = chrome.page_for_target(browser, app.target_id)
        if page is not None and chrome.ensure_parked(browser, app.target_id):
            if not _same_form(page.url, url):
                if before_nav is not None:
                    before_nav(page)
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            return page, app.target_id
        log.info("app %s: its window is minimized (Alex's); opening a fresh parked window", app.id)
    return chrome.open_background_tab(browser, url)


def _fill_external(cfg: Config, conn, app: Application, profile: Profile, chrome: Chrome, adapter_cls,
                   questions: list[Question], packet: AnswerPacket) -> FillReport:
    """The Claude fallback agent: open the tab, disconnect, let the agent's own MCP server own the tab (and arm the
    guard there), then re-attach to read back and make sure nothing is left armed."""
    from .browser.guard import Guard as _Guard
    url = adapter_cls.start_url(app, questions)
    with sync_playwright() as pw:
        browser = chrome.attach(pw)
        page, tid = _open_or_reuse(chrome, browser, app, url)
        db.update_application(conn, app.id, target_id=tid, tab_url=url)
        page.wait_for_load_state("domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        browser.close()   # the sync API cannot serve routes while blocked in subprocess; the server arms its own
    try:
        report, _res = adapter_cls.run(cfg, conn, app, profile, tid, questions, packet)
    except Exception as e:  # noqa: BLE001
        log.exception("agent adapter crashed on app %s", app.id)
        report = FillReport(status="needs_human", reason="adapter_error", notes=[f"{type(e).__name__}: {str(e)[:200]}"])
    with sync_playwright() as pw:
        browser = chrome.attach(pw)
        page = chrome.page_for_target(browser, tid)
        if page is None:
            report.notes.append("WARNING: the application tab is gone")
            if report.status == "ready_for_review":
                report.status, report.reason = "needs_human", "adapter_error"
        else:
            g = _Guard(page, app.ats)
            if g.dom_armed():          # the server was killed before its disarm; clean up now instead of waiting the TTL
                g.disarm()
                report.notes.append("guard left armed by the agent server; disarmed on read-back")
            db.update_application(conn, app.id, tab_url=page.url)
            _label_tab(page, tab_label(app, report))
            _present(chrome, browser, tid, app.id)
        browser.close()
    return report


def stage_fill(cfg: Config, conn, app: Application, profile: Profile, chrome: Chrome, questions: list[Question],
               packet: AnswerPacket) -> FillReport:
    adapter_cls = for_ats(app.ats)
    if adapter_cls is None:
        tracker.needs_human(conn, app.id, "unknown_site", {"ats": app.ats})
        return FillReport(status="needs_human", reason="unknown_site")
    tracker.transition(conn, app.id, AppState.FILLING, "filling")
    chrome.ensure()
    report: FillReport
    if getattr(adapter_cls, "external", False):
        report = _fill_external(cfg, conn, app, profile, chrome, adapter_cls, questions, packet)
        return _finish_fill(conn, app, report)
    _prenavigate(chrome, app, adapter_cls.start_url(app, questions))
    with sync_playwright() as pw:
        browser = chrome.attach(pw)
        url = adapter_cls.start_url(app, questions)   # hosted form vs embedded posting
        # A reused tab is armed before it navigates: registering the guard's routes on the same connection right
        # after a navigation that loads reCAPTCHA iframes never returns (Stripe and Pinterest refills, 2026-10-06:
        # Page.route hung for 13 minutes). Routes set before the navigation carry over and arm every new frame.
        armed: dict[str, Guard] = {}

        def _arm_first(p: Page) -> None:
            armed["g"] = Guard(p, app.ats, ttl_ms=int(cfg.guard.ttl_ms))
            armed["g"].arm()

        page, tid = _open_or_reuse(chrome, browser, app, url, before_nav=_arm_first)
        chrome.activate_tab(browser, tid)    # active tab of a parked window: full frame rate (see chrome.py)
        db.update_application(conn, app.id, target_id=tid, tab_url=url)
        page.wait_for_load_state("domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        guard = armed.get("g")
        if guard is None or guard.page is not page:
            guard = Guard(page, app.ats, ttl_ms=int(cfg.guard.ttl_ms))
            guard.arm()
        else:
            guard._inject_now()          # the DOM blocker on the new document (the init script covers it too)
        adapter = adapter_cls(cfg, conn, app, profile, page,
                              Actions(page, ats=app.ats, guard=guard, review_reached=None, upload_dir=app.workdir))
        adapter.act.review_reached = adapter.review_reached
        try:
            report = adapter.fill(questions, packet)
        except Refused as e:
            report = FillReport(status="needs_human", reason="adapter_error", notes=[f"refused: {e}"])
        except Exception as e:  # noqa: BLE001
            log.exception("adapter crashed on app %s", app.id)
            report = FillReport(status="needs_human", reason="adapter_error", notes=[f"{type(e).__name__}: {str(e)[:200]}"])
        report.notes.append(f"guard aborted {len(guard.aborted)} request(s)")
        clean = guard.disarm()
        if not clean:
            report.notes.append("WARNING: guard did not disarm cleanly")
        db.update_application(conn, app.id, tab_url=page.url)
        _label_tab(page, tab_label(app, report))
        _present(chrome, browser, tid, app.id)
        browser.close()   # disconnect only; the tab stays for Alex
    return _finish_fill(conn, app, report)


REASON_LABELS = {"captcha": "solve the CAPTCHA", "login": "sign in", "verification_pending": "verify the email",
                 "unknown_site": "apply by hand", "adapter_error": "check the form"}


def tab_label(app: Application, report: FillReport) -> str:
    """The tab title Alex sorts his windows by (Alex, 2026-10-15: label every tab when a fill ends)."""
    title = re.sub(r"\s*[-(,].*$", "", app.title or "")[:40]
    name = f"{app.company} {title}".strip()
    if report.status == "ready_for_review":
        return f"[SUBMIT] {name}"
    if report.status == "needs_human":
        left = [f.label or f.name for f in report.left_for_human if (f.label or f.name) and (f.label or f.name) != "page"]
        what = ", ".join(x[:40] for x in left[:2]) if left else REASON_LABELS.get(report.reason or "", "finish the form")
        return f"[YOU] {name}: {what}"
    return f"[FAILED] {name}"


def _label_tab(page: Page, label: str) -> None:
    """Set the title and keep it: single-page ATSs rewrite document.title on every step."""
    try:
        page.evaluate("""l => { document.title = l; window.__saWant = l; if (!window.__saLabel) {
            window.__saLabel = setInterval(() => { if (document.title !== window.__saWant) document.title = window.__saWant; }, 1500); } }""",
                      label)
    except Exception as e:  # noqa: BLE001
        log.debug("tab label failed: %s", e)


def _present(chrome: Chrome, browser: Browser, tid: str, app_id: int) -> None:
    """The fill is over: the window goes on-screen behind everything (never activated), so its taskbar button works."""
    try:
        chrome.present(browser, tid, slot=app_id)
    except Exception as e:  # noqa: BLE001
        log.debug("present failed for app %s: %s", app_id, e)


def _finish_fill(conn, app: Application, report: FillReport) -> FillReport:
    for f in report.fields:
        db.record_field(conn, app.id, page=f.page, name=f.name, label=f.label, intended=f.intended, observed=f.observed,
                        status=f.status, note=f.note)
    write_json(app.fill_report_path, report.to_dict())
    if report.status == "ready_for_review":
        tracker.transition(conn, app.id, AppState.READY_FOR_REVIEW, "ready_for_review",
                           detail={"fields": len(report.fields), "notes": report.notes[:5]})
    else:
        tracker.needs_human(conn, app.id, report.reason or "adapter_error", {"notes": report.notes[:5]})
    return report


def fill_application(cfg: Config, conn, app: Application, profile: Profile, chrome: Chrome, *,
                     use_claude: bool = True, redo_answers: bool = False) -> FillReport:
    """The whole flow for one application. Idempotent: reuses questions.json/answers.json when present."""
    app = tracker.load(conn, app.id)
    if app.state in (AppState.QUEUED, AppState.FAILED) or not app.questions_path.exists():
        if app.state == AppState.FAILED:
            tracker.transition(conn, app.id, AppState.QUEUED, "retry")
        questions = stage_prefetch(cfg, conn, app)
        if questions is None:
            if for_ats(app.ats) is None:
                tracker.needs_human(conn, app.id, "unknown_site", {"ats": app.ats, "note": "no prefetcher, no adapter"})
                return FillReport(status="needs_human", reason="unknown_site")
            questions = []          # no prefetcher: the browser agent reads the page itself
            write_json(app.questions_path, [])
    else:
        questions = load_questions(app)
        if app.state == AppState.NEEDS_HUMAN:
            tracker.transition(conn, app.id, AppState.PREFETCHED, "retry")
    app = tracker.load(conn, app.id)
    # Always rebuild the answers: profile rules are free and earlier Claude answers are reused inside build_packet,
    # so a refill costs nothing extra, and a profile change always reaches the form (2026-10-01: a refill of a
    # Review-stage application reused its old answers.json and kept "prefer not to disclose" after Alex answered).
    packet = stage_draft(cfg, conn, app, profile, questions, use_claude=use_claude)
    app = tracker.load(conn, app.id)
    return stage_fill(cfg, conn, app, profile, chrome, questions, packet)


# ---------------------------------------------------------------- dry run (phase 0 gate)

@dataclass
class DryRunReport:
    ok: bool
    target_id: str | None = None
    url: str = ""
    focus_stolen: bool = False
    uploaded: str | None = None
    tool_refused: bool = False
    tool_reason: str = ""
    network_aborted: bool = False
    dom_blocked: int = 0
    disarmed_clean: bool = False
    tab_alive_after: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def dry_run(cfg: Config, conn, app: Application, profile: Profile, *, keep_tab: bool = True) -> DryRunReport:
    chrome = Chrome(cfg)
    chrome.ensure()
    rep = DryRunReport(ok=False, url=app.url)
    fg_before = foreground_window_handle()
    tracker.note(conn, app.id, "dry_run_start")
    with sync_playwright() as pw:
        browser: Browser = chrome.attach(pw)
        page, tid = chrome.open_background_tab(browser, app.url)
        rep.target_id = tid
        db.update_application(conn, app.id, target_id=tid, tab_url=app.url)
        fg_after = foreground_window_handle()
        rep.focus_stolen = bool(fg_before and fg_after and fg_before != fg_after)
        if rep.focus_stolen:
            rep.notes.append(f"foreground window changed {fg_before} -> {fg_after}")
        page.wait_for_load_state("domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        guard = Guard(page, app.ats, ttl_ms=int(cfg.guard.ttl_ms))
        guard.arm()
        actions = Actions(page, ats=app.ats, guard=guard, upload_dir=app.workdir)

        resume = app.workdir / profile.resume_filename
        file_inputs = page.locator("input[type=file]")
        if resume.exists() and file_inputs.count() > 0:
            try:
                rep.uploaded = actions.upload(file_inputs, resume, nth=0)
                rep.notes.append(f"uploaded {rep.uploaded} into file input #0 of {file_inputs.count()}")
            except (Refused, Exception) as e:  # noqa: BLE001
                rep.notes.append(f"upload skipped: {e}")

        submit_btn = page.locator("button, input[type=submit]").filter(has_text=SUBMIT_TEXT)
        if submit_btn.count() == 0:
            submit_btn = page.locator("button[type=submit], input[type=submit]")
        if submit_btn.count() > 0:
            try:
                actions.click(submit_btn, nth=0)
                rep.notes.append("TOOL LAYER FAILED: click on submit button went through")
            except Refused as e:
                rep.tool_refused = True
                rep.tool_reason = str(e)
        else:
            rep.notes.append("no submit-like button found on this page (multi-step form?)")

        try:
            res = page.evaluate("async (u) => { try { const r = await fetch(u, {method:'POST', body:'sa=dryrun'}); return 'status ' + r.status; } catch (e) { return 'error ' + e.message; } }", page.url)
            rep.network_aborted = str(res).startswith("error") or bool(guard.aborted)
            rep.notes.append(f"fetch POST {page.url[:80]} -> {res}; aborted list={len(guard.aborted)}")
        except Exception as e:  # noqa: BLE001
            rep.notes.append(f"network probe error: {e}")

        try:
            page.evaluate("""() => {
                const re = %s;
                const btn = [...document.querySelectorAll('button, input[type=submit], [role=button]')]
                    .find(b => re.test(((b.innerText || b.value || b.getAttribute('aria-label') || '') + '').trim()));
                if (btn) btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                const f = document.querySelector('form');
                if (f) f.dispatchEvent(new Event('submit', {cancelable: true, bubbles: true}));
            }""" % ("/" + SUBMIT_TEXT.pattern.replace("/", r"\/") + "/i"))
            time.sleep(0.3)
            rep.dom_blocked = guard.dom_blocked_count()
        except Exception as e:  # noqa: BLE001
            rep.notes.append(f"dom probe error: {e}")

        rep.disarmed_clean = guard.disarm()
        if not keep_tab:
            page.close()
        browser.close()
    rep.tab_alive_after = chrome.tab(tid) is not None if keep_tab else True
    rep.ok = (not rep.focus_stolen) and rep.tool_refused and rep.network_aborted and rep.dom_blocked > 0 \
        and rep.disarmed_clean and rep.tab_alive_after
    write_json(app.workdir / "dry_run.json", rep.to_dict())
    tracker.note(conn, app.id, "dry_run_done", rep.to_dict())
    return rep
