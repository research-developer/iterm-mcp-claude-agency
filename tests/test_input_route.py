"""Tests for POST /api/input level resolution and routing.

Headless: DashboardServer is built via __new__ with fake terminal and
fake stream reader/writer objects. No sockets, no iTerm.
"""

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, Mock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.dashboard import DashboardServer


class FakeReader:
    def __init__(self, body: bytes):
        self._body = body

    async def read(self, n: int) -> bytes:
        return self._body


class FakeWriter:
    """Captures _send_response output; parses status and JSON body."""

    def __init__(self):
        self.data = b""

    def write(self, chunk: bytes) -> None:
        self.data += chunk

    async def drain(self) -> None:
        pass

    @property
    def status(self) -> int:
        return int(self.data.split(b" ", 2)[1])

    @property
    def json(self) -> dict:
        return json.loads(self.data.split(b"\r\n\r\n", 1)[1])


def make_server(terminal=None, pending=False):
    """Build a DashboardServer shell wired with fakes."""
    server = DashboardServer.__new__(DashboardServer)
    server.terminal = terminal
    server._sse_clients = []
    server._driver_store = None
    server._broadcast_named_event = AsyncMock()
    if pending:
        store = server._get_driver_store()
        store.post_question("stop", "test?", [{"id": "a", "label": "A"}])
    else:
        server._get_driver_store()
    return server


async def call(server, payload):
    """POST payload to _handle_input; return the FakeWriter."""
    body = json.dumps(payload).encode()
    writer = FakeWriter()
    await server._handle_input(
        writer,
        FakeReader(body),
        {"content-length": str(len(body))},
    )
    return writer


class TestValidation(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_action_400(self):
        server = make_server()
        writer = await call(server, {"action": "explode", "modifier": False})
        self.assertEqual(writer.status, 400)

    async def test_missing_body_400(self):
        server = make_server()
        writer = FakeWriter()
        await server._handle_input(writer, FakeReader(b""), {})
        self.assertEqual(writer.status, 400)

    async def test_invalid_json_400(self):
        server = make_server()
        writer = FakeWriter()
        await server._handle_input(
            writer, FakeReader(b"not json"), {"content-length": "8"}
        )
        self.assertEqual(writer.status, 400)

    async def test_non_object_json_400(self):
        """Valid JSON that is not an object must 400, not AttributeError."""
        server = make_server()
        writer = FakeWriter()
        body = b"[1, 2, 3]"
        await server._handle_input(
            writer, FakeReader(body), {"content-length": str(len(body))}
        )
        self.assertEqual(writer.status, 400)

    async def test_oversize_body_413(self):
        """Content-Length beyond MAX_BODY_SIZE is rejected before any read."""
        server = make_server()
        writer = FakeWriter()
        await server._handle_input(
            writer, FakeReader(b""), {"content-length": "5000"}
        )
        self.assertEqual(writer.status, 413)


class TestTilesRouting(unittest.IsolatedAsyncioTestCase):
    """Pending question + no modifier ⇒ SSE action event."""

    async def test_move_next_broadcasts_action(self):
        server = make_server(pending=True)
        writer = await call(server, {"action": "move_next", "modifier": False})

        self.assertEqual(writer.status, 200)
        self.assertEqual(writer.json, {"status": "ok", "level": "tiles"})
        server._broadcast_named_event.assert_any_await(
            "action", {"action": "move_next", "level": "tiles"}
        )
        server._broadcast_named_event.assert_any_await(
            "level", {"level": "tiles", "status": "ok"}
        )


class TestTuiRouting(unittest.IsolatedAsyncioTestCase):
    """No pending question + no modifier ⇒ special key to active session."""

    async def test_select_sends_enter(self):
        session = AsyncMock()
        terminal = AsyncMock()
        terminal.get_active_session = AsyncMock(return_value=session)
        server = make_server(terminal=terminal)

        writer = await call(server, {"action": "select", "modifier": False})

        self.assertEqual(writer.json, {"status": "ok", "level": "tui"})
        session.send_special_key.assert_awaited_once_with("enter")

    async def test_move_prev_sends_up(self):
        session = AsyncMock()
        terminal = AsyncMock()
        terminal.get_active_session = AsyncMock(return_value=session)
        server = make_server(terminal=terminal)

        await call(server, {"action": "move_prev", "modifier": False})

        session.send_special_key.assert_awaited_once_with("up")

    async def test_cancel_sends_escape(self):
        session = AsyncMock()
        terminal = AsyncMock()
        terminal.get_active_session = AsyncMock(return_value=session)
        server = make_server(terminal=terminal)

        await call(server, {"action": "cancel", "modifier": False})

        session.send_special_key.assert_awaited_once_with("escape")

    async def test_no_active_session_drops_with_notice(self):
        terminal = AsyncMock()
        terminal.get_active_session = AsyncMock(return_value=None)
        server = make_server(terminal=terminal)

        writer = await call(server, {"action": "select", "modifier": False})

        self.assertEqual(writer.json, {"status": "dropped", "level": "tui"})
        server._broadcast_named_event.assert_any_await(
            "notice", {"message": "No active iTerm session"}
        )
        server._broadcast_named_event.assert_any_await(
            "level", {"level": "tui", "status": "dropped"}
        )


class TestPanesRouting(unittest.IsolatedAsyncioTestCase):
    """Modifier held ⇒ pane focus moves, even with a question pending."""

    async def test_modifier_move_next_focuses_pane(self):
        terminal = AsyncMock()
        terminal.focus_relative_pane = AsyncMock(return_value=True)
        server = make_server(terminal=terminal, pending=True)

        writer = await call(server, {"action": "move_next", "modifier": True})

        self.assertEqual(writer.json, {"status": "ok", "level": "panes"})
        terminal.focus_relative_pane.assert_awaited_once_with(1)

    async def test_modifier_move_prev_focuses_pane(self):
        terminal = AsyncMock()
        terminal.focus_relative_pane = AsyncMock(return_value=True)
        server = make_server(terminal=terminal)

        await call(server, {"action": "move_prev", "modifier": True})

        terminal.focus_relative_pane.assert_awaited_once_with(-1)

    async def test_modifier_select_is_noop_ok(self):
        terminal = AsyncMock()
        server = make_server(terminal=terminal)

        writer = await call(server, {"action": "select", "modifier": True})

        self.assertEqual(writer.json, {"status": "ok", "level": "panes"})
        terminal.focus_relative_pane.assert_not_awaited()

    async def test_modifier_move_next_drop_toasts(self):
        terminal = AsyncMock()
        terminal.focus_relative_pane = AsyncMock(return_value=False)
        server = make_server(terminal=terminal)

        writer = await call(server, {"action": "move_next", "modifier": True})

        self.assertEqual(writer.json, {"status": "dropped", "level": "panes"})
        server._broadcast_named_event.assert_any_await(
            "notice", {"message": "No pane to focus"}
        )

    async def test_modifier_move_prev_drop_toasts(self):
        terminal = AsyncMock()
        terminal.focus_relative_pane = AsyncMock(return_value=False)
        server = make_server(terminal=terminal)

        writer = await call(server, {"action": "move_prev", "modifier": True})

        self.assertEqual(writer.json, {"status": "dropped", "level": "panes"})
        server._broadcast_named_event.assert_any_await(
            "notice", {"message": "No pane to focus"}
        )


class TestWindowCycle(unittest.IsolatedAsyncioTestCase):
    async def test_window_cycle_activates_next_window(self):
        terminal = AsyncMock()
        terminal.cycle_window = AsyncMock(return_value=True)
        server = make_server(terminal=terminal)

        writer = await call(
            server, {"action": "window_cycle", "modifier": False}
        )

        self.assertEqual(writer.json, {"status": "ok", "level": "window"})
        terminal.cycle_window.assert_awaited_once_with(1)
        server._broadcast_named_event.assert_any_await(
            "level", {"level": "window", "status": "ok"}
        )

    async def test_window_cycle_failure_reports_dropped(self):
        terminal = AsyncMock()
        terminal.cycle_window = AsyncMock(return_value=False)
        server = make_server(terminal=terminal)

        writer = await call(
            server, {"action": "window_cycle", "modifier": False}
        )

        self.assertEqual(writer.json["status"], "dropped")


class TestTerminalErrorsDrop(unittest.IsolatedAsyncioTestCase):
    async def test_driver_store_exception_drops_with_valid_level(self):
        """pending_questions() raising before level is set must still 200.

        Regression: the store check runs before the tiles branch assigns
        level; an exception there must not leave level unbound.
        """
        server = make_server()
        store = Mock()
        store.pending_questions = Mock(side_effect=RuntimeError("store broken"))
        server._get_driver_store = Mock(return_value=store)

        writer = await call(server, {"action": "select", "modifier": False})

        self.assertEqual(writer.status, 200)
        self.assertEqual(writer.json, {"status": "dropped", "level": "tui"})

    async def test_terminal_exception_drops_not_500(self):
        terminal = AsyncMock()
        terminal.get_active_session = AsyncMock(
            side_effect=RuntimeError("connection lost")
        )
        server = make_server(terminal=terminal)

        writer = await call(server, {"action": "select", "modifier": False})

        self.assertEqual(writer.status, 200)
        self.assertEqual(writer.json["status"], "dropped")


if __name__ == "__main__":
    unittest.main()
