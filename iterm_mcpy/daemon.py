"""Singleton daemon: runs the FastMCP server over streamable HTTP.

One daemon per machine. State (port/pid/version) is advertised in
~/.iterm-mcp/daemon.json so shims can discover or spawn it.
"""

import json
import os
import signal
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
                # The socket closes before we return; there is a small TOCTOU window
                # between this probe and FastMCP's bind. Acceptable on loopback.
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
