"""Session monitoring tools.

Provides tools for starting and stopping real-time monitoring of terminal
sessions. When active, monitoring captures terminal output changes and can
trigger workflow events via pattern subscriptions on the EventBus.
"""

import asyncio
import time
from typing import Optional

from mcp.server.fastmcp import Context

from core.flows import EventBus

from iterm_mcpy.helpers import resolve_session


async def _start_monitoring_core(
    session,
    event_bus: Optional[EventBus],
    logger,
    *,
    enable_event_bus: bool = True,
    settle_delay: float = 2.0,
) -> bool:
    """Start monitoring on a single resolved session.

    Wires up the EventBus callback (when `enable_event_bus` is true) and kicks
    off polling on the session. Idempotent: calling on an already-monitored
    session is a no-op (returns True).

    Args:
        session: The resolved ItermSession to monitor.
        event_bus: EventBus to route output to. If None (or if
            enable_event_bus is False), monitoring still starts but no
            callback is attached — output is not published to the bus.
        logger: Logger for debug/info/error messages.
        enable_event_bus: Whether to attach a callback that routes output to
            the EventBus (for pattern subscriptions). Default True. Silently
            skipped when event_bus is None.
        settle_delay: Seconds to wait after starting before verifying state.

    Returns:
        True when monitoring is active on the session, False if start failed.
    """
    if session.is_monitoring:
        return True

    if enable_event_bus and event_bus is not None:
        async def event_bus_callback(output: str) -> None:
            """Route terminal output to EventBus for pattern matching."""
            try:
                triggered = await event_bus.process_terminal_output(
                    session_id=session.id,
                    output=output,
                )
                if triggered:
                    logger.debug(f"Pattern subscriptions triggered: {triggered}")

                await event_bus.trigger(
                    event_name="terminal_output",
                    payload={
                        "session_id": session.id,
                        "session_name": session.name,
                        "output": output,
                        "timestamp": time.time(),
                    },
                    source=f"session:{session.name}",
                )
            except Exception as e:
                logger.error(f"Error in event bus callback: {e}")

        if hasattr(session, "_event_bus_callback") and session._event_bus_callback:
            session.remove_monitor_callback(session._event_bus_callback)
            logger.debug(
                f"Removed existing event bus callback for session: {session.name}"
            )

        session.add_monitor_callback(event_bus_callback)
        session._event_bus_callback = event_bus_callback

    await session.start_monitoring(update_interval=0.2)
    if settle_delay > 0:
        await asyncio.sleep(settle_delay)

    return bool(session.is_monitoring)


async def _stop_monitoring_core(session, logger) -> bool:
    """Stop monitoring on a single resolved session.

    Detaches any previously-attached EventBus callback and stops the session's
    polling task. Idempotent: returns False if the session was not monitored.

    Returns:
        True if monitoring was active and was stopped, False otherwise.
    """
    if not session.is_monitoring:
        return False

    if hasattr(session, "_event_bus_callback") and session._event_bus_callback:
        session.remove_monitor_callback(session._event_bus_callback)
        session._event_bus_callback = None

    await session.stop_monitoring()
    logger.info(f"Stopped monitoring for session: {session.name}")
    return True


async def start_monitoring_session(
    ctx: Context,
    session_id: Optional[str] = None,
    agent: Optional[str] = None,
    name: Optional[str] = None,
    enable_event_bus: bool = True
) -> str:
    """Start real-time monitoring for a session.

    When monitoring is active, terminal output changes are captured and can
    trigger workflow events through pattern subscriptions. Use
    subscribe_to_output_pattern to set up patterns that trigger events.

    Args:
        session_id: Target session ID (optional)
        agent: Target agent name (optional)
        name: Target session name (optional)
        enable_event_bus: If True, route output to EventBus for pattern matching
    """
    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    event_bus: EventBus = ctx.request_context.lifespan_context["event_bus"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        sessions = await resolve_session(terminal, agent_registry, session_id, name, agent)
        if not sessions:
            return "No matching session found"

        session = sessions[0]

        if session.is_monitoring:
            return f"Session {session.name} is already being monitored"

        started = await _start_monitoring_core(
            session, event_bus, logger, enable_event_bus=enable_event_bus
        )
        if started:
            logger.info(f"Started monitoring for session: {session.name} (event_bus={enable_event_bus})")
            return f"Started monitoring for session: {session.name} (event_bus integration: {enable_event_bus})"
        return f"Failed to start monitoring for session: {session.name}"
    except Exception as e:
        logger.error(f"Error starting monitoring: {e}")
        return f"Error: {e}"


async def stop_monitoring_session(
    ctx: Context,
    session_id: Optional[str] = None,
    agent: Optional[str] = None,
    name: Optional[str] = None
) -> str:
    """Stop monitoring for a session.

    Args:
        session_id: Target session ID (optional)
        agent: Target agent name (optional)
        name: Target session name (optional)
    """
    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        sessions = await resolve_session(terminal, agent_registry, session_id, name, agent)
        if not sessions:
            return "No matching session found"

        session = sessions[0]

        if not session.is_monitoring:
            return f"Session {session.name} is not being monitored"

        await _stop_monitoring_core(session, logger)
        return f"Stopped monitoring for session: {session.name}"
    except Exception as e:
        logger.error(f"Error stopping monitoring: {e}")
        return f"Error: {e}"


def register(mcp):
    """Register monitoring tools with the FastMCP instance."""
    mcp.tool()(start_monitoring_session)
    mcp.tool()(stop_monitoring_session)
