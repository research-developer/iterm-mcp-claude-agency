"""Tests for sessions_v2 dispatcher (SP2 Tasks 4a + 4b)."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from iterm_mcpy.tools.sessions_v2 import SessionsDispatcher, sessions_v2


def _make_ctx(terminal=None, agent_registry=None, lock_manager=None, logger=None, **extra):
    """Build a fake MCP Context with the lifespan context filled in."""
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

    def test_read_missing_target_info_returns_err(self):
        async def go():
            return await sessions_v2(
                ctx=_make_ctx(),
                op="GET",
                target="output",
                # no session_id/agent/name/team/targets
            )
        parsed = json.loads(asyncio.run(go()))
        self.assertFalse(parsed["ok"])
        self.assertIn("requires", parsed["error"].lower())


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


if __name__ == "__main__":
    unittest.main()
