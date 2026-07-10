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


class TestNormalizeKey(unittest.TestCase):
    """pynput key objects normalize to lowercase names."""

    def test_named_key(self):
        from core.input_listener import normalize_key

        class FakeKey:
            name = "f13"

        self.assertEqual(normalize_key(FakeKey()), "f13")

    def test_uppercase_name_lowercased(self):
        from core.input_listener import normalize_key

        class FakeKey:
            name = "F14"

        self.assertEqual(normalize_key(FakeKey()), "f14")

    def test_character_key_returns_none(self):
        from core.input_listener import normalize_key

        class FakeKeyCode:  # pynput KeyCode has .char, no .name
            char = "a"

        self.assertIsNone(normalize_key(FakeKeyCode()))

    def test_none_returns_none(self):
        from core.input_listener import normalize_key

        self.assertIsNone(normalize_key(None))


class TestBackoff(unittest.TestCase):
    """Failures gate posting with capped exponential backoff."""

    def test_attempts_allowed_initially(self):
        from core.input_listener import Backoff

        b = Backoff()
        self.assertTrue(b.should_attempt(now=100.0))

    def test_failure_blocks_until_delay_elapses(self):
        from core.input_listener import Backoff

        b = Backoff(base=1.0, cap=30.0)
        b.record_failure(now=100.0)
        self.assertFalse(b.should_attempt(now=100.5))
        self.assertTrue(b.should_attempt(now=101.1))

    def test_consecutive_failures_double_delay_up_to_cap(self):
        from core.input_listener import Backoff

        b = Backoff(base=1.0, cap=4.0)
        b.record_failure(now=100.0)   # delay 1s
        b.record_failure(now=101.0)   # delay 2s
        b.record_failure(now=103.0)   # delay 4s
        b.record_failure(now=107.0)   # delay capped at 4s
        self.assertFalse(b.should_attempt(now=110.9))
        self.assertTrue(b.should_attempt(now=111.1))

    def test_success_resets(self):
        from core.input_listener import Backoff

        b = Backoff(base=1.0, cap=30.0)
        b.record_failure(now=100.0)
        b.record_success()
        self.assertTrue(b.should_attempt(now=100.1))


class TestPostAction(unittest.TestCase):
    """post_action never raises; returns False when the daemon is down."""

    def test_unreachable_url_returns_false(self):
        import socket

        from core.input_listener import post_action

        # Bind an ephemeral port without listening: connections to it are
        # deterministically refused while we hold the socket open.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        try:
            ok = post_action(
                "http://127.0.0.1:%d/api/input" % port,
                {"action": "select", "modifier": False},
                timeout=0.5,
            )
        finally:
            sock.close()
        self.assertFalse(ok)

    def test_unserializable_payload_returns_false(self):
        from core.input_listener import post_action

        ok = post_action(
            "http://127.0.0.1:9999/api/input",
            {"action": object()},  # json.dumps raises TypeError
            timeout=0.2,
        )
        self.assertFalse(ok)

    def test_posts_json_to_live_server(self):
        import http.server
        import json
        import threading

        from core.input_listener import post_action

        received = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                received.update(json.loads(self.rfile.read(length)))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = "http://127.0.0.1:%d/api/input" % server.server_port
            ok = post_action(url, {"action": "move_next", "modifier": True})
        finally:
            server.shutdown()
        self.assertTrue(ok)
        self.assertEqual(
            received, {"action": "move_next", "modifier": True}
        )


class TestMainWithoutPynput(unittest.TestCase):
    """main() exits with a clear message when pynput is missing."""

    def test_missing_pynput_exits_2(self):
        import builtins
        import unittest.mock as mock

        from core.input_listener import main

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("pynput"):
                raise ImportError("No module named 'pynput'")
            return real_import(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__", fake_import):
            exit_code = main(["--url", "http://127.0.0.1:1"])
        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
