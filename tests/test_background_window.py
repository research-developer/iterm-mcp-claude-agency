"""Tests for background-window-default feature in ItermTerminal.create_window.

Verifies the capture-and-restore mechanism that makes new windows open
in the background (without stealing focus) by default.

No live iTerm2 connection is required — all iTerm2 objects are mocked.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_terminal():
    """Build a minimally-mocked ItermTerminal suitable for create_window tests.

    Returns:
        A MagicMock that impersonates ItermTerminal with a populated ``app``
        and the real ``create_window`` method bound to it.
    """
    # Import here so the module-level patch on iterm2 is in effect.
    from core.terminal import ItermTerminal

    # Lightweight stand-in for iterm2.Connection — never actually called.
    fake_connection = MagicMock()

    terminal = ItermTerminal(connection=fake_connection, enable_logging=False)
    return terminal


def _make_app(previous_window=None, new_window=None):
    """Build a mock iterm2 app.

    Args:
        previous_window: The mock window that was current before creation.
        new_window: The mock window that async_create will produce.

    Returns:
        A MagicMock whose current_terminal_window is previous_window.
    """
    app = MagicMock()
    app.current_terminal_window = previous_window
    app.async_activate = AsyncMock()
    return app


def _make_window(session_id="sess-1"):
    """Build a mock iterm2.Window returned by async_create.

    Args:
        session_id: The ID string the inner mock session will report.

    Returns:
        A MagicMock that looks enough like an iterm2.Window to satisfy
        create_window's post-creation logic.
    """
    mock_session = MagicMock()
    mock_session.session_id = session_id

    mock_tab = MagicMock()
    mock_tab.sessions = [mock_session]

    window = MagicMock()
    window.tabs = [mock_tab]
    window.window_id = "win-1"
    window.async_activate = AsyncMock()
    return window


# ---------------------------------------------------------------------------
# Patch iterm2.Window.async_create at the location the SUT imports it.
# ItermTerminal uses `import iterm2` at the top of core/terminal.py, so we
# patch `iterm2.Window.async_create` directly.
# ---------------------------------------------------------------------------

class TestCreateWindowBackground(unittest.IsolatedAsyncioTestCase):
    """Default (foreground=False): new window opens in the background."""

    async def test_previous_window_activate_called_on_background_create(self):
        """After create_window(), the previous window's async_activate is awaited."""
        terminal = _make_terminal()

        previous_window = MagicMock()
        previous_window.async_activate = AsyncMock()
        previous_window.window_id = "win-prev"

        new_window = _make_window()
        new_window.window_id = "win-new"  # different from previous

        terminal.app = _make_app(previous_window=previous_window)

        with patch("iterm2.Window.async_create", new=AsyncMock(return_value=new_window)):
            session = await terminal.create_window()

        # Focus was restored to the previous window.
        previous_window.async_activate.assert_awaited_once()
        # The new window's activate was NOT called (no explicit promotion).
        new_window.async_activate.assert_not_awaited()
        # App-level activate was never called.
        terminal.app.async_activate.assert_not_awaited()
        # A session was still returned.
        self.assertIsNotNone(session)

    async def test_foreground_true_skips_restore(self):
        """With foreground=True the previous window's async_activate is NOT called."""
        terminal = _make_terminal()

        previous_window = MagicMock()
        previous_window.async_activate = AsyncMock()
        previous_window.window_id = "win-prev"

        new_window = _make_window()
        new_window.window_id = "win-new"

        terminal.app = _make_app(previous_window=previous_window)

        with patch("iterm2.Window.async_create", new=AsyncMock(return_value=new_window)):
            session = await terminal.create_window(foreground=True)

        previous_window.async_activate.assert_not_awaited()
        self.assertIsNotNone(session)

    async def test_no_previous_window_no_crash(self):
        """When no window was open before creation, no restore is attempted."""
        terminal = _make_terminal()

        new_window = _make_window()
        terminal.app = _make_app(previous_window=None)

        with patch("iterm2.Window.async_create", new=AsyncMock(return_value=new_window)):
            session = await terminal.create_window()

        # No crash; a session is returned.
        self.assertIsNotNone(session)
        # App-level activate was never called.
        terminal.app.async_activate.assert_not_awaited()

    async def test_restore_exception_does_not_propagate(self):
        """If the previous window's async_activate raises, create_window still succeeds."""
        terminal = _make_terminal()

        previous_window = MagicMock()
        previous_window.window_id = "win-prev"
        previous_window.async_activate = AsyncMock(side_effect=RuntimeError("window gone"))

        new_window = _make_window()
        new_window.window_id = "win-new"

        terminal.app = _make_app(previous_window=previous_window)

        with patch("iterm2.Window.async_create", new=AsyncMock(return_value=new_window)):
            # Must NOT raise even though async_activate raises.
            session = await terminal.create_window()

        # activate was attempted (and raised internally, but swallowed).
        previous_window.async_activate.assert_awaited_once()
        # A valid session is still returned.
        self.assertIsNotNone(session)


if __name__ == "__main__":
    unittest.main()
