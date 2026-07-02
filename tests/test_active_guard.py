"""Tests for the active-iTerm guard that keeps live tests from opening windows.

These are fully headless: they mock the frontmost-app detector and never open
an iTerm2 window or touch a live connection.
"""
import os
import unittest
from unittest import mock

from core import test_window_tracker as twt


class TestSkipReasonIfItermActive(unittest.TestCase):
    def setUp(self):
        # Make sure no override leaks in from the real environment.
        os.environ.pop(twt.ALLOW_ACTIVE_ENV, None)

    def test_skips_when_iterm_frontmost(self):
        with mock.patch.object(twt, "iterm_frontmost_state", return_value=True):
            reason = twt.skip_reason_if_iterm_active()
        self.assertIsNotNone(reason)
        self.assertIn("frontmost", reason)
        self.assertIn(twt.ALLOW_ACTIVE_ENV, reason)

    def test_skips_when_undetermined(self):
        with mock.patch.object(twt, "iterm_frontmost_state", return_value=None):
            reason = twt.skip_reason_if_iterm_active()
        self.assertIsNotNone(reason)
        self.assertIn("determine", reason.lower())

    def test_runs_when_iterm_backgrounded(self):
        with mock.patch.object(twt, "iterm_frontmost_state", return_value=False):
            reason = twt.skip_reason_if_iterm_active()
        self.assertIsNone(reason)

    def test_override_runs_even_when_frontmost(self):
        with mock.patch.dict(os.environ, {twt.ALLOW_ACTIVE_ENV: "1"}), \
             mock.patch.object(twt, "iterm_frontmost_state", return_value=True) as detector:
            reason = twt.skip_reason_if_iterm_active()
        self.assertIsNone(reason)
        # Override short-circuits before the detector is consulted.
        detector.assert_not_called()

    def test_override_accepts_truthy_words(self):
        for val in ("1", "true", "YES", "On"):
            with mock.patch.dict(os.environ, {twt.ALLOW_ACTIVE_ENV: val}), \
                 mock.patch.object(twt, "iterm_frontmost_state", return_value=True):
                self.assertIsNone(
                    twt.skip_reason_if_iterm_active(), f"override value {val!r} should run"
                )

    def test_falsey_override_still_skips_when_frontmost(self):
        for val in ("0", "false", "", "no"):
            with mock.patch.dict(os.environ, {twt.ALLOW_ACTIVE_ENV: val}), \
                 mock.patch.object(twt, "iterm_frontmost_state", return_value=True):
                self.assertIsNotNone(
                    twt.skip_reason_if_iterm_active(), f"override value {val!r} should skip"
                )


if __name__ == "__main__":
    unittest.main()
