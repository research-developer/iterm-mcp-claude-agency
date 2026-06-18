"""Tests for the voice arm-state machine (no real ~/.iterm-mcp writes)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.voice import session


class TestSession(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch_path = mock.patch.object(
            session, "STATE_PATH", Path(self._tmp.name) / "state.json"
        )
        self._patch_path.start()
        self._t = [1000.0]
        self._patch_now = mock.patch.object(session, "_now", lambda: self._t[0])
        self._patch_now.start()

    def tearDown(self):
        self._patch_path.stop()
        self._patch_now.stop()
        self._tmp.cleanup()

    def test_disarmed_by_default(self):
        self.assertFalse(session.is_armed())

    def test_arm_then_armed(self):
        session.arm(timeout_s=600)
        self.assertTrue(session.is_armed())

    def test_idle_auto_disarm(self):
        session.arm(timeout_s=600)
        self._t[0] += 601
        self.assertFalse(session.is_armed())

    def test_touch_refreshes_idle(self):
        session.arm(timeout_s=600)
        self._t[0] += 300
        session.touch()
        self._t[0] += 400  # 400 since touch < 600
        self.assertTrue(session.is_armed())

    def test_disarm(self):
        session.arm()
        session.disarm()
        self.assertFalse(session.is_armed())

    def test_status_reports_armed(self):
        session.arm()
        self.assertTrue(session.status()["armed"])


if __name__ == "__main__":
    unittest.main()
