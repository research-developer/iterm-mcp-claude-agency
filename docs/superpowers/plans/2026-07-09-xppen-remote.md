# XP-Pen Remote Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive Claude Code focus-independently from an XP-Pen shortcut remote via dead keys (F13–F19) → in-repo pynput listener → `POST /api/input` → level-based routing (driver tiles / Claude Code TUI / iTerm panes / window cycle).

**Architecture:** A pure `KeyEventMapper` (mirroring `GamepadController`) turns dead-key events into `{action, modifier}` payloads; a pynput listener process POSTs them to the dashboard; the dashboard resolves the effective level (modifier ⇒ panes, else tiles-if-question-pending, else tui) and routes to SSE `action` events (tiles), `send_special_key` (tui), or new `ItermTerminal` focus helpers (panes/window).

**Tech Stack:** Python 3.12 (repo `.venv`), pynput (new optional extra `[remote]`), asyncio dashboard in `core/dashboard.py`, plain ES2020 in `static/driver.js`, unittest via pytest.

**Spec:** `docs/superpowers/specs/2026-07-09-xppen-remote-design.md` — read it first.

## Global Constraints

- Tests must be **headless** — never open iTerm windows, never require a live iTerm2 connection, never import pynput at module scope in tested code paths.
- Run tests with the repo venv: `.venv/bin/python -m pytest …` (bare `python` is 2.7 on this machine).
- **Before every commit**, the CI-equivalent suite must be green:
  ```
  .venv/bin/python -m pytest tests/ \
    --ignore=tests/test_basic_functionality.py --ignore=tests/test_advanced_features.py \
    --ignore=tests/test_line_limits.py --ignore=tests/test_logging.py \
    --ignore=tests/test_persistent_session.py --ignore=tests/test_expect.py \
    --ignore=tests/test_flows.py --ignore=tests/test_messaging.py \
    --ignore=tests/test_session_suspend.py -q
  ```
  Baseline at plan time: 1370 passed, 6 skipped. Don't regress.
- `pynput` must remain an **optional** dependency (`pip install iterm-mcp[remote]`); base install and CI must work without it.
- Work on branch `feat/xppen-remote`. Do NOT push, do NOT open a PR.
- End every commit message with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- Code style: Google-style docstrings, snake_case, 4-space indent, imports grouped stdlib/external/local.

---

### Task 1: KeyEventMapper — pure dead-key → payload logic

**Files:**
- Create: `core/input_listener.py`
- Test: `tests/test_input_listener.py`

**Interfaces:**
- Produces: `KeyEventMapper` class with `handle_event(event: dict) -> Optional[dict]`.
  Input event shape: `{"key": "f13", "pressed": True}`.
  Return: `{"action": "<move_prev|move_next|select|cancel|window_cycle>", "modifier": <bool>}` on a rising edge of a mapped key, else `None`.
  Module constants: `KEY_ACTIONS: dict[str, str]`, `MODIFIER_KEY = "f18"`.
- Consumes: nothing (stdlib only — no pynput import in this task).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_input_listener.py`:

```python
"""Tests for the XP-Pen input listener (core/input_listener.py).

Headless: exercises only pure logic. No pynput, no network, no iTerm.
Dead-key contract (set once in the XP-Pen driver app):
    F13/F14 wheel CCW/CW, F15 center button, F16 K1 select,
    F17 K2 cancel, F18 K7 modifier (hold), F19 K9 select.
"""

import os
import sys
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.input_listener import KeyEventMapper


def press(key):
    """Build a key-down event dict."""
    return {"key": key, "pressed": True}


def release(key):
    """Build a key-up event dict."""
    return {"key": key, "pressed": False}


class TestKeyMapping(unittest.TestCase):
    """Dead keys translate to the right action payloads."""

    def setUp(self):
        self.mapper = KeyEventMapper()

    def test_f13_maps_to_move_prev(self):
        self.assertEqual(
            self.mapper.handle_event(press("f13")),
            {"action": "move_prev", "modifier": False},
        )

    def test_f14_maps_to_move_next(self):
        self.assertEqual(
            self.mapper.handle_event(press("f14")),
            {"action": "move_next", "modifier": False},
        )

    def test_f15_maps_to_window_cycle(self):
        self.assertEqual(
            self.mapper.handle_event(press("f15")),
            {"action": "window_cycle", "modifier": False},
        )

    def test_f16_maps_to_select(self):
        self.assertEqual(
            self.mapper.handle_event(press("f16")),
            {"action": "select", "modifier": False},
        )

    def test_f17_maps_to_cancel(self):
        self.assertEqual(
            self.mapper.handle_event(press("f17")),
            {"action": "cancel", "modifier": False},
        )

    def test_f19_maps_to_select(self):
        self.assertEqual(
            self.mapper.handle_event(press("f19")),
            {"action": "select", "modifier": False},
        )

    def test_unmapped_key_returns_none(self):
        self.assertIsNone(self.mapper.handle_event(press("f20")))
        self.assertIsNone(self.mapper.handle_event(press("a")))

    def test_release_returns_none(self):
        self.mapper.handle_event(press("f16"))
        self.assertIsNone(self.mapper.handle_event(release("f16")))


class TestModifier(unittest.TestCase):
    """Holding F18 stamps modifier=True on subsequent payloads."""

    def setUp(self):
        self.mapper = KeyEventMapper()

    def test_modifier_key_itself_returns_none(self):
        self.assertIsNone(self.mapper.handle_event(press("f18")))
        self.assertIsNone(self.mapper.handle_event(release("f18")))

    def test_modifier_held_stamps_payload(self):
        self.mapper.handle_event(press("f18"))
        self.assertEqual(
            self.mapper.handle_event(press("f13")),
            {"action": "move_prev", "modifier": True},
        )

    def test_modifier_release_unstamps(self):
        self.mapper.handle_event(press("f18"))
        self.mapper.handle_event(release("f18"))
        self.assertEqual(
            self.mapper.handle_event(press("f13")),
            {"action": "move_prev", "modifier": False},
        )

    def test_modifier_repeat_keeps_held_state(self):
        # macOS key-repeat sends repeated key-down while held.
        self.mapper.handle_event(press("f18"))
        self.mapper.handle_event(press("f18"))
        self.assertEqual(
            self.mapper.handle_event(press("f14")),
            {"action": "move_next", "modifier": True},
        )


class TestEdgeDetection(unittest.TestCase):
    """OS key-repeat must not fire duplicate actions."""

    def setUp(self):
        self.mapper = KeyEventMapper()

    def test_held_key_fires_once(self):
        self.assertIsNotNone(self.mapper.handle_event(press("f16")))
        self.assertIsNone(self.mapper.handle_event(press("f16")))
        self.assertIsNone(self.mapper.handle_event(press("f16")))

    def test_release_rearms(self):
        self.mapper.handle_event(press("f14"))
        self.mapper.handle_event(release("f14"))
        self.assertEqual(
            self.mapper.handle_event(press("f14")),
            {"action": "move_next", "modifier": False},
        )

    def test_keys_tracked_independently(self):
        self.assertIsNotNone(self.mapper.handle_event(press("f13")))
        self.assertIsNotNone(self.mapper.handle_event(press("f14")))
        self.assertIsNone(self.mapper.handle_event(press("f13")))


class TestMalformedEvents(unittest.TestCase):
    """Malformed events return None instead of raising."""

    def setUp(self):
        self.mapper = KeyEventMapper()

    def test_empty_event(self):
        self.assertIsNone(self.mapper.handle_event({}))

    def test_missing_pressed(self):
        self.assertIsNone(self.mapper.handle_event({"key": "f13"}))

    def test_none_key(self):
        self.assertIsNone(
            self.mapper.handle_event({"key": None, "pressed": True})
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_input_listener.py -q`
Expected: collection ERROR — `ModuleNotFoundError: No module named 'core.input_listener'` (or ImportError). Any other failure reason means a typo — fix before proceeding.

- [ ] **Step 3: Write minimal implementation**

Create `core/input_listener.py`:

```python
"""
XP-Pen remote input listener: dead keys (F13-F19) → dashboard actions.

The XP-Pen driver app maps the remote's wheel/keys to dead function keys
that no macOS app uses. This module catches them globally (pynput) and
POSTs {"action", "modifier"} payloads to the dashboard's /api/input,
which resolves the traversal level and routes the action. See
docs/superpowers/specs/2026-07-09-xppen-remote-design.md.

Pure logic (KeyEventMapper, normalize_key, Backoff) is separated from
the pynput listener so it tests headlessly; pynput is imported lazily
and only inside run_listener()/main().
"""

from typing import Optional


# Dead-key contract — must match the XP-Pen driver app configuration.
KEY_ACTIONS = {
    "f13": "move_prev",     # wheel CCW
    "f14": "move_next",     # wheel CW
    "f15": "window_cycle",  # center wheel button
    "f16": "select",        # K1
    "f17": "cancel",        # K2
    "f19": "select",        # K9 (ergonomic duplicate)
}
MODIFIER_KEY = "f18"        # K7 — hold to shift wheel to the panes level


class KeyEventMapper:
    """Map dead-key events to action payloads with edge detection.

    Mirrors core.driver.GamepadController: a held key fires once (macOS
    key-repeat sends repeated key-down events) and must be released to
    re-arm. Holding the modifier key stamps modifier=True on payloads.
    """

    def __init__(self) -> None:
        self._held_keys: set = set()
        self._modifier_down = False

    def handle_event(self, event: dict) -> Optional[dict]:
        """Map a key event to an action payload, or return None.

        Args:
            event: {"key": str, "pressed": bool} — key is a lowercase
                name like "f13" (see normalize_key).

        Returns:
            {"action": str, "modifier": bool} on a rising edge of a
            mapped key; None for holds, releases, the modifier key
            itself, unmapped keys, and malformed events.
        """
        key = event.get("key")
        pressed = event.get("pressed")
        if not isinstance(key, str) or not isinstance(pressed, bool):
            return None

        if key == MODIFIER_KEY:
            self._modifier_down = pressed
            return None

        if not pressed:
            self._held_keys.discard(key)
            return None
        if key in self._held_keys:
            return None  # OS key-repeat — already fired on the edge
        self._held_keys.add(key)

        action = KEY_ACTIONS.get(key)
        if action is None:
            return None
        return {"action": action, "modifier": self._modifier_down}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_input_listener.py -q`
Expected: `18 passed`

- [ ] **Step 5: Commit**

```bash
git add core/input_listener.py tests/test_input_listener.py
git commit -m "feat(remote): KeyEventMapper for XP-Pen dead-key events

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Listener process — normalize_key, Backoff, post_action, main()

**Files:**
- Modify: `core/input_listener.py` (append to Task 1's file)
- Modify: `pyproject.toml` (add `remote` extra + console script)
- Test: `tests/test_input_listener.py` (append)

**Interfaces:**
- Consumes: `KeyEventMapper` from Task 1.
- Produces:
  - `normalize_key(key: object) -> Optional[str]` — pynput key object → lowercase name.
  - `Backoff(base: float = 1.0, cap: float = 30.0)` with `should_attempt(now: float) -> bool`, `record_failure(now: float) -> None`, `record_success() -> None`.
  - `post_action(url: str, payload: dict, timeout: float = 2.0) -> bool` — POSTs JSON, returns False on any error, never raises.
  - `main(argv: Optional[list] = None) -> int` and `python -m core.input_listener` / `iterm-mcp-input-listener` entry points.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_input_listener.py`:

```python
class TestNormalizeKey(unittest.TestCase):
    """pynput key objects normalize to lowercase names."""

    def test_named_key(self):
        from core.input_listener import normalize_key

        class FakeKey:
            name = "f13"

        self.assertEqual(normalize_key(FakeKey()), "f13")

    def test_uppercase_name_lowercased(self):
        from core.input_listener import normalize_key

        class FakeKey:
            name = "F14"

        self.assertEqual(normalize_key(FakeKey()), "f14")

    def test_character_key_returns_none(self):
        from core.input_listener import normalize_key

        class FakeKeyCode:  # pynput KeyCode has .char, no .name
            char = "a"

        self.assertIsNone(normalize_key(FakeKeyCode()))

    def test_none_returns_none(self):
        from core.input_listener import normalize_key

        self.assertIsNone(normalize_key(None))


class TestBackoff(unittest.TestCase):
    """Failures gate posting with capped exponential backoff."""

    def test_attempts_allowed_initially(self):
        from core.input_listener import Backoff

        b = Backoff()
        self.assertTrue(b.should_attempt(now=100.0))

    def test_failure_blocks_until_delay_elapses(self):
        from core.input_listener import Backoff

        b = Backoff(base=1.0, cap=30.0)
        b.record_failure(now=100.0)
        self.assertFalse(b.should_attempt(now=100.5))
        self.assertTrue(b.should_attempt(now=101.1))

    def test_consecutive_failures_double_delay_up_to_cap(self):
        from core.input_listener import Backoff

        b = Backoff(base=1.0, cap=4.0)
        b.record_failure(now=100.0)   # delay 1s
        b.record_failure(now=101.0)   # delay 2s
        b.record_failure(now=103.0)   # delay 4s
        b.record_failure(now=107.0)   # delay capped at 4s
        self.assertFalse(b.should_attempt(now=110.9))
        self.assertTrue(b.should_attempt(now=111.1))

    def test_success_resets(self):
        from core.input_listener import Backoff

        b = Backoff(base=1.0, cap=30.0)
        b.record_failure(now=100.0)
        b.record_success()
        self.assertTrue(b.should_attempt(now=100.1))


class TestPostAction(unittest.TestCase):
    """post_action never raises; returns False when the daemon is down."""

    def test_unreachable_url_returns_false(self):
        from core.input_listener import post_action

        # Port 1 on localhost is never listening.
        ok = post_action(
            "http://127.0.0.1:1/api/input",
            {"action": "select", "modifier": False},
            timeout=0.2,
        )
        self.assertFalse(ok)

    def test_posts_json_to_live_server(self):
        import http.server
        import json
        import threading

        from core.input_listener import post_action

        received = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                received.update(json.loads(self.rfile.read(length)))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = "http://127.0.0.1:%d/api/input" % server.server_port
            ok = post_action(url, {"action": "move_next", "modifier": True})
        finally:
            server.shutdown()
        self.assertTrue(ok)
        self.assertEqual(
            received, {"action": "move_next", "modifier": True}
        )


class TestMainWithoutPynput(unittest.TestCase):
    """main() exits with a clear message when pynput is missing."""

    def test_missing_pynput_exits_2(self):
        import builtins
        import unittest.mock as mock

        from core.input_listener import main

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("pynput"):
                raise ImportError("No module named 'pynput'")
            return real_import(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__", fake_import):
            exit_code = main(["--url", "http://127.0.0.1:1"])
        self.assertEqual(exit_code, 2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_input_listener.py -q`
Expected: Task 1's 18 still pass; the new tests fail with `ImportError: cannot import name 'normalize_key'` (etc.).

- [ ] **Step 3: Write the implementation**

Append to `core/input_listener.py` (add `import argparse, json, logging, os, sys, time, urllib.error, urllib.request` to the imports at the top, keeping stdlib grouping):

```python
logger = logging.getLogger(__name__)

DEFAULT_DASHBOARD_URL = os.environ.get(
    "ITERM_MCP_DASHBOARD_URL", "http://127.0.0.1:9999"
)


def normalize_key(key: object) -> Optional[str]:
    """Normalize a pynput key object to a lowercase name like 'f13'.

    Args:
        key: pynput Key (has .name) or KeyCode (has .char) or None.

    Returns:
        Lowercase key name for named keys, None for character keys
        and anything unrecognizable.
    """
    name = getattr(key, "name", None)
    if isinstance(name, str):
        return name.lower()
    return None


class Backoff:
    """Capped exponential backoff gate for posting during outages.

    Time is injected (pass `now`) so the logic tests headlessly.
    """

    def __init__(self, base: float = 1.0, cap: float = 30.0) -> None:
        self._base = base
        self._cap = cap
        self._delay = 0.0
        self._blocked_until = 0.0

    def should_attempt(self, now: float) -> bool:
        """Return True if a post may be attempted at time `now`."""
        return now >= self._blocked_until

    def record_failure(self, now: float) -> None:
        """Double the delay (capped) and block until it elapses."""
        self._delay = min(self._cap, self._delay * 2 or self._base)
        self._blocked_until = now + self._delay

    def record_success(self) -> None:
        """Reset the gate after a successful post."""
        self._delay = 0.0
        self._blocked_until = 0.0


def post_action(url: str, payload: dict, timeout: float = 2.0) -> bool:
    """POST a JSON action payload; never raises.

    Args:
        url: Full endpoint URL (e.g. http://127.0.0.1:9999/api/input).
        payload: JSON-serializable dict.
        timeout: Socket timeout in seconds.

    Returns:
        True on HTTP 2xx, False on any error (connection refused,
        timeout, non-2xx, etc.).
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("post_action failed: %s", exc)
        return False


def run_listener(dashboard_url: str) -> int:
    """Run the global key listener until interrupted.

    Imports pynput lazily so the module itself stays importable (and
    testable) without the optional dependency.

    Args:
        dashboard_url: Base URL of the dashboard server.

    Returns:
        Process exit code (0 normal, 2 pynput missing).
    """
    try:
        from pynput import keyboard
    except ImportError:
        sys.stderr.write(
            "pynput is not installed. Run: pip install 'iterm-mcp[remote]'\n"
        )
        return 2

    endpoint = dashboard_url.rstrip("/") + "/api/input"
    mapper = KeyEventMapper()
    backoff = Backoff()

    sys.stderr.write(
        "XP-Pen input listener started → %s\n"
        "If key presses don't register, grant Accessibility permission to\n"
        "%s in System Settings → Privacy & Security → Accessibility.\n"
        % (endpoint, sys.executable)
    )

    def dispatch(key: object, pressed: bool) -> None:
        name = normalize_key(key)
        if name is None:
            return
        payload = mapper.handle_event({"key": name, "pressed": pressed})
        if payload is None:
            return
        now = time.monotonic()
        if not backoff.should_attempt(now):
            return  # daemon outage — drop stale nav events
        if post_action(endpoint, payload):
            backoff.record_success()
        else:
            backoff.record_failure(time.monotonic())

    with keyboard.Listener(
        on_press=lambda key: dispatch(key, True),
        on_release=lambda key: dispatch(key, False),
    ) as listener:
        listener.join()
    return 0


def main(argv: Optional[list] = None) -> int:
    """CLI entry point for `iterm-mcp-input-listener`."""
    parser = argparse.ArgumentParser(
        description="XP-Pen remote → iterm-mcp dashboard input listener"
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_DASHBOARD_URL,
        help="Dashboard base URL (default: %(default)s or "
        "$ITERM_MCP_DASHBOARD_URL)",
    )
    args = parser.parse_args(argv)
    return run_listener(args.url)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Add the packaging hooks**

In `pyproject.toml`, add to `[project.optional-dependencies]` (after the `voice` entry):

```toml
# XP-Pen / macro-pad remote input — install with: pip install iterm-mcp[remote]
remote = [
    "pynput>=1.7",
]
```

And to `[project.scripts]`:

```toml
iterm-mcp-input-listener = "core.input_listener:main"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_input_listener.py -q`
Expected: `28 passed` (18 from Task 1 + 10 new). Note: pynput is NOT installed in `.venv` — `test_missing_pynput_exits_2` exercises the real missing-dep path plus the import mock for determinism.

- [ ] **Step 6: Commit**

```bash
git add core/input_listener.py tests/test_input_listener.py pyproject.toml
git commit -m "feat(remote): pynput listener process with backoff posting

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: ItermTerminal focus helpers (active session, panes, windows)

**Files:**
- Modify: `core/terminal.py` (append methods to `ItermTerminal`, after `focus_session` which ends near line 543)
- Test: `tests/test_terminal_focus_helpers.py`

**Interfaces:**
- Consumes: existing `ItermTerminal.get_session_by_id(session_id)` (async, returns `Optional[ItermSession]`), `self.app` (iTerm2 App: `.current_window`, `.windows`; window: `.window_id`, `.current_tab`, `.async_activate()`; tab: `.current_session`, `.sessions`; raw session: `.session_id`, `.async_activate()`).
- Produces (all on `ItermTerminal`):
  - `async def get_active_session(self) -> Optional["ItermSession"]`
  - `async def focus_relative_pane(self, delta: int) -> bool`
  - `async def cycle_window(self, delta: int = 1) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_terminal_focus_helpers.py`:

```python
"""Tests for ItermTerminal focus helpers used by /api/input routing.

Headless: the iTerm2 app object is faked with SimpleNamespace/AsyncMock.
ItermTerminal is instantiated via __new__ so no connection is needed.
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.terminal import ItermTerminal


def make_terminal(app):
    """Build an ItermTerminal shell without an iTerm2 connection."""
    terminal = ItermTerminal.__new__(ItermTerminal)
    terminal.app = app
    return terminal


def fake_session(session_id):
    return SimpleNamespace(session_id=session_id, async_activate=AsyncMock())


def fake_window(window_id, sessions, current_index=0):
    tab = SimpleNamespace(
        sessions=sessions,
        current_session=sessions[current_index] if sessions else None,
    )
    return SimpleNamespace(
        window_id=window_id,
        current_tab=tab,
        async_activate=AsyncMock(),
    )


class TestGetActiveSession(unittest.IsolatedAsyncioTestCase):
    async def test_returns_wrapper_for_current_session(self):
        raw = fake_session("s1")
        window = fake_window("w1", [raw])
        terminal = make_terminal(SimpleNamespace(current_window=window))
        wrapper = object()
        terminal.get_session_by_id = AsyncMock(return_value=wrapper)

        result = await terminal.get_active_session()

        self.assertIs(result, wrapper)
        terminal.get_session_by_id.assert_awaited_once_with("s1")

    async def test_no_current_window_returns_none(self):
        terminal = make_terminal(SimpleNamespace(current_window=None))
        self.assertIsNone(await terminal.get_active_session())

    async def test_no_app_returns_none(self):
        terminal = make_terminal(None)
        self.assertIsNone(await terminal.get_active_session())


class TestFocusRelativePane(unittest.IsolatedAsyncioTestCase):
    async def test_focuses_next_pane(self):
        s1, s2, s3 = fake_session("s1"), fake_session("s2"), fake_session("s3")
        window = fake_window("w1", [s1, s2, s3], current_index=0)
        terminal = make_terminal(SimpleNamespace(current_window=window))

        ok = await terminal.focus_relative_pane(1)

        self.assertTrue(ok)
        s2.async_activate.assert_awaited_once()

    async def test_focuses_prev_pane_wraps(self):
        s1, s2 = fake_session("s1"), fake_session("s2")
        window = fake_window("w1", [s1, s2], current_index=0)
        terminal = make_terminal(SimpleNamespace(current_window=window))

        ok = await terminal.focus_relative_pane(-1)

        self.assertTrue(ok)
        s2.async_activate.assert_awaited_once()  # wrapped to the end

    async def test_single_pane_is_noop_success(self):
        s1 = fake_session("s1")
        window = fake_window("w1", [s1])
        terminal = make_terminal(SimpleNamespace(current_window=window))

        ok = await terminal.focus_relative_pane(1)

        self.assertTrue(ok)
        s1.async_activate.assert_not_awaited()

    async def test_no_window_returns_false(self):
        terminal = make_terminal(SimpleNamespace(current_window=None))
        self.assertFalse(await terminal.focus_relative_pane(1))


class TestCycleWindow(unittest.IsolatedAsyncioTestCase):
    async def test_activates_next_window_round_robin(self):
        s = fake_session("s1")
        w1 = fake_window("w1", [s])
        w2 = fake_window("w2", [s])
        w3 = fake_window("w3", [s])
        app = SimpleNamespace(current_window=w3, windows=[w1, w2, w3])
        terminal = make_terminal(app)

        ok = await terminal.cycle_window(1)

        self.assertTrue(ok)
        w1.async_activate.assert_awaited_once()  # wrapped past the end

    async def test_single_window_is_noop_success(self):
        s = fake_session("s1")
        w1 = fake_window("w1", [s])
        app = SimpleNamespace(current_window=w1, windows=[w1])
        terminal = make_terminal(app)

        ok = await terminal.cycle_window(1)

        self.assertTrue(ok)
        w1.async_activate.assert_not_awaited()

    async def test_no_windows_returns_false(self):
        app = SimpleNamespace(current_window=None, windows=[])
        terminal = make_terminal(app)
        self.assertFalse(await terminal.cycle_window(1))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_terminal_focus_helpers.py -q`
Expected: FAIL — `AttributeError: 'ItermTerminal' object has no attribute 'get_active_session'` (and siblings).

- [ ] **Step 3: Write the implementation**

In `core/terminal.py`, insert immediately after the `focus_session` method (keep the class's existing style):

```python
    async def get_active_session(self) -> Optional["ItermSession"]:
        """Return the wrapper for iTerm's currently focused session.

        Returns:
            The ItermSession for the active pane, or None if there is
            no app connection, window, tab, or matching session.
        """
        if not self.app:
            return None
        window = self.app.current_window
        if not window or not window.current_tab:
            return None
        raw = window.current_tab.current_session
        if raw is None:
            return None
        return await self.get_session_by_id(raw.session_id)

    async def focus_relative_pane(self, delta: int) -> bool:
        """Move focus to the previous/next pane in the current window.

        Args:
            delta: -1 for previous, +1 for next (wraps around).

        Returns:
            True if focus is valid after the call (including the
            single-pane no-op), False if there is no window/tab/panes.
        """
        if not self.app:
            return False
        window = self.app.current_window
        if not window or not window.current_tab:
            return False
        sessions = window.current_tab.sessions
        current = window.current_tab.current_session
        if not sessions or current is None:
            return False
        if len(sessions) == 1:
            return True
        try:
            index = sessions.index(current)
        except ValueError:
            return False
        target = sessions[(index + delta) % len(sessions)]
        await target.async_activate()
        return True

    async def cycle_window(self, delta: int = 1) -> bool:
        """Activate the previous/next iTerm window, round-robin.

        Args:
            delta: -1 for previous, +1 for next (wraps around).

        Returns:
            True if a window is active after the call (including the
            single-window no-op), False if there are no windows.
        """
        if not self.app:
            return False
        windows = self.app.windows
        current = self.app.current_window
        if not windows or current is None:
            return False
        if len(windows) == 1:
            return True
        index = 0
        for i, window in enumerate(windows):
            if window.window_id == current.window_id:
                index = i
                break
        target = windows[(index + delta) % len(windows)]
        await target.async_activate()
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_terminal_focus_helpers.py -q`
Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add core/terminal.py tests/test_terminal_focus_helpers.py
git commit -m "feat(terminal): active-session, pane, and window focus helpers

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Dashboard `POST /api/input` — level resolution and routing

**Files:**
- Modify: `core/dashboard.py` (route table in `_handle_connection` around line 301; new handler after `_handle_answer`)
- Test: `tests/test_input_route.py`

**Interfaces:**
- Consumes: Task 3's `terminal.get_active_session()`, `terminal.focus_relative_pane(delta)`, `terminal.cycle_window(delta)`; existing `self._get_driver_store()` (`.pending_questions()`), `self._broadcast_named_event(event_type, data)`, `self._send_response(writer, status, content_type, body)`; `ItermSession.send_special_key(key)` accepting `"up"|"down"|"enter"|"escape"`.
- Produces: `POST /api/input` accepting `{"action": str, "modifier": bool}`.
  - Actions: `move_prev`, `move_next`, `select`, `cancel`, `window_cycle`.
  - 200 response: `{"status": "ok"|"dropped", "level": "tiles"|"tui"|"panes"|"window"}`.
  - 400 on unknown action / malformed body; 413 on oversize body.
  - SSE events: `action` → `{"action": str, "level": "tiles"}` (tiles routing); `notice` → `{"message": str}` (dropped-action toasts).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_input_route.py`:

```python
"""Tests for POST /api/input level resolution and routing.

Headless: DashboardServer is built via __new__ with fake terminal and
fake stream reader/writer objects. No sockets, no iTerm.
"""

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.dashboard import DashboardServer


class FakeReader:
    def __init__(self, body: bytes):
        self._body = body

    async def read(self, n: int) -> bytes:
        return self._body


class FakeWriter:
    """Captures _send_response output; parses status and JSON body."""

    def __init__(self):
        self.data = b""

    def write(self, chunk: bytes) -> None:
        self.data += chunk

    async def drain(self) -> None:
        pass

    @property
    def status(self) -> int:
        return int(self.data.split(b" ", 2)[1])

    @property
    def json(self) -> dict:
        return json.loads(self.data.split(b"\r\n\r\n", 1)[1])


def make_server(terminal=None, pending=False):
    """Build a DashboardServer shell wired with fakes."""
    server = DashboardServer.__new__(DashboardServer)
    server.terminal = terminal
    server._sse_clients = []
    server._driver_store = None
    server._broadcast_named_event = AsyncMock()
    if pending:
        store = server._get_driver_store()
        store.post_question("stop", "test?", [{"id": "a", "label": "A"}])
    else:
        server._get_driver_store()
    return server


async def call(server, payload):
    """POST payload to _handle_input; return the FakeWriter."""
    body = json.dumps(payload).encode()
    writer = FakeWriter()
    await server._handle_input(
        writer,
        FakeReader(body),
        {"content-length": str(len(body))},
    )
    return writer


class TestValidation(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_action_400(self):
        server = make_server()
        writer = await call(server, {"action": "explode", "modifier": False})
        self.assertEqual(writer.status, 400)

    async def test_missing_body_400(self):
        server = make_server()
        writer = FakeWriter()
        await server._handle_input(writer, FakeReader(b""), {})
        self.assertEqual(writer.status, 400)

    async def test_invalid_json_400(self):
        server = make_server()
        writer = FakeWriter()
        await server._handle_input(
            writer, FakeReader(b"not json"), {"content-length": "8"}
        )
        self.assertEqual(writer.status, 400)


class TestTilesRouting(unittest.IsolatedAsyncioTestCase):
    """Pending question + no modifier ⇒ SSE action event."""

    async def test_move_next_broadcasts_action(self):
        server = make_server(pending=True)
        writer = await call(server, {"action": "move_next", "modifier": False})

        self.assertEqual(writer.status, 200)
        self.assertEqual(writer.json, {"status": "ok", "level": "tiles"})
        server._broadcast_named_event.assert_awaited_once_with(
            "action", {"action": "move_next", "level": "tiles"}
        )


class TestTuiRouting(unittest.IsolatedAsyncioTestCase):
    """No pending question + no modifier ⇒ special key to active session."""

    async def test_select_sends_enter(self):
        session = AsyncMock()
        terminal = AsyncMock()
        terminal.get_active_session = AsyncMock(return_value=session)
        server = make_server(terminal=terminal)

        writer = await call(server, {"action": "select", "modifier": False})

        self.assertEqual(writer.json, {"status": "ok", "level": "tui"})
        session.send_special_key.assert_awaited_once_with("enter")

    async def test_move_prev_sends_up(self):
        session = AsyncMock()
        terminal = AsyncMock()
        terminal.get_active_session = AsyncMock(return_value=session)
        server = make_server(terminal=terminal)

        await call(server, {"action": "move_prev", "modifier": False})

        session.send_special_key.assert_awaited_once_with("up")

    async def test_cancel_sends_escape(self):
        session = AsyncMock()
        terminal = AsyncMock()
        terminal.get_active_session = AsyncMock(return_value=session)
        server = make_server(terminal=terminal)

        await call(server, {"action": "cancel", "modifier": False})

        session.send_special_key.assert_awaited_once_with("escape")

    async def test_no_active_session_drops_with_notice(self):
        terminal = AsyncMock()
        terminal.get_active_session = AsyncMock(return_value=None)
        server = make_server(terminal=terminal)

        writer = await call(server, {"action": "select", "modifier": False})

        self.assertEqual(writer.json, {"status": "dropped", "level": "tui"})
        server._broadcast_named_event.assert_awaited_once_with(
            "notice", {"message": "No active iTerm session"}
        )


class TestPanesRouting(unittest.IsolatedAsyncioTestCase):
    """Modifier held ⇒ pane focus moves, even with a question pending."""

    async def test_modifier_move_next_focuses_pane(self):
        terminal = AsyncMock()
        terminal.focus_relative_pane = AsyncMock(return_value=True)
        server = make_server(terminal=terminal, pending=True)

        writer = await call(server, {"action": "move_next", "modifier": True})

        self.assertEqual(writer.json, {"status": "ok", "level": "panes"})
        terminal.focus_relative_pane.assert_awaited_once_with(1)

    async def test_modifier_move_prev_focuses_pane(self):
        terminal = AsyncMock()
        terminal.focus_relative_pane = AsyncMock(return_value=True)
        server = make_server(terminal=terminal)

        await call(server, {"action": "move_prev", "modifier": True})

        terminal.focus_relative_pane.assert_awaited_once_with(-1)

    async def test_modifier_select_is_noop_ok(self):
        terminal = AsyncMock()
        server = make_server(terminal=terminal)

        writer = await call(server, {"action": "select", "modifier": True})

        self.assertEqual(writer.json, {"status": "ok", "level": "panes"})
        terminal.focus_relative_pane.assert_not_awaited()


class TestWindowCycle(unittest.IsolatedAsyncioTestCase):
    async def test_window_cycle_activates_next_window(self):
        terminal = AsyncMock()
        terminal.cycle_window = AsyncMock(return_value=True)
        server = make_server(terminal=terminal)

        writer = await call(
            server, {"action": "window_cycle", "modifier": False}
        )

        self.assertEqual(writer.json, {"status": "ok", "level": "window"})
        terminal.cycle_window.assert_awaited_once_with(1)

    async def test_window_cycle_failure_reports_dropped(self):
        terminal = AsyncMock()
        terminal.cycle_window = AsyncMock(return_value=False)
        server = make_server(terminal=terminal)

        writer = await call(
            server, {"action": "window_cycle", "modifier": False}
        )

        self.assertEqual(writer.json["status"], "dropped")


class TestTerminalErrorsDrop(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_exception_drops_not_500(self):
        terminal = AsyncMock()
        terminal.get_active_session = AsyncMock(
            side_effect=RuntimeError("connection lost")
        )
        server = make_server(terminal=terminal)

        writer = await call(server, {"action": "select", "modifier": False})

        self.assertEqual(writer.status, 200)
        self.assertEqual(writer.json["status"], "dropped")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_input_route.py -q`
Expected: FAIL — `AttributeError: 'DashboardServer' object has no attribute '_handle_input'`.

- [ ] **Step 3: Write the implementation**

In `core/dashboard.py`, add the route in `_handle_connection` after the `/api/answer` line (~line 301):

```python
            elif url_path == "/api/input":
                await self._handle_input(writer, reader, headers)
```

Add the handler after `_handle_answer` (module already imports `json` and `logger`):

```python
    # Actions accepted by POST /api/input (XP-Pen remote / any macro pad).
    INPUT_ACTIONS = {"move_prev", "move_next", "select", "cancel", "window_cycle"}
    # tui-level action → send_special_key name.
    TUI_KEYS = {
        "move_prev": "up",
        "move_next": "down",
        "select": "enter",
        "cancel": "escape",
    }

    async def _handle_input(
        self,
        writer: asyncio.StreamWriter,
        reader: asyncio.StreamReader,
        headers: Dict[str, str],
    ) -> None:
        """Handle POST /api/input — focus-independent remote actions.

        The listener process (core/input_listener.py) cannot see driver
        state, so it sends only {"action", "modifier"}; this handler
        resolves the effective level: modifier ⇒ panes, else tiles if a
        question is pending, else tui. See the XP-Pen design spec.

        Request body (JSON):
            action (str): move_prev|move_next|select|cancel|window_cycle
            modifier (bool, optional): True while the modifier key is held.

        Response (JSON, always 200 once validated):
            {"status": "ok"|"dropped", "level": "tiles"|"tui"|"panes"|"window"}
        """
        MAX_BODY_SIZE = 4 * 1024

        content_length = int(headers.get("content-length", 0))
        if content_length > MAX_BODY_SIZE:
            body = json.dumps({"error": "Request body too large"}).encode()
            await self._send_response(writer, 413, "application/json", body)
            return
        if content_length <= 0:
            body = json.dumps({"error": "Missing request body"}).encode()
            await self._send_response(writer, 400, "application/json", body)
            return

        raw = await reader.read(content_length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            body = json.dumps({"error": f"Invalid JSON: {exc}"}).encode()
            await self._send_response(writer, 400, "application/json", body)
            return

        action = data.get("action")
        modifier = bool(data.get("modifier", False))
        if action not in self.INPUT_ACTIONS:
            body = json.dumps(
                {"error": f"action must be one of {sorted(self.INPUT_ACTIONS)}"}
            ).encode()
            await self._send_response(writer, 400, "application/json", body)
            return

        status = "ok"
        try:
            if action == "window_cycle":
                level = "window"
                if not await self.terminal.cycle_window(1):
                    status = "dropped"
            elif modifier:
                level = "panes"
                if action == "move_prev":
                    status = "ok" if await self.terminal.focus_relative_pane(-1) else "dropped"
                elif action == "move_next":
                    status = "ok" if await self.terminal.focus_relative_pane(1) else "dropped"
                # select/cancel at the panes level are v1 no-ops.
            elif self._get_driver_store().pending_questions():
                level = "tiles"
                await self._broadcast_named_event(
                    "action", {"action": action, "level": "tiles"}
                )
            else:
                level = "tui"
                session = await self.terminal.get_active_session()
                if session is None:
                    status = "dropped"
                    await self._broadcast_named_event(
                        "notice", {"message": "No active iTerm session"}
                    )
                else:
                    await session.send_special_key(self.TUI_KEYS[action])
        except Exception as exc:
            # A lost iTerm connection must not 500 the remote's tight loop.
            logger.warning(f"[input] action {action} failed: {exc}")
            status = "dropped"

        body = json.dumps({"status": status, "level": level}).encode()
        await self._send_response(writer, 200, "application/json", body)
        logger.info(f"[input] action={action} level={level} status={status}")
```

Note: `level` is assigned in every branch before any await that can raise, except the `except` path where the failing branch already set it — Python scoping keeps it available.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_input_route.py tests/test_driver.py -q`
Expected: all pass (new file plus no regressions in the existing driver tests).

- [ ] **Step 5: Commit**

```bash
git add core/dashboard.py tests/test_input_route.py
git commit -m "feat(dashboard): POST /api/input with level-resolved routing

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Driver page — SSE action/notice handlers and level indicator

**Files:**
- Modify: `static/driver.js` (inside `connect()`, after the existing `cleared` listener; plus one helper near `showToast`)
- Modify: `static/driver.html` (header status area)
- Modify: `static/driver.css` (indicator style)

**Interfaces:**
- Consumes: SSE events from Task 4 (`action` → `{"action", "level"}`, `notice` → `{"message"}`); existing `applyGamepadAction(action)` and `showToast(msg, isError)` in `driver.js` (function declarations — hoisted, so the SSE handlers can call them).
- Produces: `#level-indicator` element showing the routing level of the last remote action.

- [ ] **Step 1: Add the level indicator to the page**

In `static/driver.html`, inside `<div id="status">`, before the status dot:

```html
      <span id="level-indicator" title="Remote routing level"></span>
```

In `static/driver.css`, after the `#status-dot.connecting` rule:

```css
#level-indicator {
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #60a5fa;
  background: #1e3a5f;
  border-radius: 3px;
  padding: 0.15rem 0.5rem;
  display: none;
}

#level-indicator.visible {
  display: inline-block;
}
```

- [ ] **Step 2: Wire the SSE handlers**

In `static/driver.js`, add a DOM ref with the others at the top:

```js
  const levelIndicator   = document.getElementById("level-indicator");
```

Add next to `showToast` (same section):

```js
  function showLevel(level) {
    levelIndicator.textContent = level;
    levelIndicator.classList.add("visible");
  }
```

Inside `connect()`, after the `cleared` listener:

```js
    // Named event: a remote (XP-Pen) action routed to the tiles level.
    evtSource.addEventListener("action", function (e) {
      let data;
      try {
        data = JSON.parse(e.data);
      } catch (err) {
        return;
      }
      showLevel(data.level || "tiles");
      applyGamepadAction(data.action);
    });

    // Named event: server-side notice worth surfacing (dropped actions).
    evtSource.addEventListener("notice", function (e) {
      let data;
      try {
        data = JSON.parse(e.data);
      } catch (err) {
        return;
      }
      if (data.message) showToast(data.message);
    });
```

- [ ] **Step 3: Syntax check**

Run: `node --check static/driver.js`
Expected: exit 0, no output.

- [ ] **Step 4: Run the JS + driver test files (guard against regressions)**

Run: `.venv/bin/python -m pytest tests/test_gamepad_js.py tests/test_driver.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add static/driver.js static/driver.html static/driver.css
git commit -m "feat(driver): SSE action/notice handlers and level indicator

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Docs, service registration recipe, full-suite gate

**Files:**
- Modify: `docs/gamepad-manual-test.md` (append XP-Pen section)
- Test: full CI-equivalent suite

**Interfaces:**
- Consumes: everything above; the services registry (`ServiceConfig.command` — services are shell commands managed by the `services` tool).

- [ ] **Step 1: Append the XP-Pen manual test + setup doc**

Append to `docs/gamepad-manual-test.md`:

```markdown
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
```

- [ ] **Step 2: Run the full CI-equivalent suite**

Run the Global Constraints command.
Expected: ≥ 1370 + ~40 new passed, 6 skipped, zero failures. If anything unrelated regressed, STOP and investigate before committing.

- [ ] **Step 3: Commit**

```bash
git add docs/gamepad-manual-test.md
git commit -m "docs(remote): XP-Pen setup and manual test checklist

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review Notes (already applied)

- Spec coverage: listener service (T1–T2), optional extra + entry point (T2), terminal helpers (T3), `/api/input` + SSE + level resolution (T4), driver page indicator/toasts (T5), manual doc + service recipe (T6). Spec's "service refuses to start without pynput" = exit code 2 path (T2). Spec's Accessibility failure mode is a startup hint (T2) — pynput cannot reliably detect denial; documented in the manual doc instead.
- Type consistency: payload `{"action", "modifier"}` identical in T1 mapper output, T2 post_action tests, T4 handler input. Level strings `tiles|tui|panes|window` identical in T4 handler/tests and T5 indicator.
- No placeholders: every step carries complete code/commands.
```
