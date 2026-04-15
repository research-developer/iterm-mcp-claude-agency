"""Tests for workflows dispatcher (SP2 Task 12).

Covers the three legacy tools that workflows replaces:
    - list_workflow_events       -> GET  target=None / target='events'
    - get_workflow_event_history -> GET  target='history'
    - trigger_workflow_event     -> POST definer='TRIGGER'

subscribe_to_output_pattern is intentionally NOT covered — it migrates as
a separate action tool in Task 14.
"""
import asyncio
import json
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from iterm_mcpy.tools.workflows import WorkflowsDispatcher, workflows


def _make_ctx(event_bus=None, flow_manager=None, logger=None, **extra):
    """Build a fake MCP Context with a lifespan_context dict.

    event_bus defaults to a MagicMock with sensible async stubs; extras go
    straight into lifespan_context.
    """
    ctx = MagicMock()

    eb = event_bus
    if eb is None:
        eb = MagicMock()
        eb.get_registered_events = AsyncMock(return_value=[])
        eb.get_history = AsyncMock(return_value=[])
        eb.trigger = AsyncMock(return_value=None)
        # Private registry — the legacy list tool reads it directly, so
        # workflows does too.
        registry = MagicMock()
        registry.get_listeners = AsyncMock(return_value=[])
        registry.get_router = AsyncMock(return_value=None)
        registry.get_start_handler = AsyncMock(return_value=None)
        eb._registry = registry

    fm = flow_manager
    if fm is None:
        fm = MagicMock()
        fm.list_flows.return_value = []

    ctx.request_context.lifespan_context = {
        "event_bus": eb,
        "flow_manager": fm,
        "logger": logger or MagicMock(),
        **extra,
    }
    return ctx


def _make_event(
    name="test_event",
    event_id="evt-1",
    source="test_source",
    timestamp=None,
    priority_name="NORMAL",
):
    """Build a stand-in Event with the attributes the dispatcher reads."""
    event = MagicMock()
    event.name = name
    event.id = event_id
    event.source = source
    event.timestamp = timestamp or datetime(2026, 4, 14, 12, 0, 0)
    priority = MagicMock()
    priority.name = priority_name
    event.priority = priority
    return event


def _make_event_result(
    event=None,
    success=True,
    handler_name=None,
    routed_to=None,
    duration_ms=1.5,
    error=None,
):
    """Build a stand-in EventResult with the attributes the dispatcher reads."""
    result = MagicMock()
    result.event = event or _make_event()
    result.success = success
    result.handler_name = handler_name
    result.routed_to = routed_to
    result.duration_ms = duration_ms
    result.error = error
    return result


# ========================================================================= #
# OPTIONS                                                                   #
# ========================================================================= #


class TestOptions(unittest.TestCase):
    def test_options_returns_schema(self):
        parsed = json.loads(asyncio.run(workflows(ctx=_make_ctx(), op="OPTIONS")))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["collection"], "workflows")
        self.assertIn("GET", parsed["data"]["methods"])
        self.assertIn("POST", parsed["data"]["methods"])
        self.assertIn("HEAD", parsed["data"]["methods"])
        # Read-only on the collection level aside from TRIGGER — no
        # PATCH/PUT/DELETE advertised.
        self.assertNotIn("PATCH", parsed["data"]["methods"])
        self.assertNotIn("PUT", parsed["data"]["methods"])
        self.assertNotIn("DELETE", parsed["data"]["methods"])
        # Sub-resources: events + history.
        self.assertIn("events", parsed["data"]["sub_resources"])
        self.assertIn("history", parsed["data"]["sub_resources"])

    def test_options_lists_post_definers(self):
        parsed = json.loads(asyncio.run(workflows(ctx=_make_ctx(), op="OPTIONS")))
        post = parsed["data"]["methods"]["POST"]
        self.assertIn("TRIGGER", post["definers"])
        # CREATE / SEND / INVOKE shouldn't be advertised — workflows only
        # supports TRIGGER.
        self.assertNotIn("CREATE", post["definers"])
        self.assertNotIn("SEND", post["definers"])
        self.assertNotIn("INVOKE", post["definers"])

    def test_schema_verb_works(self):
        parsed = json.loads(asyncio.run(workflows(ctx=_make_ctx(), op="schema")))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])


# ========================================================================= #
# GET /workflows/events — list registered events                            #
# ========================================================================= #


class TestListEvents(unittest.TestCase):
    def test_list_events_default_target(self):
        eb = MagicMock()
        eb.get_registered_events = AsyncMock(return_value=["evt_b", "evt_a"])
        registry = MagicMock()
        # evt_a has 2 listeners + router; evt_b has none.
        registry.get_listeners = AsyncMock(
            side_effect=lambda name: [MagicMock(), MagicMock()] if name == "evt_a" else []
        )
        registry.get_router = AsyncMock(
            side_effect=lambda name: MagicMock() if name == "evt_a" else None
        )
        registry.get_start_handler = AsyncMock(return_value=None)
        eb._registry = registry

        fm = MagicMock()
        fm.list_flows.return_value = ["MyFlow"]

        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(event_bus=eb, flow_manager=fm),
            op="list",
        )))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["total_count"], 2)
        # Events come back sorted — evt_a before evt_b.
        events = parsed["data"]["events"]
        self.assertEqual(events[0]["event_name"], "evt_a")
        self.assertTrue(events[0]["has_listeners"])
        self.assertTrue(events[0]["has_router"])
        self.assertEqual(events[0]["listener_count"], 2)
        self.assertEqual(events[1]["event_name"], "evt_b")
        self.assertFalse(events[1]["has_listeners"])
        self.assertFalse(events[1]["has_router"])
        self.assertEqual(parsed["data"]["flows_registered"], ["MyFlow"])

    def test_list_events_empty(self):
        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(),
            op="list",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["total_count"], 0)
        self.assertEqual(parsed["data"]["events"], [])

    def test_list_events_explicit_target(self):
        """target='events' should yield the same list as target=None."""
        eb = MagicMock()
        eb.get_registered_events = AsyncMock(return_value=["evt_x"])
        registry = MagicMock()
        registry.get_listeners = AsyncMock(return_value=[MagicMock()])
        registry.get_router = AsyncMock(return_value=None)
        registry.get_start_handler = AsyncMock(return_value=MagicMock())
        eb._registry = registry

        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(event_bus=eb),
            op="GET", target="events",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["total_count"], 1)
        self.assertEqual(parsed["data"]["events"][0]["event_name"], "evt_x")
        self.assertTrue(parsed["data"]["events"][0]["is_start_event"])

    def test_list_events_no_flow_manager(self):
        """flow_manager is optional — missing one should leave flows_registered empty."""
        ctx = MagicMock()
        eb = MagicMock()
        eb.get_registered_events = AsyncMock(return_value=[])
        registry = MagicMock()
        registry.get_listeners = AsyncMock(return_value=[])
        registry.get_router = AsyncMock(return_value=None)
        registry.get_start_handler = AsyncMock(return_value=None)
        eb._registry = registry
        ctx.request_context.lifespan_context = {"event_bus": eb, "logger": MagicMock()}

        parsed = json.loads(asyncio.run(workflows(ctx=ctx, op="list")))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["flows_registered"], [])


# ========================================================================= #
# GET /workflows/events?target=history — event history                      #
# ========================================================================= #


class TestHistory(unittest.TestCase):
    def test_history_all_events(self):
        eb = MagicMock()
        eb.get_registered_events = AsyncMock(return_value=[])
        registry = MagicMock()
        registry.get_listeners = AsyncMock(return_value=[])
        registry.get_router = AsyncMock(return_value=None)
        registry.get_start_handler = AsyncMock(return_value=None)
        eb._registry = registry
        eb.get_history = AsyncMock(return_value=[
            _make_event_result(
                event=_make_event(name="evt1", event_id="e1"),
                success=True, handler_name="handler_a", duration_ms=2.0,
            ),
            _make_event_result(
                event=_make_event(name="evt2", event_id="e2"),
                success=False, error="boom", duration_ms=5.5,
            ),
        ])

        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(event_bus=eb),
            op="GET", target="history",
        )))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["total_count"], 2)
        entries = parsed["data"]["entries"]
        self.assertEqual(entries[0]["event_name"], "evt1")
        self.assertEqual(entries[0]["event_id"], "e1")
        self.assertTrue(entries[0]["success"])
        self.assertEqual(entries[0]["handler_name"], "handler_a")
        self.assertEqual(entries[1]["event_name"], "evt2")
        self.assertFalse(entries[1]["success"])
        self.assertEqual(entries[1]["error"], "boom")

        # limit should default to 100, success_only=False.
        _, kwargs = eb.get_history.call_args
        self.assertEqual(kwargs["limit"], 100)
        self.assertFalse(kwargs["success_only"])
        self.assertIsNone(kwargs["event_name"])

    def test_history_with_event_name_filter(self):
        eb = MagicMock()
        eb.get_history = AsyncMock(return_value=[
            _make_event_result(
                event=_make_event(name="specific_evt", event_id="e99"),
            ),
        ])

        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(event_bus=eb),
            op="GET", target="history",
            event_name="specific_evt",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["total_count"], 1)
        self.assertEqual(parsed["data"]["entries"][0]["event_name"], "specific_evt")

        _, kwargs = eb.get_history.call_args
        self.assertEqual(kwargs["event_name"], "specific_evt")

    def test_history_with_success_only_and_limit(self):
        eb = MagicMock()
        eb.get_history = AsyncMock(return_value=[])

        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(event_bus=eb),
            op="GET", target="history",
            success_only=True, limit=50,
        )))
        self.assertTrue(parsed["ok"])
        _, kwargs = eb.get_history.call_args
        self.assertTrue(kwargs["success_only"])
        self.assertEqual(kwargs["limit"], 50)

    def test_history_limit_capped_at_1000(self):
        eb = MagicMock()
        eb.get_history = AsyncMock(return_value=[])

        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(event_bus=eb),
            op="GET", target="history",
            limit=5000,
        )))
        self.assertTrue(parsed["ok"])
        _, kwargs = eb.get_history.call_args
        self.assertEqual(kwargs["limit"], 1000)


# ========================================================================= #
# POST /workflows/events (TRIGGER) — trigger event                          #
# ========================================================================= #


class TestTrigger(unittest.TestCase):
    def test_trigger_queued(self):
        eb = MagicMock()
        eb.trigger = AsyncMock(return_value=None)

        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(event_bus=eb),
            op="trigger",
            event_name="my_event",
            payload={"key": "value"},
        )))
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "TRIGGER")
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["success"])
        self.assertTrue(parsed["data"]["queued"])
        self.assertFalse(parsed["data"]["processed"])
        eb.trigger.assert_awaited_once()

        # Check kwargs passed to the bus.
        _, kwargs = eb.trigger.call_args
        self.assertEqual(kwargs["event_name"], "my_event")
        self.assertEqual(kwargs["payload"], {"key": "value"})
        self.assertFalse(kwargs["immediate"])

    def test_trigger_immediate_success(self):
        event = _make_event(name="immediate_evt", event_id="ie-1", priority_name="HIGH")
        result = _make_event_result(
            event=event,
            success=True,
            handler_name="my_handler",
            routed_to=None,
            duration_ms=3.2,
        )
        eb = MagicMock()
        eb.trigger = AsyncMock(return_value=result)

        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(event_bus=eb),
            op="trigger",
            event_name="immediate_evt",
            immediate=True,
            priority="high",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["definer"], "TRIGGER")
        data = parsed["data"]
        self.assertTrue(data["success"])
        self.assertFalse(data["queued"])
        self.assertTrue(data["processed"])
        self.assertEqual(data["event"]["name"], "immediate_evt")
        self.assertEqual(data["event"]["id"], "ie-1")
        self.assertEqual(data["event"]["priority"], "high")
        self.assertEqual(data["handler_name"], "my_handler")

    def test_trigger_via_post_plus_definer(self):
        eb = MagicMock()
        eb.trigger = AsyncMock(return_value=None)

        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(event_bus=eb),
            op="POST", definer="TRIGGER",
            event_name="my_event",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["definer"], "TRIGGER")
        self.assertTrue(parsed["data"]["queued"])

    def test_trigger_via_start_verb(self):
        eb = MagicMock()
        eb.trigger = AsyncMock(return_value=None)

        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(event_bus=eb),
            op="start",
            event_name="my_event",
        )))
        # 'start' is a TRIGGER-family verb in VERB_ATLAS.
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "TRIGGER")

    def test_trigger_missing_event_name_returns_err(self):
        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(),
            op="trigger",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("event_name", parsed["error"].lower())

    def test_trigger_with_source_and_metadata(self):
        eb = MagicMock()
        eb.trigger = AsyncMock(return_value=None)

        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(event_bus=eb),
            op="trigger",
            event_name="my_event",
            source="my_flow",
            metadata={"trace_id": "abc123"},
        )))
        self.assertTrue(parsed["ok"])
        _, kwargs = eb.trigger.call_args
        self.assertEqual(kwargs["source"], "my_flow")
        self.assertEqual(kwargs["metadata"], {"trace_id": "abc123"})

    def test_trigger_immediate_failure_preserves_error(self):
        event = _make_event(name="bad_evt", event_id="be-1")
        result = _make_event_result(
            event=event,
            success=False,
            handler_name="failing_handler",
            error="handler exploded",
        )
        eb = MagicMock()
        eb.trigger = AsyncMock(return_value=result)

        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(event_bus=eb),
            op="trigger",
            event_name="bad_evt",
            immediate=True,
        )))
        # Envelope is ok=true (handler completed); the payload reports
        # success=false with the error message.
        self.assertTrue(parsed["ok"])
        self.assertFalse(parsed["data"]["success"])
        self.assertEqual(parsed["data"]["error"], "handler exploded")
        self.assertEqual(parsed["data"]["handler_name"], "failing_handler")


# ========================================================================= #
# HEAD                                                                      #
# ========================================================================= #


class TestHead(unittest.TestCase):
    def test_head_returns_compact_envelope(self):
        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(),
            op="HEAD",
        )))
        self.assertEqual(parsed["method"], "HEAD")
        self.assertTrue(parsed["ok"])


# ========================================================================= #
# Unknown op / unsupported combinations                                     #
# ========================================================================= #


class TestUnknownOp(unittest.TestCase):
    def test_bad_verb_returns_err_envelope(self):
        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(),
            op="frobnicate",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("Unknown op", parsed["error"])


class TestUnsupportedDefiners(unittest.TestCase):
    def test_post_create_not_implemented(self):
        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(),
            op="POST", definer="CREATE",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not implemented", parsed["error"].lower())

    def test_post_send_not_implemented(self):
        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(),
            op="POST", definer="SEND",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not implemented", parsed["error"].lower())

    def test_patch_not_implemented(self):
        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(),
            op="PATCH", definer="MODIFY",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not implemented", parsed["error"].lower())

    def test_delete_not_implemented(self):
        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(),
            op="DELETE",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not implemented", parsed["error"].lower())

    def test_wrong_family_definer_rejected(self):
        # REPLACE belongs to PUT, not POST.
        parsed = json.loads(asyncio.run(workflows(
            ctx=_make_ctx(),
            op="POST", definer="REPLACE",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not in POST family", parsed["error"])


# ========================================================================= #
# Dispatcher direct instantiation (sanity check)                            #
# ========================================================================= #


class TestDispatcherDirect(unittest.TestCase):
    def test_collection_name_and_sub_resources(self):
        d = WorkflowsDispatcher()
        self.assertEqual(d.collection, "workflows")
        self.assertIn("events", d.sub_resources)
        self.assertIn("history", d.sub_resources)


if __name__ == "__main__":
    unittest.main()
