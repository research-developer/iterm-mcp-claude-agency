"""Tests for the project-declaration UserPromptSubmit hook (headless)."""
import json
import os
import tempfile
import unittest
from unittest import mock

from hooks import project_declare as ph


class TestProjectDeclareHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = mock.patch.object(ph, "MARKER_DIR", os.path.join(self.tmp.name, "projects"))
        self.p.start()
        self.addCleanup(self.p.stop)
        os.makedirs(ph.MARKER_DIR, exist_ok=True)

    def _decide(self, session_id):
        return ph.decide({"session_id": session_id, "hook_event_name": "UserPromptSubmit"})

    def test_injects_when_undeclared(self):
        out = self._decide("s1")
        self.assertIn("additionalContext", out["hookSpecificOutput"])
        self.assertIn("iterm-mcp project set", out["hookSpecificOutput"]["additionalContext"])

    def test_noop_when_declared(self):
        open(os.path.join(ph.MARKER_DIR, "s2"), "w").close()
        out = self._decide("s2")
        self.assertNotIn("additionalContext", out["hookSpecificOutput"])

    def test_stops_after_max_prompts(self):
        for _ in range(ph.MAX_PROMPTS):
            self.assertIn("additionalContext", self._decide("s3")["hookSpecificOutput"])
        self.assertNotIn("additionalContext", self._decide("s3")["hookSpecificOutput"])

    def test_shape_is_userpromptsubmit(self):
        out = self._decide("s4")
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")


if __name__ == "__main__":
    unittest.main()
