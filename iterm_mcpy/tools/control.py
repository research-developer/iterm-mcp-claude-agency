"""Control and status tools.

Provides tools for sending control characters and special keys to sessions,
and for querying per-session status (processing state, monitoring state,
lock/tag information).
"""

import json
from typing import Optional

from mcp.server.fastmcp import Context

from core.models import (
    SessionTarget,
    SetActiveSessionRequest,
)

from iterm_mcpy.helpers import resolve_session, resolve_target_sessions


def _ensure_model(model_cls, payload):
    """Validate or coerce an incoming payload into a Pydantic model."""
    if isinstance(payload, model_cls):
        return payload
    return model_cls.model_validate(payload)


async def send_control_character(control_char: str, target: SessionTarget, ctx: Context) -> str:
    """Send a control character to session(s)."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        target_model = _ensure_model(SessionTarget, target)
        sessions = await resolve_target_sessions(terminal, agent_registry, [target_model])
        if not sessions:
            return "No matching sessions found"

        results = []
        for session in sessions:
            await session.send_control_character(control_char)
            results.append(f"{session.name}: Ctrl+{control_char.upper()} sent")

        logger.info(f"Sent Ctrl+{control_char.upper()} to {len(sessions)} sessions")
        return "\n".join(results)
    except Exception as e:
        logger.error(f"Error sending control character: {e}")
        return f"Error: {e}"


async def send_special_key(
    key: str,
    ctx: Context,
    session_id: Optional[str] = None,
    agent: Optional[str] = None,
    name: Optional[str] = None
) -> str:
    """Send a special key to a session.

    Args:
        key: Special key ('enter', 'tab', 'escape', 'up', 'down', etc.)
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
        await session.send_special_key(key)
        logger.info(f"Sent '{key}' to session: {session.name}")
        return f"Special key '{key}' sent to session: {session.name}"
    except Exception as e:
        logger.error(f"Error sending special key: {e}")
        return f"Error: {e}"


async def check_session_status(request: SetActiveSessionRequest, ctx: Context) -> str:
    """Check status of a session."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        req = _ensure_model(SetActiveSessionRequest, request)
        sessions = await resolve_session(
            terminal,
            agent_registry,
            session_id=req.session_id,
            name=req.name,
            agent=req.agent,
        )
        if not sessions:
            return "No matching session found"

        session = sessions[0]
        agent_obj = agent_registry.get_agent_by_session(session.id)

        status = {
            "name": session.name,
            "id": session.id,
            "persistent_id": session.persistent_id,
            "agent": agent_obj.name if agent_obj else None,
            "teams": agent_obj.teams if agent_obj else [],
            "is_processing": getattr(session, "is_processing", False),
            "is_monitoring": getattr(session, "is_monitoring", False),
            "is_active": session.id == agent_registry.active_session,
        }

        # Add tag and lock info
        if lock_manager:
            lock_info = lock_manager.get_lock_info(session.id)
            status["tags"] = lock_manager.get_tags(session.id)
            status["locked"] = lock_info is not None
            status["locked_by"] = lock_info.owner if lock_info else None
            status["locked_at"] = lock_info.locked_at.isoformat() if lock_info else None
            status["pending_access_requests"] = len(lock_info.pending_requests) if lock_info else 0

        logger.info(f"Status for session: {session.name}")
        return json.dumps(status, indent=2)
    except Exception as e:
        logger.error(f"Error checking session status: {e}")
        return f"Error: {e}"


def register(mcp):
    """Register control & status tools with the FastMCP instance."""
    mcp.tool()(send_control_character)
    mcp.tool()(send_special_key)
    mcp.tool()(check_session_status)
