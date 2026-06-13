"""Tests for the consolidated iterm-mcp CLI (argument routing only)."""
import unittest
from unittest import mock


class TestCliRouting(unittest.TestCase):
    def _run(self, argv):
        # Patches must target the source module (e.g. iterm_mcpy.shim.run_shim),
        # NOT iterm_mcpy.main.<name>: main.py defers all heavy imports into the
        # subcommand branches, so names resolve at call time in their home modules.
        from iterm_mcpy import main as cli
        with mock.patch("sys.argv", ["iterm-mcp"] + argv):
            cli.main()

    def test_default_runs_shim(self):
        with mock.patch("iterm_mcpy.shim.run_shim") as run_shim:
            self._run([])
        run_shim.assert_called_once()

    def test_daemon_subcommand_runs_daemon(self):
        with mock.patch("iterm_mcpy.daemon.run_daemon") as run_daemon:
            self._run(["daemon", "--port", "12347"])
        run_daemon.assert_called_once_with(host="127.0.0.1", port=12347)

    def test_stdio_subcommand_runs_fastmcp_main(self):
        with mock.patch("iterm_mcpy.fastmcp_server.main") as serve:
            self._run(["stdio"])
        serve.assert_called_once()

    def test_status_with_no_state_prints_not_running(self):
        with mock.patch("iterm_mcpy.daemon.read_state", return_value=None), \
             mock.patch("builtins.print") as fake_print:
            self._run(["status"])
        printed = " ".join(str(c) for c in fake_print.call_args_list)
        self.assertIn("not running", printed)

    def test_stop_with_no_state_prints_not_running(self):
        with mock.patch("iterm_mcpy.daemon.read_state", return_value=None), \
             mock.patch("builtins.print") as fake_print:
            self._run(["stop"])
        printed = " ".join(str(c) for c in fake_print.call_args_list)
        self.assertIn("not running", printed)

    def test_stop_terminates_and_clears(self):
        state = {"pid": 4242, "port": 12341}
        with mock.patch("iterm_mcpy.daemon.read_state", return_value=state), \
             mock.patch("iterm_mcpy.shim.terminate_daemon") as term, \
             mock.patch("iterm_mcpy.daemon.clear_state") as clear, \
             mock.patch("builtins.print"):
            self._run(["stop"])
        term.assert_called_once_with(4242)
        clear.assert_called_once()

    def test_install_code_prints_claude_mcp_add(self):
        with mock.patch("builtins.print") as fake_print:
            self._run(["install", "--code"])
        printed = " ".join(str(c) for c in fake_print.call_args_list)
        self.assertIn("claude mcp add", printed)
        self.assertIn("-m iterm_mcpy", printed)


if __name__ == "__main__":
    unittest.main()
