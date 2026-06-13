# ControIDE Phase-0: Browser-Based Human-in-the-Loop Driver

**Date:** 2026-06-12  
**Status:** Implementation in progress

---

## Overview

A browser-based human-in-the-loop driver for Claude Code. When Claude Code
finishes a turn (Stop hook) or is about to use a tool (PreToolUse hook), a
question card appears in the browser. The human clicks a tile. The hook gets
the answer and returns structured JSON to Claude Code.

The key property: the hook script **blocks** until the human answers (or times
out), so Claude Code waits for human approval before continuing or using a tool.

---

## Load-bearing Loop

```
┌─ Claude Code ──────────────────────────────────────────────────────────┐
│  1. Hook fires (Stop or PreToolUse)                                    │
│  2. hooks/stop.sh or hooks/pretooluse.sh is spawned                    │
│  3. Script reads stdin JSON, calls driver_hook.py                      │
│  4. driver_hook.py POSTs question to POST /api/ask (BLOCKS here)       │
│  5. Server stores question, broadcasts SSE "question" event            │
│  6. Browser renders clickable tiles                                    │
│  7. Human clicks a tile → browser POSTs to POST /api/answer           │
│  8. Server wakes the blocked /api/ask response, returns answer         │
│  9. driver_hook.py emits structured JSON to stdout, exits              │
│ 10. Claude Code reads the JSON and acts accordingly                    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Files Created

| File | Purpose |
|------|---------|
| `core/driver.py` | `DriverStore` ask/answer router + `Action` enum + `Controller` protocol |
| `core/dashboard.py` | Extended with `/api/ask`, `/api/answer` routes and SSE named events |
| `hooks/driver_hook.py` | Pure-stdlib Python helper: reads stdin, POSTs to dashboard, emits hook JSON |
| `hooks/stop.sh` | Thin bash wrapper: invokes `driver_hook.py stop` |
| `hooks/pretooluse.sh` | Thin bash wrapper: invokes `driver_hook.py pretooluse` |
| `static/driver.html` | Standalone driver page (tiles + connection status) |
| `static/driver.js` | SSE listener + tile click → POST /api/answer |
| `static/driver.css` | Large high-contrast tile styles |
| `tests/test_driver.py` | Unit + integration tests for DriverStore and hook decision logic |
| `docs/examples/hooks-settings.json` | Example Claude Code hooks configuration |

---

## Endpoint Contracts

### POST /api/ask

**Called by:** hook scripts (blocks until answered or timed out)

Request body:
```json
{
  "hook_type": "stop" | "pretooluse",
  "prompt": "Human-readable summary of what Claude is about to do",
  "options": [
    {"id": "continue", "label": "Continue", "text": "Continue with the plan..."},
    {"id": "stop",     "label": "Stop here", "text": ""}
  ],
  "timeout": 120.0
}
```

Response (after human answers):
```json
{
  "choice_id": "continue",
  "choice_text": "Continue with the plan...",
  "custom_text": null
}
```

Response (on timeout): HTTP 408 with `{"error": "timeout"}`

### POST /api/answer

**Called by:** browser tile click

Request body:
```json
{
  "id": "<question_uuid>",
  "choice_id": "continue",
  "custom_text": null
}
```

Response:
```json
{"success": true}
```

Response (question not found): HTTP 404 `{"error": "Question not found"}`

### SSE /events (extended)

Existing unnamed data events continue unchanged. New named events:

```
event: question
data: {"id": "...", "hook_type": "stop", "prompt": "...", "options": [...]}

event: cleared
data: {"id": "..."}
```

---

## Hook JSON Output Shapes

### Stop Hook

**Continue / Refine:**
```json
{"decision": "block", "reason": "Continue with the plan you just described."}
```

**Stop here:**
```json
{}
```

**Custom:**
```json
{"decision": "block", "reason": "<custom_text from human>"}
```

### PreToolUse Hook

**Allow:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "permissionDecisionReason": "allow"
  }
}
```

**Deny:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "deny"
  }
}
```

**Ask (edit):**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "ask"
  }
}
```

---

## Test List

### DriverStore unit tests (synchronous)
1. `test_post_question_creates_question` — post returns a Question with an id
2. `test_answer_question_sets_answer` — answer sets the answer dict on the Question
3. `test_answer_unknown_id_returns_false` — returns False for bogus id
4. `test_pending_questions_lists_unanswered` — answered questions are excluded

### DriverStore asyncio tests
5. `test_wait_for_answer_resolves` — answer in a task, await wait_for_answer succeeds
6. `test_wait_for_answer_timeout` — no answer within timeout returns None

### Hook decision-shaping tests (pure Python, no HTTP)
7. `test_stop_hook_continue_decision`
8. `test_stop_hook_stop_decision`
9. `test_stop_hook_custom_decision`
10. `test_pretooluse_allow_decision`
11. `test_pretooluse_deny_decision`

### Full ask/answer flow contract test
12. `test_ask_answer_full_flow` — asyncio: post question, start wait_for_answer task,
    answer it, verify returned answer

---

## Future Work (Phase 1+)

- `GamepadController` — map gamepad buttons to `Action` enum
- `DictationController` — map speech-to-text results to `Action` enum
- Keyboard shortcuts in driver.html (1-4 number keys for tiles)
- Question queue (multiple pending questions)
- Persistent question log in DashboardDB
