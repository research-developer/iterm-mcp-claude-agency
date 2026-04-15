"""Session management tools.

Provides tools for listing sessions (with grouped/compact/full formats and
path-shortcut support), setting tags, managing session locks and listing
agent-owned locks, setting the active session, and creating or splitting
sessions.

Also hosts the path-shortcut and last-message extraction helpers used
exclusively by list_sessions formatting.
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional

from mcp.server.fastmcp import Context

from core.models import (
    AGENT_CLI_COMMANDS,
    CreateSessionsRequest,
    ListSessionsResponse,
    ManageSessionLockRequest,
    ManageSessionLockResponse,
    SessionInfo,
    SessionRole,
    SetActiveSessionRequest,
    SplitSessionRequest,
    SplitSessionResponse,
)
from core.roles import RoleManager

from iterm_mcpy.helpers import (
    execute_create_sessions,
    notify_lock_request,
    resolve_session,
    resolve_target_sessions,
)


def _ensure_model(model_cls, payload):
    """Validate or coerce an incoming payload into a Pydantic model."""
    if isinstance(payload, model_cls):
        return payload
    return model_cls.model_validate(payload)


# Path shortcuts for compact display
# Users can customize by setting ITERM_MCP_PATH_SHORTCUTS environment variable
# Format: "/path1=$ALIAS1,/path2=$ALIAS2"
def _get_path_shortcuts() -> Dict[str, str]:
    """Get path shortcuts from environment or defaults.

    Returns:
        Dict mapping full paths to shortcut names
    """
    # Default shortcuts
    shortcuts = {
        os.path.expanduser("~"): "$HOME",
    }

    # Add from environment variable if set
    # Format: "/path1=$ALIAS1,/path2=$ALIAS2"
    env_shortcuts = os.environ.get("ITERM_MCP_PATH_SHORTCUTS", "")
    if env_shortcuts:
        for pair in env_shortcuts.split(","):
            if "=" in pair:
                path, alias = pair.split("=", 1)
                shortcuts[os.path.expanduser(path.strip())] = alias.strip()

    return shortcuts

PATH_SHORTCUTS = _get_path_shortcuts()


def _apply_shortcuts(path: Optional[str], shortcuts: Dict[str, str]) -> Optional[str]:
    """Apply path shortcuts for compact display.

    Args:
        path: Full path to shorten
        shortcuts: Dict mapping full paths to shortcut names

    Returns:
        Shortened path with shortcuts applied
    """
    if not path:
        return None

    # Sort by length (longest first) to match most specific paths first
    sorted_shortcuts = sorted(shortcuts.items(), key=lambda x: -len(x[0]))

    for full_path, shortcut in sorted_shortcuts:
        if path.startswith(full_path):
            remainder = path[len(full_path):]
            if remainder.startswith("/"):
                remainder = remainder[1:]
            if remainder:
                return f"{shortcut}/{remainder}"
            return shortcut

    return path


def _humanize_time(dt: Optional[datetime]) -> str:
    """Convert datetime to human-readable relative time.

    Args:
        dt: Datetime to convert

    Returns:
        Human-readable string like "2m", "1h", "3d"
    """
    if not dt:
        return ""

    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    delta = now - dt
    seconds = int(delta.total_seconds())

    if seconds < 60:
        return "now"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    elif seconds < 86400:
        return f"{seconds // 3600}h"
    else:
        return f"{seconds // 86400}d"


# Constants for message extraction
MAX_LAST_MESSAGE_LENGTH = 40
MIN_MEANINGFUL_CONTENT_LENGTH = 10


def _extract_last_message(screen_content: str) -> Optional[str]:
    """Extract the last Claude message from terminal output.

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

    # Find the last meaningful output line (skip status markers and prompts)
    for line in reversed(lines):
        line = line.strip()
        # Skip empty lines and prompts
        if not line or line.startswith('❯') or line.startswith('$'):
            continue
        # Skip tool calls and system output
        if '(MCP)' in line or 'Bash(' in line or 'Read(' in line:
            continue
        # Skip lines that are just status indicators (⏺ with parentheses = tool status)
        if line.startswith('⏺') and '(' in line and ')' in line:
            continue
        # This looks like actual Claude output
        if len(line) > MIN_MEANINGFUL_CONTENT_LENGTH:
            # Truncate and add ellipsis
            if len(line) > MAX_LAST_MESSAGE_LENGTH:
                return f'"{line[:MAX_LAST_MESSAGE_LENGTH - 3]}..."'
            return f'"{line}"'

    return None


STATUS_ICONS = {
    "processing": "🔄",
    "locked": "🔒",
    "agent": "🤖",
    "monitoring": "👁",
    "suspended": "⏸",
    "idle": "·",
}


def _get_status_icon(session_info: SessionInfo) -> str:
    """Get status icon for a session."""
    if session_info.is_processing:
        return STATUS_ICONS["processing"]
    if session_info.suspended:
        return STATUS_ICONS["suspended"]
    if session_info.locked:
        return STATUS_ICONS["locked"]
    if session_info.agent:
        return STATUS_ICONS["agent"]
    return STATUS_ICONS["idle"]


def _format_grouped_output(
    sessions: List[SessionInfo],
    shortcuts: bool = True,
    include_message: bool = True,
    group_by: str = "directory",
) -> str:
    """Format sessions grouped by directory, team, or ungrouped.

    Args:
        sessions: List of SessionInfo objects
        shortcuts: Whether to apply path shortcuts
        include_message: Whether to include last message
        group_by: How to group sessions: 'directory', 'team', or 'none'

    Returns:
        Formatted string with sessions grouped accordingly
    """
    if not sessions:
        return "No sessions found"

    # Apply shortcuts
    shortcut_map = PATH_SHORTCUTS if shortcuts else {}

    # Group sessions based on group_by parameter
    groups: Dict[str, List[SessionInfo]] = {}
    for session in sessions:
        if group_by == "team":
            # Group by team
            group_key = session.team or "(no team)"
        elif group_by == "none":
            # No grouping - single group
            group_key = "All Sessions"
        else:
            # Default: group by directory
            cwd = session.cwd
            if cwd:
                # Find the project root (stop at common project directories)
                group_key = _apply_shortcuts(cwd, shortcut_map) or cwd
                # For worktrees, group under the parent project
                if "/.worktrees/" in (cwd or ""):
                    parts = cwd.split("/.worktrees/")
                    group_key = _apply_shortcuts(parts[0], shortcut_map) or parts[0]
            else:
                group_key = "(unknown)"

        if group_key not in groups:
            groups[group_key] = []
        groups[group_key].append(session)

    # Sort groups by number of sessions (descending)
    sorted_groups = sorted(groups.items(), key=lambda x: (-len(x[1]), x[0]))

    # Count unique groups for header
    group_count = len(sorted_groups)

    # Build header based on group_by type
    if group_by == "team":
        header = f"SESSIONS ({len(sessions)} total, {group_count} teams)"
    elif group_by == "none":
        header = f"SESSIONS ({len(sessions)} total)"
    else:
        header = f"SESSIONS ({len(sessions)} total, {group_count} directories)"

    lines = [header]
    lines.append("─" * 80)

    for group_name, group_sessions in sorted_groups:
        # Group header (skip for "none" grouping)
        if group_by != "none":
            lines.append(f"\n{group_name} ({len(group_sessions)} sessions)")

        for session in group_sessions:
            # Session name (truncate to 18 chars)
            name = session.name
            if len(name) > 18:
                name = name[:15] + "..."

            # Relative path within group (show worktree or ".")
            rel_path = "."
            if session.cwd and "/.worktrees/" in session.cwd:
                parts = session.cwd.split("/.worktrees/")
                if len(parts) > 1:
                    # Use more of the worktree path for better identification
                    worktree_name = parts[1]
                    max_len = 18  # Leave room for ".worktrees/" prefix
                    if len(worktree_name) > max_len:
                        worktree_name = worktree_name[:max_len - 3] + "..."
                    rel_path = f".worktrees/{worktree_name}"
            elif session.cwd:
                # For non-worktree, show relative to group
                cwd_short = _apply_shortcuts(session.cwd, shortcut_map)
                if cwd_short and cwd_short != group_name:
                    # Get the part after group_name
                    if cwd_short.startswith(group_name + "/"):
                        rel_path = cwd_short[len(group_name) + 1:]

            # Status icon
            status = _get_status_icon(session)

            # Time since last activity
            activity = _humanize_time(session.last_activity) if session.last_activity else ""

            # Last message or tags
            extra = ""
            if include_message and session.last_message:
                extra = session.last_message[:40]
            elif session.tags:
                extra = f"[{', '.join(session.tags[:2])}]"

            # Format line: "  name     rel_path     status  time  extra"
            line = f"  {name:<18}  {rel_path:<22}  {status:<4}  {activity:<5}  {extra}"
            lines.append(line.rstrip())

    return "\n".join(lines)


def _format_compact_session(session_info: SessionInfo) -> str:
    """Format a session in compact one-line format.

    Format: name  agent  lock_status  [tags]
    Example: worker-1  claude-1  🔒claude-1  [ssh, production]
    """
    name = session_info.name.ljust(12)
    agent = (session_info.agent or "").ljust(12)

    # Lock status with emoji (truncate long agent names to 20 chars)
    if session_info.locked and session_info.locked_by:
        owner = session_info.locked_by[:20]
        lock_status = f"🔒{owner}"
    else:
        lock_status = "🔓"
    lock_status = lock_status.ljust(24)

    # Tags in brackets
    if session_info.tags:
        tags = f"[{', '.join(session_info.tags)}]"
    else:
        tags = "[]"

    return f"{name}  {agent}  {lock_status}  {tags}"


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
    """Core listing/filtering pipeline shared by list_sessions and sessions_v2.

    Applies all filters and enriches each match into a SessionInfo (cwd,
    last_message, last_activity, process_name). Returns the raw response model
    so callers can either serialize it (legacy list_sessions) or hand it to an
    envelope (sessions_v2).

    Args:
        ctx: MCP context, used to pull terminal, agent_registry, lock_manager,
            role_manager, and logger from the lifespan context.
        agents_only: If True, only include sessions with registered agents.
        tag, tags, match: Tag filters (single or multiple with "any"/"all").
        locked, locked_by: Lock filters.
        session_id, agent, team: Identity filters (folded in from resolve_session).
        role: Role filter (folded in from get_sessions_by_role).
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

    # Resolve role filter to a set of session IDs up front (role is folded in
    # from the legacy get_sessions_by_role tool).
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

        # Apply identity filters (folded in from resolve_session).
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


async def list_sessions(
    ctx: Context,
    agents_only: bool = False,
    tag: Optional[str] = None,
    tags: Optional[List[str]] = None,
    match: str = "any",
    locked: Optional[bool] = None,
    locked_by: Optional[str] = None,
    format: str = "grouped",
    group_by: str = "directory",
    include_message: bool = True,
    shortcuts: bool = True,
) -> str:
    """List all available terminal sessions with agent info.

    Args:
        agents_only: If True, only show sessions with registered agents
        tag: Single tag to filter by
        tags: Multiple tags to filter by
        match: How to match multiple tags: 'any' (OR) or 'all' (AND)
        locked: Filter by lock status (True = only locked, False = only unlocked)
        locked_by: Filter by lock owner
        format: Output format: 'grouped' (default, by directory), 'compact' (flat list),
            'full'/'json' (equivalent; both return full JSON for backward compatibility)
        group_by: How to group sessions: 'directory' (by cwd), 'team', or 'none'
        include_message: Include last Claude message in output
        shortcuts: Apply path shortcuts ($MY_REPOS, etc.)
    """
    # Decide whether the chosen format actually consumes last_message.
    #   - compact: never uses last_message
    #   - grouped: only when include_message=True
    #   - full/json: always returned in SessionInfo
    needs_last_message = (
        format in ("full", "json")
        or (format == "grouped" and include_message)
    )
    force_enrich = format in ("grouped", "compact", "full", "json")

    try:
        response = await _list_sessions_core(
            ctx,
            agents_only=agents_only,
            tag=tag,
            tags=tags,
            match=match,
            locked=locked,
            locked_by=locked_by,
            include_message=needs_last_message,
            force_enrich=force_enrich,
        )
    except ValueError as exc:
        return f"Error: {exc}"

    result = response.sessions

    # Format output.
    if format == "grouped":
        return _format_grouped_output(
            result,
            shortcuts=shortcuts,
            include_message=include_message,
            group_by=group_by,
        )
    if format == "compact":
        lines = [_format_compact_session(s) for s in result]
        return "\n".join(lines) if lines else "No sessions found"
    # Full JSON format using the response model.
    return response.model_dump_json(indent=2, exclude_none=True)


async def set_session_tags(
    ctx: Context,
    session_id: str,
    tags: List[str],
    append: bool = True,
) -> str:
    """Set or append tags on a session."""
    lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
    if not lock_manager:
        return "Tag/lock manager unavailable"

    updated = lock_manager.set_tags(session_id, tags, append=append)
    return json.dumps({"session_id": session_id, "tags": updated}, indent=2)


async def manage_session_lock(
    request: ManageSessionLockRequest,
    ctx: Context,
) -> str:
    """Manage session locks with a single consolidated tool.

    Consolidates: lock_session, unlock_session, request_session_access

    Operations:
    - lock: Lock a session for an agent (requires agent)
    - unlock: Unlock a session (agent optional, for owner verification)
    - request_access: Request permission to write to locked session (requires agent)

    Args:
        request: The lock operation request with operation type and parameters

    Returns:
        JSON with operation results
    """
    lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
    if not lock_manager:
        response = ManageSessionLockResponse(
            operation=request.operation,
            success=False,
            session_id=request.session_id,
            error="Tag/lock manager unavailable"
        )
        return response.model_dump_json(indent=2, exclude_none=True)

    try:
        if request.operation == "lock":
            if not request.agent:
                response = ManageSessionLockResponse(
                    operation=request.operation,
                    success=False,
                    session_id=request.session_id,
                    error="agent is required for lock operation"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            acquired, owner = lock_manager.lock_session(request.session_id, request.agent)
            status = "acquired" if acquired else "locked"
            response = ManageSessionLockResponse(
                operation=request.operation,
                success=acquired,
                session_id=request.session_id,
                data={
                    "locked_by": owner,
                    "status": status
                }
            )
            return response.model_dump_json(indent=2, exclude_none=True)

        elif request.operation == "unlock":
            previous_owner = lock_manager.lock_owner(request.session_id)
            success = lock_manager.unlock_session(request.session_id, request.agent)
            current_owner = lock_manager.lock_owner(request.session_id)
            response = ManageSessionLockResponse(
                operation=request.operation,
                success=success,
                session_id=request.session_id,
                data={
                    "unlocked": success,
                    "previous_owner": previous_owner,
                    "locked_by": current_owner
                }
            )
            return response.model_dump_json(indent=2, exclude_none=True)

        elif request.operation == "request_access":
            if not request.agent:
                response = ManageSessionLockResponse(
                    operation=request.operation,
                    success=False,
                    session_id=request.session_id,
                    error="agent is required for request_access operation"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            notification_manager = ctx.request_context.lifespan_context.get("notification_manager")
            allowed, owner = lock_manager.check_permission(request.session_id, request.agent)

            if allowed:
                response = ManageSessionLockResponse(
                    operation=request.operation,
                    success=True,
                    session_id=request.session_id,
                    data={"allowed": True}
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            # Notify the lock owner about the access request
            await notify_lock_request(
                notification_manager,
                owner,
                request.session_id,
                request.agent,
                action_hint="Respond by unlocking if approved",
            )

            response = ManageSessionLockResponse(
                operation=request.operation,
                success=False,
                session_id=request.session_id,
                data={
                    "allowed": False,
                    "locked_by": owner
                }
            )
            return response.model_dump_json(indent=2, exclude_none=True)

        else:
            response = ManageSessionLockResponse(
                operation=request.operation,
                success=False,
                session_id=request.session_id,
                error=f"Unknown operation: {request.operation}"
            )
            return response.model_dump_json(indent=2, exclude_none=True)

    except Exception as e:
        response = ManageSessionLockResponse(
            operation=request.operation,
            success=False,
            session_id=request.session_id,
            error=str(e)
        )
        return response.model_dump_json(indent=2, exclude_none=True)


async def list_my_locks(
    ctx: Context,
    agent: str,
) -> str:
    """List all sessions locked by a specific agent.

    Args:
        agent: The agent name to list locks for

    Returns:
        JSON object with list of locked sessions and their details
    """
    lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
    terminal = ctx.request_context.lifespan_context.get("terminal")
    logger = ctx.request_context.lifespan_context.get("logger")

    if not lock_manager:
        return json.dumps({"error": "Lock manager unavailable"}, indent=2)

    try:
        # Get all session IDs locked by this agent
        locked_session_ids = lock_manager.get_locks_by_agent(agent)

        locks = []
        for session_id in locked_session_ids:
            lock_info = lock_manager.get_lock_info(session_id)
            session_name = None

            # Try to get session name from terminal
            if terminal:
                try:
                    session = await terminal.get_session_by_id(session_id)
                    if session:
                        session_name = session.name
                except Exception as e:
                    if logger:
                        logger.debug(f"Could not get session name for {session_id}: {e}")

            locks.append({
                "session_id": session_id,
                "session_name": session_name,
                "locked_at": lock_info.locked_at.isoformat() if lock_info else None,
                "pending_requests": sorted(lock_info.pending_requests) if lock_info else [],
            })

        result = {
            "agent": agent,
            "lock_count": len(locks),
            "locks": locks,
        }

        if logger:
            logger.info(f"Listed {len(locks)} locks for agent {agent}")

        return json.dumps(result, indent=2)

    except Exception as e:
        if logger:
            logger.error(f"Error listing locks for agent {agent}: {e}")
        return json.dumps({"error": str(e)}, indent=2)


async def set_active_session(request: SetActiveSessionRequest, ctx: Context) -> str:
    """Set the active session for subsequent operations.

    Args:
        request: Session identifier (session_id, name, or agent) and optional focus flag.
                 Set focus=True to also bring the session to the foreground in iTerm.
    """

    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]
    focus_cooldown = ctx.request_context.lifespan_context.get("focus_cooldown")

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
        agent = agent_registry.get_agent_by_session(session.id)
        agent_name = agent.name if agent else None
        agent_registry.active_session = session.id

        if req.focus:
            # Check focus cooldown
            if focus_cooldown:
                logger.info(f"Checking focus cooldown for {session.name} (agent={agent_name})")
                allowed, blocking_agent, remaining = focus_cooldown.check_cooldown(
                    session.id, agent_name
                )
                logger.info(f"Cooldown check result: allowed={allowed}, blocking_agent={blocking_agent}, remaining={remaining:.1f}s")
                if not allowed:
                    error_msg = (
                        f"Focus cooldown active: {remaining:.1f}s remaining. "
                        f"Last focus by agent '{blocking_agent or 'unknown'}'. "
                        f"Wait or use the same agent."
                    )
                    logger.warning(f"Focus blocked for {session.name}: cooldown {remaining:.1f}s")
                    return f"Error: {error_msg}"
            else:
                logger.warning("No focus_cooldown manager available!")

            await terminal.focus_session(session.id)

            # Record focus event
            if focus_cooldown:
                focus_cooldown.record_focus(session.id, agent_name)

            logger.info(f"Set active session and focused: {session.name} ({session.id})")
            return f"Active session set and focused: {session.name} ({session.id})"

        logger.info(f"Set active session to: {session.name} ({session.id})")
        return f"Active session set to: {session.name} ({session.id})"
    except Exception as e:
        logger.error(f"Error setting active session: {e}")
        return f"Error: {e}"


async def create_sessions(request: CreateSessionsRequest, ctx: Context) -> str:
    """Create new terminal sessions with optional agent registration."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    layout_manager = ctx.request_context.lifespan_context["layout_manager"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    profile_manager = ctx.request_context.lifespan_context["profile_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        create_request = _ensure_model(CreateSessionsRequest, request)
        result = await execute_create_sessions(
            create_request, terminal, layout_manager, agent_registry, logger,
            profile_manager=profile_manager
        )
        logger.info(f"Created {len(result.sessions)} sessions")
        return result.model_dump_json(indent=2, exclude_none=True)
    except Exception as e:
        logger.error(f"Error creating sessions: {e}")
        return f"Error: {e}"


async def split_session(request: SplitSessionRequest, ctx: Context) -> str:
    """Split an existing session in a specific direction, creating a new pane.

    Creates a new pane by splitting an existing session. The direction
    determines where the new pane appears relative to the target session.

    Direction mapping:
    - above: New pane appears above the target
    - below: New pane appears below the target
    - left: New pane appears to the left of the target
    - right: New pane appears to the right of the target

    Supports optional agent registration, team assignment, role assignment, and initial command execution.
    """

    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    profile_manager = ctx.request_context.lifespan_context.get("profile_manager")
    role_manager: RoleManager = ctx.request_context.lifespan_context["role_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        split_request = _ensure_model(SplitSessionRequest, request)

        # Resolve the target session
        target_sessions = await resolve_target_sessions(
            terminal, agent_registry, [split_request.target]
        )

        if not target_sessions:
            return json.dumps({
                "error": "Target session not found",
                "target": split_request.target.model_dump()
            }, indent=2)

        if len(target_sessions) > 1:
            return json.dumps({
                "error": "Ambiguous target: multiple sessions matched. Please be more specific.",
                "matched_sessions": [s.id for s in target_sessions]
            }, indent=2)

        source_session = target_sessions[0]

        # Create the split pane
        new_session = await terminal.split_session_directional(
            session_id=source_session.id,
            direction=split_request.direction,
            name=split_request.name,
            profile=split_request.profile
        )

        agent_name = None
        team_name = split_request.team

        # Register agent if specified
        if split_request.agent:
            teams = [team_name] if team_name else []
            agent_registry.register_agent(
                name=split_request.agent,
                session_id=new_session.id,
                teams=teams,
            )
            agent_name = split_request.agent

            # Apply team profile colors if agent is in a team
            if team_name and profile_manager:
                team_profile = profile_manager.get_or_create_team_profile(team_name)
                profile_manager.save_profiles()
                # Apply the team's tab color to the session
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
                                    "Color Space": "sRGB"
                                }
                            }
                        )
                    )
                    logger.debug(f"Applied team '{team_name}' color to split session {new_session.name}")
                except Exception as e:
                    logger.warning(f"Could not apply team color to session: {e}")

        # Launch AI agent CLI if agent_type specified
        if split_request.agent_type:
            cli_command = AGENT_CLI_COMMANDS.get(split_request.agent_type)
            if cli_command:
                logger.info(f"Launching {split_request.agent_type} agent in split session {new_session.name}: {cli_command}")
                await new_session.execute_command(cli_command)
            else:
                logger.warning(f"Unknown agent type: {split_request.agent_type}")
        elif split_request.command:
            # Only run custom command if no agent_type (agent_type takes precedence)
            await new_session.execute_command(split_request.command)

        # Start monitoring if requested
        if split_request.monitor:
            await new_session.start_monitoring(update_interval=0.2)

        # Assign role if specified
        assigned_role = None
        if split_request.role:
            try:
                role_manager.assign_role(
                    session_id=new_session.id,
                    role=split_request.role,
                    role_config=split_request.role_config,
                )
                assigned_role = split_request.role.value
                logger.info(f"Assigned role '{assigned_role}' to split session {new_session.id}")
            except Exception as e:
                logger.warning(f"Could not assign role to split session: {e}")

        response = SplitSessionResponse(
            session_id=new_session.id,
            name=new_session.name,
            agent=agent_name,
            persistent_id=new_session.persistent_id or "",
            source_session_id=source_session.id,
            direction=split_request.direction,
            role=assigned_role
        )

        logger.info(f"Split session {source_session.id} ({split_request.direction}) -> {new_session.id}")
        return response.model_dump_json(indent=2, exclude_none=True)

    except Exception as e:
        logger.error(f"Error splitting session: {e}")
        return json.dumps({"error": str(e)}, indent=2)


def register(mcp):
    """Register session management tools with the FastMCP instance."""
    mcp.tool()(list_sessions)
    mcp.tool()(set_session_tags)
    mcp.tool()(manage_session_lock)
    mcp.tool()(list_my_locks)
    mcp.tool()(set_active_session)
    mcp.tool()(create_sessions)
    mcp.tool()(split_session)
