"""Telemetry dashboard tool.

Provides the start_telemetry_dashboard tool and a helper that starts a
lightweight HTTP server streaming telemetry JSON.
"""

import asyncio
import json

from mcp.server.fastmcp import Context

from core.dashboard import start_dashboard
from utils.telemetry import TelemetryEmitter


async def _start_telemetry_server(port: int, duration: int = 300) -> str:
    """Start a lightweight HTTP server that streams telemetry JSON."""
    # Import lazily to access live module-level globals set during lifespan.
    from iterm_mcpy import fastmcp_server as _srv

    if _srv._telemetry is None or _srv._terminal is None:
        raise RuntimeError("Telemetry not initialized")

    if _srv._telemetry_server_task:
        _srv._telemetry_server_task.cancel()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await _srv._terminal.get_sessions()
            payload = _srv._telemetry.dashboard_state(_srv._terminal)
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

    _srv._telemetry_server_task = asyncio.create_task(serve())
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
    # Import lazily to access live module-level globals set during lifespan.
    from iterm_mcpy import fastmcp_server as _srv

    telemetry: TelemetryEmitter = ctx.request_context.lifespan_context["telemetry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        message = await start_dashboard(
            telemetry=telemetry,
            terminal=_srv._terminal,
            notification_manager=_srv._notification_manager,
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
