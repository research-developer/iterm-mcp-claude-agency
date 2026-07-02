"""Tests for the `iterm-mcp project` CLI (set/get)."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from iterm_mcpy import project_cli


class TestProjectCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = mock.patch.object(project_cli, "MARKER_DIR", os.path.join(self.tmp.name, "projects"))
        self.p.start()
        self.addCleanup(self.p.stop)

    def test_set_emits_escape_and_writes_marker(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_SESSION_ID": "cc-1"}), redirect_stdout(out):
            project_cli.cmd_set("/Users/me/repoA", session_id=None)
        self.assertIn("SetUserVar=mcp_project=", out.getvalue())
        marker = os.path.join(self.tmp.name, "projects", "cc-1")
        self.assertTrue(os.path.exists(marker))
        with open(marker) as fh:
            self.assertEqual(fh.read().strip(), "/Users/me/repoA")

    def test_set_uses_explicit_session_id_when_env_absent(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(out):
            project_cli.cmd_set("/Users/me/repoA", session_id="explicit-9")
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "projects", "explicit-9")))

    def test_get_reads_marker(self):
        os.makedirs(os.path.join(self.tmp.name, "projects"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "projects", "cc-1"), "w") as fh:
            fh.write("/Users/me/repoA\n")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_SESSION_ID": "cc-1"}), redirect_stdout(out):
            project_cli.cmd_get(session_id=None)
        self.assertIn("/Users/me/repoA", out.getvalue())

    def test_get_when_undeclared_prints_none(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_SESSION_ID": "nope"}), redirect_stdout(out):
            project_cli.cmd_get(session_id=None)
        self.assertIn("not set", out.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
