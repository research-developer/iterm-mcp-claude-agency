"""Tests for the pure project resolver + SetUserVar escape builder."""
import asyncio
import base64
import unittest
from unittest import mock

from core import projects


class TestResolveProject(unittest.TestCase):
    def test_git_repo_returns_toplevel(self):
        with mock.patch("core.projects.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="/Users/me/repoA\n")
            self.assertEqual(projects.resolve_project("/Users/me/repoA/sub/dir"), "/Users/me/repoA")
        args = run.call_args[0][0]
        self.assertIn("rev-parse", args)
        self.assertIn("--show-toplevel", args)
        self.assertIn("/Users/me/repoA/sub/dir", args)

    def test_non_git_falls_back_to_cwd(self):
        with mock.patch("core.projects.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=128, stdout="")
            self.assertEqual(projects.resolve_project("/tmp/loose"), "/tmp/loose")

    def test_git_missing_or_error_falls_back_to_cwd(self):
        with mock.patch("core.projects.subprocess.run", side_effect=FileNotFoundError):
            self.assertEqual(projects.resolve_project("/tmp/loose"), "/tmp/loose")

    def test_blank_cwd_returns_none(self):
        self.assertIsNone(projects.resolve_project(""))
        self.assertIsNone(projects.resolve_project(None))

    def test_label_is_basename(self):
        self.assertEqual(projects.project_label("/Users/me/repoA"), "repoA")
        self.assertEqual(projects.project_label("/"), "/")
        self.assertIsNone(projects.project_label(None))


class TestSetUserVarEscape(unittest.TestCase):
    def test_escape_is_osc1337_with_base64_value(self):
        esc = projects.build_setuservar_escape("mcp_project", "/Users/me/repoA")
        b64 = base64.b64encode(b"/Users/me/repoA").decode()
        self.assertEqual(esc, f"\033]1337;SetUserVar=mcp_project={b64}\007")


class TestSessionProject(unittest.IsolatedAsyncioTestCase):
    def _conn(self):
        return mock.MagicMock()  # opaque connection handle

    async def test_returns_existing_declared_project_without_pinning(self):
        with mock.patch("core.projects.get_user_variable", new=mock.AsyncMock(return_value="/Users/me/repoB")), \
             mock.patch("core.projects.set_user_variable", new=mock.AsyncMock()) as setv:
            got = await projects.get_session_project(self._conn(), "sess-1")
        self.assertEqual(got, "/Users/me/repoB")
        setv.assert_not_awaited()  # already declared -> never overwrite (sticky)

    async def test_infers_and_pins_when_unset(self):
        conn = self._conn()
        with mock.patch("core.projects.get_user_variable", new=mock.AsyncMock(return_value="")), \
             mock.patch("core.projects.get_session_path", new=mock.AsyncMock(return_value="/Users/me/repoA/sub")), \
             mock.patch("core.projects.resolve_project", return_value="/Users/me/repoA"), \
             mock.patch("core.projects.set_user_variable", new=mock.AsyncMock(return_value=True)) as setv:
            got = await projects.get_session_project(conn, "sess-2")
        self.assertEqual(got, "/Users/me/repoA")
        setv.assert_awaited_once()
        self.assertEqual(setv.await_args.args[1:], ("sess-2", "mcp_project", "/Users/me/repoA"))

    async def test_returns_none_and_does_not_pin_when_no_cwd(self):
        with mock.patch("core.projects.get_user_variable", new=mock.AsyncMock(return_value="")), \
             mock.patch("core.projects.get_session_path", new=mock.AsyncMock(return_value=None)), \
             mock.patch("core.projects.set_user_variable", new=mock.AsyncMock()) as setv:
            got = await projects.get_session_project(self._conn(), "sess-3")
        self.assertIsNone(got)
        setv.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
