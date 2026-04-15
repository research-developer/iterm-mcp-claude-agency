"""MCP server implementation for iTerm2 controller using the official MCP Python SDK.

This version supports parallel multi-session operations with agent/team management.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator, Dict, List, Optional, Any

import iterm2
from mcp.server.fastmcp import FastMCP, Context

from core.layouts import LayoutManager, LayoutType
from core.session import ItermSession
from core.terminal import ItermTerminal
from core.agents import AgentRegistry, CascadingMessage, SendTarget
from utils.telemetry import TelemetryEmitter
from utils.otel import (
    init_tracing,
    shutdown_tracing,
    trace_operation,
    add_span_attributes,
)
from core.tags import SessionTagLockManager, FocusCooldownManager
from core.profiles import ProfileManager, get_profile_manager
from core.feedback import (
    FeedbackCategory,
    FeedbackStatus,
    FeedbackTriggerType,
    FeedbackContext,
    FeedbackEntry,
    FeedbackConfig,
    FeedbackHookManager,
    FeedbackCollector,
    FeedbackRegistry,
    FeedbackForker,
    GitHubIntegration,
)
from core.services import (
    ServicePriority,
    ServiceConfig,
    ServiceManager,
    get_service_manager,
)
from core.service_hooks import (
    ServiceHookManager,
    get_service_hook_manager,
)
from core.memory import SQLiteMemoryStore
from core.dashboard import start_dashboard
from core.models import (
    SessionTarget,
    SessionMessage,
    WriteToSessionsRequest,
    WriteResult,
    WriteToSessionsResponse,
    ReadTarget,
    ReadSessionsRequest,
    ReadSessionsResponse,
    SessionOutput,
    CreateSessionsRequest,
    CreatedSession,
    CreateSessionsResponse,
    CascadeMessageRequest,
    CascadeResult,
    CascadeMessageResponse,
    RegisterAgentRequest,
    CreateTeamRequest,
    SetActiveSessionRequest,
    PlaybookCommandResult,
    OrchestrateRequest,
    OrchestrateResponse,
    ModifySessionsRequest,
    SessionModification,
    ModificationResult,
    ModifySessionsResponse,
    # Agent type support
    AGENT_CLI_COMMANDS,
    # Notification models
    AgentNotification,
    GetNotificationsRequest,
    GetNotificationsResponse,
    # Wait for agent models
    WaitForAgentRequest,
    WaitResult,
    # Manager models
    CreateManagerRequest,
    CreateManagerResponse,
    DelegateTaskRequest,
    TaskResultResponse,
    TaskStepSpec,
    TaskPlanSpec,
    ExecutePlanRequest,
    PlanResultResponse,
    AddWorkerRequest,
    RemoveWorkerRequest,
    ManagerInfoResponse,
    # Workflow event models
    TriggerEventRequest,
    TriggerEventResponse,
    EventInfo,
    WorkflowEventInfo,
    ListWorkflowEventsResponse,
    EventHistoryEntry,
    GetEventHistoryRequest,
    GetEventHistoryResponse,
    PatternSubscriptionRequest,
    PatternSubscriptionResponse,
    # Role-based session specialization
    SessionRole,
    RoleConfig,
    DEFAULT_ROLE_CONFIGS,
    # Session info models (Issue #52)
    SessionInfo,
    ListSessionsRequest,
    ListSessionsResponse,
    # Consolidated operations
    ManageMemoryRequest,
    ManageMemoryResponse,
    ManageServicesRequest,
    ManageServicesResponse,
    ManageSessionLockRequest,
    ManageSessionLockResponse,
    ManageTeamsRequest,
    ManageTeamsResponse,
    ManageManagersRequest,
    ManageManagersResponse,
    # Split session models (Issue #85)
    SplitSessionRequest,
    SplitSessionResponse,
    # Agent hooks models
    ManageAgentHooksRequest,
    ManageAgentHooksResponse,
)
from core.manager import (
    SessionRole as ManagerSessionRole,
    TaskStep,
    TaskPlan,
    DelegationStrategy,
    ManagerAgent,
    ManagerRegistry,
)
from core.flows import (
    EventBus,
    EventPriority,
    FlowManager,
    get_event_bus,
    get_flow_manager,
    trigger,
)
from core.roles import RoleManager

# Global references for resources (set during lifespan)
_terminal: Optional[ItermTerminal] = None
_logger: Optional[logging.Logger] = None
_agent_registry: Optional[AgentRegistry] = None
_telemetry: Optional[TelemetryEmitter] = None
_telemetry_server_task: Optional[asyncio.Task] = None
_notification_manager: Optional["NotificationManager"] = None
_tag_lock_manager: Optional[SessionTagLockManager] = None
_focus_cooldown: Optional[FocusCooldownManager] = None
_feedback_registry: Optional[FeedbackRegistry] = None
_feedback_hook_manager: Optional[FeedbackHookManager] = None
_feedback_forker: Optional[FeedbackForker] = None
_github_integration: Optional[GitHubIntegration] = None
_profile_manager: Optional[ProfileManager] = None
_service_manager: Optional[ServiceManager] = None
_service_hook_manager: Optional[ServiceHookManager] = None
_manager_registry: Optional[ManagerRegistry] = None
_event_bus: Optional[EventBus] = None
_flow_manager: Optional[FlowManager] = None
_role_manager: Optional[RoleManager] = None
_memory_store: Optional[SQLiteMemoryStore] = None


# ============================================================================
# NOTIFICATION MANAGER
# ============================================================================

class NotificationManager:
    """Manages agent notifications with ring buffer storage."""

    # Status icons for compact display
    STATUS_ICONS = {
        "info": "ℹ",
        "warning": "⚠",
        "error": "✗",
        "success": "✓",
        "blocked": "⏸",
    }

    def __init__(self, max_per_agent: int = 50, max_total: int = 200):
        self._notifications: List[AgentNotification] = []
        self._max_per_agent = max_per_agent
        self._max_total = max_total
        self._lock = asyncio.Lock()

    async def add(self, notification: AgentNotification) -> None:
        """Add a notification, maintaining ring buffer limits."""
        async with self._lock:
            self._notifications.append(notification)
            # Trim to max total
            if len(self._notifications) > self._max_total:
                self._notifications = self._notifications[-self._max_total:]

    async def add_simple(
        self,
        agent: str,
        level: str,
        summary: str,
        context: Optional[str] = None,
        action_hint: Optional[str] = None,
    ) -> None:
        """Convenience method to add a notification."""
        notification = AgentNotification(
            agent=agent,
            timestamp=datetime.now(),
            level=level,  # type: ignore
            summary=summary[:100],
            context=context,
            action_hint=action_hint,
        )
        await self.add(notification)

    async def get(
        self,
        limit: int = 10,
        level: Optional[str] = None,
        agent: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> List[AgentNotification]:
        """Get notifications with optional filters."""
        async with self._lock:
            result = self._notifications.copy()

        # Apply filters
        if level:
            result = [n for n in result if n.level == level]
        if agent:
            result = [n for n in result if n.agent == agent]
        if since:
            result = [n for n in result if n.timestamp >= since]

        # Return most recent first, limited
        return sorted(result, key=lambda n: n.timestamp, reverse=True)[:limit]

    async def get_latest_per_agent(self) -> Dict[str, AgentNotification]:
        """Get the most recent notification for each agent."""
        async with self._lock:
            latest: Dict[str, AgentNotification] = {}
            for n in reversed(self._notifications):
                if n.agent not in latest:
                    latest[n.agent] = n
            return latest

    def format_compact(self, notifications: List[AgentNotification]) -> str:
        """Format notifications for compact TUI display."""
        if not notifications:
            return "━━━ No notifications ━━━"

        lines = ["━━━ Agent Status ━━━"]
        for n in notifications:
            icon = self.STATUS_ICONS.get(n.level, "?")
            lines.append(f"{n.agent:<12} {icon} {n.summary}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)


@asynccontextmanager
async def iterm_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage the lifecycle of iTerm2 connections and resources.

    Args:
        server: The FastMCP server instance

    Yields:
        A dictionary containing initialized resources
    """
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(os.path.expanduser("~/.iterm-mcp.log")),
            logging.StreamHandler()
        ]
    )
    logger = logging.getLogger("iterm-mcp-server")
    logger.info("Initializing iTerm2 connection...")

    # Initialize OpenTelemetry tracing
    tracing_enabled = init_tracing()
    if tracing_enabled:
        logger.info("OpenTelemetry tracing initialized successfully")
    else:
        logger.info("OpenTelemetry tracing not available (install with: pip install iterm-mcp[otel])")

    connection = None
    terminal = None
    layout_manager = None
    agent_registry = None
    event_bus = None
    flow_manager = None

    try:
        # Initialize iTerm2 connection
        try:
            connection = await iterm2.Connection.async_create()
            logger.info("iTerm2 connection established successfully")
        except Exception as conn_error:
            logger.error(f"Failed to establish iTerm2 connection: {str(conn_error)}")
            raise

        # Initialize terminal controller
        logger.info("Initializing iTerm terminal controller...")
        log_dir = os.path.expanduser("~/.iterm_mcp_logs")
        terminal = ItermTerminal(
            connection=connection,
            log_dir=log_dir,
            enable_logging=True,
            default_max_lines=100,
            max_snapshot_lines=1000
        )

        try:
            await terminal.initialize()
            logger.info("iTerm terminal controller initialized successfully")
        except Exception as term_error:
            logger.error(f"Failed to initialize iTerm terminal controller: {str(term_error)}")
            raise

        # Initialize layout manager
        logger.info("Initializing layout manager...")
        layout_manager = LayoutManager(terminal)
        logger.info("Layout manager initialized successfully")

        # Initialize agent registry
        logger.info("Initializing agent registry...")
        lock_manager = SessionTagLockManager()
        agent_registry = AgentRegistry(lock_manager=lock_manager)
        logger.info("Agent registry initialized successfully")

        # Initialize telemetry emitter
        logger.info("Initializing telemetry emitter...")
        telemetry = TelemetryEmitter(
            log_manager=getattr(terminal, "log_manager", None),
            agent_registry=agent_registry,
        )
        logger.info("Telemetry emitter initialized successfully")

        # Initialize notification manager
        logger.info("Initializing notification manager...")
        notification_manager = NotificationManager()
        logger.info("Notification manager initialized successfully")

        # Initialize focus cooldown manager
        logger.info("Initializing focus cooldown manager...")
        focus_cooldown = FocusCooldownManager()
        logger.info("Focus cooldown manager initialized (cooldown=2s)")

        # Initialize feedback system
        logger.info("Initializing feedback system...")
        feedback_registry = FeedbackRegistry()
        feedback_hook_manager = FeedbackHookManager()
        feedback_forker = FeedbackForker()
        github_integration = GitHubIntegration()
        logger.info("Feedback system initialized successfully")

        # Initialize profile manager
        logger.info("Initializing profile manager...")
        profile_manager = get_profile_manager(logger)
        logger.info(f"Profile manager initialized with {len(profile_manager.list_team_profiles())} team profiles")

        # Initialize service manager and hooks
        logger.info("Initializing service manager...")
        service_manager = get_service_manager(logger=logger)
        service_manager.set_terminal(terminal)
        service_manager.load_global_config()
        service_hook_manager = get_service_hook_manager(service_manager, logger)
        logger.info("Service manager initialized successfully")

        # Initialize manager registry for hierarchical task delegation
        logger.info("Initializing manager registry...")
        manager_registry = ManagerRegistry()
        logger.info("Manager registry initialized successfully")

        # Initialize event bus and flow manager
        logger.info("Initializing event bus and flow manager...")
        event_bus = get_event_bus()
        flow_manager = get_flow_manager()
        await event_bus.start()
        logger.info("Event bus and flow manager initialized successfully")

        # Initialize role manager
        logger.info("Initializing role manager...")
        role_manager = RoleManager(agent_registry=agent_registry)
        logger.info(f"Role manager initialized with {len(role_manager.list_roles())} role assignments")

        # Initialize memory store
        logger.info("Initializing memory store...")
        memory_store = SQLiteMemoryStore()
        logger.info("Memory store initialized successfully (SQLite with FTS5)")

        # Set global references for resources
        global _terminal, _logger, _agent_registry, _telemetry, _notification_manager
        _terminal = terminal
        _logger = logger
        _agent_registry = agent_registry
        _telemetry = telemetry
        _notification_manager = notification_manager
        global _tag_lock_manager, _focus_cooldown
        _tag_lock_manager = lock_manager
        _focus_cooldown = focus_cooldown
        global _feedback_registry, _feedback_hook_manager, _feedback_forker, _github_integration
        _feedback_registry = feedback_registry
        _feedback_hook_manager = feedback_hook_manager
        _feedback_forker = feedback_forker
        _github_integration = github_integration
        global _profile_manager, _service_manager, _service_hook_manager
        _profile_manager = profile_manager
        _service_manager = service_manager
        _service_hook_manager = service_hook_manager
        global _manager_registry, _event_bus, _flow_manager, _role_manager, _memory_store
        _manager_registry = manager_registry
        _event_bus = event_bus
        _flow_manager = flow_manager
        _role_manager = role_manager
        _memory_store = memory_store

        # Yield the initialized components
        yield {
            "connection": connection,
            "terminal": terminal,
            "layout_manager": layout_manager,
            "agent_registry": agent_registry,
            "telemetry": telemetry,
            "notification_manager": notification_manager,
            "tag_lock_manager": lock_manager,
            "focus_cooldown": focus_cooldown,
            "feedback_registry": feedback_registry,
            "feedback_hook_manager": feedback_hook_manager,
            "feedback_forker": feedback_forker,
            "github_integration": github_integration,
            "profile_manager": profile_manager,
            "service_manager": service_manager,
            "service_hook_manager": service_hook_manager,
            "manager_registry": manager_registry,
            "event_bus": event_bus,
            "flow_manager": flow_manager,
            "role_manager": role_manager,
            "memory_store": memory_store,
            "logger": logger,
            "log_dir": log_dir
        }

    finally:
        # Clean up resources
        logger.info("Shutting down iTerm MCP server...")
        if event_bus:
            await event_bus.stop()

        # Shutdown OpenTelemetry tracing
        shutdown_tracing()
        logger.info("OpenTelemetry tracing shutdown completed")

        logger.info("iTerm MCP server shutdown completed")


# Create an MCP server
mcp = FastMCP(
    name="iTerm2 Controller",
    instructions="Control iTerm2 terminal sessions with parallel multi-agent orchestration",
    lifespan=iterm_lifespan,
    dependencies=["iterm2", "asyncio", "pydantic"]
)

# Register tool modules extracted from this file.
# See iterm_mcpy/tools/__init__.py
from iterm_mcpy.tools import register_all  # noqa: E402
register_all(mcp)


# ============================================================================
# OAUTH METADATA ENDPOINTS (for HTTP transport compatibility)
# ============================================================================
# These routes return proper JSON 404 responses for OAuth discovery endpoints.
# Without these, the MCP client receives plain text "Not Found" which it cannot
# parse as JSON, causing: "Invalid OAuth error response: SyntaxError"

from starlette.requests import Request
from starlette.responses import JSONResponse


@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def oauth_authorization_server_metadata(request: Request) -> JSONResponse:
    """Return JSON 404 for OAuth authorization server metadata.

    The MCP client checks this endpoint for OAuth configuration.
    Returning a proper JSON response allows the client to proceed without auth.
    """
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "error_description": "OAuth authorization server metadata not configured"
        }
    )


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_protected_resource_metadata(request: Request) -> JSONResponse:
    """Return JSON 404 for OAuth protected resource metadata (root path)."""
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "error_description": "OAuth protected resource metadata not configured"
        }
    )


@mcp.custom_route("/.well-known/oauth-protected-resource/mcp", methods=["GET"])
async def oauth_protected_resource_mcp_metadata(request: Request) -> JSONResponse:
    """Return JSON 404 for OAuth protected resource metadata (MCP path)."""
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "error_description": "OAuth protected resource metadata not configured"
        }
    )


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================
# See iterm_mcpy/helpers.py for resolve_session, resolve_target_sessions,
# execute_create_sessions, execute_write_request, execute_read_request, and
# execute_cascade_request. They live in a separate module so tool modules
# can import them without depending on this file (avoids circular imports).
# check_condition and notify_lock_request are re-exported from helpers.py
# below because other code in this file still references them directly.

from iterm_mcpy.helpers import (  # noqa: E402
    check_condition,
    execute_cascade_request,
    execute_create_sessions,
    execute_read_request,
    execute_write_request,
    notify_lock_request,
    resolve_session,
    resolve_target_sessions,
)


def ensure_model(model_cls, payload):
    """Validate or coerce an incoming payload into a Pydantic model."""

    if isinstance(payload, model_cls):
        return payload
    return model_cls.model_validate(payload)


# ============================================================================
# SESSION MANAGEMENT TOOLS
# ============================================================================

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


@mcp.tool()
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
    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
    logger = ctx.request_context.lifespan_context["logger"]

    # Build filter description for logging
    filters = []
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

    filter_desc = f" [{', '.join(filters)}]" if filters else ""
    logger.info(f"Listing sessions{filter_desc}")

    sessions = list(terminal.sessions.values())
    result: List[SessionInfo] = []

    # Combine single tag and multiple tags for filtering
    all_filter_tags = []
    if tag:
        all_filter_tags.append(tag)
    if tags:
        all_filter_tags.extend(tags)

    # Validate lock_manager availability when tag/lock filters are used
    requires_lock_manager = all_filter_tags or locked is not None or locked_by is not None
    if requires_lock_manager and lock_manager is None:
        logger.warning("Tag/lock filtering requested but tag_lock_manager is not available")
        return "Error: Tag and lock filtering requires the tag_lock_manager to be initialized"

    for session in sessions:
        agent_obj = agent_registry.get_agent_by_session(session.id)

        # Apply agents_only filter
        if agents_only and agent_obj is None:
            continue

        # Get lock info
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

        # Apply tag filter
        if all_filter_tags:
            if match == "all":
                if not lock_manager or not lock_manager.has_all_tags(session.id, all_filter_tags):
                    continue
            else:  # "any" match
                if not lock_manager or not lock_manager.has_any_tags(session.id, all_filter_tags):
                    continue

        # Apply locked filter
        if locked is not None:
            if locked and not is_locked:
                continue
            if not locked and is_locked:
                continue

        # Apply locked_by filter
        if locked_by is not None:
            if lock_owner != locked_by:
                continue

        # Gather extended session context (for grouped format)
        session_cwd = None
        last_message = None
        last_activity_dt = None
        process_name = None

        # Gather extended info for all formats (can be slow for many sessions)
        if format in ("grouped", "compact", "full", "json"):
            try:
                # Get CWD from session
                session_cwd = await session.get_cwd()
            except Exception as e:
                logger.debug(f"Error getting CWD for session {session.id}: {e}")

            try:
                # Get screen content for last message
                screen_content = await session.get_screen_contents(max_lines=15)
                last_message = _extract_last_message(screen_content)
            except Exception as e:
                logger.debug(f"Error getting screen for session {session.id}: {e}")

            # Convert last_update_time to datetime
            try:
                last_update = getattr(session, "last_update_time", None)
                if last_update:
                    last_activity_dt = datetime.fromtimestamp(last_update)
            except Exception as e:
                logger.debug(f"Error converting last_update_time for session {session.id}: {e}")

            # Extract process name from session name (e.g., "name (process)")
            name = session.name
            if "(" in name and name.endswith(")"):
                process_name = name[name.rfind("(") + 1:-1]

        # Build SessionInfo
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
            # Extended context
            cwd=session_cwd,
            last_activity=last_activity_dt,
            last_message=last_message,
            process_name=process_name,
        )
        result.append(session_info)

    logger.info(f"Found {len(result)} active sessions")

    # Format output
    if format == "grouped":
        # New grouped format (default)
        return _format_grouped_output(
            result,
            shortcuts=shortcuts,
            include_message=include_message,
            group_by=group_by
        )
    elif format == "compact":
        # Legacy compact format (flat list)
        lines = [_format_compact_session(s) for s in result]
        return "\n".join(lines) if lines else "No sessions found"
    else:
        # Full JSON format using the response model
        response = ListSessionsResponse(
            sessions=result,
            total_count=len(result),
            filter_applied=bool(filters),
        )
        return response.model_dump_json(indent=2, exclude_none=True)


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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
        req = ensure_model(SetActiveSessionRequest, request)
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


@mcp.tool()
async def create_sessions(request: CreateSessionsRequest, ctx: Context) -> str:
    """Create new terminal sessions with optional agent registration."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    layout_manager = ctx.request_context.lifespan_context["layout_manager"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    profile_manager = ctx.request_context.lifespan_context["profile_manager"]
    lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
    notification_manager = ctx.request_context.lifespan_context.get("notification_manager")
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        create_request = ensure_model(CreateSessionsRequest, request)
        result = await execute_create_sessions(
            create_request, terminal, layout_manager, agent_registry, logger,
            profile_manager=profile_manager
        )
        logger.info(f"Created {len(result.sessions)} sessions")
        return result.model_dump_json(indent=2, exclude_none=True)
    except Exception as e:
        logger.error(f"Error creating sessions: {e}")
        return f"Error: {e}"


@mcp.tool()
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
        split_request = ensure_model(SplitSessionRequest, request)

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


# ============================================================================
# COMMAND EXECUTION TOOLS (Array-based)
# ============================================================================

@mcp.tool()
async def write_to_sessions(request: WriteToSessionsRequest, ctx: Context) -> str:
    """Write messages to one or more sessions using the gRPC-aligned schema."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
    notification_manager = ctx.request_context.lifespan_context.get("notification_manager")
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        write_request = ensure_model(WriteToSessionsRequest, request)
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


@mcp.tool()
async def read_sessions(request: ReadSessionsRequest, ctx: Context) -> str:
    """Read output from one or more sessions."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        read_request = ensure_model(ReadSessionsRequest, request)
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


@mcp.tool()
async def send_cascade_message(request: CascadeMessageRequest, ctx: Context) -> str:
    """Send cascading messages to agents/teams."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        cascade_request = ensure_model(CascadeMessageRequest, request)
        result = await execute_cascade_request(cascade_request, terminal, agent_registry, logger)
        return result.model_dump_json(indent=2, exclude_none=True)
    except Exception as e:
        logger.error(f"Error in send_cascade_message: {e}")
        return f"Error: {e}"


@mcp.tool()
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


@mcp.tool()
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


# ============================================================================
# ORCHESTRATION TOOLS
# ============================================================================

@mcp.tool()
async def orchestrate_playbook(request: OrchestrateRequest, ctx: Context) -> str:
    """Execute a high-level playbook (layout + commands + cascade + reads)."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    layout_manager = ctx.request_context.lifespan_context["layout_manager"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    profile_manager = ctx.request_context.lifespan_context["profile_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        orchestration_request = ensure_model(OrchestrateRequest, request)
        playbook = orchestration_request.playbook

        response = OrchestrateResponse()

        if playbook.layout:
            response.layout = await execute_create_sessions(
                playbook.layout, terminal, layout_manager, agent_registry, logger,
                profile_manager=profile_manager
            )

        command_results: List[PlaybookCommandResult] = []
        for command in playbook.commands:
            write_request = WriteToSessionsRequest(
                messages=command.messages,
                parallel=command.parallel,
                skip_duplicates=command.skip_duplicates,
            )
            write_result = await execute_write_request(
                write_request,
                terminal,
                agent_registry,
                logger,
                lock_manager=lock_manager,
                notification_manager=notification_manager,
            )
            command_results.append(PlaybookCommandResult(name=command.name, write_result=write_result))

        response.commands = command_results

        if playbook.cascade:
            response.cascade = await execute_cascade_request(playbook.cascade, terminal, agent_registry, logger)

        if playbook.reads:
            response.reads = await execute_read_request(playbook.reads, terminal, agent_registry, logger)

        logger.info(
            "Playbook completed: layout=%s, commands=%s, cascade=%s, reads=%s",
            bool(response.layout),
            len(response.commands),
            bool(response.cascade),
            bool(response.reads),
        )

        return response.model_dump_json(indent=2, exclude_none=True)
    except Exception as e:
        logger.error(f"Error orchestrating playbook: {e}")
        return f"Error: {e}"


# ============================================================================
# CONTROL & STATUS TOOLS
# ============================================================================

@mcp.tool()
async def send_control_character(control_char: str, target: SessionTarget, ctx: Context) -> str:
    """Send a control character to session(s)."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        target_model = ensure_model(SessionTarget, target)
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


@mcp.tool()
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


@mcp.tool()
async def check_session_status(request: SetActiveSessionRequest, ctx: Context) -> str:
    """Check status of a session."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        req = ensure_model(SetActiveSessionRequest, request)
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


# ============================================================================
# AGENT & TEAM MANAGEMENT TOOLS
# ============================================================================

@mcp.tool()
async def register_agent(request: RegisterAgentRequest, ctx: Context) -> str:
    """Register an agent for a session."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        req = ensure_model(RegisterAgentRequest, request)
        session = await terminal.get_session_by_id(req.session_id)
        if not session:
            return "No matching session found. Provide a valid session_id."

        agent = agent_registry.register_agent(
            name=req.name,
            session_id=session.id,
            teams=req.teams,
            metadata=req.metadata,
        )

        logger.info(f"Registered agent '{agent.name}' for session {session.name}")

        return json.dumps({
            "agent": agent.name,
            "session_id": agent.session_id,
            "session_name": session.name,
            "teams": agent.teams,
            "metadata": agent.metadata,
        }, indent=2)
    except Exception as e:
        logger.error(f"Error registering agent: {e}")
        return f"Error: {e}"


@mcp.tool()
async def list_agents(ctx: Context, team: Optional[str] = None) -> str:
    """List all registered agents.

    Args:
        team: Filter by team name (optional)
    """
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        agents = agent_registry.list_agents(team=team)
        result = [
            {
                "name": a.name,
                "session_id": a.session_id,
                "teams": a.teams
            }
            for a in agents
        ]

        logger.info(f"Listed {len(result)} agents" + (f" in team '{team}'" if team else ""))
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error listing agents: {e}")
        return f"Error: {e}"


@mcp.tool()
async def remove_agent(
    agent_name: str,
    ctx: Context
) -> str:
    """Remove an agent registration.

    Args:
        agent_name: Name of the agent to remove
    """
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        if agent_registry.remove_agent(agent_name):
            logger.info(f"Removed agent '{agent_name}'")
            return f"Agent '{agent_name}' removed successfully"
        else:
            return f"Agent '{agent_name}' not found"
    except Exception as e:
        logger.error(f"Error removing agent: {e}")
        return f"Error: {e}"


@mcp.tool()
async def manage_teams(
    request: ManageTeamsRequest,
    ctx: Context,
) -> str:
    """Manage teams with a single consolidated tool.

    Consolidates: create_team, list_teams, remove_team, assign_agent_to_team, remove_agent_from_team

    Operations:
    - create: Create a new team (requires team_name)
    - list: List all teams
    - remove: Remove a team (requires team_name)
    - assign_agent: Add an agent to a team (requires team_name and agent_name)
    - remove_agent: Remove an agent from a team (requires team_name and agent_name)

    Args:
        request: The team operation request with operation type and parameters

    Returns:
        JSON with operation results
    """
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    profile_manager = ctx.request_context.lifespan_context["profile_manager"]
    service_hook_manager = ctx.request_context.lifespan_context["service_hook_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        if request.operation == "create":
            if not request.team_name:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="team_name is required for create operation"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            # Check service hooks before creating team
            hook_result = await service_hook_manager.pre_create_team_hook(
                team_name=request.team_name,
                repo_path=request.repo_path
            )

            # Build response with hook information
            response_data = {}

            # If hook requires prompt, return the hook result for agent to decide
            if hook_result.prompt_required:
                response_data["service_prompt"] = {
                    "message": hook_result.message,
                    "inactive_services": [
                        {
                            "name": s.name,
                            "display_name": s.effective_display_name,
                            "priority": s.priority.value,
                        }
                        for s in hook_result.inactive_services
                    ],
                    "action_required": True,
                }
                logger.info(f"Service hook prompting for team '{request.team_name}': {hook_result.message}")

            # If hook says don't proceed, return error
            if not hook_result.proceed:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error=hook_result.message,
                    data={"proceed": False}
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            # Add info about auto-started services
            if hook_result.auto_started:
                response_data["auto_started_services"] = [
                    s.name for s in hook_result.auto_started
                ]

            team = agent_registry.create_team(
                name=request.team_name,
                description=request.description,
                parent_team=request.parent_team,
            )

            # Create a profile for the team with auto-assigned color
            team_profile = profile_manager.get_or_create_team_profile(team.name)
            profile_manager.save_profiles()

            logger.info(f"Created team '{team.name}' with profile color hue={team_profile.color.hue:.1f}")

            response_data.update({
                "name": team.name,
                "description": team.description,
                "parent_team": team.parent_team,
                "profile_guid": team_profile.guid,
                "color_hue": round(team_profile.color.hue, 1)
            })

            response = ManageTeamsResponse(
                operation=request.operation,
                success=True,
                data=response_data
            )
            return response.model_dump_json(indent=2, exclude_none=True)

        elif request.operation == "list":
            teams = agent_registry.list_teams()
            result = []
            for t in teams:
                team_info = {
                    "name": t.name,
                    "description": t.description,
                    "parent_team": t.parent_team,
                    "member_count": len(agent_registry.list_agents(team=t.name))
                }
                # Include profile info if available
                team_profile = profile_manager.get_team_profile(t.name)
                if team_profile:
                    team_info["profile_guid"] = team_profile.guid
                    team_info["color_hue"] = round(team_profile.color.hue, 1)
                result.append(team_info)

            logger.info(f"Listed {len(result)} teams")
            response = ManageTeamsResponse(
                operation=request.operation,
                success=True,
                data={"teams": result, "count": len(result)}
            )
            return response.model_dump_json(indent=2, exclude_none=True)

        elif request.operation == "remove":
            if not request.team_name:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="team_name is required for remove operation"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            if agent_registry.remove_team(request.team_name):
                # Also remove the team's profile
                profile_removed = profile_manager.remove_team_profile(request.team_name)
                if profile_removed:
                    profile_manager.save_profiles()
                    logger.info(f"Removed team '{request.team_name}' and its profile")
                else:
                    logger.info(f"Removed team '{request.team_name}' (no profile to remove)")

                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=True,
                    data={"team_name": request.team_name, "profile_removed": profile_removed}
                )
                return response.model_dump_json(indent=2, exclude_none=True)
            else:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error=f"Team '{request.team_name}' not found"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

        elif request.operation == "assign_agent":
            if not request.team_name or not request.agent_name:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="team_name and agent_name are required for assign_agent operation"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            if agent_registry.assign_to_team(request.agent_name, request.team_name):
                logger.info(f"Added agent '{request.agent_name}' to team '{request.team_name}'")
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=True,
                    data={"team_name": request.team_name, "agent_name": request.agent_name}
                )
                return response.model_dump_json(indent=2, exclude_none=True)
            else:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="Failed to add agent to team (agent not found or already member)"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

        elif request.operation == "remove_agent":
            if not request.team_name or not request.agent_name:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="team_name and agent_name are required for remove_agent operation"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            if agent_registry.remove_from_team(request.agent_name, request.team_name):
                logger.info(f"Removed agent '{request.agent_name}' from team '{request.team_name}'")
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=True,
                    data={"team_name": request.team_name, "agent_name": request.agent_name}
                )
                return response.model_dump_json(indent=2, exclude_none=True)
            else:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="Failed to remove agent from team (agent not found or not a member)"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

        else:
            response = ManageTeamsResponse(
                operation=request.operation,
                success=False,
                error=f"Unknown operation: {request.operation}"
            )
            return response.model_dump_json(indent=2, exclude_none=True)

    except Exception as e:
        logger.error(f"Error in manage_teams: {e}")
        response = ManageTeamsResponse(
            operation=request.operation,
            success=False,
            error=str(e)
        )
        return response.model_dump_json(indent=2, exclude_none=True)


# ============================================================================
# SESSION MODIFICATION TOOLS
# ============================================================================

async def apply_session_modification(
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
            # Just enable/disable without changing color
            await session.set_tab_color(0, 0, 0, modification.tab_color_enabled)
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


@mcp.tool()
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
        req = ensure_model(ModifySessionsRequest, request)
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
            result = await apply_session_modification(
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


# ============================================================================
# MONITORING TOOLS
# ============================================================================

@mcp.tool()
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

        # Create a callback that routes output to the EventBus
        if enable_event_bus:
            async def event_bus_callback(output: str) -> None:
                """Route terminal output to EventBus for pattern matching."""
                try:
                    # Process output against pattern subscriptions
                    triggered = await event_bus.process_terminal_output(
                        session_id=session.id,
                        output=output
                    )
                    if triggered:
                        logger.debug(f"Pattern subscriptions triggered: {triggered}")

                    # Also trigger a generic terminal_output event
                    await event_bus.trigger(
                        event_name="terminal_output",
                        payload={
                            "session_id": session.id,
                            "session_name": session.name,
                            "output": output,
                            "timestamp": time.time()
                        },
                        source=f"session:{session.name}"
                    )
                except Exception as e:
                    logger.error(f"Error in event bus callback: {e}")

            # Remove existing callback if present to avoid duplicates
            if hasattr(session, '_event_bus_callback') and session._event_bus_callback:
                session.remove_monitor_callback(session._event_bus_callback)
                logger.debug(f"Removed existing event bus callback for session: {session.name}")

            # Register the new callback
            session.add_monitor_callback(event_bus_callback)
            # Store callback reference for cleanup
            session._event_bus_callback = event_bus_callback

        await session.start_monitoring(update_interval=0.2)
        await asyncio.sleep(2)

        if session.is_monitoring:
            logger.info(f"Started monitoring for session: {session.name} (event_bus={enable_event_bus})")
            return f"Started monitoring for session: {session.name} (event_bus integration: {enable_event_bus})"
        else:
            return f"Failed to start monitoring for session: {session.name}"
    except Exception as e:
        logger.error(f"Error starting monitoring: {e}")
        return f"Error: {e}"


@mcp.tool()
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

        # Remove event bus callback if present
        if hasattr(session, '_event_bus_callback') and session._event_bus_callback:
            session.remove_monitor_callback(session._event_bus_callback)
            session._event_bus_callback = None

        await session.stop_monitoring()
        logger.info(f"Stopped monitoring for session: {session.name}")
        return f"Stopped monitoring for session: {session.name}"
    except Exception as e:
        logger.error(f"Error stopping monitoring: {e}")
        return f"Error: {e}"


# ============================================================================
# RESOURCES
# ============================================================================

@mcp.resource("terminal://{session_id}/output")
async def get_terminal_output(session_id: str) -> str:
    """Get output from a terminal session."""
    if _terminal is None or _logger is None:
        raise RuntimeError("Server not initialized. Please wait for initialization to complete.")

    terminal = _terminal
    logger = _logger

    try:
        session = await terminal.get_session_by_id(session_id)
        if not session:
            return f"No session found with ID: {session_id}"

        output = await session.get_screen_contents()
        return output
    except Exception as e:
        logger.error(f"Error getting terminal output: {e}")
        return f"Error: {e}"


@mcp.resource("terminal://{session_id}/info")
async def get_terminal_info(session_id: str) -> str:
    """Get information about a terminal session."""
    if _terminal is None or _logger is None:
        raise RuntimeError("Server not initialized. Please wait for initialization to complete.")

    terminal = _terminal
    agent_registry = _agent_registry
    logger = _logger

    try:
        session = await terminal.get_session_by_id(session_id)
        if not session:
            return f"No session found with ID: {session_id}"

        agent = agent_registry.get_agent_by_session(session.id)

        info = {
            "name": session.name,
            "id": session.id,
            "persistent_id": session.persistent_id,
            "agent": agent.name if agent else None,
            "teams": agent.teams if agent else [],
            "is_processing": getattr(session, "is_processing", False),
            "is_monitoring": getattr(session, "is_monitoring", False),
            "max_lines": session.max_lines
        }

        return json.dumps(info, indent=2)
    except Exception as e:
        logger.error(f"Error getting terminal info: {e}")
        return f"Error: {e}"


@mcp.resource("terminal://sessions")
async def list_all_sessions_resource() -> str:
    """Get a list of all terminal sessions."""
    if _terminal is None or _logger is None:
        raise RuntimeError("Server not initialized. Please wait for initialization to complete.")

    terminal = _terminal
    agent_registry = _agent_registry
    logger = _logger

    try:
        sessions = list(terminal.sessions.values())
        result = []

        for session in sessions:
            agent = agent_registry.get_agent_by_session(session.id)
            result.append({
                "id": session.id,
                "name": session.name,
                "persistent_id": session.persistent_id,
                "agent": agent.name if agent else None,
                "teams": agent.teams if agent else [],
                "is_processing": getattr(session, "is_processing", False),
                "is_monitoring": getattr(session, "is_monitoring", False)
            })

        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        return f"Error: {e}"


@mcp.resource("agents://all")
async def list_all_agents_resource() -> str:
    """Get a list of all registered agents."""
    agent_registry = _agent_registry
    logger = _logger

    try:
        agents = agent_registry.list_agents()
        result = [
            {
                "name": a.name,
                "session_id": a.session_id,
                "teams": a.teams
            }
            for a in agents
        ]
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error listing agents: {e}")
        return f"Error: {e}"


@mcp.resource("teams://all")
async def list_all_teams_resource() -> str:
    """Get a list of all teams."""
    agent_registry = _agent_registry
    logger = _logger

    try:
        teams = agent_registry.list_teams()
        result = [
            {
                "name": t.name,
                "description": t.description,
                "parent_team": t.parent_team,
                "member_count": len(agent_registry.list_agents(team=t.name))
            }
            for t in teams
        ]
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error listing teams: {e}")
        return f"Error: {e}"


# ============================================================================
# PROMPTS
# ============================================================================

@mcp.prompt("orchestrate_agents")
def orchestrate_agents_prompt(task: str) -> str:
    """Prompt for orchestrating multiple agents.

    Args:
        task: The task to orchestrate
    """
    return f"""
You're orchestrating multiple Claude agents through iTerm2 sessions.

Task: {task}

Use the following tools to coordinate:
- create_sessions: Create new sessions with agents
- write_to_sessions: Send commands to multiple sessions
- read_sessions: Read output from sessions
- send_cascade_message: Send hierarchical messages to teams/agents

Remember:
- Use teams for logical groupings
- Cascade messages from broad to specific
- Check for duplicates to avoid redundant work
"""


@mcp.prompt("monitor_team")
def monitor_team_prompt(team_name: str) -> str:
    """Prompt for monitoring a team of agents.

    Args:
        team_name: The team to monitor
    """
    return f"""
You're monitoring the '{team_name}' team of agents.

Use read_sessions with team='{team_name}' to check all members.
Watch for:
- Error messages
- Completion signals
- Progress indicators

Coordinate responses as needed using write_to_sessions or send_cascade_message.
"""


# ============================================================================
# NOTIFICATION TOOLS
# See iterm_mcpy/tools/notifications.py
# ============================================================================


# ============================================================================
# WAIT FOR AGENT TOOLS
# See iterm_mcpy/tools/wait.py
# ============================================================================

# Re-export for backward compatibility with tests that import from this module.
from iterm_mcpy.tools.wait import wait_for_agent  # noqa: E402,F401


# ============================================================================
# FEEDBACK SYSTEM TOOLS
# See iterm_mcpy/tools/feedback.py
# ============================================================================


# ============================================================================
# SERVICE MANAGEMENT TOOLS
# See iterm_mcpy/tools/services.py
# ============================================================================


# ============================================================================
# MANAGER AGENT TOOLS
# See iterm_mcpy/tools/managers.py
# ============================================================================


# ============================================================================
# ROLE MANAGEMENT TOOLS
# See iterm_mcpy/tools/roles.py
# ============================================================================


# ============================================================================
# TELEMETRY TOOL
# See iterm_mcpy/tools/telemetry.py for start_telemetry_dashboard tool.
# The telemetry_dashboard resource remains here (resources aren't part of
# this extraction).
# ============================================================================

@mcp.resource("telemetry://dashboard")
async def telemetry_dashboard() -> str:
    """Get the current telemetry dashboard state as JSON."""
    if _terminal is None or _logger is None or _telemetry is None:
        return json.dumps({"error": "Telemetry not initialized"}, indent=2)

    try:
        state = _telemetry.dashboard_state(_terminal)
        return json.dumps(state, indent=2)
    except Exception as e:
        _logger.error(f"Error getting telemetry dashboard: {e}")
        return json.dumps({"error": str(e)}, indent=2)


# ============================================================================
# WORKFLOW EVENT TOOLS
# See iterm_mcpy/tools/workflows.py
# ============================================================================


# ============================================================================
# MEMORY STORE TOOLS
# See iterm_mcpy/tools/memory.py
# ============================================================================


# ============================================================================
# AGENT HOOKS MANAGEMENT
# See iterm_mcpy/tools/agent_hooks.py
# ============================================================================


@mcp.resource("memory://stats")
async def memory_stats_resource() -> str:
    """Get memory store statistics as a resource."""
    if _memory_store is None or _logger is None:
        return json.dumps({"error": "Memory store not initialized"}, indent=2)

    try:
        stats = await _memory_store.get_stats()
        return json.dumps(stats, indent=2)
    except Exception as e:
        _logger.error(f"Error getting memory stats resource: {e}")
        return json.dumps({"error": str(e)}, indent=2)


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Run the MCP server."""
    try:
        mcp.run()
    except KeyboardInterrupt:
        if os.environ.get("ITERM_MCP_CLEAN_EXIT"):
            return
        print("\nServer stopped by user", file=sys.stderr)
        os._exit(0)
    except Exception as e:
        print(f"Error running MCP server: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
