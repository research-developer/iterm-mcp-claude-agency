"""Regression test for the ``query_feedback`` MCP tool (PR #113 review).

Background
----------
PR #113 fixed two bugs in ``iterm_mcpy.fastmcp_server.query_feedback``:

1. It was ``await``-ing ``FeedbackRegistry.query()``, which is a *synchronous*
   method (the await would raise ``TypeError`` at runtime).
2. It was forwarding the parameter as ``agent_id=`` even though the registry's
   signature uses ``agent_name=``.

A subsequent commit also renamed the public tool parameter from ``agent_id`` to
``agent_name`` to match the underlying registry kwarg.

This regression test pins both behaviours so the bug cannot silently return:
  * ``query()`` is invoked synchronously (no ``await``).
  * The forwarded kwarg is named ``agent_name`` (and never ``agent_id``).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add parent directory to path for imports (matches sibling test conventions).
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.feedback import (  # noqa: E402  (path mutated above)
    FeedbackCategory,
    FeedbackContext,
    FeedbackEntry,
    FeedbackStatus,
    FeedbackTriggerType,
)
from iterm_mcpy.tools.feedback import query_feedback  # noqa: E402


def _make_entry() -> FeedbackEntry:
    """Build a minimally-valid FeedbackEntry the production code can serialize."""
    return FeedbackEntry(
        agent_id="sess-x",
        agent_name="agent-x",
        session_id="sess-x",
        trigger_type=FeedbackTriggerType.MANUAL,
        context=FeedbackContext(
            git_commit="abc123",
            git_branch="main",
            project_path="/tmp",
        ),
        category=FeedbackCategory.BUG,
        title="Example",
        description="Example description",
    )


def _make_ctx(fake_registry: MagicMock) -> MagicMock:
    """Build a minimal Context whose lifespan_context exposes the fake registry."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context = {
        "feedback_registry": fake_registry,
        "logger": logging.getLogger("test_query_feedback"),
    }
    return ctx


def test_query_feedback_calls_registry_synchronously_with_agent_name():
    """query_feedback must call .query() synchronously and pass agent_name=.

    Driven via ``asyncio.run`` rather than pytest-asyncio so the test runs in
    the project's pytest config without an extra plugin.
    """
    entry = _make_entry()

    # IMPORTANT: a regular MagicMock — *not* AsyncMock. If the production code
    # accidentally re-introduces ``await fake_registry.query(...)``, this will
    # raise: ``TypeError: object MagicMock can't be used in 'await' expression``.
    fake_registry = MagicMock()
    fake_registry.query = MagicMock(return_value=[entry])

    ctx = _make_ctx(fake_registry)

    result = asyncio.run(
        query_feedback(
            ctx,
            status="pending",
            agent_name="agent-x",
            limit=5,
        )
    )

    # 1. Result is parseable JSON with exactly one entry.
    payload = json.loads(result)
    assert payload.get("count") == 1, f"unexpected payload: {payload!r}"
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["agent"] == "agent-x"

    # 2. .query() was called exactly once.
    assert fake_registry.query.call_count == 1, (
        f"expected exactly 1 call to registry.query, got "
        f"{fake_registry.query.call_count}"
    )

    # 3. Forwarded kwargs include agent_name="agent-x" and NOT agent_id.
    call = fake_registry.query.call_args
    assert "agent_name" in call.kwargs, (
        f"expected agent_name kwarg, got kwargs={call.kwargs!r}"
    )
    assert call.kwargs["agent_name"] == "agent-x"
    assert "agent_id" not in call.kwargs, (
        "regression: agent_id kwarg leaked back into FeedbackRegistry.query() "
        f"(kwargs={call.kwargs!r})"
    )

    # 4. The mock's return value must NOT be a coroutine. If the production
    #    code re-introduces ``await registry.query(...)`` against a sync method
    #    we'd never reach this assertion — the await on a MagicMock raises
    #    TypeError first — but pin the invariant explicitly for clarity.
    assert not inspect.iscoroutine(fake_registry.query.return_value), (
        "registry.query is a sync method; its return value must not be a coroutine"
    )
