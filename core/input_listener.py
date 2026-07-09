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

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
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


logger = logging.getLogger(__name__)

DEFAULT_DASHBOARD_URL = os.environ.get(
    "ITERM_MCP_DASHBOARD_URL", "http://127.0.0.1:9999"
)


def normalize_key(key: object) -> Optional[str]:
    """Normalize a pynput key object to a lowercase name like 'f13'.

    Args:
        key: pynput Key (has .name) or KeyCode (has .char) or None.

    Returns:
        Lowercase key name for named keys, None for character keys
        and anything unrecognizable.
    """
    name = getattr(key, "name", None)
    if isinstance(name, str):
        return name.lower()
    return None


class Backoff:
    """Capped exponential backoff gate for posting during outages.

    Time is injected (pass `now`) so the logic tests headlessly.
    """

    def __init__(self, base: float = 1.0, cap: float = 30.0) -> None:
        self._base = base
        self._cap = cap
        self._delay = 0.0
        self._blocked_until = 0.0

    def should_attempt(self, now: float) -> bool:
        """Return True if a post may be attempted at time `now`."""
        return now >= self._blocked_until

    def record_failure(self, now: float) -> None:
        """Double the delay (capped) and block until it elapses."""
        self._delay = min(self._cap, self._delay * 2 or self._base)
        self._blocked_until = now + self._delay

    def record_success(self) -> None:
        """Reset the gate after a successful post."""
        self._delay = 0.0
        self._blocked_until = 0.0


def post_action(url: str, payload: dict, timeout: float = 2.0) -> bool:
    """POST a JSON action payload; never raises.

    Args:
        url: Full endpoint URL (e.g. http://127.0.0.1:9999/api/input).
        payload: JSON-serializable dict.
        timeout: Socket timeout in seconds.

    Returns:
        True on HTTP 2xx, False on any error (connection refused,
        timeout, non-2xx, etc.).
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("post_action failed: %s", exc)
        return False


def run_listener(dashboard_url: str) -> int:
    """Run the global key listener until interrupted.

    Imports pynput lazily so the module itself stays importable (and
    testable) without the optional dependency.

    Args:
        dashboard_url: Base URL of the dashboard server.

    Returns:
        Process exit code (0 normal, 2 pynput missing).
    """
    try:
        from pynput import keyboard
    except ImportError:
        sys.stderr.write(
            "pynput is not installed. Run: pip install 'iterm-mcp[remote]'\n"
        )
        return 2

    endpoint = dashboard_url.rstrip("/") + "/api/input"
    mapper = KeyEventMapper()
    backoff = Backoff()

    sys.stderr.write(
        "XP-Pen input listener started → %s\n"
        "If key presses don't register, grant Accessibility permission to\n"
        "%s in System Settings → Privacy & Security → Accessibility.\n"
        % (endpoint, sys.executable)
    )

    def dispatch(key: object, pressed: bool) -> None:
        name = normalize_key(key)
        if name is None:
            return
        payload = mapper.handle_event({"key": name, "pressed": pressed})
        if payload is None:
            return
        now = time.monotonic()
        if not backoff.should_attempt(now):
            return  # daemon outage — drop stale nav events
        if post_action(endpoint, payload):
            backoff.record_success()
        else:
            backoff.record_failure(time.monotonic())

    with keyboard.Listener(
        on_press=lambda key: dispatch(key, True),
        on_release=lambda key: dispatch(key, False),
    ) as listener:
        listener.join()
    return 0


def main(argv: Optional[list] = None) -> int:
    """CLI entry point for `iterm-mcp-input-listener`."""
    parser = argparse.ArgumentParser(
        description="XP-Pen remote → iterm-mcp dashboard input listener"
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_DASHBOARD_URL,
        help="Dashboard base URL (default: %(default)s or "
        "$ITERM_MCP_DASHBOARD_URL)",
    )
    args = parser.parse_args(argv)
    return run_listener(args.url)


if __name__ == "__main__":
    sys.exit(main())
