"""Unit tests for daemon port resolution (pinned/preferred port).

These are pure-logic tests: importing iterm_mcpy.daemon pulls in only the
standard library (FastMCP/iterm2 are lazy-imported inside run_daemon), so
running this module never touches iTerm2.
"""

import json
import socket
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from iterm_mcpy import daemon


class PreferredPortTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.cfg = Path(self._tmp.name) / "config.json"
        # Point the module's CONFIG_PATH at an isolated temp file and start
        # from a clean environment for every case.
        self._patchers = [
            mock.patch.object(daemon, "CONFIG_PATH", self.cfg),
            mock.patch.dict(daemon.os.environ, {}, clear=False),
        ]
        for p in self._patchers:
            p.start()
        daemon.os.environ.pop("ITERM_MCP_PORT", None)

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        self._tmp.cleanup()

    def _write_cfg(self, obj):
        self.cfg.write_text(json.dumps(obj))

    def test_none_when_unset(self):
        self.assertIsNone(daemon.preferred_port())

    def test_config_file_used(self):
        self._write_cfg({"preferred_port": 12345})
        self.assertEqual(daemon.preferred_port(), 12345)

    def test_env_var_overrides_config(self):
        self._write_cfg({"preferred_port": 12345})
        daemon.os.environ["ITERM_MCP_PORT"] = "23456"
        self.assertEqual(daemon.preferred_port(), 23456)

    def test_invalid_env_falls_through_to_config(self):
        # An empty/junk env var (e.g. `export ITERM_MCP_PORT=`) must not
        # silently disable a valid persisted pin.
        self._write_cfg({"preferred_port": 12345})
        for bad in ("", "not-a-port", "99999"):
            with self.subTest(bad=bad):
                daemon.os.environ["ITERM_MCP_PORT"] = bad
                self.assertEqual(daemon.preferred_port(), 12345)

    def test_invalid_env_and_no_config_is_none(self):
        daemon.os.environ["ITERM_MCP_PORT"] = "not-a-port"
        self.assertIsNone(daemon.preferred_port())

    def test_out_of_range_rejected(self):
        self._write_cfg({"preferred_port": 99999})
        self.assertIsNone(daemon.preferred_port())

    def test_missing_config_key_is_none(self):
        self._write_cfg({"something_else": 1})
        self.assertIsNone(daemon.preferred_port())

    def test_corrupt_config_is_none(self):
        self.cfg.write_text("{ not json")
        self.assertIsNone(daemon.preferred_port())


class FindFreePortTests(unittest.TestCase):
    def setUp(self):
        # Isolate CONFIG_PATH to an empty temp file so preferred_port() can't
        # pick up the developer's real ~/.iterm-mcp/config.json pin.
        self._tmp = TemporaryDirectory()
        self._patchers = [
            mock.patch.object(daemon, "CONFIG_PATH",
                              Path(self._tmp.name) / "config.json"),
            mock.patch.dict(daemon.os.environ, {}, clear=False),
        ]
        for p in self._patchers:
            p.start()
        daemon.os.environ.pop("ITERM_MCP_PORT", None)

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        self._tmp.cleanup()

    def test_returns_pinned_port_when_free(self):
        # Reserve then release an ephemeral port so we know it's bindable.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        daemon.os.environ["ITERM_MCP_PORT"] = str(free_port)
        self.assertEqual(daemon.find_free_port(), free_port)

    def test_falls_back_to_range_when_pinned_busy(self):
        # Hold a pinned port so it can't be bound; find_free_port must fall
        # back to the documented range rather than raising.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
            held.bind(("127.0.0.1", 0))
            held.listen(1)
            busy_port = held.getsockname()[1]
            daemon.os.environ["ITERM_MCP_PORT"] = str(busy_port)
            chosen = daemon.find_free_port()
        self.assertIn(chosen, daemon.PORT_RANGE)
        self.assertNotEqual(chosen, busy_port)


class SetPreferredPortTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.cfg = Path(self._tmp.name) / "config.json"
        self._patchers = [
            mock.patch.object(daemon, "CONFIG_PATH", self.cfg),
            mock.patch.object(daemon, "STATE_DIR", Path(self._tmp.name)),
            mock.patch.dict(daemon.os.environ, {}, clear=False),
        ]
        for p in self._patchers:
            p.start()
        daemon.os.environ.pop("ITERM_MCP_PORT", None)

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        self._tmp.cleanup()

    def test_round_trip_set_then_read(self):
        daemon.set_preferred_port(12345)
        self.assertEqual(daemon.preferred_port(), 12345)
        self.assertEqual(json.loads(self.cfg.read_text())["preferred_port"], 12345)

    def test_clear_with_none_removes_pin(self):
        daemon.set_preferred_port(12345)
        daemon.set_preferred_port(None)
        self.assertIsNone(daemon.preferred_port())
        self.assertNotIn("preferred_port", json.loads(self.cfg.read_text()))

    def test_preserves_unrelated_keys(self):
        self.cfg.write_text(json.dumps({"other": "keep-me"}))
        daemon.set_preferred_port(12345)
        cfg = json.loads(self.cfg.read_text())
        self.assertEqual(cfg["other"], "keep-me")
        self.assertEqual(cfg["preferred_port"], 12345)

    def test_no_temp_file_left_behind(self):
        daemon.set_preferred_port(12345)
        leftovers = [p.name for p in Path(self._tmp.name).glob("*.tmp")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
