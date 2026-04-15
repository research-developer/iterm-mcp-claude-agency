"""Telemetry dashboard tool.

Provides the start_telemetry_dashboard tool and a helper that starts a
lightweight HTTP server streaming telemetry JSON.
"""

import asyncio
import json
from typing import Optional

from mcp.server.fastmcp import Context

from core.dashboard import start_dashboard
from core.terminal import ItermTerminal
from utils.telemetry import TelemetryEmitter


# Module-local state for the helper HTTP server (singleton task per server process).
_telemetry_server_task: Optional[asyncio.Task] = None


async def _start_telemetry_server(
    port: int,
    duration: int,
    telemetry: TelemetryEmitter,
    terminal: ItermTerminal,
) -> str:
    """Start a lightweight HTTP server that streams telemetry JSON."""
    global _telemetry_server_task

    if _telemetry_server_task:
        _telemetry_server_task.cancel()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await terminal.get_sessions()
            payload = telemetry.dashboard_state(terminal)
            body = json.dumps(payload, indent=2)
            response = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body.encode())}\r\n"
                "Connection: close\r\n\r\n"
                f"{body}"
            )
            writer.write(response.encode())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def serve() -> None:
        server = await asyncio.start_server(handle, "0.0.0.0", port)
        try:
            async with server:
                await asyncio.wait_for(server.serve_forever(), timeout=duration)
        except asyncio.TimeoutError:
            # Normal shutdown after duration
            pass
        finally:
            server.close()
            await server.wait_closed()

    _telemetry_server_task = asyncio.create_task(serve())
    return f"Telemetry web dashboard running at http://localhost:{port} for {duration}s"


async def start_telemetry_dashboard(
    ctx: Context,
    port: int = 9999,
    duration_seconds: int = 300,
) -> str:
    """Start a lightweight web server that streams telemetry JSON for external dashboards.

    The dashboard provides:
    - Real-time agent status cards with SSE updates
    - Event stream showing notifications and activities
    - Action buttons for focusing panes and sending commands via API calls
    - Dark terminal theme matching iTerm2 aesthetic

    Args:
        port: Port to run the telemetry server on (default: 9999)
        duration_seconds: How long to keep the server running (default: 300, 0 = indefinitely)
    """
    # Pull dependencies from lifespan context — do NOT use module-global lazy imports
    # because launching via `python -m iterm_mcpy.fastmcp_server` creates two module
    # instances (one as `__main__`, one as `iterm_mcpy.fastmcp_server`) and the latter
    # has uninitialized globals.
    lifespan = ctx.request_context.lifespan_context
    telemetry: TelemetryEmitter = lifespan["telemetry"]
    terminal: ItermTerminal = lifespan["terminal"]
    notification_manager = lifespan["notification_manager"]
    logger = lifespan["logger"]

    try:
        message = await start_dashboard(
            telemetry=telemetry,
            terminal=terminal,
            notification_manager=notification_manager,
            port=port,
            duration=duration_seconds,
        )
        logger.info(message)

        # Include setup instructions
        setup_msg = (
            f"\n\nOpen the dashboard at: http://localhost:{port}\n\n"
            f"The dashboard uses API calls for agent control:\n"
            f"  - /api/focus?agent=<name> - Focus an agent's pane\n"
            f"  - /api/send?agent=<name>&command=<cmd> - Send command to agent"
        )

        return json.dumps({
            "status": "started",
            "message": message,
            "url": f"http://localhost:{port}",
            "setup": setup_msg,
        }, indent=2)
    except Exception as e:
        logger.error(f"Error starting telemetry server: {e}")
        return json.dumps({"error": str(e)}, indent=2)


def register(mcp):
    """Register telemetry tools with the FastMCP instance."""
    mcp.tool()(start_telemetry_dashboard)
