# Always-Multiple-Choice (MC) Coercion — Design Doc

**Date:** 2026-06-13
**Branch:** `feat/mc-coercion` (stacked on ControIDE driver branch, PR #130)
**Status:** Implementation plan → active

---

## Problem

Claude Code responses often present a wall of prose. When the human-in-the-loop
driver is running, we want every assistant turn to end with a structured
multiple-choice list so the dashboard can offer quick-select tiles and so
operators can see exactly what Claude thinks the options are.

---

## Design: Three Layers

### Layer 1 — Toggle flag file

```
~/.iterm-mcp/multiple-choice.on
```

- **Present** → MC coercion ON.
- **Absent** → OFF; all hooks behave exactly as #130.
- No daemon restart needed; hooks re-stat the file each invocation.
- Toggle script: `hooks/mc_toggle.sh on|off|status`.
- Optional future: `/api/mc-toggle` endpoint on `core/dashboard.py` so the
  browser tile can flip it live. (Seam left in code with a TODO comment.)

### Layer 2 — UserPromptSubmit hook (soft injection)

When the flag is ON, every user message receives `additionalContext` instructing
Claude to end its next response with a numbered multiple-choice list.

**Output shape (ON):**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "<MC_INSTRUCTION>"
  }
}
```

**Output shape (OFF):**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit"
  }
}
```

This hook also **resets** the per-session reprompt counter (see Layer 3) because
a new user turn begins; whatever Claude did last turn is irrelevant.

### Layer 3 — Stop hook reprompt-once enforcement

When the flag is ON:

1. Inspect the just-finished assistant turn via `_read_last_assistant_text`.
2. If the text does **not** match `OPTION_PATTERN` AND the per-session reprompt
   counter is `0`:
   - Return `{"decision": "block", "reason": "<reprompt instruction>"}`.
   - Increment counter to `1`.
3. If options **are** present, **or** counter is already `1`:
   - Fall through to the existing #130 stop behavior (present turn-end options
     to the dashboard).
   - **Phase-2 seam (TODO):** When counter is 1 and options still missing,
     synthesize a choices list from the transcript before posting to the
     dashboard, rather than showing raw prose.

When the flag is OFF, the stop hook behaves exactly as in #130.

---

## Format Spec — `OPTION_PATTERN`

Accepted formats (any of):

```
1) Title: description text
1. Title: description text
(1) Title: description text
```

Regex (shared between injection instruction and detector):

```python
OPTION_PATTERN = re.compile(r'^\s*(?:\d+[.)]\s+|\(\d+\)\s+)\S', re.MULTILINE)
```

At least **one** matching line anywhere in the assistant response passes detection.

### Injection instruction (injected into `additionalContext`)

```
Please end your response with a numbered list of 3–5 suggested next steps,
formatted as:

  1) Option title: brief description
  2) Option title: brief description
  ...

Use exactly this format so the driver dashboard can display quick-select tiles.
```

The detector's regex matches this format so a complying Claude always passes.

---

## Loop-Guard State Machine

State is stored per-session in:

```
~/.iterm-mcp/mc_reprompt/<session_id>
```

Content: a single integer (`0` or `1`).

| Event | Action |
|---|---|
| UserPromptSubmit | Write `0` to state file (reset for new turn) |
| Stop hook, flag ON, options missing, count=0 | Block; write `1` |
| Stop hook, flag ON, options missing, count=1 | Fall through (Phase-2 seam) |
| Stop hook, flag ON, options present | Fall through |
| Stop hook, flag OFF | Skip all MC logic |

Missing or corrupt state files are treated as `0`.

State dir is configurable via `MC_STATE_DIR` env var (for tests).

---

## Files

| File | Role |
|---|---|
| `hooks/driver_hook.py` | Extended with `userpromptsubmit` mode + stop-hook MC layer |
| `hooks/userpromptsubmit.sh` | Thin wrapper: `exec python driver_hook.py userpromptsubmit` |
| `hooks/mc_toggle.sh` | `on\|off\|status` toggle for `~/.iterm-mcp/multiple-choice.on` |
| `docs/examples/hooks-settings.json` | Updated with `UserPromptSubmit` hook entry |
| `tests/test_mc_coercion.py` | Unittest coverage for all three layers |
| `docs/superpowers/plans/2026-06-13-mc-coercion.md` | This document |

---

## Phase-2 Synthesize-Fallback Seam

Location: `hooks/driver_hook.py` → `run_stop_hook()`, guarded by:

```python
# TODO Phase-2: synthesize options from transcript when Claude didn't comply
# after reprompt. Replace `prompt` below with AI-generated choices list.
```

The Phase-2 synthesizer would call a local LLM (or the same Claude session via
`-p` flag) to extract 3–5 choices from the assistant text before posting to the
dashboard, so operators get sensible tiles even when Claude ignores the format.

---

## Toggle — Browser Seam

`core/dashboard.py` has a commented-out route `# TODO: /api/mc-toggle` between
the `/api/answer` and `/api/db/*` routes. Implementing it is low-effort (touch
or rm the flag file) but deferred to keep this PR focused on the hook layer.

---

## OFF-path guarantee

Every new code path in `driver_hook.py` is gated by `_mc_flag_on()` which is a
single `Path.exists()` call. When the flag file is absent, no new code runs:

- `run_userpromptsubmit_hook` emits the minimal no-op JSON immediately.
- `run_stop_hook` proceeds to the existing #130 code path without reading the
  transcript for MC purposes or touching state files.
