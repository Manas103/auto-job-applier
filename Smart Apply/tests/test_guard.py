"""The submit guard against a real Chrome and a counting fixture server. These are the tests that matter most:
if any of them fails, Smart Apply could submit an application."""

from __future__ import annotations

import time

import pytest
from playwright.sync_api import sync_playwright

from smartapply.browser.actions import Actions, Refused
from smartapply.browser.chrome import foreground_window_handle
from smartapply.browser.guard import Guard, looks_like_submit

pytestmark = pytest.mark.usefixtures("chrome")


def _review_reached(page) -> bool:
    try:
        return "Review" in page.locator("[data-automation-id=progressBarActiveStep]").inner_text(timeout=500)
    except Exception:  # noqa: BLE001
        return False


def test_submit_text_patterns():
    for t in ["Submit", "Submit application", "Submit Application", "Apply now", "Apply", "Send Application", "Finish",
              "Apply and save", "Apply without saving", "Sign and submit"]:
        assert looks_like_submit(t), t
    for t in ["Save and Continue", "Next", "Back", "Add another", "Apply filters", "Yes", "No", "Submit feedback later"]:
        assert not looks_like_submit(t), t


def test_background_tab_keeps_focus_and_window_parked(chrome, fixture_server):
    before = foreground_window_handle()
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page, tid = chrome.open_background_tab(b, fixture_server.url("single.html"))
        page.wait_for_load_state("domcontentloaded")
        assert chrome.tab(tid) is not None
        assert chrome.is_parked(b, tid), f"window not parked off-screen: {chrome.window_bounds(b, tid)}"
        # a parked window renders at full rate, so actions are fast (a minimized one gets 1 frame/s: ~2 s per action)
        page.locator("#name").click()          # warm-up: first action after load pays Playwright's init cost
        t0 = time.time()
        for i in range(3):
            page.locator("#name").click()
            page.locator("#name").fill(f"x{i}")
        assert page.locator("#name").input_value() == "x2"
        assert time.time() - t0 < 1.5, "actions are slow: the window is probably minimized, not parked"
        raf = page.evaluate("() => new Promise(r => { let n=0; const t0=performance.now(); function f(){ n++; if (performance.now()-t0 < 500) requestAnimationFrame(f); else r(n); } requestAnimationFrame(f); setTimeout(()=>r(n), 900); })")
        assert raf > 10, f"only {raf} animation frames in 0.5 s: window is throttled"
        # if the human minimizes the window, it is left alone (restoring it activates it) and the next application
        # opens in a fresh parked window
        win_before = chrome.window_bounds(b, tid)["windowId"]
        assert before == foreground_window_handle(), "tab creation or parking changed the foreground window"
        s = b.new_browser_cdp_session()
        s.send("Browser.setWindowBounds", {"windowId": win_before, "bounds": {"windowState": "minimized"}})
        s.detach()
        # simulating the human's minimize through CDP hands the foreground to another Chrome window (Windows does
        # that for any minimize); a real click on the minimize button is the human's own focus change. Resample.
        before = foreground_window_handle()
        assert chrome.window_state(b, tid) == "minimized"
        assert chrome.ensure_parked(b, tid) is False
        page2, tid2 = chrome.open_background_tab(b, fixture_server.url("single.html"))
        page2.wait_for_load_state("domcontentloaded")
        assert chrome.window_bounds(b, tid2)["windowId"] != win_before
        assert chrome.is_parked(b, tid2)
        assert chrome.window_state(b, tid) == "minimized"
        page2.close()
        page.close()   # the minimized window keeps the fixture's about:blank tab; later tests get a fresh parked window
        b.close()
    after = foreground_window_handle()
    assert before == after, "tab creation or parking changed the foreground window"


def test_wizard_guard_blocks_only_finalize(chrome, fixture_server):
    fixture_server.reset()
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page, tid = chrome.open_background_tab(b, fixture_server.url("wizard.html"))
        page.wait_for_load_state("domcontentloaded")
        guard = Guard(page, "_fixture", ttl_ms=60000)
        guard.arm()
        act = Actions(page, ats="_fixture", guard=guard, review_reached=_review_reached)
        # honeypot refused, normal field filled
        with pytest.raises(Refused):
            act.fill(page.locator("[data-automation-id=beecatcher]"), "bot")
        assert act.fill(page.locator("#fn"), "Alex") == "Alex"
        # intermediate saves pass the network layer
        act.click(page.locator("#next"))
        page.wait_for_function("() => document.getElementById('page2') && !document.getElementById('page2').hidden")
        assert fixture_server.hits("/jobapplication/1/name") == 1
        act.click(page.locator("#next"))
        page.wait_for_function("() => !document.getElementById('page3').hidden")
        # review page: Next is now Submit -> tool layer refuses, Back allowed
        assert _review_reached(page)
        with pytest.raises(Refused):
            act.click(page.locator("#next"))
        # network layer: a scripted click (bypassing the tool layer) must still not reach finalize
        page.evaluate("() => document.getElementById('next').dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}))")
        time.sleep(0.5)
        assert fixture_server.hits("/finalize") == 0
        assert guard.dom_blocked_count() >= 1 or guard.aborted, "neither DOM nor network layer engaged"
        # even a direct fetch from page JS is aborted while armed
        res = page.evaluate("async () => { try { const r = await fetch('/wday/cxs/t/jobapplication/1/finalize', {method:'POST', body:'{}'}); return r.status; } catch (e) { return 'ERR'; } }")
        assert res == "ERR"
        assert fixture_server.hits("/finalize") == 0
        act.click(page.locator("#back"))
        # handoff
        assert guard.disarm()
        assert not guard.dom_armed()
        b.close()
        # the human (a second, clean connection) can submit
        b2 = chrome.attach(pw)
        p2 = chrome.page_for_target(b2, tid)
        assert p2 is not None
        p2.locator("#next").click()   # back to review
        p2.wait_for_function("() => !document.getElementById('page3').hidden")
        p2.locator("#next").click()   # Submit
        p2.wait_for_function("() => document.getElementById('log').textContent.includes('finalize:200')", timeout=5000)
        assert fixture_server.hits("/finalize") == 1
        p2.close()
        b2.close()


def test_single_page_guard_and_enter_key(chrome, fixture_server):
    fixture_server.reset()
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page, tid = chrome.open_background_tab(b, fixture_server.url("single.html"))
        page.wait_for_load_state("domcontentloaded")
        guard = Guard(page, "_fixture", ttl_ms=60000)
        guard.arm()
        act = Actions(page, ats="_fixture", guard=guard)
        act.fill(page.locator("#name"), "Alex Doe")
        act.click(page.locator("#add"))                      # type=button inside a form: fine
        with pytest.raises(Refused):
            act.click(page.locator("#btn-submit"))
        with pytest.raises(Refused):
            act.press_safe(page.locator("#name"), "Enter")
        # a raw Enter keypress (as a runaway agent might) is swallowed by the DOM layer
        page.locator("#email").press("Enter")
        time.sleep(0.4)
        assert fixture_server.hits("/apply") == 0
        # requestSubmit is cancelled by the capturing listener
        page.evaluate("() => document.getElementById('application-form').requestSubmit()")
        time.sleep(0.4)
        assert fixture_server.hits("/apply") == 0
        assert guard.dom_blocked_count() >= 1
        assert guard.disarm()
        page.close()
        b.close()


def test_typeless_yesno_buttons_allowed_submit_refused(chrome, fixture_server):
    fixture_server.reset()
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page, tid = chrome.open_background_tab(b, fixture_server.url("yesno.html"))
        page.wait_for_load_state("domcontentloaded")
        guard = Guard(page, "_fixture", ttl_ms=60000)
        guard.arm()
        act = Actions(page, ats="_fixture", guard=guard)
        act.click(page.locator("button[data-option=no]"))
        assert page.locator("button[data-option=no]").get_attribute("aria-pressed") == "true"
        with pytest.raises(Refused):
            act.click(page.locator("#submit"))
        page.evaluate("() => document.getElementById('submit').click()")
        time.sleep(0.4)
        assert fixture_server.hits("/apply") == 0
        assert guard.disarm()
        page.close()
        b.close()


def test_ttl_expiry_releases_page_without_disarm(chrome, fixture_server):
    """A crashed daemon must not leave the page blocked: the DOM layer self-removes after the TTL."""
    fixture_server.reset()
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page, tid = chrome.open_background_tab(b, fixture_server.url("single.html"))
        page.wait_for_load_state("domcontentloaded")
        guard = Guard(page, "_fixture", ttl_ms=1500)
        guard.arm()
        assert guard.dom_armed()
        page.unroute_all(behavior="wait")   # simulate the network layer dying with the daemon
        b.close()                           # simulate the crash: no disarm
        b2 = chrome.attach(pw)
        p2 = chrome.page_for_target(b2, tid)
        deadline = time.time() + 6
        while time.time() < deadline and p2.evaluate("() => !!window.__sa"):
            time.sleep(0.25)
        assert not p2.evaluate("() => !!window.__sa"), "DOM guard outlived its TTL"
        p2.evaluate("() => document.getElementById('application-form').requestSubmit()")
        p2.wait_for_function("() => document.getElementById('log').textContent.includes('apply:200')", timeout=5000)
        assert fixture_server.hits("/apply") == 1
        p2.close()
        b2.close()


def test_double_injection_is_harmless(chrome, fixture_server):
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page, tid = chrome.open_background_tab(b, fixture_server.url("single.html"))
        page.wait_for_load_state("domcontentloaded")
        g1 = Guard(page, "_fixture", ttl_ms=60000)
        g1.arm()
        g2 = Guard(page, "_fixture", ttl_ms=60000)
        g2.arm()
        assert page.evaluate("() => document.querySelectorAll('#__sa_banner').length") == 1
        assert g2.disarm()
        assert not g1.dom_armed()
        page.close()
        b.close()


def test_workday_overlay_is_judged_by_the_covered_button(chrome, fixture_server):
    """DraftKings (2026-09-27): every Workday auth/footer button sits under `div[role=button][aria-label=Submit]`.
    The sign-in overlay must pass (tool layer and DOM layer); the overlay above a real Submit stays blocked."""
    fixture_server.reset()
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        page, tid = chrome.open_background_tab(b, fixture_server.url("overlay.html"))
        page.wait_for_load_state("domcontentloaded")
        guard = Guard(page, "workday")
        guard.arm()
        act = Actions(page, ats="workday", guard=guard)
        try:
            with pytest.raises(Refused):
                act.click(page.locator("#submitOverlay"))               # tool layer: judged as the covered "Submit"
            page.locator("#submitOverlay").click()                       # DOM layer, raw click like a stray script
            page.wait_for_timeout(600)
            assert page.evaluate("() => window.__sa && window.__sa.blocked") >= 1
            assert fixture_server.hits("/apply") == 0
            info = act.click(page.locator("#signinOverlay"))            # judged as the covered "Sign In": allowed
            assert info.automation_id == "signInSubmitButton"
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(800)
            assert fixture_server.hits("/signin") == 1 and fixture_server.hits("/apply") == 0
        finally:
            try:
                guard.disarm()
            except Exception:  # noqa: BLE001
                pass
            chrome.close_tab(tid)
            b.close()
