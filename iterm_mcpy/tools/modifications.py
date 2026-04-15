"""Session modification tools.

Provides the modify_sessions tool for applying appearance (colors, badge),
state (active/focus), and process-control (suspend/resume) changes to one
or more sessions in a single call.
"""

import logging
from typing import List, Optional

from mcp.server.fastmcp import Context

from core.agents import AgentRegistry
from core.models import (
    ModificationResult,
    ModifySessionsRequest,
    ModifySessionsResponse,
    SessionModification,
)
from core.session import ItermSession
from core.tags import FocusCooldownManager
from core.terminal import ItermTerminal

from iterm_mcpy.helpers import resolve_session


def _ensure_model(model_cls, payload):
    """Validate or coerce an incoming payload into a Pydantic model."""
    if isinstance(payload, model_cls):
        return payload
    return model_cls.model_validate(payload)


async def _apply_session_modification(
    session: ItermSession,
    modification: SessionModification,
    terminal: ItermTerminal,
    agent_registry: AgentRegistry,
    logger: logging.Logger,
    focus_cooldown: Optional[FocusCooldownManager] = None,
) -> ModificationResult:
    """Apply modification settings to a single session."""
    agent = agent_registry.get_agent_by_session(session.id)
    agent_name = agent.name if agent else None
    result = ModificationResult(
        session_id=session.id,
        session_name=session.name,
        agent=agent_name,
    )
    changes = []

    try:
        # Handle set_active
        if modification.set_active:
            agent_registry.active_session = session.id
            changes.append("set_active")

        # Handle suspend/resume (with toggle fallback if both are set)
        if modification.suspend and modification.resume:
            # Both set: toggle based on current state
            if session.is_suspended:
                try:
                    await session.resume()
                    changes.append("toggle->resume")
                    logger.info(f"Toggled session {session.name}: resumed (was suspended)")
                except RuntimeError as e:
                    result.error = str(e)
                    logger.warning(f"Could not resume session {session.name}: {e}")
                    return result
            else:
                suspend_agent = modification.suspend_by or agent_name
                try:
                    await session.suspend(agent=suspend_agent)
                    changes.append(f"toggle->suspend (by {suspend_agent or 'unknown'})")
                    logger.info(f"Toggled session {session.name}: suspended (was running)")
                except RuntimeError as e:
                    result.error = str(e)
                    logger.warning(f"Could not suspend session {session.name}: {e}")
                    return result
        elif modification.suspend:
            suspend_agent = modification.suspend_by or agent_name
            try:
                await session.suspend(agent=suspend_agent)
                changes.append(f"suspend (by {suspend_agent or 'unknown'})")
                logger.info(f"Suspended session {session.name} by agent {suspend_agent}")
            except RuntimeError as e:
                result.error = str(e)
                logger.warning(f"Could not suspend session {session.name}: {e}")
                return result
        elif modification.resume:
            try:
                await session.resume()
                changes.append("resume")
                logger.info(f"Resumed session {session.name}")
            except RuntimeError as e:
                result.error = str(e)
                logger.warning(f"Could not resume session {session.name}: {e}")
                return result

        # Handle focus with cooldown check
        if modification.focus:
            if focus_cooldown:
                allowed, blocking_agent, remaining = focus_cooldown.check_cooldown(
                    session.id, agent_name
                )
                if not allowed:
                    result.error = (
                        f"Focus cooldown active: {remaining:.1f}s remaining. "
                        f"Last focus by agent '{blocking_agent or 'unknown'}'. "
                        f"Wait or use the same agent."
                    )
                    logger.warning(f"Focus blocked for {session.name}: cooldown {remaining:.1f}s")
                    return result

            await terminal.focus_session(session.id)
            changes.append("focus")

            # Record the focus event for cooldown
            if focus_cooldown:
                focus_cooldown.record_focus(session.id, agent_name)
                logger.debug(f"Recorded focus event for {session.name} by {agent_name}")

        # Reset colors first if requested
        if modification.reset:
            await session.reset_colors()
            changes.append("reset_colors")

        # Apply background color
        if modification.background_color:
            c = modification.background_color
            await session.set_background_color(c.red, c.green, c.blue, c.alpha)
            changes.append(f"background_color=RGB({c.red},{c.green},{c.blue})")

        # Apply tab color
        if modification.tab_color:
            c = modification.tab_color
            enabled = modification.tab_color_enabled if modification.tab_color_enabled is not None else True
            await session.set_tab_color(c.red, c.green, c.blue, enabled)
            changes.append(f"tab_color=RGB({c.red},{c.green},{c.blue})")
        elif modification.tab_color_enabled is not None:
            # Toggle the enable flag without overwriting the currently-configured color.
            await session.set_tab_color_enabled(modification.tab_color_enabled)
            changes.append(f"tab_color_enabled={modification.tab_color_enabled}")

        # Apply cursor color
        if modification.cursor_color:
            c = modification.cursor_color
            await session.set_cursor_color(c.red, c.green, c.blue)
            changes.append(f"cursor_color=RGB({c.red},{c.green},{c.blue})")

        # Apply badge
        if modification.badge is not None:
            await session.set_badge(modification.badge)
            changes.append(f"badge='{modification.badge}'")

        result.success = True
        result.changes = changes
        logger.info(f"Applied modifications to {session.name}: {', '.join(changes)}")

    except Exception as e:
        result.error = str(e)
        logger.error(f"Error applying modifications to {session.name}: {e}")

    return result


async def modify_sessions(
    request: ModifySessionsRequest,
    ctx: Context
) -> str:
    """Modify multiple terminal sessions (appearance, focus, active state, suspend/resume).

    This consolidated tool handles all session modifications in a single call:
    - Visual appearance: background color, tab color, cursor color, badge
    - Session state: set as active session, bring to foreground (focus)
    - Process control: suspend (Ctrl+Z) or resume (fg) running processes

    Each modification entry specifies a target session (by agent, name, or session_id)
    and the properties to modify.

    Example request:
    {
        "modifications": [
            {
                "agent": "claude-1",
                "focus": true,
                "set_active": true,
                "tab_color": {"red": 100, "green": 200, "blue": 255},
                "badge": "🤖 Claude-1"
            },
            {
                "agent": "claude-2",
                "background_color": {"red": 40, "green": 30, "blue": 30},
                "tab_color": {"red": 255, "green": 150, "blue": 100}
            },
            {
                "agent": "claude-3",
                "reset": true
            },
            {
                "agent": "long-running-agent",
                "suspend": true,
                "suspend_by": "orchestrator"
            },
            {
                "agent": "paused-agent",
                "resume": true
            }
        ]
    }
    """
    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]
    focus_cooldown = ctx.request_context.lifespan_context.get("focus_cooldown")

    try:
        req = _ensure_model(ModifySessionsRequest, request)
        results: List[ModificationResult] = []

        for modification in req.modifications:
            # Resolve the target session
            sessions = await resolve_session(
                terminal,
                agent_registry,
                session_id=modification.session_id,
                name=modification.name,
                agent=modification.agent,
            )

            if not sessions:
                results.append(ModificationResult(
                    session_id="",
                    session_name=None,
                    agent=modification.agent,
                    success=False,
                    error=f"No session found for target: agent={modification.agent}, name={modification.name}, id={modification.session_id}"
                ))
                continue

            session = sessions[0]
            result = await _apply_session_modification(
                session, modification, terminal, agent_registry, logger, focus_cooldown
            )
            results.append(result)

        success_count = sum(1 for r in results if r.success)
        error_count = sum(1 for r in results if not r.success)

        response = ModifySessionsResponse(
            results=results,
            success_count=success_count,
            error_count=error_count,
        )

        logger.info(f"Modified sessions: {success_count} succeeded, {error_count} failed")
        return response.model_dump_json(indent=2, exclude_none=True)

    except Exception as e:
        logger.error(f"Error in modify_sessions: {e}")
        return f"Error: {e}"


def register(mcp):
    """Register session modification tools with the FastMCP instance."""
    mcp.tool()(modify_sessions)
