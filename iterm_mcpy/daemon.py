"""Singleton daemon: runs the FastMCP server over streamable HTTP.

One daemon per machine. State (port/pid/version) is advertised in
~/.iterm-mcp/daemon.json so shims can discover or spawn it.
"""

import functools
import json
import os
import signal
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

STATE_DIR = Path("~/.iterm-mcp").expanduser()
CONFIG_PATH = STATE_DIR / "config.json"  # persistent, survives `stop`/restart
PORT_RANGE = range(12340, 12350)  # documented range, kept from the old attempt
_REPO_ROOT = Path(__file__).resolve().parent.parent  # dir containing .git


def _base_version() -> str:
    """The ``major.minor`` prefix, from installed package metadata.

    The pyproject patch digit is intentionally ignored — the patch is
    derived from git (see package_version). Falls back to "0.1" when
    metadata is unreadable.
    """
    try:
        from importlib.metadata import version
        parts = version("iterm-mcp").split(".")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{parts[0]}.{parts[1]}"
    except Exception:
        pass
    return "0.1"


def _commit_count() -> Optional[str]:
    """`git rev-list --count HEAD` against this checkout, or None if no git.

    Pinned to _REPO_ROOT (not cwd) so the daemon and shim — which may run
    from different working directories — compute the same value.
    """
    try:
        out = subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(_REPO_ROOT), stderr=subprocess.DEVNULL, text=True,
        ).strip()
        return out if out.isdigit() else None
    except (OSError, subprocess.CalledProcessError):
        return None


@functools.lru_cache(maxsize=1)
def _resolve_version():
    """Return ``(version_string, source)`` frozen per process.

    ``source`` is "git" when the patch digit came from the commit count,
    else "metadata" (git unavailable in this process's environment — e.g. a
    wheel install, or Claude Desktop launched from Finder with a minimal
    PATH). The source is what lets the shim avoid a *false* staleness
    restart when only one side can run git (see is_stale).

    Frozen via lru_cache: the daemon reports the code it started with, while
    a freshly spawned shim computes the *current* checkout — so a new commit
    makes the two differ and the shim restarts the stale daemon.
    """
    count = _commit_count()
    if count is None:
        try:
            from importlib.metadata import version
            return version("iterm-mcp"), "metadata"
        except Exception:
            return "0.0.0+dev", "metadata"
    return f"{_base_version()}.{count}", "git"


def package_version() -> str:
    """Auto-derived ``x.y.z``: ``major.minor`` + git commit count as patch."""
    return _resolve_version()[0]


def version_source() -> str:
    """"git" if the version was derived from the commit count, else "metadata"."""
    return _resolve_version()[1]


def is_stale(reported_version, reported_source) -> bool:
    """Whether a daemon reporting these values runs older code than us.

    Returns True only on a *confident* signal: both this process and the
    daemon derived their version from git and the versions differ. If either
    side fell back to metadata (git absent in that environment), the
    mismatch is untrustworthy, so this returns False and the shared
    singleton daemon is left running rather than thrashed by a client that
    merely lacks git on its PATH. An old daemon that predates this field
    reports no source, so it too is treated as not-confidently-stale (it is
    replaced on the next manual restart / genuine git-vs-git mismatch).
    """
    if reported_version == package_version():
        return False
    return reported_source == "git" and version_source() == "git"


def _coerce_port(raw) -> Optional[int]:
    """Parse a port value; return None if it isn't a valid 1-65535 int."""
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def preferred_port() -> Optional[int]:
    """Resolve a pinned daemon port, if one is configured.

    Precedence: a *valid* ITERM_MCP_PORT environment variable (a transient
    override) over the persisted ``preferred_port`` in
    ~/.iterm-mcp/config.json. An empty or malformed env var falls through to
    the config value rather than silently disabling the pin. Both the manual
    `iterm-mcp daemon` path and the shim's auto-spawn path funnel through
    find_free_port(), so a value here pins every daemon on this machine —
    surviving restarts and auto-respawns.

    Returns:
        A valid port (1-65535), or None when unset/invalid, in which case
        find_free_port() scans PORT_RANGE.
    """
    env_port = _coerce_port(os.environ.get("ITERM_MCP_PORT"))
    if env_port is not None:
        return env_port
    try:
        cfg_raw = json.loads(CONFIG_PATH.read_text()).get("preferred_port")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        cfg_raw = None
    return _coerce_port(cfg_raw)


def set_preferred_port(port: Optional[int]) -> None:
    """Persist (or clear) the pinned daemon port in ~/.iterm-mcp/config.json.

    Passing None removes the pin so the daemon reverts to scanning
    PORT_RANGE. Writes via a per-process temp file plus atomic replace, so a
    concurrent *reader* never sees a half-written file. Concurrent *writers*
    are last-writer-wins — acceptable for this admin-only helper.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        cfg = {}
    if port is None:
        cfg.pop("preferred_port", None)
    else:
        cfg["preferred_port"] = int(port)
    # Per-process tmp name so two concurrent writers don't clobber the same
    # scratch file before either replace() lands.
    tmp = CONFIG_PATH.with_suffix(f".json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(CONFIG_PATH)


def find_free_port() -> int:
    """Pick the port the daemon should bind.

    Honors a pinned port (see preferred_port) first; if it is set but
    cannot be bound (already in use), warns and falls back to the first
    free port in PORT_RANGE so the daemon still starts and stays
    discoverable via the state file. With no pin, scans PORT_RANGE.
    """
    pinned = preferred_port()
    candidates = ([pinned] if pinned is not None else []) + [
        p for p in PORT_RANGE if p != pinned
    ]
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # Mirror uvicorn's bind semantics: it sets SO_REUSEADDR, so a port
            # left in TIME_WAIT by a just-stopped daemon is still bindable by
            # the server. Without this the probe spuriously rejects the pinned
            # port right after a restart and drifts into the fallback range.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                # The socket closes before we return; there is a small TOCTOU window
                # between this probe and FastMCP's bind. Acceptable on loopback.
                s.bind(("127.0.0.1", port))
                if pinned is not None and port != pinned:
                    print(
                        f"iterm-mcp: pinned port {pinned} unavailable; "
                        f"using {port} instead",
                        file=sys.stderr,
                    )
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
    """Remove the state file — but only if it belongs to this process.

    A SIGTERM'd old daemon's atexit must not delete the state file a
    newly spawned successor just wrote (split-brain: healthy daemon
    becomes invisible and the next shim spawns another).
    """
    state = read_state()
    if state and state.get("pid") not in (None, os.getpid()):
        return
    try:
        (STATE_DIR / "daemon.json").unlink()
    except FileNotFoundError:
        pass


def run_daemon(host: str = "127.0.0.1", port: Optional[int] = None) -> None:
    """Run the FastMCP server as the singleton HTTP daemon (blocking).

    Clears the state file on normal exit and SIGTERM.
    """
    import atexit
    # Import here: pulls in iterm2/FastMCP, which the tests above must not need.
    from iterm_mcpy.fastmcp_server import mcp

    port = port or find_free_port()
    mcp.settings.host = host
    mcp.settings.port = port
    write_state(port, host)
    atexit.register(clear_state)
    # atexit only fires on normal interpreter exit; translate SIGTERM into
    # SystemExit so `kill <pid>` also clears the state file.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    print(f"iterm-mcp daemon v{package_version()} on http://{host}:{port}/mcp",
          file=sys.stderr)
    mcp.run(transport="streamable-http")
