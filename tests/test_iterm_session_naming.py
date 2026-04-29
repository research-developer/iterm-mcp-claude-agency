"""Regression coverage for fb-20260424-157473f7 item #2.

Submitting `sessions=[{"name": "x"}]` on `op=create` used to return a
session named " " instead of "x". Root cause: iterm2's `async_set_name`
propagates the new name asynchronously inside iTerm. `ItermSession.__init__`
seeds `_name` from the underlying `session.name`. When the create flow
later runs `terminal.get_session_by_id(...)`, that calls
`_refresh_sessions()`, which rebuilds every `ItermSession` wrapper from
scratch (no `name=` kwarg) — so the fresh wrapper reads whatever iterm2
currently has, which can still be the profile default if the rename has
not yet propagated. The fix pushes a retry+verify loop into
`ItermSession.set_name`, so set_name does not return until iTerm itself
agrees the name has changed.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from core.session import ItermSession


def _fake_iterm2_session(initial_name: str = " ", apply_after: int = 1):
    """Build a fake iterm2.Session whose name updates only after `apply_after`
    calls to `async_set_name`. Defaults: name starts as a single space (matches
    the user-reported symptom) and the rename takes one retry to land.
    """
    state = {"name": initial_name, "calls": 0}

    async def async_set_name(new_name: str) -> None:
        state["calls"] += 1
        if state["calls"] >= apply_after:
            state["name"] = new_name

    fake = MagicMock(spec=["name", "async_set_name", "session_id"])
    fake.session_id = "fake-id"
    fake.async_set_name = AsyncMock(side_effect=async_set_name)
    type(fake).name = property(lambda self: state["name"])  # dynamic getter
    fake._state = state
    return fake


class TestSetNameRetriesUntilApplied(unittest.TestCase):
    def test_set_name_returns_only_after_iterm_agrees(self):
        """The race scenario: iterm2 needs >1 set_name call before it sticks.
        set_name must keep trying until session.name reports the new value."""
        fake = _fake_iterm2_session(initial_name=" ", apply_after=2)
        sess = ItermSession(session=fake, max_lines=50)

        asyncio.run(sess.set_name("alpha"))

        # Wrapper-cached name reflects the request.
        self.assertEqual(sess.name, "alpha")
        # iterm2 was called more than once (retry happened).
        self.assertGreaterEqual(fake.async_set_name.await_count, 2)
        # Underlying iterm2 session now agrees.
        self.assertEqual(fake.name, "alpha")

    def test_fresh_wrapper_after_set_name_reads_correct_name(self):
        """After set_name returns, recreating the wrapper from scratch (the
        path _refresh_sessions takes) must pick up the new name."""
        fake = _fake_iterm2_session(initial_name=" ", apply_after=2)
        original = ItermSession(session=fake, max_lines=50)
        asyncio.run(original.set_name("alpha"))

        rebuilt = ItermSession(session=fake, max_lines=50)
        # No `name=` kwarg passed, so _name = None or session.name.
        # If set_name had returned too early, this would still be " ".
        self.assertEqual(rebuilt.name, "alpha")

    def test_set_name_logs_warning_and_returns_when_iterm_never_applies(self):
        """If iterm2 silently drops every set_name call, set_name must log a
        warning and return rather than hang the caller."""
        fake = _fake_iterm2_session(initial_name=" ", apply_after=999)
        sess = ItermSession(session=fake, max_lines=50)
        sess.logger = MagicMock()

        asyncio.run(sess.set_name("alpha"))

        # Wrapper still reflects the requested name (best-effort).
        self.assertEqual(sess.name, "alpha")
        # iterm2 was retried the expected number of times.
        self.assertGreaterEqual(fake.async_set_name.await_count, 3)
        # No exception raised.
        # (We don't assert on a specific logger method — set_name uses the
        # session logger if attached; the contract is "do not raise".)

    def test_set_name_no_op_when_iterm_already_agrees(self):
        """If the iterm2 session already has the requested name, retry must
        not fire — keeps the fast path fast."""
        fake = _fake_iterm2_session(initial_name="alpha", apply_after=1)
        sess = ItermSession(session=fake, max_lines=50)

        asyncio.run(sess.set_name("alpha"))

        # Either zero calls (early return) or exactly one (single set+verify).
        # Both are acceptable; we just want to ensure no retry loop ran.
        self.assertLessEqual(fake.async_set_name.await_count, 1)


if __name__ == "__main__":
    unittest.main()
