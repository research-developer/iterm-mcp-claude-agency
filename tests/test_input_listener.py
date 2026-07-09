"""Tests for the XP-Pen input listener (core/input_listener.py).

Headless: exercises only pure logic. No pynput, no network, no iTerm.
Dead-key contract (set once in the XP-Pen driver app):
    F13/F14 wheel CCW/CW, F15 center button, F16 K1 select,
    F17 K2 cancel, F18 K7 modifier (hold), F19 K9 select.
"""

import os
import sys
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.input_listener import KeyEventMapper


def press(key):
    """Build a key-down event dict."""
    return {"key": key, "pressed": True}


def release(key):
    """Build a key-up event dict."""
    return {"key": key, "pressed": False}


class TestKeyMapping(unittest.TestCase):
    """Dead keys translate to the right action payloads."""

    def setUp(self):
        self.mapper = KeyEventMapper()

    def test_f13_maps_to_move_prev(self):
        self.assertEqual(
            self.mapper.handle_event(press("f13")),
            {"action": "move_prev", "modifier": False},
        )

    def test_f14_maps_to_move_next(self):
        self.assertEqual(
            self.mapper.handle_event(press("f14")),
            {"action": "move_next", "modifier": False},
        )

    def test_f15_maps_to_window_cycle(self):
        self.assertEqual(
            self.mapper.handle_event(press("f15")),
            {"action": "window_cycle", "modifier": False},
        )

    def test_f16_maps_to_select(self):
        self.assertEqual(
            self.mapper.handle_event(press("f16")),
            {"action": "select", "modifier": False},
        )

    def test_f17_maps_to_cancel(self):
        self.assertEqual(
            self.mapper.handle_event(press("f17")),
            {"action": "cancel", "modifier": False},
        )

    def test_f19_maps_to_select(self):
        self.assertEqual(
            self.mapper.handle_event(press("f19")),
            {"action": "select", "modifier": False},
        )

    def test_unmapped_key_returns_none(self):
        self.assertIsNone(self.mapper.handle_event(press("f20")))
        self.assertIsNone(self.mapper.handle_event(press("a")))

    def test_release_returns_none(self):
        self.mapper.handle_event(press("f16"))
        self.assertIsNone(self.mapper.handle_event(release("f16")))


class TestModifier(unittest.TestCase):
    """Holding F18 stamps modifier=True on subsequent payloads."""

    def setUp(self):
        self.mapper = KeyEventMapper()

    def test_modifier_key_itself_returns_none(self):
        self.assertIsNone(self.mapper.handle_event(press("f18")))
        self.assertIsNone(self.mapper.handle_event(release("f18")))

    def test_modifier_held_stamps_payload(self):
        self.mapper.handle_event(press("f18"))
        self.assertEqual(
            self.mapper.handle_event(press("f13")),
            {"action": "move_prev", "modifier": True},
        )

    def test_modifier_release_unstamps(self):
        self.mapper.handle_event(press("f18"))
        self.mapper.handle_event(release("f18"))
        self.assertEqual(
            self.mapper.handle_event(press("f13")),
            {"action": "move_prev", "modifier": False},
        )

    def test_modifier_repeat_keeps_held_state(self):
        # macOS key-repeat sends repeated key-down while held.
        self.mapper.handle_event(press("f18"))
        self.mapper.handle_event(press("f18"))
        self.assertEqual(
            self.mapper.handle_event(press("f14")),
            {"action": "move_next", "modifier": True},
        )


class TestEdgeDetection(unittest.TestCase):
    """OS key-repeat must not fire duplicate actions."""

    def setUp(self):
        self.mapper = KeyEventMapper()

    def test_held_key_fires_once(self):
        self.assertIsNotNone(self.mapper.handle_event(press("f16")))
        self.assertIsNone(self.mapper.handle_event(press("f16")))
        self.assertIsNone(self.mapper.handle_event(press("f16")))

    def test_release_rearms(self):
        self.mapper.handle_event(press("f14"))
        self.mapper.handle_event(release("f14"))
        self.assertEqual(
            self.mapper.handle_event(press("f14")),
            {"action": "move_next", "modifier": False},
        )

    def test_keys_tracked_independently(self):
        self.assertIsNotNone(self.mapper.handle_event(press("f13")))
        self.assertIsNotNone(self.mapper.handle_event(press("f14")))
        self.assertIsNone(self.mapper.handle_event(press("f13")))


class TestMalformedEvents(unittest.TestCase):
    """Malformed events return None instead of raising."""

    def setUp(self):
        self.mapper = KeyEventMapper()

    def test_empty_event(self):
        self.assertIsNone(self.mapper.handle_event({}))

    def test_missing_pressed(self):
        self.assertIsNone(self.mapper.handle_event({"key": "f13"}))

    def test_none_key(self):
        self.assertIsNone(
            self.mapper.handle_event({"key": None, "pressed": True})
        )


if __name__ == "__main__":
    unittest.main()
