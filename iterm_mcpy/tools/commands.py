"""Command execution tools.

Provides array-based tools for writing commands to sessions, reading output,
sending cascading messages, and resolving/focusing panes by team/agent
hierarchy.
"""

import json
import logging
from typing import Dict, List, Optional

from mcp.server.fastmcp import Context

from core.agents import AgentRegistry, CascadingMessage, SendTarget
from core.models import (
    CascadeMessageRequest,
    ReadSessionsRequest,
    WriteToSessionsRequest,
)
from core.terminal import ItermTerminal

from iterm_mcpy.helpers import (
    execute_cascade_request,
    execute_read_request,
    execute_write_request,
)


def _ensure_model(model_cls, payload):
    """Validate or coerce an incoming payload into a Pydantic model."""
    if isinstance(payload, model_cls):
        return payload
    return model_cls.model_validate(payload)


async def write_to_sessions(request: WriteToSessionsRequest, ctx: Context) -> str:
    """Write messages to one or more sessions using the gRPC-aligned schema."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
    notification_manager = ctx.request_context.lifespan_context.get("notification_manager")
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        write_request = _ensure_model(WriteToSessionsRequest, request)
        result = await execute_write_request(
            write_request,
            terminal,
            agent_registry,
            logger,
            lock_manager=lock_manager,
            notification_manager=notification_manager,
        )
        return result.model_dump_json(indent=2, exclude_none=True)
    except Exception as e:
        logger.error(f"Error in write_to_sessions: {e}")
        return f"Error: {e}"


async def read_sessions(request: ReadSessionsRequest, ctx: Context) -> str:
    """Read output from one or more sessions."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        read_request = _ensure_model(ReadSessionsRequest, request)
        result = await execute_read_request(read_request, terminal, agent_registry, logger)
        return result.model_dump_json(indent=2, exclude_none=True)
    except Exception as e:
        logger.error(f"Error in read_sessions: {e}")
        return f"Error: {e}"


async def _deliver_cascade(
    terminal: ItermTerminal,
    agent_registry: AgentRegistry,
    cascade: CascadingMessage,
    skip_duplicates: bool,
    execute: bool,
    logger: logging.Logger,
) -> str:
    """Send a cascading message and return serialized results."""

    try:
        message_targets = agent_registry.resolve_cascade_targets(cascade)

        results = []
        delivered = 0
        skipped = 0

        for message, agent_names in message_targets.items():
            if skip_duplicates:
                agent_names = agent_registry.filter_unsent_recipients(message, agent_names)

            actually_delivered = []

            for agent_name in agent_names:
                agent = agent_registry.get_agent(agent_name)
                if not agent:
                    continue

                session = await terminal.get_session_by_id(agent.session_id)
                if not session:
                    results.append({
                        "agent": agent_name,
                        "delivered": False,
                        "skipped_reason": "session_not_found"
                    })
                    skipped += 1
                    continue

                await session.send_text(message, execute=execute)
                agent_registry.record_message_sent(message, [agent_name])
                delivered += 1
                actually_delivered.append(agent_name)

            if not actually_delivered:
                skipped += len(agent_names)

            results.append({
                "message": message,
                "targets": agent_names,
                "delivered": actually_delivered
            })

        logger.info(f"Delivered {delivered} cascading messages ({skipped} skipped)")

        return json.dumps({
            "results": results,
            "delivered": delivered,
            "skipped": skipped
        }, indent=2)
    except Exception as e:
        logger.error(f"Error sending cascading message: {e}")
        return f"Error: {e}"


async def send_cascade_message(request: CascadeMessageRequest, ctx: Context) -> str:
    """Send cascading messages to agents/teams."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        cascade_request = _ensure_model(CascadeMessageRequest, request)
        result = await execute_cascade_request(cascade_request, terminal, agent_registry, logger)
        return result.model_dump_json(indent=2, exclude_none=True)
    except Exception as e:
        logger.error(f"Error in send_cascade_message: {e}")
        return f"Error: {e}"


async def select_panes_by_hierarchy(
    ctx: Context,
    targets: List[Dict[str, Optional[str]]],
    set_active: bool = True,
) -> str:
    """Resolve panes by team/agent hierarchy and optionally focus the first.

    Args:
        targets: List of target dicts using SendTarget fields (team/agent)
        set_active: Whether to set the first resolved session as active
    """

    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    results = []
    resolved_sessions = []

    for target in targets:
        send_target = SendTarget(**target)
        team = send_target.team
        agent_name = send_target.agent

        if agent_name:
            agent = agent_registry.get_agent(agent_name)
            if not agent:
                results.append({"agent": agent_name, "team": team, "error": "unknown_agent"})
                continue

            if team and not agent.is_member_of(team):
                results.append({"agent": agent_name, "team": team, "error": "agent_not_in_team"})
                continue

            session = await terminal.get_session_by_id(agent.session_id)
            if not session:
                results.append({"agent": agent_name, "team": team, "error": "session_not_found"})
                continue

            resolved_sessions.append(session)
            results.append({
                "agent": agent_name,
                "team": team,
                "session_id": session.id,
                "session_name": session.name
            })
            continue

        if team:
            team_agents = agent_registry.list_agents(team=team)
            if not team_agents:
                results.append({"team": team, "error": "team_has_no_agents"})
                continue

            for agent in team_agents:
                session = await terminal.get_session_by_id(agent.session_id)
                if session:
                    resolved_sessions.append(session)
                    results.append({
                        "agent": agent.name,
                        "team": team,
                        "session_id": session.id,
                        "session_name": session.name
                    })

    if set_active and resolved_sessions:
        agent_registry.active_session = resolved_sessions[0].id
        logger.info(f"Active session set via hierarchy: {resolved_sessions[0].name}")

    return json.dumps({
        "resolved": results,
        "active_session": resolved_sessions[0].id if set_active and resolved_sessions else None
    }, indent=2)


async def send_hierarchical_message(
    ctx: Context,
    targets: List[Dict[str, Optional[str]]],
    broadcast: Optional[str] = None,
    skip_duplicates: bool = True,
    execute: bool = True,
) -> str:
    """Send cascading messages using hierarchical SendTarget specs."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    send_targets = [SendTarget(**t) for t in targets]

    cascade = CascadingMessage(
        broadcast=broadcast,
        teams={},
        agents={},
    )

    for target in send_targets:
        if target.team and target.agent:
            agent_obj = agent_registry.get_agent(target.agent)
            if not agent_obj or not agent_obj.is_member_of(target.team):
                logger.error(f"Agent '{target.agent}' is not a member of team '{target.team}'. Skipping.")
                continue
            cascade.agents[target.agent] = target.message or broadcast or ""
        elif target.agent:
            cascade.agents[target.agent] = target.message or broadcast or ""
        elif target.team:
            cascade.teams[target.team] = target.message or broadcast or ""

    return await _deliver_cascade(
        terminal=terminal,
        agent_registry=agent_registry,
        cascade=cascade,
        skip_duplicates=skip_duplicates,
        execute=execute,
        logger=logger,
    )


def register(mcp):
    """Register command execution tools with the FastMCP instance."""
    mcp.tool()(write_to_sessions)
    mcp.tool()(read_sessions)
    mcp.tool()(send_cascade_message)
    mcp.tool()(select_panes_by_hierarchy)
    mcp.tool()(send_hierarchical_message)
