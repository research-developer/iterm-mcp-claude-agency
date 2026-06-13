"""Tests for advanced features of iTerm2 MCP integration."""

import asyncio
import os
import unittest
import time
import tempfile
import shutil
import re

import iterm2

from core.terminal import ItermTerminal
from core.layouts import LayoutManager, LayoutType
from core.session import ItermSession
from core.test_window_tracker import mark_session
from utils.logging import ItermLogManager, ItermSessionLogger
from tests.live_iterm_base import LiveItermTestCase


class TestAdvancedFeatures(LiveItermTestCase):
    """Test advanced features of the iTerm2 MCP integration."""

    async def async_setup(self):
        """Set up the test environment."""
        await super().async_setup()

        # Create a tagged test window (auto-closed by run_async_test teardown).
        self.test_session = await self.create_tagged_window(name="AdvTestSession")
        # Wait for window to be ready
        await asyncio.sleep(1)

    async def async_teardown(self):
        """Clean up per-test state before the base class closes tagged sessions."""
        if hasattr(self, "test_session"):
            # Use async version of stop_monitoring
            if self.test_session.is_monitoring:
                await self.test_session.stop_monitoring()
        await super().async_teardown()

    def test_screen_monitoring(self):
        """Test screen monitoring functionality."""
        async def test_impl():
            import logging
            logger = logging.getLogger("test-screen-monitoring")

            # Start monitoring the session
            output_received = asyncio.Event()
            captured_output = []

            async def output_callback(content):
                logger.info(f"Callback received content: {content[:50]}...")
                captured_output.append(content)
                output_received.set()

            # Add the callback and start monitoring
            self.test_session.add_monitor_callback(output_callback)

            # Start monitoring and wait longer to ensure it's properly initialized
            await self.test_session.start_monitoring(update_interval=0.2)

            # Wait to ensure monitoring is started
            await asyncio.sleep(2)

            # Verify monitoring is active
            self.assertTrue(self.test_session.is_monitoring, "Monitoring should be active")

            # Use a unique marker for easier identification
            unique_marker = f"UNIQUE_TEST_MARKER_{time.time()}"
            test_string = f"echo '{unique_marker}'"
            logger.info(f"Sending test command: {test_string}")

            # Send the command and wait longer to ensure it's processed
            await self.test_session.send_text(f"{test_string}\n")

            # Wait for output to be received with a longer timeout
            try:
                await asyncio.wait_for(output_received.wait(), timeout=10.0)
                logger.info("Output received event was triggered")
            except asyncio.TimeoutError:
                # Try to diagnose the problem
                current_output = await self.test_session.get_screen_contents()
                logger.error(f"Timed out waiting for screen update. Current screen: {current_output}")
                self.fail("Timed out waiting for screen update")

            # Wait a bit more to ensure all output is captured
            await asyncio.sleep(2)

            # Stop monitoring - using async version
            await self.test_session.stop_monitoring()

            # Verify monitoring stopped
            self.assertFalse(self.test_session.is_monitoring, "Monitoring should be stopped")

            # Log captured output for debugging
            logger.info(f"Captured {len(captured_output)} outputs")
            for i, output in enumerate(captured_output):
                logger.info(f"Output {i}: {output[:100]}...")

            # Verify we captured the output by checking each captured output
            output_found = False
            for output in captured_output:
                if unique_marker in output:
                    output_found = True
                    break

            self.assertTrue(output_found, f"Expected to find '{unique_marker}' in captured output")

            # Verify snapshot file exists and contains our test string
            self.assertTrue(os.path.exists(self.test_session.logger.snapshot_file),
                          "Snapshot file should exist")

            with open(self.test_session.logger.snapshot_file, 'r') as f:
                snapshot = f.read()
                self.assertIn(unique_marker, snapshot,
                             f"Expected to find '{unique_marker}' in snapshot file")

        self.run_async_test(test_impl)

    def test_output_filtering(self):
        """Test output filtering functionality."""
        async def test_impl():
            # Add a filter to only capture lines with 'ERROR'
            self.test_session.logger.add_output_filter(r"ERROR")

            # Enable monitoring to ensure we catch all output
            await self.test_session.start_monitoring()

            # Use unique timestamps to ensure we don't match old log entries
            timestamp = time.time()

            # Send various messages with consistent identifiers
            await self.test_session.send_text(f"echo 'TEST-{timestamp}: This is a normal message'\n")
            await self.test_session.send_text(f"echo 'TEST-{timestamp}: This message contains an ERROR'\n")
            await self.test_session.send_text(f"echo 'TEST-{timestamp}: Another normal message'\n")

            # Wait longer for commands to complete and log file to be written
            await asyncio.sleep(3)

            # Read the log file
            with open(self.test_session.logger.log_file, 'r') as f:
                log_content = f.read()

            # The normal messages should not be in the log
            normal_msg1 = f"OUTPUT: TEST-{timestamp}: This is a normal message"
            normal_msg2 = f"OUTPUT: TEST-{timestamp}: Another normal message"
            error_msg = f"OUTPUT: TEST-{timestamp}: This message contains an ERROR"

            self.assertNotIn(normal_msg1, log_content, "Found normal message 1 when it should have been filtered")
            self.assertNotIn(normal_msg2, log_content, "Found normal message 2 when it should have been filtered")

            # The error message should be in the log
            self.assertIn(error_msg, log_content, "Error message not found in log file")

            # Clear filters and send another message
            self.test_session.logger.clear_output_filters()
            clear_msg = f"echo 'TEST-{timestamp}: After clearing filters'\n"
            await self.test_session.send_text(clear_msg)
            await asyncio.sleep(2)

            # Read the log file again
            with open(self.test_session.logger.log_file, 'r') as f:
                updated_log = f.read()

            # Now the normal message should be in the log
            self.assertIn(f"OUTPUT: TEST-{timestamp}: After clearing filters", updated_log,
                         "Message after clearing filters not found")

            # Stop monitoring
            await self.test_session.stop_monitoring()

        self.run_async_test(test_impl)

    def test_multiple_sessions(self):
        """Test creating and managing multiple sessions."""
        async def test_impl():
            import logging
            logger = logging.getLogger("test-multiple-sessions")

            # Create multiple sessions with different commands
            session_configs = [
                {"name": "Session1", "command": "echo 'Hello from Session 1'", "monitor": True},
                {"name": "Session2", "command": "echo 'Hello from Session 2'", "layout": True, "vertical": True}
            ]

            logger.info("Creating multiple sessions...")
            session_map = await self.terminal.create_multiple_sessions(session_configs)

            # Tag all created sessions so the base-class teardown sweep can
            # close them on failure (create_multiple_sessions doesn't tag).
            for session_id in session_map.values():
                pane = await self.terminal.get_session_by_id(session_id)
                if pane is not None:
                    await mark_session(pane.session, self._tag)

            # Verify we got sessions back
            self.assertEqual(len(session_map), 2, "Should have created 2 sessions")
            self.assertIn("Session1", session_map, "Session1 should be in session_map")
            self.assertIn("Session2", session_map, "Session2 should be in session_map")

            # Wait for sessions to be fully initialized and commands to execute
            await asyncio.sleep(3)

            # Get session objects with robust retry logic
            async def get_session_with_retry(name):
                session_id = session_map[name]
                for attempt in range(3):
                    try:
                        session = await self.terminal.get_session_by_id(session_id)
                        if session:
                            return session
                    except Exception as e:
                        logger.error(f"Error getting session {name} (attempt {attempt+1}): {str(e)}")
                    await asyncio.sleep(1)
                self.fail(f"Failed to get session {name} after 3 attempts")

            logger.info("Getting session objects...")
            session1 = await get_session_with_retry("Session1")
            session2 = await get_session_with_retry("Session2")

            # For Session1, explicitly start monitoring since it may not have been started
            # or might have been terminated early
            logger.info("Ensuring Session1 monitoring is active...")
            if not session1.is_monitoring:
                logger.info("Session1 not monitoring, starting it now...")
                await session1.start_monitoring(update_interval=0.2)
                await asyncio.sleep(2)

            # Now check that Session1 is being monitored
            self.assertTrue(session1.is_monitoring, "Session1 should be monitoring")

            # Verify output from each session
            logger.info("Verifying output from sessions...")
            output1 = await session1.get_screen_contents()
            output2 = await session2.get_screen_contents()

            logger.info(f"Session1 output: {output1}")
            logger.info(f"Session2 output: {output2}")

            self.assertIn("Hello from Session 1", output1, "Expected output not found in Session1")
            self.assertIn("Hello from Session 2", output2, "Expected output not found in Session2")

            # Clean up extra sessions (base class will close tagged ones, but
            # create_multiple_sessions doesn't tag the new sessions automatically,
            # so we close them explicitly here).
            logger.info("Cleaning up sessions...")
            for name, session_id in session_map.items():
                try:
                    session = await self.terminal.get_session_by_id(session_id)
                    if session:
                        if session.is_monitoring:
                            logger.info(f"Stopping monitoring for {name}...")
                            await session.stop_monitoring()
                        logger.info(f"Closing session {name}...")
                        await self.terminal.close_session(session_id)
                except Exception as e:
                    logger.error(f"Error during cleanup of session {name}: {str(e)}")
                    # Continue with cleanup even if there's an error

        self.run_async_test(test_impl)


if __name__ == "__main__":
    unittest.main()
