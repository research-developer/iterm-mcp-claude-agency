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
from pathlib import Path

import anyio

from iterm_mcpy.daemon import STATE_DIR, is_stale, read_state

# Generous wait for a SIGTERM'd stale daemon to exit; it may need to flush state.
_SIGTERM_WAIT = 5.0


def probe_health(state: dict):
    """GET /health; return parsed body or None if unreachable/broken."""
    try:
        host = state.get("host", "127.0.0.1")
        url = f"http://{host}:{state['port']}/health"
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def spawn_daemon() -> None:
    """Start the daemon detached; logs go to ~/.iterm-mcp/daemon.log."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Pin cwd to the repo root (parent of the iterm_mcpy package) so the
    # daemon always runs against this checkout rather than the client's cwd,
    # which may be unrelated or point at a stale editable install.
    repo_root = Path(__file__).resolve().parents[1]
    log_path = STATE_DIR / "daemon.log"
    if log_path.exists() and log_path.stat().st_size > 5 * 1024 * 1024:
        log_path.replace(log_path.with_suffix(".log.1"))  # keep one generation
    with open(log_path, "ab") as log:
        subprocess.Popen(
            [sys.executable, "-m", "iterm_mcpy", "daemon"],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=repo_root,
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

    if health and not is_stale(health.get("version"), health.get("version_source")):
        return state
    if health:  # confidently stale (git-vs-git mismatch): restart to match this code
        terminate_daemon(health["pid"])
        deadline = time.monotonic() + _SIGTERM_WAIT
        while time.monotonic() < deadline and probe_health(state):
            time.sleep(poll_interval)

    with _spawn_lock():
        # Another shim may have spawned while we waited on the lock.
        state = read_state()
        health = probe_health(state) if state else None
        if health and not is_stale(health.get("version"), health.get("version_source")):
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

    Cancellation design — do not simplify:
      stdio_server() internally spawns a stdin_reader coroutine that blocks
      on `async for line in stdin`. That reader will NOT exit until stdin
      reaches EOF (which the host process never sends while the session is
      alive) or until stdio_server's own task group is cancelled.

      When the daemon stream ends (to_client exhausts srv_read), we must:
        1. Close client_write so the stdout_writer inside stdio_server drains.
        2. Cancel `outer` — which propagates into stdio_server()'s __aexit__,
           cancelling its task group and thereby unblocking stdin_reader.
      Cancelling only the inner `tg` is not enough: stdio_server() has already
      yielded past the `yield`, so its task group is awaiting __aexit__, which
      won't return until stdin_reader finishes. Hence the outer wrapper.

    Known limitation: if the daemon exits while stdin is idle (no pending
    message from the host), the pump does not detect the failure until the
    host sends its next message. A future fix could add a periodic
    health-probe task inside `outer`.
    """
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.server.stdio import stdio_server

    async with anyio.create_task_group() as outer:
        async def run_pump() -> None:
            async with stdio_server() as (client_read, client_write):
                # sse_read_timeout must exceed wait_for's 600 s max long-poll.
                # SDK accepts float seconds or timedelta; using float to match defaults.
                async with streamablehttp_client(
                    endpoint,
                    timeout=30.0,
                    sse_read_timeout=660.0,  # > wait_for's 600 s max long-poll
                ) as (srv_read, srv_write, _sid):
                    async with anyio.create_task_group() as tg:
                        async def to_daemon():
                            """Forward stdin messages → HTTP daemon."""
                            async for msg in client_read:
                                if isinstance(msg, Exception):
                                    print(
                                        f"iterm-mcp shim: transport error (stdin->daemon): {msg!r}",
                                        file=sys.stderr,
                                    )
                                    continue
                                await srv_write.send(msg)
                            # Stdin closed: signal HTTP write side we're done.
                            # Don't cancel yet — in-flight responses must arrive.
                            await srv_write.aclose()

                        async def to_client():
                            """Forward HTTP daemon responses → stdout."""
                            async for msg in srv_read:
                                if isinstance(msg, Exception):
                                    print(
                                        f"iterm-mcp shim: transport error (daemon->stdout): {msg!r}",
                                        file=sys.stderr,
                                    )
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
