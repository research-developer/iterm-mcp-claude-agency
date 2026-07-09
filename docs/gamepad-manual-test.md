# Manual test: ControIDE gamepad navigation

The pure mapping logic (`static/gamepad.js`) is covered headlessly by
`tests/js/test_gamepad.mjs` (run via `tests/test_gamepad_js.py` or
`node --test tests/js/test_gamepad.mjs`). This checklist covers the DOM
wiring in `static/driver.js`, which needs a real browser and a real
controller.

## Setup

1. Pair a standard-mapping controller (Xbox, DualShock/DualSense, or any
   pad Chrome reports with `mapping: "standard"`) to the Mac over
   Bluetooth or USB.
2. Start the dashboard and open the driver page in Chrome
   (`http://127.0.0.1:<dashboard-port>/driver.html`).
3. Post a test question so tiles render, e.g.:

   ```bash
   curl -s -X POST http://127.0.0.1:<dashboard-port>/api/ask \
     -H 'Content-Type: application/json' \
     -d '{"hook_type": "pretooluse",
          "prompt": "Gamepad manual test",
          "options": [
            {"id": "allow",  "label": "Allow",   "text": "allow"},
            {"id": "deny",   "label": "Deny",    "text": "deny"},
            {"id": "custom", "label": "Custom…", "text": ""}
          ]}'
   ```

4. Press any button once — browsers only expose a pad (and fire
   `gamepadconnected`) after the first input.

## Checklist

| # | Step | Expected |
|---|------|----------|
| 1 | Press any button after loading the page | Toast: "Gamepad connected: <pad id>" |
| 2 | Tap D-pad **down** | Focus highlight moves to the next tile (same blue ring as Tab) |
| 3 | Tap D-pad **up** | Focus highlight moves to the previous tile |
| 4 | Tap D-pad down repeatedly past the last tile | Focus wraps to the first tile |
| 5 | **Hold** D-pad down for 2 s | Focus moves exactly once (no repeat storm) |
| 6 | Push **left stick** down past ~60 % and hold | Focus moves exactly once; release to neutral re-arms |
| 7 | Ease the stick back only halfway, then push down again | No second move (hysteresis) — must return near neutral first |
| 8 | Focus a tile, press **A** (cross) | Tile submits: "Submitting answer…", then "Answer sent: <id>" toast; card clears via SSE |
| 9 | Re-post the question, navigate to the **Custom…** tile, press A | Custom text area opens with the input focused |
| 10 | Press **B** (circle) while the custom area is open | Custom area closes, input cleared, focus returns to the first tile |
| 11 | Press B with no custom area open | Nothing happens |
| 12 | Hold A | Exactly one submission (edge detection; the `submitting` guard is a second line of defense) |
| 13 | With no question pending, press D-pad/A/B | Nothing happens, no console errors |
| 14 | Turn the controller off | Toast: "Gamepad disconnected: <pad id>"; page keeps working with mouse/keyboard |
| 15 | Turn it back on and press a button | "Gamepad connected" toast; navigation works again |

## No-hardware smoke test

Chrome DevTools cannot emulate gamepads, but the wiring can be smoke-
tested from the DevTools console by stubbing the API before any pad
input:

```js
// Paste in DevTools on driver.html, then post a question.
const pad = {
  index: 0, connected: true, mapping: "standard",
  id: "Fake Pad", axes: [0, 0, 0, 0],
  buttons: Array.from({ length: 17 }, () => ({ pressed: false })),
};
navigator.getGamepads = () => [pad];
window.dispatchEvent(new Event("gamepadconnected")); // starts the poll loop
// (a plain Event has no .gamepad — the handler falls back to "pad" in the toast)

// Simulate: D-pad down press + release
pad.buttons[13].pressed = true;
setTimeout(() => { pad.buttons[13].pressed = false; }, 200);
```

Expected: the focus ring moves one tile per simulated press.

---

# Manual test: XP-Pen remote (dead-key input chain)

Pure logic is covered headlessly (`tests/test_input_listener.py`,
`tests/test_input_route.py`, `tests/test_terminal_focus_helpers.py`).
This checklist covers the real chain: XP-Pen hardware → pynput →
`/api/input` → SSE/iTerm.

## One-time setup

1. `pip install 'iterm-mcp[remote]'` into the environment that will run
   the listener.
2. In the **XP-Pen driver app**, map the remote: wheel CCW→F13,
   wheel CW→F14, center button→F15, K1→F16, K2→F17, K7→F18, K9→F19.
3. Start the dashboard, then the listener:
   `iterm-mcp-input-listener --url http://127.0.0.1:<dashboard-port>`
4. Grant **Accessibility** permission to the listed python binary when
   macOS prompts (System Settings → Privacy & Security → Accessibility).
   The listener prints the exact path at startup.
5. Optional — register as a managed service (services tool):
   `{"name": "xppen-listener", "command": "iterm-mcp-input-listener", "priority": "optional"}`

## Checklist

| # | Step | Expected |
|---|------|----------|
| 1 | No question pending, iTerm focused elsewhere: spin wheel | Arrow keys arrive in the active iTerm session (Claude Code TUI menus move); works with ANY app focused |
| 2 | Press K1 | Enter arrives in the active session |
| 3 | Press K2 | Escape arrives in the active session |
| 4 | Post a driver question (see gamepad section above): spin wheel | Focus ring moves across tiles on the driver page; level indicator shows "tiles" |
| 5 | Press K1 on a highlighted tile | Tile submits via /api/answer |
| 6 | HOLD K7 and spin the wheel | iTerm pane focus moves between panes; release K7 → wheel drives tiles/TUI again |
| 7 | Click the wheel's center button | Next iTerm window activates (round-robin across project windows) |
| 8 | Hold the wheel one notch (key-repeat) | Exactly one action per notch — no repeat storm |
| 9 | Stop the dashboard, spin the wheel, restart it | No crash; listener backs off during the outage and recovers |
| 10 | Run listener without pynput installed | Exits with "pip install 'iterm-mcp[remote]'" message, code 2 |

## No-hardware smoke test

Any keyboard that can emit F13–F19 (or macOS Karabiner, or `Fn`-row
remapping) exercises the identical path — the listener cannot tell an
XP-Pen from a keyboard; the dead-key contract is the interface.
