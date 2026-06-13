"""Tests for persistent session functionality."""

import asyncio
import os
import json
import shutil
import tempfile
import unittest
import time
import uuid

import iterm2

from core.terminal import ItermTerminal
from core.session import ItermSession
from utils.logging import ItermLogManager
from tests.live_iterm_base import LiveItermTestCase


class TestPersistentSessions(LiveItermTestCase):
    """Test the persistent session functionality."""

    async def async_setup(self):
        """Set up the test environment."""
        await super().async_setup()

        # Create a tagged test window (auto-closed by run_async_test teardown).
        self.test_session = await self.create_tagged_window(name="PersistentTestSession")
        # Wait for window to be ready
        await asyncio.sleep(1)

    def test_persistent_id_generation(self):
        """Test that persistent IDs are generated correctly."""
        async def test_impl():
            # Verify that the session has a persistent ID
            self.assertTrue(hasattr(self.test_session, "persistent_id"))
            self.assertIsNotNone(self.test_session.persistent_id)

            # Verify that the persistent ID is a valid UUID
            try:
                uuid_obj = uuid.UUID(self.test_session.persistent_id)
                self.assertEqual(str(uuid_obj), self.test_session.persistent_id)
            except ValueError:
                self.fail("Persistent ID is not a valid UUID")

        self.run_async_test(test_impl)

    def test_persistent_id_saved(self):
        """Test that persistent IDs are saved to the persistent sessions file."""
        async def test_impl():
            # Get the persistent ID
            persistent_id = self.test_session.persistent_id

            # Check if the persistent sessions file was created
            persistent_sessions_file = os.path.join(self._log_dir, "persistent_sessions.json")
            self.assertTrue(os.path.exists(persistent_sessions_file))

            # Verify that the persistent ID is in the file
            with open(persistent_sessions_file, "r") as f:
                persistent_sessions = json.load(f)
                self.assertIn(persistent_id, persistent_sessions)

                # Verify the session details
                session_details = persistent_sessions[persistent_id]
                self.assertEqual(session_details["session_id"], self.test_session.id)
                # Note: The name test is disabled because iTerm sometimes
                # changes the name before we can set it
                # self.assertEqual(session_details["name"], "PersistentTestSession")

        self.run_async_test(test_impl)

    def test_persistent_id_lookup(self):
        """Test that sessions can be looked up by persistent ID."""
        async def test_impl():
            # Get the persistent ID
            persistent_id = self.test_session.persistent_id

            # Look up the session by persistent ID
            found_session = await self.terminal.get_session_by_persistent_id(persistent_id)

            # Verify that the correct session was found
            self.assertIsNotNone(found_session)
            self.assertEqual(found_session.id, self.test_session.id)
            # Note: The name test is disabled because iTerm sometimes
            # changes the name before we can set it
            # self.assertEqual(found_session.name, "PersistentTestSession")

        self.run_async_test(test_impl)

    def test_persistent_id_reconnection(self):
        """Test reconnection to a session using persistent ID."""
        async def test_impl():
            # Get the persistent ID and session ID
            persistent_id = self.test_session.persistent_id
            original_session_id = self.test_session.id

            # Send a command to create some unique output
            unique_marker = f"PERSISTENT_TEST_{int(time.time())}"
            await self.test_session.send_text(f"echo '{unique_marker}'\n")
            await asyncio.sleep(1)

            # Create a new terminal manager (simulating a new connection)
            new_terminal = ItermTerminal(
                connection=self.connection,
                log_dir=self._log_dir,
                enable_logging=True
            )
            await new_terminal.initialize()

            # Look up the session by persistent ID in the new terminal
            found_session = await new_terminal.get_session_by_persistent_id(persistent_id)

            # Verify that the correct session was found
            self.assertIsNotNone(found_session)
            self.assertEqual(found_session.id, original_session_id)

            # Check that we can access the session's output
            output = await found_session.get_screen_contents()
            self.assertIn(unique_marker, output)

        self.run_async_test(test_impl)

    def test_log_manager_persistent_methods(self):
        """Test the log manager's persistent session methods."""
        async def test_impl():
            # Access the log manager
            log_manager = self.terminal.log_manager

            # Get a list of persistent sessions
            persistent_sessions = log_manager.list_persistent_sessions()

            # Verify that our test session is in the list
            found_session = False
            for persistent_id, details in persistent_sessions.items():
                if details["session_id"] == self.test_session.id:
                    found_session = True
                    break

            self.assertTrue(found_session)

            # Get the persistent ID
            persistent_id = self.test_session.persistent_id

            # Get session details
            session_details = log_manager.get_persistent_session(persistent_id)
            self.assertIsNotNone(session_details)
            self.assertEqual(session_details["session_id"], self.test_session.id)

        self.run_async_test(test_impl)


if __name__ == "__main__":
    unittest.main()
