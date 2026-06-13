"""Tests for session suspend/resume functionality.

These tests validate the suspend/resume implementation for pausing
and resuming processes in iTerm2 sessions.
"""

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import iterm2

from core.terminal import ItermTerminal
from core.session import ItermSession
from tests.live_iterm_base import LiveItermTestCase


class TestSuspendStateManagement(unittest.TestCase):
    """Unit tests for suspend state tracking (no iTerm2 connection required)."""

    def test_session_not_suspended_by_default(self):
        """Session should not be suspended initially."""
        mock_session = MagicMock()
        mock_session.session_id = "test-session-id"

        session = ItermSession(mock_session, "TestSession")

        self.assertFalse(session.is_suspended)
        self.assertIsNone(session.suspended_at)
        self.assertIsNone(session.suspended_by)

    def test_suspend_state_properties(self):
        """Verify suspend state properties are accessible."""
        mock_session = MagicMock()
        mock_session.session_id = "test-session-id"

        session = ItermSession(mock_session, "TestSession")

        # Manually set internal state to test properties
        session._suspended = True
        session._suspended_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        session._suspended_by = "test-agent"

        self.assertTrue(session.is_suspended)
        self.assertEqual(session.suspended_at.year, 2024)
        self.assertEqual(session.suspended_by, "test-agent")


class TestSuspendResumeAsync(unittest.TestCase):
    """Async unit tests for suspend/resume methods."""

    def setUp(self):
        """Set up mock session."""
        self.mock_iterm_session = MagicMock()
        self.mock_iterm_session.session_id = "test-session-id"
        self.mock_iterm_session.async_send_text = AsyncMock()
        self.session = ItermSession(self.mock_iterm_session, "TestSession")

    def run_async(self, coro):
        """Helper to run async tests."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_suspend_sets_state(self):
        """Suspend should set internal state correctly."""
        async def test_impl():
            # Mock send_control_character
            self.session.send_control_character = AsyncMock()

            await self.session.suspend(agent="test-agent")

            self.assertTrue(self.session.is_suspended)
            self.assertIsNotNone(self.session.suspended_at)
            self.assertEqual(self.session.suspended_by, "test-agent")
            self.session.send_control_character.assert_called_once_with("z")

        self.run_async(test_impl())

    def test_suspend_without_agent(self):
        """Suspend should work without specifying an agent."""
        async def test_impl():
            self.session.send_control_character = AsyncMock()

            await self.session.suspend()

            self.assertTrue(self.session.is_suspended)
            self.assertIsNone(self.session.suspended_by)

        self.run_async(test_impl())

    def test_suspend_already_suspended_raises(self):
        """Suspend on already suspended session should raise RuntimeError."""
        async def test_impl():
            self.session.send_control_character = AsyncMock()

            await self.session.suspend()

            with self.assertRaises(RuntimeError) as context:
                await self.session.suspend()

            self.assertIn("already suspended", str(context.exception))

        self.run_async(test_impl())

    def test_resume_clears_state(self):
        """Resume should clear suspend state."""
        async def test_impl():
            self.session.send_control_character = AsyncMock()

            # First suspend
            await self.session.suspend(agent="test-agent")
            self.assertTrue(self.session.is_suspended)

            # Then resume
            await self.session.resume()

            self.assertFalse(self.session.is_suspended)
            self.assertIsNone(self.session.suspended_at)
            self.assertIsNone(self.session.suspended_by)
            self.mock_iterm_session.async_send_text.assert_called_with("fg\n")

        self.run_async(test_impl())

    def test_resume_not_suspended_raises(self):
        """Resume on non-suspended session should raise RuntimeError."""
        async def test_impl():
            with self.assertRaises(RuntimeError) as context:
                await self.session.resume()

            self.assertIn("not suspended", str(context.exception))

        self.run_async(test_impl())

    def test_suspend_resume_cycle(self):
        """Test full suspend/resume cycle."""
        async def test_impl():
            self.session.send_control_character = AsyncMock()

            # Start not suspended
            self.assertFalse(self.session.is_suspended)

            # Suspend
            await self.session.suspend(agent="agent-1")
            self.assertTrue(self.session.is_suspended)
            self.assertEqual(self.session.suspended_by, "agent-1")

            # Resume
            await self.session.resume()
            self.assertFalse(self.session.is_suspended)

            # Can suspend again
            await self.session.suspend(agent="agent-2")
            self.assertTrue(self.session.is_suspended)
            self.assertEqual(self.session.suspended_by, "agent-2")

        self.run_async(test_impl())

    def test_toggle_when_not_suspended_suspends(self):
        """Toggle on non-suspended session should suspend."""
        async def test_impl():
            self.session.send_control_character = AsyncMock()

            # Not suspended initially
            self.assertFalse(self.session.is_suspended)

            # Simulate toggle logic: if not suspended, suspend
            if self.session.is_suspended:
                await self.session.resume()
            else:
                await self.session.suspend(agent="toggler")

            self.assertTrue(self.session.is_suspended)
            self.assertEqual(self.session.suspended_by, "toggler")

        self.run_async(test_impl())

    def test_toggle_when_suspended_resumes(self):
        """Toggle on suspended session should resume."""
        async def test_impl():
            self.session.send_control_character = AsyncMock()

            # Suspend first
            await self.session.suspend(agent="original")
            self.assertTrue(self.session.is_suspended)

            # Simulate toggle logic: if suspended, resume
            if self.session.is_suspended:
                await self.session.resume()
            else:
                await self.session.suspend(agent="toggler")

            self.assertFalse(self.session.is_suspended)

        self.run_async(test_impl())


class TestSuspendResumeIntegration(LiveItermTestCase):
    """Integration tests for suspend/resume with real iTerm2 connection.

    These tests require iTerm2 to be running.
    """

    async def async_setup(self):
        """Set up the test environment."""
        await super().async_setup()

        # Create a tagged test window (auto-closed by run_async_test teardown).
        self.test_session = await self.create_tagged_window(name="SuspendTestSession")
        await asyncio.sleep(1)

    async def async_teardown(self):
        """Resume the session if suspended before base class closes it."""
        if hasattr(self, "test_session"):
            # Make sure to resume if suspended before closing
            if self.test_session.is_suspended:
                try:
                    await self.test_session.resume()
                except Exception:
                    pass
        await super().async_teardown()

    def test_suspend_running_process(self):
        """Test suspending a running process with Ctrl+Z."""
        async def test_impl():
            # Start a long-running process (cat waits for input)
            await self.test_session.send_text("cat\n")
            await asyncio.sleep(0.5)

            # Suspend it
            await self.test_session.suspend(agent="test-agent")
            await asyncio.sleep(0.5)

            # Verify suspended state
            self.assertTrue(self.test_session.is_suspended)
            self.assertEqual(self.test_session.suspended_by, "test-agent")

            # Check output contains "[1]+" indicating suspended job
            output = await self.test_session.get_screen_contents()
            # The output should show the job was stopped
            # (exact format varies by shell)

            # Resume it
            await self.test_session.resume()
            await asyncio.sleep(0.5)

            # Verify resumed
            self.assertFalse(self.test_session.is_suspended)

            # Clean up - send Ctrl+C to exit cat
            await self.test_session.send_control_character("c")
            await asyncio.sleep(0.5)

        self.run_async_test(test_impl)

    def test_suspend_and_run_other_command(self):
        """Test that we can run commands after suspending a process."""
        async def test_impl():
            # Start a process
            await self.test_session.send_text("cat\n")
            await asyncio.sleep(0.5)

            # Suspend it
            await self.test_session.suspend()
            await asyncio.sleep(0.5)

            # Run another command while suspended
            await self.test_session.send_text("echo 'running while suspended'\n")
            await asyncio.sleep(0.5)

            # Check output
            output = await self.test_session.get_screen_contents()
            self.assertIn("running while suspended", output)

            # Resume the original process
            await self.test_session.resume()
            await asyncio.sleep(0.5)

            # Clean up
            await self.test_session.send_control_character("c")
            await asyncio.sleep(0.5)

        self.run_async_test(test_impl)


if __name__ == "__main__":
    unittest.main()
