"""Tests for daemon state file and port selection (no iTerm2 required)."""
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestStateFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patcher = mock.patch(
            "iterm_mcpy.daemon.STATE_DIR", Path(self.tmp.name)
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_write_then_read_round_trips(self):
        from iterm_mcpy import daemon
        daemon.write_state(port=12341)
        state = daemon.read_state()
        self.assertEqual(state["port"], 12341)
        self.assertEqual(state["endpoint"], "http://127.0.0.1:12341/mcp")
        self.assertIsInstance(state["pid"], int)
        self.assertIn("version", state)

    def test_read_missing_returns_none(self):
        from iterm_mcpy import daemon
        self.assertIsNone(daemon.read_state())

    def test_read_corrupt_returns_none(self):
        from iterm_mcpy import daemon
        (Path(self.tmp.name) / "daemon.json").write_text("{not json")
        self.assertIsNone(daemon.read_state())

    def test_clear_state(self):
        from iterm_mcpy import daemon
        daemon.write_state(port=12341)
        daemon.clear_state()
        self.assertIsNone(daemon.read_state())


class TestPortSelection(unittest.TestCase):
    def test_skips_occupied_port(self):
        from iterm_mcpy import daemon
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 12340))
        blocker.listen(1)
        self.addCleanup(blocker.close)
        port = daemon.find_free_port()
        self.assertNotEqual(port, 12340)
        self.assertIn(port, range(12340, 12350))


if __name__ == "__main__":
    unittest.main()
