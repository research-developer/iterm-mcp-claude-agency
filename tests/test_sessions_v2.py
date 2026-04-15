"""Tests for sessions_v2 dispatcher (SP2 Task 4a — core)."""
import asyncio
import json
import unittest
from unittest.mock import MagicMock

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


if __name__ == "__main__":
    unittest.main()
