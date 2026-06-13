# Background-Window-Default for iTerm2 Window Creation

**Date:** 2026-06-13
**Feature:** Make new iTerm2 windows open in the background by default (no focus steal)

---

## Confirmed iTerm2 API Facts

- `iterm2.Window.async_create(connection, profile=None, command=None, profile_customizations=None)` — has NO background/activation parameter.
- `Window.async_activate()` — "Gives the window keyboard focus and orders it to the front." This is what raises a window within iTerm.
- `App.async_activate(raise_all_windows=True, ignoring_other_apps=False)` — foregrounds the whole iTerm2 app. There is NO `async_deactivate`.
- `app.current_terminal_window` — the currently-front iTerm2 terminal window (may be None). Used in `core/terminal.py` around line 168.
- `async_create` always makes the newly created window the key/front window WITHIN iTerm2. This internal promotion is what we are suppressing via capture-and-restore.

---

## Mechanism: Capture-and-Restore in `create_window`

**File:** `core/terminal.py`, function `create_window` (≈ lines 188–246)

### Change Summary

1. Add parameter `foreground: bool = False` — default False means background (new default behavior).
2. BEFORE creating the window: capture `previous_window = self.app.current_terminal_window` (may be None).
3. Create the window as before (`iterm2.Window.async_create(...)` with same profile/fallback logic).
4. AFTER creating:
   - If `foreground` is False AND `previous_window` is not None AND it is a different object than the new window: call `await previous_window.async_activate()` to restore focus.
   - Wrap the restore call in `try/except Exception` — a focus-restore failure MUST NOT break window creation. Log at WARNING level and continue.
5. If `foreground` is True: skip the restore entirely (leave new window in front — matches old behavior).
6. NEVER call `app.async_activate()`.
7. All other logic (session wrapper, logging, registry) is unchanged.

### Why This Works

`async_create` makes the new window front within iTerm2. By immediately calling `previous_window.async_activate()` afterward, we hand focus back to the window that was front before. From the user's perspective the new window appears but does not steal focus. This does NOT prevent the iTerm2 app itself from potentially coming to the foreground at the OS level if another app was frontmost — that behavior depends on how iTerm2 handles window creation internally and cannot be controlled without a private API.

---

## Scope: Tabs and Splits

### Decision: Leave tabs/splits unchanged with a NOTE comment

`create_tab` and `create_split_pane` also move focus within a window (the new tab or pane becomes active). Applying the same capture/restore pattern to them is feasible, but:

- The user's explicit ask is about WINDOWS.
- Tab/split focus changes are within the same window — less disruptive than a new window popping to the front.
- Adding the pattern to all three methods at once increases the scope and risk surface for a single PR.

**What was done:** Added a `# NOTE:` comment in both `create_tab` and `create_split_pane` explaining that they still steal tab/pane focus within their window, and that the capture/restore pattern could be applied as a future extension (with the same `foreground` flag).

---

## Backward Compatibility

All callers that want the old behavior (new window comes to front) should pass `foreground=True`. The default is now `False` (background). Existing callers that do not pass the parameter will silently get the new background behavior.

This is intentional: the whole point of this change is to make background the default.

---

## LIVE VERIFICATION NEEDED

The following must be confirmed with a live iTerm2 session. No automated test can substitute for these checks:

1. **New window stays behind** — With iTerm2 in the foreground and another app (e.g., Terminal.app or a browser) NOT in focus, call `create_window()` (without `foreground=True`). Confirm the new iTerm2 window opens but the PREVIOUSLY FOCUSED iTerm2 window regains focus within iTerm2 (i.e., the new window does not become the key window).

2. **No disruptive flicker** — Observe whether the new window briefly appears in front before the restore call fires. A short flicker may be unavoidable due to the async round-trip. If this is unacceptable, the only alternative is a private API or a different approach (e.g., AppleScript `ignoring application responses`).

3. **iTerm2 does not foreground over other apps** — If the user is working in a different app (e.g., a browser) when `create_window()` is called, confirm that iTerm2 does NOT jump to the foreground. This is the most important scenario. If iTerm2's `async_create` inherently activates the app at the OS level, `previous_window.async_activate()` alone cannot prevent it — only `app.async_activate()` on the OTHER app could help, which is not available. This would be a KNOWN LIMITATION to document.

4. **`foreground=True` works** — Call `create_window(foreground=True)` and confirm the new window becomes the frontmost window in iTerm2 (old behavior preserved).

5. **`previous_window=None` edge case** — Close all iTerm2 windows, then call `create_window()`. Confirm no exception is raised (no previous window to restore to).

6. **Focus-restore failure is silent** — Manually test a scenario where the previous window is closed between capture and restore (hard to test, but the try/except should cover it). Check that the log shows a WARNING and no exception propagates.

---

## Implementation Plan

1. [x] Write plan doc (`docs/superpowers/plans/2026-06-13-background-window-default.md`) — DONE (this file)
2. [x] Write failing tests (`tests/test_background_window.py`) covering 4 assertions
3. [x] Implement `create_window` change in `core/terminal.py`
4. [x] Run `python -m unittest tests.test_background_window -v` (all pass)
5. [x] Run `python scripts/test_baseline.py --timeout 60` (no regression)
