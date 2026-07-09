# XP-Pen Remote Control for ControIDE — Design

**Date:** 2026-07-09
**Status:** Approved (brainstormed with Preston)
**Builds on:** gamepad navigation (`feat/gamepad-nav`), ControIDE Phase-0 driver
(`core/driver.py`, `static/driver.js`), voice control v1 (PR #139)

## Goal

Drive Claude Code with an XP-Pen shortcut remote (ACK05-style: rotary wheel,
center wheel button, keys K1–K10) **focus-independently** — the remote must
work no matter which app is frontmost — while also being able to drive the
ControIDE driver page's question tiles. Input unifies behind the existing
`Action` abstraction (`MOVE_PREV` / `MOVE_NEXT` / `SELECT` / `CANCEL`).

## Architecture (hub-and-spoke through the daemon)

```
XP-Pen hardware
  │  (XP-Pen driver app maps controls → dead keys F13–F19, set once)
  ▼
core/input_listener.py  — in-repo pynput global listener (iterm-mcp service)
  │  POST /api/input {"action": ..., "modifier": bool}
  ▼
core/dashboard.py — action router (resolves effective level)
  ├─ level "tiles"  → SSE "action" event → driver.js applyGamepadAction()
  ├─ level "tui"    → send_special_key (arrows/Enter/Esc) into active session
  ├─ level "panes"  → move iTerm focus between panes in the current window
  └─ action "window_cycle" → activate next iTerm window, round-robin
```

The XP-Pen never talks to any app directly. Dead keys (F13–F19) collide with
nothing, so the remote is inert outside this system. The browser never needs
focus; the driver page is just one routing target.

**Rejected alternatives:** browser-focused keyboard nav only (loses focus
independence); third-party hotkey layers — Raycast / Hammerspoon / skhd —
(user preferred keeping it in-repo since Python already runs in the
background); iTerm URL scheme as the control path (non-standard and thin —
though note: it *may* save trouble for one-off cases like window focus, and
should be re-evaluated per feature before adding daemon machinery).

## Traversal hierarchy (3 levels + window jump)

| Context | Wheel CCW / CW | Select (K1/K9) | Cancel (K2) |
|---|---|---|---|
| **tiles** (base when a question is pending) | prev / next tile focus | click focused tile | close custom-text area |
| **tui** (base when no question pending) | Up / Down arrow into active session | Enter | Escape |
| **panes** (while modifier K7 is HELD) | focus prev / next pane in current window | no-op (v1) | no-op (v1) |

- **Base level is automatic:** `tiles` if `DriverStore` has a pending
  question, else `tui`. No mode to remember for the common case.
- **Modifier is a quasimode:** holding K7 shifts the wheel to `panes`;
  release snaps back to base. The listener tracks the modifier key's
  down/up state and stamps every POST with `modifier: true/false`; the
  **daemon** resolves the effective level (modifier ⇒ `panes`, else
  `tiles` if a question is pending, else `tui`) since only it can see
  `DriverStore` state.
- **Center wheel button = `window_cycle`:** discrete round-robin jump across
  iTerm windows (one window per project). A direct action, not a held mode.
- The driver page header shows the active level (tiles / tui / panes-held)
  and the tiles⇄tui auto-swap, so the user always knows what the wheel will
  do. Level changes toast.

## Physical mapping

Set once in the XP-Pen driver app; the software side lives in one Python
dict in `core/input_listener.py`.

| Control | Dead key | Meaning |
|---|---|---|
| Wheel CCW / CW | F13 / F14 | `move_prev` / `move_next` |
| Center wheel button | F15 | `window_cycle` |
| K1 | F16 | `select` |
| K2 | F17 | `cancel` |
| K7 (tall, thumb-reachable) | F18 | **modifier** — hold = panes level |
| K9 (wide) | F19 | `select` (ergonomic duplicate) |
| K3–K6, K8, K10 | — | reserved (v2: digits 1–4 direct tile pick, pane ops) |

The XP-Pen's own 4-group cycling (center button's default) is deliberately
NOT used for the level mechanic: group state is invisible to software, so
the UI could not display the active level. Groups remain available for
coarse context switching (e.g. group 2 = media keys) if the user wants.

## Components

1. **`core/input_listener.py`** — new module.
   - Pure keymap + edge-detection logic (dead key → `(action, level)`)
     separated from the pynput listener thread, mirroring the
     gamepad-mapper pattern so it tests headlessly with no pynput import.
   - Listener thread: pynput `keyboard.Listener`; tracks modifier down/up;
     POSTs to the daemon over localhost HTTP with retry/backoff.
   - Registered in the existing **services** registry (`services` tool:
     start/stop/configure), following the established service pattern.
2. **`pynput` as an optional extra** — `pip install iterm-mcp[remote]`.
   Base install unaffected; the service refuses to start with a clear
   message if pynput is missing.
3. **`core/dashboard.py`** — new `POST /api/input` route: validates
   `{action, modifier}`, resolves the effective level (see hierarchy
   section), routes per the table above, broadcasts SSE `action` events
   for the tiles level. Unknown action / malformed body → 400.
4. **`static/driver.js`** — SSE `action` handler that feeds the existing
   `applyGamepadAction()`; header level indicator.
5. **Window/pane control** — daemon-side, via the existing iTerm2 Python
   API connection (`ItermTerminal` focus helpers, `send_special_key`).

## Failure handling

- **No macOS Accessibility permission** → the service logs a one-line fix
  ("grant Accessibility to <python path> in System Settings…") and stays
  stopped. Never crashes the daemon.
- **Daemon unreachable from listener** → retry with capped exponential
  backoff; drop events during outage (stale nav actions must not replay).
- **No active iTerm session at tui/panes level** → action dropped; toast.
- **Unknown action/level** → HTTP 400, ignored.

## Testing

Headless (CI suite, no iTerm windows):
- Keymap/edge-detection unit tests mirroring `tests/test_gamepad_controller.py`
  (held key fires once, modifier stamping, unmapped keys → None).
- Router tests for `/api/input`: level resolution (pending question ⇒ tiles),
  action → route dispatch (mocked terminal), 400 paths.
- Listener import guard test: service reports "unavailable" cleanly when
  pynput is absent.

Manual (hardware): checklist appended to `docs/gamepad-manual-test.md`
covering XP-Pen app setup, each mapping row, modifier hold/release,
window cycling, and permission-denied behavior.

## Out of scope (v1)

- Pane-level select/cancel semantics (zoom, close — v2).
- Digits 1–4 direct tile pick from K3–K6 (v2).
- Multiple simultaneous remotes; non-XP-Pen macro pads (the dead-key
  contract means any device that can emit F13–F19 works incidentally).
- Windows/Linux support (pynput is cross-platform but permissions and the
  XP-Pen driver differ; untested).
