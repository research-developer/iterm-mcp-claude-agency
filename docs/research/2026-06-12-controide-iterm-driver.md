# ControIDE-style iTerm Driver for Claude Code

**Date:** 2026-06-12
**Status:** Research + architecture proposal (no production code beyond this doc)
**Author:** research investigation

## The vision (verbatim intent)

> We want to be able to operate Claude Code using iTerm. It's going to be ~95%
> iTerm functionality along with Claude Hooks. We need triggers and a highly
> custom profile. The goal is to have a locally running API that we can operate
> through a web browser which can forward the contents of Claude Code along
> with any questions and prompts it has. The catch is that we need to present
> multiple-choice options for ALL responses, because we want to control Claude
> Code using a gaming controller in combination with dictation.

So: a human drives a Claude Code session running inside an iTerm pane; a local
web app mirrors what Claude Code shows and — critically — turns every point
where Claude Code needs input into a **multiple-choice** selection, navigable
by **gamepad + dictation**, with the choice injected back into the iTerm pane.

---

## Part 1 — ControIDE (the prior art)

**Location:** `/Users/psentro/research-developer/ControIDE` (found; small early-stage repo, last commit May 2026).

### What it is

ControIDE is a working MVP of *exactly the inner loop* this vision needs,
scoped down to one event. It is a Claude Code controller built on the **Stop
hook**: when Claude finishes a turn, a Tk popup appears showing a summary of
what Claude just said plus a multiple-choice list. Picking a choice sends its
text back as the next instruction via the hook returning
`{"decision": "block", "reason": <choice text>}`.

Its README frames the lineage explicitly:

> Inspired by Lebski/Claude-Code-Controller, but instead of simulating keyboard
> events at the OS level, ControIDE plugs directly into the Claude Code agent
> loop.

That sentence is the key architectural insight ControIDE contributes: **don't
screen-scrape and fake keystrokes — hook the agent loop and return structured
decisions.**

### Architecture (as built)

```
hook.stop ── parses transcript, builds choices, spawns popup, emits Stop JSON
   │  subprocess (stdin = payload, stdout = selected text)
   ▼
ui.popup ── Tk window + radio list, driven by …
   │
   ▼
input.MockKeyboardController ── Action enum dispatch (MOVE_PREV/NEXT/SELECT/CANCEL)
   │
   ▼
model.ChoiceList ── cursor with modulo wrap
```

Concrete pieces (all read for this report):

- `src/controide/hook/stop.py` — Stop-hook entrypoint. Reads `transcript_path`
  from the hook stdin JSON, extracts the last assistant text, builds choices,
  spawns the popup as a subprocess (stdin = payload JSON, stdout = chosen text,
  120s timeout), and on a non-empty selection emits
  `{"decision":"block","reason":selection}` so Claude continues with that text
  as its new instruction. Empty selection → `{}` (let Claude stop).
- `src/controide/transcript.py` — `parse_last_assistant(path)`: streams the
  JSONL transcript, returns the concatenated `text` blocks of the last
  `type=="assistant"` entry. This is the "forward the contents of Claude Code"
  primitive, sourced from the transcript rather than the screen.
- `src/controide/ui/popup.py` — Tk window: scrollable summary box + radio list +
  a "Custom…" entry box (the dictate/type escape hatch) + Submit/Cancel.
  Keyboard: ↑↓/k/j navigate, ↵ submit, esc cancel. Returns resolved text on
  stdout.
- `src/controide/model.py` — `Choice(id,label,text)` and `ChoiceList(summary,
  choices,cursor)` with `move(delta)` modulo-wrap cursor and `selected()`.
- `src/controide/choices.py` — `default_choices()`: Continue / Refine / Stop
  here / Custom…. The first two carry canned `text`; Stop and Custom carry empty
  `text` (Stop = let it stop; Custom = use typed text).
- `src/controide/input/base.py` — `Action` enum (`MOVE_PREV`, `MOVE_NEXT`,
  `SELECT`, `CANCEL`) and a `Controller` Protocol with `handle_key(key) ->
  Optional[Action]`.
- `src/controide/input/mock_keyboard.py` — `MockKeyboardController` mapping
  keysyms to `Action`s, plus a headless `drive(choice_list, keys)` for tests.
- `hooks/stop.sh` + `.claude/settings.json` — wires the Stop hook
  (`"$CLAUDE_PROJECT_DIR"/hooks/stop.sh`, 600s timeout).

### Intended functionality (the aspirational roadmap)

ControIDE's README roadmap *is* the rest of this vision, already named:

- Real gamepad input (DualSense via `hidapi`)
- Dictation / transcription (whisper.cpp / MLX)
- Scrollable summaries
- Reroll with guided input
- Conversation rollback / branching via the Claude Agent SDK `SessionStore`
- Persistent daemon

### What's reusable

- **The decision-injection pattern** (Stop hook → `decision:block` → reason as
  next turn). This is the cleanest way to "send a choice back into Claude Code"
  and avoids fragile TTY keystroke faking for the *turn-boundary* case.
- **The `Controller`/`Action` abstraction** — input source is decoupled from
  the choice-list mechanics. A `GamepadController` and a `DictationController`
  drop in behind the same `handle_key`/`Action` interface (or a slightly
  generalized `handle_event -> Action`). This is the seam the gamepad+dictation
  requirement plugs into.
- **The `Choice`/`ChoiceList` model** — minimal, correct, testable.
- **Transcript parsing** — reading the last assistant message from the JSONL
  transcript is more reliable than screen-scraping for *content*, and Claude
  Code hands you `transcript_path` for free.

### What it is NOT (gaps vs. the vision)

- Only handles the **Stop** boundary (end of turn). It does **not** intercept
  permission prompts, plan approval, or `AskUserQuestion` — the other places
  Claude Code asks for input.
- UI is **local Tk**, not a **browser** app. The vision wants a web UI reachable
  over a local HTTP API.
- Choices are a **fixed list**, not synthesized per-question. The "multiple
  choice for ALL responses, including open-ended" requirement is unmet.
- **No iTerm involvement at all** — ControIDE runs Claude Code in whatever
  terminal; it doesn't use iTerm control, triggers, or profiles. The "~95% iTerm
  functionality" requirement is entirely greenfield in ControIDE.
- **No gamepad/dictation** yet (roadmap only).

**Bottom line:** ControIDE proves the hardest *correctness* point (hook the
agent loop, return a decision) and gives a clean input-abstraction seam. This
repo (iterm-mcp) provides everything ControIDE's roadmap is missing: iTerm
control, triggers, profiles, a local HTTP+SSE server, and screen monitoring.
The proposal below fuses the two.

---

## Part 2 — What this repo already provides

This repo (`iterm-mcp-claude-agency`) is an iTerm2 MCP server with multi-agent
orchestration. Against the vision, here is the foundation-vs-greenfield map.

### iTerm session control — STRONG foundation

`core/session.py` (`ItermSession`) and `core/terminal.py` (`ItermTerminal`):

- `send_text(text, execute=True)` and `execute_command(command, use_encoding)` —
  inject text/commands into a pane. **This is the "send the choice back" path.**
- `send_special_key(key)` — named keys: enter/return/tab/escape/esc/space/
  backspace/delete/up/down/right/left/home/end. **This is what answers Claude
  Code's *in-TTY* prompts** (e.g. its permission menu where you press ↑/↓ + ↵, or
  "1/2/3").
- `send_control_character(c)` — Ctrl+C/Ctrl+Z etc. (`suspend()`/`resume()` wrap
  Ctrl+Z / `fg`).
- `get_screen_contents(max_lines, from_end)` — read the pane buffer.
- `expect(patterns, timeout, ...)`, `wait_for_prompt(...)`,
  `send_and_expect(...)`, `interact_until(prompt_pattern, responses, ...)` —
  **a full pattern/response engine already exists** for driving interactive
  CLIs. `interact_until` in particular is a generic "see prompt → send mapped
  response" loop — directly relevant.
- Session creation/splitting: `create_window`, `create_tab`,
  `create_split_pane`, `split_session_directional`, plus lookup by
  id/name/persistent-id and `focus_session`.

### Screen monitoring — STRONG foundation

`core/session.py`: polling-based monitor (not subscription, deliberately, due to
historical WebSocket-frame issues).

- `start_monitoring(update_interval=0.5)` / `stop_monitoring()` /
  `is_monitoring`.
- `add_monitor_callback(cb)` / `remove_monitor_callback(cb)` — callbacks fire
  with the new screen contents whenever the buffer changes (diffed against the
  previous snapshot). **This is the live "forward the contents of Claude Code"
  stream for the web UI.**

`core/iterm_path_monitor.py`: watches the iTerm2 `path` SESSION variable via
`iterm2.VariableMonitor` / `EachSessionOnceMonitor`; on change routes through
`AgentHookManager.on_path_changed(...)`, applies styling, assigns teams, and
sets the `user.claude_session_id` iTerm user variable (shell-integration bridge
between an iTerm session and a Claude Code session id).

### Subscriptions / output capture — STRONG foundation

`iterm_mcpy/tools/subscribe.py` + `core/flows.py` (EventBus):

- Arm a subscription with a **regex** `pattern`, optional `target_session_id` /
  `target_agent`, and a `notify_agent`/`notify_level`. When the pattern matches
  output, it can fire a workflow `event_name` and push a notification to another
  agent (cross-agent feed, PR #125).
- `iterm_mcpy/tools/sessions.py` wires the monitor callback into
  `event_bus.process_terminal_output(session_id, output, agent_name)` which runs
  all subscriptions and triggers `terminal_output` workflow events.

**This is the pattern engine for "detect that Claude Code is asking
something."** A subscription whose regex matches Claude Code's prompt chrome
(e.g. the permission box, "Do you want to proceed?", the `❯` selector) fires an
event we can turn into a choice card.

### iTerm2 triggers — STRONG foundation (already pointed at a local API)

`scripts/install_iterm2_triggers.py` installs 5 triggers; the load-bearing ones:

| Regex | Action |
|---|---|
| `^⏺ ` | Invoke script function `capture_claude_response_rpc(session.id)` |
| `^⏺ ` | Set Mark |
| `^⏺ (.+)` | Capture Output (Toolbelt) |
| `^⏺.*([Ee]rror\|[Ff]ailed\|Exception)` | Highlight red |
| `^⏺.*([Ee]rror\|[Ff]ailed\|Exception)` | Post Notification "Claude Error" |

`scripts/iterm2_capture_response.py` registers `capture_claude_response_rpc` via
`@iterm2.RPC` (under `iterm2.run_forever`). On the `⏺` trigger it reads the
response block from the screen, classifies it (tool call / success / error /
warning by color + text), and **POSTs it to a local HTTP API at
`http://localhost:9999/api/db/responses`.** This is precedent for exactly the
forwarding the vision wants — already trigger-driven and already targeting a
local web server.

### Profiles — STRONG foundation

`core/profiles.py` (`ProfileManager`, `TeamProfile`, `HSLColor`,
`ColorDistributor`) writes iTerm2 **Dynamic Profiles** to
`~/Library/Application Support/iTerm2/DynamicProfiles/iterm-mcp-profiles.json`
(stable GUIDs, tab color, badge text, tags, "shell corrections disabled" initial
text). Sessions get visual properties applied via
`session.async_set_profile_properties(...)`. `docs/PROFILES*.md` document the
full property surface (tab color, badge, cursor, etc.). **The "highly custom
profile" requirement has a generator and an apply path already.**

### Hooks (internal) — PARTIAL foundation (note the naming clash)

`core/agent_hooks.py` and `core/service_hooks.py` are **internal
agent-lifecycle hooks**, NOT Claude Code hooks. `AgentHookManager` fires on
`AGENT_STARTED`/`DIRECTORY_CHANGED`/`TEAM_ASSIGNED`/`STYLE_APPLIED`/
`SESSION_ID_PASSED`, driven by repo `.iterm/hooks.json`. Useful for
styling/team logic and for the iTerm↔Claude-session-id bridge, but the
**Claude Code hooks** (Stop/PreToolUse/Notification/…) are a separate,
greenfield wiring we must add (ControIDE-style).

### Local HTTP API + SSE + store — STRONG foundation (this is the host)

`core/dashboard.py` is already an asyncio HTTP server (default port **9999**,
started via the `telemetry` tool — `iterm_mcpy/tools/telemetry.py`,
`POST+TRIGGER /telemetry`):

- Serves `static/dashboard.{html,css,js}`.
- **SSE `/events`** endpoint with a broadcast loop (`_sse_clients`,
  `_broadcast_loop`, `_send_sse_event`) — live push to the browser already
  exists.
- REST: `/api/state`, `/api/focus?agent=`, **`/api/send?agent=&command=`**
  (POST body `{command}` → `session.send_text(command + "\n")` — *the inject
  path is already implemented and HTTP-reachable*), plus `/api/db/responses`
  (GET list / **POST add** — what the trigger posts to), `/api/db/agents`,
  `/teams`, `/services`, `/repos`, `/stats`, `/search`.
- `core/dashboard_db.py` — SQLite with FTS5 full-text search over captured
  `responses` (the captured-Claude-output store).

> Note: this worktree predates the singleton streamable-HTTP daemon described in
> the project CLAUDE.md (PR #129, ports 12340–12349, `/health`). When this branch
> merges forward, the *daemon* becomes the natural long-lived host for the
> control API and the *dashboard* server is the existing template for the
> HTTP+SSE+inject surface. Either way the HTTP+SSE+inject+store machinery already
> exists in-repo and does not need to be invented.

### Foundation vs. greenfield scorecard

| Vision requirement | Status | Where |
|---|---|---|
| Run Claude Code in an iTerm pane, control it | Foundation | `core/terminal.py`, `core/session.py` |
| Inject a chosen answer back into the pane | **Done** | `session.send_text/send_special_key`; `/api/send` |
| Live-forward Claude Code's screen to a browser | Foundation | screen monitor callbacks + dashboard SSE `/events` |
| Forward Claude Code's *content* reliably | Foundation | transcript parse (ControIDE) + `⏺` trigger capture |
| Detect "Claude Code is asking something" | Foundation | subscribe/EventBus regex; iTerm triggers; CC hooks (new) |
| Local HTTP API to operate from a browser | Foundation | `core/dashboard.py` (HTTP+SSE+REST) / future daemon |
| Custom iTerm profile | Foundation | `core/profiles.py` + Dynamic Profiles |
| Custom trigger set | Foundation | `scripts/install_iterm2_triggers.py` |
| Claude Code hooks wired to the API | **Greenfield** | new `hooks/` posting to the local API |
| Multiple-choice for ALL responses (incl. open-ended) | **Greenfield (hard)** | new choice-synthesis service |
| Gamepad input in browser | **Greenfield** | Gamepad API in `dashboard.js` |
| Dictation input | **Greenfield** | Web Speech API / whisper |

Almost every transport/control primitive exists. The genuinely new work is:
(a) Claude Code hook wiring, (b) **choice synthesis**, (c) browser input
(gamepad + dictation).

---

## Part 3 — Architecture proposal

### Naming

Call the new subsystem **the Driver**: a Claude Code ↔ browser control loop that
hosts on the repo's local API, mirrors a Claude Code session running in an iTerm
pane, and presents every input moment as gamepad/dictation-navigable
multiple-choice.

### 3.0 The single most important decision: hooks-first, screen-second

ControIDE's insight stands: **prefer structured signals over screen-scraping.**
Use the most structured source available for each kind of input moment, and fall
back to screen monitoring only for mirroring and for prompts no hook exposes.

Three signal tiers, in order of reliability:

1. **Claude Code hooks** (structured JSON, authoritative): the primary trigger
   for "Claude needs input" and the primary way to *answer* at turn boundaries.
2. **iTerm2 triggers + screen monitoring** (regex over the pane): for the
   *in-TTY* interactive prompts that hooks cannot fully answer (Claude Code's
   own permission selector, plan-approval menu, free-form `❯` prompt) and for
   live mirroring of the pane to the browser.
3. **Transcript parsing** (JSONL): for clean *content* (the assistant's last
   message, tool inputs) without ANSI noise — exactly ControIDE's
   `parse_last_assistant`, now generalized to read the latest tool_use / text /
   question blocks.

### 3.1 Capturing output + detecting "Claude is asking" — the recommended mix

**Mirror (always-on):** `session.start_monitoring()` callback → push raw screen
deltas onto dashboard SSE `/events`. The browser renders the live pane. Use the
transcript (parsed via the ControIDE primitive) for the clean "what Claude said"
panel above the choices. This satisfies "forward the contents."

**Detect input moments** — map each kind to its best signal:

| Input moment | Best signal | How to answer |
|---|---|---|
| **Tool permission** ("allow Bash? yes / yes-and-don't-ask / no") | **PreToolUse hook** | Hook BLOCKS (returns nothing yet), POSTs the pending request to the Driver, the Driver long-polls/waits for the user's choice, then the hook returns `hookSpecificOutput.permissionDecision = allow\|deny\|ask`. This answers *without touching the TTY*. |
| **End of turn** ("what next?") | **Stop hook** (ControIDE pattern) | Hook returns `{"decision":"block","reason":<chosen text>}` to continue, or `{}` to stop. |
| **Idle / waiting for input** | **Notification hook** (`notification_type` idle/permission) | Signals the Driver that input is awaited; the Driver raises the choice card. |
| **Free-form prompt at the `❯`** (Claude Code asking a question in-TTY, or the shell prompt) | **screen monitor + subscribe regex** | The chosen answer is injected with `session.send_text(answer)` then `send_special_key("enter")`. |
| **Claude Code's own arrow-key menus** (plan approval, permission box rendered in TTY) | **screen monitor regex** detects the menu; `interact_until` / `send_special_key("up"/"down"/"enter")` or numeric `send_text("1")` selects | TTY navigation via special keys. |
| **AskUserQuestion** (structured multiple-choice) | **Agent-SDK `canUseTool`** *if* running under the SDK; otherwise screen regex | If SDK: answer via `updatedInput.answers`. If CLI: detect the rendered question + options on screen and inject the selection key. |

**Why this mix:** Permission and turn-boundary decisions are the high-value,
high-frequency input moments and they have *authoritative structured hooks* —
use them and never screen-scrape those. PreToolUse is doubly valuable because
its `tool_name` + `tool_input` give us the exact thing to render as a choice
("Run `rm -rf build/`? [Allow once] [Allow always] [Deny] [Edit command…]").
Everything Claude Code only renders in the TTY (its arrow-key menus, free-form
questions) is caught by the existing subscribe/trigger regex engine and answered
with `send_special_key`/`send_text`. This is the "~95% iTerm functionality +
Claude Hooks" split the user described.

**Extracting the options Claude is offering:**

- *Permission prompts:* options are known a priori from the hook
  (`allow`/`allow-always`/`deny`/`edit`). Render the command/diff from
  `tool_input` so the human sees what they're approving.
- *Plan approval / Claude's TTY menus:* parse the rendered menu lines from the
  screen buffer (the numbered/`❯`-marked options) with a regex in the
  subscribe engine; each parsed line becomes a `Choice` whose `text`/key answers
  it.
- *AskUserQuestion (SDK):* options come structured in `questions[].options[]`.
- *Free-form question:* no options exist → **synthesize them (3.2)**.

### 3.2 The hard requirement: multiple-choice for ALL responses

For permission/plan/AskUserQuestion the options already exist. The genuinely
hard case is an **open-ended** prompt ("What should I name this module?",
"How should I handle the error case?"). The user must still pick with a gamepad.

**Recommended: a Choice Synthesizer service in the Driver.**

When an input moment has no enumerable options, the Driver calls a small,
**fast** meta-LLM (e.g. Claude Haiku via the Anthropic API — see the
`claude-api` reference for model ids/pricing) with:

- the parsed question (from transcript/screen),
- recent context (last assistant turn + the tool_input or surrounding screen),

and asks it to return **N (3–5) concrete candidate answers** as strict JSON
(`[{label, text}]`). The Driver renders those as the choice card. Always append
two fixed escape-hatch choices:

- **"Dictate custom…"** — opens the Web Speech / whisper capture; the
  transcribed text becomes the answer (ControIDE's "Custom…" generalized).
- **"Reroll"** — re-ask the synthesizer for a fresh N (ControIDE roadmap names
  "reroll with guided input").

So every screen the human ever sees is: *up to N synthesized answers + Dictate +
Reroll + (where applicable) Stop/Deny*. That satisfies "multiple-choice for ALL
responses" without ever blocking on a keyboard.

**Latency control:** synthesis is on the human-input critical path, so it must
be sub-second-ish. Use Haiku, cap output tokens, stream nothing (need the whole
JSON), and pre-warm. For permission prompts skip synthesis entirely (options are
fixed). Cache the synthesizer's last result so "Reroll" is the only re-call.

### 3.3 Local API + web UI

Host on the repo's existing local server (`core/dashboard.py` today; the
singleton daemon post-merge). Endpoints (extend the existing surface):

- `GET /events` (SSE, exists) — live screen deltas + Driver state. Add event
  types: `screen`, `question` (a pending input moment with its choices),
  `cleared` (moment resolved).
- `POST /api/driver/answer` — body `{moment_id, choice_id | custom_text}`. The
  Driver resolves the matching pending moment.
- `GET /api/driver/pending` — long-poll fallback for the hook side (a blocked
  PreToolUse/Stop hook calls this and waits until the human answers, then reads
  the decision). Alternatively the hook posts and blocks on a per-moment
  response.
- `POST /api/db/responses` (exists) — the `⏺` trigger keeps posting captured
  output here; reuse for the mirror/history panel.
- `POST /api/hook/<event>` — endpoints the Claude Code hooks POST to
  (`/api/hook/pretooluse`, `/stop`, `/notification`, `/userpromptsubmit`). Each
  registers a pending moment and (for blocking hooks) waits for the answer.

**The inject-back path already exists:** `/api/send` → `session.send_text(...)`.
For TTY menu answers add a thin variant that calls `send_special_key`.

**UI:** a single page (`static/`) with three regions: (1) live pane mirror
(from `screen` SSE), (2) the clean "Claude said" summary, (3) the **choice
card** — large, high-contrast, numbered tiles sized for glanceability and gamepad
focus. Reuse the `frontend-design` aesthetic.

### 3.4 Input: gamepad + dictation in the browser

This is pure front-end and maps cleanly onto ControIDE's `Action` enum:

- **Gamepad:** the browser **Gamepad API** (`navigator.getGamepads()` polled in
  `requestAnimationFrame`). Map D-pad up/down (or left stick) → `MOVE_PREV`/
  `MOVE_NEXT`; A/✕ → `SELECT`; B/◯ → `CANCEL`/back; a face/shoulder button →
  "Dictate"; another → "Reroll". Selection moves a highlight over the choice
  tiles; SELECT POSTs `/api/driver/answer`. (DualSense pairs over Bluetooth and
  appears to the Gamepad API directly — no `hidapi` needed in-browser, which is
  simpler than ControIDE's roadmap.)
- **Dictation:** the **Web Speech API** (`SpeechRecognition`) for zero-install
  in-browser dictation; offer a **local whisper** fallback (whisper.cpp / MLX,
  per ControIDE roadmap) for accuracy/offline. Two dictation modes:
  1. *Pick a choice by voice* — match the utterance to the nearest choice label
     (fuzzy) → SELECT.
  2. *Custom answer* — when "Dictate custom…" is the active choice, the
     transcript becomes the `custom_text` in `/api/driver/answer`.

Front-end only; the server stays input-agnostic (it just receives a resolved
choice). This preserves ControIDE's clean input/transport separation.

### 3.5 Custom iTerm profile + trigger set

**Profile** (extend `core/profiles.py` to emit a "Claude Driver" Dynamic
Profile): distinctive tab color + badge (e.g. "🎮 Claude"), a large readable
font, generous scrollback (so screen monitor/captures don't lose context),
mouse reporting off (we drive via gamepad), shell-correction off (already done
for team profiles), and the `user.claude_session_id` variable bridge so the
Driver can map iTerm session ↔ Claude session.

**Triggers** (extend `scripts/install_iterm2_triggers.py`): keep the `⏺`
capture trigger (feeds the mirror/history). Add prompt-detection triggers whose
regex matches Claude Code's in-TTY input chrome — the permission box header, the
plan-approval menu, the free-form `❯`/`>` question line — each invoking a script
function that POSTs a "pending question" to `/api/hook/...` (mirroring the
existing `capture_claude_response_rpc` pattern). These cover the prompts no
Claude Code hook exposes.

### 3.6 Claude Code hooks configuration

A new `hooks/` dir + a `.claude/settings.json` block (ControIDE-style) wires:

- **PreToolUse** (matcher `*` or scoped to Bash/Edit/Write) → POST
  `tool_name`+`tool_input`+`session_id` to `/api/hook/pretooluse`, **block until
  answered**, then emit
  `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow|deny|ask","permissionDecisionReason":...}}`.
  This is the highest-value hook: it turns every dangerous/permissioned action
  into a gamepad choice.
- **Stop** → ControIDE's exact behavior: POST last-assistant summary +
  synthesized "what next" choices, then return
  `{"decision":"block","reason":<chosen text>}` or `{}`.
- **Notification** → POST the `notification_type`/`message` so the Driver knows
  Claude is idle/awaiting input and raises a card (Notification can't block, so
  it's a signal only).
- **UserPromptSubmit** (optional) → log/annotate what the human sent (for the
  history panel).
- **SessionStart** → register the session with the Driver and bind it to the
  iTerm pane (via `user.claude_session_id`).

Each hook is a tiny script (like `hooks/stop.sh`) that reads stdin JSON and
talks to the local API. Where the CLI offers no blocking callback, the
hook-blocks-on-long-poll pattern (`/api/driver/pending`) bridges to the human.

> **SDK upgrade path:** the Agent SDK exposes a `canUseTool` callback and the
> structured `AskUserQuestion` tool. If a future version runs Claude inside the
> Agent SDK (rather than the bare CLI), permission requests and multiple-choice
> questions arrive as **structured callbacks** — no screen-scraping at all, and
> AskUserQuestion's options come pre-enumerated. Design the Driver's
> moment/answer model so a `canUseTool` adapter can replace the hook adapter
> without touching the UI or the synthesizer. This is the long-term "plug into
> the agent loop" endgame ControIDE gestured at.

### 3.7 Phased rollout

**Phase 0 — Port ControIDE's Stop loop to the browser (proves the loop).**
Stop hook → POST summary + fixed choices to the dashboard API → render a choice
card on the existing SSE page → click to answer → `decision:block`. No gamepad,
no synthesis, no iTerm yet. This validates the API/SSE/hook round-trip end to
end inside this repo.

**Phase 1 — iTerm + mirror + permission prompts.** Run Claude in an iTerm pane;
stream the screen monitor to SSE; wire **PreToolUse** with block-until-answered
so tool permissions become choice cards (fixed options — no synthesis needed).
Add the custom profile.

**Phase 2 — Choice synthesis.** Add the Haiku-backed synthesizer for open-ended
prompts + the "Dictate custom" / "Reroll" escape hatches. Add the in-TTY
prompt-detection triggers/regex + `send_special_key` answering for Claude's own
menus.

**Phase 3 — Gamepad + dictation.** Gamepad API navigation and Web Speech /
whisper dictation in the front end. Now fully hands-free-ish.

**Phase 4 — Polish / SDK path.** Conversation rollback/branching (SDK
`SessionStore`, per ControIDE roadmap), persistent daemon host (the singleton
daemon), `canUseTool`/`AskUserQuestion` SDK adapter, multi-session.

### 3.8 Key technical risks

1. **Synthesizing good options for free-form prompts (the core hard problem).**
   The whole UX collapses if the 3–5 synthesized answers are usually wrong or
   generic — the human ends up dictating every time, defeating the gamepad
   premise. Mitigation: give the synthesizer rich context (tool_input, recent
   transcript), tune the prompt hard, always offer Dictate + Reroll, and measure
   "pick-without-dictate" rate as the north-star metric.
2. **Latency on the input critical path.** Hook-blocks-until-answered + a
   synthesizer LLM call sits directly between Claude and the human. Keep
   synthesis on Haiku with capped tokens; skip it entirely for fixed-option
   moments; pre-warm; and set generous hook timeouts (ControIDE uses 600s) so a
   slow human doesn't fail the hook.
3. **Reliably detecting in-TTY prompts by regex.** Claude Code's TTY chrome
   (boxes, `❯`, ANSI) changes between versions; regex triggers are brittle.
   Mitigation: lean on hooks (stable JSON) wherever possible and treat
   screen-regex as the fallback; centralize the regexes so they're easy to
   re-tune; prefer the SDK `canUseTool` path long-term to eliminate scraping.
4. **Answer injection races.** Injecting `send_text` while Claude is mid-render,
   or answering the wrong moment, corrupts state. Mitigation: gate injection on
   `wait_for_prompt`/`expect` (already in `core/session.py`); tag every pending
   moment with a `moment_id` and require it on `/api/driver/answer` so stale
   answers are rejected.
5. **Background subagents bypass `canUseTool`** (known CC limitation). If the
   SDK path is adopted, background `Task` subagents won't route permissions
   through the callback — pre-approve them via permission rules/hooks.

### 3.9 What to prototype first

**Prototype the Phase 0 loop plus a single PreToolUse permission card.**
Concretely: a `hooks/pretooluse.sh` + `hooks/stop.sh` (ControIDE-shaped) that
POST to two new endpoints on `core/dashboard.py`, block on a long-poll, and
return the structured hook JSON; a minimal addition to `static/dashboard.js`
that renders an incoming `question` SSE event as clickable tiles and POSTs the
answer. No synthesis, no gamepad, no dictation. This single prototype exercises
every load-bearing seam — hook → local API → SSE → browser → answer → hook
return → Claude continues — for both the high-frequency moment (permissions) and
the turn boundary (Stop). If that round-trips with acceptable latency, every
later phase is additive.

---

## Appendix — key files

- ControIDE: `/Users/psentro/research-developer/ControIDE/src/controide/`
  (`hook/stop.py`, `transcript.py`, `ui/popup.py`, `model.py`, `choices.py`,
  `input/base.py`, `input/mock_keyboard.py`).
- iTerm control: `core/session.py`, `core/terminal.py`.
- Monitoring: `core/session.py` (`start_monitoring`/`add_monitor_callback`),
  `core/iterm_path_monitor.py`.
- Subscribe/EventBus: `iterm_mcpy/tools/subscribe.py`, `core/flows.py`,
  `iterm_mcpy/tools/sessions.py`.
- Triggers: `scripts/install_iterm2_triggers.py`,
  `scripts/iterm2_capture_response.py`.
- Profiles: `core/profiles.py`, `docs/PROFILES*.md`.
- Internal hooks (not CC hooks): `core/agent_hooks.py`, `core/service_hooks.py`.
- Local API host: `core/dashboard.py` (HTTP+SSE, `/api/send`, `/api/db/*`),
  `core/dashboard_db.py`, `iterm_mcpy/tools/telemetry.py`, `static/`.
