"""Tests for shim daemon-discovery logic (no network, no iTerm2)."""
import unittest
from unittest import mock


class TestEnsureDaemon(unittest.TestCase):
    # NOTE: shim.py does `from iterm_mcpy.daemon import read_state, ...`, so
    # patches must target the names in shim's namespace, not iterm_mcpy.daemon.

    def test_healthy_matching_version_is_reused(self):
        # A healthy, not-stale daemon (is_stale -> False) is reused as-is.
        from iterm_mcpy import shim
        state = {"pid": 999, "port": 12341, "endpoint": "http://127.0.0.1:12341/mcp",
                 "version": "0.1.0"}
        with mock.patch.object(shim, "read_state", return_value=state), \
             mock.patch.object(shim, "probe_health",
                               return_value={"status": "ok", "version": "0.1.0",
                                             "version_source": "git", "pid": 999}), \
             mock.patch.object(shim, "spawn_daemon") as spawn, \
             mock.patch.object(shim, "is_stale", return_value=False):
            result = shim.ensure_daemon()
        spawn.assert_not_called()
        self.assertEqual(result["endpoint"], "http://127.0.0.1:12341/mcp")

    def test_no_daemon_spawns_one(self):
        from iterm_mcpy import shim
        fresh = {"pid": 1000, "port": 12342, "endpoint": "http://127.0.0.1:12342/mcp",
                 "version": "0.1.0"}
        # read_state: initial probe -> None; recheck under lock -> None;
        # first poll iteration -> fresh. probe_health is only reached once,
        # in that poll iteration (earlier calls are skipped while state is None).
        with mock.patch.object(shim, "read_state", side_effect=[None, None, fresh]), \
             mock.patch.object(shim, "probe_health",
                               return_value={"status": "ok", "version": "0.1.0",
                                             "version_source": "git", "pid": 1000}), \
             mock.patch.object(shim, "spawn_daemon") as spawn, \
             mock.patch.object(shim, "is_stale", return_value=False), \
             mock.patch.object(shim, "_spawn_lock"):
            result = shim.ensure_daemon(spawn_timeout=1.0, poll_interval=0.01)
        spawn.assert_called_once()
        self.assertEqual(result["port"], 12342)

    def test_version_mismatch_restarts_daemon(self):
        from iterm_mcpy import shim
        stale = {"pid": 999, "port": 12341, "endpoint": "http://127.0.0.1:12341/mcp",
                 "version": "0.0.9"}
        fresh = {"pid": 1001, "port": 12341, "endpoint": "http://127.0.0.1:12341/mcp",
                 "version": "0.1.0"}
        # probe sequence: stale health (is_stale -> True) -> None (confirms the
        # SIGTERM'd daemon is gone) -> fresh health in the post-spawn poll.
        # read_state's second value is None: after SIGTERM the daemon clears
        # its state file, so the under-lock recheck finds nothing. is_stale is
        # consulted once (the initial probe); the under-lock recheck sees no
        # state, and the post-spawn poll doesn't re-check the version.
        with mock.patch.object(shim, "read_state", side_effect=[stale, None, fresh]), \
             mock.patch.object(shim, "probe_health",
                               side_effect=[{"status": "ok", "version": "0.0.9",
                                             "version_source": "git", "pid": 999},
                                            None,
                                            {"status": "ok", "version": "0.1.0",
                                             "version_source": "git", "pid": 1001}]), \
             mock.patch.object(shim, "terminate_daemon") as term, \
             mock.patch.object(shim, "spawn_daemon") as spawn, \
             mock.patch.object(shim, "is_stale", return_value=True), \
             mock.patch.object(shim, "_spawn_lock"):
            result = shim.ensure_daemon(spawn_timeout=1.0, poll_interval=0.01)
        term.assert_called_once_with(999)
        spawn.assert_called_once()
        self.assertEqual(result["version"], "0.1.0")


import os


@unittest.skipUnless(os.environ.get("ITERM_MCP_E2E"), "needs iTerm2; set ITERM_MCP_E2E=1")
class TestShimEndToEnd(unittest.TestCase):
    """Full path: stdio shim -> auto-spawned HTTP daemon -> initialize."""

    def test_initialize_through_shim(self):
        import json as _json
        import selectors
        import subprocess
        import sys
        import time as _time

        proc = subprocess.Popen(
            [sys.executable, "-c",
             "from iterm_mcpy.shim import run_shim; run_shim()"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26",
                               "capabilities": {},
                               "clientInfo": {"name": "e2e", "version": "0"}}}
            proc.stdin.write((_json.dumps(init) + "\n").encode())
            proc.stdin.flush()

            sel = selectors.DefaultSelector()
            sel.register(proc.stdout, selectors.EVENT_READ)
            deadline = _time.monotonic() + 30
            line = b""
            while _time.monotonic() < deadline:
                if sel.select(timeout=1.0):
                    line = proc.stdout.readline()
                    break
            self.assertTrue(line, "no response from shim within 30s (see ~/.iterm-mcp/daemon.log)")
            resp = _json.loads(line)
            self.assertEqual(resp["id"], 1)
            self.assertIn("serverInfo", resp["result"])
        finally:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    pipe.close()
                except Exception:
                    pass
            # Stop the daemon the shim auto-spawned so it doesn't linger.
            from iterm_mcpy import daemon as d
            from iterm_mcpy import shim as s
            state = d.read_state()
            if state:
                s.terminate_daemon(state["pid"])
                d.clear_state()


if __name__ == "__main__":
    unittest.main()
