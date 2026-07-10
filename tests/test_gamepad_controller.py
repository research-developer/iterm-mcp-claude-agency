"""Tests for the GamepadController seam in core/driver.py.

Headless: exercises only the pure event→Action mapping logic. No iTerm2
connection, no windows, no network.

Event dict shapes (mirroring what the browser gamepad loop would emit):
    {"type": "button", "index": int, "pressed": bool}
    {"type": "axis", "index": int, "value": float}
"""

import os
import sys
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.driver import Action, GamepadController


def button(index, pressed):
    """Build a button event dict."""
    return {"type": "button", "index": index, "pressed": pressed}


def axis(index, value):
    """Build an axis event dict."""
    return {"type": "axis", "index": index, "value": value}


class TestGamepadButtonMapping(unittest.TestCase):
    """Standard-mapping buttons translate to the right Actions."""

    def setUp(self):
        self.controller = GamepadController()

    def test_dpad_up_maps_to_move_prev(self):
        self.assertEqual(
            self.controller.handle_event(button(12, True)), Action.MOVE_PREV
        )

    def test_dpad_down_maps_to_move_next(self):
        self.assertEqual(
            self.controller.handle_event(button(13, True)), Action.MOVE_NEXT
        )

    def test_face_a_maps_to_select(self):
        self.assertEqual(
            self.controller.handle_event(button(0, True)), Action.SELECT
        )

    def test_face_b_maps_to_cancel(self):
        self.assertEqual(
            self.controller.handle_event(button(1, True)), Action.CANCEL
        )

    def test_unmapped_button_returns_none(self):
        self.assertIsNone(self.controller.handle_event(button(7, True)))

    def test_button_release_returns_none(self):
        self.controller.handle_event(button(0, True))
        self.assertIsNone(self.controller.handle_event(button(0, False)))


class TestGamepadButtonEdgeDetection(unittest.TestCase):
    """A held button fires exactly once; release re-arms it."""

    def setUp(self):
        self.controller = GamepadController()

    def test_held_button_fires_once(self):
        self.assertEqual(
            self.controller.handle_event(button(0, True)), Action.SELECT
        )
        self.assertIsNone(self.controller.handle_event(button(0, True)))
        self.assertIsNone(self.controller.handle_event(button(0, True)))

    def test_release_rearms_button(self):
        self.controller.handle_event(button(13, True))
        self.controller.handle_event(button(13, False))
        self.assertEqual(
            self.controller.handle_event(button(13, True)), Action.MOVE_NEXT
        )

    def test_buttons_tracked_independently(self):
        self.assertEqual(
            self.controller.handle_event(button(12, True)), Action.MOVE_PREV
        )
        # Holding 12 must not swallow a fresh press of 13.
        self.assertEqual(
            self.controller.handle_event(button(13, True)), Action.MOVE_NEXT
        )
        self.assertIsNone(self.controller.handle_event(button(12, True)))


class TestGamepadStick(unittest.TestCase):
    """Left-stick Y (axis 1) maps to MOVE_PREV/MOVE_NEXT with hysteresis."""

    def setUp(self):
        self.controller = GamepadController()

    def test_stick_up_maps_to_move_prev(self):
        self.assertEqual(
            self.controller.handle_event(axis(1, -0.9)), Action.MOVE_PREV
        )

    def test_stick_down_maps_to_move_next(self):
        self.assertEqual(
            self.controller.handle_event(axis(1, 0.9)), Action.MOVE_NEXT
        )

    def test_below_threshold_returns_none(self):
        self.assertIsNone(self.controller.handle_event(axis(1, -0.4)))

    def test_held_deflection_fires_once(self):
        self.assertEqual(
            self.controller.handle_event(axis(1, 0.9)), Action.MOVE_NEXT
        )
        self.assertIsNone(self.controller.handle_event(axis(1, 0.9)))
        self.assertIsNone(self.controller.handle_event(axis(1, 0.8)))

    def test_must_return_to_neutral_to_rearm(self):
        self.controller.handle_event(axis(1, 0.9))
        # 0.4 is below the press threshold but above the release threshold:
        # still considered deflected, so a following push must not re-fire.
        self.controller.handle_event(axis(1, 0.4))
        self.assertIsNone(self.controller.handle_event(axis(1, 0.9)))
        # Back to true neutral re-arms.
        self.controller.handle_event(axis(1, 0.1))
        self.assertEqual(
            self.controller.handle_event(axis(1, 0.9)), Action.MOVE_NEXT
        )

    def test_direction_flip_fires_without_neutral(self):
        self.assertEqual(
            self.controller.handle_event(axis(1, 0.9)), Action.MOVE_NEXT
        )
        self.assertEqual(
            self.controller.handle_event(axis(1, -0.9)), Action.MOVE_PREV
        )

    def test_other_axes_ignored(self):
        self.assertIsNone(self.controller.handle_event(axis(0, -0.9)))
        self.assertIsNone(self.controller.handle_event(axis(3, 0.9)))


class TestGamepadMalformedEvents(unittest.TestCase):
    """Malformed events return None instead of raising."""

    def setUp(self):
        self.controller = GamepadController()

    def test_empty_event(self):
        self.assertIsNone(self.controller.handle_event({}))

    def test_unknown_type(self):
        self.assertIsNone(
            self.controller.handle_event({"type": "touchpad", "x": 0.5})
        )

    def test_button_missing_index(self):
        self.assertIsNone(
            self.controller.handle_event({"type": "button", "pressed": True})
        )

    def test_axis_missing_value(self):
        self.assertIsNone(
            self.controller.handle_event({"type": "axis", "index": 1})
        )

    def test_non_numeric_axis_value(self):
        self.assertIsNone(
            self.controller.handle_event(
                {"type": "axis", "index": 1, "value": "up"}
            )
        )

    def test_non_dict_event(self):
        self.assertIsNone(self.controller.handle_event(None))
        self.assertIsNone(self.controller.handle_event("button"))
        self.assertIsNone(self.controller.handle_event([1, 2, 3]))

    def test_nan_axis_value_ignored(self):
        # NaN must not reset the axis state and re-arm the stick
        # (mirrors the JS mapper's isNaN guard).
        self.assertEqual(
            self.controller.handle_event(axis(1, 0.9)), Action.MOVE_NEXT
        )
        self.assertIsNone(
            self.controller.handle_event(axis(1, float("nan")))
        )
        # Still held: a repeated deflection must not fire again.
        self.assertIsNone(self.controller.handle_event(axis(1, 0.9)))


class TestGamepadControllerProtocol(unittest.TestCase):
    """GamepadController satisfies the Controller protocol."""

    def test_has_callable_handle_event(self):
        controller = GamepadController()
        self.assertTrue(callable(getattr(controller, "handle_event", None)))


if __name__ == "__main__":
    unittest.main()
