# Browser automation for filling job applications with Claude: research report (as of 2026-09-24)

**Bottom line (this agent's view):** the best base is Claude Code with the official Claude in Chrome extension, running in the user's own Chrome on Windows. Its tabs are the human's tabs, so the handoff needs no special mode. It is covered by the Max subscription. Its tool calls go through Claude Code hooks, so a "never click Submit" rule can be enforced. The fallback, if it works under `claude -p`, is Playwright MCP or playwright-cli attached to the same Chrome through its browser extension. Most of the Workday reliability should come from a deterministic Workday filler script, with the agent handling everything else. None of the agent frameworks has been shown to fill Workday reliably on its own.

**Local facts checked:** Chrome 154.0.8037.57 and Claude Code 2.1.282.

**How to read the numbers:** Star counts, last-push dates and open counts come from the GitHub API on 2026-09-24. GitHub's open count includes PRs.

---

## 1. Comparison table

| Tool | What it is | Runs where | Claude support | Maturity (stars / last push / license) | Cost | Handoff to the human in the same tab | Fit for Workday + stop before submit |
|---|---|---|---|---|---|---|---|
| **Claude in Chrome + Claude Code (`claude --chrome`)** | Anthropic's extension; Claude Code drives real Chrome over native messaging | Local, real Chrome/Edge profile. Windows yes, WSL no | Native; needs Pro/Max/Team/Enterprise login (not API key) | GA July 2026; ~9M users; closed source | Included in Max (counts against limits) | **Yes.** Tabs sit in normal Chrome in a tab group; non-empty tabs kept on exit | **Best fit.** Form input, file upload (v2.1.211+), hooks can block tools. Not confirmed under `-p` |
| **Playwright MCP / playwright-cli** (Microsoft) | Accessibility-tree browser tools; `browser_fill_form`, `browser_file_upload` | Local. Own persistent profile, or `--extension` to attach to real Chrome tabs | Any MCP client | 37.5k / 2026-09-18 / Apache-2.0; CLI 13.5k | Free; tokens via Max or API | Yes with `--extension`. A browser it launches belongs to the session | **Strong alternative.** Deterministic tools that script well with `claude -p` |
| **chrome-devtools-mcp** (Google) | DevTools MCP with `--autoConnect` to running Chrome | Local | Any MCP client | 52.6k / 2026-09-24 / Apache-2.0 | Free | Yes, but consent dialog on every connection | Okay supervised; per-connection consent hurts batch |
| **browser-use** (library) | Python agent framework; **pure CDP since Aug 2025**, no Playwright | Local (or cloud) | `ChatAnthropic`, or via their gateway | 116k / 2026-09-24 / MIT; v0.13.10 (Sep 2026) | Local: your tokens. Cloud: model cost +20%, browser $0.02/h | `keep_alive=True`, or attach with `cdp_url` | Usable, no Workday evidence, API key needed |
| **browser-harness** (browser-use) | Thin CDP harness for coding agents; powers Browser Use CLI 3.0 (Jul 2026) | Local; attaches via `chrome://inspect/#remote-debugging` toggle | Claude Code skill | 18.1k / 2026-09-12 / MIT | Free + Max | Yes (real Chrome) | Plausible alternative to Claude in Chrome; less guard tooling |
| **workflow-use** (browser-use) | Record once, replay | Local | Mostly GPT examples | 4.2k / 2026-09-18 / **AGPL-3.0** | Free | Unclear | README: "very early development… don't use in production" |
| **Stagehand** (Browserbase) | `act`/`extract`/`observe`/`agent`; v4 (2026-08-10) runs in-browser as an extension | Local or Browserbase | Anthropic supported | 25.4k / 2026-09-24 / MIT | Local free; Browserbase $0–$99/mo | Local possible; remote live view | Good dev SDK, no Workday evidence. Releases still show 3.7.x |
| **Skyvern** | Vision + DOM agent platform with workflows | Self-host (Docker/pip; Windows needs Rust + MSVC) or cloud | Claude Opus 5 / 4.x | 23.1k / 2026-09-24 / **AGPL-3.0** | Cloud $0/$29/$149/mo | Live view with "take control" in cloud | **Most Workday-specific engineering** (Jul–Aug 2026 PRs), but the script appears to live in cloud code |
| **Magnitude browser-agent** | Vision-first agent | Local | Recommends Claude Sonnet 4 (old) | 4.1k / **last push 2026-02-08** / Apache-2.0 | Free | Possible | **Stale.** Org pivoted to local inference |
| **Notte** | Agent + scripting framework | Local or cloud | Gemini/OpenAI shown | 2.0k / 2026-09-24 / **SSPL-1.0** | Cloud usage-based | Console viewer | Small; self-reported benchmarks; SSPL |
| **Steel** | Browser API/sandbox, not an agent | Docker/Node locally; cloud | No LLM | 7.7k / 2026-09-21 / Apache-2.0 | Free self-host | Debug UI | Infrastructure only |
| **Lightpanda** | Headless browser in Zig | Headless only; **Windows via WSL2 only** | No LLM | 35.6k / 2026-09-24 / AGPL-3.0 | Free | **No (no rendering)** | **Unsuitable** |
| **Vercel agent-browser** | Rust CLI for agents; `fill`, `upload`, `snapshot -i`, `--auto-connect` | Local, native Windows | Any agent via CLI/skills | 43.2k / 2026-09-23 / Apache-2.0 | Free | Only with `--auto-connect`. **Browsers it launches on Windows die with its daemon** | Decent CLI; domain allowlist, action confirmation |
| **Anthropic browser use tool** (`browser_toolset_20260801`) | API toolset, GA 2026-08-19: `read_page`, `find`, `form_input`, `file_upload`. You supply the executor | Your code | Opus 5.5 / 5 / 4.8, Sonnet 5, Fable/Mythos 5.x | GA; no official reference executor | API tokens (~6.6k overhead per request); not on Max | Whatever your executor does | Good for custom app; you write the executor |
| **Anthropic computer use tool** (`computer_toolset_20260801`) | Screenshot + mouse/keyboard; GA 2026-08-19 | Your VM/desktop | Same models | GA | ~4.5k overhead + ~1–1.8k per screenshot | Only on your own desktop | Weaker than DOM tools for long forms |
| **OpenAI ChatGPT agent / Operator** | Consumer agent | Cloud | n/a | **Operator shut down 2025-08-31; ChatGPT agent removed Aug 2026** | ChatGPT plan | No | **Not viable** |
| **OpenAI GPT-5.4 computer use (API)** | Responses API computer use (2026-03-05) | Your code | Not Claude | GA | OpenAI API | Depends | Off-stack |
| **Gemini computer use** | Gemini 3.x; 3.5 Flash (2026-06-24) | Your code | Not Claude | GA/preview | Gemini API | Depends | Off-stack |
| **Meta Muse** | **Consumer agent product + proprietary model family (§3)** | Meta's cloud VM | n/a | Launched 2026-09-08 | Free / $20 / $100 per mo | No | **Not usable here** |
| **Simplify Copilot** | Rule-based ATS autofill | Your Chrome | n/a | Mature | Free autofill | Yes; never submits | ~90% Greenhouse/Lever, ~70% Workday (blog-reported). Worth using alongside |

---

## 2. Notes per tool

### browser-use
- **Architecture:** dropped Playwright for raw CDP (`cdp-use` + `bubus` event bus) on 2025-08-19/20. [changelog](https://browser-use.com/changelog/19-8-2025), [post](https://browser-use.com/posts/playwright-to-cdp)
- **Activity:** v0.13.10 early Sep 2026 ([releases](https://github.com/browser-use/browser-use/releases)). Recent releases added Claude "Fable 5" support and "improved Anthropic tool argument recovery". 116k stars; MIT.
- **LLMs:** `ChatAnthropic` directly, or `ChatBrowserUse(model='anthropic/claude-…')` via gateway. Their own bu-2-0 is $0.60/$3.50 per MTok. [README](https://github.com/browser-use/browser-use), [pricing](https://browser-use.com/pricing)
- **Windows:** docs list Windows paths. `Browser.from_system_chrome()` and `list_chrome_profiles()` exist. [real-browser docs](https://docs.browser-use.com/customize/browser/real-browser)
  - **Gotcha:** launches Chrome with a debugging port against the real `User Data` folder; Chrome 136+ blocks that ([#1520](https://github.com/browser-use/browser-use/issues/1520)). Workarounds: non-default user-data-dir, or attach with `cdp_url`.
- **Handoff and guard options:** `keep_alive`, `cdp_url`, `allowed_domains` / `prohibited_domains`, `sensitive_data`, `available_file_paths`, `Tools(exclude_actions=[...])`, custom `@tools.action`, step hooks. [browser params](https://docs.browser-use.com/open-source/customize/browser/all-parameters), [agent params](https://docs.browser-use.com/open-source/customize/agent/all-parameters). No built-in "forbid submit"; would be custom.
- **Cloud vs local:** library free and local. Cloud: model cost +20%, $0.02/browser-hour, $5/GB proxies, $15 free credit.
- **Direction:** pushing CLI 3.0 built on browser-harness (2026-07-01, [changelog](https://browser-use.com/changelog/1-7-2026)).
- **Reliability:** Cloud reports 97% Online-Mind2Web (2026-03-25), self-reported with its **own agentic judge on the Claude Agent SDK** ([post](https://browser-use.com/posts/online-mind2web-benchmark)). Open-source library measured at 30% in the independent 2025 human evaluation ([arXiv 2504.01382](https://arxiv.org/html/2504.01382v4)). **No issues or PRs mentioning Workday.**
- **workflow-use:** AGPL; README warns not production-ready. [repo](https://github.com/browser-use/workflow-use)

### browser-harness (browser-use)
- MIT, 18.1k stars. One CDP websocket to **running** Chrome after ticking "Allow remote debugging for this browser instance" at `chrome://inspect/#remote-debugging`. Same on Windows. Installs as a Claude Code skill; also MCP. [repo](https://github.com/browser-use/browser-harness), [install.md](https://github.com/browser-use/browser-harness/blob/main/install.md)
- No guard or handoff features, but drives real Chrome so handoff is automatic.

### Claude in Chrome with Claude Code
Source: [code.claude.com/docs/en/chrome](https://code.claude.com/docs/en/chrome).
- **Requirements:** extension 1.0.36+, direct Anthropic plan, `/login` session. API key or `setup-token` turns it off. Chrome and Edge, **Windows included**; WSL not.
- **Can do:** file uploads up to 10 MB (v2.1.211+), shared login state, visible window. Pauses on logins and CAPTCHAs.
- **Tab lifecycle:** per-session tab group. On exit or `/resume`, group closed only if it holds nothing but empty tabs. `/clear` closes the group. Changelog v2.1.280: "close the browser tabs it opens once it no longer needs them". Handoff risk; instruct Claude to leave the application tab open.
- **Guards:** PreToolUse hooks fire for Claude in Chrome tools and MCP tools, and run in `-p` mode. A hook can deny or rewrite input. [hooks](https://code.claude.com/docs/en/hooks)
- **Built-in safety** ([help](https://support.claude.com/en/articles/12902428-use-claude-in-chrome-safely)): refuses to bypass CAPTCHAs or input "sensitive data"; pauses on high-risk actions. Expect declines on SSN, EEO, DOB fields.
- **Site blocklist:** ~58 domains (banking, payments, crypto, dating, some news, Reddit) per [open-claude-in-chrome README](https://github.com/noemica-io/open-claude-in-chrome). **ATS domains not on it.**
- **Unknown: whether `--chrome` works with `claude -p`.** Not documented; test first.
- **Practitioner evidence:**
  - [cezkid/jobs PR #20](https://github.com/cezkid/jobs/pull/20) (2026-09-24) fills Workday through Claude in Chrome using a **generated JS filler**, not click-by-click. Never clicks Save or Submit. Its `workday.md` records: values only stick after focusout; hidden tabs throttle `setTimeout` to ~once a minute and screenshots come back black; extension tool calls time out at 45 seconds; listbox and "promptOption" widgets need special handling; "Autofill with Resume" mis-parses the PDF.
  - [neonwatty/job-apply-plugin](https://github.com/neonwatty/job-apply-plugin) (112 stars, MIT): Workday, Greenhouse, Ashby, Lever, "stops at final review", ATS flows "unverified".

### Playwright MCP / playwright-cli
- Playwright MCP: Apache-2.0, 37.5k stars, few open issues. `browser_fill_form`, `browser_file_upload`, persistent profiles, `--cdp-endpoint`, `--extension`. [repo](https://github.com/microsoft/playwright-mcp)
- **Playwright Extension** (Chrome Web Store) connects to a chosen existing tab. `PLAYWRIGHT_MCP_EXTENSION_TOKEN` skips the approval prompt; each client gets its own coloured tab group. [extension README](https://github.com/microsoft/playwright/blob/main/packages/extension/README.md)
- README now recommends **playwright-cli** as a skill over MCP for coding agents (token efficiency). CLI supports `attach --extension=chrome`, `attach --cdp`, `--persistent`, `--headed`. [playwright-cli](https://github.com/microsoft/playwright-cli)
- A Chrome Playwright launches runs with automation flags; invisible reCAPTCHA (Greenhouse) may score it worse. Inference, not measured; attaching to real Chrome avoids it.

### Skyvern
- AGPL, 23.1k stars, very active. v1.0.54 covers Jul 2 to Sep 7, 2026.
- **Workday-specific work, Jul–Aug 2026:** deterministic Workday script, "Route Workday login to the agent; keep fill/submit deterministic", "Workday submit: require positive confirmation", repeating-section recovery. PRs [#7778](https://github.com/Skyvern-AI/skyvern/pull/7778), [#7712](https://github.com/Skyvern-AI/skyvern/pull/7712), [#8215](https://github.com/Skyvern-AI/skyvern/pull/8215).
- **But:** open-source tree only shows a `workday_offline` pytest marker; PRs are "sync" merges, so the Workday script appears to be cloud/private (inference).
- Can attach to local Chrome via the `chrome://inspect` toggle. Windows self-host heavy. [repo](https://github.com/Skyvern-AI/skyvern), [pricing](https://www.skyvern.com/pricing)

### Stagehand
- v4 (2026-08-10) runs as an in-browser extension; claims "2x faster than Playwright, ~80% more token efficient". [changelog](https://www.browserbase.com/changelog/stagehand-v4)
- CDP-based; Anthropic supported; MIT. GitHub releases still show 3.7.x; local Chrome behaviour unclear. `mcp-server-browserbase` is **archived**.

### Others
- **Magnitude:** no push since 2026-02-08; org's main repo is now an inference engine. Effectively abandoned.
- **Notte:** SSPL, 2.0k stars, self-reported benchmarks.
- **Steel:** Apache-2.0 local Docker browser API. Not needed.
- **Lightpanda:** headless only, WSL2 on Windows. Wrong tool.
- **agent-browser:** strong CLI with domain allowlist and live dashboard. On Windows, launched browsers die with the daemon; use `--auto-connect`.
- **dev-browser:** no Windows support.
- **nanobrowser:** Apache extension, 13.8k stars. Not examined in depth.

---

## 3. Meta "Muse": what it actually is

"Muse" is **not an open browser-agent framework or dataset.** It is two things:

1. **Muse, the consumer "personal AI agent" app**, announced 2026-09-08 at Meta Connect. iOS, Android, muse.ai web, US only. Free / $20 / $100 tiers. Runs in a Meta-hosted "Muse Secure VM" with its own browser; fills forms, "checks with the person before sensitive actions". "Computer work" on **Mac** announced as coming (TechCrunch 09-23); no Windows mentioned. Developer programme for "connectors", no agent SDK. [Meta newsroom](https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/), [TechCrunch 09-08](https://techcrunch.com/2026/09/08/meta-debuts-its-muse-ai-agent-will-consumers-trust-it/), [TechCrunch 09-23](https://techcrunch.com/2026/09/23/everything-new-coming-to-metas-ai-agent-muse/)
2. **The Muse model family** from Meta Superintelligence Labs. **Muse Spark** proprietary: 1.0 on 2026-07-09, 1.3 on 2026-09-02; on the OpenAI-compatible Meta Model API (US preview) and OpenRouter; open weights promised, no date. **Muse Glimmer** 30B open-weight (Apache-2.0, 2026-08-10). API docs describe **no computer-use or browser tool.** [Wikipedia](https://en.wikipedia.org/wiki/Muse_Spark), [Meta Model API](https://dev.meta.ai/docs/models), [The Register](https://www.theregister.com/ai-and-ml/2026/09/02/zucks-muse-to-spark-joy-with-open-weights-release-soon/5294093)

**Verdict:** not usable here. Runs in Meta's cloud, can't hand off a local tab, isn't Claude, isn't scriptable.

---

## 4. Benchmarks (dated, with caveats)

- **Online-Mind2Web** (300 tasks, 136 sites; [leaderboard](https://leaderboard.steel.dev/leaderboards/online-mind2web/)): Browser Use Cloud 97.0% (Mar 2026, self-reported, custom judge). GPT-5.4 computer use 93% (OpenAI-reported). ABP + Claude Opus 4.6 90.53% (Mar 2026, independently verified). Claude Opus 4.8 84% (Anthropic, 2026-05-28). **2025 human-evaluated baseline** ([arXiv 2504.01382](https://arxiv.org/html/2504.01382v4)): Operator 61.3%, Claude 3.7 CU 56.3%, Browser Use 30.0%. That paper showed WebVoyager inflates scores (51% vs 22%).
- **WebVoyager:** top scores 97–99%; saturated and self-reported.
- **WebArena:** top ~74.3% (2026-06-29). **OSWorld-Verified:** top ~86.1% (2026-09-04).
- **Write and form tasks:** **WebBench** (Halluminate, 2025-05-30; 2,454 tasks): best overall Anthropic CUA 66.0%, but on write tasks best was **Skyvern 2.0 at only 46.6%**. Write tasks needed 2–5x more steps. [Halluminate](https://www.halluminate.ai/blog/benchmark). **FormFactory** (arXiv 2506.01520, Jun 2025): no multimodal model above 5% field accuracy with pure-vision agents. Dated but shows coordinate-only form filling is hard.
- **No published benchmark covers Workday, Greenhouse, Lever or Ashby application filling.**

---

## 5. The "fill, then hand to the human" problem

- **Chrome 136+:** `--remote-debugging-port` / `--remote-debugging-pipe` ignored on default user-data-dir ([Chrome blog, 2025-03-17](https://developer.chrome.com/blog/remote-debugging-port)). Custom `--user-data-dir` or Chrome for Testing still works.
- **Sanctioned path (Chrome 144+):** `chrome://inspect/#remote-debugging` toggle. Attaches to the **running, real profile**. Shows the "controlled by automated test software" banner, and for chrome-devtools-mcp `--autoConnect` a **consent dialog per connection** ([Chrome blog, 2025-12-11](https://developer.chrome.com/blog/chrome-devtools-mcp-debug-your-browser-session)). Used by browser-harness, agent-browser `--auto-connect`, Skyvern. Some clients fail via plain `http://localhost:9222` ([chrome-devtools-mcp #1194](https://github.com/ChromeDevTools/chrome-devtools-mcp/issues/1194)); they need the websocket from `DevToolsActivePort`.
- **Extension approaches** (no debugging port; real profile; human keeps the tab): Claude in Chrome and the Playwright Extension. Cleanest handoffs.
- **Explicit "human takes over" modes:** Skyvern cloud "take control"; Claude in Chrome pauses for login/CAPTCHA; browser-use `keep_alive`, pause/resume, human-input hook; Browserbase/Notte/Steel live views (remote browsers).

---

## 6. Recommendation (this agent's)

**Primary: Claude Code (Max) + Claude in Chrome, plus per-ATS deterministic fillers and a hook-based submit guard.**

1. **Setup:** a dedicated Chrome profile used only for job hunting. Holds Workday tenant logins, Greenhouse/Lever sessions, Gmail for verification codes. Install Claude in Chrome there.
2. **Control flow:** a driver script loops over the jobs, routing by ATS.
   - **Workday:** inject a label-based JS filler (cezkid approach: focusin/value/focusout, `MessageChannel` waits, listbox handling). Claude handles leftovers and custom questions. Beats click-by-click on speed, tokens, reliability; copes with the 45-second tool timeout and hidden-tab throttling.
   - **Greenhouse, Lever, Ashby:** plain agent fill on simple single pages; `file_upload` the matching PDF.
   - **Unknown sites:** general agent fill with a turn budget and a "give up and flag" path.
3. **Submit guard, in layers:** (a) prompt rule; (b) PreToolUse hook denying clicks whose target/description matches submit/apply/send/finish, and any JS containing `.submit(` or `requestSubmit`; (c) injected capture-phase click blocker on submit buttons that only a human bookmarklet unlocks. CDP-dispatched clicks look trusted to the page, so (c) protects against mis-clicks, not an adversarial agent; (b) is the real enforcement. Deny coordinate clicks on review pages.
4. **Handoff:** Claude leaves each tab open in the session's tab group and writes a per-job summary of filled fields plus fields the human must fill. Human reviews and submits in the same real Chrome, which also gives the best reCAPTCHA standing.

**Fallback or companion:** Playwright MCP or playwright-cli with `--extension` on the same profile, driven by `claude -p`.

**If building a custom app outside Claude Code:** Anthropic API `browser_toolset_20260801` with your own Playwright/CDP executor attached to real Chrome, or browser-use with `cdp_url` + `keep_alive`. Both mean API billing and more code.

**Not recommended:** Meta Muse, OpenAI's agent (removed), Lightpanda, Magnitude, Notte, workflow-use, Steel/Browserbase for this case. Skyvern only worth a cloud trial for Workday, and its flow includes submitting.

**Rough cost:** Workday application at 50–150 steps and 5–15k tokens per snapshot, with caching: **$0.5–3 per application on Sonnet 5 via API**, ~2x on Opus 5.5. Deterministic filler cuts this sharply. On Max it draws plan limits; 100 applications could hit rate limits.

---

## 7. Risks and unknowns

1. **Whether `claude -p --chrome` works headless:** not documented. Test first.
2. **Claude in Chrome's tab-closing behaviour** (v2.1.280; `/clear` closes the group) could close a filled form.
3. **Workday per-tenant accounts:** new account and email verification per company, password creation, which Claude won't automate ("won't fill passwords"). Session lifetime of filled-but-unsubmitted Workday forms unknown; "Save and Continue" saves each page into the draft.
4. **Refusals on sensitive fields** (SSN, DOB, demographics) and CAPTCHAs.
5. **Hook guard limits:** coordinate clicks and `javascript_exec` can bypass text matching. Restrict tools on review pages.
6. **ATS ToS and anti-bot:** invisible reCAPTCHA on Greenhouse; a blog reports new ATS CAPTCHAs in May 2026 breaking several tools.
7. **Benchmark hype:** self-reported, custom judges. No ATS benchmark; measure on 10 real applications before scaling.
8. **Prompt injection from job pages:** Anthropic reports under 0.08% attack success for Opus 4.8 in Chrome, not zero.
9. **Agent SDK auth:** third-party products shouldn't use claude.ai login; Chrome integration refuses API-key auth. Personal use through `claude` CLI login works.
10. **Unverified:** Stagehand v4 on local Chrome; whether Skyvern OSS includes the Workday script; Simplify's accuracy; browser-use cost on Workday.
