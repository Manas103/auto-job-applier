# Agent-fills, human-submits form automation on Windows 11: research report (as of 2026-09-24)

## Summary

- **Recommended:** a Python daemon owns one dedicated Chrome profile. The daemon starts Chrome itself as a separate, detached process and connects to it with Playwright `connect_over_cdp`. It does not use `launch_persistent_context`.
- **Handoff:** the filled tab stays open in a normal Chrome window, and the browser keeps running whether or not the script is alive.
- **Submit blocking:** use three layers. The tool layer refuses submit clicks, request routing aborts submit POSTs, and a DOM overlay covers the button.
- **Other options:** Claude in Chrome through `claude --chrome` gives the best handoff with no custom code. You can't script it headlessly, though, and you can't block submit in a way the agent can't get around. A custom MV3 extension is the most robust design, but it takes the most work to build.

---

## 1. Chrome remote debugging on Windows (Chrome 136+)

**The rule.** From Chrome 136, `--remote-debugging-port` and `--remote-debugging-pipe` are ignored on the default user-data-dir. They only work together with `--user-data-dir` pointing at a non-default folder. The reason is infostealer hardening: a non-default directory gets a different encryption key. Google recommends Chrome for Testing for automation because it keeps the old behavior. ([Chrome blog, 2025-03-17](https://developer.chrome.com/blog/remote-debugging-port))

**How it fails.** Chrome gives no error. It starts normally, the client appears to connect, and then it hangs on a blank page. This was reproduced on Chrome 152. ([cc-skills #151, 2026-09-08](https://github.com/terrylica/cc-skills/issues/151))

**Playwright's documentation agrees.** Automating the default Chrome profile is "not supported"; pointing `userDataDir` at Chrome's main User Data folder "may result in pages not loading or the browser exiting." ([Playwright BrowserType docs](https://playwright.dev/python/docs/api/class-browsertype))

**New approved way into the real profile (Chrome 144+).** Enable the toggle at `chrome://inspect/#remote-debugging`, then connect with chrome-devtools-mcp `--autoConnect`.
- Chrome shows an "Allow" dialog on every connection. A request to remember the choice was closed as "not planned". ([Chrome auto-connect docs, updated 2026-05-13](https://developer.chrome.com/docs/devtools/agents/use-cases/auto-connect); [chrome-devtools-mcp #825, 2026-01-24](https://github.com/ChromeDevTools/chrome-devtools-mcp/issues/825))
- This route has no `/json/version` HTTP endpoint and uses `DevToolsActivePort` instead. On Chrome 151 it was described as the only approved CDP route into a real profile. ([trycua/cua #2916, Aug 2026](https://github.com/trycua/cua/issues/2916); [browser-tools #149](https://github.com/dungle-scrubs/browser-tools/issues/149))
- A per-connection click from the human makes it a poor fit for an unattended agent.

**Workarounds.**

| Option | Behavior |
|---|---|
| Separate `--user-data-dir` with branded Chrome | Works. Chrome looks and behaves normally. |
| Chrome for Testing | Old behavior kept. Not auto-updated, and Google sign-in is sometimes blocked. |
| Chromium | Works ([browser-use #1520](https://github.com/browser-use/browser-use/issues/1520)). Lacks Google sync. |
| Edge, Brave | Same Chromium code, so assume the same default-profile rule applies. No official statement found from either vendor. |

**Does the separate profile feel normal to the human?** Mostly yes. It is a full Chrome window with its own extensions, history and passwords.
- You can't copy the everyday profile into it. App-bound encryption (Chrome 127+) plus the per-directory key mean copied cookies and passwords can't be decrypted. ([Chrome blog](https://developer.chrome.com/blog/remote-debugging-port); [ElcomSoft, 2026-01](https://blog.elcomsoft.com/2026/01/browser-forensics-in-2026-app-bound-encryption-and-live-triage/))
- The practical fix is to sign into your Google account inside that profile once and let passwords and addresses sync.
- Sign in while no automation client is attached. Google blocks sign-in with "This browser or app may not be secure" when it sees automation signals. ([playwright #19420](https://github.com/microsoft/playwright/issues/19420))

**Can `connect_over_cdp` attach to a Chrome the user launched, and can they keep using it?** Yes to both.
- `browser.close()` on a CDP-connected browser only disconnects. The Chrome process and its tabs stay open. ([Playwright Browser docs](https://playwright.dev/docs/api/class-browser))
- A Chrome started with a fixed `--remote-debugging-port` and no `--enable-automation` keeps `navigator.webdriver` false. Attaching over CDP does not set it. ([Scrapfly](https://scrapfly.io/blog/posts/chrome-cdp-stealth-browser-automation-detection-explained))

**Stability.**
- **Connection hang (open bug, 2026-09-23).** `connect_over_cdp` can hang forever if any existing tab doesn't answer CDP, for example a sleeping or discarded tab. Reproduced on Playwright 1.63 with Chrome 153 used as an everyday browser. The workaround is raw CDP `Target.createTarget` / `attachToTarget`. ([playwright #42882](https://github.com/microsoft/playwright/issues/42882); [#42730](https://github.com/microsoft/playwright/issues/42730)) Keeping the agent profile's tab count low avoids most of this.
- **Windows process-model change (Chrome 142).** The launcher process exits right away, and Playwright's `launch` treats that as a crash and kills the child processes (headed mode, pipe). ([Kilo-Org #11226, 2026-06-15](https://github.com/Kilo-Org/kilocode/issues/11226)) Starting Chrome yourself and attaching over CDP avoids this entirely.

## 2. Playwright persistent context (`launch_persistent_context`, headed, channel chrome/msedge)

- **The window.** Real, usable, but shows the "controlled by automated test software" bar because of `--enable-automation`, and `navigator.webdriver` is true. Remove the flag with `ignore_default_args=["--enable-automation"]`.
- **Lifetime.** Chrome is a child of Playwright's Node driver. When the Python script exits, the driver exits and Chrome is killed, taking unsaved form state with it. Playwright has no "detach" option ([playwright-python #1522](https://github.com/microsoft/playwright-python/issues/1522)).
- **So the script must keep running** until the human is finished. Closing the last page can also close a persistent context ([playwright-python #2753](https://github.com/microsoft/playwright-python/issues/2753)), and the Chrome 142 Windows bug applies here too.
- **The "daemon owns the browser" pattern does exist** (Playwright MCP, browser-use, agent-browser). The more robust version is to launch Chrome independently (detached `subprocess.Popen`, fixed port, dedicated `--user-data-dir`) and connect over CDP. The browser then outlives any agent crash, the daemon can restart and reattach, and the human never loses the tab.

## 3. Chrome extension approach

**Setting a resume on a file input.** A content script can build a `File` from bytes, then `new DataTransfer()`, `items.add(file)`, `input.files = dt.files`, and dispatch `input`/`change` events ([PQINA](https://pqina.nl/blog/the-trouble-with-editing-and-uploading-files-in-the-browser); [MDN](https://developer.mozilla.org/en-US/docs/Web/API/DataTransfer/files)). Limits:
- The extension can't read local files itself; the backend sends bytes over native messaging or WebSocket.
- Drop zones need synthetic `dragenter`/`dragover`/`drop` events; works on most React dropzones, not all.

Alternative: `chrome.debugger` with `DOM.setFileInputFiles` (what CDP does). Reliable, but shows a "started debugging this browser" bar on every tab. Suppress with `--silent-debugger-extension-api` or by force-installing the extension via `ExtensionInstallForcelist` policy. ([claude-code #69287, 2026-06-18](https://github.com/anthropics/claude-code/issues/69287))

**MV3 service worker lifetime.**
- Killed after about 30 seconds idle.
- An open `connectNative()` port keeps it alive.
- Since Chrome 116, WebSocket traffic resets the idle timer; keepalive every ~20 seconds. ([Chrome docs](https://developer.chrome.com/docs/extensions/how-to/web-platform/websockets))

**Cross-origin iframes.** Declare `all_frames: true` and host permissions. Some Workday widgets and captcha iframes remain off limits.

**Existing tools.**

| Tool | What it is |
|---|---|
| **Claude in Chrome** (official) | GA on all paid plans since 2026-08-26, including Max. Uses `chrome.debugger`, so shows the debugging bar. Detail below. |
| **open-claude-in-chrome** | MIT clean-room reimplementation: MV3 extension, native messaging, Node MCP server. Any Chromium browser, no domain blocklist, uploads files from any path. ([GitHub](https://github.com/noemica-io/open-claude-in-chrome)) Good reference architecture. |
| **Playwright MCP Bridge** (Microsoft) | Extension for Chrome/Edge plus `--extension` mode. Attaches to tabs in the user's real profile and reuses its logins. ([docs](https://playwright.dev/mcp/configuration/browser-extension)) |
| **Nanobrowser** | Open-source MV3 multi-agent extension using your own API key ([GitHub](https://github.com/nanobrowser/nanobrowser)). Driven from its side panel, not a backend. |
| **browser-use** | CDP only. Planning an extension path because of the Chrome 136 rule ([agent-hands #10](https://github.com/zm2231/agent-hands/issues/10)). |
| **Simplify Copilot** | Proprietary autofill for Workday, Greenhouse, Lever and others; user clicks submit. ~90% accuracy on Greenhouse/Lever, ~70% on Workday ([review, 2026](https://resumeoptimizerpro.com/blog/autofill-job-applications-chrome-extension)). |
| **JobWizard** | Same review-first model ([2026-07-13](https://jobwizard.ai/blog/safe-workday-autofill-review-before-submit-no-auto-apply)). |

Not verified: BrowserOS, Automa, Fillout, Magical.

**Claude in Chrome in more detail.**
- Works in the everyday profile.
- Scriptable from Claude Code with `claude --chrome`; VS Code extension has it built in.
- Windows support via named pipe and native messaging host in HKCU.
- File upload needs Claude Code v2.1.211+, limited to 10 MB.
- Pauses for CAPTCHAs and logins.
- Requires `/login` on a claude.ai account. API keys and `setup-token` credentials disable it, so it can't run under an API-key Agent SDK process.
- Tab group stays open when the session ends unless `/clear`.
- Idle service worker drops connections; fix with `/chrome` then "Reconnect".
- Sources: [Claude Code Chrome docs](https://code.claude.com/docs/en/chrome); [Help Center](https://support.claude.com/en/articles/12012173-get-started-with-claude-in-chrome)
- Safety: third-party write-ups say it confirms before submitting forms ([usecarly, 2026-07-17](https://www.usecarly.com/blog/claude-in-chrome-limitations/)). The official safety page (2026-08-12) only names downloads and sensitive-data entry as needing confirmation ([safety page](https://support.claude.com/en/articles/12902428-use-claude-in-chrome-safely)). Treat as a model-level soft guard, not a hard block.

## 4. Detection

**What gets flagged.**
- Vanilla Playwright-launched Chrome: `navigator.webdriver` true, `__playwright` globals, CDP-level tells.
- In 2026, CDP/automation-protocol fingerprinting matters more than JS patches ([Foil](https://usefoil.com/learn/playwright-bot-detection); [benchmark, 2026-05-13](https://ianlpaterson.com/blog/anti-detect-browser-benchmark-patchright-nodriver-curl-cffi/)).
- In that 31-target benchmark: nodriver passed 28, Patchright and Camoufox mid-pack, vanilla Playwright worst.

**What that means for job sites.**
- **Greenhouse** hosted boards run invisible reCAPTCHA Enterprise, scoring the session when submit is pressed. A low score leads to an emailed verification code, not a block ([Greenhouse support, 2026-03-02](https://support.greenhouse.io/hc/en-us/articles/115005448066-Invisible-reCAPTCHA)).
- Because the human presses submit, the score reflects real human input at submit time. Earlier automation signals such as `webdriver=true` still count.
- **Lever** uses hCaptcha. **Workday** mostly relies on account creation and email verification; no public evidence of Workday-specific CDP blocking.
- Take-away: self-launched real Chrome with CDP attached (no `--enable-automation`, `webdriver` false, real profile history) covers most of the benefit. Stealth forks are secondary.

**Stealth tools alive** (per the benchmark and [BotCloud](https://botcloud.dev/blog/playwright-anti-fingerprinting-alternatives-2026/)).

| Tool | Status |
|---|---|
| Patchright | Active, pushed 2026-08 |
| Camoufox | Active, 2026-08. Firefox-based, no CDP. |
| nodriver | Maintained, 2026-05. AGPL. |
| rebrowser-playwright | Abandoned, last commit Sept 2024 |
| playwright-stealth | Legacy approach |
| zendriver | Not verified |

## 5. Submit-blocking

**(a) Tool-layer guard (primary).** The agent never gets a raw "click anything" or "run JavaScript" tool.
- Every click resolves its target element first. Refuse if accessible name/text/value matches `/submit|send application|apply( now)?|finish|complete/i`, if it is `type=submit` on the final step, or if it matches a known selector (Workday review-page submit `data-automation-id`, unverified).
- Refuse Enter key presses inside forms on the review step.
- Refuse `evaluate` and `form.submit()`.
- **Robustness:** deterministic, outside the model, the strongest layer. Leaks if arbitrary JS or coordinate clicks are exposed. For coordinate clicks, hit-test with `elementFromPoint` first.

**(b) Network routing (backstop).** `context.route("**/*", handler)` aborts POST/PUT/GraphQL requests matching submit patterns while the "agent-armed" flag is on. The daemon clears the flag at handoff, so the human's submit goes through.
- Set `service_workers="block"`, because routes don't see requests a service worker handles ([Playwright network docs](https://playwright.dev/python/docs/network)).
- Only the Greenhouse API endpoint is officially documented; verify the rest by capturing one real submit with `page.on("request")`.

| ATS | Submit request |
|---|---|
| Greenhouse hosted (`job-boards.greenhouse.io`) | Multipart POST under `/{board}/jobs/{id}`. Official API form: `POST boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}` ([docs](https://docs.greenhouse.io/job-board.html)). |
| Lever | `POST jobs.lever.co/{company}/{postingId}/apply` |
| Ashby | `POST jobs.ashbyhq.com/api/non-user-graphql?op=ApiSubmitSingleApplicationFormAction` |
| Workday | POST to `{tenant}.wd*.myworkdayjobs.com/wday/cxs/...` on the Review step. Public CXS endpoints documented are only for listings ([dev.to](https://dev.to/agenticemail/every-major-ats-has-a-public-job-feed-here-is-how-to-read-them-all-3k10)). Workday also saves progress with POSTs on every "Next", so match on the final step only. |

- **Robustness:** high where the endpoint is known; breaks when an ATS changes endpoints; Workday's intermediate POSTs make a blanket block unsafe.

**(c) DOM overlay.** Inject a `pointer-events` shield or `disabled` over the submit button; remove at handoff.
- **Robustness:** weakest. SPA re-render removes it; JS or Enter bypasses it. Still a useful visual cue for the human.

**Order of preference:** (a), then (b), then (c). Use all three. On the Claude in Chrome route you can't add (a) or (b) outside the model.

## 6. Windows specifics

- **Window and profile separation.** Dedicated `--user-data-dir` gives its own window. Give it its own shortcut, profile name and icon/color for the taskbar.
- **Daemon.** Not a Windows service: Session 0 isolation means a service can't show a browser window on the desktop ([Microsoft](https://techcommunity.microsoft.com/blog/askperf/application-compatibility---session-0-isolation/372361)). Use a Task Scheduler "At log on" task, "Run only when user is logged on", running `pythonw.exe`. A Startup-folder shortcut also works.
- **Console-window pop-ups.** Launching `python.exe`, node, git or Chrome from a console-less parent flashes or opens a console. Use `pythonw.exe` and `creationflags=subprocess.CREATE_NO_WINDOW` (plus `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` for a Chrome that should outlive the parent). ([m3-memory #153](https://github.com/skynetcmd/m3-memory/issues/153); [hermes-agent #117781](https://github.com/NousResearch/hermes-agent/issues/117781))
- **stdout under pythonw.** `sys.stdout` is `None`, which can break Playwright's driver launch; log to a file.

---

## Architecture comparison

| | A. CDP-attach to a separate, self-launched profile | B. Playwright persistent-context daemon | C. MV3 extension + local backend (or Claude in Chrome) |
|---|---|---|---|
| **Handoff** | Very good. Normal Chrome window, no automation bar; tab survives agent exit/crash. | Fair. Automation bar; process must stay alive or state is lost. | Best. The everyday browser; nothing to hand off. |
| **Resume upload** | Excellent (`set_input_files`) | Excellent | Good with DataTransfer (some dropzones flaky). Excellent with `chrome.debugger`, at the cost of a bar. Claude in Chrome: up to 10 MB. |
| **Detection risk** | Low to medium | Medium to high (`webdriver` true unless patched) | Lowest with content scripts only. `chrome.debugger` behaves like CDP. |
| **Saved passwords / autofill** | Yes, after one-time Google sign-in and sync | Same, but Google sign-in often blocked | Native |
| **Build effort** | Low; existing Playwright code keeps working | Lowest | High for custom extension. Near zero for Claude in Chrome, but little control. |
| **Hard submit block** | Yes: (a)+(b)+(c) | Yes: (a)+(b)+(c) | Custom: (a)+(c), plus `declarativeNetRequest` as (b). Claude in Chrome: soft only. |
| **Robustness** | Good. Watch playwright #42882 (keep tabs few). | Weak on Windows (Chrome 142 launcher bug, dies with script). | Service-worker keepalive and native-host plumbing; fewest moving parts once built. |

## Recommendation

**Build A now**, keep C as the long-term upgrade.

**Setup.**
1. Create a "SmartApply" profile at `%LOCALAPPDATA%\SmartApply\ChromeProfile`.
2. Launch branded Chrome with that `--user-data-dir`, `--remote-debugging-port=9222`, `--remote-debugging-address=127.0.0.1`, no `--enable-automation`. Start from a `pythonw` daemon with `CREATE_NO_WINDOW | DETACHED_PROCESS`, or a desktop shortcut.
3. Once, before automation runs, sign that profile into Google so passwords and addresses sync. Log into Workday and other ATS accounts there too.

**Daemon.**
- Task Scheduler at-logon task under `pythonw`.
- Attaches with `connect_over_cdp` per job, opens one new tab per application, fills it, uploads the resume with `set_input_files`.
- At the end disconnects without closing anything and notifies (toast, bring window to front).
- Human reviews, edits and clicks Submit in the same tab.

**Submit guard.**
- Only typed tools exposed to Claude: `fill(ref, value)`, `select`, `upload`, `click(ref)`. No raw JS, no coordinate clicks.
- Submit-pattern refusal in the tool layer.
- `context.route` aborts for Greenhouse, Lever, Ashby and Workday submit patterns, gated on an "agent armed" flag removed at handoff.
- Overlay banner on the submit button.
- Log every aborted request to notice endpoint changes.

**If zero custom browser code is wanted**, `claude --chrome` with Claude in Chrome (Max) in the everyday Chrome gives the best handoff on Windows. It can't be driven by an API-key Agent SDK process, and its submit protection is only a model-level confirmation.

**Unverified:** Lever, Ashby and Workday submit endpoints and the Workday button id; no Workday-specific bot-detection documentation found.
