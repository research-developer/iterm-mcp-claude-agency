"""
XP-Pen remote input listener: dead keys (F13-F19) → dashboard actions.

The XP-Pen driver app maps the remote's wheel/keys to dead function keys
that no macOS app uses. This module catches them globally (pynput) and
POSTs {"action", "modifier"} payloads to the dashboard's /api/input,
which resolves the traversal level and routes the action. See
docs/superpowers/specs/2026-07-09-xppen-remote-design.md.

Pure logic (KeyEventMapper, normalize_key, Backoff) is separated from
the pynput listener so it tests headlessly; pynput is imported lazily
and only inside run_listener()/main().
"""

from typing import Optional


# Dead-key contract — must match the XP-Pen driver app configuration.
KEY_ACTIONS = {
    "f13": "move_prev",     # wheel CCW
    "f14": "move_next",     # wheel CW
    "f15": "window_cycle",  # center wheel button
    "f16": "select",        # K1
    "f17": "cancel",        # K2
    "f19": "select",        # K9 (ergonomic duplicate)
}
MODIFIER_KEY = "f18"        # K7 — hold to shift wheel to the panes level


class KeyEventMapper:
    """Map dead-key events to action payloads with edge detection.

    Mirrors core.driver.GamepadController: a held key fires once (macOS
    key-repeat sends repeated key-down events) and must be released to
    re-arm. Holding the modifier key stamps modifier=True on payloads.
    """

    def __init__(self) -> None:
        self._held_keys: set = set()
        self._modifier_down = False

    def handle_event(self, event: dict) -> Optional[dict]:
        """Map a key event to an action payload, or return None.

        Args:
            event: {"key": str, "pressed": bool} — key is a lowercase
                name like "f13" (see normalize_key).

        Returns:
            {"action": str, "modifier": bool} on a rising edge of a
            mapped key; None for holds, releases, the modifier key
            itself, unmapped keys, and malformed events.
        """
        key = event.get("key")
        pressed = event.get("pressed")
        if not isinstance(key, str) or not isinstance(pressed, bool):
            return None

        if key == MODIFIER_KEY:
            self._modifier_down = pressed
            return None

        if not pressed:
            self._held_keys.discard(key)
            return None
        if key in self._held_keys:
            return None  # OS key-repeat — already fired on the edge
        self._held_keys.add(key)

        action = KEY_ACTIONS.get(key)
        if action is None:
            return None
        return {"action": action, "modifier": self._modifier_down}
