"""The SmartApply Chrome: a separate profile, launched detached, attached to over CDP, never focused.

Why this shape (research/04-browser-handoff.md):
- Chrome 136+ refuses a debug port on the default profile, so a dedicated --user-data-dir is required anyway.
- Chrome is started by us as a detached process, not by Playwright, so the filled tabs outlive the daemon.
- `connect_over_cdp` attaches; `browser.close()` on it only disconnects.
- Playwright's `new_page()` foregrounds the window, so tabs are created with CDP `Target.createTarget(background=true)`.
- The window stays minimized while filling; it is restored only when Alex asks to review.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from ..config import Config
from ..log import get_logger
from ..util import detached_flags

log = get_logger("chrome")

# Keep background tabs responsive. These are the subset of Playwright's own launch switches that matter for a
# minimized, occluded window; the rest (disable-extensions, mock keychain, ...) would make the browser worse for a human.
BACKGROUND_SWITCHES = [
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-ipc-flooding-protection",
    "--disable-hang-monitor",
    "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling,TabHoverCardImages",
]

BASE_SWITCHES = [
    "--remote-debugging-port=0",
    "--remote-debugging-address=127.0.0.1",
    "--no-first-run",
    "--no-default-browser-check",
    "--no-startup-window",
    "--disable-session-crashed-bubble",
    "--hide-crash-restore-bubble",
    "--disable-search-engine-choice-screen",
]


class ChromeNotRunning(RuntimeError):
    pass


class Chrome:
    def __init__(self, cfg: Config):
        self.exe = Path(str(cfg.chrome.exe))
        self.profile_dir = cfg.chrome_profile_dir
        self.extra_args = [str(a) for a in (cfg.chrome.get("extra_args") or [])]
        self.connect_timeout_s = int(cfg.chrome.get("connect_timeout_s", 15))
        self.max_tabs = int(cfg.chrome.get("max_tabs", 6))
        self._target_cache: dict[int, str] = {}   # id(page) -> targetId

    # ------------------------------------------------------------------ process

    @property
    def active_port_file(self) -> Path:
        return self.profile_dir / "DevToolsActivePort"

    def devtools_port(self) -> int | None:
        """The live port from DevToolsActivePort, validated against /json/version. None if not running."""
        try:
            first = self.active_port_file.read_text(encoding="utf-8").splitlines()[0].strip()
            port = int(first)
        except (OSError, ValueError, IndexError):
            return None
        return port if self._alive(port) else None

    def _alive(self, port: int) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def is_running(self) -> bool:
        return self.devtools_port() is not None

    def launch(self, *, wait_s: int = 30) -> int:
        """Start Chrome detached (no console, own process group) and return its debug port."""
        port = self.devtools_port()
        if port is not None:
            return port
        if not self.exe.exists():
            raise FileNotFoundError(f"chrome.exe not found: {self.exe}")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.active_port_file.unlink()
        except OSError:
            pass
        argv = [str(self.exe), f"--user-data-dir={self.profile_dir}", *BASE_SWITCHES, *BACKGROUND_SWITCHES, *self.extra_args]
        log.info("launching chrome: %s", " ".join(argv[1:]))
        subprocess.Popen(argv, creationflags=detached_flags(), close_fds=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + wait_s
        while time.time() < deadline:
            port = self.devtools_port()
            if port is not None:
                return port
            time.sleep(0.25)
        raise ChromeNotRunning("Chrome did not expose a DevTools port in time")

    def ensure(self) -> int:
        return self.launch()

    def version(self) -> dict[str, Any]:
        port = self.devtools_port()
        if port is None:
            raise ChromeNotRunning("Chrome is not running")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))

    # ------------------------------------------------------------------ tabs without attaching (no automation signal)

    def list_tabs(self) -> list[dict[str, Any]]:
        port = self.devtools_port()
        if port is None:
            return []
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [t for t in data if t.get("type") == "page"]

    def tab(self, target_id: str) -> dict[str, Any] | None:
        for t in self.list_tabs():
            if t.get("id") == target_id:
                return t
        return None

    def navigate_raw(self, target_id: str, url: str, *, wait_s: float = 20.0) -> bool:
        """Navigate a tab over its own CDP socket, before Playwright attaches. Playwright navigating a reused tab
        itself attaches to every iframe the new page creates, and on reCAPTCHA pages its later route/unroute calls
        then wait minutes or forever (Stripe and Pinterest refills, 2026-10-06); a fresh attach afterwards is
        fine. Waits for the load event; never activates the tab."""
        t = self.tab(target_id)
        ws_url = (t or {}).get("webSocketDebuggerUrl")
        if not ws_url:
            return False
        from websockets.sync.client import connect
        deadline = time.time() + wait_s
        with connect(ws_url, max_size=None, open_timeout=10) as ws:
            ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
            ws.send(json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": url}}))
            while time.time() < deadline:
                try:
                    msg = json.loads(ws.recv(timeout=max(0.1, deadline - time.time())))
                except TimeoutError:
                    break
                if msg.get("id") == 2 and "error" in msg:
                    return False
                if msg.get("method") == "Page.loadEventFired":
                    return True
        return True

    def close_tab(self, target_id: str) -> bool:
        port = self.devtools_port()
        if port is None:
            return False
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/close/{target_id}", timeout=3) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    # ------------------------------------------------------------------ attach

    def attach(self, pw: Playwright, *, owned_targets: set[str] | None = None) -> Browser:
        """connect_over_cdp with a timeout; on a hang (Playwright #42882: a discarded tab never answers) close
        tabs we do not own and retry once."""
        port = self.devtools_port()
        if port is None:
            raise ChromeNotRunning("Chrome is not running")
        url = f"http://127.0.0.1:{port}"
        try:
            return pw.chromium.connect_over_cdp(url, timeout=self.connect_timeout_s * 1000)
        except Exception as e:  # noqa: BLE001
            log.warning("connect_over_cdp failed (%s); closing unowned tabs and retrying", e)
            for t in self.list_tabs():
                if owned_targets and t.get("id") in owned_targets:
                    continue
                if t.get("url", "").startswith(("chrome://", "about:")) or not owned_targets:
                    self.close_tab(t["id"])
            return pw.chromium.connect_over_cdp(url, timeout=self.connect_timeout_s * 1000)

    @staticmethod
    def context(browser: Browser) -> BrowserContext:
        """Only the default context is ever used: contexts we create die with browser.close()."""
        if not browser.contexts:
            raise ChromeNotRunning("attached browser has no default context")
        return browser.contexts[0]

    def target_id(self, page: Page) -> str:
        key = id(page)
        if key in self._target_cache:
            return self._target_cache[key]
        session = page.context.new_cdp_session(page)
        try:
            info = session.send("Target.getTargetInfo")
        finally:
            try:
                session.detach()
            except Exception:  # noqa: BLE001
                pass
        tid = str(info["targetInfo"]["targetId"])
        self._target_cache[key] = tid
        return tid

    def page_for_target(self, browser: Browser, target_id: str) -> Page | None:
        for page in self.context(browser).pages:
            try:
                if self.target_id(page) == target_id:
                    return page
            except Exception:  # noqa: BLE001
                continue
        return None

    def _create_exposed(self, session: Any, browser: Browser, url: str, wait_s: int) -> tuple[Page | None, str]:
        """`Target.createTarget(background, newWindow)` plus the attach nudge until Playwright exposes the page."""
        params: dict[str, Any] = {"url": url, "background": True, "newWindow": True}
        res = session.send("Target.createTarget", params)
        tid = str(res["targetId"])
        deadline = time.time() + wait_s
        page: Page | None = None
        # The nudge only takes once the target exists on Chrome's side; repeat attach/detach until exposed.
        while time.time() < deadline:
            page = self.page_for_target(browser, tid)
            if page is not None:
                break
            try:
                nudge_sid = str(session.send("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"])
            except Exception as e:  # noqa: BLE001
                log.debug("nudge attach failed: %s", e)
                nudge_sid = None
            time.sleep(0.3)
            page = self.page_for_target(browser, tid)
            if nudge_sid:
                try:
                    session.send("Target.detachFromTarget", {"sessionId": nudge_sid})
                except Exception:  # noqa: BLE001
                    pass
            if page is not None:
                break
            time.sleep(0.2)
        return page, tid

    def open_background_tab(self, browser: Browser, url: str, *, wait_s: int = 15) -> tuple[Page, str]:
        """Create a tab without activating or restoring the window. Returns (page, targetId).

        Measured on Chrome 154 / Playwright 1.62: a plain `Target.createTarget` restores a minimized window, so
        `background: true` is required; but Playwright does not initialize a background target until a second
        CDP session attaches to it, so we attach a throwaway session and detach it once the page is exposed."""
        self.context(browser)
        session = browser.new_browser_cdp_session()
        try:
            # One window per application. Measured 2026-09-25: `Target.createTarget(newWindow, background)` never
            # takes the foreground and the new window's only tab renders at full rate; `Target.activateTarget`
            # (needed to un-throttle a second tab in a shared window) DOES take the foreground when Chrome was
            # started from a foreground process (a terminal), so tabs are never shared and never activated.
            # A window Alex minimized is left alone: restoring it, even via Win32 SW_SHOWNOACTIVATE, activates it.
            have_window = False
            page, tid = self._create_exposed(session, browser, url, wait_s)
            if page is None:
                # Seen about once per full test run: Chrome creates the target but Playwright never gets the page.
                # A fresh target the second time has always worked; the orphan is closed so no blank tab lingers.
                log.warning("target %s never exposed a page; closing it and creating another", tid)
                try:
                    session.send("Target.closeTarget", {"targetId": tid})
                except Exception:  # noqa: BLE001
                    pass
                page, tid = self._create_exposed(session, browser, url, wait_s)
            if page is None:
                raise ChromeNotRunning(f"created target {tid} but Playwright never exposed a page for it")
            if not have_window:
                # A brand-new window is the only case that can appear; park it off-screen before anything loads.
                try:
                    self._park_via_cdp(session, tid)
                except Exception as e:  # noqa: BLE001
                    log.debug("parking new window failed: %s", e)
            return page, tid
        finally:
            try:
                session.detach()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ window parking (never minimize)
    #
    # Measured 2026-09-24: a minimized Chrome window produces 1 animation frame per second, so every Playwright
    # action waits ~2 s on its stability check and clicks race the widgets. Restoring a minimized window (CDP, or
    # even Win32 SW_SHOWNOACTIVATE) makes Chrome activate it, which steals focus. So the SmartApply window is never
    # minimized or restored by us: it lives off-screen in the normal state (151 fps, 40 ms clicks, invisible). A
    # window Alex minimized stays minimized; new applications open in a fresh parked window.

    PARK_LEFT = -32000
    PARK_TOP = -32000

    def window_bounds(self, browser: Browser, target_id: str) -> dict[str, Any]:
        session = browser.new_browser_cdp_session()
        try:
            win = session.send("Browser.getWindowForTarget", {"targetId": target_id})
            b = dict(session.send("Browser.getWindowBounds", {"windowId": win["windowId"]})["bounds"])
            b["windowId"] = win["windowId"]
            return b
        finally:
            try:
                session.detach()
            except Exception:  # noqa: BLE001
                pass

    def window_state(self, browser: Browser, target_id: str) -> str:
        return str(self.window_bounds(browser, target_id).get("windowState", ""))

    def is_parked(self, browser: Browser, target_id: str) -> bool:
        b = self.window_bounds(browser, target_id)
        return b.get("windowState") == "normal" and int(b.get("left", 0)) <= self.PARK_LEFT // 2

    def _parked_window_id(self, session) -> int | None:
        seen: set[int] = set()
        for t in self.list_tabs():
            try:
                win = session.send("Browser.getWindowForTarget", {"targetId": t["id"]})["windowId"]
                if win in seen:
                    continue
                seen.add(win)
                b = session.send("Browser.getWindowBounds", {"windowId": win})["bounds"]
                if b.get("windowState") == "normal" and int(b.get("left", 0)) <= self.PARK_LEFT // 2:
                    return int(win)
            except Exception:  # noqa: BLE001
                continue
        return None

    def _park_via_cdp(self, session, target_id: str) -> bool:
        """Move a normal/maximized window off-screen. Returns False for a minimized window (never restored by us)."""
        win = session.send("Browser.getWindowForTarget", {"targetId": target_id})["windowId"]
        bounds = session.send("Browser.getWindowBounds", {"windowId": win})["bounds"]
        if bounds.get("windowState") == "minimized":
            return False
        session.send("Browser.setWindowBounds", {"windowId": win, "bounds": {
            "left": self.PARK_LEFT, "top": self.PARK_TOP, "width": int(bounds.get("width") or 1280),
            "height": int(bounds.get("height") or 900), "windowState": "normal"}})
        return True

    def ensure_parked(self, browser: Browser, target_id: str) -> bool:
        """Before a fill: park the tab's window off-screen. False means the window is minimized and the caller
        must open the application in a new window instead."""
        if self.is_parked(browser, target_id):
            return True
        session = browser.new_browser_cdp_session()
        try:
            return self._park_via_cdp(session, target_id)
        finally:
            try:
                session.detach()
            except Exception:  # noqa: BLE001
                pass

    def is_minimized(self, browser: Browser, target_id: str) -> bool:
        return self.window_state(browser, target_id) == "minimized"

    def activate_tab(self, browser: Browser, target_id: str) -> None:
        """Deliberately a no-op. `Target.activateTarget` moves the foreground to the Chrome window when Chrome was
        launched from a foreground process (measured 2026-09-25 with GetForegroundWindow), so nothing here ever
        activates a tab; every application lives alone in its own parked window, whose single tab renders at
        full rate without activation."""
        log.debug("activate_tab is a no-op (one window per application); target %s", target_id)

    def parked_windows(self, browser: Browser) -> list[tuple[int, str]]:
        """(windowId, a targetId in it) for every parked SmartApply window."""
        out: list[tuple[int, str]] = []
        seen: set[int] = set()
        for t in self.list_tabs():
            try:
                b = self.window_bounds(browser, t["id"])
            except Exception:  # noqa: BLE001
                continue
            if b["windowId"] in seen:
                continue
            seen.add(b["windowId"])
            if b.get("windowState") == "normal" and int(b.get("left", 0)) <= self.PARK_LEFT // 2:
                out.append((int(b["windowId"]), t["id"]))
        return out

    def unpark_all(self, browser: Browser, *, left: int = 80, top: int = 60, step: int = 36) -> list[str]:
        """Bring every parked window on-screen, cascaded. Only the review commands call this, because Alex asked.
        Returns the target ids used, first one first (the caller may bring that page to front)."""
        tids: list[str] = []
        for i, (_win, tid) in enumerate(self.parked_windows(browser)):
            self.unpark(browser, tid, left=left + i * step, top=top + i * step)
            tids.append(tid)
        return tids

    def unpark(self, browser: Browser, target_id: str, *, left: int = 80, top: int = 60) -> None:
        """Bring the window on-screen. Only `Runner.open_review` calls this, only because Alex asked."""
        session = browser.new_browser_cdp_session()
        try:
            win = session.send("Browser.getWindowForTarget", {"targetId": target_id})["windowId"]
            bounds = session.send("Browser.getWindowBounds", {"windowId": win})["bounds"]
            if bounds.get("windowState") == "minimized":
                session.send("Browser.setWindowBounds", {"windowId": win, "bounds": {"windowState": "normal"}})
            session.send("Browser.setWindowBounds", {"windowId": win, "bounds": {
                "left": left, "top": top, "width": int(bounds.get("width") or 1280), "height": int(bounds.get("height") or 900),
                "windowState": "normal"}})
        finally:
            try:
                session.detach()
            except Exception:  # noqa: BLE001
                pass


    def present(self, browser: Browser, target_id: str, slot: int = 0) -> bool:
        """After a fill: put the window on-screen but behind everything, never activated, so clicking its taskbar
        button shows it (a parked window only "activated" off-screen, which looked like a dead click; Alex,
        2026-10-05). Moving a normal-state window via CDP was measured not to change the foreground window;
        the Win32 z-order push uses SWP_NOACTIVATE. Returns False when the window is minimized (left alone)."""
        left, top = 60 + (slot % 10) * 30, 40 + (slot % 10) * 24
        if self.window_state(browser, target_id) == "minimized":
            return False
        self.unpark(browser, target_id, left=left, top=top)
        try:
            _send_to_back(left, top)
        except Exception as e:  # noqa: BLE001
            log.debug("z-order push failed: %s", e)
        return True

    # ------------------------------------------------------------------ window

    def park_all(self, browser: Browser) -> None:
        """Park every SmartApply window (daemon start, after Alex has reviewed and left windows around)."""
        seen: set[int] = set()
        for t in self.list_tabs():
            try:
                b = self.window_bounds(browser, t["id"])
                if b["windowId"] in seen:
                    continue
                seen.add(b["windowId"])
                self.ensure_parked(browser, t["id"])
            except Exception as e:  # noqa: BLE001
                log.debug("park failed for %s: %s", t.get("id"), e)


def foreground_window_handle() -> int:
    """Test hook: the current foreground HWND, so tests can prove tab creation never steals focus."""
    try:
        import ctypes
        return int(ctypes.windll.user32.GetForegroundWindow())
    except Exception:  # noqa: BLE001
        return 0


def _send_to_back(left: int, top: int) -> int:
    """Push the Chrome top-level window at (left, top) (CDP DIPs) to the bottom of the z-order without activating
    it. Matched by position because `present` just put it at a slot no other SmartApply window uses. Returns how
    many windows were moved."""
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    hwnd_bottom, flags = 1, 0x0001 | 0x0002 | 0x0010   # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
    hits: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lp):
        cls = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "Chrome_WidgetWin_1" and u32.IsWindowVisible(hwnd):
            r = wintypes.RECT()
            u32.GetWindowRect(hwnd, ctypes.byref(r))
            try:
                scale = u32.GetDpiForWindow(hwnd) / 96.0
            except Exception:  # noqa: BLE001
                scale = 1.0
            if any(abs(r.left - left * s) <= 12 and abs(r.top - top * s) <= 12 for s in (1.0, scale)):
                hits.append(hwnd)
        return True

    u32.EnumWindows(visit, 0)
    for h in hits:
        u32.SetWindowPos(h, hwnd_bottom, 0, 0, 0, 0, flags)
    return len(hits)
