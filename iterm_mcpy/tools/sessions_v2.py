"""SP2 method-semantic `sessions` tool.

A single collection tool built on the MethodDispatcher base class,
implementing the WebSpec method surface for sessions:

    GET     — list/filter sessions (via _list_sessions_core),
              read terminal output when target="output", or return
              processing state when target="status".
    HEAD    — compact projection of GET (auto via HEAD_FIELDS)
    OPTIONS — self-describing schema (auto)
    POST + CREATE — create new sessions from a layout
              (delegates to execute_create_sessions), or split an
              existing pane when target="splits".
    POST + SEND   — write to session(s) when target="output"
              (delegates to execute_write_request)
              OR send control char / special key when target="keys"
    POST + TRIGGER — start monitoring a session when target="monitoring"
              (delegates to _start_monitoring_core).
    PATCH   — update sub-resources: tags (MODIFY replaces, APPEND adds),
              roles (assign), locks (acquire / request access), the
              session itself (target='active' + focus=true), or
              appearance/modifications (target='appearance' or None)
              covering colors, suspend/resume, badge, and focus cooldown.
    DELETE  — remove sub-resources: roles (removes assignment), locks
              (releases the lock), or monitoring (stops the monitor).

This module owns its helpers (``_list_sessions_core``,
``_split_session_core``, ``_start_monitoring_core``,
``_stop_monitoring_core``, ``_apply_session_modification``, plus
``_extract_last_message`` and ``_ensure_model``) directly — the SP1
per-verb modules (list_sessions / split_session / monitoring /
modifications) no longer exist.
"""
import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context

from core.agents import AgentRegistry
from core.flows import EventBus
from core.models import (
    AGENT_CLI_COMMANDS,
    CreateSessionsRequest,
    ListSessionsResponse,
    ModificationResult,
    ReadSessionsRequest,
    ReadTarget,
    SessionInfo,
    SessionMessage,
    SessionModification,
    SessionRole,
    SessionTarget,
    SplitSessionRequest,
    SplitSessionResponse,
    WriteToSessionsRequest,
)
from core.roles import RoleManager
from core.session import ItermSession
from core.tags import FocusCooldownManager
from core.terminal import ItermTerminal
from iterm_mcpy.dispatcher import MethodDispatcher
from iterm_mcpy.helpers import (
    execute_create_sessions,
    execute_read_request,
    execute_write_request,
    resolve_session,
    resolve_target_sessions,
)


# ------------------------------- Helpers -------------------------------- #

# Constants for "last message" extraction during session enrichment.
MAX_LAST_MESSAGE_LENGTH = 40
MIN_MEANINGFUL_CONTENT_LENGTH = 10


def _ensure_model(model_cls, payload):
    """Validate or coerce an incoming payload into a Pydantic model."""
    if isinstance(payload, model_cls):
        return payload
    return model_cls.model_validate(payload)


def _extract_last_message(screen_content: str) -> Optional[str]:
    """Extract the last meaningful message line from terminal output.

    Scans terminal output for meaningful content, skipping Claude's status
    indicators (⏺ markers), tool calls, and shell prompts to find actual
    message text.

    Args:
        screen_content: Recent terminal output

    Returns:
        Truncated last message or None
    """
    if not screen_content:
        return None

    lines = screen_content.strip().split('\n')

    # Find the last meaningful output line (skip status markers and prompts).
    for line in reversed(lines):
        line = line.strip()
        # Skip empty lines and prompts.
        if not line or line.startswith('❯') or line.startswith('$'):
            continue
        # Skip tool calls and system output.
        if '(MCP)' in line or 'Bash(' in line or 'Read(' in line:
            continue
        # Skip lines that are just status indicators (⏺ with parentheses = tool status).
        if line.startswith('⏺') and '(' in line and ')' in line:
            continue
        # This looks like actual Claude output.
        if len(line) > MIN_MEANINGFUL_CONTENT_LENGTH:
            # Truncate and add ellipsis.
            if len(line) > MAX_LAST_MESSAGE_LENGTH:
                return f'"{line[:MAX_LAST_MESSAGE_LENGTH - 3]}..."'
            return f'"{line}"'

    return None


async def _list_sessions_core(
    ctx: Context,
    *,
    agents_only: bool = False,
    tag: Optional[str] = None,
    tags: Optional[List[str]] = None,
    match: str = "any",
    locked: Optional[bool] = None,
    locked_by: Optional[str] = None,
    session_id: Optional[str] = None,
    agent: Optional[str] = None,
    team: Optional[str] = None,
    role: Optional[str] = None,
    include_message: bool = True,
    force_enrich: bool = True,
) -> ListSessionsResponse:
    """Core listing/filtering pipeline backing the sessions GET handler.

    Applies all filters and enriches each match into a SessionInfo (cwd,
    last_message, last_activity, process_name). Returns the raw response
    model so the dispatcher can serialize it via the envelope.

    Args:
        ctx: MCP context, used to pull terminal, agent_registry, lock_manager,
            role_manager, and logger from the lifespan context.
        agents_only: If True, only include sessions with registered agents.
        tag, tags, match: Tag filters (single or multiple with "any"/"all").
        locked, locked_by: Lock filters.
        session_id, agent, team: Identity filters (folded in from resolve_session).
        role: Role filter (folded in from the SP1 get_sessions_by_role tool).
        include_message: Whether to populate last_message (expensive).
        force_enrich: If True (default), always enrich with cwd/screen/etc.
            Callers that only care about a compact projection can pass False
            to skip the expensive per-session calls.

    Returns:
        ListSessionsResponse with all matching sessions.

    Raises:
        ValueError: If tag/lock filters are used without a tag_lock_manager,
            or if an unknown role name is supplied.
    """
    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
    role_manager = ctx.request_context.lifespan_context.get("role_manager")
    logger = ctx.request_context.lifespan_context["logger"]

    # Build filter description for logging.
    filters: List[str] = []
    if agents_only:
        filters.append("agents_only")
    if tag:
        filters.append(f"tag={tag}")
    if tags:
        filters.append(f"tags={tags} (match={match})")
    if locked is not None:
        filters.append(f"locked={locked}")
    if locked_by:
        filters.append(f"locked_by={locked_by}")
    if session_id:
        filters.append(f"session_id={session_id}")
    if agent:
        filters.append(f"agent={agent}")
    if team:
        filters.append(f"team={team}")
    if role:
        filters.append(f"role={role}")

    filter_desc = f" [{', '.join(filters)}]" if filters else ""
    logger.info(f"Listing sessions{filter_desc}")

    # Combine single tag and multiple tags for filtering.
    all_filter_tags: List[str] = []
    if tag:
        all_filter_tags.append(tag)
    if tags:
        all_filter_tags.extend(tags)

    requires_lock_manager = bool(all_filter_tags) or locked is not None or locked_by is not None
    if requires_lock_manager and lock_manager is None:
        logger.warning("Tag/lock filtering requested but tag_lock_manager is not available")
        raise ValueError(
            "Tag and lock filtering requires the tag_lock_manager to be initialized"
        )

    # Resolve role filter to a set of session IDs up front.
    role_session_ids: Optional[set] = None
    if role is not None:
        if role_manager is None:
            raise ValueError("Role filtering requires the role_manager to be initialized")
        try:
            session_role = SessionRole(role.lower())
        except ValueError as exc:
            valid_roles = [r.value for r in SessionRole]
            raise ValueError(
                f"Invalid role '{role}'. Valid roles are: {valid_roles}"
            ) from exc
        role_session_ids = set(role_manager.get_sessions_by_role(session_role))

    sessions = list(terminal.sessions.values())
    result: List[SessionInfo] = []

    for session in sessions:
        agent_obj = agent_registry.get_agent_by_session(session.id)

        # Apply agents_only filter.
        if agents_only and agent_obj is None:
            continue

        # Apply identity filters.
        if session_id is not None and session.id != session_id:
            continue
        if agent is not None and (agent_obj is None or agent_obj.name != agent):
            continue
        if team is not None and (agent_obj is None or team not in (agent_obj.teams or [])):
            continue

        # Apply role filter.
        if role_session_ids is not None and session.id not in role_session_ids:
            continue

        # Get lock info.
        is_locked = False
        lock_owner = None
        lock_time = None
        pending_requests = 0
        session_tags: List[str] = []

        if lock_manager:
            session_tags = lock_manager.get_tags(session.id)
            lock_info = lock_manager.get_lock_info(session.id)
            if lock_info:
                is_locked = True
                lock_owner = lock_info.owner
                lock_time = lock_info.locked_at
                pending_requests = len(lock_info.pending_requests)

        # Apply tag filter.
        if all_filter_tags:
            if match == "all":
                if not lock_manager or not lock_manager.has_all_tags(session.id, all_filter_tags):
                    continue
            else:  # "any" match
                if not lock_manager or not lock_manager.has_any_tags(session.id, all_filter_tags):
                    continue

        # Apply locked filter.
        if locked is not None:
            if locked and not is_locked:
                continue
            if not locked and is_locked:
                continue

        # Apply locked_by filter.
        if locked_by is not None:
            if lock_owner != locked_by:
                continue

        # Gather extended session context.
        session_cwd = None
        last_message = None
        last_activity_dt = None
        process_name = None

        if force_enrich:
            try:
                session_cwd = await session.get_cwd()
            except Exception as e:
                logger.debug(f"Error getting CWD for session {session.id}: {e}")

            if include_message:
                try:
                    screen_content = await session.get_screen_contents(max_lines=15)
                    last_message = _extract_last_message(screen_content)
                except Exception as e:
                    logger.debug(f"Error getting screen for session {session.id}: {e}")

            try:
                last_update = getattr(session, "last_update_time", None)
                if last_update:
                    last_activity_dt = datetime.fromtimestamp(last_update)
            except Exception as e:
                logger.debug(f"Error converting last_update_time for session {session.id}: {e}")

            name = session.name
            if "(" in name and name.endswith(")"):
                process_name = name[name.rfind("(") + 1:-1]

        session_info = SessionInfo(
            session_id=session.id,
            name=session.name,
            persistent_id=session.persistent_id,
            agent=agent_obj.name if agent_obj else None,
            team=agent_obj.teams[0] if agent_obj and agent_obj.teams else None,
            teams=agent_obj.teams if agent_obj else [],
            is_processing=getattr(session, "is_processing", False),
            suspended=getattr(session, "is_suspended", False),
            suspended_at=getattr(session, "suspended_at", None),
            suspended_by=getattr(session, "suspended_by", None),
            tags=session_tags,
            locked=is_locked,
            locked_by=lock_owner,
            locked_at=lock_time,
            pending_access_requests=pending_requests,
            cwd=session_cwd,
            last_activity=last_activity_dt,
            last_message=last_message,
            process_name=process_name,
        )
        result.append(session_info)

    logger.info(f"Found {len(result)} active sessions")

    return ListSessionsResponse(
        sessions=result,
        total_count=len(result),
        filter_applied=bool(filters),
    )


async def _split_session_core(
    split_request: SplitSessionRequest,
    terminal,
    agent_registry,
    role_manager: RoleManager,
    logger,
    profile_manager=None,
) -> SplitSessionResponse:
    """Core split-session logic backing POST /sessions/splits.

    Creates a new pane by splitting an existing session, registers an agent
    if requested, applies team colors, optionally launches an AI agent CLI,
    starts monitoring, and assigns a role. Returns the SplitSessionResponse.

    Raises:
        ValueError: When the target session can't be found or matches
            multiple sessions ambiguously.
    """
    target_sessions = await resolve_target_sessions(
        terminal, agent_registry, [split_request.target]
    )

    if not target_sessions:
        raise ValueError(
            f"Target session not found: {split_request.target.model_dump()}"
        )

    if len(target_sessions) > 1:
        matched_ids = [s.id for s in target_sessions]
        raise ValueError(
            f"Ambiguous target: multiple sessions matched {matched_ids}. "
            "Please be more specific."
        )

    source_session = target_sessions[0]

    new_session = await terminal.split_session_directional(
        session_id=source_session.id,
        direction=split_request.direction,
        name=split_request.name,
        profile=split_request.profile,
    )

    agent_name = None
    team_name = split_request.team

    # Register agent if specified.
    if split_request.agent:
        teams = [team_name] if team_name else []
        agent_registry.register_agent(
            name=split_request.agent,
            session_id=new_session.id,
            teams=teams,
        )
        agent_name = split_request.agent

        # Apply team profile colors if agent is in a team.
        if team_name and profile_manager:
            team_profile = profile_manager.get_or_create_team_profile(team_name)
            profile_manager.save_profiles()
            r, g, b = team_profile.color.to_rgb()
            try:
                import iterm2
                await new_session.session.async_set_profile_properties(
                    iterm2.LocalWriteOnlyProfile(
                        values={
                            "Tab Color": {
                                "Red Component": r,
                                "Green Component": g,
                                "Blue Component": b,
                                "Color Space": "sRGB",
                            }
                        }
                    )
                )
                logger.debug(
                    f"Applied team '{team_name}' color to split session {new_session.name}"
                )
            except Exception as e:
                logger.warning(f"Could not apply team color to session: {e}")

    # Launch AI agent CLI if agent_type specified.
    if split_request.agent_type:
        cli_command = AGENT_CLI_COMMANDS.get(split_request.agent_type)
        if cli_command:
            logger.info(
                f"Launching {split_request.agent_type} agent in split session "
                f"{new_session.name}: {cli_command}"
            )
            await new_session.execute_command(cli_command)
        else:
            logger.warning(f"Unknown agent type: {split_request.agent_type}")
    elif split_request.command:
        await new_session.execute_command(split_request.command)

    if split_request.monitor:
        await new_session.start_monitoring(update_interval=0.2)

    assigned_role = None
    if split_request.role:
        try:
            role_manager.assign_role(
                session_id=new_session.id,
                role=split_request.role,
                role_config=split_request.role_config,
            )
            assigned_role = split_request.role.value
            logger.info(
                f"Assigned role '{assigned_role}' to split session {new_session.id}"
            )
        except Exception as e:
            logger.warning(f"Could not assign role to split session: {e}")

    response = SplitSessionResponse(
        session_id=new_session.id,
        name=new_session.name,
        agent=agent_name,
        persistent_id=new_session.persistent_id or "",
        source_session_id=source_session.id,
        direction=split_request.direction,
        role=assigned_role,
    )

    logger.info(
        f"Split session {source_session.id} ({split_request.direction}) -> {new_session.id}"
    )
    return response


async def _start_monitoring_core(
    session,
    event_bus: Optional[EventBus],
    logger,
    *,
    enable_event_bus: bool = True,
    settle_delay: float = 2.0,
) -> bool:
    """Start monitoring on a single resolved session.

    Wires up the EventBus callback (when `enable_event_bus` is true) and
    kicks off polling on the session. Idempotent: calling on an
    already-monitored session is a no-op (returns True).

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


async def _apply_session_modification(
    session: ItermSession,
    modification: SessionModification,
    terminal: ItermTerminal,
    agent_registry: AgentRegistry,
    logger: logging.Logger,
    focus_cooldown: Optional[FocusCooldownManager] = None,
) -> ModificationResult:
    """Apply modification settings to a single session.

    Handles appearance (colors, badge), state (active/focus) and process
    control (suspend/resume). Returns a ModificationResult describing the
    applied changes or any error encountered.
    """
    agent = agent_registry.get_agent_by_session(session.id)
    agent_name = agent.name if agent else None
    result = ModificationResult(
        session_id=session.id,
        session_name=session.name,
        agent=agent_name,
    )
    changes = []

    try:
        # Handle set_active.
        if modification.set_active:
            agent_registry.active_session = session.id
            changes.append("set_active")

        # Handle suspend/resume (with toggle fallback if both are set).
        if modification.suspend and modification.resume:
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

        # Handle focus with cooldown check.
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
                    logger.warning(
                        f"Focus blocked for {session.name}: cooldown {remaining:.1f}s"
                    )
                    return result

            await terminal.focus_session(session.id)
            changes.append("focus")

            if focus_cooldown:
                focus_cooldown.record_focus(session.id, agent_name)
                logger.debug(f"Recorded focus event for {session.name} by {agent_name}")

        # Reset colors first if requested.
        if modification.reset:
            await session.reset_colors()
            changes.append("reset_colors")

        # Apply background color.
        if modification.background_color:
            c = modification.background_color
            await session.set_background_color(c.red, c.green, c.blue, c.alpha)
            changes.append(f"background_color=RGB({c.red},{c.green},{c.blue})")

        # Apply tab color.
        if modification.tab_color:
            c = modification.tab_color
            enabled = modification.tab_color_enabled if modification.tab_color_enabled is not None else True
            await session.set_tab_color(c.red, c.green, c.blue, enabled)
            changes.append(f"tab_color=RGB({c.red},{c.green},{c.blue})")
        elif modification.tab_color_enabled is not None:
            await session.set_tab_color_enabled(modification.tab_color_enabled)
            changes.append(f"tab_color_enabled={modification.tab_color_enabled}")

        # Apply cursor color.
        if modification.cursor_color:
            c = modification.cursor_color
            await session.set_cursor_color(c.red, c.green, c.blue)
            changes.append(f"cursor_color=RGB({c.red},{c.green},{c.blue})")

        # Apply badge.
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


# Parameters that _list_sessions_core accepts. Anything outside this set is
# dropped from the GET handler's kwargs to keep the helper signature tight.
_GET_CORE_PARAMS = {
    "agents_only",
    "tag",
    "tags",
    "match",
    "locked",
    "locked_by",
    "session_id",
    "agent",
    "team",
    "role",
    "include_message",
}


class SessionsDispatcher(MethodDispatcher):
    """Dispatcher for the `sessions` collection (SP2 method-semantic)."""

    collection = "sessions"

    METHODS = {
        "GET": {
            "aliases": ["list", "get", "read", "query"],
            "params": [
                "session_id?", "agent?", "team?", "role?",
                "tag?", "tags?", "match?", "locked?", "locked_by?",
                "format?", "group_by?", "include_message?", "shortcuts?",
                "agents_only?",
                "target?",
                "targets?",
                "max_lines?", "parallel?",
                "target='status'",
            ],
            "description": (
                "List sessions (no target), read output (target='output'), "
                "or fetch session status (target='status')."
            ),
        },
        "POST": {
            "definers": {
                "CREATE": {
                    "aliases": ["create", "split"],
                    "params": [
                        "layout", "sessions", "register_agents?", "shell?",
                        "target='splits' + direction='below'|'above'|'left'|'right' + session_id",
                        "name?", "agent?", "team?", "register_agent?",
                    ],
                    "description": (
                        "Create sessions (no target) or split an existing "
                        "session (target='splits')."
                    ),
                },
                "SEND": {
                    "aliases": ["send", "write", "dispatch"],
                    "params": [
                        "target='output' | target='keys'",
                        # output target:
                        "messages? | content? + (session_id|agent|name|team)",
                        "parallel?", "skip_duplicates?", "execute?", "use_encoding?",
                        # keys target:
                        "control_char? | key? + (session_id|agent|name|team)",
                    ],
                    "description": (
                        "Write to session(s). target='output' -> text/commands; "
                        "target='keys' -> control char or named special key."
                    ),
                },
                "TRIGGER": {
                    "aliases": ["start", "trigger", "monitor"],
                    "params": [
                        "target='monitoring'",
                        "session_id | agent | name",
                        "enable_event_bus?",
                    ],
                    "description": "Start monitoring a session (target='monitoring').",
                },
            },
        },
        "PATCH": {
            "definers": {
                "MODIFY": {
                    "aliases": ["update", "patch", "assign"],
                    "params": [
                        (
                            "target='tags' | 'roles' | 'locks' | 'active' | "
                            "'appearance' | None (default appearance/focus)"
                        ),
                        "session_id",
                        # tags:
                        "tags?=[...]",
                        # roles:
                        "role?", "assigned_by?",
                        # locks:
                        "agent?", "action?='lock'|'request_access'",
                        # active / appearance:
                        "focus?", "suspended?", "tab_color?", "cursor_color?",
                        "background_color?", "tab_color_enabled?", "badge?",
                        "name?", "reset?",
                    ],
                    "description": (
                        "Update session fields or sub-resources, including "
                        "appearance (colors/badge), suspend/resume, focus, "
                        "and active state."
                    ),
                },
                "APPEND": {
                    "aliases": ["append"],
                    "params": [
                        "target='tags'",
                        "session_id",
                        "tags=[...]",
                    ],
                    "description": "Append to session tags (vs MODIFY which replaces).",
                },
            },
        },
        "DELETE": {
            "aliases": ["remove", "unlock", "stop"],
            "params": [
                "target='roles' | 'locks' | 'monitoring'",
                "session_id",
                # locks:
                "agent?",
                # roles:
                "removed_by?",
                # monitoring: session_id|agent|name (no body)
            ],
            "description": (
                "Remove role assignment, release a session lock, or stop "
                "monitoring a session (target='monitoring')."
            ),
        },
        "HEAD": {"compact_fields": ["session_id", "name", "agent", "is_processing", "locked"]},
        "OPTIONS": {"description": "Return this schema."},
    }

    sub_resources = [
        "output", "status", "tags", "locks", "roles", "monitoring",
        "splits", "keys", "appearance", "active",
    ]

    async def on_get(self, ctx, **params):
        """Route GET by `target` — list sessions, read output, or get status."""
        target = params.get("target")
        if target == "output":
            return await self._get_output(ctx, **params)
        if target == "status":
            return await self._get_status(ctx, **params)
        return await self._list_sessions(ctx, **params)

    async def _list_sessions(self, ctx, **params):
        """List sessions with optional filters.

        Params are a superset of _list_sessions_core's signature. Display-only
        params (format, group_by, shortcuts) are irrelevant here because the
        envelope renders the raw SessionInfo list; they're accepted so the tool
        wrapper signature stays consistent and simply ignored.
        """
        core_params = {k: v for k, v in params.items() if k in _GET_CORE_PARAMS}
        response = await _list_sessions_core(ctx, **core_params)
        return response.sessions

    async def _get_output(self, ctx, **params):
        """GET /sessions/output — read terminal output via execute_read_request.

        Three ways to specify targets, in precedence order:
        1. Explicit `targets=[...]` list (matching legacy read_sessions)
        2. Shortcut params (session_id / agent / name / team) → built into a
           single target
        3. Neither provided → delegate to ReadSessionsRequest's built-in
           "active session" semantics (pass an empty targets list)
        """
        targets = params.get("targets")
        if targets is None:
            target_spec: dict = {}
            for key in ("session_id", "agent", "name", "team"):
                val = params.get(key)
                if val is not None:
                    target_spec[key] = val
            if target_spec:
                # Allow per-shortcut max_lines override.
                if params.get("max_lines") is not None:
                    target_spec["max_lines"] = params["max_lines"]
                targets = [target_spec]
            else:
                # Fall through to the active-session case.
                targets = []

        coerced_targets = [
            ReadTarget(**t) if isinstance(t, dict) else t for t in targets
        ]

        request_kwargs: dict = {"targets": coerced_targets}
        if params.get("parallel") is not None:
            request_kwargs["parallel"] = params["parallel"]
        if params.get("filter_pattern") is not None:
            request_kwargs["filter_pattern"] = params["filter_pattern"]

        request = ReadSessionsRequest(**request_kwargs)

        terminal = ctx.request_context.lifespan_context["terminal"]
        agent_registry = ctx.request_context.lifespan_context["agent_registry"]
        logger = ctx.request_context.lifespan_context["logger"]

        return await execute_read_request(request, terminal, agent_registry, logger)

    async def on_post(self, ctx, definer, **params):
        """Route POST by `(definer, target)` — create, split, write, send keys, monitor."""
        target = params.get("target")

        if definer == "CREATE" and not target:
            return await self._create_sessions(ctx, **params)

        if definer == "CREATE" and target == "splits":
            return await self._create_split(ctx, **params)

        if definer == "SEND" and target == "output":
            return await self._write_output(ctx, **params)

        if definer == "SEND" and target == "keys":
            return await self._send_keys(ctx, **params)

        if definer == "TRIGGER" and target == "monitoring":
            return await self._start_monitoring(ctx, **params)

        raise NotImplementedError(
            f"POST+{definer} on target={target!r} not yet implemented"
        )

    async def _create_sessions(self, ctx, **params):
        """POST + CREATE — delegate to execute_create_sessions."""
        terminal = ctx.request_context.lifespan_context["terminal"]
        layout_manager = ctx.request_context.lifespan_context["layout_manager"]
        agent_registry = ctx.request_context.lifespan_context["agent_registry"]
        profile_manager = ctx.request_context.lifespan_context["profile_manager"]
        logger = ctx.request_context.lifespan_context["logger"]

        # Build the CreateSessionsRequest. `register_agents` and `shell` are
        # accepted by the tool signature for forward-compat but are not part
        # of the existing CreateSessionsRequest — they're ignored here and
        # will be wired up in a later task if needed.
        create_request = CreateSessionsRequest.model_validate({
            "layout": params["layout"],
            "sessions": params["sessions"],
        })
        result = await execute_create_sessions(
            create_request,
            terminal,
            layout_manager,
            agent_registry,
            logger,
            profile_manager=profile_manager,
        )
        logger.info(f"sessions_v2 CREATE: created {len(result.sessions)} sessions")
        return result

    async def _write_output(self, ctx, **params):
        """POST + SEND on target='output' — delegate to execute_write_request.

        Accepts either a structured `messages=[...]` list (matching the legacy
        write_to_sessions schema) or shortcut params: `content` plus a single
        target identifier (session_id/agent/name/team).
        """
        messages = params.get("messages")
        if not messages:
            content = params.get("content")
            if not content:
                raise ValueError(
                    "write output requires either messages=[...] or content=..."
                )
            target_spec: dict = {}
            for key in ("session_id", "agent", "name", "team"):
                val = params.get(key)
                if val is not None:
                    target_spec[key] = val
            if not target_spec:
                raise ValueError(
                    "write output requires at least one of: "
                    "session_id, agent, name, team (or explicit messages)"
                )
            message: dict = {
                "content": content,
                "targets": [target_spec],
            }
            if params.get("execute") is not None:
                message["execute"] = params["execute"]
            if params.get("use_encoding") is not None:
                message["use_encoding"] = params["use_encoding"]
            messages = [message]

        # Coerce dict messages into Pydantic models. Pydantic also accepts the
        # raw dicts directly via model_validate, but explicit coercion keeps
        # the request shape obvious to readers.
        coerced_messages = []
        for m in messages:
            if isinstance(m, dict):
                # Coerce nested target dicts into SessionTarget models too.
                m_targets = m.get("targets") or []
                coerced_targets = [
                    SessionTarget(**t) if isinstance(t, dict) else t for t in m_targets
                ]
                m_kwargs = {**m, "targets": coerced_targets}
                coerced_messages.append(SessionMessage(**m_kwargs))
            else:
                coerced_messages.append(m)

        request_kwargs: dict = {"messages": coerced_messages}
        if params.get("parallel") is not None:
            request_kwargs["parallel"] = params["parallel"]
        if params.get("skip_duplicates") is not None:
            request_kwargs["skip_duplicates"] = params["skip_duplicates"]

        request = WriteToSessionsRequest(**request_kwargs)

        terminal = ctx.request_context.lifespan_context["terminal"]
        agent_registry = ctx.request_context.lifespan_context["agent_registry"]
        logger = ctx.request_context.lifespan_context["logger"]
        lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
        notification_manager = ctx.request_context.lifespan_context.get(
            "notification_manager"
        )

        return await execute_write_request(
            request,
            terminal,
            agent_registry,
            logger,
            lock_manager=lock_manager,
            notification_manager=notification_manager,
        )

    async def _send_keys(self, ctx, **params):
        """POST /sessions/keys — send control char or special key to sessions.

        Accepts exactly one of:
          - control_char: single letter for Ctrl+X (e.g., "C" for Ctrl+C)
          - key: named special key (e.g., "enter", "tab", "escape", "up", ...)

        Targets via session_id / agent / name / team (same as resolve_session).
        """
        control_char = params.get("control_char")
        key = params.get("key")

        if control_char and key:
            raise ValueError("send keys: pass either control_char or key, not both")
        if not control_char and not key:
            raise ValueError("send keys: requires control_char=... or key=...")

        terminal = ctx.request_context.lifespan_context["terminal"]
        agent_registry = ctx.request_context.lifespan_context["agent_registry"]
        logger = ctx.request_context.lifespan_context["logger"]

        sessions = await resolve_session(
            terminal,
            agent_registry,
            session_id=params.get("session_id"),
            name=params.get("name"),
            agent=params.get("agent"),
            team=params.get("team"),
        )
        if not sessions:
            raise ValueError("send keys: no matching session found")

        results = []
        for session in sessions:
            if control_char:
                await session.send_control_character(control_char)
                label = f"Ctrl+{control_char.upper()}"
            else:
                await session.send_special_key(key)
                label = f"key '{key}'"
            logger.info(f"Sent {label} to session {session.name}")
            results.append({"session_id": session.id, "name": session.name, "sent": label})

        return {"sent": results, "count": len(results)}

    # ---------------------- PATCH / DELETE (Task 4d) ---------------------- #

    async def on_patch(self, ctx, definer, **params):
        """Route PATCH by (definer, target).

        Per WebSpec definer semantics, we reject definer/target combinations
        that are not supported. Only `tags` supports APPEND (adding vs
        replacing). All other PATCH targets require MODIFY.
        """
        target = params.get("target")

        # Only tags supports APPEND; everything else is MODIFY-only.
        if definer == "APPEND" and target != "tags":
            raise ValueError(
                f"PATCH+APPEND is only valid with target='tags'. "
                f"Use PATCH+MODIFY for target={target!r}."
            )
        if definer not in ("MODIFY", "APPEND"):
            raise ValueError(
                f"PATCH+{definer} is not supported on sessions. "
                f"Valid definers are MODIFY (and APPEND for tags)."
            )

        if target == "tags":
            return await self._patch_tags(ctx, definer, **params)

        if target == "roles":
            # MODIFY only (already guarded above).
            return await self._patch_roles(ctx, definer, **params)

        if target == "locks":
            # MODIFY only (already guarded above).
            return await self._patch_locks(ctx, definer, **params)

        # PATCH on the session itself — appearance (target='appearance'),
        # active/focus (target='active'), or default (target=None).
        if target in (None, "active", "appearance"):
            return await self._patch_session(ctx, definer, **params)

        raise NotImplementedError(f"PATCH target={target!r} not yet implemented")

    async def on_delete(self, ctx, **params):
        """Route DELETE by `target` — roles, locks, or monitoring."""
        target = params.get("target")

        if target == "roles":
            return await self._delete_role(ctx, **params)

        if target == "locks":
            return await self._delete_lock(ctx, **params)

        if target == "monitoring":
            return await self._stop_monitoring(ctx, **params)

        # DELETE on the session itself is NOT supported in SP2 (there was no
        # legacy remove_session tool). Reserved for a future task.
        raise NotImplementedError(f"DELETE target={target!r} not yet implemented")

    async def _patch_tags(self, ctx, definer, **params):
        """PATCH /sessions/{id}/tags. MODIFY replaces, APPEND adds."""
        session_id = params.get("session_id")
        if not session_id:
            raise ValueError("patch tags requires session_id")

        tags = params.get("tags")
        if tags is None:
            raise ValueError("patch tags requires tags=[...]")

        lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
        if not lock_manager:
            raise RuntimeError("tag_lock_manager not available")

        # MODIFY → replace (append=False). APPEND → append=True.
        append = (definer == "APPEND")
        updated = lock_manager.set_tags(session_id, tags, append=append)
        return {"session_id": session_id, "tags": updated, "appended": append}

    async def _patch_session(self, ctx, definer, **params):
        """PATCH on a session — focus/activate, appearance, suspend/resume.

        Routes:
          - target='active' + focus=True → terminal.focus_session(session_id)
            (fast-path preserved for back-compat with Task 4d tests).
          - target='appearance' or target=None with any appearance/process
            modification fields → delegates to `_apply_session_modification`.
        """
        session_id = params.get("session_id")
        if not session_id:
            raise ValueError("patch session requires session_id")

        target = params.get("target")
        focus = params.get("focus")

        lifespan = ctx.request_context.lifespan_context
        terminal = lifespan["terminal"]

        # Collect the set of modification fields the caller actually passed.
        # These are the attributes understood by SessionModification.
        modification_fields = (
            "set_active", "focus", "suspend", "resume", "suspend_by",
            "background_color", "tab_color", "tab_color_enabled",
            "cursor_color", "badge", "reset",
        )
        # Map our tool-level `suspended` flag onto the model's suspend/resume
        # pair before looking at the rest of the fields. This is a small
        # convenience so callers can say `suspended=True` / `suspended=False`
        # rather than picking between suspend= and resume=.
        if "suspended" in params and "suspend" not in params and "resume" not in params:
            if params["suspended"]:
                params["suspend"] = True
            else:
                params["resume"] = True

        passed_mods = {
            k: params[k] for k in modification_fields if k in params
        }

        # Fast path: target='active' + focus=True with no other modification
        # fields — preserve the Task 4d lightweight response.
        if (
            target == "active"
            and focus is True
            and len(passed_mods) == 1
        ):
            await terminal.focus_session(session_id)
            return {"session_id": session_id, "focused": True}

        # Without any modification fields and without an explicit target, the
        # request is a no-op — surface NotImplemented as in 4d.
        if not passed_mods and target in (None, "active"):
            raise NotImplementedError(
                "patch session: no modification fields provided"
            )

        # Otherwise delegate to the module-local _apply_session_modification.
        agent_registry = lifespan["agent_registry"]
        focus_cooldown = lifespan.get("focus_cooldown")
        logger = lifespan["logger"]

        # Build a SessionModification. Identity always comes from session_id
        # (sessions_v2 is session-centric; the modification helper itself
        # resolves session_id -> session).
        mod_kwargs = {"session_id": session_id}
        # Forward supported fields. Use model_validate so Pydantic handles
        # nested ColorSpec dicts (tab_color/cursor_color/background_color).
        for key in modification_fields:
            if key in params:
                mod_kwargs[key] = params[key]

        modification = SessionModification.model_validate(mod_kwargs)

        sessions = await resolve_session(
            terminal, agent_registry, session_id=session_id,
        )
        if not sessions:
            raise ValueError(f"patch session: no session found with id={session_id}")

        session = sessions[0]
        result = await _apply_session_modification(
            session, modification, terminal, agent_registry, logger, focus_cooldown,
        )
        return result

    async def _patch_roles(self, ctx, definer, **params):
        """PATCH /sessions/{id}/roles — assign a role."""
        session_id = params.get("session_id")
        role = params.get("role")
        if not session_id:
            raise ValueError("patch roles requires session_id")
        if not role:
            raise ValueError("patch roles requires role")

        role_manager = ctx.request_context.lifespan_context.get("role_manager")
        if not role_manager:
            raise RuntimeError("role_manager not available")

        # Coerce raw strings to the SessionRole enum (case-insensitive match on value).
        role_enum = role if isinstance(role, SessionRole) else SessionRole(role.lower())
        assigned_by = params.get("assigned_by")
        role_manager.assign_role(session_id, role_enum, assigned_by=assigned_by)
        return {"session_id": session_id, "role": role_enum.value}

    async def _delete_role(self, ctx, **params):
        """DELETE /sessions/{id}/roles — remove role assignment."""
        session_id = params.get("session_id")
        if not session_id:
            raise ValueError("delete roles requires session_id")

        role_manager = ctx.request_context.lifespan_context.get("role_manager")
        if not role_manager:
            raise RuntimeError("role_manager not available")

        # NOTE: core/roles.py RoleManager.remove_role only takes session_id.
        # `removed_by` is accepted by the tool signature but not persisted;
        # audit trail for removals can be added in a follow-up.
        removed = role_manager.remove_role(session_id)
        return {"session_id": session_id, "removed": bool(removed)}

    async def _patch_locks(self, ctx, definer, **params):
        """PATCH /sessions/{id}/locks — acquire a lock or request access."""
        session_id = params.get("session_id")
        agent = params.get("agent")
        if not session_id:
            raise ValueError("patch locks requires session_id")
        if not agent:
            raise ValueError("patch locks requires agent")

        lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
        if not lock_manager:
            raise RuntimeError("tag_lock_manager not available")

        action = params.get("action", "lock")  # "lock" or "request_access"

        if action == "lock":
            acquired, owner = lock_manager.lock_session(session_id, agent)
            return {
                "session_id": session_id,
                "agent": agent,
                "acquired": acquired,
                "owner": owner,
            }

        if action == "request_access":
            allowed, owner = lock_manager.check_permission(session_id, agent)
            return {
                "session_id": session_id,
                "agent": agent,
                "allowed": allowed,
                "owner": owner,
            }

        raise ValueError(
            f"patch locks: unknown action={action!r} "
            "(expected 'lock' or 'request_access')"
        )

    async def _delete_lock(self, ctx, **params):
        """DELETE /sessions/{id}/locks — release lock."""
        session_id = params.get("session_id")
        agent = params.get("agent")
        if not session_id:
            raise ValueError("delete locks requires session_id")
        if not agent:
            raise ValueError("delete locks requires agent")

        lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
        if not lock_manager:
            raise RuntimeError("tag_lock_manager not available")

        success = lock_manager.unlock_session(session_id, agent)
        return {"session_id": session_id, "agent": agent, "unlocked": success}

    # ---------------------- Status / splits / monitoring (Task 4e) ---- #

    async def _get_status(self, ctx, **params):
        """GET /sessions/{id}/status — returns processing state.

        Replaces the legacy ``check_session_status`` tool. Resolves the target
        session (by session_id / agent / name) and returns a compact status
        record per match.
        """
        session_id = params.get("session_id")
        name = params.get("name")
        agent = params.get("agent")
        if not any([session_id, name, agent]):
            raise ValueError(
                "get status requires at least one of: session_id, name, agent"
            )

        lifespan = ctx.request_context.lifespan_context
        terminal = lifespan["terminal"]
        agent_registry = lifespan["agent_registry"]
        lock_manager = lifespan.get("tag_lock_manager")

        sessions = await resolve_session(
            terminal, agent_registry,
            session_id=session_id, name=name, agent=agent,
        )
        if not sessions:
            raise ValueError("get status: no matching session found")

        active_id = getattr(agent_registry, "active_session", None)

        statuses = []
        for s in sessions:
            agent_obj = agent_registry.get_agent_by_session(s.id)
            status: dict = {
                "session_id": s.id,
                "name": s.name,
                "persistent_id": getattr(s, "persistent_id", None),
                "agent": agent_obj.name if agent_obj else None,
                "teams": agent_obj.teams if agent_obj else [],
                "is_processing": getattr(s, "is_processing", False),
                "is_monitoring": getattr(s, "is_monitoring", False),
                "is_active": s.id == active_id,
                "suspended": getattr(s, "is_suspended", False),
            }
            if lock_manager is not None:
                lock_info = lock_manager.get_lock_info(s.id)
                status["tags"] = lock_manager.get_tags(s.id)
                status["locked"] = lock_info is not None
                status["locked_by"] = lock_info.owner if lock_info else None
                status["locked_at"] = (
                    lock_info.locked_at.isoformat() if lock_info else None
                )
                status["pending_access_requests"] = (
                    len(lock_info.pending_requests) if lock_info else 0
                )
            statuses.append(status)

        return statuses

    async def _create_split(self, ctx, **params):
        """POST /sessions/{id}/splits (CREATE) — split a pane.

        Delegates to the module-local ``_split_session_core`` helper.
        """
        session_id = params.get("session_id")
        if not session_id:
            raise ValueError("create split requires session_id")

        direction = params.get("direction", "below")

        lifespan = ctx.request_context.lifespan_context
        terminal = lifespan["terminal"]
        agent_registry = lifespan["agent_registry"]
        role_manager = lifespan["role_manager"]
        logger = lifespan["logger"]
        profile_manager = lifespan.get("profile_manager")

        # Build SessionTarget from the scalar session_id (the dispatcher surface
        # is identity-by-id, not the legacy nested target object).
        request_kwargs: dict = {
            "target": {"session_id": session_id},
            "direction": direction,
        }
        for key in (
            "name", "profile", "command", "agent", "agent_type",
            "team", "monitor", "role", "role_config",
        ):
            if key in params:
                request_kwargs[key] = params[key]

        split_request = SplitSessionRequest.model_validate(request_kwargs)

        response = await _split_session_core(
            split_request,
            terminal,
            agent_registry,
            role_manager,
            logger,
            profile_manager=profile_manager,
        )
        return response

    async def _start_monitoring(self, ctx, **params):
        """POST /sessions/{id}/monitoring (TRIGGER) — start monitoring a session."""
        session_id = params.get("session_id")
        name = params.get("name")
        agent = params.get("agent")
        if not any([session_id, name, agent]):
            raise ValueError(
                "start monitoring requires at least one of: session_id, name, agent"
            )

        lifespan = ctx.request_context.lifespan_context
        terminal = lifespan["terminal"]
        agent_registry = lifespan["agent_registry"]
        event_bus = lifespan.get("event_bus")
        logger = lifespan["logger"]

        sessions = await resolve_session(
            terminal, agent_registry,
            session_id=session_id, name=name, agent=agent,
        )
        if not sessions:
            raise ValueError("start monitoring: no matching session found")

        enable_event_bus = params.get("enable_event_bus", True)
        # The callback only gets wired up if both the caller opted in AND an
        # event_bus exists in the lifespan — report the effective state, not
        # just the requested one, so callers can detect missing infrastructure.
        event_bus_attached = enable_event_bus and event_bus is not None

        results = []
        for session in sessions:
            started = await _start_monitoring_core(
                session,
                event_bus,
                logger,
                enable_event_bus=enable_event_bus,
                # Settle delay mostly matters for the legacy blocking tool;
                # sessions_v2 returns the result structurally so skip the wait.
                settle_delay=0,
            )
            results.append({
                "session_id": session.id,
                "name": session.name,
                "started": started,
                "event_bus_requested": enable_event_bus,
                "event_bus_attached": event_bus_attached,
            })

        return {"monitoring": results, "count": len(results)}

    async def _stop_monitoring(self, ctx, **params):
        """DELETE /sessions/{id}/monitoring — stop monitoring a session."""
        session_id = params.get("session_id")
        name = params.get("name")
        agent = params.get("agent")
        if not any([session_id, name, agent]):
            raise ValueError(
                "stop monitoring requires at least one of: session_id, name, agent"
            )

        lifespan = ctx.request_context.lifespan_context
        terminal = lifespan["terminal"]
        agent_registry = lifespan["agent_registry"]
        logger = lifespan["logger"]

        sessions = await resolve_session(
            terminal, agent_registry,
            session_id=session_id, name=name, agent=agent,
        )
        if not sessions:
            raise ValueError("stop monitoring: no matching session found")

        results = []
        for session in sessions:
            stopped = await _stop_monitoring_core(session, logger)
            results.append({
                "session_id": session.id,
                "name": session.name,
                "stopped": stopped,
            })

        return {"monitoring": results, "count": len(results)}


_dispatcher = SessionsDispatcher()


async def sessions_v2(
    ctx: Context,
    op: str = "GET",
    definer: Optional[str] = None,
    # Identity/filter params for GET.
    session_id: Optional[str] = None,
    agent: Optional[str] = None,
    team: Optional[str] = None,
    role: Optional[str] = None,
    tag: Optional[str] = None,
    tags: Optional[List[str]] = None,
    match: str = "any",
    locked: Optional[bool] = None,
    locked_by: Optional[str] = None,
    format: str = "grouped",
    group_by: str = "directory",
    include_message: bool = True,
    shortcuts: bool = True,
    agents_only: bool = False,
    # POST+CREATE params.
    layout: Optional[str] = None,
    sessions: Optional[List[dict]] = None,
    register_agents: bool = True,
    shell: Optional[str] = None,
    # NEW (4b): output sub-resource (read + write).
    target: Optional[str] = None,
    targets: Optional[List[dict]] = None,
    max_lines: Optional[int] = None,
    parallel: Optional[bool] = None,
    filter_pattern: Optional[str] = None,
    messages: Optional[List[dict]] = None,
    content: Optional[str] = None,
    name: Optional[str] = None,
    skip_duplicates: Optional[bool] = None,
    execute: Optional[bool] = None,
    use_encoding: Optional[bool] = None,
    # NEW for 4c: keys sub-resource.
    control_char: Optional[str] = None,
    key: Optional[str] = None,
    # NEW for 4d: tags/roles/locks/active sub-resources. Note that `role` is
    # already in the signature as a GET filter (Task 4a); the same slot is
    # reused here for PATCH input.
    assigned_by: Optional[str] = None,
    removed_by: Optional[str] = None,
    action: Optional[str] = None,       # "lock" | "request_access" for locks
    focus: Optional[bool] = None,       # for target="active"
    # NEW for 4e: splits / monitoring / status / appearance (full modify).
    direction: Optional[str] = None,            # split direction
    enable_event_bus: Optional[bool] = None,    # monitoring toggle
    register_agent: Optional[bool] = None,      # (reserved for split / create)
    # Appearance & process-control modification fields. Pydantic handles nested
    # dicts for color specs (e.g. tab_color={"red":..,"green":..,"blue":..}).
    tab_color: Optional[dict] = None,
    cursor_color: Optional[dict] = None,
    background_color: Optional[dict] = None,
    tab_color_enabled: Optional[bool] = None,
    badge: Optional[str] = None,
    suspended: Optional[bool] = None,           # shortcut for suspend/resume
    suspend: Optional[bool] = None,
    resume: Optional[bool] = None,
    suspend_by: Optional[str] = None,
    set_active: Optional[bool] = None,
    reset: Optional[bool] = None,
) -> str:
    """Session operations: list, read, write, send keys, create, split, monitor,
    modify, patch, delete, HEAD, OPTIONS.

    Use op="list" or op="GET" to list sessions with filters.
    Use op="GET" + target="output" to read terminal output.
    Use op="GET" + target="status" + session_id=... to fetch processing state.
    Use op="send" (or op="POST" + definer="SEND") + target="output" to write.
    Use op="send" + target="keys" + control_char=... | key=... to send control
      characters or named special keys to session(s).
    Use op="create" + target="splits" + session_id=... + direction=... to split
      an existing pane (below/above/left/right).
    Use op="start" (or op="POST" + definer="TRIGGER") + target="monitoring" to
      begin real-time output monitoring. Use op="stop" + target="monitoring"
      (DELETE) to end it.
    Use op="update" (or op="PATCH") + target="tags" to replace tags,
      op="append" + target="tags" to add tags.
    Use op="assign" (or op="PATCH") + target="roles" + role=... to assign a role.
    Use op="update" + target="locks" + agent=... + action="lock"|"request_access"
      to acquire a lock or request access.
    Use op="update" + target="active" + focus=true + session_id=... to focus.
    Use op="update" + target="appearance" + session_id=... + (tab_color,
      cursor_color, badge, suspended, reset, ...) to change session visuals
      and process state.
    Use op="delete" (or op="DELETE") + target="roles" to remove a role.
    Use op="unlock" (or op="DELETE") + target="locks" + agent=... to release a lock.
    Use op="HEAD" (or "peek"/"summary") for a compact list.
    Use op="OPTIONS" (or "schema"/"discover") to discover the tool's surface.
    Use op="create" (or op="POST") to create new sessions from a layout.

    This is SP2's first method-semantic collection tool. It coexists with the
    legacy per-verb session tools (list_sessions, create_sessions,
    read_sessions, write_to_sessions, etc.) and will eventually replace them.
    """
    # Build a params dict of non-None values so handlers don't have to juggle
    # defaults. Use `is not None` so booleans (False) and ints (0) survive.
    raw_params = {
        "session_id": session_id,
        "agent": agent,
        "team": team,
        "role": role,
        "tag": tag,
        "tags": tags,
        "match": match,
        "locked": locked,
        "locked_by": locked_by,
        "format": format,
        "group_by": group_by,
        "include_message": include_message,
        "shortcuts": shortcuts,
        "agents_only": agents_only,
        "layout": layout,
        "sessions": sessions,
        "register_agents": register_agents,
        "shell": shell,
        "target": target,
        "targets": targets,
        "max_lines": max_lines,
        "parallel": parallel,
        "filter_pattern": filter_pattern,
        "messages": messages,
        "content": content,
        "name": name,
        "skip_duplicates": skip_duplicates,
        "execute": execute,
        "use_encoding": use_encoding,
        "control_char": control_char,
        "key": key,
        "assigned_by": assigned_by,
        "removed_by": removed_by,
        "action": action,
        "focus": focus,
        # 4e
        "direction": direction,
        "enable_event_bus": enable_event_bus,
        "register_agent": register_agent,
        "tab_color": tab_color,
        "cursor_color": cursor_color,
        "background_color": background_color,
        "tab_color_enabled": tab_color_enabled,
        "badge": badge,
        "suspended": suspended,
        "suspend": suspend,
        "resume": resume,
        "suspend_by": suspend_by,
        "set_active": set_active,
        "reset": reset,
    }
    params = {k: v for k, v in raw_params.items() if v is not None}

    return await _dispatcher.dispatch(ctx=ctx, op=op, definer=definer, **params)


def register(mcp):
    """Register the sessions_v2 dispatcher tool.

    Named `sessions_v2` to coexist with the legacy session tools during
    the SP2 coexistence period. At final cutover this gets renamed to
    `sessions` (with the old list_sessions et al. unregistered).
    """
    mcp.tool(name="sessions_v2")(sessions_v2)
