"""Basic functionality tests for iTerm2 MCP integration."""

import asyncio
import os
import unittest
import time

import iterm2

from core.terminal import ItermTerminal
from core.layouts import LayoutManager, LayoutType
from core.session import ItermSession
from tests.live_iterm_base import LiveItermTestCase


class TestBasicFunctionality(LiveItermTestCase):
    """Test basic functionality of the iTerm2 MCP integration."""

    async def async_setup(self):
        """Set up the test environment."""
        await super().async_setup()

        self.layout_manager = LayoutManager(self.terminal)

        # Create a tagged test window (auto-closed by run_async_test teardown).
        self.test_session = await self.create_tagged_window(name="TestSession")
        # Wait for window to be ready
        await asyncio.sleep(1)

    def test_create_window(self):
        """Test creating a window and setting its name."""
        async def test_impl():
            # Verify that the session exists - note that it might be updated to -zsh or other shell
            session = await self.terminal.get_session_by_id(self.test_session.id)
            self.assertIsNotNone(session)
            self.assertEqual(session.id, self.test_session.id)

        self.run_async_test(test_impl)

    def test_send_and_receive_text(self):
        """Test sending text to a session and reading output."""
        async def test_impl():
            # Send a simple command
            await self.test_session.send_text("echo 'Hello, iTerm MCP!'\n")

            # Wait for command to complete
            await asyncio.sleep(1)

            # Get screen contents
            output = await self.test_session.get_screen_contents()

            # Verify the output
            self.assertIn("Hello, iTerm MCP!", output)

        self.run_async_test(test_impl)

    def test_send_control_character(self):
        """Test sending a control character to a session."""
        async def test_impl():
            # Start a command that will hang
            await self.test_session.send_text("cat\n")

            # Wait for command to start
            await asyncio.sleep(1)

            # Send Ctrl-C to cancel
            await self.test_session.send_control_character("c")

            # Wait for command to be cancelled
            await asyncio.sleep(1)

            # Verify that command was cancelled (by running another command)
            await self.test_session.send_text("echo 'Command cancelled'\n")

            # Wait for command to complete
            await asyncio.sleep(1)

            # Get screen contents
            output = await self.test_session.get_screen_contents()

            # Verify the output
            self.assertIn("Command cancelled", output)

        self.run_async_test(test_impl)

    def test_create_layout(self):
        """Test creating a layout with multiple panes."""
        async def test_impl():
            # Create a horizontal split layout
            session_map = await self.layout_manager.create_layout(
                layout_type=LayoutType.HORIZONTAL_SPLIT,
                pane_names=["LeftPane", "RightPane"]
            )

            # Verify that we got session IDs back for both panes
            for name in ["LeftPane", "RightPane"]:
                session_id = session_map[name]
                session = await self.terminal.get_session_by_id(session_id)
                self.assertIsNotNone(session)
                # Simply verify the session exists - don't check names as they might change

            # Clean up the created sessions
            for session_id in session_map.values():
                await self.terminal.close_session(session_id)

        self.run_async_test(test_impl)


if __name__ == "__main__":
    unittest.main()
