"""Shared helper functions for iTerm MCP server.

This module contains helpers used across multiple tool modules in the
iterm_mcpy package. Extracted from fastmcp_server.py to avoid circular
imports once tools are split into their own modules.

Contents:
    - resolve_session / resolve_target_sessions: Convert session identifiers
      (id/name/agent/team) into concrete ItermSession instances.
    - execute_create_sessions: Create a new window/layout, register agents,
      apply team colors, and optionally launch agent CLIs.
    - execute_write_request / execute_read_request / execute_cascade_request:
      Core read/write/cascade tool implementations used by the MCP tool
      wrappers in fastmcp_server.py.
    - check_condition / notify_lock_request: Small helpers relied on by the
      write helper above; kept here so helpers.py has no dependency on
      fastmcp_server.py.
"""

import asyncio
import logging
import re
from typing import Any, List, Optional

import iterm2

from core.agents import AgentRegistry, CascadingMessage
from core.layouts import LayoutManager, LayoutType
from core.models import (
    AGENT_CLI_COMMANDS,
    CascadeMessageRequest,
    CascadeMessageResponse,
    CascadeResult,
    CreateSessionsRequest,
    CreateSessionsResponse,
    CreatedSession,
    ReadSessionsRequest,
    ReadSessionsResponse,
    ReadTarget,
    SessionMessage,
    SessionOutput,
    SessionTarget,
    WriteResult,
    WriteToSessionsRequest,
    WriteToSessionsResponse,
)
from core.profiles import ProfileManager
from core.session import ItermSession
from core.tags import SessionTagLockManager
from core.terminal import ItermTerminal
from utils.otel import add_span_attributes, trace_operation


async def resolve_session(
    terminal: ItermTerminal,
    agent_registry: AgentRegistry,
    session_id: Optional[str] = None,
    name: Optional[str] = None,
    agent: Optional[str] = None,
    team: Optional[str] = None
) -> List[ItermSession]:
    """Resolve session identifiers to actual sessions.

    Returns list of sessions matching the criteria.
    If no criteria provided, returns the active session.
    """
    sessions = []

    # If team specified, get all agents in team (optionally filtered by agent)
    if team:
        team_agents = agent_registry.list_agents(team=team)

        if agent:
            team_agents = [a for a in team_agents if a.name == agent]
            if not team_agents:
                return []

        for a in team_agents:
            session = await terminal.get_session_by_id(a.session_id)
            if session:
                sessions.append(session)
        return sessions

    # If agent specified, get that agent's session
    if agent:
        a = agent_registry.get_agent(agent)
        if a:
            session = await terminal.get_session_by_id(a.session_id)
            if session:
                sessions.append(session)
        return sessions

    # If session_id specified
    if session_id:
        session = await terminal.get_session_by_id(session_id)
        if session:
            sessions.append(session)
        return sessions

    # If name specified
    if name:
        session = await terminal.get_session_by_name(name)
        if session:
            sessions.append(session)
        return sessions

    # Default: use active session
    active_session_id = agent_registry.active_session
    if active_session_id:
        session = await terminal.get_session_by_id(active_session_id)
        if session:
            sessions.append(session)

    return sessions


def check_condition(content: str, condition: Optional[str]) -> bool:
    """Check if content matches a regex condition."""
    if not condition:
        return True
    try:
        return bool(re.search(condition, content))
    except re.error:
        return False


async def notify_lock_request(
    notification_manager: Optional["NotificationManager"],
    owner: Optional[str],
    session_identifier: str,
    requester: Optional[str],
    action_hint: str = "Approve or unlock to allow access",
) -> None:
    """Send a standardized lock access notification."""
    if notification_manager and owner:
        await notification_manager.add_simple(
            owner,
            "blocked",
            f"Session {session_identifier} locked",
            context=f"Request by {requester or 'unknown'}",
            action_hint=action_hint,
        )


async def resolve_target_sessions(
    terminal: ItermTerminal,
    agent_registry: AgentRegistry,
    targets: Optional[List[SessionTarget]] = None,
) -> List[ItermSession]:
    """Resolve a list of session targets to unique sessions."""

    if not targets:
        return await resolve_session(terminal, agent_registry)

    sessions: List[ItermSession] = []
    seen = set()

    for target in targets:
        resolved = await resolve_session(
            terminal,
            agent_registry,
            session_id=target.session_id,
            name=target.name,
            agent=target.agent,
            team=target.team,
        )
        for session in resolved:
            if session.id not in seen:
                sessions.append(session)
                seen.add(session.id)

    return sessions


@trace_operation("execute_create_sessions")
async def execute_create_sessions(
    create_request: CreateSessionsRequest,
    terminal: ItermTerminal,
    layout_manager: LayoutManager,
    agent_registry: AgentRegistry,
    logger: logging.Logger,
    profile_manager: Optional[ProfileManager] = None,
) -> CreateSessionsResponse:
    """Create sessions based on a CreateSessionsRequest."""

    # Add span attributes
    add_span_attributes(
        layout=create_request.layout,
        session_count=len(create_request.sessions),
    )

    try:
        layout_type = LayoutType[create_request.layout.upper()]
    except KeyError as exc:
        raise ValueError(
            f"Invalid layout type: {create_request.layout}. Use one of: {[lt.name for lt in LayoutType]}"
        ) from exc

    # Save the currently focused session to restore after creation
    # (creating new windows steals focus from the user's current session)
    original_focused = await terminal.get_focused_session()
    original_session_id = original_focused.id if original_focused else None

    pane_names = [cfg.name or f"Session-{idx}" for idx, cfg in enumerate(create_request.sessions)]
    logger.info(f"Creating layout {layout_type.name} with panes: {pane_names}")

    sessions_map = await layout_manager.create_layout(layout_type=layout_type, pane_names=pane_names)
    created: List[CreatedSession] = []

    for pane_name, session_id in sessions_map.items():
        session = await terminal.get_session_by_id(session_id)
        if not session:
            continue

        config = next((cfg for cfg in create_request.sessions if cfg.name == pane_name), None)

        agent_name = None
        agent_type = None
        team_name = None
        if config and config.agent:
            teams = [config.team] if config.team else []
            team_name = config.team
            agent_registry.register_agent(
                name=config.agent,
                session_id=session.id,
                teams=teams,
            )
            agent_name = config.agent

            # Apply team profile colors if agent is in a team
            if team_name and profile_manager:
                team_profile = profile_manager.get_or_create_team_profile(team_name)
                profile_manager.save_profiles()
                # Apply the team's tab color to the session
                r, g, b = team_profile.color.to_rgb()
                try:
                    await session.session.async_set_profile_properties(
                        iterm2.LocalWriteOnlyProfile(
                            values={
                                "Tab Color": {
                                    "Red Component": r,
                                    "Green Component": g,
                                    "Blue Component": b,
                                    "Color Space": "sRGB"
                                }
                            }
                        )
                    )
                    logger.debug(f"Applied team '{team_name}' color to session {pane_name}")
                except Exception as e:
                    logger.warning(f"Could not apply team color to session: {e}")

        # Launch AI agent CLI if agent_type specified
        if config and config.agent_type:
            agent_type = config.agent_type
            cli_command = AGENT_CLI_COMMANDS.get(agent_type)
            if cli_command:
                logger.info(f"Launching {agent_type} agent in session {pane_name}: {cli_command}")
                await session.execute_command(cli_command)
            else:
                logger.warning(f"Unknown agent type: {agent_type}")
        elif config and config.command:
            # Only run custom command if no agent_type (agent_type takes precedence)
            await session.execute_command(config.command)

        if config and config.monitor:
            await session.start_monitoring(update_interval=0.2)

        created.append(
            CreatedSession(
                session_id=session.id,
                name=session.name,
                agent=agent_name,
                persistent_id=session.persistent_id,
            )
        )

    if created and not agent_registry.active_session:
        agent_registry.active_session = created[0].session_id

    # Restore focus to the original session to avoid disrupting the user
    if original_session_id:
        try:
            await terminal.focus_session(original_session_id)
            logger.debug(f"Restored focus to original session: {original_session_id}")
        except Exception as e:
            logger.warning(f"Could not restore focus to original session: {e}")

    return CreateSessionsResponse(sessions=created, window_id=create_request.window_id or "")


@trace_operation("execute_write_request")
async def execute_write_request(
    write_request: WriteToSessionsRequest,
    terminal: ItermTerminal,
    agent_registry: AgentRegistry,
    logger: logging.Logger,
    lock_manager: Optional[SessionTagLockManager] = None,
    notification_manager: Optional["NotificationManager"] = None,
) -> WriteToSessionsResponse:
    """Send messages according to WriteToSessionsRequest and return structured results."""

    # Add span attributes for message count
    add_span_attributes(
        message_count=len(write_request.messages),
        parallel=write_request.parallel,
        skip_duplicates=write_request.skip_duplicates,
    )

    results: List[WriteResult] = []
    active_agent = agent_registry.get_active_agent()
    requesting_agent = write_request.requesting_agent or (active_agent.name if active_agent else None)

    async def send_to_session(session: ItermSession, message: SessionMessage) -> WriteResult:
        result = WriteResult(session_id=session.id, session_name=session.name)

        if lock_manager:
            allowed, owner = lock_manager.check_permission(session.id, requesting_agent)
            if not allowed:
                result.skipped = True
                result.skipped_reason = "locked"
                safe_name = session.name or session.id
                await notify_lock_request(
                    notification_manager,
                    owner,
                    safe_name,
                    requesting_agent,
                )
                return result

        if message.condition:
            output = await session.get_screen_contents()
            if not check_condition(output, message.condition):
                result.skipped = True
                result.skipped_reason = "condition_not_met"
                return result

        agent = agent_registry.get_agent_by_session(session.id)
        if write_request.skip_duplicates and agent:
            if agent_registry.was_message_sent(message.content, agent.name):
                result.skipped = True
                result.skipped_reason = "duplicate"
                return result

        try:
            if message.execute:
                await session.execute_command(message.content, use_encoding=message.use_encoding)
            else:
                await session.send_text(message.content, execute=False)

            if agent:
                agent_registry.record_message_sent(message.content, [agent.name])
            result.success = True
        except Exception as exc:
            result.error = str(exc)

        return result

    tasks: List[Any] = []

    for message in write_request.messages:
        sessions = await resolve_target_sessions(terminal, agent_registry, message.targets)
        if not sessions:
            results.append(
                WriteResult(
                    session_id="",
                    session_name=None,
                    skipped=True,
                    skipped_reason="no_match",
                )
            )
            continue

        for session in sessions:
            if write_request.parallel:
                tasks.append(send_to_session(session, message))
            else:
                results.append(await send_to_session(session, message))

    if write_request.parallel and tasks:
        for response in await asyncio.gather(*tasks):
            results.append(response)

    sent_count = sum(1 for r in results if r.success)
    skipped_count = sum(1 for r in results if r.skipped)
    error_count = sum(1 for r in results if not r.success and not r.skipped)

    logger.info(f"Delivered {sent_count}/{len(results)} messages (skipped={skipped_count}, errors={error_count})")

    return WriteToSessionsResponse(
        results=results,
        sent_count=sent_count,
        skipped_count=skipped_count,
        error_count=error_count,
    )


@trace_operation("execute_read_request")
async def execute_read_request(
    read_request: ReadSessionsRequest,
    terminal: ItermTerminal,
    agent_registry: AgentRegistry,
    logger: logging.Logger,
) -> ReadSessionsResponse:
    """Read outputs according to ReadSessionsRequest."""

    # Add span attributes
    add_span_attributes(
        target_count=len(read_request.targets) if read_request.targets else 1,
        parallel=read_request.parallel,
        has_filter=bool(read_request.filter_pattern),
    )

    outputs: List[SessionOutput] = []

    async def read_from_session(session: ItermSession, max_lines: Optional[int]) -> SessionOutput:
        content = await session.get_screen_contents(max_lines=max_lines)

        if read_request.filter_pattern:
            try:
                pattern = re.compile(read_request.filter_pattern)
                lines = content.split("\n")
                filtered = [line for line in lines if pattern.search(line)]
                content = "\n".join(filtered)
            except re.error as regex_err:
                logger.error(f"Invalid filter_pattern '{read_request.filter_pattern}': {regex_err}")

        agent = agent_registry.get_agent_by_session(session.id)
        line_count = len(content.split("\n")) if content else 0
        truncated = bool(max_lines and line_count >= max_lines)

        return SessionOutput(
            session_id=session.id,
            name=session.name,
            agent=agent.name if agent else None,
            content=content,
            line_count=line_count,
            truncated=truncated,
        )

    tasks: List[Any] = []

    targets = read_request.targets or [ReadTarget()]
    for target in targets:
        sessions = await resolve_target_sessions(
            terminal,
            agent_registry,
            [
                SessionTarget(
                    session_id=target.session_id,
                    name=target.name,
                    agent=target.agent,
                    team=target.team,
                )
            ] if any([target.session_id, target.name, target.agent, target.team]) else None,
        )

        for session in sessions:
            if read_request.parallel:
                tasks.append(read_from_session(session, target.max_lines))
            else:
                outputs.append(await read_from_session(session, target.max_lines))

    if read_request.parallel and tasks:
        outputs.extend(await asyncio.gather(*tasks))

    logger.info(f"Read output from {len(outputs)} sessions")
    return ReadSessionsResponse(outputs=outputs, total_sessions=len(outputs))


@trace_operation("execute_cascade_request")
async def execute_cascade_request(
    cascade_request: CascadeMessageRequest,
    terminal: ItermTerminal,
    agent_registry: AgentRegistry,
    logger: logging.Logger,
) -> CascadeMessageResponse:
    """Execute a cascade delivery and return structured results."""

    # Add span attributes
    add_span_attributes(
        has_broadcast=bool(cascade_request.broadcast),
        team_count=len(cascade_request.teams),
        agent_count=len(cascade_request.agents),
        skip_duplicates=cascade_request.skip_duplicates,
    )

    cascade = CascadingMessage(
        broadcast=cascade_request.broadcast,
        teams=cascade_request.teams,
        agents=cascade_request.agents,
    )

    message_targets = agent_registry.resolve_cascade_targets(cascade)
    results: List[CascadeResult] = []
    delivered = 0
    skipped = 0

    for message, agent_names in message_targets.items():
        if cascade_request.skip_duplicates:
            agent_names = agent_registry.filter_unsent_recipients(message, agent_names)

        delivered_agents = []
        for agent_name in agent_names:
            agent = agent_registry.get_agent(agent_name)
            if not agent:
                continue

            session = await terminal.get_session_by_id(agent.session_id)
            if not session:
                results.append(
                    CascadeResult(
                        agent=agent_name,
                        session_id="",
                        message_type="unknown",
                        delivered=False,
                        skipped_reason="session_not_found",
                    )
                )
                skipped += 1
                continue

            message_type = "broadcast"
            if cascade_request.agents.get(agent_name, None) == message:
                message_type = "agent"
            elif any(agent.is_member_of(team) and cascade_request.teams.get(team) == message for team in cascade_request.teams):
                message_type = "team"

            if cascade_request.execute:
                await session.execute_command(message)
            else:
                await session.send_text(message, execute=False)

            results.append(
                CascadeResult(
                    agent=agent_name,
                    session_id=session.id,
                    message_type=message_type,
                    delivered=True,
                )
            )
            delivered_agents.append(agent_name)
            delivered += 1

        if delivered_agents:
            agent_registry.record_message_sent(message, delivered_agents)

    logger.info(f"Cascade: delivered={delivered}, skipped={skipped}")
    return CascadeMessageResponse(results=results, delivered_count=delivered, skipped_count=skipped)
