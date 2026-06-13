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
