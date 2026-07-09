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
