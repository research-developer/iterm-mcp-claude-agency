"""Tests for the logging system."""

import asyncio
import os
import shutil
import tempfile
import unittest
import time

import iterm2

from core.terminal import ItermTerminal
from core.layouts import LayoutManager, LayoutType
from core.session import ItermSession
from core.test_window_tracker import mark_session
from utils.logging import ItermLogManager, ItermSessionLogger
from tests.live_iterm_base import LiveItermTestCase


class TestLogging(LiveItermTestCase):
    """Test the iTerm2 logging functionality."""

    async def async_setup(self):
        """Set up the test environment."""
        await super().async_setup()

        # Create a tagged test window (auto-closed by run_async_test teardown).
        self.test_session = await self.create_tagged_window(name="LogTestSession")
        # Wait for window to be ready
        await asyncio.sleep(1)

    def test_session_logger_creation(self):
        """Test that session loggers are created correctly."""
        async def test_impl():
            # Verify that a logger was created for the test session
            self.assertTrue(hasattr(self.test_session, "logger"))
            self.assertIsNotNone(self.test_session.logger)

            # Verify that the log file exists
            self.assertTrue(os.path.exists(self.test_session.logger.log_file))

            # Verify log file contains session information
            with open(self.test_session.logger.log_file, "r") as f:
                log_content = f.read()
                self.assertIn(f"Session started - ID: {self.test_session.id}", log_content)
                # Session name might be set by rename event, just check that the ID is correctly logged
                self.assertIn(self.test_session.id, log_content)

        self.run_async_test(test_impl)

    def test_command_logging(self):
        """Test that commands are logged."""
        async def test_impl():
            # Send a command
            test_command = "echo 'Test command logging'"
            await self.test_session.send_text(f"{test_command}\n")

            # Wait for command to complete
            await asyncio.sleep(1)

            # Verify that the command was logged
            with open(self.test_session.logger.log_file, "r") as f:
                log_content = f.read()
                self.assertIn(f"COMMAND: {test_command}", log_content)

        self.run_async_test(test_impl)

    def test_output_logging(self):
        """Test that output is logged."""
        async def test_impl():
            # Send a command
            test_output = "Test output logging"
            await self.test_session.send_text(f"echo '{test_output}'\n")

            # Wait for command to complete
            await asyncio.sleep(1)

            # Get screen contents to trigger output logging
            output = await self.test_session.get_screen_contents()

            # Verify that the output was logged
            with open(self.test_session.logger.log_file, "r") as f:
                log_content = f.read()
                self.assertIn(f"OUTPUT:", log_content)
                self.assertIn(test_output, log_content)

        self.run_async_test(test_impl)

    def test_control_character_logging(self):
        """Test that control characters are logged."""
        async def test_impl():
            # Send a control character
            await self.test_session.send_control_character("c")

            # Wait a moment
            await asyncio.sleep(1)

            # Verify that the control character was logged
            with open(self.test_session.logger.log_file, "r") as f:
                log_content = f.read()
                self.assertIn("CONTROL: Ctrl-C", log_content)

        self.run_async_test(test_impl)

    def test_session_rename_logging(self):
        """Test that session renames are logged."""
        async def test_impl():
            # Rename the session
            new_name = "RenamedLogTestSession"
            await self.test_session.set_name(new_name)

            # Verify that the rename was logged
            with open(self.test_session.logger.log_file, "r") as f:
                log_content = f.read()
                self.assertIn(f"RENAME: LogTestSession -> {new_name}", log_content)

        self.run_async_test(test_impl)

    def test_session_closure_logging(self):
        """Test that session closures are logged."""
        async def test_impl():
            # Get the log file path before closing
            log_file = self.test_session.logger.log_file

            # Create another session we can use after closing the test session.
            # Tag it so the base-class teardown closes it on failure.
            other_session = await self.terminal.create_window()
            await mark_session(other_session.session, self._tag)

            # Close the test session
            await self.terminal.close_session(self.test_session.id)

            # Verify that the closure was logged
            with open(log_file, "r") as f:
                log_content = f.read()
                self.assertIn(f"Session closed - ID: {self.test_session.id}", log_content)

            # Clean up the other session
            await self.terminal.close_session(other_session.id)

            # Prevent the teardown from trying to close the already closed session
            # (and the tagged teardown will gracefully skip already-closed sessions)
            delattr(self, "test_session")

        self.run_async_test(test_impl)


if __name__ == "__main__":
    unittest.main()
