"""SP2 `telemetry_v2` action tool — Task 13/14.

Replaces the legacy ``start_telemetry_dashboard`` tool. Exposes the
dashboard lifecycle as a method-semantic action:

    POST+TRIGGER /telemetry → start the dashboard (analogue of legacy tool).
    DELETE      /telemetry  → stop the dashboard (best-effort; cancels the
                              cached asyncio task and calls
                              :func:`core.dashboard.stop_dashboard` if
                              available).

Any other (op, definer) pair returns an err envelope.
"""
from typing import Optional

from mcp.server.fastmcp import Context

from core.definer_verbs import DefinerError, resolve_op
from iterm_mcpy.responses import err_envelope, ok_envelope
from iterm_mcpy.tools import telemetry as telemetry_module


async def _start_dashboard(ctx: Context, port: int, duration_seconds: int):
    """Start the telemetry dashboard — mirrors the legacy start body."""
    # Lazy import so the test suite can stub out ``core.dashboard``.
    from core.dashboard import start_dashboard

    lifespan = ctx.request_context.lifespan_context
    telemetry = lifespan["telemetry"]
    terminal = lifespan["terminal"]
    notification_manager = lifespan["notification_manager"]
    logger = lifespan["logger"]

    message = await start_dashboard(
        telemetry=telemetry,
        terminal=terminal,
        notification_manager=notification_manager,
        port=port,
        duration=duration_seconds,
    )
    logger.info(message)

    setup_msg = (
        f"\n\nOpen the dashboard at: http://localhost:{port}\n\n"
        f"The dashboard uses API calls for agent control:\n"
        f"  - /api/focus?agent=<name> - Focus an agent's pane\n"
        f"  - /api/send?agent=<name>&command=<cmd> - Send command to agent"
    )

    return {
        "status": "started",
        "message": message,
        "url": f"http://localhost:{port}",
        "setup": setup_msg,
    }


async def _stop_dashboard(ctx: Context):
    """Stop the telemetry dashboard.

    Cancels the legacy-module task (if one exists) and calls
    :func:`core.dashboard.stop_dashboard` to tear down the server.
    """
    logger = ctx.request_context.lifespan_context["logger"]

    cancelled_task = False
    task = getattr(telemetry_module, "_telemetry_server_task", None)
    if task is not None and not task.done():
        task.cancel()
        cancelled_task = True

    try:
        from core.dashboard import stop_dashboard
        await stop_dashboard()
        stopped_server = True
    except Exception as e:
        logger.warning(f"telemetry_v2 stop: stop_dashboard raised: {e}")
        stopped_server = False

    return {
        "status": "stopped",
        "cancelled_task": cancelled_task,
        "stopped_server": stopped_server,
    }


async def telemetry_v2(
    ctx: Context,
    op: str = "POST",
    definer: Optional[str] = None,
    port: int = 9999,
    duration_seconds: int = 300,
) -> str:
    """Telemetry dashboard lifecycle.

    POST+TRIGGER (or op="start"): start the dashboard. Mirrors the legacy
    ``start_telemetry_dashboard`` tool — launches a lightweight web server
    on ``port`` that streams telemetry JSON for ``duration_seconds`` (0 =
    indefinitely).

    DELETE (or op="stop"): stop the dashboard. Best-effort — cancels the
    module-level task and calls :func:`core.dashboard.stop_dashboard`.

    Args:
        op: HTTP method or friendly verb. "start"/"trigger" resolve to
            POST+TRIGGER; "stop"/"delete" resolve to DELETE.
        definer: Explicit definer — must be TRIGGER for POST (ignored for
            DELETE).
        port: Port for the telemetry server (POST only, default 9999).
        duration_seconds: How long to keep the server running (POST only,
            default 300, 0 = indefinitely).
    """
    try:
        resolution = resolve_op(op, definer)
    except DefinerError as e:
        return err_envelope(method=op.upper(), error=str(e))

    method = resolution.method
    resolved_definer = resolution.definer

    if method == "POST":
        if resolved_definer != "TRIGGER":
            return err_envelope(
                method="POST", definer=resolved_definer,
                error=(
                    f"telemetry_v2 POST requires definer=TRIGGER "
                    f"(got {resolved_definer})"
                ),
            )
        try:
            data = await _start_dashboard(ctx, port=port, duration_seconds=duration_seconds)
            return ok_envelope(method="POST", definer="TRIGGER", data=data)
        except Exception as e:
            return err_envelope(method="POST", definer="TRIGGER", error=str(e))

    if method == "DELETE":
        try:
            data = await _stop_dashboard(ctx)
            return ok_envelope(method="DELETE", data=data)
        except Exception as e:
            return err_envelope(method="DELETE", error=str(e))

    return err_envelope(
        method=method,
        definer=resolved_definer,
        error=f"telemetry_v2 only supports POST+TRIGGER or DELETE (got {method})",
    )


def register(mcp):
    """Register the telemetry_v2 action tool."""
    mcp.tool(name="telemetry_v2")(telemetry_v2)
