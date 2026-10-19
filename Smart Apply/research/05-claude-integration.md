# Claude integration options for a browser-based job-application filler (September 2026)

## Summary

- **Recommended path:** the Claude Agent SDK in Python, with your own in-process Playwright tools, a guard inside the tool code, a PreToolUse hook as a second guard, and API-key billing. Use `claude -p` with Playwright MCP to prototype quickly on the Max plan. Move to the raw API loop with Anthropic's `browser_toolset_20260801` only if you later want Anthropic's trained browser tool contract and are willing to write the executor.
- **Max billing:** the Agent SDK and `claude -p` currently draw from the Max plan's usage limits. Anthropic planned to move them onto a separate monthly credit on June 15, 2026, then paused that change the day it was due. The docs still tell Agent SDK developers to use an API key, and Max limits assume "ordinary, individual usage". An overnight batch should run on the API key.
- **Models:** Sonnet 5 (`claude-sonnet-5`, $2/$10 per MTok) to drive the form. Opus 5.5 (`claude-opus-5-5`, $4/$20) for short-answer writing. Avoid Haiku 4.5: 200K context, not in the browser-toolset model list, retirement "not sooner than Oct 15, 2026".
- **Cost per 6-page Workday application:** about $1.00–1.70 on Sonnet 5 and $1.75–2.50 on Opus 5.5 with prompt caching. Without caching $9–19, so caching is essential.
- **Human review before submit:** Anthropic's docs recommend a human confirm consequential actions, in the executor. Claude Code also has a PreToolUse hook decision `"defer"` that pauses a `claude -p` run at a tool call and lets you resume the same session later.
- **Claude in Chrome** is interactive and tied to the personal browser. Not a good fit for unattended overnight batches (section 4).

---

## 1. Claude Agent SDK (Python and TypeScript)

**Current state.** "Claude Code as a library": same tools, agent loop, context management, hooks, sessions, subagents and MCP support. Python package `claude-agent-sdk` (Python 3.10+), TypeScript `@anthropic-ai/claude-agent-sdk` (Node 18+). Both bundle a native Claude Code binary; Windows x64 supported via `pip`. TypeScript supports more hook events (`PostToolBatch`, `SessionStart`/`SessionEnd`, `StopFailure`). Python has `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `UserPromptSubmit`, `Stop`, `SubagentStart`/`SubagentStop`, `PreCompact`, `PermissionRequest`, `Notification`. ([overview](https://code.claude.com/docs/en/agent-sdk/overview), [quickstart](https://code.claude.com/docs/en/agent-sdk/quickstart), [hooks](https://code.claude.com/docs/en/agent-sdk/hooks))

**Custom tools.** `@tool(name, description, schema)` in Python or `tool(...)` with Zod in TypeScript, wrapped in `create_sdk_mcp_server(...)` and passed in `mcp_servers`. Full name `mcp__{server}__{tool}`.
- `tools=[]` removes every built-in tool, so Claude can only use yours.
- `allowed_tools` auto-approves but does not restrict.
- A bare name in `disallowed_tools` removes that tool from context.
- Tool search is on by default and defers loading of SDK MCP tool schemas.
- Python `@tool` forwards only `content` and `is_error`; `structuredContent` needs TypeScript or a standalone MCP server.

([custom tools](https://code.claude.com/docs/en/agent-sdk/custom-tools))

**Hooks.** A `PreToolUse` callback returns `hookSpecificOutput.permissionDecision`: `"allow"`, `"deny"`, `"ask"` or `"defer"`. Any `deny` wins. `updatedInput` can rewrite arguments. `permissionDecisionReason` on a deny is shown to Claude. `"defer"` ends the query so you can resume later. ([hooks](https://code.claude.com/docs/en/agent-sdk/hooks), [defer](https://code.claude.com/docs/en/hooks))

**Billing.**
- Docs: "Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK. Use the API key authentication." ([overview](https://code.claude.com/docs/en/agent-sdk/overview))
- Legal page: Agent SDK developers "should use API key authentication"; Max limits "assume ordinary, individual usage of Claude Code and the Agent SDK". ([legal](https://code.claude.com/docs/en/legal-and-compliance))
- Right now SDK and `claude -p` usage on Max counts against subscription limits. The planned separate credit ($100/month on Max 5x, $200/month on Max 20x) was paused June 15, 2026. ([help center](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan), [The New Stack](https://thenewstack.io/anthropic-pauses-claude-agent-sdk-subscription-change/))
- For an overnight bulk job, use `ANTHROPIC_API_KEY`, set explicitly.

**Cost controls** (`ClaudeAgentOptions`): `max_turns`, `max_budget_usd` (client-side estimate, per call). Hitting a limit ends the run with `ResultMessage.subtype` `error_max_turns` / `error_max_budget_usd`. `total_cost_usd` and `usage` on the result. Also `model`, `fallback_model`, `effort` (`low`..`max`). ([Python reference](https://code.claude.com/docs/en/agent-sdk/python))

**Structured output.** `output_format={"type":"json_schema","schema":...}`; result in `ResultMessage.structured_output`. JSON Schema draft-07; SDK re-prompts on mismatch; failure subtype `error_max_structured_output_retries`. Treat "`success` but `structured_output` is None" as failure. ([structured outputs](https://code.claude.com/docs/en/agent-sdk/structured-outputs))

### Code sketch: Agent SDK agent that owns a Playwright browser and guards the submit button

Option names are from the docs; not run, Playwright details illustrative.

```python
# pip install claude-agent-sdk playwright ; playwright install chromium
import asyncio, re, json, pathlib
from typing import Any
from playwright.async_api import async_playwright, Page
from claude_agent_sdk import (tool, create_sdk_mcp_server, query, ClaudeAgentOptions,
                              HookMatcher, ResultMessage)

FORBIDDEN = re.compile(r"\b(submit|apply|send application|finish|confirm|sign and submit)\b", re.I)
UPLOAD_DIR = pathlib.Path(r"C:\SmartApply\docs").resolve()
state: dict[str, Page] = {}

def loc(role: str, name: str):
    return state["page"].get_by_role(role, name=name, exact=True)

@tool("snapshot", "Return the ARIA (accessibility) snapshot of the current page. Use role+name from it to target elements.", {})
async def snapshot(args):
    snap = await state["page"].locator("body").aria_snapshot()
    return {"content": [{"type": "text", "text": snap[:40000]}]}

@tool("fill", "Fill a textbox/combobox identified by ARIA role and accessible name.",
      {"role": str, "name": str, "value": str})
async def fill(args):
    await loc(args["role"], args["name"]).fill(args["value"])
    return {"content": [{"type": "text", "text": f"filled {args['name']}"}]}

@tool("click", "Click an element by ARIA role and accessible name. Final-submit buttons are blocked.",
      {"role": str, "name": str})
async def click(args):
    el = loc(args["role"], args["name"])
    # In-tool guard: inspect the REAL element, not the model's description of it
    label = " ".join(filter(None, [await el.inner_text(), await el.get_attribute("aria-label") or "",
                                   await el.get_attribute("data-automation-id") or ""]))
    if FORBIDDEN.search(label) and not re.search(r"save|next|continue", label, re.I):
        return {"content": [{"type": "text", "text": f"BLOCKED: '{label}' looks like final submit. Stop and report ready_for_review."}],
                "is_error": True}
    await el.click()
    return {"content": [{"type": "text", "text": f"clicked {label}"}]}

@tool("upload", "Attach a file (resume/cover letter) to a file input by accessible name.",
      {"name": str, "filename": str})
async def upload(args):
    p = (UPLOAD_DIR / args["filename"]).resolve()
    if UPLOAD_DIR not in p.parents or not p.exists():
        return {"content": [{"type": "text", "text": "file not allowed"}], "is_error": True}
    await state["page"].get_by_label(args["name"]).set_input_files(str(p))
    return {"content": [{"type": "text", "text": f"uploaded {p.name}"}]}

browser = create_sdk_mcp_server(name="browser", version="1.0.0", tools=[snapshot, fill, click, upload])

async def guard(input_data, tool_use_id, context):   # belt-and-braces second guard
    name = input_data["tool_input"].get("name", "")
    if FORBIDDEN.search(name) and not re.search(r"save|next|continue", name, re.I):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": "Final submission is reserved for the human reviewer."}}
    return {}

REPORT = {"type": "object", "required": ["status", "pages_completed", "open_questions"],
          "properties": {"status": {"enum": ["ready_for_review", "blocked", "failed"]},
                         "pages_completed": {"type": "integer"},
                         "open_questions": {"type": "array", "items": {"type": "string"}},
                         "answers_written": {"type": "array", "items": {"type": "object"}}}}

async def run_one(url: str, profile_json: str):
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(r"C:\SmartApply\profile", headless=False)
        state["page"] = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await state["page"].goto(url)
        opts = ClaudeAgentOptions(
            system_prompt=open("system_prompt.md").read(),      # keep byte-stable for caching
            tools=[],                                            # no Bash/Read/Edit
            mcp_servers={"browser": browser},
            allowed_tools=["mcp__browser__*"],
            permission_mode="dontAsk",
            setting_sources=[],                                  # don't load ~/.claude hooks/CLAUDE.md
            hooks={"PreToolUse": [HookMatcher(matcher="mcp__browser__click", hooks=[guard])]},
            model="claude-sonnet-5", effort="medium",
            max_turns=80, max_budget_usd=3.00,
            output_format={"type": "json_schema", "schema": REPORT},
        )
        async for m in query(prompt=f"Candidate profile:\n{profile_json}\n\nFill every page; "
                                    "click Save/Next between pages; never submit. End with the report.",
                             options=opts):
            if isinstance(m, ResultMessage):
                print(m.subtype, m.total_cost_usd, json.dumps(m.structured_output))
        # leave the browser/profile in place (or save a draft) for morning review

asyncio.run(run_one("https://example.wd1.myworkdayjobs.com/...", open("profile.json").read()))
```

Why the guard lives inside the tool: a PreToolUse hook only sees the arguments Claude chose. The tool handler sees the actual DOM element, so it can't be fooled by a mislabelled description or a prompt injection. Pressing Enter in a text field can submit a form, so don't expose a raw key-press tool.

---

## 2. Headless `claude -p` with Playwright MCP or a custom MCP server

| Control | Behaviour |
|---|---|
| `--allowedTools` | Auto-approves listed tools; does not restrict Claude to them. Rule syntax such as `mcp__playwright__browser_click`. |
| `--disallowedTools` | Bare name removes the tool from context (`"mcp__*"` removes every MCP tool). Scoped rule denies only matching calls. |
| `--tools` | Restricts which built-ins are available. |
| `--max-turns`, `--max-budget-usd` | Print mode only. Subagent spend counts. Totals restored by `--resume` don't count. |
| `--output-format json` | Returns `total_cost_usd` and `session_id`. |
| `--json-schema` | Validated output in `structured_output`. |
| `--permission-mode dontAsk` / `--permission-prompts none` | For unattended runs. |
| `--bare` | Skips hooks, `.mcp.json`, CLAUDE.md. Requires `ANTHROPIC_API_KEY`: "bare mode doesn't use your subscription login". Pass MCP with `--mcp-config`, settings with `--settings`. |

([headless](https://code.claude.com/docs/en/headless), [CLI reference](https://code.claude.com/docs/en/cli-reference))

**Resuming for a "fix" loop.** `--resume <session_id>` works from any directory; `--continue` resumes latest; `--fork-session` branches. With `defer`, a PreToolUse hook makes the run exit with `stop_reason: "tool_deferred"` and a `deferred_tool_use` payload; resume later with `claude -p --resume <id>`. Limits: only works when the turn contains a single tool call; session file kept 30 days by default. ([defer](https://code.claude.com/docs/en/hooks))

The browser is a separate MCP process, so it does not survive the pause. Use a persistent profile (`--user-data-dir`) or `--cdp-endpoint` pointing at a browser you keep running.

**Per-project MCP.** `.mcp.json` in project root is "project" scope, supports `${VAR}` expansion. Under `-p` it loads without prompting, but since v2.1.196 only after the folder was trusted once interactively. On Windows wrap the command with `cmd /c`. ([MCP](https://code.claude.com/docs/en/mcp))

```json
// .mcp.json
{ "mcpServers": { "playwright": { "command": "cmd", "args": ["/c","npx","@playwright/mcp@latest",
   "--user-data-dir","C:\\SmartApply\\profile","--image-responses","omit",
   "--allowed-origins","https://*.myworkdayjobs.com;https://*.workday.com"] } } }
```

```json
// .claude/settings.json: command hook; exit 0 + JSON deny, or exit 2 to block
{ "hooks": { "PreToolUse": [ { "matcher": "mcp__playwright__browser_click|mcp__playwright__browser_press_key",
    "hooks": [ { "type": "command", "command": "python .claude/hooks/no_submit.py" } ] } ] } }
```

```
claude -p "$(cat task.md)" --model claude-sonnet-5 --effort medium ^
  --allowedTools "mcp__playwright__*" ^
  --disallowedTools "mcp__playwright__browser_run_code_unsafe mcp__playwright__browser_evaluate Bash Edit Write" ^
  --permission-mode dontAsk --max-turns 80 --max-budget-usd 3 ^
  --output-format json --json-schema "{...report schema...}"
```

Deny `browser_run_code_unsafe`: the README calls it "RCE-equivalent" and it could click Submit directly. ([Playwright MCP README](https://github.com/microsoft/playwright-mcp))

**Pros/cons vs Agent SDK:**
- **Pros:** almost no code; reuses the existing headless setup. Playwright MCP is mature (`browser_fill_form`, `browser_file_upload`, `browser_find`). Can run on the Max login, but not in `--bare`.
- **Con, weaker guard:** a hook only sees Claude's `element` description and a snapshot `target` ref.
- **Con, token overhead:** full Claude Code system prompt loads unless `--bare`; less control over context pruning.
- **Con, per-app state:** each application orchestrated from the shell.
- **Con, submit paths:** Enter can submit a form; guard `browser_press_key` too.

---

## 3. Computer use (screenshots) vs accessibility-tree tools

**Model support.** `computer_toolset_20260801` is GA, no beta header. Supports Fable 5.1/5, Mythos 5.1/5, Opus 5.5, Opus 5, Sonnet 5, Opus 4.8. Older `computer_20251124` covers Opus 4.7/4.6/4.5 and Sonnet 4.6, errors on newer models.

**Costs:** screenshots ~1,000–1,800 input tokens each; toolset definition ~4,500 tokens per request; adding/removing images invalidates cached message blocks. ([computer use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool), [pricing](https://platform.claude.com/docs/en/about-claude/pricing), [caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching))

**Anthropic's dedicated browser toolset, `browser_toolset_20260801`.** GA on the Claude API, same model list as the computer toolset except Opus 4.8. Client-side: you execute the calls with Playwright. 27 member tools by default, including `read_page` (accessibility tree with `ref_N` handles), `find`, `form_input`. `file_upload` optional, off by default. Overhead ~6,600 tokens.

The docs recommend a hybrid:

> "Prefer accessibility trees where pages have usable structures; references survive layout shifts … fall back to coordinates for canvas, embedded media, virtualized lists, cross-origin iframes … a tree read of a typical page often costs fewer input tokens than a screenshot."

([browser use tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/browser-use-tool))

**Playwright MCP snapshot mode.** `browser_snapshot` returns an accessibility tree with refs. Options: `--snapshot-mode full|none`; `browser_snapshot` accepts `depth` and a target; `browser_find` searches cheaply; `--caps vision` adds coordinate tools; `--image-responses omit` strips screenshots. Microsoft positions the MCP server for long-running autonomous work. ([README](https://github.com/microsoft/playwright-mcp))

**Verdict for Workday:** accessibility tree first, with `form_input`/`fill`. Screenshot only when validation errors or custom widgets (date picker, searchable multiselect) can't be understood from the tree.

---

## 4. Claude in Chrome

- Chrome extension, GA August 26, 2026 on all paid plans. Acts in the logged-in browser, with safety classifiers that can auto-approve actions. ([GA post](https://claude.com/blog/claude-in-chrome-generally-available), [help](https://support.claude.com/en/articles/12012173-get-started-with-claude-in-chrome))
- Can schedule recurring tasks, but only "as long as Chrome is open"; each run uses plan quota.
- Claude Code drives it with `claude --chrome`. Needs a `/login` subscription session; API keys and `setup-token` disable it. Docs never show it under `-p`; flow is interactive, pauses for logins and CAPTCHAs. ([Claude Code + Chrome](https://code.claude.com/docs/en/chrome))
- Safety guidance: won't bypass CAPTCHAs or input "sensitive data"; asks before entering sensitive information. Job applications involve address, work authorisation and EEO answers, so expect frequent pauses. ([safety](https://support.claude.com/en/articles/12902428-use-claude-in-chrome-safely))
- **Fit:** good for assisted, interactive filling of one application at a time. Poor for "many applications overnight": no API, real profile, no hard guard, one visible browser.

---

## 5. Models, pricing, caching and cost per application

USD per million tokens. ([models](https://platform.claude.com/docs/en/about-claude/models/overview), [pricing](https://platform.claude.com/docs/en/about-claude/pricing))

| Model | ID | Input | Cache write (5 min) | Cache read | Output | Notes |
|---|---|---|---|---|---|---|
| Fable 5.1 | `claude-fable-5-1` | $10 | $12.50 | $0.25 | $50 | Top tier; overkill here |
| Opus 5.5 | `claude-opus-5-5` | $4 | $5 | $0.20 | $20 | Anthropic's recommended default; effort defaults to `medium`; thinking can't be turned off |
| Opus 5 | `claude-opus-5` | $5 | $6.25 | $0.50 | $25 | Legacy |
| Sonnet 5 | `claude-sonnet-5` | $2 | $2.50 | $0.20 | $10 | Standard price |
| Haiku 4.5 | `claude-haiku-4-5` | $1 | $1.25 | $0.10 | $5 | 200K context; min cacheable prompt 4,096 tokens; retirement not sooner than Oct 15, 2026 |

**Caching.** Tool results appended to the conversation do not invalidate the cache. Automatic top-level `cache_control` moves the breakpoint forward. Keep system prompt, tool list and candidate profile byte-identical across applications. Use a 1-hour cache (2x write price) if applications are spaced more than 5 minutes apart. Batch API's 50% discount suits generating short answers in bulk ahead of time, not interactive browser loops.

**Cost estimate**, one application, 40 tool calls, 5k-token snapshots, 8k stable prefix, ~500 output tokens per turn incl. thinking, 3k tokens of written answers. Estimates, not measurements.

| Model | A: full history, no cache | B: full history, cached | C: cached, older snapshots pruned to stubs |
|---|---|---|---|
| Fable 5.1 | $47.25 | $5.10 | $4.20 |
| Opus 5.5 | $18.90 | $2.48 | $1.76 |
| Opus 5 | $23.62 | $4.19 | $2.41 |
| **Sonnet 5** | $9.45 | $1.68 | **$0.96** |
| Haiku 4.5 | $4.73 (context ~222K, over its limit) | $0.84 | $0.48 |

In scenario C cost is dominated by writing each new snapshot to the cache. To cut further:
- Scoped snapshots (current form section, or interactive elements only), aiming for 1–2k tokens.
- Fill several fields in one call.
- Script the predictable Workday widgets; use Claude only for unfamiliar questions. Can bring a typical application to ~$0.10–0.30.

The Agent SDK and `claude -p` add Claude Code's own system prompt unless `--bare` or `tools=[]` with a custom `system_prompt`.

**Model split:** Sonnet 5 at `medium` effort to navigate and fill. Opus 5.5 for short-answer text as a separate Messages API call with structured output. The "advisor" tool pattern (Sonnet executor, Opus advisor) is also documented.

---

## 6. Anthropic guidance on browser agents and human review

- **Browser use tool docs:** isolated browser with fresh profile, domain allowlist re-checked after redirects, http/https only. "Have a human confirm consequential actions … and make that check in your executor before each call, because one turn can carry several." Run several calls in order, stop at first failure. ([browser use tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/browser-use-tool))
- **Computer use docs:** human confirmation for anything with real-world consequences or needing consent. Prompt-injection classifiers on by default, "won't be ideal … without a human in the loop". ([computer use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool))
- **Reference code:** `anthropics/claude-quickstarts/browser-use-demo`: Playwright tool with `read_page` refs, `form_input`, screenshots. Targets Claude 4.5, not production. ([repo](https://github.com/anthropics/claude-quickstarts/blob/main/browser-use-demo/README.md))
- **Principles:** "keep humans in control … particularly before high-stakes decisions" ([framework](https://www.anthropic.com/news/our-framework-for-developing-safe-and-trustworthy-agents)). Prompt injection not solved: "no browser agent is immune" ([research](https://www.anthropic.com/research/prompt-injection-defenses)).
- **Mechanisms for the review gate:** no submit tool at all plus executor guard (strongest); PreToolUse `deny`; PreToolUse `defer` (pause, resume after review); `canUseTool` callback or `--permission-prompt-tool` for an approval UI.

---

## Which integration path to use

| | Agent SDK (Python) | `claude -p` + Playwright MCP | Raw API tool loop |
|---|---|---|---|
| Submit guard | Strongest: in-process tool inspects the real element | Hook sees only Claude's arguments | Strong: your executor inspects the element |
| Code to write | Moderate | Very little | Most: loop, caching, context |
| Max-plan billing | Draws on plan now, but docs say use an API key | Works on login, not with `--bare` | API key only |
| Token efficiency | Good with `tools=[]` and custom prompt | Worst unless `--bare` | Best |
| Resume and review | `resume`, `defer`, sessions | `--resume`, `defer` | You build it |
| Anthropic-trained browser toolset | Not documented in SDK | No | Yes: `browser_toolset_20260801` |

**Pick the Agent SDK in Python** as the main path:
1. Browser lives in the same process as the tools, so the no-submit guard checks the real DOM element and cannot be talked around.
2. Loop, sessions, hooks, `max_budget_usd`/`max_turns` and schema-validated reports for free.
3. Fits a Python job queue that works through applications overnight and writes a review report for each.

Run the overnight batch on the API key. Use `claude -p` with Playwright MCP on Max to explore new Workday tenants and prototype prompts. Consider the raw API with `browser_toolset_20260801` later if Anthropic's trained browser contract works better on Workday's custom widgets.
