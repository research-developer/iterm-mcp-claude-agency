"""Tests for the SP2 action tools (Tasks 13 + 14).

Covers all 6 action tools in one file:
    - messages_v2   (POST+SEND)
    - orchestrate_v2 (POST+INVOKE)
    - delegate_v2    (POST+INVOKE, target="task" | "plan")
    - wait_for_v2    (GET)
    - subscribe_v2   (POST+TRIGGER)
    - telemetry_v2   (POST+TRIGGER, DELETE)

Each tool gets 3–5 tests covering happy path, wrong op, unknown verb, and
missing required param.
"""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from iterm_mcpy.tools.messages_v2 import messages_v2
from iterm_mcpy.tools.orchestrate_v2 import orchestrate_v2
from iterm_mcpy.tools.delegate_v2 import delegate_v2
from iterm_mcpy.tools.wait_for_v2 import wait_for_v2
from iterm_mcpy.tools.subscribe_v2 import subscribe_v2
from iterm_mcpy.tools.telemetry_v2 import telemetry_v2


def _make_ctx(**extra):
    """Build a fake MCP Context with a lifespan_context dict.

    ``**extra`` keys go straight into lifespan_context — tests inject
    whichever managers they need.
    """
    ctx = MagicMock()
    lifespan = {
        "terminal": MagicMock(),
        "agent_registry": MagicMock(),
        "logger": MagicMock(),
        "notification_manager": MagicMock(),
    }
    lifespan.update(extra)
    ctx.request_context.lifespan_context = lifespan
    return ctx


# ========================================================================= #
# messages_v2 — POST+SEND                                                   #
# ========================================================================= #


class TestMessagesV2(unittest.TestCase):
    def test_cascade_happy_path(self):
        # Mock execute_cascade_request to avoid touching the agent registry
        # internals — we already have dedicated helpers tests.
        from core.models import CascadeMessageResponse

        async def fake_cascade(req, term, reg, log):
            return CascadeMessageResponse(
                results=[],
                delivered_count=1,
                skipped_count=0,
            )

        with patch(
            "iterm_mcpy.tools.messages_v2.execute_cascade_request",
            side_effect=fake_cascade,
        ):
            parsed = json.loads(asyncio.run(messages_v2(
                ctx=_make_ctx(),
                op="send",
                cascade={"broadcast": "hello"},
            )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "SEND")
        self.assertEqual(parsed["data"]["delivered_count"], 1)

    def test_wrong_op_returns_err(self):
        # GET is the wrong family for messages_v2.
        parsed = json.loads(asyncio.run(messages_v2(
            ctx=_make_ctx(),
            op="GET",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("POST+SEND", parsed["error"])

    def test_wrong_definer_returns_err(self):
        # CREATE is a valid POST definer but not what messages_v2 supports.
        parsed = json.loads(asyncio.run(messages_v2(
            ctx=_make_ctx(),
            op="POST", definer="CREATE",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("POST+SEND", parsed["error"])

    def test_unknown_verb_returns_err(self):
        parsed = json.loads(asyncio.run(messages_v2(
            ctx=_make_ctx(),
            op="frobnicate",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("Unknown op", parsed["error"])

    def test_missing_cascade_and_targets_returns_err(self):
        parsed = json.loads(asyncio.run(messages_v2(
            ctx=_make_ctx(),
            op="send",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("cascade", parsed["error"].lower())
        self.assertIn("targets", parsed["error"].lower())

    def test_both_cascade_and_targets_returns_err(self):
        parsed = json.loads(asyncio.run(messages_v2(
            ctx=_make_ctx(),
            op="send",
            cascade={"broadcast": "hi"},
            targets=[{"agent": "alice"}],
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not both", parsed["error"].lower())


# ========================================================================= #
# orchestrate_v2 — POST+INVOKE                                              #
# ========================================================================= #


class TestOrchestrateV2(unittest.TestCase):
    def _ctx(self):
        return _make_ctx(
            layout_manager=MagicMock(),
            profile_manager=MagicMock(),
        )

    def test_happy_path_empty_playbook(self):
        parsed = json.loads(asyncio.run(orchestrate_v2(
            ctx=self._ctx(),
            op="invoke",
            playbook={},  # no layout/commands/cascade/reads
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "INVOKE")
        # No layout, commands, cascade, or reads → all omitted from envelope.
        self.assertEqual(parsed["data"].get("commands", []), [])

    def test_happy_path_with_commands(self):
        from core.models import WriteToSessionsResponse

        async def fake_write(req, term, reg, log, lock_manager=None, notification_manager=None):
            return WriteToSessionsResponse(
                results=[], sent_count=1, skipped_count=0, error_count=0,
            )

        with patch(
            "iterm_mcpy.tools.orchestrate_v2.execute_write_request",
            side_effect=fake_write,
        ):
            parsed = json.loads(asyncio.run(orchestrate_v2(
                ctx=self._ctx(),
                op="POST", definer="INVOKE",
                playbook={
                    "commands": [
                        {
                            "name": "step1",
                            "messages": [
                                {
                                    "content": "echo hi",
                                    "targets": [{"agent": "alice"}],
                                },
                            ],
                        },
                    ],
                },
            )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(len(parsed["data"]["commands"]), 1)
        self.assertEqual(parsed["data"]["commands"][0]["name"], "step1")

    def test_wrong_op_returns_err(self):
        parsed = json.loads(asyncio.run(orchestrate_v2(
            ctx=self._ctx(),
            op="GET",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("POST+INVOKE", parsed["error"])

    def test_unknown_verb_returns_err(self):
        parsed = json.loads(asyncio.run(orchestrate_v2(
            ctx=self._ctx(),
            op="frobnicate",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("Unknown op", parsed["error"])

    def test_missing_playbook_returns_err(self):
        parsed = json.loads(asyncio.run(orchestrate_v2(
            ctx=self._ctx(),
            op="invoke",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("playbook", parsed["error"].lower())


# ========================================================================= #
# delegate_v2 — POST+INVOKE (target=task|plan)                              #
# ========================================================================= #


class TestDelegateV2(unittest.TestCase):
    def _ctx(self, manager_registry=None):
        return _make_ctx(manager_registry=manager_registry or MagicMock())

    def test_task_happy_path(self):
        from core.manager import TaskStatus
        # Stub the manager's delegate method to return a fake TaskResult.
        manager = MagicMock()
        manager.delegate = AsyncMock(return_value=MagicMock(
            task_id="t-1",
            task="echo hi",
            worker="alice",
            status=MagicMock(value="completed"),
            success=True,
            output="hi",
            error=None,
            duration_seconds=0.5,
            validation_passed=True,
            validation_message=None,
        ))
        manager_registry = MagicMock()
        manager_registry.get_manager.return_value = manager

        # Also patch _setup_manager_callbacks (it's fine as a no-op here).
        with patch("iterm_mcpy.tools.delegate_v2._setup_manager_callbacks"):
            parsed = json.loads(asyncio.run(delegate_v2(
                ctx=self._ctx(manager_registry=manager_registry),
                op="delegate",
                target="task",
                manager_name="mgr1",
                task="echo hi",
            )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "INVOKE")
        self.assertEqual(parsed["data"]["status"], "completed")
        self.assertEqual(parsed["data"]["worker"], "alice")

    def test_task_missing_manager_returns_err(self):
        parsed = json.loads(asyncio.run(delegate_v2(
            ctx=self._ctx(),
            op="delegate",
            target="task",
            task="echo hi",  # manager_name missing
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("manager_name", parsed["error"])

    def test_task_manager_not_found_returns_err(self):
        manager_registry = MagicMock()
        manager_registry.get_manager.return_value = None

        parsed = json.loads(asyncio.run(delegate_v2(
            ctx=self._ctx(manager_registry=manager_registry),
            op="delegate",
            target="task",
            manager_name="ghost",
            task="echo hi",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("ghost", parsed["error"])
        self.assertIn("not found", parsed["error"].lower())

    def test_plan_happy_path(self):
        manager = MagicMock()
        manager.orchestrate = AsyncMock(return_value=MagicMock(
            plan_name="p1",
            success=True,
            results=[],
            duration_seconds=1.0,
            stopped_early=False,
            stop_reason=None,
        ))
        manager_registry = MagicMock()
        manager_registry.get_manager.return_value = manager

        with patch("iterm_mcpy.tools.delegate_v2._setup_manager_callbacks"):
            parsed = json.loads(asyncio.run(delegate_v2(
                ctx=self._ctx(manager_registry=manager_registry),
                op="POST", definer="INVOKE",
                target="plan",
                manager_name="mgr1",
                plan={
                    "name": "p1",
                    "steps": [
                        {"id": "s1", "task": "echo hello"},
                    ],
                },
            )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["plan_name"], "p1")

    def test_plan_missing_plan_returns_err(self):
        parsed = json.loads(asyncio.run(delegate_v2(
            ctx=self._ctx(),
            op="delegate",
            target="plan",
            manager_name="mgr1",  # plan missing
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("plan", parsed["error"].lower())

    def test_bad_target_returns_err(self):
        parsed = json.loads(asyncio.run(delegate_v2(
            ctx=self._ctx(),
            op="delegate",
            target="mystery",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("target must be", parsed["error"])

    def test_wrong_op_returns_err(self):
        parsed = json.loads(asyncio.run(delegate_v2(
            ctx=self._ctx(),
            op="GET",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("POST+INVOKE", parsed["error"])


# ========================================================================= #
# wait_for_v2 — GET                                                         #
# ========================================================================= #


class TestWaitForV2(unittest.TestCase):
    def test_happy_path_agent_found_idle(self):
        from core.agents import Agent

        agent_registry = MagicMock()
        agent_registry.get_agent.return_value = Agent(
            name="alice", session_id="s1", teams=[],
        )
        terminal = MagicMock()
        session = MagicMock()
        session.get_screen_contents = AsyncMock(return_value="done")
        session.is_processing = False
        terminal.get_session_by_id = AsyncMock(return_value=session)

        notification_manager = MagicMock()
        notification_manager.add_simple = AsyncMock()

        parsed = json.loads(asyncio.run(wait_for_v2(
            ctx=_make_ctx(
                agent_registry=agent_registry,
                terminal=terminal,
                notification_manager=notification_manager,
            ),
            op="GET",
            agent_name="alice",
            wait_up_to=5,
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["data"]["completed"])
        self.assertEqual(parsed["data"]["agent"], "alice")

    def test_agent_not_found(self):
        agent_registry = MagicMock()
        agent_registry.get_agent.return_value = None

        parsed = json.loads(asyncio.run(wait_for_v2(
            ctx=_make_ctx(agent_registry=agent_registry),
            op="GET",
            agent_name="ghost",
        )))
        self.assertTrue(parsed["ok"])  # envelope is ok; payload carries the error.
        self.assertFalse(parsed["data"]["completed"])
        self.assertEqual(parsed["data"]["status"], "unknown")
        self.assertIn("ghost", parsed["data"]["summary"])

    def test_wrong_op_returns_err(self):
        parsed = json.loads(asyncio.run(wait_for_v2(
            ctx=_make_ctx(),
            op="POST",
            agent_name="alice",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("GET", parsed["error"])

    def test_missing_agent_name_returns_err(self):
        parsed = json.loads(asyncio.run(wait_for_v2(
            ctx=_make_ctx(),
            op="GET",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("agent_name", parsed["error"])

    def test_unknown_verb_returns_err(self):
        parsed = json.loads(asyncio.run(wait_for_v2(
            ctx=_make_ctx(),
            op="frobnicate",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("Unknown op", parsed["error"])

    def test_out_of_range_timeout_returns_err(self):
        parsed = json.loads(asyncio.run(wait_for_v2(
            ctx=_make_ctx(),
            op="GET",
            agent_name="alice",
            wait_up_to=0,  # below min=1
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("1 and 600", parsed["error"])


# ========================================================================= #
# subscribe_v2 — POST+TRIGGER                                               #
# ========================================================================= #


class TestSubscribeV2(unittest.TestCase):
    def test_happy_path(self):
        event_bus = MagicMock()
        event_bus.subscribe_to_pattern = AsyncMock(return_value="sub-123")

        parsed = json.loads(asyncio.run(subscribe_v2(
            ctx=_make_ctx(event_bus=event_bus),
            op="subscribe",
            pattern=r"error:\s",
            event_name="error_event",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "TRIGGER")
        self.assertEqual(parsed["data"]["subscription_id"], "sub-123")
        self.assertEqual(parsed["data"]["pattern"], r"error:\s")
        self.assertEqual(parsed["data"]["event_name"], "error_event")

    def test_post_plus_trigger_explicit(self):
        event_bus = MagicMock()
        event_bus.subscribe_to_pattern = AsyncMock(return_value="sub-1")

        parsed = json.loads(asyncio.run(subscribe_v2(
            ctx=_make_ctx(event_bus=event_bus),
            op="POST", definer="TRIGGER",
            pattern="foo",
        )))
        self.assertTrue(parsed["ok"])

    def test_wrong_op_returns_err(self):
        parsed = json.loads(asyncio.run(subscribe_v2(
            ctx=_make_ctx(),
            op="GET",
            pattern="foo",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("POST+TRIGGER", parsed["error"])

    def test_wrong_definer_returns_err(self):
        parsed = json.loads(asyncio.run(subscribe_v2(
            ctx=_make_ctx(),
            op="POST", definer="CREATE",
            pattern="foo",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("POST+TRIGGER", parsed["error"])

    def test_missing_pattern_returns_err(self):
        parsed = json.loads(asyncio.run(subscribe_v2(
            ctx=_make_ctx(),
            op="subscribe",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("pattern", parsed["error"].lower())

    def test_bad_regex_returns_err(self):
        parsed = json.loads(asyncio.run(subscribe_v2(
            ctx=_make_ctx(),
            op="subscribe",
            pattern="[bad(regex",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("Invalid regex", parsed["error"])


# ========================================================================= #
# telemetry_v2 — POST+TRIGGER / DELETE                                      #
# ========================================================================= #


class TestTelemetryV2(unittest.TestCase):
    def test_start_happy_path(self):
        ctx = _make_ctx(
            telemetry=MagicMock(),
            terminal=MagicMock(),
        )
        # Patch start_dashboard where it's imported — inside _start_dashboard.
        with patch("core.dashboard.start_dashboard", new=AsyncMock(return_value="running on 9999")):
            parsed = json.loads(asyncio.run(telemetry_v2(
                ctx=ctx,
                op="start",
                port=9999,
                duration_seconds=10,
            )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "TRIGGER")
        self.assertEqual(parsed["data"]["status"], "started")
        self.assertIn("9999", parsed["data"]["url"])

    def test_stop_happy_path(self):
        with patch("core.dashboard.stop_dashboard", new=AsyncMock()):
            parsed = json.loads(asyncio.run(telemetry_v2(
                ctx=_make_ctx(),
                op="stop",
            )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["method"], "DELETE")
        self.assertEqual(parsed["data"]["status"], "stopped")

    def test_wrong_definer_returns_err(self):
        parsed = json.loads(asyncio.run(telemetry_v2(
            ctx=_make_ctx(),
            op="POST", definer="CREATE",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("TRIGGER", parsed["error"])

    def test_unknown_verb_returns_err(self):
        parsed = json.loads(asyncio.run(telemetry_v2(
            ctx=_make_ctx(),
            op="frobnicate",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("Unknown op", parsed["error"])

    def test_patch_not_supported(self):
        parsed = json.loads(asyncio.run(telemetry_v2(
            ctx=_make_ctx(),
            op="PATCH", definer="MODIFY",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("POST+TRIGGER or DELETE", parsed["error"])


# ========================================================================= #
# Registration sanity                                                       #
# ========================================================================= #


class TestRegistration(unittest.TestCase):
    def test_all_action_tools_register(self):
        """Each action tool's ``register`` helper should call mcp.tool(name=...)."""
        from iterm_mcpy.tools import (
            messages_v2 as m,
            orchestrate_v2 as o,
            delegate_v2 as d,
            wait_for_v2 as w,
            subscribe_v2 as s,
            telemetry_v2 as t,
        )

        for mod, expected_name in [
            (m, "messages_v2"),
            (o, "orchestrate_v2"),
            (d, "delegate_v2"),
            (w, "wait_for_v2"),
            (s, "subscribe_v2"),
            (t, "telemetry_v2"),
        ]:
            mcp = MagicMock()
            tool_decorator = MagicMock(side_effect=lambda f: f)
            mcp.tool.return_value = tool_decorator
            mod.register(mcp)
            mcp.tool.assert_called_with(name=expected_name)


if __name__ == "__main__":
    unittest.main()
