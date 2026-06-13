# Singleton Daemon for Claude Code + Claude Desktop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One singleton backend process (one iTerm2 connection, one shared set of registries) serving both Claude Code and Claude Desktop, while both platforms keep their zero-config stdio spawn UX.

**Architecture:** A thin stdio↔HTTP shim becomes what both clients spawn; it auto-starts (or discovers) a single streamable-HTTP daemon and pipes JSON-RPC through. The critical enabler is hoisting all state out of the FastMCP lifespan into a process-level `AppContext` singleton — verified against mcp SDK 1.27.0: `Server.run()` enters the lifespan **once per client session**, so without this hoist, HTTP mode would build one iTerm2 connection *per client* and a disconnecting client's lifespan-`finally` would stop the shared event bus.

**Tech Stack:** Python 3.10+, `mcp` SDK ≥1.8 (streamable-HTTP), `anyio`, `unittest` (project standard: `python -m unittest discover tests`).

**Current state (verified during inspection, 2026-06-12):**
- Claude Code (`~/.claude.json`) spawns console script `iterm-mcp` → `iterm_mcpy.main:main` → FastMCP stdio. A stalled attempt at HTTP wiring exists: `"iterm-mcp": {"type": "stdio", "command": "http://127.0.0.1:12345/mcp"}` (invalid — URL as stdio command; sits in `disabledMcpServers`).
- Claude Desktop (`claude_desktop_config.json`) spawns `/opt/anaconda3/bin/python run_server.py` → same FastMCP instance, stdio.
- Result today: **two processes, two iTerm2 WebSocket connections, two divergent copies of every registry.** Cross-agent features (notifications, subscribe feed, teams) silently don't span platforms.
- `run_server.py` already supports `--transport streamable-http` (daemon half-exists); the OAuth 404 `custom_route`s in `fastmcp_server.py` are residue of that attempt.
- Tools already read state from `ctx.request_context.lifespan_context[...]` (a dict) — they are transport-agnostic. The ~20 module globals in `fastmcp_server.py` serve only the `@mcp.resource` handlers.
- Dead parallel stacks: `iterm_mcpy/mcp_server.py` (legacy, `--legacy` flag), `iterm_mcpy/grpc_server.py`/`grpc_client.py`/`iterm_mcp_pb2*.py`/`protos/` (separate gRPC service on :50051 with its own AgentRegistry), `install_claude_desktop.py` (references nonexistent `server.main`, probes ports 12340-12349).

**Out of scope (separate plans):**
- iTerm2 connection reconnect supervisor (CLAUDE.md "Next Steps" item 3). The daemon makes this more valuable but it is independently shippable.
- Auth token on the HTTP port (binds 127.0.0.1 only; same trust domain as iTerm2's own API).
- Idle auto-shutdown of the daemon (`iterm-mcp stop` covers the need).

---

## File Structure

| File | Responsibility |
|---|---|
| `iterm_mcpy/app_context.py` (new) | `AppContext` dataclass (mapping-compatible), process-singleton `get_app_context()` / `shutdown_app_context()`, the moved init body |
| `iterm_mcpy/fastmcp_server.py` (modify) | Slims to: thin lifespan, `mcp` instance, resources/prompts reading from `get_app_context()`. Globals deleted. |
| `iterm_mcpy/daemon.py` (new) | Port selection (12340-12349), `~/.iterm-mcp/daemon.json` state file, `/health` route, `run_daemon()` |
| `iterm_mcpy/shim.py` (new) | Daemon discovery, flock-guarded auto-spawn, version handshake/restart, stdio↔HTTP message pump |
| `iterm_mcpy/__main__.py` (new) | `python -m iterm_mcpy …` entry (needed for re-spawnable daemon) |
| `iterm_mcpy/main.py` (rewrite) | Single CLI: default=shim, `daemon`, `stdio`, `status`, `stop`, `install`. Demo controller and `--legacy` deleted. |
| `run_server.py` (replace) | 6-line deprecation wrapper → CLI `stdio` (keeps existing Desktop config working until `install` is run) |
| deleted | `iterm_mcpy/mcp_server.py`, `iterm_mcpy/grpc_server.py`, `iterm_mcpy/grpc_client.py`, `iterm_mcpy/iterm_mcp_pb2*.py`, `protos/`, `tests/test_grpc_*.py`, `install_claude_desktop.py` |

---

### Task 1: AppContext module with process-level singleton

**Files:**
- Create: `iterm_mcpy/app_context.py`
- Test: `tests/test_app_context.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the process-level AppContext singleton."""
import asyncio
import unittest
from unittest import mock


class TestAppContextMapping(unittest.TestCase):
    def test_getitem_returns_field(self):
        from iterm_mcpy.app_context import AppContext
        ctx = AppContext(logger="fake-logger")
        self.assertEqual(ctx["logger"], "fake-logger")

    def test_getitem_unknown_key_raises_keyerror(self):
        from iterm_mcpy.app_context import AppContext
        ctx = AppContext()
        with self.assertRaises(KeyError):
            ctx["nope"]

    def test_get_returns_default_for_unknown_key(self):
        from iterm_mcpy.app_context import AppContext
        ctx = AppContext()
        self.assertIsNone(ctx.get("nope"))
        self.assertEqual(ctx.get("nope", 7), 7)

    def test_contains(self):
        from iterm_mcpy.app_context import AppContext
        ctx = AppContext(terminal="t")
        self.assertIn("terminal", ctx)
        self.assertNotIn("nope", ctx)


class TestSingleton(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import iterm_mcpy.app_context as ac
        ac._app_context = None  # reset between tests

    async def test_concurrent_calls_build_once_and_share_instance(self):
        import iterm_mcpy.app_context as ac
        from iterm_mcpy.app_context import AppContext, get_app_context
        calls = 0

        async def fake_build():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)  # widen the race window
            return AppContext(logger="built")

        with mock.patch.object(ac, "_build_app_context", fake_build):
            results = await asyncio.gather(*[get_app_context() for _ in range(5)])
        self.assertEqual(calls, 1)
        self.assertTrue(all(r is results[0] for r in results))

    async def test_shutdown_clears_singleton(self):
        import iterm_mcpy.app_context as ac
        from iterm_mcpy.app_context import AppContext, get_app_context, shutdown_app_context

        async def fake_build():
            return AppContext(logger="built")

        with mock.patch.object(ac, "_build_app_context", fake_build):
            first = await get_app_context()
            await shutdown_app_context()
            second = await get_app_context()
        self.assertIsNot(first, second)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_app_context -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'iterm_mcpy.app_context'`

- [ ] **Step 3: Write the module (singleton skeleton; the real builder body arrives in Task 2)**

```python
"""Process-level application context for the iTerm MCP server.

All long-lived state (iTerm2 connection, terminal controller, registries)
lives here exactly once per process. The FastMCP lifespan only hands out a
reference — this is what makes a multi-client daemon possible, because the
mcp SDK runs the lifespan once per *client session*, not once per process.

AppContext implements the read-only mapping protocol so existing tool code
(`ctx.request_context.lifespan_context["terminal"]`) keeps working unchanged.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("iterm-mcp-server")


@dataclass
class AppContext:
    connection: Any = None
    terminal: Any = None
    layout_manager: Any = None
    agent_registry: Any = None
    telemetry: Any = None
    notification_manager: Any = None
    tag_lock_manager: Any = None
    focus_cooldown: Any = None
    feedback_registry: Any = None
    feedback_hook_manager: Any = None
    feedback_forker: Any = None
    github_integration: Any = None
    profile_manager: Any = None
    service_manager: Any = None
    service_hook_manager: Any = None
    manager_registry: Any = None
    event_bus: Any = None
    flow_manager: Any = None
    role_manager: Any = None
    memory_store: Any = None
    logger: Any = None
    log_dir: Optional[str] = None

    # -- mapping protocol (back-compat with the old lifespan dict) --------
    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key) from None

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)


_app_context: Optional[AppContext] = None
_init_lock = asyncio.Lock()


async def _build_app_context() -> AppContext:
    """Build the real context. Body moves here from iterm_lifespan in Task 2."""
    raise NotImplementedError("populated in Task 2")


async def get_app_context() -> AppContext:
    """Return the process-wide AppContext, building it on first call.

    Double-checked lock: concurrent client sessions during daemon startup
    must not each build an iTerm2 connection.
    """
    global _app_context
    if _app_context is not None:
        return _app_context
    async with _init_lock:
        if _app_context is None:
            _app_context = await _build_app_context()
    return _app_context


async def shutdown_app_context() -> None:
    """Tear down shared resources. Called at process exit, never per-session."""
    global _app_context
    ctx = _app_context
    _app_context = None
    if ctx is None:
        return
    if ctx.event_bus is not None:
        try:
            await ctx.event_bus.stop()
        except Exception:
            logger.exception("Error stopping event bus during shutdown")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_app_context -v`
Expected: PASS (6 tests). The `NotImplementedError` builder is never hit — singleton tests patch `_build_app_context`.

- [ ] **Step 5: Commit**

```bash
git add iterm_mcpy/app_context.py tests/test_app_context.py
git commit -m "feat(app-context): process-level AppContext singleton with mapping protocol"
```

---

### Task 2: Move initialization out of the lifespan; delete the module globals

**Files:**
- Modify: `iterm_mcpy/app_context.py` (fill `_build_app_context`)
- Modify: `iterm_mcpy/fastmcp_server.py:50-70` (globals), `:166-363` (lifespan), `:466-672` (resources)

- [ ] **Step 1: Move the init body into `_build_app_context`**

In `iterm_mcpy/app_context.py`, replace the `raise NotImplementedError` body of `_build_app_context` with the contents of `iterm_lifespan` from `fastmcp_server.py` lines 176-299 (logging config through memory-store init), with these exact seam changes:

1. Copy the import block needed by the body from `fastmcp_server.py` lines 15-48 (iterm2, FastMCP not needed; LayoutManager, ItermTerminal, AgentRegistry, TelemetryEmitter, init_tracing, SessionTagLockManager, FocusCooldownManager, ProfileManager/get_profile_manager, feedback imports, services imports, SQLiteMemoryStore, ManagerRegistry, EventBus/FlowManager getters, RoleManager) to the top of `app_context.py`. Move `NotificationManager` (fastmcp_server.py lines 77-163) into `app_context.py` as well — it is state, not server wiring.
2. Delete the `try:` wrapper and the `finally:` block entirely (lines 202, 353-363). Cleanup now lives only in `shutdown_app_context` (already stops the event bus; also add `shutdown_tracing()` there, imported from `utils.otel`).
3. Delete the `global …` assignments (lines 301-325).
4. Replace the `yield {…}` dict (lines 327-351) with:

```python
    return AppContext(
        connection=connection,
        terminal=terminal,
        layout_manager=layout_manager,
        agent_registry=agent_registry,
        telemetry=telemetry,
        notification_manager=notification_manager,
        tag_lock_manager=lock_manager,
        focus_cooldown=focus_cooldown,
        feedback_registry=feedback_registry,
        feedback_hook_manager=feedback_hook_manager,
        feedback_forker=feedback_forker,
        github_integration=github_integration,
        profile_manager=profile_manager,
        service_manager=service_manager,
        service_hook_manager=service_hook_manager,
        manager_registry=manager_registry,
        event_bus=event_bus,
        flow_manager=flow_manager,
        role_manager=role_manager,
        memory_store=memory_store,
        logger=logger,
        log_dir=log_dir,
    )
```

While moving the body, drop the symmetric `logger.info("Initializing X...")` / `logger.info("X initialized successfully")` pairs to one line per manager (simplify-agent finding 6b — ~15 such blocks, pure ceremony).

- [ ] **Step 2: Slim the lifespan and delete the globals in fastmcp_server.py**

Deleting all 20 globals is safe: the simplify agent verified **13 are write-only** (set at lines 302-325, read nowhere), and the remaining 6 (`_terminal`, `_logger`, `_agent_registry`, `_telemetry`, `_notification_manager`, `_memory_store`) are read only by the resource functions rewired in Step 3. Zero external readers (`grep "fastmcp_server import _\|fastmcp_server\._"` → no hits).

Replace lines 50-70 (the `_terminal` … `_memory_store` globals) and lines 77-163 (NotificationManager, now moved) and lines 166-363 (old lifespan) with:

```python
from iterm_mcpy.app_context import (
    AppContext,
    NotificationManager,  # re-export: tools/tests import it from here today
    get_app_context,
)


@asynccontextmanager
async def iterm_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Hand each client session a reference to the shared AppContext.

    The mcp SDK enters this once per client session. It must stay cheap and
    must NOT tear anything down on exit — other clients are still connected.
    """
    yield await get_app_context()
```

- [ ] **Step 3: Rewire the five resources off the globals**

In each of `get_terminal_output`, `get_terminal_info`, `list_all_sessions_resource`, `list_all_agents_resource`, `list_all_teams_resource`, `telemetry_dashboard`, `memory_stats_resource` (fastmcp_server.py lines 466-672): replace the `if _terminal is None …` guard and `terminal = _terminal`-style reads with:

```python
    app = await get_app_context()
    terminal = app.terminal
    logger = app.logger
    # (and agent_registry / telemetry / memory_store per resource)
```

- [ ] **Step 4: Verify nothing else references the deleted globals or imports NotificationManager from the old location**

Run: `grep -rn "fastmcp_server\._\|_terminal\b" --include='*.py' iterm_mcpy/ core/ utils/ tests/ | grep -v app_context`
Expected: no hits outside comments.
Run: `grep -rn "from iterm_mcpy.fastmcp_server import" --include='*.py' . | grep -v .worktrees`
Expected: only `mcp`, `iterm_lifespan`, `NotificationManager` (re-exported), `main` — all still importable.

- [ ] **Step 5: Run the full suite, including the lifespan-threading regression**

Run: `python -m unittest discover tests -v 2>&1 | tail -20`
Expected: same pass count as on main (run on main first to baseline). Pay attention to `tests/test_orchestrate*` — commit ca042e7 added a regression test for lifespan-manager threading; it must stay green.

- [ ] **Step 6: Commit**

```bash
git add iterm_mcpy/app_context.py iterm_mcpy/fastmcp_server.py
git commit -m "refactor(server): hoist all state into AppContext; lifespan becomes per-session reference handout"
```

---

### Task 3: Daemon — port discovery, state file, /health, runner

**Files:**
- Create: `iterm_mcpy/daemon.py`
- Test: `tests/test_daemon.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for daemon state file and port selection (no iTerm2 required)."""
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestStateFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patcher = mock.patch(
            "iterm_mcpy.daemon.STATE_DIR", Path(self.tmp.name)
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_write_then_read_round_trips(self):
        from iterm_mcpy import daemon
        daemon.write_state(port=12341)
        state = daemon.read_state()
        self.assertEqual(state["port"], 12341)
        self.assertEqual(state["endpoint"], "http://127.0.0.1:12341/mcp")
        self.assertIsInstance(state["pid"], int)
        self.assertIn("version", state)

    def test_read_missing_returns_none(self):
        from iterm_mcpy import daemon
        self.assertIsNone(daemon.read_state())

    def test_read_corrupt_returns_none(self):
        from iterm_mcpy import daemon
        (Path(self.tmp.name) / "daemon.json").write_text("{not json")
        self.assertIsNone(daemon.read_state())

    def test_clear_state(self):
        from iterm_mcpy import daemon
        daemon.write_state(port=12341)
        daemon.clear_state()
        self.assertIsNone(daemon.read_state())


class TestPortSelection(unittest.TestCase):
    def test_skips_occupied_port(self):
        from iterm_mcpy import daemon
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 12340))
        blocker.listen(1)
        self.addCleanup(blocker.close)
        port = daemon.find_free_port()
        self.assertNotEqual(port, 12340)
        self.assertIn(port, range(12340, 12350))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_daemon -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'iterm_mcpy.daemon'`

- [ ] **Step 3: Implement daemon.py**

```python
"""Singleton daemon: runs the FastMCP server over streamable HTTP.

One daemon per machine. State (port/pid/version) is advertised in
~/.iterm-mcp/daemon.json so shims can discover or spawn it.
"""

import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path("~/.iterm-mcp").expanduser()
PORT_RANGE = range(12340, 12350)  # documented range, kept from the old attempt


def package_version() -> str:
    try:
        from importlib.metadata import version
        return version("iterm-mcp")
    except Exception:
        return "0.0.0+dev"


def find_free_port() -> int:
    for port in PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port in {PORT_RANGE.start}-{PORT_RANGE.stop - 1}")


def write_state(port: int, host: str = "127.0.0.1") -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "pid": os.getpid(),
        "host": host,
        "port": port,
        "endpoint": f"http://{host}:{port}/mcp",
        "version": package_version(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = STATE_DIR / "daemon.json.tmp"
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_DIR / "daemon.json")


def read_state():
    try:
        return json.loads((STATE_DIR / "daemon.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def clear_state() -> None:
    try:
        (STATE_DIR / "daemon.json").unlink()
    except FileNotFoundError:
        pass


def run_daemon(host: str = "127.0.0.1", port: int = None) -> None:
    """Run the FastMCP server as the singleton HTTP daemon (blocking)."""
    import atexit
    # Import here: pulls in iterm2/FastMCP, which tests above must not need.
    from iterm_mcpy.fastmcp_server import mcp

    port = port or find_free_port()
    mcp.settings.host = host
    mcp.settings.port = port
    write_state(port, host)
    atexit.register(clear_state)
    print(f"iterm-mcp daemon v{package_version()} on http://{host}:{port}/mcp",
          file=sys.stderr)
    mcp.run(transport="streamable-http")
```

- [ ] **Step 4: Add the /health route next to the OAuth routes in fastmcp_server.py (after line 428)**

```python
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Liveness + version handshake endpoint for the shim."""
    from iterm_mcpy.daemon import package_version
    return JSONResponse({
        "status": "ok",
        "version": package_version(),
        "pid": os.getpid(),
    })
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest tests.test_daemon -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Smoke the daemon manually (requires iTerm2 running)**

Run: `python -c "from iterm_mcpy.daemon import run_daemon; run_daemon(port=12348)" & sleep 4 && curl -s http://127.0.0.1:12348/health && kill %1`
Expected: `{"status":"ok","version":"0.1.0","pid":<n>}`

- [ ] **Step 7: Commit**

```bash
git add iterm_mcpy/daemon.py tests/test_daemon.py iterm_mcpy/fastmcp_server.py
git commit -m "feat(daemon): streamable-http singleton daemon with state file and /health"
```

---

### Task 4: Shim — discovery, flock-guarded auto-spawn, version handshake, message pump

**Files:**
- Create: `iterm_mcpy/shim.py`, `iterm_mcpy/__main__.py`
- Test: `tests/test_shim.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for shim daemon-discovery logic (no network, no iTerm2)."""
import unittest
from unittest import mock


class TestEnsureDaemon(unittest.TestCase):
    # NOTE: shim.py does `from iterm_mcpy.daemon import read_state, ...`, so
    # patches must target the names in shim's namespace, not iterm_mcpy.daemon.

    def test_healthy_matching_version_is_reused(self):
        from iterm_mcpy import shim
        state = {"pid": 999, "port": 12341, "endpoint": "http://127.0.0.1:12341/mcp",
                 "version": "0.1.0"}
        with mock.patch.object(shim, "read_state", return_value=state), \
             mock.patch.object(shim, "probe_health",
                               return_value={"status": "ok", "version": "0.1.0", "pid": 999}), \
             mock.patch.object(shim, "spawn_daemon") as spawn, \
             mock.patch.object(shim, "package_version", return_value="0.1.0"):
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
                               return_value={"status": "ok", "version": "0.1.0", "pid": 1000}), \
             mock.patch.object(shim, "spawn_daemon") as spawn, \
             mock.patch.object(shim, "package_version", return_value="0.1.0"), \
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
        # probe sequence: stale health (version mismatch) -> None (confirms the
        # SIGTERM'd daemon is gone) -> fresh health in the post-spawn poll.
        with mock.patch.object(shim, "read_state", side_effect=[stale, None, fresh]), \
             mock.patch.object(shim, "probe_health",
                               side_effect=[{"status": "ok", "version": "0.0.9", "pid": 999},
                                            None,
                                            {"status": "ok", "version": "0.1.0", "pid": 1001}]), \
             mock.patch.object(shim, "terminate_daemon") as term, \
             mock.patch.object(shim, "spawn_daemon") as spawn, \
             mock.patch.object(shim, "package_version", return_value="0.1.0"), \
             mock.patch.object(shim, "_spawn_lock"):
            result = shim.ensure_daemon(spawn_timeout=1.0, poll_interval=0.01)
        term.assert_called_once_with(999)
        spawn.assert_called_once()
        self.assertEqual(result["version"], "0.1.0")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_shim -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'iterm_mcpy.shim'`

- [ ] **Step 3: Implement shim.py**

```python
"""stdio<->HTTP shim: what Claude Code and Claude Desktop actually spawn.

Discovers the singleton daemon (or spawns it, serialized by a file lock),
checks the version handshake, then pipes JSON-RPC between the client on
stdio and the daemon's streamable-HTTP endpoint. The shim is a dumb pipe:
it never interprets MCP messages, so initialize/tools/etc. pass through.
"""

import contextlib
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

import anyio

from iterm_mcpy.daemon import STATE_DIR, package_version, read_state


def probe_health(state: dict):
    """GET /health; return parsed body or None if unreachable/broken."""
    url = f"http://{state['host'] if 'host' in state else '127.0.0.1'}:{state['port']}/health"
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def spawn_daemon() -> None:
    """Start the daemon detached; logs go to ~/.iterm-mcp/daemon.log."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = open(STATE_DIR / "daemon.log", "ab")
    subprocess.Popen(
        [sys.executable, "-m", "iterm_mcpy", "daemon"],
        stdout=log, stderr=log, stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def terminate_daemon(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, signal.SIGTERM)


@contextlib.contextmanager
def _spawn_lock():
    """Serialize daemon spawning across concurrent shims (flock)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "spawn.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def ensure_daemon(spawn_timeout: float = 20.0, poll_interval: float = 0.25) -> dict:
    """Return state for a healthy, version-matched daemon, spawning if needed."""
    state = read_state()
    health = probe_health(state) if state else None

    if health and health.get("version") == package_version():
        return state
    if health:  # alive but stale version: restart so behavior matches this code
        terminate_daemon(health["pid"])
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and probe_health(state):
            time.sleep(poll_interval)

    with _spawn_lock():
        # Another shim may have spawned while we waited on the lock.
        state = read_state()
        health = probe_health(state) if state else None
        if health and health.get("version") == package_version():
            return state
        spawn_daemon()
        deadline = time.monotonic() + spawn_timeout
        while time.monotonic() < deadline:
            state = read_state()
            if state and probe_health(state):
                return state
            time.sleep(poll_interval)
    raise RuntimeError(
        f"iterm-mcp daemon failed to start within {spawn_timeout}s "
        f"(see {STATE_DIR / 'daemon.log'})"
    )


async def _pump(endpoint: str) -> None:
    """Bidirectional SessionMessage pipe: stdio client <-> HTTP daemon."""
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (client_read, client_write):
        async with streamablehttp_client(endpoint) as (srv_read, srv_write, _sid):
            async with anyio.create_task_group() as tg:
                async def to_daemon():
                    async for msg in client_read:
                        if isinstance(msg, Exception):
                            continue  # malformed stdin line; skip
                        await srv_write.send(msg)
                    tg.cancel_scope.cancel()  # client hung up

                async def to_client():
                    async for msg in srv_read:
                        if isinstance(msg, Exception):
                            continue
                        await client_write.send(msg)
                    tg.cancel_scope.cancel()  # daemon hung up

                tg.start_soon(to_daemon)
                tg.start_soon(to_client)


def run_shim() -> None:
    """Entry point: ensure daemon, then pump until either side disconnects."""
    state = ensure_daemon()
    anyio.run(_pump, state["endpoint"])
```

- [ ] **Step 4: Create `iterm_mcpy/__main__.py`**

```python
"""Allow `python -m iterm_mcpy <subcommand>` (used by shim to spawn daemon)."""
from iterm_mcpy.main import main

main()
```

- [ ] **Step 5: Run unit tests**

Run: `python -m unittest tests.test_shim -v`
Expected: PASS (3 tests)

- [ ] **Step 6: End-to-end smoke (requires iTerm2 running) — gate behind env var, also add as test**

Append to `tests/test_shim.py`:

```python
import os
import unittest


@unittest.skipUnless(os.environ.get("ITERM_MCP_E2E"), "needs iTerm2; set ITERM_MCP_E2E=1")
class TestShimEndToEnd(unittest.TestCase):
    def test_initialize_and_list_tools_through_shim(self):
        import json as _json
        import subprocess, sys
        proc = subprocess.Popen(
            [sys.executable, "-m", "iterm_mcpy"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26",
                               "capabilities": {},
                               "clientInfo": {"name": "e2e", "version": "0"}}}
            proc.stdin.write((_json.dumps(init) + "\n").encode())
            proc.stdin.flush()
            line = proc.stdout.readline()
            resp = _json.loads(line)
            self.assertEqual(resp["id"], 1)
            self.assertIn("serverInfo", resp["result"])
        finally:
            proc.kill()
```

Run: `ITERM_MCP_E2E=1 python -m unittest tests.test_shim.TestShimEndToEnd -v`
Expected: PASS. **Known risk:** if the streamable-HTTP client transport refuses raw passthrough of the initialize handshake (session-id header sequencing), this test catches it. Fallback documented in the Risks section below — do not improvise; stop and re-plan with the team.

- [ ] **Step 7: Commit**

```bash
git add iterm_mcpy/shim.py iterm_mcpy/__main__.py tests/test_shim.py
git commit -m "feat(shim): stdio-to-HTTP shim with flock auto-spawn and version handshake"
```

---

### Task 5: CLI consolidation in main.py

**Files:**
- Rewrite: `iterm_mcpy/main.py`
- Replace: `run_server.py`
- Modify: `pyproject.toml:34-37` ([project.scripts])

- [ ] **Step 1: Rewrite main.py**

Delete the entire current contents (the `ItermController` demo, lines 18-159, and the old `main`, lines 162-235). New contents:

```python
"""iterm-mcp CLI.

Default (no args): run the stdio shim — the right thing for Claude Code
and Claude Desktop config entries. Subcommands manage the daemon directly.
"""

import argparse
import json
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="iterm-mcp",
                                     description="iTerm2 MCP server")
    sub = parser.add_subparsers(dest="command")

    p_daemon = sub.add_parser("daemon", help="run the singleton HTTP daemon (foreground)")
    p_daemon.add_argument("--host", default="127.0.0.1")
    p_daemon.add_argument("--port", type=int, default=None,
                          help="default: first free port in 12340-12349")

    sub.add_parser("stdio", help="single-process stdio server (no daemon; debugging)")
    sub.add_parser("status", help="show daemon state and health")
    sub.add_parser("stop", help="stop the running daemon")

    p_install = sub.add_parser("install", help="write client configs")
    p_install.add_argument("--desktop", action="store_true",
                           help="update Claude Desktop config")
    p_install.add_argument("--code", action="store_true",
                           help="print the 'claude mcp add' command for Claude Code")

    args = parser.parse_args()

    if args.command is None:
        from iterm_mcpy.shim import run_shim
        run_shim()
    elif args.command == "daemon":
        from iterm_mcpy.daemon import run_daemon
        run_daemon(host=args.host, port=args.port)
    elif args.command == "stdio":
        from iterm_mcpy.fastmcp_server import main as serve_stdio
        serve_stdio()
    elif args.command == "status":
        _status()
    elif args.command == "stop":
        _stop()
    elif args.command == "install":
        _install(desktop=args.desktop, code=args.code)


def _status() -> None:
    from iterm_mcpy.daemon import read_state
    from iterm_mcpy.shim import probe_health
    state = read_state()
    if not state:
        print("daemon: not running (no state file)")
        return
    health = probe_health(state)
    print(json.dumps({"state": state, "health": health or "unreachable"}, indent=2))


def _stop() -> None:
    from iterm_mcpy.daemon import clear_state, read_state
    from iterm_mcpy.shim import terminate_daemon
    state = read_state()
    if not state:
        print("daemon: not running")
        return
    terminate_daemon(state["pid"])
    clear_state()
    print(f"sent SIGTERM to daemon pid {state['pid']}")


def _install(desktop: bool, code: bool) -> None:
    from pathlib import Path
    if not desktop and not code:
        desktop = code = True
    if desktop:
        cfg_path = Path("~/Library/Application Support/Claude/"
                        "claude_desktop_config.json").expanduser()
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        cfg.setdefault("mcpServers", {})["iterm"] = {
            "command": sys.executable,
            "args": ["-m", "iterm_mcpy"],
        }
        cfg_path.write_text(json.dumps(cfg, indent=2))
        print(f"updated {cfg_path} (restart Claude Desktop to pick it up)")
    if code:
        print("For Claude Code, run:")
        print(f"  claude mcp add --scope user iterm -- {sys.executable} -m iterm_mcpy")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Replace run_server.py with a deprecation wrapper**

The live Claude Desktop config spawns `run_server.py` today; it must keep working until `iterm-mcp install --desktop` is run. Replace the whole file with:

```python
#!/usr/bin/env python3
"""DEPRECATED: kept so existing Claude Desktop configs keep working.

Run `iterm-mcp install --desktop` to migrate, then delete this file.
Now routes through the shim, so Desktop shares the singleton daemon too.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from iterm_mcpy.shim import run_shim

run_shim()
```

- [ ] **Step 3: Trim [project.scripts] in pyproject.toml**

Replace lines 34-37 with:

```toml
[project.scripts]
iterm-mcp = "iterm_mcpy.main:main"
```

(`iterm-mcp-server` pointed at the legacy server deleted in Task 6; `iterm-mcp-fastmcp` is now `iterm-mcp stdio`.)

- [ ] **Step 4: Verify the CLI surface**

Run: `pip install -e . -q && iterm-mcp status && iterm-mcp --help`
Expected: "daemon: not running (no state file)" (or live state), then help text listing daemon/stdio/status/stop/install.

- [ ] **Step 5: Run full suite**

Run: `python -m unittest discover tests 2>&1 | tail -5`
Expected: same pass count as Task 2 baseline.

- [ ] **Step 6: Commit**

```bash
git add iterm_mcpy/main.py run_server.py pyproject.toml
git commit -m "feat(cli): consolidate entry points; default command is the singleton shim"
```

---

### Task 6: Delete the dead stacks

**Files:**
- Delete: `iterm_mcpy/mcp_server.py`, `iterm_mcpy/grpc_server.py`, `iterm_mcpy/grpc_client.py`, `iterm_mcpy/iterm_mcp_pb2.py`, `iterm_mcpy/iterm_mcp_pb2_grpc.py`, `protos/`, `tests/test_grpc_client.py`, `tests/test_grpc_smoke.py`, `install_claude_desktop.py`
- Modify: `iterm_mcpy/__init__.py` (drop lazy `ITermClient` export), `pyproject.toml` (drop `grpcio`, `protobuf` deps and `grpcio-tools` from dev)

> Cross-check against the simplify-agent report (appendix below) before executing — it independently verified reachability of each candidate.

- [ ] **Step 1: Verify nothing outside the deletion set references the deleted modules**

Run: `grep -rn "mcp_server\|grpc_client\|grpc_server\|iterm_mcp_pb2\|ITermClient\|install_claude_desktop" --include='*.py' --include='*.toml' --include='*.json' --include='*.md' . | grep -v -e .worktrees -e docs/archive -e "^Binary"`
Expected: hits only in the files being deleted, `iterm_mcpy/__init__.py`, `pyproject.toml`, `CLAUDE.md`/`README` (docs fixed in Task 7), and `utils/otel.py` (its "grpc" hit is the OTLP exporter — unrelated, keep).

- [ ] **Step 2: Delete**

```bash
git rm iterm_mcpy/mcp_server.py iterm_mcpy/grpc_server.py iterm_mcpy/grpc_client.py \
       iterm_mcpy/iterm_mcp_pb2.py iterm_mcpy/iterm_mcp_pb2_grpc.py \
       tests/test_grpc_client.py tests/test_grpc_smoke.py install_claude_desktop.py
git rm -r protos/
```

- [ ] **Step 3: Reduce `iterm_mcpy/__init__.py` to**

```python
"""Server module for Model Context Protocol (MCP) integration with iTerm2."""
```

- [ ] **Step 4: Drop the deps and residue**

In `pyproject.toml` dependencies, delete the `"grpcio>=1.76.0",` and `"protobuf>=6.31.1",` lines; in `[project.optional-dependencies].dev` delete `"grpcio-tools>=1.76.0",`; delete the two pb2 coverage-omit lines (pyproject.toml:80-81). In `README.md`, delete the gRPC usage section (README.md:665-668).

- [ ] **Step 5: Reinstall and run full suite**

Run: `pip install -e . -q && python -m unittest discover tests 2>&1 | tail -5`
Expected: same pass count minus the two deleted gRPC test modules.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: delete legacy mcp_server, gRPC stack, and broken Desktop installer"
```

---

### Task 7: Docs, live configs, and dual-platform verification

**Files:**
- Modify: `CLAUDE.md` (project structure section, "Running the Server", "Claude Desktop Integration"), `README.md` (matching sections), `.mcp.json`

- [ ] **Step 1: Update `.mcp.json`**

```json
{
  "mcpServers": {
    "iTerm": {
      "command": "python",
      "args": ["-m", "iterm_mcpy"],
      "env": {}
    }
  }
}
```

- [ ] **Step 2: Update CLAUDE.md**

Replace the "Running the Server" and "Claude Desktop Integration" sections with the new model: `iterm-mcp` (shim, default), `iterm-mcp daemon|stdio|status|stop|install`; note that both platforms share one daemon and that the daemon auto-starts on first client. Remove `--demo`/`--legacy` references and the "manually start the server before using Claude Desktop" instruction (now obsolete). Update the project-structure tree (drop deleted files, add `app_context.py`, `daemon.py`, `shim.py`, `__main__.py`).

Also sweep the stale pre-rename references the simplify agent found: `README.md` lines 105, 108, 111, 114, 135, 148, 175, 176, 215 still document `iterm_mcp_python.server.main` / `server.main`, a package that no longer exists. Replace each with the single `iterm-mcp` entry point.

- [ ] **Step 3: Fix the user-level configs (manual, with Preston)**

- Claude Code: remove the broken entry `"iterm-mcp": {"type": "stdio", "command": "http://127.0.0.1:12345/mcp"}` from `~/.claude.json` (`claude mcp remove iterm-mcp`), keep/re-add `iTerm` via the printed `claude mcp add` command from `iterm-mcp install --code`.
- Claude Desktop: run `iterm-mcp install --desktop`, restart Desktop.

- [ ] **Step 4: Verify the singleton end-to-end**

1. `iterm-mcp stop` (clean slate), then open a Claude Code session and call `sessions` with `op="GET"` — daemon auto-starts; `iterm-mcp status` shows one pid.
2. Open Claude Desktop, use the iterm server — `iterm-mcp status` shows the **same pid**; `ps aux | grep iterm_mcpy | grep -v grep | wc -l` shows 1 daemon + 2 shims.
3. Register an agent from Claude Code (`agents op="CREATE"`), then list agents from Claude Desktop — the agent must be visible (shared registry: the actual point of this whole plan).
4. Quit both clients; daemon stays up (by design); `iterm-mcp stop` shuts it down and clears state.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md .mcp.json
git commit -m "docs: singleton daemon architecture; update client setup for both platforms"
```

---

## Risks & Fallbacks

1. **Shim passthrough quirks (highest risk).** The pump forwards transport-level `SessionMessage`s without driving a `ClientSession`. The streamable-HTTP client transport manages the `mcp-session-id` header itself, so initialize should pass through — but if the SDK's transport insists on owning the handshake, the e2e test in Task 4 Step 6 fails. Fallback: have the shim run a real `ClientSession` against the daemon and a real low-level `Server` on stdio, forwarding each request type explicitly (~80 more lines, fully deterministic); or interim-bridge with `npx mcp-remote <endpoint>` while that's built.

   > **Execution outcome (2026-06-12): passthrough WORKS** — initialize round-trips through the shim in ~2s. But the pump code as written above **deadlocks on disconnect** and was corrected during implementation: `stdio_server()`'s internal `stdin_reader` blocks on stdin and is unaffected by cancelling the inner task group, and `anyio`'s thread-backed `readline` defers cancellation. The committed design (see `iterm_mcpy/shim.py:_pump`) wraps the pump in an **outer** task group; the daemon→client pump closes `client_write` then cancels the outer scope, and the client→daemon pump closes `srv_write` (whose transport `finally` chains the shutdown) rather than cancelling. Verified empirically: spec'd version still alive 10s after stdin EOF; committed version exits cleanly in 0.2s. Do not "simplify" the pump back to the version printed above.
2. **Per-session lifespan regressions.** Some tool/test may have depended on lifespan cleanup running per client. Task 2 Step 5 baselines the suite on main first, and the ca042e7 lifespan-threading regression test is explicitly checked.
3. **Shared-state races across clients.** All sessions share one event loop; registries are async-single-loop and `NotificationManager` already locks. Concurrent same-named-agent registration from two clients is last-writer-wins — acceptable now; flag in code review if a stricter policy is wanted.
4. **Version skew during development.** Editable installs report the same `0.1.0` even when code changed, so the handshake won't catch every stale daemon during dev. `iterm-mcp stop` is the dev workaround; bumping `version` in pyproject per release makes the handshake real for users.
5. **Daemon dies with iTerm2.** A long-lived daemon holding a dead iTerm2 WebSocket will fail all tool calls until restarted. Out of scope here (reconnect supervisor is its own plan); `iterm-mcp stop` + auto-respawn-on-next-client is the interim recovery.

## Appendix: simplify-agent findings (completed 2026-06-12)

A code-simplifier agent independently reviewed the transport/server stacks in an isolated worktree. Summary of its report and how each finding maps into this plan:

| Finding | Evidence | Where folded in |
|---|---|---|
| Delete gRPC stack (~2,150 LOC incl. tests/deps) | Only importers are the gRPC files themselves, their 2 tests, and the lazy `ITermClient` shim in `iterm_mcpy/__init__.py`; `grpc_server.py:34` builds its **own** AgentRegistry — it *is* a second backend | Task 6 (added pyproject coverage-omit lines 80-81, README gRPC section 665-668) |
| Delete `install_claude_desktop.py` — broken, not just stale | Spawns/configures `python -m server.main`; no `server/` package exists; probes ports nothing binds | Task 6 (replacement is `iterm-mcp install`, Task 5) |
| Delete legacy `mcp_server.py` + `--demo` controller (~840 LOC) | `mcp_server.py` reachable only via `--legacy`, zero test imports; demo used by nothing; `while True: sleep(10)` tail in main.py is unreachable (mcp.run() blocks) | Tasks 5 & 6 |
| 13 of 20 module globals are write-only; only 6 read, all by co-located resources | grep: zero external readers of any `fastmcp_server._*` | Task 2 (deletes all 20; resources rewired) |
| Tests already inject state by assigning `lifespan_context = {...}` directly (`tests/test_sessions.py:18`, `tests/test_action_tools.py:41`) | The lifespan dict is the de-facto injection seam | Validates Task 1's mapping-protocol `AppContext` — existing tests keep working with dicts; no 60-80-call-site migration required up front |
| Consolidate 6 launch paths → 1, keep launcher inside the package (run_server.py needs a sys.path hack) | All three console scripts call `mcp.run()` on the same object | Task 5 (single `iterm-mcp` CLI; run_server.py demoted to deprecation wrapper) |
| Init-logging ceremony (~15 symmetric log pairs), OAuth 404 triplication, duplicated session-summary dicts | fastmcp_server.py:202-325, 391-428, 466-595 | Logging trim folded into Task 2 Step 1; OAuth dedup and `_session_summary` helper left as optional follow-ups (low value vs. plan size) |
| Stale `iterm_mcp_python.server.main` doc references across README/CLAUDE.md | README lines 105-215 (9 sites) | Task 7 Step 2 |

**One correction to the agent's report:** it concluded the dual-platform goal is "already serviceable by `run_server.py` alone" via `--transport streamable-http`. That is the trap this plan exists to avoid: verified against mcp SDK 1.27.0, the FastMCP lifespan runs **once per client session** (`StreamableHTTPSessionManager._handle_stateful_request` → `app.run()` → `Server.run()` enters `self.lifespan` per call), so HTTP mode with today's `iterm_lifespan` still builds one iTerm2 connection and one set of registries *per client*, and a disconnecting client's `finally` stops the shared event bus. Tasks 1-2 (AppContext hoist) are the prerequisite that makes daemon mode an actual singleton.
