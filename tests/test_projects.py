"""Tests for the pure project resolver + SetUserVar escape builder."""
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


if __name__ == "__main__":
    unittest.main()
