"""Tests for agents dispatcher (SP2 Task 5)."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from iterm_mcpy.tools.agents import AgentsDispatcher, agents


def _make_ctx(
    terminal=None,
    agent_registry=None,
    lock_manager=None,
    notification_manager=None,
    logger=None,
    **extra,
):
    """Build a fake MCP Context with the lifespan context filled in.

    `**extra` keys go straight into `lifespan_context` so tests can inject
    whichever managers they need.
    """
    ctx = MagicMock()
    ctx.request_context.lifespan_context = {
        "terminal": terminal or MagicMock(),
        "agent_registry": agent_registry or MagicMock(),
        "tag_lock_manager": lock_manager,
        "notification_manager": notification_manager or MagicMock(),
        "logger": logger or MagicMock(),
        **extra,
    }
    return ctx


# ========================================================================= #
# OPTIONS / HEAD / verbs                                                    #
# ========================================================================= #


class TestOptions(unittest.TestCase):
    def test_options_returns_schema(self):
        async def go():
            return await agents(ctx=_make_ctx(), op="OPTIONS")

        result = asyncio.run(go())
        parsed = json.loads(result)
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["collection"], "agents")
        self.assertIn("GET", parsed["data"]["methods"])
        self.assertIn("POST", parsed["data"]["methods"])
        self.assertIn("PATCH", parsed["data"]["methods"])
        self.assertIn("DELETE", parsed["data"]["methods"])
        # All four sub-resources should be advertised.
        subs = parsed["data"]["sub_resources"]
        for name in ("status", "notifications", "hooks", "locks"):
            self.assertIn(name, subs)

    def test_options_lists_post_definers(self):
        parsed = json.loads(asyncio.run(agents(ctx=_make_ctx(), op="OPTIONS")))
        post = parsed["data"]["methods"]["POST"]
        self.assertIn("CREATE", post["definers"])
        self.assertIn("SEND", post["definers"])
        self.assertIn("TRIGGER", post["definers"])

    def test_schema_verb_works(self):
        parsed = json.loads(asyncio.run(agents(ctx=_make_ctx(), op="schema")))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])


class TestUnknownOp(unittest.TestCase):
    def test_bad_verb_returns_err_envelope(self):
        parsed = json.loads(asyncio.run(agents(ctx=_make_ctx(), op="frobnicate")))
        self.assertFalse(parsed["ok"])
        self.assertIn("Unknown op", parsed["error"]["message"])


class TestWrongDefiner(unittest.TestCase):
    def test_post_replace_rejected(self):
        # REPLACE is in the PUT family, not POST.
        parsed = json.loads(asyncio.run(
            agents(ctx=_make_ctx(), op="POST", definer="REPLACE")
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("not in POST family", parsed["error"]["message"])


# ========================================================================= #
# GET /agents — list                                                        #
# ========================================================================= #


class TestListAgents(unittest.TestCase):
    def test_list_delegates_to_registry(self):
        from core.agents import Agent

        agent_registry = MagicMock()
        agent_registry.list_agents.return_value = [
            Agent(name="alice", session_id="s1", teams=["t1"]),
            Agent(name="bob", session_id="s2", teams=[]),
        ]

        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(agent_registry=agent_registry),
            op="list",
        )))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertEqual(len(parsed["data"]), 2)
        self.assertEqual(parsed["data"][0]["name"], "alice")
        self.assertEqual(parsed["data"][1]["session_id"], "s2")
        agent_registry.list_agents.assert_called_once_with(team=None)

    def test_list_filters_by_team(self):
        from core.agents import Agent

        agent_registry = MagicMock()
        agent_registry.list_agents.return_value = [
            Agent(name="alice", session_id="s1", teams=["backend"]),
        ]
        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(agent_registry=agent_registry),
            op="list", team="backend",
        )))
        self.assertTrue(parsed["ok"])
        agent_registry.list_agents.assert_called_once_with(team="backend")


class TestHead(unittest.TestCase):
    def test_head_projects_via_agent_head_fields(self):
        from core.agents import Agent

        agent_registry = MagicMock()
        agent_registry.list_agents.return_value = [
            Agent(
                name="alice",
                session_id="s1",
                teams=["t1"],
                metadata={"key": "value"},
            ),
        ]
        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(agent_registry=agent_registry),
            op="HEAD",
        )))
        self.assertEqual(parsed["method"], "HEAD")
        self.assertTrue(parsed["ok"])
        # HEAD_FIELDS = {name, session_id, teams}; metadata must be excluded.
        head = parsed["data"][0]
        self.assertEqual(head["name"], "alice")
        self.assertEqual(head["session_id"], "s1")
        self.assertEqual(head["teams"], ["t1"])
        self.assertNotIn("metadata", head)
        self.assertNotIn("created_at", head)


# ========================================================================= #
# POST /agents (CREATE) — register_agent                                    #
# ========================================================================= #


class TestRegisterAgent(unittest.TestCase):
    def test_register_delegates_to_registry(self):
        from core.agents import Agent

        fake_session = MagicMock()
        fake_session.id = "sid-1"
        fake_session.name = "pane-1"

        terminal = MagicMock()
        terminal.get_session_by_id = AsyncMock(return_value=fake_session)

        agent_registry = MagicMock()
        agent_registry.register_agent.return_value = Agent(
            name="alice", session_id="sid-1", teams=["backend"],
        )

        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(terminal=terminal, agent_registry=agent_registry),
            op="register",  # -> POST + CREATE
            agent_name="alice",
            session_id="sid-1",
            team="backend",
        )))
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "CREATE")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["agent"], "alice")
        self.assertEqual(parsed["data"]["session_id"], "sid-1")
        self.assertEqual(parsed["data"]["teams"], ["backend"])
        agent_registry.register_agent.assert_called_once_with(
            name="alice",
            session_id="sid-1",
            teams=["backend"],
            metadata={},
        )

    def test_register_with_teams_list(self):
        from core.agents import Agent

        fake_session = MagicMock()
        fake_session.id = "sid-1"
        fake_session.name = "pane-1"
        terminal = MagicMock()
        terminal.get_session_by_id = AsyncMock(return_value=fake_session)

        agent_registry = MagicMock()
        agent_registry.register_agent.return_value = Agent(
            name="alice", session_id="sid-1", teams=["a", "b"],
        )

        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(terminal=terminal, agent_registry=agent_registry),
            op="POST", definer="CREATE",
            agent_name="alice", session_id="sid-1",
            teams=["a", "b"],
        )))
        self.assertTrue(parsed["ok"])
        agent_registry.register_agent.assert_called_once_with(
            name="alice", session_id="sid-1", teams=["a", "b"], metadata={},
        )

    def test_register_missing_session_id_returns_err(self):
        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(),
            op="register", agent_name="alice",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("session_id", parsed["error"]["message"].lower())

    def test_register_missing_agent_name_returns_err(self):
        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(),
            op="register", session_id="sid",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("agent_name", parsed["error"]["message"].lower())

    def test_register_session_not_found(self):
        terminal = MagicMock()
        terminal.get_session_by_id = AsyncMock(return_value=None)

        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(terminal=terminal),
            op="register", agent_name="alice", session_id="missing",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("no matching session", parsed["error"]["message"].lower())


# ========================================================================= #
# POST /agents/{name}/notifications (SEND) — notify                         #
# ========================================================================= #


class TestNotify(unittest.TestCase):
    def test_notify_delegates_to_manager(self):
        notification_manager = MagicMock()
        notification_manager.add_simple = AsyncMock()

        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(notification_manager=notification_manager),
            op="notify", target="notifications",
            agent="alice", level="info", summary="task done",
        )))
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "SEND")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["agent"], "alice")
        notification_manager.add_simple.assert_awaited_once_with(
            agent="alice",
            level="info",
            summary="task done",
            context=None,
            action_hint=None,
        )

    def test_notify_with_context_and_hint(self):
        notification_manager = MagicMock()
        notification_manager.add_simple = AsyncMock()

        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(notification_manager=notification_manager),
            op="POST", definer="SEND", target="notifications",
            agent="alice", level="warning", summary="watch out",
            context="x failed", action_hint="restart",
        )))
        self.assertTrue(parsed["ok"])
        notification_manager.add_simple.assert_awaited_once_with(
            agent="alice", level="warning", summary="watch out",
            context="x failed", action_hint="restart",
        )

    def test_notify_missing_level_returns_err(self):
        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(),
            op="notify", target="notifications",
            agent="alice", summary="x",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("level", parsed["error"]["message"].lower())


# ========================================================================= #
# GET /agents/{name}/notifications                                          #
# ========================================================================= #


class TestGetNotifications(unittest.TestCase):
    def test_get_notifications_delegates_to_manager(self):
        from core.models import AgentNotification
        from datetime import datetime

        notification_manager = MagicMock()
        notification_manager.get = AsyncMock(return_value=[
            AgentNotification(
                agent="alice", timestamp=datetime.now(),
                level="info", summary="hi",
            ),
        ])

        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(notification_manager=notification_manager),
            op="GET", target="notifications", agent="alice",
        )))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["total_count"], 1)
        self.assertEqual(len(parsed["data"]["notifications"]), 1)
        notification_manager.get.assert_awaited_once()
        call_kwargs = notification_manager.get.await_args.kwargs
        self.assertEqual(call_kwargs["agent"], "alice")

    def test_get_notifications_with_level_and_limit(self):
        notification_manager = MagicMock()
        notification_manager.get = AsyncMock(return_value=[])

        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(notification_manager=notification_manager),
            op="GET", target="notifications",
            level="error", limit=5,
        )))
        self.assertTrue(parsed["ok"])
        call_kwargs = notification_manager.get.await_args.kwargs
        self.assertEqual(call_kwargs["level"], "error")
        self.assertEqual(call_kwargs["limit"], 5)


# ========================================================================= #
# GET /agents/status — status summary                                       #
# ========================================================================= #


class TestGetStatusSummary(unittest.TestCase):
    def test_status_summary_produces_formatted_string(self):
        from core.agents import Agent
        from core.models import AgentNotification
        from datetime import datetime

        notification_manager = MagicMock()
        # STATUS_ICONS comes from the real class; expose it on the mock.
        notification_manager.STATUS_ICONS = {"info": "I", "error": "E"}
        notification_manager.get_latest_per_agent = AsyncMock(return_value={
            "alice": AgentNotification(
                agent="alice", timestamp=datetime.now(),
                level="info", summary="all good",
            ),
        })

        agent_registry = MagicMock()
        agent_registry.list_agents.return_value = [
            Agent(name="alice", session_id="s1"),
            Agent(name="bob", session_id="s2"),
        ]

        lock_manager = MagicMock()
        lock_manager.get_locks_by_agent.return_value = []

        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(
                notification_manager=notification_manager,
                agent_registry=agent_registry,
                lock_manager=lock_manager,
            ),
            op="GET", target="status",
        )))
        self.assertTrue(parsed["ok"])
        # The data is the formatted string the legacy tool produced.
        self.assertIsInstance(parsed["data"], str)
        self.assertIn("Agent Status", parsed["data"])
        self.assertIn("alice", parsed["data"])
        # bob had no notification — a placeholder "No activity" row is added.
        self.assertIn("bob", parsed["data"])

    def test_status_summary_no_notifications_and_no_agents(self):
        notification_manager = MagicMock()
        notification_manager.STATUS_ICONS = {}
        notification_manager.get_latest_per_agent = AsyncMock(return_value={})
        agent_registry = MagicMock()
        agent_registry.list_agents.return_value = []

        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(
                notification_manager=notification_manager,
                agent_registry=agent_registry,
            ),
            op="GET", target="status",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"], "━━━ No notifications ━━━")


# ========================================================================= #
# GET /agents/{name}/hooks                                                  #
# ========================================================================= #


class TestGetHooks(unittest.TestCase):
    def test_get_hooks_default_op_is_get_config(self):
        async def go():
            from iterm_mcpy.tools import agents as mod
            fake_response_json = json.dumps({
                "operation": "get_config",
                "success": True,
                "data": {"enabled": True},
            })
            with patch.object(
                mod,
                "_manage_agent_hooks",
                new=AsyncMock(return_value=fake_response_json),
            ) as mock_legacy:
                result = await agents(
                    ctx=_make_ctx(),
                    op="GET", target="hooks",
                )
                return mock_legacy.call_args, result

        call_args, result = asyncio.run(go())
        parsed = json.loads(result)
        self.assertTrue(parsed["ok"])
        # Legacy tool was called with ManageAgentHooksRequest(operation="get_config").
        request = call_args.args[0]
        self.assertEqual(request.operation, "get_config")
        # Data is the parsed legacy response dict.
        self.assertEqual(parsed["data"]["data"]["enabled"], True)

    def test_get_hooks_with_explicit_op(self):
        async def go():
            from iterm_mcpy.tools import agents as mod
            fake_response_json = json.dumps({
                "operation": "get_stats",
                "success": True,
                "data": {"total": 42},
            })
            with patch.object(
                mod,
                "_manage_agent_hooks",
                new=AsyncMock(return_value=fake_response_json),
            ) as mock_legacy:
                result = await agents(
                    ctx=_make_ctx(),
                    op="GET", target="hooks",
                    hooks_op="get_stats",
                )
                return mock_legacy.call_args, result

        call_args, result = asyncio.run(go())
        parsed = json.loads(result)
        self.assertTrue(parsed["ok"])
        request = call_args.args[0]
        self.assertEqual(request.operation, "get_stats")


class TestPatchHooks(unittest.TestCase):
    def test_patch_hooks_defaults_to_update_config(self):
        async def go():
            from iterm_mcpy.tools import agents as mod
            fake_response_json = json.dumps({
                "operation": "update_config",
                "success": True,
                "data": {"updated": {"enabled": False}},
            })
            with patch.object(
                mod,
                "_manage_agent_hooks",
                new=AsyncMock(return_value=fake_response_json),
            ) as mock_legacy:
                result = await agents(
                    ctx=_make_ctx(),
                    op="PATCH", target="hooks",
                    enabled=False,
                )
                return mock_legacy.call_args, result

        call_args, result = asyncio.run(go())
        parsed = json.loads(result)
        self.assertEqual(parsed["method"], "PATCH")
        self.assertEqual(parsed["definer"], "MODIFY")
        self.assertTrue(parsed["ok"])
        request = call_args.args[0]
        self.assertEqual(request.operation, "update_config")
        self.assertEqual(request.enabled, False)

    def test_patch_hooks_with_explicit_set_variable(self):
        async def go():
            from iterm_mcpy.tools import agents as mod
            fake_response_json = json.dumps({
                "operation": "set_variable",
                "success": True,
                "data": {"variable_name": "hooks_enabled"},
            })
            with patch.object(
                mod,
                "_manage_agent_hooks",
                new=AsyncMock(return_value=fake_response_json),
            ) as mock_legacy:
                result = await agents(
                    ctx=_make_ctx(),
                    op="PATCH", target="hooks",
                    hooks_op="set_variable",
                    session_id="sid", variable_name="hooks_enabled",
                    variable_value="true",
                )
                return mock_legacy.call_args, result

        call_args, result = asyncio.run(go())
        parsed = json.loads(result)
        self.assertTrue(parsed["ok"])
        request = call_args.args[0]
        self.assertEqual(request.operation, "set_variable")
        self.assertEqual(request.session_id, "sid")
        self.assertEqual(request.variable_name, "hooks_enabled")


class TestTriggerHook(unittest.TestCase):
    def test_trigger_path_change_delegates(self):
        async def go():
            from iterm_mcpy.tools import agents as mod
            fake_response_json = json.dumps({
                "operation": "trigger_path_change",
                "success": True,
                "data": {"actions_taken": []},
            })
            with patch.object(
                mod,
                "_manage_agent_hooks",
                new=AsyncMock(return_value=fake_response_json),
            ) as mock_legacy:
                result = await agents(
                    ctx=_make_ctx(),
                    op="POST", definer="TRIGGER", target="hooks",
                    hooks_op="trigger_path_change",
                    session_id="sid", new_path="/new", agent="alice",
                )
                return mock_legacy.call_args, result

        call_args, result = asyncio.run(go())
        parsed = json.loads(result)
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "TRIGGER")
        self.assertTrue(parsed["ok"])
        request = call_args.args[0]
        self.assertEqual(request.operation, "trigger_path_change")
        self.assertEqual(request.session_id, "sid")
        self.assertEqual(request.new_path, "/new")
        # The v2 'agent' param maps to the legacy 'agent_name' field.
        self.assertEqual(request.agent_name, "alice")

    def test_trigger_hook_missing_op_returns_err(self):
        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(),
            op="POST", definer="TRIGGER", target="hooks",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("hooks_op", parsed["error"]["message"].lower())


# ========================================================================= #
# GET /agents/{name}/locks                                                  #
# ========================================================================= #


class TestGetLocks(unittest.TestCase):
    def test_get_locks_delegates_to_lock_manager(self):
        from datetime import datetime

        # Fake lock_info with a plausible locked_at and pending_requests.
        fake_lock = MagicMock()
        fake_lock.locked_at = datetime(2025, 1, 1, 12, 0, 0)
        fake_lock.pending_requests = {"bob"}

        lock_manager = MagicMock()
        lock_manager.get_locks_by_agent.return_value = ["sid-1", "sid-2"]
        lock_manager.get_lock_info.return_value = fake_lock

        fake_session = MagicMock()
        fake_session.name = "pane"
        terminal = MagicMock()
        terminal.get_session_by_id = AsyncMock(return_value=fake_session)

        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(terminal=terminal, lock_manager=lock_manager),
            op="GET", target="locks", agent="alice",
        )))
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["agent"], "alice")
        self.assertEqual(parsed["data"]["lock_count"], 2)
        self.assertEqual(len(parsed["data"]["locks"]), 2)
        # Lock records include session_id, session_name, locked_at,
        # pending_requests.
        first = parsed["data"]["locks"][0]
        self.assertEqual(first["session_id"], "sid-1")
        self.assertEqual(first["session_name"], "pane")
        self.assertEqual(first["pending_requests"], ["bob"])
        lock_manager.get_locks_by_agent.assert_called_once_with("alice")

    def test_get_locks_missing_agent_returns_err(self):
        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(lock_manager=MagicMock()),
            op="GET", target="locks",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("agent", parsed["error"]["message"].lower())

    def test_get_locks_no_lock_manager_returns_err(self):
        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(),  # lock_manager=None
            op="GET", target="locks", agent="alice",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("tag_lock_manager", parsed["error"]["message"])


# ========================================================================= #
# DELETE /agents/{name}                                                     #
# ========================================================================= #


class TestDeleteAgent(unittest.TestCase):
    def test_delete_delegates_to_registry(self):
        agent_registry = MagicMock()
        agent_registry.remove_agent.return_value = True

        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(agent_registry=agent_registry),
            op="delete", agent_name="alice",
        )))
        self.assertEqual(parsed["method"], "DELETE")
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["removed"])
        agent_registry.remove_agent.assert_called_once_with("alice")

    def test_delete_agent_not_found(self):
        agent_registry = MagicMock()
        agent_registry.remove_agent.return_value = False

        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(agent_registry=agent_registry),
            op="DELETE", agent_name="missing",
        )))
        self.assertTrue(parsed["ok"])
        self.assertFalse(parsed["data"]["removed"])

    def test_delete_missing_agent_name_returns_err(self):
        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(),
            op="delete",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("agent_name", parsed["error"]["message"].lower())


# ========================================================================= #
# Unsupported definers / combinations                                       #
# ========================================================================= #


class TestUnsupportedCombinations(unittest.TestCase):
    def test_post_invoke_not_implemented(self):
        parsed = json.loads(asyncio.run(
            agents(ctx=_make_ctx(), op="POST", definer="INVOKE")
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("not implemented", parsed["error"]["message"].lower())

    def test_patch_unknown_target_not_implemented(self):
        parsed = json.loads(asyncio.run(agents(
            ctx=_make_ctx(),
            op="PATCH", target="bogus",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not implemented", parsed["error"]["message"].lower())


if __name__ == "__main__":
    unittest.main()
