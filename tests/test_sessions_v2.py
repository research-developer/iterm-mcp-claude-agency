"""Tests for sessions_v2 dispatcher (SP2 Tasks 4a + 4b + 4c + 4d + 4e)."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from iterm_mcpy.tools.sessions_v2 import SessionsDispatcher, sessions_v2


def _make_ctx(terminal=None, agent_registry=None, lock_manager=None, logger=None, **extra):
    """Build a fake MCP Context with the lifespan context filled in.

    `**extra` keys (e.g., `role_manager`, `notification_manager`,
    `focus_cooldown`) go straight into `lifespan_context` so tests can inject
    whichever managers they need.
    """
    ctx = MagicMock()
    ctx.request_context.lifespan_context = {
        "terminal": terminal or MagicMock(),
        "agent_registry": agent_registry or MagicMock(),
        "tag_lock_manager": lock_manager,
        "logger": logger or MagicMock(),
        **extra,
    }
    return ctx


class TestOptions(unittest.TestCase):
    def test_options_returns_schema(self):
        async def go():
            return await sessions_v2(ctx=_make_ctx(), op="OPTIONS")
        result = asyncio.run(go())
        parsed = json.loads(result)
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["collection"], "sessions")
        self.assertIn("GET", parsed["data"]["methods"])
        self.assertIn("POST", parsed["data"]["methods"])
        self.assertIn("output", parsed["data"]["sub_resources"])


class TestVerbResolution(unittest.TestCase):
    def test_schema_verb_works(self):
        async def go():
            return await sessions_v2(ctx=_make_ctx(), op="schema")
        parsed = json.loads(asyncio.run(go()))
        self.assertEqual(parsed["method"], "OPTIONS")
        self.assertTrue(parsed["ok"])


class TestHead(unittest.TestCase):
    def test_head_returns_compact_envelope(self):
        # We won't mock out the full terminal, but with no sessions we should
        # still get back an ok envelope with method=HEAD and empty data.
        terminal = MagicMock()
        terminal.sessions = {}
        ctx = _make_ctx(terminal=terminal)
        parsed = json.loads(asyncio.run(sessions_v2(ctx=ctx, op="HEAD")))
        self.assertEqual(parsed["method"], "HEAD")
        self.assertTrue(parsed["ok"])


class TestUnknownOp(unittest.TestCase):
    def test_bad_verb_returns_err_envelope(self):
        parsed = json.loads(asyncio.run(sessions_v2(ctx=_make_ctx(), op="frobnicate")))
        self.assertFalse(parsed["ok"])
        self.assertIn("Unknown op", parsed["error"])


class TestWrongDefiner(unittest.TestCase):
    def test_post_replace_rejected(self):
        parsed = json.loads(asyncio.run(
            sessions_v2(ctx=_make_ctx(), op="POST", definer="REPLACE")
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("not in POST family", parsed["error"])


class TestReadOutput(unittest.TestCase):
    def test_read_delegates_to_execute_read_request(self):
        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(mod, "execute_read_request", new=AsyncMock()) as mock_read:
                from core.models import ReadSessionsResponse, SessionOutput
                mock_read.return_value = ReadSessionsResponse(
                    outputs=[SessionOutput(
                        session_id="abc",
                        name="s1",
                        content="hello",
                        line_count=1,
                    )],
                    total_sessions=1,
                )
                result = await sessions_v2(
                    ctx=_make_ctx(),
                    op="GET",
                    target="output",
                    session_id="abc",
                    max_lines=100,
                )
                return mock_read.call_count, mock_read.call_args, result

        count, call_args, result = asyncio.run(go())
        self.assertEqual(count, 1)
        # Verify the request that was passed in.
        request = call_args.args[0]
        self.assertEqual(len(request.targets), 1)
        self.assertEqual(request.targets[0].session_id, "abc")
        self.assertEqual(request.targets[0].max_lines, 100)
        # Verify the envelope that came out.
        parsed = json.loads(result)
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        self.assertIn("outputs", parsed["data"])

    def test_read_with_explicit_targets_list(self):
        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(mod, "execute_read_request", new=AsyncMock()) as mock_read:
                from core.models import ReadSessionsResponse
                mock_read.return_value = ReadSessionsResponse(
                    outputs=[], total_sessions=0
                )
                result = await sessions_v2(
                    ctx=_make_ctx(),
                    op="GET",
                    target="output",
                    targets=[{"agent": "alice"}, {"agent": "bob"}],
                )
                return mock_read.call_args, result

        call_args, result = asyncio.run(go())
        request = call_args.args[0]
        self.assertEqual(len(request.targets), 2)
        self.assertEqual(request.targets[0].agent, "alice")
        self.assertEqual(request.targets[1].agent, "bob")
        parsed = json.loads(result)
        self.assertTrue(parsed["ok"])

    def test_read_no_targets_falls_through_to_active_session(self):
        """No targets/session_id/agent/name passed → build a request with an
        empty targets list, which ReadSessionsRequest interprets as 'active
        session'. Don't pre-reject — let the request model's defaults apply.
        """
        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(mod, "execute_read_request", new=AsyncMock()) as mock_read:
                from core.models import ReadSessionsResponse
                mock_read.return_value = ReadSessionsResponse(outputs=[], total_sessions=0)
                result = await sessions_v2(ctx=_make_ctx(), op="GET", target="output")
                return mock_read.call_args, result

        call_args, result = asyncio.run(go())
        # Request built with empty targets — ReadSessionsRequest will resolve
        # to the active session per its docstring.
        request = call_args.args[0]
        self.assertEqual(list(request.targets), [])
        parsed = json.loads(result)
        self.assertTrue(parsed["ok"])


class TestWriteOutput(unittest.TestCase):
    def test_write_delegates_to_execute_write_request(self):
        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(mod, "execute_write_request", new=AsyncMock()) as mock_write:
                from core.models import WriteResult, WriteToSessionsResponse
                mock_write.return_value = WriteToSessionsResponse(
                    results=[WriteResult(
                        session_id="abc",
                        session_name="s1",
                        success=True,
                    )],
                    sent_count=1,
                    skipped_count=0,
                    error_count=0,
                )
                result = await sessions_v2(
                    ctx=_make_ctx(),
                    op="send",  # maps to POST+SEND
                    target="output",
                    content="echo hello",
                    session_id="abc",
                )
                return mock_write.call_count, mock_write.call_args, result

        count, call_args, result = asyncio.run(go())
        self.assertEqual(count, 1)
        # Verify the request shape that was passed in.
        request = call_args.args[0]
        self.assertEqual(len(request.messages), 1)
        self.assertEqual(request.messages[0].content, "echo hello")
        self.assertEqual(request.messages[0].targets[0].session_id, "abc")
        # Verify the envelope.
        parsed = json.loads(result)
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "SEND")
        self.assertTrue(parsed["ok"])

    def test_write_passes_execute_false_through(self):
        """Booleans False must not get stripped from params."""
        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(mod, "execute_write_request", new=AsyncMock()) as mock_write:
                from core.models import WriteToSessionsResponse
                mock_write.return_value = WriteToSessionsResponse(
                    results=[], sent_count=0, skipped_count=0, error_count=0,
                )
                await sessions_v2(
                    ctx=_make_ctx(),
                    op="POST",
                    definer="SEND",
                    target="output",
                    content="vim",
                    session_id="abc",
                    execute=False,
                )
                return mock_write.call_args

        call_args = asyncio.run(go())
        request = call_args.args[0]
        self.assertEqual(request.messages[0].execute, False)

    def test_write_with_structured_messages(self):
        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(mod, "execute_write_request", new=AsyncMock()) as mock_write:
                from core.models import WriteToSessionsResponse
                mock_write.return_value = WriteToSessionsResponse(
                    results=[], sent_count=0, skipped_count=0, error_count=0,
                )
                result = await sessions_v2(
                    ctx=_make_ctx(),
                    op="POST",
                    definer="SEND",
                    target="output",
                    messages=[
                        {
                            "content": "echo a",
                            "targets": [{"agent": "alice"}],
                        },
                        {
                            "content": "echo b",
                            "targets": [{"agent": "bob"}],
                        },
                    ],
                )
                return mock_write.call_args, result

        call_args, result = asyncio.run(go())
        request = call_args.args[0]
        self.assertEqual(len(request.messages), 2)
        self.assertEqual(request.messages[0].content, "echo a")
        self.assertEqual(request.messages[0].targets[0].agent, "alice")
        self.assertEqual(request.messages[1].content, "echo b")
        parsed = json.loads(result)
        self.assertTrue(parsed["ok"])

    def test_write_missing_content_and_targets_returns_err(self):
        async def go():
            return await sessions_v2(
                ctx=_make_ctx(),
                op="POST",
                definer="SEND",
                target="output",
                # no content, no messages
            )
        parsed = json.loads(asyncio.run(go()))
        self.assertFalse(parsed["ok"])

    def test_write_content_without_target_returns_err(self):
        async def go():
            return await sessions_v2(
                ctx=_make_ctx(),
                op="POST",
                definer="SEND",
                target="output",
                content="echo hi",
                # no session_id/agent/name/team
            )
        parsed = json.loads(asyncio.run(go()))
        self.assertFalse(parsed["ok"])
        self.assertIn("requires", parsed["error"].lower())


class TestPostWithUnsupportedDefiner(unittest.TestCase):
    def test_post_invoke_not_yet_implemented(self):
        parsed = json.loads(asyncio.run(
            sessions_v2(ctx=_make_ctx(), op="POST", definer="INVOKE")
        ))
        self.assertFalse(parsed["ok"])
        # Dispatcher converts NotImplementedError into a generic err envelope.
        self.assertIn("not implemented", parsed["error"].lower())

    def test_post_send_without_target_not_yet_implemented(self):
        # SEND without target='output' is reserved for future sub-resources
        # (e.g. POST+SEND on cascade). Should be NotImplemented for now.
        parsed = json.loads(asyncio.run(
            sessions_v2(ctx=_make_ctx(), op="POST", definer="SEND")
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("not implemented", parsed["error"].lower())


class TestOptionsAdvertisesOutputAndSend(unittest.TestCase):
    def test_options_lists_send_definer(self):
        async def go():
            return await sessions_v2(ctx=_make_ctx(), op="OPTIONS")
        parsed = json.loads(asyncio.run(go()))
        post = parsed["data"]["methods"]["POST"]
        self.assertIn("SEND", post["definers"])
        # GET method should advertise the new params.
        get_params = parsed["data"]["methods"]["GET"]["params"]
        self.assertIn("target?", get_params)


class TestSendKeys(unittest.TestCase):
    def test_send_control_char_delegates_to_session(self):
        mock_session = MagicMock()
        mock_session.id = "sid"
        mock_session.name = "s1"
        mock_session.send_control_character = AsyncMock()

        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(mod, "resolve_session", new=AsyncMock(return_value=[mock_session])):
                return await sessions_v2(
                    ctx=_make_ctx(),
                    op="send",
                    target="keys",
                    control_char="C",
                    session_id="sid",
                )

        result = asyncio.run(go())
        parsed = json.loads(result)
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "SEND")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 1)
        mock_session.send_control_character.assert_awaited_once_with("C")

    def test_send_special_key_delegates_to_session(self):
        mock_session = MagicMock()
        mock_session.id = "sid"
        mock_session.name = "s1"
        mock_session.send_special_key = AsyncMock()

        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(mod, "resolve_session", new=AsyncMock(return_value=[mock_session])):
                return await sessions_v2(
                    ctx=_make_ctx(),
                    op="POST",
                    definer="SEND",
                    target="keys",
                    key="enter",
                    session_id="sid",
                )

        parsed = json.loads(asyncio.run(go()))
        self.assertTrue(parsed["ok"])
        mock_session.send_special_key.assert_awaited_once_with("enter")

    def test_send_keys_both_control_and_key_rejected(self):
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(), op="send", target="keys",
            control_char="C", key="enter", session_id="sid",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("either", parsed["error"].lower())

    def test_send_keys_missing_both_rejected(self):
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(), op="send", target="keys", session_id="sid",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("requires", parsed["error"].lower())

    def test_send_keys_no_matching_session(self):
        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(mod, "resolve_session", new=AsyncMock(return_value=[])):
                return await sessions_v2(
                    ctx=_make_ctx(),
                    op="send",
                    target="keys",
                    key="enter",
                    session_id="nonexistent",
                )

        parsed = json.loads(asyncio.run(go()))
        self.assertFalse(parsed["ok"])
        self.assertIn("no matching session", parsed["error"].lower())


# ========================================================================= #
# Task 4d — PATCH/DELETE on tags, roles, locks, and active session.         #
# ========================================================================= #


class TestPatchDefinerValidation(unittest.TestCase):
    """Regression: each PATCH target must only accept compatible definers."""

    def test_patch_append_on_roles_rejected(self):
        # APPEND is tags-only; roles should reject it rather than silently
        # execute as MODIFY.
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(role_manager=MagicMock()),
            op="append", target="roles", session_id="sid", role="builder",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("APPEND", parsed["error"])

    def test_patch_append_on_locks_rejected(self):
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(lock_manager=MagicMock()),
            op="append", target="locks",
            session_id="sid", agent="alice", action="lock",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("APPEND", parsed["error"])

    def test_patch_append_on_session_rejected(self):
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(),
            op="append", session_id="sid",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("APPEND", parsed["error"])

    def test_patch_append_on_tags_accepted(self):
        # Sanity: APPEND *is* valid on tags.
        lock_manager = MagicMock()
        lock_manager.set_tags.return_value = ["x"]
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(lock_manager=lock_manager),
            op="append", target="tags", session_id="sid", tags=["x"],
        )))
        self.assertTrue(parsed["ok"])


class TestPatchTags(unittest.TestCase):
    def test_patch_tags_replaces(self):
        lock_manager = MagicMock()
        lock_manager.set_tags.return_value = ["x", "y"]
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(lock_manager=lock_manager),
            op="update", target="tags", session_id="sid", tags=["x", "y"],
        )))
        self.assertEqual(parsed["method"], "PATCH")
        self.assertEqual(parsed["definer"], "MODIFY")
        self.assertTrue(parsed["ok"])
        lock_manager.set_tags.assert_called_once_with("sid", ["x", "y"], append=False)

    def test_patch_tags_append(self):
        lock_manager = MagicMock()
        lock_manager.set_tags.return_value = ["a", "b", "x"]
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(lock_manager=lock_manager),
            op="append", target="tags", session_id="sid", tags=["x"],
        )))
        self.assertEqual(parsed["definer"], "APPEND")
        self.assertTrue(parsed["ok"])
        lock_manager.set_tags.assert_called_once_with("sid", ["x"], append=True)

    def test_patch_tags_missing_session_id(self):
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(lock_manager=MagicMock()),
            op="update", target="tags", tags=["x"],
        )))
        self.assertFalse(parsed["ok"])

    def test_patch_tags_missing_tags(self):
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(lock_manager=MagicMock()),
            op="update", target="tags", session_id="sid",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("tags", parsed["error"].lower())


class TestPatchActive(unittest.TestCase):
    def test_focus_session(self):
        terminal = MagicMock()
        terminal.focus_session = AsyncMock()
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(terminal=terminal),
            op="update", target="active", session_id="sid", focus=True,
        )))
        self.assertEqual(parsed["method"], "PATCH")
        self.assertTrue(parsed["ok"])
        terminal.focus_session.assert_awaited_once_with("sid")

    def test_focus_without_flag_not_yet_implemented(self):
        # Only focus=true is supported in 4d. Plain PATCH on active session
        # without focus=true should surface NotImplemented.
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(),
            op="update", target="active", session_id="sid",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("not implemented", parsed["error"].lower())


class TestPatchRoles(unittest.TestCase):
    def test_assign_role(self):
        from core.models import SessionRole
        role_manager = MagicMock()
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(role_manager=role_manager),
            op="assign", target="roles", session_id="sid", role="builder",
        )))
        self.assertEqual(parsed["method"], "PATCH")
        self.assertTrue(parsed["ok"])
        # Role string coerced to the SessionRole enum before calling the manager.
        role_manager.assign_role.assert_called_once_with(
            "sid", SessionRole.BUILDER, assigned_by=None
        )

    def test_assign_role_missing_role(self):
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(role_manager=MagicMock()),
            op="assign", target="roles", session_id="sid",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("role", parsed["error"].lower())

    def test_assign_role_unknown_value(self):
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(role_manager=MagicMock()),
            op="assign", target="roles", session_id="sid", role="nonsense",
        )))
        self.assertFalse(parsed["ok"])


class TestDeleteRole(unittest.TestCase):
    def test_delete_role(self):
        role_manager = MagicMock()
        role_manager.remove_role.return_value = True
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(role_manager=role_manager),
            op="delete", target="roles", session_id="sid",
        )))
        self.assertEqual(parsed["method"], "DELETE")
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["removed"])
        # core/roles.py RoleManager.remove_role takes only session_id.
        role_manager.remove_role.assert_called_once_with("sid")


class TestPatchLocks(unittest.TestCase):
    def test_lock_session(self):
        lock_manager = MagicMock()
        lock_manager.lock_session.return_value = (True, "agent1")
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(lock_manager=lock_manager),
            op="update", target="locks", session_id="sid", agent="agent1", action="lock",
        )))
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["acquired"])
        self.assertEqual(parsed["data"]["owner"], "agent1")

    def test_lock_session_default_action_is_lock(self):
        # When `action` is omitted, it defaults to "lock".
        lock_manager = MagicMock()
        lock_manager.lock_session.return_value = (True, "agent1")
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(lock_manager=lock_manager),
            op="update", target="locks", session_id="sid", agent="agent1",
        )))
        self.assertTrue(parsed["ok"])
        lock_manager.lock_session.assert_called_once_with("sid", "agent1")

    def test_request_access(self):
        lock_manager = MagicMock()
        lock_manager.check_permission.return_value = (True, "agent1")
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(lock_manager=lock_manager),
            op="update", target="locks", session_id="sid", agent="agent1", action="request_access",
        )))
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["allowed"])

    def test_patch_locks_missing_agent(self):
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(lock_manager=MagicMock()),
            op="update", target="locks", session_id="sid",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("agent", parsed["error"].lower())

    def test_patch_locks_bad_action(self):
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(lock_manager=MagicMock()),
            op="update", target="locks", session_id="sid", agent="a", action="bogus",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("unknown action", parsed["error"].lower())


class TestDeleteLock(unittest.TestCase):
    def test_unlock(self):
        lock_manager = MagicMock()
        lock_manager.unlock_session.return_value = True
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(lock_manager=lock_manager),
            op="unlock", target="locks", session_id="sid", agent="agent1",
        )))
        self.assertEqual(parsed["method"], "DELETE")
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["unlocked"])

    def test_delete_locks_missing_agent(self):
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(lock_manager=MagicMock()),
            op="delete", target="locks", session_id="sid",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("agent", parsed["error"].lower())


class TestOptionsAdvertisesPatchAndDelete(unittest.TestCase):
    def test_options_lists_patch_definers_and_delete(self):
        parsed = json.loads(asyncio.run(sessions_v2(ctx=_make_ctx(), op="OPTIONS")))
        methods = parsed["data"]["methods"]
        self.assertIn("PATCH", methods)
        self.assertIn("MODIFY", methods["PATCH"]["definers"])
        self.assertIn("APPEND", methods["PATCH"]["definers"])
        self.assertIn("DELETE", methods)
        # Sub-resources should include roles/locks/tags/active.
        subs = parsed["data"]["sub_resources"]
        for name in ("tags", "roles", "locks", "active"):
            self.assertIn(name, subs)


# ========================================================================= #
# Task 4e — status + splits + monitoring + full appearance modify.           #
# ========================================================================= #


class TestGetStatus(unittest.TestCase):
    def test_get_status_returns_processing_state(self):
        session = MagicMock()
        session.id = "sid"
        session.name = "s1"
        session.persistent_id = "pid-1"
        session.is_processing = True
        session.is_monitoring = False
        session.is_suspended = False

        agent_registry = MagicMock()
        agent_registry.get_agent_by_session.return_value = None
        agent_registry.active_session = None

        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(mod, "resolve_session", new=AsyncMock(return_value=[session])):
                return await sessions_v2(
                    ctx=_make_ctx(agent_registry=agent_registry),
                    op="GET", target="status", session_id="sid",
                )

        parsed = json.loads(asyncio.run(go()))
        self.assertEqual(parsed["method"], "GET")
        self.assertTrue(parsed["ok"])
        # data is a list of status dicts.
        self.assertEqual(len(parsed["data"]), 1)
        status = parsed["data"][0]
        self.assertEqual(status["session_id"], "sid")
        self.assertTrue(status["is_processing"])
        self.assertFalse(status["is_monitoring"])

    def test_get_status_no_target_info(self):
        parsed = json.loads(asyncio.run(
            sessions_v2(ctx=_make_ctx(), op="GET", target="status")
        ))
        self.assertFalse(parsed["ok"])
        self.assertIn("requires", parsed["error"].lower())

    def test_get_status_no_match(self):
        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(mod, "resolve_session", new=AsyncMock(return_value=[])):
                return await sessions_v2(
                    ctx=_make_ctx(),
                    op="GET", target="status", session_id="missing",
                )

        parsed = json.loads(asyncio.run(go()))
        self.assertFalse(parsed["ok"])
        self.assertIn("no matching session", parsed["error"].lower())


class TestStartMonitoring(unittest.TestCase):
    def test_start_monitoring_delegates(self):
        session = MagicMock()
        session.id = "sid"
        session.name = "s1"
        session.is_monitoring = False

        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(mod, "resolve_session", new=AsyncMock(return_value=[session])):
                with patch.object(
                    mod, "_start_monitoring_core", new=AsyncMock(return_value=True)
                ) as mock_start:
                    result = await sessions_v2(
                        ctx=_make_ctx(event_bus=MagicMock()),
                        op="start", target="monitoring", session_id="sid",
                    )
                    return mock_start.call_count, mock_start.call_args, result

        count, call_args, result = asyncio.run(go())
        self.assertEqual(count, 1)
        parsed = json.loads(result)
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "TRIGGER")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["count"], 1)
        self.assertTrue(parsed["data"]["monitoring"][0]["started"])
        # Passed the session, event_bus, logger positionally.
        self.assertIs(call_args.args[0], session)

    def test_start_monitoring_no_target_info(self):
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(event_bus=MagicMock()),
            op="start", target="monitoring",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("requires", parsed["error"].lower())

    def test_start_monitoring_no_match(self):
        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(mod, "resolve_session", new=AsyncMock(return_value=[])):
                return await sessions_v2(
                    ctx=_make_ctx(event_bus=MagicMock()),
                    op="start", target="monitoring", session_id="missing",
                )

        parsed = json.loads(asyncio.run(go()))
        self.assertFalse(parsed["ok"])
        self.assertIn("no matching session", parsed["error"].lower())


class TestStopMonitoring(unittest.TestCase):
    def test_stop_monitoring_delegates(self):
        session = MagicMock()
        session.id = "sid"
        session.name = "s1"

        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(mod, "resolve_session", new=AsyncMock(return_value=[session])):
                with patch.object(
                    mod, "_stop_monitoring_core", new=AsyncMock(return_value=True)
                ) as mock_stop:
                    result = await sessions_v2(
                        ctx=_make_ctx(),
                        op="stop", target="monitoring", session_id="sid",
                    )
                    return mock_stop.call_count, result

        count, result = asyncio.run(go())
        self.assertEqual(count, 1)
        parsed = json.loads(result)
        self.assertEqual(parsed["method"], "DELETE")
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["monitoring"][0]["stopped"])

    def test_stop_monitoring_no_target_info(self):
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(),
            op="stop", target="monitoring",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("requires", parsed["error"].lower())


class TestCreateSplit(unittest.TestCase):
    def test_create_split_delegates_to_core(self):
        from core.models import SplitSessionResponse

        fake_response = SplitSessionResponse(
            session_id="new-sid",
            name="split-pane",
            persistent_id="pid-2",
            source_session_id="src",
            direction="below",
        )

        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(
                mod, "_split_session_core", new=AsyncMock(return_value=fake_response)
            ) as mock_split:
                ctx = _make_ctx(role_manager=MagicMock())
                result = await sessions_v2(
                    ctx=ctx,
                    op="POST", definer="CREATE",
                    target="splits",
                    session_id="src", direction="below",
                )
                return mock_split.call_args, result

        call_args, result = asyncio.run(go())
        parsed = json.loads(result)
        self.assertEqual(parsed["method"], "POST")
        self.assertEqual(parsed["definer"], "CREATE")
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["data"]["session_id"], "new-sid")
        # Verify SplitSessionRequest was built with session_id → target.session_id.
        split_request = call_args.args[0]
        self.assertEqual(split_request.target.session_id, "src")
        self.assertEqual(split_request.direction, "below")

    def test_create_split_missing_session_id(self):
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(role_manager=MagicMock()),
            op="POST", definer="CREATE", target="splits", direction="below",
        )))
        self.assertFalse(parsed["ok"])
        self.assertIn("session_id", parsed["error"].lower())


class TestPatchAppearance(unittest.TestCase):
    def test_patch_appearance_delegates_to_modification_helper(self):
        from core.models import ModificationResult

        fake_result = ModificationResult(
            session_id="sid",
            session_name="s1",
            success=True,
            changes=["badge='Worker'"],
        )

        session = MagicMock()
        session.id = "sid"
        session.name = "s1"

        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(mod, "resolve_session", new=AsyncMock(return_value=[session])):
                from iterm_mcpy.tools import modifications
                with patch.object(
                    modifications,
                    "_apply_session_modification",
                    new=AsyncMock(return_value=fake_result),
                ) as mock_apply:
                    ctx = _make_ctx(focus_cooldown=MagicMock())
                    result = await sessions_v2(
                        ctx=ctx,
                        op="PATCH", definer="MODIFY",
                        target="appearance",
                        session_id="sid",
                        badge="Worker",
                    )
                    return mock_apply.call_count, mock_apply.call_args, result

        count, call_args, result = asyncio.run(go())
        self.assertEqual(count, 1)
        parsed = json.loads(result)
        self.assertEqual(parsed["method"], "PATCH")
        self.assertEqual(parsed["definer"], "MODIFY")
        self.assertTrue(parsed["ok"])
        self.assertTrue(parsed["data"]["success"])
        # The modification model should have `badge="Worker"` set.
        modification = call_args.args[1]
        self.assertEqual(modification.badge, "Worker")

    def test_patch_appearance_suspended_true_translates_to_suspend(self):
        from core.models import ModificationResult

        session = MagicMock()
        session.id = "sid"
        session.name = "s1"

        async def go():
            from iterm_mcpy.tools import sessions_v2 as mod
            with patch.object(mod, "resolve_session", new=AsyncMock(return_value=[session])):
                from iterm_mcpy.tools import modifications
                with patch.object(
                    modifications,
                    "_apply_session_modification",
                    new=AsyncMock(return_value=ModificationResult(
                        session_id="sid", session_name="s1", success=True,
                    )),
                ) as mock_apply:
                    await sessions_v2(
                        ctx=_make_ctx(focus_cooldown=None),
                        op="PATCH", definer="MODIFY",
                        target="appearance",
                        session_id="sid",
                        suspended=True,
                    )
                    return mock_apply.call_args

        call_args = asyncio.run(go())
        modification = call_args.args[1]
        self.assertTrue(modification.suspend)
        self.assertFalse(modification.resume)

    def test_patch_appearance_no_fields_returns_err(self):
        # Plain PATCH with no target and no modifications should still fail
        # with the Task 4d "not implemented" error so we don't no-op silently.
        parsed = json.loads(asyncio.run(sessions_v2(
            ctx=_make_ctx(),
            op="PATCH", definer="MODIFY",
            session_id="sid",
        )))
        self.assertFalse(parsed["ok"])


class TestOptionsAdvertises4e(unittest.TestCase):
    def test_options_lists_new_targets_and_verbs(self):
        parsed = json.loads(asyncio.run(sessions_v2(ctx=_make_ctx(), op="OPTIONS")))
        methods = parsed["data"]["methods"]
        # POST should advertise TRIGGER.
        self.assertIn("TRIGGER", methods["POST"]["definers"])
        # Sub-resources should include splits, monitoring, appearance.
        subs = parsed["data"]["sub_resources"]
        for name in ("splits", "monitoring", "appearance", "status"):
            self.assertIn(name, subs)


if __name__ == "__main__":
    unittest.main()
