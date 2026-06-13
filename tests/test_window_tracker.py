"""Unit tests for core/test_window_tracker.py.

These tests exercise the tag-matching and teardown logic using mock
iTerm2 objects — no live iTerm2 connection is required.

The safety-critical part is ``close_tagged_sessions``: it must ONLY close
sessions whose ``user.mcp_test_run`` variable matches the given tag (or,
with prefix_sweep, whose profile name starts with ``"MCP-TEST"``).  Any
session without a matching marker must be left untouched.

``ensure_test_profile`` tests point at a temp dir so the real iTerm2
DynamicProfiles directory is never written during testing.
"""

import asyncio
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

from core.test_window_tracker import (
    TAG_PREFIX,
    TEST_PROFILE_NAME,
    _parse_tag,
    close_tagged_sessions,
    ensure_test_profile,
    make_run_tag,
    mark_session,
)


# ---------------------------------------------------------------------------
# Helpers to build fake iTerm2 object trees
# ---------------------------------------------------------------------------

def _make_session(
    session_id: str = "s1",
    var_value=None,          # value returned by async_get_variable; None → raise
    profile_name: str = "Default",
    close_raises: bool = False,
) -> MagicMock:
    """Return a mock iterm2.Session.

    Args:
        session_id: Mock session_id attribute.
        var_value: If not None, returned by async_get_variable; if None,
            async_get_variable raises RuntimeError (simulates error).
        profile_name: Name returned by the mock profile.
        close_raises: If True, async_close raises RuntimeError.

    Returns:
        A MagicMock representing the session.
    """
    session = MagicMock()
    session.session_id = session_id

    if var_value is None:
        session.async_get_variable = AsyncMock(
            side_effect=RuntimeError("variable not found")
        )
    else:
        session.async_get_variable = AsyncMock(return_value=var_value)

    profile = MagicMock()
    profile.name = profile_name
    session.async_get_profile = AsyncMock(return_value=profile)

    if close_raises:
        session.async_close = AsyncMock(side_effect=RuntimeError("already closed"))
    else:
        session.async_close = AsyncMock()

    return session


def _make_app(*sessions) -> MagicMock:
    """Build a fake app with a single window→tab→sessions tree.

    Args:
        *sessions: Mock session objects.

    Returns:
        A MagicMock representing the iterm2 app.
    """
    tab = MagicMock()
    tab.sessions = list(sessions)

    window = MagicMock()
    window.tabs = [tab]

    app = MagicMock()
    app.windows = [window]
    return app


def _run(coro):
    """Run a coroutine in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# make_run_tag
# ---------------------------------------------------------------------------

class TestMakeRunTag(unittest.TestCase):
    """Tests for make_run_tag()."""

    def test_format_matches_pattern(self):
        """Tag must match MCP-TEST·<digits>-<hex8>."""
        tag = make_run_tag()
        parsed = _parse_tag(tag)
        self.assertIsNotNone(parsed, f"Tag did not parse: {tag!r}")

    def test_contains_current_pid(self):
        """Tag must embed the current process PID."""
        tag = make_run_tag()
        pid_str, _ = _parse_tag(tag)
        self.assertEqual(int(pid_str), os.getpid())

    def test_two_calls_differ(self):
        """Two calls must produce different tags (UUID portion differs)."""
        tag1 = make_run_tag()
        tag2 = make_run_tag()
        self.assertNotEqual(tag1, tag2)

    def test_starts_with_prefix(self):
        """Tag must start with TAG_PREFIX."""
        tag = make_run_tag()
        self.assertTrue(tag.startswith(TAG_PREFIX))

    def test_uuid_portion_is_hex(self):
        """UUID portion must be 8 lowercase hex chars."""
        tag = make_run_tag()
        _, uuid8 = _parse_tag(tag)
        self.assertRegex(uuid8, r"^[0-9a-f]{8}$")


# ---------------------------------------------------------------------------
# mark_session
# ---------------------------------------------------------------------------

class TestMarkSession(unittest.IsolatedAsyncioTestCase):
    """Tests for mark_session()."""

    async def test_sets_correct_variable(self):
        """mark_session must call async_set_variable with user.mcp_test_run."""
        session = MagicMock()
        session.session_id = "s1"
        session.async_set_variable = AsyncMock()

        tag = "MCP-TEST·99-deadbeef"
        await mark_session(session, tag)

        session.async_set_variable.assert_called_once_with("user.mcp_test_run", tag)

    async def test_tolerates_set_variable_error(self):
        """mark_session must not raise if async_set_variable fails."""
        session = MagicMock()
        session.session_id = "s1"
        session.async_set_variable = AsyncMock(
            side_effect=RuntimeError("permission denied")
        )

        # Should not raise
        await mark_session(session, "MCP-TEST·99-deadbeef")


# ---------------------------------------------------------------------------
# close_tagged_sessions — matching
# ---------------------------------------------------------------------------

class TestCloseTaggedSessionsMatching(unittest.IsolatedAsyncioTestCase):
    """Safety-critical tests: matching/skipping logic in close_tagged_sessions."""

    _TAG = "MCP-TEST·42-aabbccdd"
    _OTHER_TAG = "MCP-TEST·99-11223344"

    async def _run_close(self, app, *, prefix_sweep=False):
        conn = MagicMock()
        with patch(
            "core.test_window_tracker.iterm2.async_get_app",
            AsyncMock(return_value=app),
        ):
            return await close_tagged_sessions(conn, self._TAG, prefix_sweep=prefix_sweep)

    # --- sessions that SHOULD be closed ---

    async def test_closes_session_with_matching_tag(self):
        """Session whose user.mcp_test_run == tag must be closed."""
        s = _make_session("s1", var_value=self._TAG)
        app = _make_app(s)
        count = await self._run_close(app)
        self.assertEqual(count, 1)
        s.async_close.assert_called_once_with(force=True)

    async def test_closes_multiple_matching_sessions(self):
        """All matching sessions in one tab must be closed."""
        s1 = _make_session("s1", var_value=self._TAG)
        s2 = _make_session("s2", var_value=self._TAG)
        app = _make_app(s1, s2)
        count = await self._run_close(app)
        self.assertEqual(count, 2)
        s1.async_close.assert_called_once_with(force=True)
        s2.async_close.assert_called_once_with(force=True)

    async def test_prefix_sweep_closes_orphan_profile(self):
        """With prefix_sweep, session with MCP-TEST· profile name is closed."""
        # Session has no matching variable (empty string returned), but profile
        # name starts with TAG_PREFIX — classic orphan from crashed prior run.
        s = _make_session("s1", var_value="", profile_name="MCP-TEST·orphan")
        app = _make_app(s)
        count = await self._run_close(app, prefix_sweep=True)
        self.assertEqual(count, 1)
        s.async_close.assert_called_once_with(force=True)

    # --- sessions that must NOT be closed ---

    async def test_skips_session_with_different_tag(self):
        """Session whose variable contains a different tag must be skipped."""
        s = _make_session("s1", var_value=self._OTHER_TAG)
        app = _make_app(s)
        count = await self._run_close(app)
        self.assertEqual(count, 0)
        s.async_close.assert_not_called()

    async def test_skips_session_with_empty_tag(self):
        """Session whose variable is empty string must be skipped."""
        s = _make_session("s1", var_value="")
        app = _make_app(s)
        count = await self._run_close(app)
        self.assertEqual(count, 0)
        s.async_close.assert_not_called()

    async def test_skips_session_with_no_tag_variable(self):
        """Session where async_get_variable raises (variable absent) is skipped."""
        s = _make_session("s1", var_value=None)  # → raises RuntimeError
        app = _make_app(s)
        count = await self._run_close(app)
        self.assertEqual(count, 0)
        s.async_close.assert_not_called()

    async def test_skips_normal_profile_without_prefix_sweep(self):
        """Default profile with MCP Agent name must never be closed (no prefix_sweep)."""
        s = _make_session("s1", var_value=None, profile_name="MCP Agent")
        app = _make_app(s)
        count = await self._run_close(app, prefix_sweep=False)
        self.assertEqual(count, 0)
        s.async_close.assert_not_called()

    async def test_prefix_sweep_skips_non_test_profile(self):
        """With prefix_sweep, sessions with non-test profile are still skipped."""
        s = _make_session("s1", var_value="", profile_name="MCP Agent")
        app = _make_app(s)
        count = await self._run_close(app, prefix_sweep=True)
        self.assertEqual(count, 0)
        s.async_close.assert_not_called()

    async def test_prefix_sweep_skips_production_team_profile(self):
        """'MCP Team: Foo' profile must never be closed by prefix_sweep."""
        s = _make_session("s1", var_value="", profile_name="MCP Team: Foo")
        app = _make_app(s)
        count = await self._run_close(app, prefix_sweep=True)
        self.assertEqual(count, 0)
        s.async_close.assert_not_called()

    async def test_skips_session_whose_profile_read_errors(self):
        """If async_get_profile raises, session is skipped during prefix_sweep."""
        s = _make_session("s1", var_value="")
        s.async_get_profile = AsyncMock(side_effect=RuntimeError("profile error"))
        app = _make_app(s)
        count = await self._run_close(app, prefix_sweep=True)
        self.assertEqual(count, 0)
        s.async_close.assert_not_called()

    # --- mixed scenarios ---

    async def test_mixed_sessions_closes_only_matching(self):
        """In a mixed batch, only the matching session is closed."""
        s_match = _make_session("s1", var_value=self._TAG)
        s_other = _make_session("s2", var_value=self._OTHER_TAG)
        s_none = _make_session("s3", var_value=None)
        s_empty = _make_session("s4", var_value="")
        app = _make_app(s_match, s_other, s_none, s_empty)

        count = await self._run_close(app)
        self.assertEqual(count, 1)
        s_match.async_close.assert_called_once_with(force=True)
        s_other.async_close.assert_not_called()
        s_none.async_close.assert_not_called()
        s_empty.async_close.assert_not_called()


# ---------------------------------------------------------------------------
# close_tagged_sessions — robustness
# ---------------------------------------------------------------------------

class TestCloseTaggedSessionsRobustness(unittest.IsolatedAsyncioTestCase):
    """Robustness tests: errors during close must not abort other closes."""

    _TAG = "MCP-TEST·42-aabbccdd"

    async def _run_close(self, app, *, prefix_sweep=False):
        conn = MagicMock()
        with patch(
            "core.test_window_tracker.iterm2.async_get_app",
            AsyncMock(return_value=app),
        ):
            return await close_tagged_sessions(conn, self._TAG, prefix_sweep=prefix_sweep)

    async def test_continues_after_close_error(self):
        """If async_close raises for one session, others are still closed."""
        s1 = _make_session("s1", var_value=self._TAG, close_raises=True)
        s2 = _make_session("s2", var_value=self._TAG)
        app = _make_app(s1, s2)

        count = await self._run_close(app)
        # s1 raised, s2 succeeded
        self.assertEqual(count, 1)
        s2.async_close.assert_called_once_with(force=True)

    async def test_returns_zero_when_app_get_fails(self):
        """If async_get_app raises, return 0 without crashing."""
        conn = MagicMock()
        with patch(
            "core.test_window_tracker.iterm2.async_get_app",
            AsyncMock(side_effect=RuntimeError("no connection")),
        ):
            count = await close_tagged_sessions(conn, self._TAG)
        self.assertEqual(count, 0)

    async def test_empty_windows_returns_zero(self):
        """App with no windows returns 0."""
        app = MagicMock()
        app.windows = []
        conn = MagicMock()
        with patch(
            "core.test_window_tracker.iterm2.async_get_app",
            AsyncMock(return_value=app),
        ):
            count = await close_tagged_sessions(conn, self._TAG)
        self.assertEqual(count, 0)

    async def test_returns_correct_count(self):
        """Return value is the number of sessions actually closed."""
        s1 = _make_session("s1", var_value=self._TAG)
        s2 = _make_session("s2", var_value=self._TAG)
        s3 = _make_session("s3", var_value="other")
        app = _make_app(s1, s2, s3)

        count = await self._run_close(app)
        self.assertEqual(count, 2)


# ---------------------------------------------------------------------------
# ensure_test_profile
# ---------------------------------------------------------------------------

class TestEnsureTestProfile(unittest.TestCase):
    """Tests for ensure_test_profile() — filesystem writes go to a temp dir."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._profiles_dir = Path(self._tmp) / "DynamicProfiles"

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_writes_profile_file(self):
        """ensure_test_profile() must create the profile JSON file."""
        path = ensure_test_profile(profiles_dir=self._profiles_dir)
        self.assertTrue(path.exists(), "Profile file should exist after ensure_test_profile()")

    def test_profile_contains_mcp_test_name(self):
        """The written profile must contain the MCP-TEST profile name."""
        ensure_test_profile(profiles_dir=self._profiles_dir)
        files = list(self._profiles_dir.glob("*.json"))
        self.assertEqual(len(files), 1, "Exactly one JSON file should be written")
        data = json.loads(files[0].read_text())
        names = [p["Name"] for p in data.get("Profiles", [])]
        self.assertIn(TEST_PROFILE_NAME, names, f"Expected 'MCP-TEST' in profiles; got {names}")

    def test_idempotent_does_not_overwrite(self):
        """A second call must not overwrite the file (idempotent)."""
        ensure_test_profile(profiles_dir=self._profiles_dir)
        target = self._profiles_dir / "iterm-mcp-test-profile.json"
        first_mtime = target.stat().st_mtime

        ensure_test_profile(profiles_dir=self._profiles_dir)
        second_mtime = target.stat().st_mtime

        self.assertEqual(
            first_mtime,
            second_mtime,
            "ensure_test_profile() must not touch the file if it already exists",
        )

    def test_creates_directory_if_missing(self):
        """ensure_test_profile() must create the DynamicProfiles directory."""
        nested = Path(self._tmp) / "nested" / "DynamicProfiles"
        self.assertFalse(nested.exists())
        ensure_test_profile(profiles_dir=nested)
        self.assertTrue(nested.exists(), "Directory should be created")

    def test_tolerates_write_failure(self):
        """ensure_test_profile() must not raise even if the write fails."""
        # Point at a file (not a dir) so the mkdir will fail on the JSON write.
        bogus = Path("/dev/null/cannot_create_dir")
        # Should not raise — just logs a warning.
        ensure_test_profile(profiles_dir=bogus)

    def test_profile_has_distinctive_badge(self):
        """MCP-TEST profile must carry the 'MCP-TEST' badge for visual ID."""
        ensure_test_profile(profiles_dir=self._profiles_dir)
        files = list(self._profiles_dir.glob("*.json"))
        data = json.loads(files[0].read_text())
        test_profile = next(
            (p for p in data.get("Profiles", []) if p.get("Name") == TEST_PROFILE_NAME),
            None,
        )
        self.assertIsNotNone(test_profile, "MCP-TEST entry not found in Profiles list")
        self.assertEqual(
            test_profile.get("Badge Text"),
            "MCP-TEST",
            "Badge Text must be 'MCP-TEST' for visual identification",
        )


# ---------------------------------------------------------------------------
# prefix_sweep broadened to startswith("MCP-TEST")
# ---------------------------------------------------------------------------

class TestPrefixSweepBroadened(unittest.IsolatedAsyncioTestCase):
    """The prefix_sweep must match both 'MCP-TEST' and 'MCP-TEST·…' profiles
    but NEVER touch production profiles ('MCP Agent', 'MCP Team: …')."""

    _TAG = "MCP-TEST·42-aabbccdd"

    async def _run_close(self, app, *, prefix_sweep=False):
        conn = MagicMock()
        with patch(
            "core.test_window_tracker.iterm2.async_get_app",
            AsyncMock(return_value=app),
        ):
            return await close_tagged_sessions(conn, self._TAG, prefix_sweep=prefix_sweep)

    async def test_prefix_sweep_matches_stable_profile_name(self):
        """With prefix_sweep, a session with the stable 'MCP-TEST' profile is closed."""
        s = _make_session("s1", var_value="", profile_name="MCP-TEST")
        app = _make_app(s)
        count = await self._run_close(app, prefix_sweep=True)
        self.assertEqual(count, 1)
        s.async_close.assert_called_once_with(force=True)

    async def test_prefix_sweep_matches_tagged_variant_profile(self):
        """With prefix_sweep, 'MCP-TEST·orphan' profile is still closed."""
        s = _make_session("s1", var_value="", profile_name="MCP-TEST·crashed-abc")
        app = _make_app(s)
        count = await self._run_close(app, prefix_sweep=True)
        self.assertEqual(count, 1)
        s.async_close.assert_called_once_with(force=True)

    async def test_prefix_sweep_never_matches_mcp_agent(self):
        """'MCP Agent' must NEVER be closed by prefix_sweep."""
        s = _make_session("s1", var_value="", profile_name="MCP Agent")
        app = _make_app(s)
        count = await self._run_close(app, prefix_sweep=True)
        self.assertEqual(count, 0)
        s.async_close.assert_not_called()

    async def test_prefix_sweep_never_matches_mcp_team(self):
        """'MCP Team: Engineering' must NEVER be closed by prefix_sweep."""
        s = _make_session("s1", var_value="", profile_name="MCP Team: Engineering")
        app = _make_app(s)
        count = await self._run_close(app, prefix_sweep=True)
        self.assertEqual(count, 0)
        s.async_close.assert_not_called()

    async def test_prefix_sweep_never_matches_mcp_team_colon_only(self):
        """'MCP Team:' (no name) must NEVER be closed by prefix_sweep."""
        s = _make_session("s1", var_value="", profile_name="MCP Team:")
        app = _make_app(s)
        count = await self._run_close(app, prefix_sweep=True)
        self.assertEqual(count, 0)
        s.async_close.assert_not_called()

    async def test_prefix_sweep_never_matches_default(self):
        """'Default' profile must NEVER be closed by prefix_sweep."""
        s = _make_session("s1", var_value="", profile_name="Default")
        app = _make_app(s)
        count = await self._run_close(app, prefix_sweep=True)
        self.assertEqual(count, 0)
        s.async_close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
