"""Tests for the AgentMessageBus (core/bus.py).

Covers:
- Addressing: agent:, team:, broadcast
- Durable enqueue / receive / ack
- Per-recipient FIFO ordering
- Long-poll: returns-on-new-message vs times-out-empty
- Restart durability: reopen DB and still get unacked messages
- TTL expiry filtering
- NotificationManager → bus adapter
- bus tool ops: send, receive, ack, status, options
"""

import asyncio
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bus(path: str):
    """Create an AgentMessageBus pointed at a temp path."""
    from core.bus import AgentMessageBus
    return AgentMessageBus(db_path=path)


def _make_registry(agents=None, teams=None):
    """Minimal stub of AgentRegistry for fan-out resolution."""
    registry = MagicMock()
    agent_objects = []
    if agents:
        for name in agents:
            a = MagicMock()
            a.name = name
            a.teams = teams.get(name, []) if teams else []
            agent_objects.append(a)

    def list_agents_side_effect(team=None):
        if team is None:
            return agent_objects
        return [a for a in agent_objects if team in a.teams]

    registry.list_agents.side_effect = list_agents_side_effect
    return registry


# ---------------------------------------------------------------------------
# Core Bus Tests
# ---------------------------------------------------------------------------

class TestBusSendToAgent(unittest.IsolatedAsyncioTestCase):
    """test_send_to_agent — send agent:alice → one row in inbox."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_send_to_agent(self):
        result = await self.bus.send(
            sender="orchestrator",
            recipient="agent:alice",
            kind="instruction",
            body="hello alice",
        )
        self.assertEqual(result["accepted_recipients"], ["agent:alice"])
        self.assertIn("message_id", result)

        recv = await self.bus.receive("agent:alice", wait_up_to=0)
        self.assertEqual(len(recv["messages"]), 1)
        self.assertEqual(recv["messages"][0]["body"], "hello alice")
        self.assertEqual(recv["messages"][0]["sender"], "orchestrator")
        self.assertEqual(recv["messages"][0]["kind"], "instruction")


class TestBusFanOutToTeam(unittest.IsolatedAsyncioTestCase):
    """test_send_to_team — fan-out to 2 team members."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_send_to_team(self):
        registry = _make_registry(
            agents=["alice", "bob"],
            teams={"alice": ["backend"], "bob": ["backend"]},
        )
        result = await self.bus.send(
            sender="orchestrator",
            recipient="team:backend",
            kind="instruction",
            body="team msg",
            agent_registry=registry,
        )
        self.assertIn("agent:alice", result["accepted_recipients"])
        self.assertIn("agent:bob", result["accepted_recipients"])

        alice_msgs = await self.bus.receive("agent:alice", wait_up_to=0)
        bob_msgs = await self.bus.receive("agent:bob", wait_up_to=0)
        self.assertEqual(len(alice_msgs["messages"]), 1)
        self.assertEqual(len(bob_msgs["messages"]), 1)


class TestBusBroadcast(unittest.IsolatedAsyncioTestCase):
    """test_broadcast — fan-out to 3 registered agents."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_broadcast(self):
        registry = _make_registry(agents=["alice", "bob", "carol"])
        result = await self.bus.send(
            sender="system",
            recipient="broadcast",
            kind="event",
            body="system event",
            agent_registry=registry,
        )
        # Should have 3 agent rows + 1 broadcast row = 4
        self.assertEqual(len(result["accepted_recipients"]), 4)
        self.assertIn("broadcast", result["accepted_recipients"])

        for name in ["alice", "bob", "carol"]:
            msgs = await self.bus.receive(f"agent:{name}", wait_up_to=0)
            self.assertEqual(len(msgs["messages"]), 1, f"{name} should have 1 message")


class TestBusReceiveNonblocking(unittest.IsolatedAsyncioTestCase):
    """test_receive_nonblocking — drain empty inbox returns empty list."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_receive_empty(self):
        result = await self.bus.receive("agent:nobody", wait_up_to=0)
        self.assertEqual(result["messages"], [])
        self.assertEqual(result["next_cursor"], 0)
        self.assertFalse(result["has_more"])


class TestBusReceiveReturnsMessages(unittest.IsolatedAsyncioTestCase):
    """test_receive_returns_messages — send 3, receive → 3 envelopes FIFO."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_receive_three(self):
        for i in range(3):
            await self.bus.send(
                sender="sender", recipient="agent:alice",
                kind="instruction", body=f"msg{i}",
            )
        result = await self.bus.receive("agent:alice", wait_up_to=0)
        self.assertEqual(len(result["messages"]), 3)
        bodies = [m["body"] for m in result["messages"]]
        self.assertEqual(bodies, ["msg0", "msg1", "msg2"])


class TestBusAckAdvancesCursor(unittest.IsolatedAsyncioTestCase):
    """test_ack_advances_cursor — ack through cursor, receive again → empty."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_ack(self):
        for i in range(3):
            await self.bus.send(
                sender="s", recipient="agent:alice",
                kind="instruction", body=f"m{i}",
            )
        recv = await self.bus.receive("agent:alice", wait_up_to=0)
        self.assertEqual(len(recv["messages"]), 3)

        # Ack all through next_cursor.
        ack_result = await self.bus.ack("agent:alice", recv["next_cursor"])
        self.assertEqual(ack_result["acked_through"], recv["next_cursor"])

        # Second receive should see nothing.
        recv2 = await self.bus.receive("agent:alice", wait_up_to=0)
        self.assertEqual(recv2["messages"], [])


class TestBusCursorDurability(unittest.IsolatedAsyncioTestCase):
    """test_cursor_durability — reopen DB, unacked messages still there."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

    async def asyncTearDown(self):
        os.unlink(self.db_path)

    async def test_durability(self):
        # Write messages with bus instance 1.
        bus1 = _make_bus(self.db_path)
        for i in range(3):
            await bus1.send(
                sender="s", recipient="agent:alice",
                kind="instruction", body=f"m{i}",
            )
        recv = await bus1.receive("agent:alice", wait_up_to=0)
        # Ack only the first message.
        first_rowid = recv["messages"][0]["rowid"]
        await bus1.ack("agent:alice", first_rowid)
        bus1.close()

        # Reopen with bus instance 2 — should see 2 unacked messages.
        bus2 = _make_bus(self.db_path)
        recv2 = await bus2.receive("agent:alice", wait_up_to=0)
        bus2.close()

        self.assertEqual(len(recv2["messages"]), 2)
        bodies = [m["body"] for m in recv2["messages"]]
        self.assertEqual(bodies, ["m1", "m2"])


class TestBusLongPollWakeup(unittest.IsolatedAsyncioTestCase):
    """test_long_poll_wakeup — receive unblocks when message arrives."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_long_poll_wakeup(self):
        received: list = []

        async def reader():
            result = await self.bus.receive("agent:alice", wait_up_to=5)
            received.extend(result["messages"])

        async def writer():
            await asyncio.sleep(0.05)
            await self.bus.send(
                sender="s", recipient="agent:alice",
                kind="instruction", body="wake up!",
            )

        start = time.monotonic()
        await asyncio.gather(reader(), writer())
        elapsed = time.monotonic() - start

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["body"], "wake up!")
        # Should have completed well before the 5s timeout.
        self.assertLess(elapsed, 2.0)


class TestBusLongPollTimeout(unittest.IsolatedAsyncioTestCase):
    """test_long_poll_timeout — receive returns empty after timeout."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_long_poll_timeout(self):
        start = time.monotonic()
        result = await self.bus.receive("agent:alice", wait_up_to=0.1)
        elapsed = time.monotonic() - start

        self.assertEqual(result["messages"], [])
        # Should have taken roughly 0.1 s but not more than 2 s.
        self.assertGreaterEqual(elapsed, 0.05)
        self.assertLess(elapsed, 2.0)


class TestBusPerRecipientFifo(unittest.IsolatedAsyncioTestCase):
    """test_per_recipient_fifo — each recipient gets only their own, in order."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_fifo(self):
        # Alice gets A, B, C.  Bob gets B, C only.
        await self.bus.send(sender="s", recipient="agent:alice", kind="instruction", body="A")
        await self.bus.send(sender="s", recipient="agent:alice", kind="instruction", body="B")
        await self.bus.send(sender="s", recipient="agent:alice", kind="instruction", body="C")
        await self.bus.send(sender="s", recipient="agent:bob",   kind="instruction", body="B")
        await self.bus.send(sender="s", recipient="agent:bob",   kind="instruction", body="C")

        alice = await self.bus.receive("agent:alice", wait_up_to=0)
        bob   = await self.bus.receive("agent:bob",   wait_up_to=0)

        self.assertEqual([m["body"] for m in alice["messages"]], ["A", "B", "C"])
        self.assertEqual([m["body"] for m in bob["messages"]],   ["B", "C"])


class TestBusKindsFilter(unittest.IsolatedAsyncioTestCase):
    """test_kinds_filter — receive(kinds=["event"]) returns only events."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_kinds_filter(self):
        await self.bus.send(sender="s", recipient="agent:alice", kind="instruction", body="inst")
        await self.bus.send(sender="s", recipient="agent:alice", kind="event",       body="evt")
        await self.bus.send(sender="s", recipient="agent:alice", kind="notification", body="notif")

        result = await self.bus.receive("agent:alice", wait_up_to=0, kinds=["event"])
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0]["kind"], "event")


class TestBusTtlExpiry(unittest.IsolatedAsyncioTestCase):
    """test_ttl_expiry — expired messages are not returned."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_ttl_expiry(self):
        # Send a message with ttl_seconds=0 (already expired).
        await self.bus.send(
            sender="s", recipient="agent:alice",
            kind="instruction", body="stale",
            ttl_seconds=0,
        )
        # Send a non-expiring message.
        await self.bus.send(
            sender="s", recipient="agent:alice",
            kind="instruction", body="fresh",
        )
        result = await self.bus.receive("agent:alice", wait_up_to=0)
        bodies = [m["body"] for m in result["messages"]]
        self.assertNotIn("stale", bodies)
        self.assertIn("fresh", bodies)

    async def test_ttl_positive_not_dropped(self):
        """Messages with a positive TTL (e.g. 3600 s) must NOT be filtered out."""
        # Send a message that won't expire for an hour.
        await self.bus.send(
            sender="s", recipient="agent:alice",
            kind="instruction", body="long-lived",
            ttl_seconds=3600,
        )
        # Send a message with no TTL (never expires).
        await self.bus.send(
            sender="s", recipient="agent:alice",
            kind="instruction", body="no-ttl",
        )
        result = await self.bus.receive("agent:alice", wait_up_to=0)
        bodies = [m["body"] for m in result["messages"]]
        self.assertIn("long-lived", bodies, "positive-TTL message should still be returned")
        self.assertIn("no-ttl", bodies)

    async def test_ttl_list_inboxes_positive_not_dropped(self):
        """list_inboxes must also count positive-TTL messages as live."""
        await self.bus.send(
            sender="s", recipient="agent:alice",
            kind="instruction", body="live-msg",
            ttl_seconds=3600,
        )
        inboxes = await self.bus.list_inboxes()
        by_r = {i["recipient"]: i for i in inboxes}
        self.assertIn("agent:alice", by_r, "inbox should be visible with positive-TTL msg")
        self.assertEqual(by_r["agent:alice"]["depth"], 1)


class TestBusListInboxes(unittest.IsolatedAsyncioTestCase):
    """list_inboxes returns depth and age info per recipient."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_list_inboxes(self):
        await self.bus.send(sender="s", recipient="agent:alice", kind="instruction", body="x")
        await self.bus.send(sender="s", recipient="agent:alice", kind="instruction", body="y")
        inboxes = await self.bus.list_inboxes()
        by_recipient = {i["recipient"]: i for i in inboxes}
        self.assertIn("agent:alice", by_recipient)
        self.assertEqual(by_recipient["agent:alice"]["depth"], 2)


# ---------------------------------------------------------------------------
# NotificationManager Adapter Test
# ---------------------------------------------------------------------------

class TestNotificationManagerBusAdapter(unittest.IsolatedAsyncioTestCase):
    """test_notification_adapter — add_simple with bus wired → bus inbox gets it."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_notification_adapter(self):
        from iterm_mcpy.app_context import NotificationManager

        nm = NotificationManager()
        nm._message_bus = self.bus

        await nm.add_simple(
            agent="alice",
            level="info",
            summary="test notification",
            context="ctx",
        )

        # Allow the create_task to execute.
        await asyncio.sleep(0.05)

        result = await self.bus.receive("agent:alice", wait_up_to=0)
        self.assertEqual(len(result["messages"]), 1)
        msg = result["messages"][0]
        self.assertEqual(msg["kind"], "notification")
        self.assertEqual(msg["sender"], "system")
        self.assertIn("summary", msg["body"])
        self.assertEqual(msg["body"]["summary"], "test notification")

    async def test_notification_adapter_not_wired(self):
        """add_simple without bus wired should not raise."""
        from iterm_mcpy.app_context import NotificationManager

        nm = NotificationManager()
        # _message_bus is None by default
        await nm.add_simple(agent="bob", level="warning", summary="quiet")
        # Ring buffer still works.
        notifs = await nm.get(agent="bob")
        self.assertEqual(len(notifs), 1)


# ---------------------------------------------------------------------------
# Bus Tool Tests (unit tests with a fake ctx)
# ---------------------------------------------------------------------------

def _make_ctx(bus_instance, registry=None):
    """Build a minimal fake FastMCP Context for bus tool tests."""
    lifespan = {"message_bus": bus_instance, "agent_registry": registry}
    ctx = MagicMock()
    ctx.request_context.lifespan_context = lifespan
    return ctx


class TestBusToolSendOp(unittest.IsolatedAsyncioTestCase):
    """test_bus_tool_send_op — POST SEND returns ok envelope with message_id."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_send_op(self):
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        # Use op="send" (maps to POST+SEND) or op="POST" definer="SEND".
        result = await bus_tool(
            ctx,
            op="send",
            to="agent:alice",
            kind="instruction",
            body="hello",
            sender="tester",
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["method"], "POST")
        self.assertIn("message_id", result["data"])

    async def test_send_op_explicit_definer(self):
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        result = await bus_tool(
            ctx,
            op="POST",
            definer="SEND",
            to="agent:alice",
            kind="instruction",
            body="hello2",
        )
        self.assertTrue(result["ok"], result)

    async def test_send_missing_to(self):
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        # POST+SEND without `to` — should fail.
        result = await bus_tool(ctx, op="POST", definer="SEND", body="hello")
        self.assertFalse(result["ok"])
        self.assertIn("to", result["error"]["message"])


class TestBusToolReceiveOp(unittest.IsolatedAsyncioTestCase):
    """test_bus_tool_receive_op — GET after send returns messages in data."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_receive_op(self):
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        # Send first using op="send" (POST+SEND).
        await bus_tool(ctx, op="send", to="agent:alice", kind="instruction", body="test msg")

        # Receive via GET.
        result = await bus_tool(ctx, op="GET", agent="alice", wait_up_to=0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["method"], "GET")
        self.assertEqual(len(result["data"]["messages"]), 1)
        self.assertEqual(result["data"]["messages"][0]["body"], "test msg")

    async def test_receive_missing_agent(self):
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        result = await bus_tool(ctx, op="GET", wait_up_to=0)
        self.assertFalse(result["ok"])


class TestBusToolAckOp(unittest.IsolatedAsyncioTestCase):
    """test_bus_tool_ack_op — POST+TRIGGER returns acked_through."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_ack_op(self):
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        await bus_tool(ctx, op="send", to="agent:alice", kind="instruction", body="m")

        # Receive to get cursor.
        recv = await bus_tool(ctx, op="GET", agent="alice", wait_up_to=0)
        cursor = recv["data"]["next_cursor"]

        # Ack via tool.
        ack_result = await bus_tool(
            ctx,
            op="POST",
            definer="TRIGGER",
            agent="alice",
            up_to_cursor=cursor,
        )
        self.assertTrue(ack_result["ok"], ack_result)
        self.assertEqual(ack_result["data"]["acked_through"], cursor)

        # Subsequent receive should be empty.
        recv2 = await bus_tool(ctx, op="GET", agent="alice", wait_up_to=0)
        self.assertEqual(recv2["data"]["messages"], [])

    async def test_ack_missing_agent(self):
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        result = await bus_tool(
            ctx, op="POST", definer="TRIGGER", up_to_cursor=5
        )
        self.assertFalse(result["ok"])

    async def test_ack_missing_cursor(self):
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        result = await bus_tool(
            ctx, op="POST", definer="TRIGGER", agent="alice"
        )
        self.assertFalse(result["ok"])


class TestBusToolStatusOp(unittest.IsolatedAsyncioTestCase):
    """GET target=status returns inbox depths."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_status_op(self):
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        await self.bus.send(sender="s", recipient="agent:alice", kind="instruction", body="x")

        result = await bus_tool(ctx, op="GET", target="status")
        self.assertTrue(result["ok"], result)
        self.assertIn("inboxes", result["data"])
        inboxes = result["data"]["inboxes"]
        by_r = {i["recipient"]: i for i in inboxes}
        self.assertIn("agent:alice", by_r)


class TestBusToolPeekOp(unittest.IsolatedAsyncioTestCase):
    """GET target=peek returns up to 5 messages non-blocking."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_peek_op(self):
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        for i in range(3):
            await self.bus.send(sender="s", recipient="agent:alice", kind="instruction", body=f"m{i}")

        result = await bus_tool(ctx, op="GET", target="peek", agent="alice")
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["data"]["messages"]), 3)


class TestBusToolOptionsOp(unittest.IsolatedAsyncioTestCase):
    """test_bus_tool_options — OPTIONS returns ok envelope with schema."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_options(self):
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        result = await bus_tool(ctx, op="OPTIONS")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["method"], "OPTIONS")
        self.assertIn("methods", result["data"])
        self.assertIn("future_phases", result["data"])

    async def test_options_without_appcontext(self):
        """OPTIONS should work even with a broken/missing ctx."""
        from iterm_mcpy.tools.bus import bus as bus_tool

        # Simulate a ctx where lifespan access would fail.
        ctx = MagicMock()
        ctx.request_context.lifespan_context = {}  # message_bus missing

        result = await bus_tool(ctx, op="OPTIONS")
        self.assertTrue(result["ok"], result)


class TestBusToolFriendlyVerbAliases(unittest.IsolatedAsyncioTestCase):
    """Friendly verb aliases (send, receive, ack) resolve correctly."""

    async def asyncSetUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.bus = _make_bus(self.db_path)

    async def asyncTearDown(self):
        self.bus.close()
        os.unlink(self.db_path)

    async def test_send_alias(self):
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        result = await bus_tool(ctx, op="send", to="agent:alice", body="via alias")
        self.assertTrue(result["ok"], result)

    async def test_get_alias(self):
        """'get' and 'read' are GET-family aliases."""
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        result = await bus_tool(ctx, op="get", agent="alice", wait_up_to=0)
        self.assertTrue(result["ok"], result)

    async def test_read_alias(self):
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        result = await bus_tool(ctx, op="read", agent="alice", wait_up_to=0)
        self.assertTrue(result["ok"], result)

    async def test_notify_alias(self):
        """'notify' maps to POST+SEND."""
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        result = await bus_tool(ctx, op="notify", to="agent:alice", body="notif")
        self.assertTrue(result["ok"], result)

    async def test_receive_alias(self):
        """'receive' must actually receive messages (not error)."""
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        # Seed a message first.
        await bus_tool(ctx, op="send", to="agent:alice", body="via-receive-alias")

        result = await bus_tool(ctx, op="receive", agent="alice", wait_up_to=0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["method"], "GET")
        self.assertEqual(len(result["data"]["messages"]), 1)
        self.assertEqual(result["data"]["messages"][0]["body"], "via-receive-alias")

    async def test_drain_alias(self):
        """'drain' is equivalent to 'receive' (non-blocking drain)."""
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        await bus_tool(ctx, op="send", to="agent:alice", body="via-drain-alias")

        result = await bus_tool(ctx, op="drain", agent="alice", wait_up_to=0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["data"]["messages"]), 1)

    async def test_ack_alias(self):
        """'ack' must actually advance the cursor."""
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        await bus_tool(ctx, op="send", to="agent:alice", body="m-for-ack-alias")

        recv = await bus_tool(ctx, op="receive", agent="alice", wait_up_to=0)
        cursor = recv["data"]["next_cursor"]

        ack_result = await bus_tool(
            ctx, op="ack", agent="alice", up_to_cursor=cursor
        )
        self.assertTrue(ack_result["ok"], ack_result)
        self.assertEqual(ack_result["data"]["acked_through"], cursor)

        # Inbox should now be empty.
        recv2 = await bus_tool(ctx, op="receive", agent="alice", wait_up_to=0)
        self.assertEqual(recv2["data"]["messages"], [])

    async def test_acknowledge_alias(self):
        """'acknowledge' is equivalent to 'ack'."""
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        await bus_tool(ctx, op="send", to="agent:alice", body="m-for-acknowledge")

        recv = await bus_tool(ctx, op="receive", agent="alice", wait_up_to=0)
        cursor = recv["data"]["next_cursor"]

        ack_result = await bus_tool(
            ctx, op="acknowledge", agent="alice", up_to_cursor=cursor
        )
        self.assertTrue(ack_result["ok"], ack_result)

    async def test_peek_alias(self):
        """'peek' must return messages non-blockingly (maps to GET target=peek)."""
        from iterm_mcpy.tools.bus import bus as bus_tool

        ctx = _make_ctx(self.bus)
        for i in range(3):
            await bus_tool(ctx, op="send", to="agent:alice", body=f"peek-{i}")

        result = await bus_tool(ctx, op="peek", agent="alice")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["method"], "GET")
        self.assertEqual(len(result["data"]["messages"]), 3)


# ---------------------------------------------------------------------------
# BusEnvelope Model Test
# ---------------------------------------------------------------------------

class TestBusEnvelopeModel(unittest.TestCase):
    """BusEnvelope Pydantic model behaves correctly."""

    def test_defaults(self):
        from core.bus import BusEnvelope

        env = BusEnvelope(sender="s", recipient="agent:a")
        self.assertEqual(env.kind, "instruction")
        self.assertEqual(env.attempts, 0)
        self.assertIsNone(env.rowid)
        self.assertIsNotNone(env.message_id)

    def test_all_fields(self):
        from core.bus import BusEnvelope

        env = BusEnvelope(
            sender="agent:builder",
            recipient="agent:alice",
            kind="reply",
            body={"result": 42},
            correlation_id="corr-123",
            ttl_seconds=60,
        )
        self.assertEqual(env.correlation_id, "corr-123")
        self.assertEqual(env.ttl_seconds, 60)


if __name__ == "__main__":
    unittest.main()
