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
    host = state.get("host", "127.0.0.1")
    url = f"http://{host}:{state['port']}/health"
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
    """Bidirectional SessionMessage pipe: stdio client <-> HTTP daemon.

    We open both transports in a flat task group so that a cancel from either
    direction propagates cleanly and unblocks stdio_server's stdin_reader.
    """
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.server.stdio import stdio_server

    async with anyio.create_task_group() as outer:
        async def run_pump() -> None:
            async with stdio_server() as (client_read, client_write):
                async with streamablehttp_client(endpoint) as (srv_read, srv_write, _sid):
                    async with anyio.create_task_group() as tg:
                        async def to_daemon():
                            """Forward stdin messages → HTTP daemon."""
                            async for msg in client_read:
                                if isinstance(msg, Exception):
                                    continue  # malformed stdin line; skip
                                await srv_write.send(msg)
                            # Stdin closed: signal HTTP write side we're done.
                            # Don't cancel yet — in-flight responses must arrive.
                            await srv_write.aclose()

                        async def to_client():
                            """Forward HTTP daemon responses → stdout."""
                            async for msg in srv_read:
                                if isinstance(msg, Exception):
                                    continue
                                await client_write.send(msg)
                            # Daemon stream ended: flush stdout write side, then
                            # cancel the outer scope to unblock stdio_server's
                            # stdin_reader (which would otherwise wait for EOF).
                            await client_write.aclose()
                            outer.cancel_scope.cancel()

                        tg.start_soon(to_daemon)
                        tg.start_soon(to_client)

        outer.start_soon(run_pump)


def run_shim() -> None:
    """Entry point: ensure daemon, then pump until either side disconnects."""
    state = ensure_daemon()
    anyio.run(_pump, state["endpoint"])
