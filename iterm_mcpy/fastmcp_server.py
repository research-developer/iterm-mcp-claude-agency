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


async def _start_telemetry_server(port: int, duration: int = 300) -> str:
    """Start a lightweight HTTP server that streams telemetry JSON."""

    if _telemetry is None or _terminal is None:
        raise RuntimeError("Telemetry not initialized")

    global _telemetry_server_task

    if _telemetry_server_task:
        _telemetry_server_task.cancel()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await _terminal.get_sessions()
            payload = _telemetry.dashboard_state(_terminal)
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

    _telemetry_server_task = asyncio.create_task(serve())
    return f"Telemetry web dashboard running at http://localhost:{port} for {duration}s"


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


def ensure_model(model_cls, payload):
    """Validate or coerce an incoming payload into a Pydantic model."""

    if isinstance(payload, model_cls):
        return payload
    return model_cls.model_validate(payload)


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
        return response.model_dump_json(indent=2)


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
        return response.model_dump_json(indent=2)

    try:
        if request.operation == "lock":
            if not request.agent:
                response = ManageSessionLockResponse(
                    operation=request.operation,
                    success=False,
                    session_id=request.session_id,
                    error="agent is required for lock operation"
                )
                return response.model_dump_json(indent=2)

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
            return response.model_dump_json(indent=2)

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
            return response.model_dump_json(indent=2)

        elif request.operation == "request_access":
            if not request.agent:
                response = ManageSessionLockResponse(
                    operation=request.operation,
                    success=False,
                    session_id=request.session_id,
                    error="agent is required for request_access operation"
                )
                return response.model_dump_json(indent=2)

            notification_manager = ctx.request_context.lifespan_context.get("notification_manager")
            allowed, owner = lock_manager.check_permission(request.session_id, request.agent)

            if allowed:
                response = ManageSessionLockResponse(
                    operation=request.operation,
                    success=True,
                    session_id=request.session_id,
                    data={"allowed": True}
                )
                return response.model_dump_json(indent=2)

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
            return response.model_dump_json(indent=2)

        else:
            response = ManageSessionLockResponse(
                operation=request.operation,
                success=False,
                session_id=request.session_id,
                error=f"Unknown operation: {request.operation}"
            )
            return response.model_dump_json(indent=2)

    except Exception as e:
        response = ManageSessionLockResponse(
            operation=request.operation,
            success=False,
            session_id=request.session_id,
            error=str(e)
        )
        return response.model_dump_json(indent=2)


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
        return result.model_dump_json(indent=2)
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
        return response.model_dump_json(indent=2)

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
        return result.model_dump_json(indent=2)
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
        return result.model_dump_json(indent=2)
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
        return result.model_dump_json(indent=2)
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
    lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
    notification_manager = ctx.request_context.lifespan_context.get("notification_manager")
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
                return response.model_dump_json(indent=2)

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
                return response.model_dump_json(indent=2)

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
            return response.model_dump_json(indent=2)

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
            return response.model_dump_json(indent=2)

        elif request.operation == "remove":
            if not request.team_name:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="team_name is required for remove operation"
                )
                return response.model_dump_json(indent=2)

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
                return response.model_dump_json(indent=2)
            else:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error=f"Team '{request.team_name}' not found"
                )
                return response.model_dump_json(indent=2)

        elif request.operation == "assign_agent":
            if not request.team_name or not request.agent_name:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="team_name and agent_name are required for assign_agent operation"
                )
                return response.model_dump_json(indent=2)

            if agent_registry.assign_to_team(request.agent_name, request.team_name):
                logger.info(f"Added agent '{request.agent_name}' to team '{request.team_name}'")
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=True,
                    data={"team_name": request.team_name, "agent_name": request.agent_name}
                )
                return response.model_dump_json(indent=2)
            else:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="Failed to add agent to team (agent not found or already member)"
                )
                return response.model_dump_json(indent=2)

        elif request.operation == "remove_agent":
            if not request.team_name or not request.agent_name:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="team_name and agent_name are required for remove_agent operation"
                )
                return response.model_dump_json(indent=2)

            if agent_registry.remove_from_team(request.agent_name, request.team_name):
                logger.info(f"Removed agent '{request.agent_name}' from team '{request.team_name}'")
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=True,
                    data={"team_name": request.team_name, "agent_name": request.agent_name}
                )
                return response.model_dump_json(indent=2)
            else:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="Failed to remove agent from team (agent not found or not a member)"
                )
                return response.model_dump_json(indent=2)

        else:
            response = ManageTeamsResponse(
                operation=request.operation,
                success=False,
                error=f"Unknown operation: {request.operation}"
            )
            return response.model_dump_json(indent=2)

    except Exception as e:
        logger.error(f"Error in manage_teams: {e}")
        response = ManageTeamsResponse(
            operation=request.operation,
            success=False,
            error=str(e)
        )
        return response.model_dump_json(indent=2)


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
        return response.model_dump_json(indent=2)

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
# ============================================================================

@mcp.tool()
async def get_notifications(request: GetNotificationsRequest, ctx: Context) -> str:
    """Get recent agent notifications.

    Returns a list of notifications about agent status changes, errors,
    completions, and other events. Use this to stay aware of what's happening
    across all managed agents.
    """
    notification_manager = ctx.request_context.lifespan_context["notification_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        req = ensure_model(GetNotificationsRequest, request)
        notifications = await notification_manager.get(
            limit=req.limit,
            level=req.level,
            agent=req.agent,
            since=req.since,
        )

        response = GetNotificationsResponse(
            notifications=notifications,
            total_count=len(notifications),
            has_more=len(notifications) == req.limit,
        )

        logger.info(f"Retrieved {len(notifications)} notifications")
        return response.model_dump_json(indent=2)

    except Exception as e:
        logger.error(f"Error getting notifications: {e}")
        return f"Error: {e}"


@mcp.tool()
async def get_agent_status_summary(ctx: Context) -> str:
    """Get a compact status summary of all agents.

    Returns a one-line-per-agent summary showing the most recent
    notification for each agent, including lock counts. Great for quick status checks.
    """
    notification_manager = ctx.request_context.lifespan_context["notification_manager"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Get latest notification per agent
        latest = await notification_manager.get_latest_per_agent()

        # Also include agents with no notifications
        all_agents = agent_registry.list_agents()
        for agent in all_agents:
            if agent.name not in latest:
                # Create a placeholder notification
                latest[agent.name] = AgentNotification(
                    agent=agent.name,
                    timestamp=datetime.now(),
                    level="info",
                    summary="No activity recorded",
                )

        notifications = list(latest.values())

        # Build custom format with lock counts
        if not notifications:
            return "━━━ No notifications ━━━"

        lines = ["━━━ Agent Status ━━━"]
        for n in notifications:
            icon = notification_manager.STATUS_ICONS.get(n.level, "?")

            # Get lock info for this agent
            lock_info = ""
            if lock_manager:
                locks = lock_manager.get_locks_by_agent(n.agent)
                lock_count = len(locks)
                if lock_count == 0:
                    lock_info = "[0 locks]"
                elif lock_count == 1:
                    lock_info = f"[1 lock: {locks[0][:12]}]"
                else:
                    lock_info = f"[{lock_count} locks]"

            # Format: agent (12 chars) | icon | summary (truncated) | lock info
            agent_name = n.agent[:12].ljust(12)
            summary = n.summary[:20].ljust(20) if len(n.summary) > 20 else n.summary.ljust(20)
            lines.append(f"{agent_name} {icon} {summary} {lock_info}")

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        formatted = "\n".join(lines)

        logger.info(f"Generated status summary for {len(notifications)} agents")
        return formatted

    except Exception as e:
        logger.error(f"Error generating status summary: {e}")
        return f"Error: {e}"


@mcp.tool()
async def notify(
    agent: str,
    level: str,
    summary: str,
    context: Optional[str] = None,
    action_hint: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """Manually add a notification for an agent.

    Use this to record significant events like task completion,
    errors encountered, or when an agent needs attention.

    Args:
        agent: The agent name
        level: One of: info, warning, error, success, blocked
        summary: Brief one-line summary (max 100 chars)
        context: Optional additional context
        action_hint: Optional suggested next action
    """
    notification_manager = ctx.request_context.lifespan_context["notification_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        await notification_manager.add_simple(
            agent=agent,
            level=level,
            summary=summary,
            context=context,
            action_hint=action_hint,
        )
        logger.info(f"Added notification for {agent}: [{level}] {summary}")
        return f"Notification added for {agent}"

    except Exception as e:
        logger.error(f"Error adding notification: {e}")
        return f"Error: {e}"


# ============================================================================
# WAIT FOR AGENT TOOLS
# ============================================================================

@mcp.tool()
async def wait_for_agent(request: WaitForAgentRequest, ctx: Context) -> str:
    """Wait for an agent to complete or reach idle state.

    This allows an orchestrator to wait for a subagent to finish its current
    task. If the wait times out, returns a progress summary so you can decide
    whether to wait longer or take action.

    Args:
        request: Contains agent name, timeout, and output options

    Returns:
        WaitResult with completion status, elapsed time, and optional output/summary
    """
    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    notification_manager = ctx.request_context.lifespan_context["notification_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        req = ensure_model(WaitForAgentRequest, request)

        # Find the agent
        agent = agent_registry.get_agent(req.agent)
        if not agent:
            return WaitResult(
                agent=req.agent,
                completed=False,
                timed_out=False,
                elapsed_seconds=0,
                status="unknown",
                summary=f"Agent '{req.agent}' not found",
                can_continue_waiting=False,
            ).model_dump_json(indent=2)

        # Get the session
        session = await terminal.get_session_by_id(agent.session_id)
        if not session:
            return WaitResult(
                agent=req.agent,
                completed=False,
                timed_out=False,
                elapsed_seconds=0,
                status="unknown",
                summary=f"Session for agent '{req.agent}' not found",
                can_continue_waiting=False,
            ).model_dump_json(indent=2)

        logger.info(f"Waiting up to {req.wait_up_to}s for agent {req.agent}")

        # Capture initial output for comparison
        initial_output = await session.get_screen_contents()

        # Poll for completion
        start_time = time.time()
        poll_interval = 0.5  # Check every 500ms
        last_output = initial_output

        while True:
            elapsed = time.time() - start_time

            # Check if timed out
            if elapsed >= req.wait_up_to:
                # Timed out - generate summary
                current_output = await session.get_screen_contents()

                summary = None
                if req.summary_on_timeout:
                    # Generate a simple summary based on output changes
                    if current_output != initial_output:
                        lines = current_output.strip().split('\n')
                        last_lines = lines[-3:] if len(lines) > 3 else lines
                        summary = f"Still running. Last output: {' | '.join(last_lines)}"
                    else:
                        summary = "No output change detected during wait period"

                # Add notification
                await notification_manager.add_simple(
                    agent=req.agent,
                    level="info",
                    summary=f"Wait timed out after {int(elapsed)}s",
                    context=summary,
                )

                result = WaitResult(
                    agent=req.agent,
                    completed=False,
                    timed_out=True,
                    elapsed_seconds=elapsed,
                    status="running",
                    output=current_output if req.return_output else None,
                    summary=summary,
                    can_continue_waiting=True,
                )
                logger.info(f"Wait for {req.agent} timed out after {elapsed:.1f}s")
                return result.model_dump_json(indent=2)

            # Check if processing has stopped (idle)
            is_processing = getattr(session, 'is_processing', False)
            if not is_processing:
                # Check if output has stabilized
                current_output = await session.get_screen_contents()
                if current_output == last_output:
                    # Agent appears idle
                    await notification_manager.add_simple(
                        agent=req.agent,
                        level="success",
                        summary=f"Completed after {int(elapsed)}s",
                    )

                    result = WaitResult(
                        agent=req.agent,
                        completed=True,
                        timed_out=False,
                        elapsed_seconds=elapsed,
                        status="idle",
                        output=current_output if req.return_output else None,
                        summary="Agent completed successfully",
                        can_continue_waiting=False,
                    )
                    logger.info(f"Agent {req.agent} completed after {elapsed:.1f}s")
                    return result.model_dump_json(indent=2)

                last_output = current_output

            await asyncio.sleep(poll_interval)

    except Exception as e:
        logger.error(f"Error waiting for agent: {e}")
        return WaitResult(
            agent=request.agent if hasattr(request, 'agent') else "unknown",
            completed=False,
            timed_out=False,
            elapsed_seconds=0,
            status="error",
            summary=str(e),
            can_continue_waiting=False,
        ).model_dump_json(indent=2)


# ============================================================================
# FEEDBACK SYSTEM TOOLS
# ============================================================================

@mcp.tool()
async def submit_feedback(
    ctx: Context,
    title: str,
    description: str,
    category: str = "enhancement",
    agent_name: Optional[str] = None,
    session_id: Optional[str] = None,
    reproduction_steps: Optional[List[str]] = None,
    suggested_improvement: Optional[str] = None,
    error_messages: Optional[List[str]] = None,
) -> str:
    """Submit feedback about the iTerm MCP system.

    This is the manual /feedback command. Use when you have suggestions,
    found bugs, or want to request improvements to the iterm-mcp.

    Args:
        title: Short summary of the feedback
        description: Detailed description of the issue or suggestion
        category: One of: bug, enhancement, ux, performance, docs
        agent_name: Name of the agent submitting (auto-detected if not provided)
        session_id: Session ID (auto-detected from active session if not provided)
        reproduction_steps: Steps to reproduce (for bugs)
        suggested_improvement: What you think should be improved
        error_messages: Any error messages encountered
    """
    feedback_registry = ctx.request_context.lifespan_context["feedback_registry"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    notification_manager = ctx.request_context.lifespan_context["notification_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Get agent info
        if not agent_name and session_id:
            agent = agent_registry.get_agent_by_session(session_id)
            if agent:
                agent_name = agent.name

        if not session_id:
            session_id = agent_registry.active_session or "unknown"

        if not agent_name:
            agent = agent_registry.get_agent_by_session(session_id)
            agent_name = agent.name if agent else "unknown-agent"

        # Collect context
        collector = FeedbackCollector()
        context = await collector.capture_context(
            project_path=os.getcwd(),
            recent_tool_calls=[],  # Would need hook integration for real data
            recent_errors=error_messages or [],
        )

        # Parse category
        try:
            cat = FeedbackCategory(category.lower())
        except ValueError:
            cat = FeedbackCategory.ENHANCEMENT

        # Create feedback entry
        entry = FeedbackEntry(
            agent_id=agent_name,
            agent_name=agent_name,
            session_id=session_id,
            trigger_type=FeedbackTriggerType.MANUAL,
            context=context,
            category=cat,
            title=title,
            description=description,
            reproduction_steps=reproduction_steps,
            suggested_improvement=suggested_improvement,
            error_messages=error_messages,
        )

        # Save to registry (sync method, no await needed)
        feedback_registry.add(entry)

        # Notify
        await notification_manager.add_simple(
            agent=agent_name,
            level="success",
            summary=f"Feedback submitted: {title[:50]}",
            context=f"Feedback ID: {entry.id}",
        )

        logger.info(f"Feedback submitted: {entry.id} by {agent_name}")
        return json.dumps({
            "status": "submitted",
            "feedback_id": entry.id,
            "title": title,
            "category": cat.value,
            "message": "Thank you for your feedback! It has been recorded for review."
        }, indent=2)

    except Exception as e:
        logger.error(f"Error submitting feedback: {e}")
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def check_feedback_triggers(
    ctx: Context,
    agent_name: str,
    session_id: str,
    error_message: Optional[str] = None,
    tool_call_name: Optional[str] = None,
    output_text: Optional[str] = None,
) -> str:
    """Record events and check if feedback triggers should fire.

    Call this to record errors, tool calls, or check for pattern matches
    that might trigger feedback collection.

    Args:
        agent_name: Name of the agent
        session_id: Session ID
        error_message: Error message to record (triggers error threshold)
        tool_call_name: Name of tool called (triggers periodic counter)
        output_text: Text to scan for feedback patterns
    """
    hook_manager = ctx.request_context.lifespan_context["feedback_hook_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        triggered = []
        stats = hook_manager.get_stats(agent_name)

        # Record error and check threshold - record_error returns trigger type if threshold reached
        if error_message:
            trigger_type = hook_manager.record_error(agent_name, error_message)
            if trigger_type == FeedbackTriggerType.ERROR_THRESHOLD:
                triggered.append({
                    "trigger": "error_threshold",
                    "reason": f"Error threshold reached ({stats['error_threshold']} errors)",
                    "error": error_message,
                })

        # Record tool call and check periodic - record_tool_call returns trigger type if threshold reached
        if tool_call_name:
            trigger_type = hook_manager.record_tool_call(agent_name)
            if trigger_type == FeedbackTriggerType.PERIODIC:
                triggered.append({
                    "trigger": "periodic",
                    "reason": f"Periodic check ({stats['tool_call_threshold']} tool calls)",
                })

        # Check for pattern matches - check_pattern returns trigger type if pattern found
        if output_text:
            trigger_type = hook_manager.check_pattern(agent_name, output_text)
            if trigger_type == FeedbackTriggerType.PATTERN_DETECTED:
                triggered.append({
                    "trigger": "pattern",
                    "reason": "Feedback pattern detected in output",
                })

        logger.info(f"Trigger check for {agent_name}: {len(triggered)} triggers fired")
        return json.dumps({
            "agent": agent_name,
            "triggers_fired": triggered,
            "should_collect_feedback": len(triggered) > 0,
        }, indent=2)

    except Exception as e:
        logger.error(f"Error checking triggers: {e}")
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def query_feedback(
    ctx: Context,
    status: Optional[str] = None,
    category: Optional[str] = None,
    agent_name: Optional[str] = None,
    limit: int = 20,
) -> str:
    """Query the feedback registry.

    Args:
        status: Filter by status (pending, triaged, in_progress, resolved, testing, closed)
        category: Filter by category (bug, enhancement, ux, performance, docs)
        agent_name: Filter by agent name who submitted
        limit: Max number of results
    """
    feedback_registry = ctx.request_context.lifespan_context["feedback_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Parse filters
        status_filter = None
        if status:
            try:
                status_filter = FeedbackStatus(status.lower())
            except ValueError:
                pass

        category_filter = None
        if category:
            try:
                category_filter = FeedbackCategory(category.lower())
            except ValueError:
                pass

        # Query
        entries = feedback_registry.query(
            status=status_filter,
            category=category_filter,
            agent_name=agent_name,
            limit=limit,
        )

        # Format results
        results = []
        for entry in entries:
            results.append({
                "id": entry.id,
                "title": entry.title,
                "category": entry.category.value,
                "status": entry.status.value,
                "agent": entry.agent_name,
                "created_at": entry.created_at.isoformat(),
                "github_issue_url": entry.github_issue_url,
            })

        logger.info(f"Query returned {len(results)} feedback entries")
        return json.dumps({
            "count": len(results),
            "entries": results,
        }, indent=2)

    except Exception as e:
        logger.error(f"Error querying feedback: {e}")
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def fork_for_feedback(
    ctx: Context,
    feedback_id: str,
    session_id: str,
) -> str:
    """Fork the current session to a git worktree for safe feedback submission.

    Creates an isolated worktree and forks the Claude conversation there,
    allowing the agent to provide detailed feedback without affecting
    the main codebase.

    Args:
        feedback_id: The feedback ID to associate with the fork
        session_id: The session ID to fork from
    """
    forker = ctx.request_context.lifespan_context["feedback_forker"]
    notification_manager = ctx.request_context.lifespan_context["notification_manager"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        agent = agent_registry.get_agent_by_session(session_id)
        agent_name = agent.name if agent else "unknown"

        # Create worktree
        worktree_path = await forker.create_worktree(feedback_id)

        # Get fork command (the actual forking is done by executing this command)
        fork_command = forker.get_fork_command(session_id, worktree_path)

        await notification_manager.add_simple(
            agent=agent_name,
            level="info",
            summary=f"Forked for feedback: {feedback_id}",
            context=f"Worktree: {worktree_path}",
            action_hint="Continue in the forked session to provide feedback",
        )

        logger.info(f"Created worktree for session {session_id} at {worktree_path}")
        return json.dumps({
            "status": "worktree_created",
            "feedback_id": feedback_id,
            "worktree_path": str(worktree_path),
            "fork_command": fork_command,
            "message": "Worktree created. Execute the fork_command to continue in an isolated environment.",
        }, indent=2)

    except Exception as e:
        logger.error(f"Error forking for feedback: {e}")
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def triage_feedback_to_github(
    ctx: Context,
    feedback_id: str,
    labels: Optional[List[str]] = None,
    assignee: Optional[str] = None,
) -> str:
    """Create a GitHub issue from feedback.

    Triages the feedback into a GitHub issue with proper labels and context.

    Args:
        feedback_id: The feedback ID to triage
        labels: Additional labels for the issue
        assignee: GitHub username to assign
    """
    feedback_registry = ctx.request_context.lifespan_context["feedback_registry"]
    github_integration = ctx.request_context.lifespan_context["github_integration"]
    notification_manager = ctx.request_context.lifespan_context["notification_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Get the feedback entry (sync method, no await needed)
        entry = feedback_registry.get(feedback_id)
        if not entry:
            return json.dumps({"error": f"Feedback {feedback_id} not found"}, indent=2)

        # Create GitHub issue
        issue_url = await github_integration.create_issue(
            feedback=entry,
            labels=labels,
            assignee=assignee,
        )

        if issue_url:
            # Update entry with issue URL (sync method, no await needed)
            feedback_registry.update(
                entry.id,
                github_issue_url=issue_url,
                status=FeedbackStatus.TRIAGED,
            )

            # Notify the agent
            await notification_manager.add_simple(
                agent=entry.agent_name,
                level="success",
                summary=f"Feedback triaged to GitHub",
                context=issue_url,
                action_hint="Check the GitHub issue for updates",
            )

            logger.info(f"Triaged feedback {feedback_id} to {issue_url}")
            return json.dumps({
                "status": "triaged",
                "feedback_id": feedback_id,
                "github_issue_url": issue_url,
            }, indent=2)
        else:
            return json.dumps({
                "status": "failed",
                "error": "Failed to create GitHub issue. Check gh CLI is authenticated.",
            }, indent=2)

    except Exception as e:
        logger.error(f"Error triaging feedback: {e}")
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def notify_feedback_update(
    ctx: Context,
    feedback_id: str,
    update_type: str,
    message: str,
    pr_url: Optional[str] = None,
) -> str:
    """Notify agents about feedback status updates.

    Use this to notify the original agent when their feedback has been
    addressed, a PR is ready for testing, etc.

    Args:
        feedback_id: The feedback ID
        update_type: One of: acknowledged, in_progress, pr_opened, ready_for_testing, resolved
        message: Human-readable update message
        pr_url: URL to the PR if applicable
    """
    feedback_registry = ctx.request_context.lifespan_context["feedback_registry"]
    notification_manager = ctx.request_context.lifespan_context["notification_manager"]
    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Get the feedback entry (sync method, no await needed)
        entry = feedback_registry.get(feedback_id)
        if not entry:
            return json.dumps({"error": f"Feedback {feedback_id} not found"}, indent=2)

        # Update entry status based on update_type
        status_map = {
            "acknowledged": FeedbackStatus.TRIAGED,
            "in_progress": FeedbackStatus.IN_PROGRESS,
            "pr_opened": FeedbackStatus.IN_PROGRESS,
            "ready_for_testing": FeedbackStatus.TESTING,
            "resolved": FeedbackStatus.RESOLVED,
        }

        # Build updates dict
        updates = {}
        if update_type in status_map:
            updates["status"] = status_map[update_type]

        if pr_url:
            updates["github_pr_url"] = pr_url

        # Update entry (sync method, no await needed)
        updated_entry = feedback_registry.update(entry.id, **updates)
        if updated_entry:
            entry = updated_entry

        # Notify the agent
        level = "success" if update_type == "ready_for_testing" else "info"
        action_hint = None
        if update_type == "ready_for_testing":
            action_hint = f"Please test the fix: {pr_url}" if pr_url else "Please test the fix"

        await notification_manager.add_simple(
            agent=entry.agent_name,
            level=level,
            summary=f"Feedback update: {update_type}",
            context=message,
            action_hint=action_hint,
        )

        # Try to send a direct message to the agent's session if available
        agent = agent_registry.get_agent(entry.agent_name)
        if agent:
            session = await terminal.get_session_by_id(agent.session_id)
            if session:
                # Don't execute, just display the notification
                notification_text = f"\n[Feedback {feedback_id}] {update_type}: {message}"
                if pr_url:
                    notification_text += f"\nPR: {pr_url}"
                # Log but don't send to terminal (could be disruptive)
                logger.info(f"Would notify agent {entry.agent_name}: {notification_text}")

        logger.info(f"Notified about feedback {feedback_id}: {update_type}")
        return json.dumps({
            "status": "notified",
            "feedback_id": feedback_id,
            "agent": entry.agent_name,
            "update_type": update_type,
            "new_status": entry.status.value,
        }, indent=2)

    except Exception as e:
        logger.error(f"Error notifying about feedback: {e}")
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def get_feedback_config(
    ctx: Context,
    update: bool = False,
    error_threshold_count: Optional[int] = None,
    periodic_tool_call_count: Optional[int] = None,
    add_pattern: Optional[str] = None,
    remove_pattern: Optional[str] = None,
) -> str:
    """Get or update feedback trigger configuration.

    Args:
        update: If True, apply the provided configuration changes
        error_threshold_count: New error threshold (e.g., 3 = trigger after 3 errors)
        periodic_tool_call_count: New periodic interval (e.g., 100 = trigger every 100 tool calls)
        add_pattern: Regex pattern to add to pattern detection
        remove_pattern: Regex pattern to remove from pattern detection
    """
    hook_manager = ctx.request_context.lifespan_context["feedback_hook_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        if update:
            # Apply updates
            if error_threshold_count is not None:
                hook_manager.config.error_threshold.count = error_threshold_count
            if periodic_tool_call_count is not None:
                hook_manager.config.periodic.tool_call_count = periodic_tool_call_count
            if add_pattern:
                hook_manager.config.pattern.patterns.append(add_pattern)
            if remove_pattern and remove_pattern in hook_manager.config.pattern.patterns:
                hook_manager.config.pattern.patterns.remove(remove_pattern)

            # Save config
            await hook_manager.save_config()
            logger.info("Feedback config updated")

        # Return current config
        config = hook_manager.config
        return json.dumps({
            "enabled": config.enabled,
            "error_threshold": {
                "enabled": config.error_threshold.enabled,
                "count": config.error_threshold.count,
            },
            "periodic": {
                "enabled": config.periodic.enabled,
                "tool_call_count": config.periodic.tool_call_count,
            },
            "pattern": {
                "enabled": config.pattern.enabled,
                "patterns": config.pattern.patterns,
            },
            "github": {
                "repo": config.github.repo,
                "default_labels": config.github.default_labels,
            },
        }, indent=2)

    except Exception as e:
        logger.error(f"Error with feedback config: {e}")
        return json.dumps({"error": str(e)}, indent=2)


# ============================================================================
# SERVICE MANAGEMENT TOOLS (consolidated)
# ============================================================================

@mcp.tool()
async def manage_services(request: ManageServicesRequest, ctx: Context) -> str:
    """Unified service management - consolidates 6 service tools into one.

    Operations:
    - list: List configured services (optional: repo_path, min_priority, include_status)
    - start: Start a service (requires service_name; optional: repo_path)
    - stop: Stop a service (requires service_name)
    - add: Add new service (requires service_name, command; optional: priority, display_name, port, etc.)
    - configure: Update service config (requires service_name; optional: priority, port, command, etc.)
    - list_inactive: Get services that should be running but aren't (requires repo_path)

    Args:
        request: ManageServicesRequest with operation and relevant parameters

    Returns:
        JSON with operation result
    """
    service_manager = ctx.request_context.lifespan_context["service_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        op = request.operation

        # LIST operation
        if op == "list":
            priority = None
            if request.min_priority:
                priority = ServicePriority.from_string(request.min_priority)

            if request.repo_path:
                services = service_manager.get_merged_services(request.repo_path, priority)
            else:
                global_registry = service_manager.load_global_config()
                services = global_registry.services
                if priority:
                    priority_order = [
                        ServicePriority.QUIET,
                        ServicePriority.OPTIONAL,
                        ServicePriority.PREFERRED,
                        ServicePriority.REQUIRED
                    ]
                    min_idx = priority_order.index(priority)
                    services = [
                        s for s in services
                        if priority_order.index(s.priority) >= min_idx
                    ]

            result = []
            for service in services:
                info = {
                    "name": service.name,
                    "display_name": service.effective_display_name,
                    "priority": service.priority.value,
                    "command": service.command,
                    "port": service.port,
                    "working_directory": service.working_directory,
                }
                if request.include_status:
                    is_running = await service_manager.check_service_running(service)
                    info["is_running"] = is_running
                result.append(info)

            return ManageServicesResponse(
                operation=op,
                success=True,
                data={"services": result, "count": len(result), "repo_path": request.repo_path}
            ).model_dump_json(indent=2)

        # START operation
        elif op == "start":
            if not request.service_name:
                raise ValueError("service_name is required for start operation")

            if request.repo_path:
                services = service_manager.get_merged_services(request.repo_path)
            else:
                global_registry = service_manager.load_global_config()
                services = global_registry.services

            service = None
            for s in services:
                if s.name == request.service_name:
                    service = s
                    break

            if not service:
                return ManageServicesResponse(
                    operation=op,
                    success=False,
                    error=f"Service '{request.service_name}' not found",
                    data={"available_services": [s.name for s in services]}
                ).model_dump_json(indent=2)

            state = await service_manager.start_service(service, repo_path=request.repo_path)

            return ManageServicesResponse(
                operation=op,
                success=state.is_running,
                data={
                    "service": request.service_name,
                    "started": state.is_running,
                    "session_id": state.session_id,
                    "error": state.error_message,
                }
            ).model_dump_json(indent=2)

        # STOP operation
        elif op == "stop":
            if not request.service_name:
                raise ValueError("service_name is required for stop operation")

            success = await service_manager.stop_service(request.service_name)

            return ManageServicesResponse(
                operation=op,
                success=success,
                data={"service": request.service_name, "stopped": success}
            ).model_dump_json(indent=2)

        # ADD operation
        elif op == "add":
            if not request.service_name:
                raise ValueError("service_name is required for add operation")
            if not request.command:
                raise ValueError("command is required for add operation")

            service = ServiceConfig(
                name=request.service_name,
                display_name=request.display_name,
                command=request.command,
                priority=ServicePriority.from_string(request.priority or "optional"),
                port=request.port,
                working_directory=request.working_directory,
                repo_patterns=request.repo_patterns or [],
            )

            if request.scope == "repo":
                if not request.repo_path:
                    return ManageServicesResponse(
                        operation=op,
                        success=False,
                        error="repo_path required when scope is 'repo'"
                    ).model_dump_json(indent=2)

                registry = service_manager.load_repo_config(request.repo_path)
                registry.services = [s for s in registry.services if s.name != request.service_name]
                registry.services.append(service)
                service_manager.save_repo_config(request.repo_path, registry)
            else:
                registry = service_manager.load_global_config()
                registry.services = [s for s in registry.services if s.name != request.service_name]
                registry.services.append(service)
                service_manager.save_global_config(registry)

            logger.info(f"Added service '{request.service_name}' to {request.scope} config")

            return ManageServicesResponse(
                operation=op,
                success=True,
                data={"service": request.service_name, "scope": request.scope, "added": True}
            ).model_dump_json(indent=2)

        # CONFIGURE operation
        elif op == "configure":
            if not request.service_name:
                raise ValueError("service_name is required for configure operation")

            if request.scope == "repo":
                if not request.repo_path:
                    return ManageServicesResponse(
                        operation=op,
                        success=False,
                        error="repo_path required when scope is 'repo'"
                    ).model_dump_json(indent=2)
                registry = service_manager.load_repo_config(request.repo_path)
            else:
                registry = service_manager.load_global_config()

            found = False
            for i, service in enumerate(registry.services):
                if service.name == request.service_name:
                    found = True
                    updates = {}
                    if request.priority:
                        updates["priority"] = ServicePriority.from_string(request.priority)
                    if request.port is not None:
                        updates["port"] = request.port
                    if request.command:
                        updates["command"] = request.command
                    if request.working_directory:
                        updates["working_directory"] = request.working_directory

                    updated_data = service.model_dump()
                    updated_data.update(updates)
                    registry.services[i] = ServiceConfig.model_validate(updated_data)
                    break

            if not found:
                return ManageServicesResponse(
                    operation=op,
                    success=False,
                    error=f"Service '{request.service_name}' not found in {request.scope} config"
                ).model_dump_json(indent=2)

            if request.scope == "repo":
                service_manager.save_repo_config(request.repo_path, registry)
            else:
                service_manager.save_global_config(registry)

            logger.info(f"Updated service '{request.service_name}' in {request.scope} config")

            return ManageServicesResponse(
                operation=op,
                success=True,
                data={"service": request.service_name, "scope": request.scope, "updated": True}
            ).model_dump_json(indent=2)

        # LIST_INACTIVE operation
        elif op == "list_inactive":
            if not request.repo_path:
                raise ValueError("repo_path is required for list_inactive operation")

            priority = None
            if request.min_priority:
                priority = ServicePriority.from_string(request.min_priority)

            inactive = await service_manager.get_inactive_services(request.repo_path, priority)

            result = []
            for service in inactive:
                result.append({
                    "name": service.name,
                    "display_name": service.effective_display_name,
                    "priority": service.priority.value,
                    "command": service.command,
                })

            return ManageServicesResponse(
                operation=op,
                success=True,
                data={"inactive_services": result, "count": len(result), "repo_path": request.repo_path}
            ).model_dump_json(indent=2)

        else:
            return ManageServicesResponse(
                operation=op,
                success=False,
                error=f"Unknown operation: {op}"
            ).model_dump_json(indent=2)

    except Exception as e:
        logger.error(f"Error in manage_services ({request.operation}): {e}")
        return ManageServicesResponse(
            operation=request.operation,
            success=False,
            error=str(e)
        ).model_dump_json(indent=2)


# ============================================================================
# MANAGER AGENT TOOLS
# ============================================================================


async def _execute_task_on_worker(
    worker: str,
    task: str,
    timeout_seconds: Optional[int],
    terminal: ItermTerminal,
    agent_registry: AgentRegistry,
    logger: logging.Logger,
) -> tuple[Optional[str], bool, Optional[str]]:
    """Execute a task on a worker agent and return result.

    Args:
        worker: Worker agent name
        task: Command to execute
        timeout_seconds: Optional timeout
        terminal: Terminal instance
        agent_registry: Agent registry
        logger: Logger instance

    Returns:
        Tuple of (output, success, error)
    """
    agent = agent_registry.get_agent(worker)
    if not agent:
        return None, False, f"Worker agent '{worker}' not found"

    session = await terminal.get_session_by_id(agent.session_id)
    if not session:
        return None, False, f"Session for worker '{worker}' not found"

    try:
        # Send the command
        await session.send_text(task + "\n")

        # Wait for command to complete with proper timeout
        # Use a polling approach to check for command completion
        wait_time = timeout_seconds if timeout_seconds else 30
        poll_interval = 0.5
        elapsed = 0.0

        while elapsed < wait_time:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            # Check if session is no longer processing (command completed)
            if hasattr(session, 'is_processing') and not session.is_processing:
                break

        # Read output
        output = await session.get_screen_contents(max_lines=100)

        return output, True, None

    except asyncio.TimeoutError:
        return None, False, f"Task timed out after {timeout_seconds} seconds"
    except Exception as e:
        logger.error(f"Error executing task on worker {worker}: {e}")
        return None, False, str(e)


def _setup_manager_callbacks(
    manager: ManagerAgent,
    terminal: ItermTerminal,
    agent_registry: AgentRegistry,
    logger: logging.Logger,
) -> None:
    """Set up execution callbacks for a manager agent."""

    async def execute_callback(
        worker: str,
        task: str,
        timeout_seconds: Optional[int],
    ) -> tuple[Optional[str], bool, Optional[str]]:
        return await _execute_task_on_worker(
            worker, task, timeout_seconds, terminal, agent_registry, logger
        )

    manager._execute_callback = execute_callback


@mcp.tool()
async def manage_managers(
    request: ManageManagersRequest,
    ctx: Context,
) -> str:
    """Manage manager agents with a single consolidated tool.

    Consolidates: create_manager, list_managers, get_manager_info, remove_manager,
                  add_worker_to_manager, remove_worker_from_manager

    Operations:
    - create: Create a new manager (requires manager_name)
    - list: List all managers
    - get_info: Get info about a manager (requires manager_name)
    - remove: Remove a manager (requires manager_name)
    - add_worker: Add a worker to a manager (requires manager_name, worker_name)
    - remove_worker: Remove a worker from a manager (requires manager_name, worker_name)

    Args:
        request: The manager operation request with operation type and parameters

    Returns:
        JSON with operation results
    """
    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    manager_registry: ManagerRegistry = ctx.request_context.lifespan_context["manager_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        if request.operation == "create":
            if not request.manager_name:
                response = ManageManagersResponse(
                    operation=request.operation,
                    success=False,
                    error="manager_name is required for create operation"
                )
                return response.model_dump_json(indent=2)

            # Convert worker roles from strings to SessionRole
            worker_roles = {}
            for worker, role_str in request.worker_roles.items():
                worker_roles[worker] = ManagerSessionRole(role_str)

            # Create the manager
            manager = manager_registry.create_manager(
                name=request.manager_name,
                workers=request.workers,
                delegation_strategy=DelegationStrategy(request.delegation_strategy),
                worker_roles=worker_roles,
                metadata=request.metadata,
            )

            # Set up execution callbacks
            _setup_manager_callbacks(manager, terminal, agent_registry, logger)

            logger.info(f"Created manager '{request.manager_name}' with {len(request.workers)} workers")

            response = ManageManagersResponse(
                operation=request.operation,
                success=True,
                data={
                    "name": manager.name,
                    "workers": manager.workers,
                    "delegation_strategy": manager.strategy.value,
                    "created": True
                }
            )
            return response.model_dump_json(indent=2)

        elif request.operation == "list":
            managers = manager_registry.list_managers()

            result = []
            for manager in managers:
                result.append({
                    "name": manager.name,
                    "workers": manager.workers,
                    "delegation_strategy": manager.strategy.value,
                    "worker_count": len(manager.workers),
                })

            logger.info(f"Listed {len(managers)} managers")
            response = ManageManagersResponse(
                operation=request.operation,
                success=True,
                data={"managers": result, "count": len(result)}
            )
            return response.model_dump_json(indent=2)

        elif request.operation == "get_info":
            if not request.manager_name:
                response = ManageManagersResponse(
                    operation=request.operation,
                    success=False,
                    error="manager_name is required for get_info operation"
                )
                return response.model_dump_json(indent=2)

            manager = manager_registry.get_manager(request.manager_name)
            if not manager:
                response = ManageManagersResponse(
                    operation=request.operation,
                    success=False,
                    error=f"Manager '{request.manager_name}' not found"
                )
                return response.model_dump_json(indent=2)

            response = ManageManagersResponse(
                operation=request.operation,
                success=True,
                data={
                    "name": manager.name,
                    "workers": manager.workers,
                    "worker_roles": {k: v.value for k, v in manager.worker_roles.items()},
                    "delegation_strategy": manager.strategy.value,
                    "created_at": manager.created_at.isoformat(),
                    "metadata": manager.metadata,
                }
            )
            return response.model_dump_json(indent=2)

        elif request.operation == "remove":
            if not request.manager_name:
                response = ManageManagersResponse(
                    operation=request.operation,
                    success=False,
                    error="manager_name is required for remove operation"
                )
                return response.model_dump_json(indent=2)

            removed = manager_registry.remove_manager(request.manager_name)

            if removed:
                logger.info(f"Removed manager '{request.manager_name}'")
            else:
                logger.warning(f"Manager '{request.manager_name}' not found")

            response = ManageManagersResponse(
                operation=request.operation,
                success=removed,
                data={"manager_name": request.manager_name}
            )
            return response.model_dump_json(indent=2)

        elif request.operation == "add_worker":
            if not request.manager_name or not request.worker_name:
                response = ManageManagersResponse(
                    operation=request.operation,
                    success=False,
                    error="manager_name and worker_name are required for add_worker operation"
                )
                return response.model_dump_json(indent=2)

            manager = manager_registry.get_manager(request.manager_name)
            if not manager:
                response = ManageManagersResponse(
                    operation=request.operation,
                    success=False,
                    error=f"Manager '{request.manager_name}' not found"
                )
                return response.model_dump_json(indent=2)

            role = ManagerSessionRole(request.worker_role) if request.worker_role else None
            manager.add_worker(request.worker_name, role)

            logger.info(f"Added worker '{request.worker_name}' to manager '{request.manager_name}'")

            response = ManageManagersResponse(
                operation=request.operation,
                success=True,
                data={
                    "manager_name": request.manager_name,
                    "worker_name": request.worker_name,
                    "role": request.worker_role
                }
            )
            return response.model_dump_json(indent=2)

        elif request.operation == "remove_worker":
            if not request.manager_name or not request.worker_name:
                response = ManageManagersResponse(
                    operation=request.operation,
                    success=False,
                    error="manager_name and worker_name are required for remove_worker operation"
                )
                return response.model_dump_json(indent=2)

            manager = manager_registry.get_manager(request.manager_name)
            if not manager:
                response = ManageManagersResponse(
                    operation=request.operation,
                    success=False,
                    error=f"Manager '{request.manager_name}' not found"
                )
                return response.model_dump_json(indent=2)

            removed = manager.remove_worker(request.worker_name)

            if removed:
                logger.info(f"Removed worker '{request.worker_name}' from manager '{request.manager_name}'")
            else:
                logger.warning(f"Worker '{request.worker_name}' not found in manager '{request.manager_name}'")

            response = ManageManagersResponse(
                operation=request.operation,
                success=removed,
                data={
                    "manager_name": request.manager_name,
                    "worker_name": request.worker_name
                }
            )
            return response.model_dump_json(indent=2)

        else:
            response = ManageManagersResponse(
                operation=request.operation,
                success=False,
                error=f"Unknown operation: {request.operation}"
            )
            return response.model_dump_json(indent=2)

    except Exception as e:
        logger.error(f"Error in manage_managers: {e}")
        response = ManageManagersResponse(
            operation=request.operation,
            success=False,
            error=str(e)
        )
        return response.model_dump_json(indent=2)


@mcp.tool()
async def delegate_task(
    request: DelegateTaskRequest,
    ctx: Context,
) -> str:
    """Delegate a task through a manager to an appropriate worker.

    The manager selects a worker based on its delegation strategy and the
    required role. The task is executed and optionally validated.

    Args:
        request: Task delegation request with manager, task, and options

    Returns:
        JSON with task execution result
    """
    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    manager_registry: ManagerRegistry = ctx.request_context.lifespan_context["manager_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        manager = manager_registry.get_manager(request.manager)
        if not manager:
            return json.dumps({"error": f"Manager '{request.manager}' not found"}, indent=2)

        # Ensure callbacks are set up
        _setup_manager_callbacks(manager, terminal, agent_registry, logger)

        # Convert role string to ManagerSessionRole if provided
        role = ManagerSessionRole(request.role) if request.role else None

        # Delegate the task
        result = await manager.delegate(
            task=request.task,
            required_role=role,
            validation=request.validation,
            timeout_seconds=request.timeout_seconds,
            retry_count=request.retry_count,
        )

        logger.info(f"Task delegated via manager '{request.manager}': {result.status.value}")

        response = TaskResultResponse(
            task_id=result.task_id,
            task=result.task,
            worker=result.worker,
            status=result.status.value,
            success=result.success,
            output=result.output,
            error=result.error,
            duration_seconds=result.duration_seconds,
            validation_passed=result.validation_passed,
            validation_message=result.validation_message,
        )
        return response.model_dump_json(indent=2)

    except Exception as e:
        logger.error(f"Error delegating task: {e}")
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def execute_plan(
    request: ExecutePlanRequest,
    ctx: Context,
) -> str:
    """Execute a multi-step task plan through a manager.

    The manager orchestrates the execution of multiple steps, handling
    dependencies and parallel execution as specified in the plan.

    Args:
        request: Plan execution request with manager and plan specification

    Returns:
        JSON with plan execution results
    """
    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    manager_registry: ManagerRegistry = ctx.request_context.lifespan_context["manager_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        manager = manager_registry.get_manager(request.manager)
        if not manager:
            return json.dumps({"error": f"Manager '{request.manager}' not found"}, indent=2)

        # Ensure callbacks are set up
        _setup_manager_callbacks(manager, terminal, agent_registry, logger)

        # Convert plan spec to TaskPlan
        steps = []
        for step_spec in request.plan.steps:
            role = ManagerSessionRole(step_spec.role) if step_spec.role else None
            step = TaskStep(
                id=step_spec.id,
                task=step_spec.task,
                role=role,
                optional=step_spec.optional,
                depends_on=step_spec.depends_on,
                validation=step_spec.validation,
                timeout_seconds=step_spec.timeout_seconds,
                retry_count=step_spec.retry_count,
            )
            steps.append(step)

        plan = TaskPlan(
            name=request.plan.name,
            description=request.plan.description,
            steps=steps,
            parallel_groups=request.plan.parallel_groups,
            stop_on_failure=request.plan.stop_on_failure,
        )

        # Execute the plan
        plan_result = await manager.orchestrate(plan)

        logger.info(
            f"Plan '{plan.name}' completed: success={plan_result.success}, "
            f"steps={len(plan_result.results)}"
        )

        # Convert results to response
        result_responses = []
        for result in plan_result.results:
            result_responses.append(TaskResultResponse(
                task_id=result.task_id,
                task=result.task,
                worker=result.worker,
                status=result.status.value,
                success=result.success,
                output=result.output,
                error=result.error,
                duration_seconds=result.duration_seconds,
                validation_passed=result.validation_passed,
                validation_message=result.validation_message,
            ))

        response = PlanResultResponse(
            plan_name=plan_result.plan_name,
            success=plan_result.success,
            results=result_responses,
            duration_seconds=plan_result.duration_seconds,
            stopped_early=plan_result.stopped_early,
            stop_reason=plan_result.stop_reason,
        )
        return response.model_dump_json(indent=2)

    except Exception as e:
        logger.error(f"Error executing plan: {e}")
        return json.dumps({"error": str(e)}, indent=2)

# ============================================================================
# ROLE MANAGEMENT TOOLS
# ============================================================================


@mcp.tool()
async def assign_session_role(
    ctx: Context,
    session_id: str,
    role: str,
    assigned_by: Optional[str] = None,
) -> str:
    """Assign a role to a session for tool access control.

    Args:
        session_id: The iTerm session ID to assign the role to
        role: The role name (devops, builder, debugger, researcher, tester, orchestrator, monitor, custom)
        assigned_by: Optional agent name that is assigning this role (must have can_modify_roles permission)
    """
    role_manager: RoleManager = ctx.request_context.lifespan_context["role_manager"]
    agent_registry: AgentRegistry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Permission check: if assigned_by is provided, verify they have can_modify_roles
        if assigned_by:
            caller_agent = agent_registry.get_agent(assigned_by)
            if caller_agent:
                caller_session_id = caller_agent.session_id
                if not role_manager.can_modify_roles(caller_session_id):
                    return json.dumps({
                        "error": f"Agent '{assigned_by}' does not have permission to modify roles. "
                                 "Only sessions with can_modify_roles=True (e.g., orchestrator) can assign roles."
                    }, indent=2)

        # Convert string to SessionRole enum
        try:
            session_role = SessionRole(role.lower())
        except ValueError:
            valid_roles = [r.value for r in SessionRole]
            return json.dumps({
                "error": f"Invalid role '{role}'. Valid roles are: {valid_roles}"
            }, indent=2)

        assignment = role_manager.assign_role(
            session_id=session_id,
            role=session_role,
            assigned_by=assigned_by,
        )

        logger.info(f"Assigned role {role} to session {session_id}")

        return json.dumps({
            "status": "success",
            "session_id": session_id,
            "role": assignment.role.value,
            "description": assignment.role_config.description,
            "can_spawn_agents": assignment.role_config.can_spawn_agents,
            "can_modify_roles": assignment.role_config.can_modify_roles,
            "priority": assignment.role_config.priority,
            "assigned_at": assignment.assigned_at.isoformat(),
            "assigned_by": assignment.assigned_by,
        }, indent=2)
    except Exception as e:
        logger.error(f"Error assigning role: {e}")
        return json.dumps({"error": str(e)}, indent=2)

@mcp.tool()
async def get_session_role(
    ctx: Context,
    session_id: str,
) -> str:
    """Get the role assignment for a session.

    Args:
        session_id: The iTerm session ID to get the role for
    """
    role_manager: RoleManager = ctx.request_context.lifespan_context["role_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        description = role_manager.describe(session_id)
        return json.dumps(description, indent=2)
    except Exception as e:
        logger.error(f"Error getting session role: {e}")
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def remove_session_role(
    ctx: Context,
    session_id: str,
    removed_by: Optional[str] = None,
) -> str:
    """Remove the role assignment from a session.

    Args:
        session_id: The iTerm session ID to remove the role from
        removed_by: Optional agent name that is removing this role (must have can_modify_roles permission)
    """
    role_manager: RoleManager = ctx.request_context.lifespan_context["role_manager"]
    agent_registry: AgentRegistry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Permission check: if removed_by is provided, verify they have can_modify_roles
        if removed_by:
            caller_agent = agent_registry.get_agent(removed_by)
            if caller_agent:
                caller_session_id = caller_agent.session_id
                if not role_manager.can_modify_roles(caller_session_id):
                    return json.dumps({
                        "error": f"Agent '{removed_by}' does not have permission to modify roles. "
                                 "Only sessions with can_modify_roles=True (e.g., orchestrator) can remove roles."
                    }, indent=2)

        removed = role_manager.remove_role(session_id)
        if removed:
            logger.info(f"Removed role from session {session_id}")
            return json.dumps({
                "status": "success",
                "session_id": session_id,
                "message": "Role removed successfully"
            }, indent=2)
        else:
            return json.dumps({
                "status": "not_found",
                "session_id": session_id,
                "message": "No role was assigned to this session"
            }, indent=2)
    except Exception as e:
        logger.error(f"Error removing session role: {e}")
        return json.dumps({"error": str(e)}, indent=2)

@mcp.tool()
async def list_session_roles(
    ctx: Context,
    role_filter: Optional[str] = None,
) -> str:
    """List all session role assignments.

    Args:
        role_filter: Optional role name to filter by
    """
    role_manager: RoleManager = ctx.request_context.lifespan_context["role_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        filter_role = None
        if role_filter:
            try:
                filter_role = SessionRole(role_filter.lower())
            except ValueError:
                valid_roles = [r.value for r in SessionRole]
                return json.dumps({
                    "error": f"Invalid role filter '{role_filter}'. Valid roles are: {valid_roles}"
                }, indent=2)

        assignments = role_manager.list_roles(role_filter=filter_role)

        result = []
        for assignment in assignments:
            result.append({
                "session_id": assignment.session_id,
                "role": assignment.role.value,
                "description": assignment.role_config.description,
                "priority": assignment.role_config.priority,
                "assigned_at": assignment.assigned_at.isoformat(),
                "assigned_by": assignment.assigned_by,
            })

        return json.dumps({
            "count": len(result),
            "assignments": result,
        }, indent=2)
    except Exception as e:
        logger.error(f"Error listing session roles: {e}")
        return json.dumps({"error": str(e)}, indent=2)

@mcp.tool()
async def list_available_roles(
    ctx: Context,
) -> str:
    """List all available session roles and their default configurations."""
    from core.models import DEFAULT_ROLE_CONFIGS

    try:
        roles = []
        for role in SessionRole:
            config = DEFAULT_ROLE_CONFIGS.get(role)
            if config:
                roles.append({
                    "role": role.value,
                    "description": config.description,
                    "available_tools": config.available_tools,
                    "restricted_tools": config.restricted_tools,
                    "default_commands": config.default_commands,
                    "can_spawn_agents": config.can_spawn_agents,
                    "can_modify_roles": config.can_modify_roles,
                    "priority": config.priority,
                })
            else:
                roles.append({
                    "role": role.value,
                    "description": f"Custom role: {role.value}",
                    "available_tools": [],
                    "restricted_tools": [],
                    "default_commands": [],
                    "can_spawn_agents": False,
                    "can_modify_roles": False,
                    "priority": 3,
                })

        return json.dumps({
            "count": len(roles),
            "roles": roles,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def check_tool_permission(
    ctx: Context,
    session_id: str,
    tool_name: str,
) -> str:
    """Check if a specific tool is allowed for a session based on its role.

    Args:
        session_id: The iTerm session ID to check
        tool_name: The name of the tool to check permission for
    """
    role_manager: RoleManager = ctx.request_context.lifespan_context["role_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        allowed, reason = role_manager.is_tool_allowed(session_id, tool_name)

        assignment = role_manager.get_role(session_id)
        role_info = {
            "role": assignment.role.value if assignment else None,
            "has_role": assignment is not None,
        }

        return json.dumps({
            "session_id": session_id,
            "tool_name": tool_name,
            "allowed": allowed,
            "reason": reason,
            **role_info,
        }, indent=2)
    except Exception as e:
        logger.error(f"Error checking tool permission: {e}")
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def get_sessions_by_role(
    ctx: Context,
    role: str,
) -> str:
    """Get all session IDs that have a specific role assigned.

    Args:
        role: The role name to filter by
    """
    role_manager: RoleManager = ctx.request_context.lifespan_context["role_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        try:
            session_role = SessionRole(role.lower())
        except ValueError:
            valid_roles = [r.value for r in SessionRole]
            return json.dumps({
                "error": f"Invalid role '{role}'. Valid roles are: {valid_roles}"
            }, indent=2)

        session_ids = role_manager.get_sessions_by_role(session_role)

        return json.dumps({
            "role": session_role.value,
            "count": len(session_ids),
            "session_ids": session_ids,
        }, indent=2)
    except Exception as e:
        logger.error(f"Error getting sessions by role: {e}")
        return json.dumps({"error": str(e)}, indent=2)


# ============================================================================
# TELEMETRY TOOL AND RESOURCE
# ============================================================================

@mcp.tool()
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
    telemetry: TelemetryEmitter = ctx.request_context.lifespan_context["telemetry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        message = await start_dashboard(
            telemetry=telemetry,
            terminal=_terminal,
            notification_manager=_notification_manager,
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
# ============================================================================

# Priority level mapping
PRIORITY_MAP = {
    "low": EventPriority.LOW,
    "normal": EventPriority.NORMAL,
    "high": EventPriority.HIGH,
    "critical": EventPriority.CRITICAL,
}


@mcp.tool()
async def trigger_workflow_event(
    ctx: Context,
    event_name: str,
    payload: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
    priority: str = "normal",
    metadata: Optional[Dict[str, Any]] = None,
    immediate: bool = False
) -> str:
    """Trigger a workflow event.

    Events are processed by registered listeners (@listen decorators) and can
    be routed dynamically using @router decorators.

    Args:
        event_name: Name of the event to trigger
        payload: Event payload data (will be passed to listeners)
        source: Source of the event (agent/flow name)
        priority: Event priority: low, normal, high, critical
        metadata: Additional event metadata
        immediate: If True, process synchronously instead of queueing

    Returns:
        JSON response with event info and processing result
    """
    event_bus: EventBus = ctx.request_context.lifespan_context["event_bus"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Map priority string to enum
        priority_enum = PRIORITY_MAP.get(priority.lower(), EventPriority.NORMAL)

        # Trigger the event
        result = await event_bus.trigger(
            event_name=event_name,
            payload=payload,
            source=source,
            priority=priority_enum,
            metadata=metadata or {},
            immediate=immediate
        )

        if immediate and result:
            response = TriggerEventResponse(
                success=result.success,
                event=EventInfo(
                    name=result.event.name,
                    id=result.event.id,
                    source=result.event.source,
                    timestamp=result.event.timestamp,
                    priority=result.event.priority.name.lower()
                ),
                queued=False,
                processed=True,
                routed_to=result.routed_to,
                handler_name=result.handler_name,
                error=result.error
            )
        else:
            # Event was queued (not yet processed, success unknown)
            response = TriggerEventResponse(
                success=True,  # Queuing succeeded, not event processing
                queued=True,
                processed=False,
                error="Event queued for async processing; success indicates queue operation, not event handling"
            )

        logger.info(f"Triggered workflow event: {event_name}")
        return response.model_dump_json(indent=2)

    except Exception as e:
        logger.error(f"Error triggering workflow event: {e}")
        return TriggerEventResponse(
            success=False,
            error=str(e)
        ).model_dump_json(indent=2)


@mcp.tool()
async def list_workflow_events(ctx: Context) -> str:
    """List all registered workflow events.

    Returns information about all events that have listeners, routers,
    or start handlers registered.

    Returns:
        JSON response with list of registered events
    """
    event_bus: EventBus = ctx.request_context.lifespan_context["event_bus"]
    flow_manager: FlowManager = ctx.request_context.lifespan_context["flow_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Get all registered event names
        event_names = await event_bus.get_registered_events()

        # Build detailed info for each event
        events = []
        for name in sorted(event_names):
            listeners = await event_bus._registry.get_listeners(name)
            router = await event_bus._registry.get_router(name)
            start_handler = await event_bus._registry.get_start_handler(name)

            events.append(WorkflowEventInfo(
                event_name=name,
                has_listeners=len(listeners) > 0,
                has_router=router is not None,
                is_start_event=start_handler is not None,
                listener_count=len(listeners)
            ))

        # Get registered flows
        flow_names = flow_manager.list_flows()

        response = ListWorkflowEventsResponse(
            events=events,
            total_count=len(events),
            flows_registered=flow_names
        )

        logger.info(f"Listed {len(events)} workflow events")
        return response.model_dump_json(indent=2)

    except Exception as e:
        logger.error(f"Error listing workflow events: {e}")
        return json.dumps({"error": str(e)}, indent=2)


# ============================================================================
# MEMORY STORE TOOLS
# ============================================================================

# Pattern for safe namespace and key characters
# Allows alphanumeric, underscore, hyphen, and dot
_SAFE_MEMORY_PATTERN = re.compile(r'^[a-zA-Z0-9_\-\.]+$')


def _validate_namespace(namespace: List[str]) -> None:
    """Validate namespace parts contain only safe characters.

    Args:
        namespace: List of namespace parts

    Raises:
        ValueError: If any part contains invalid characters
    """
    if not namespace:
        return  # Empty namespace is valid (root)

    for part in namespace:
        if not part:
            raise ValueError("Namespace parts cannot be empty strings")
        if not _SAFE_MEMORY_PATTERN.match(part):
            raise ValueError(
                f"Invalid namespace part '{part}': only alphanumeric, underscore, hyphen, and dot allowed"
            )


def _validate_key(key: str) -> None:
    """Validate key contains only safe characters.

    Args:
        key: The memory key

    Raises:
        ValueError: If key contains invalid characters
    """
    if not key:
        raise ValueError("Key cannot be empty")
    if not _SAFE_MEMORY_PATTERN.match(key):
        raise ValueError(
            f"Invalid key '{key}': only alphanumeric, underscore, hyphen, and dot allowed"
        )


@mcp.tool()
async def manage_memory(request: ManageMemoryRequest, ctx: Context) -> str:
    """Unified memory store operations - consolidates 8 memory tools into one.

    Operations:
    - store: Save a value (requires namespace, key, value; optional metadata)
    - retrieve: Get a value (requires namespace, key)
    - search: Full-text search (requires namespace, query; optional limit)
    - list_keys: List all keys in namespace (requires namespace)
    - list_namespaces: List namespaces (optional namespace as prefix filter)
    - delete: Delete a key (requires namespace, key)
    - clear: Clear namespace (requires namespace, confirm=True)
    - stats: Get store statistics (no params required)

    Args:
        request: ManageMemoryRequest with operation and relevant parameters

    Returns:
        JSON with operation result
    """
    memory_store_instance = ctx.request_context.lifespan_context.get("memory_store")
    logger = ctx.request_context.lifespan_context["logger"]

    if not memory_store_instance:
        return ManageMemoryResponse(
            operation=request.operation,
            success=False,
            error="Memory store not initialized"
        ).model_dump_json(indent=2)

    try:
        op = request.operation

        # STORE operation
        if op == "store":
            if not request.namespace:
                raise ValueError("namespace is required for store operation")
            if not request.key:
                raise ValueError("key is required for store operation")
            if request.value is None:
                raise ValueError("value is required for store operation")

            _validate_namespace(request.namespace)
            _validate_key(request.key)
            ns_tuple = tuple(request.namespace)
            await memory_store_instance.store(ns_tuple, request.key, request.value, request.metadata)
            logger.info(f"Stored memory: {'/'.join(request.namespace)}/{request.key}")

            return ManageMemoryResponse(
                operation=op,
                success=True,
                data={
                    "status": "stored",
                    "namespace": request.namespace,
                    "key": request.key,
                    "metadata": request.metadata or {}
                }
            ).model_dump_json(indent=2)

        # RETRIEVE operation
        elif op == "retrieve":
            if not request.namespace:
                raise ValueError("namespace is required for retrieve operation")
            if not request.key:
                raise ValueError("key is required for retrieve operation")

            _validate_namespace(request.namespace)
            _validate_key(request.key)
            ns_tuple = tuple(request.namespace)
            memory = await memory_store_instance.retrieve(ns_tuple, request.key)

            if memory:
                logger.info(f"Retrieved memory: {'/'.join(request.namespace)}/{request.key}")
                return ManageMemoryResponse(
                    operation=op,
                    success=True,
                    data={
                        "found": True,
                        "key": memory.key,
                        "value": memory.value,
                        "timestamp": memory.timestamp.isoformat(),
                        "metadata": memory.metadata,
                        "namespace": list(memory.namespace)
                    }
                ).model_dump_json(indent=2)
            else:
                logger.info(f"Memory not found: {'/'.join(request.namespace)}/{request.key}")
                return ManageMemoryResponse(
                    operation=op,
                    success=True,
                    data={"found": False, "namespace": request.namespace, "key": request.key}
                ).model_dump_json(indent=2)

        # SEARCH operation
        elif op == "search":
            if not request.namespace:
                raise ValueError("namespace is required for search operation")
            if not request.query:
                raise ValueError("query is required for search operation")

            _validate_namespace(request.namespace)
            ns_tuple = tuple(request.namespace)
            results = await memory_store_instance.search(ns_tuple, request.query, request.limit)
            logger.info(f"Memory search '{request.query}' in {'/'.join(request.namespace)}: {len(results)} results")

            return ManageMemoryResponse(
                operation=op,
                success=True,
                data={
                    "query": request.query,
                    "namespace": request.namespace,
                    "count": len(results),
                    "results": [
                        {
                            "key": r.memory.key,
                            "value": r.memory.value,
                            "score": r.score,
                            "match_context": r.match_context,
                            "timestamp": r.memory.timestamp.isoformat(),
                            "metadata": r.memory.metadata,
                            "namespace": list(r.memory.namespace)
                        }
                        for r in results
                    ]
                }
            ).model_dump_json(indent=2)

        # LIST_KEYS operation
        elif op == "list_keys":
            if not request.namespace:
                raise ValueError("namespace is required for list_keys operation")

            _validate_namespace(request.namespace)
            ns_tuple = tuple(request.namespace)
            keys = await memory_store_instance.list_keys(ns_tuple)
            logger.info(f"Listed {len(keys)} keys in namespace {'/'.join(request.namespace)}")

            return ManageMemoryResponse(
                operation=op,
                success=True,
                data={"namespace": request.namespace, "count": len(keys), "keys": keys}
            ).model_dump_json(indent=2)

        # LIST_NAMESPACES operation
        elif op == "list_namespaces":
            if request.namespace:
                _validate_namespace(request.namespace)
            prefix_tuple = tuple(request.namespace) if request.namespace else None
            namespaces = await memory_store_instance.list_namespaces(prefix_tuple)
            logger.info(f"Listed {len(namespaces)} namespaces")

            return ManageMemoryResponse(
                operation=op,
                success=True,
                data={
                    "prefix": request.namespace,
                    "count": len(namespaces),
                    "namespaces": [list(ns) for ns in namespaces]
                }
            ).model_dump_json(indent=2)

        # DELETE operation
        elif op == "delete":
            if not request.namespace:
                raise ValueError("namespace is required for delete operation")
            if not request.key:
                raise ValueError("key is required for delete operation")

            _validate_namespace(request.namespace)
            _validate_key(request.key)
            ns_tuple = tuple(request.namespace)
            deleted = await memory_store_instance.delete(ns_tuple, request.key)

            if deleted:
                logger.info(f"Deleted memory: {'/'.join(request.namespace)}/{request.key}")
            else:
                logger.info(f"Memory not found for deletion: {'/'.join(request.namespace)}/{request.key}")

            return ManageMemoryResponse(
                operation=op,
                success=True,
                data={
                    "deleted": deleted,
                    "namespace": request.namespace,
                    "key": request.key,
                    "message": None if deleted else "Memory not found"
                }
            ).model_dump_json(indent=2)

        # CLEAR operation
        elif op == "clear":
            if not request.namespace:
                raise ValueError("namespace is required for clear operation")

            _validate_namespace(request.namespace)

            if not request.confirm:
                return ManageMemoryResponse(
                    operation=op,
                    success=False,
                    error="Confirmation required. Set confirm=True to clear namespace. This permanently deletes all memories.",
                    data={"namespace": request.namespace}
                ).model_dump_json(indent=2)

            ns_tuple = tuple(request.namespace)
            count = await memory_store_instance.clear_namespace(ns_tuple)
            logger.info(f"Cleared namespace {'/'.join(request.namespace)}: {count} memories deleted")

            return ManageMemoryResponse(
                operation=op,
                success=True,
                data={"cleared": True, "namespace": request.namespace, "deleted_count": count}
            ).model_dump_json(indent=2)

        # STATS operation
        elif op == "stats":
            stats = await memory_store_instance.get_stats()
            logger.info("Retrieved memory store stats")

            return ManageMemoryResponse(
                operation=op,
                success=True,
                data=stats
            ).model_dump_json(indent=2)

        else:
            return ManageMemoryResponse(
                operation=op,
                success=False,
                error=f"Unknown operation: {op}"
            ).model_dump_json(indent=2)

    except Exception as e:
        logger.error(f"Error in manage_memory ({request.operation}): {e}")
        return ManageMemoryResponse(
            operation=request.operation,
            success=False,
            error=str(e)
        ).model_dump_json(indent=2)


@mcp.tool()
async def get_workflow_event_history(
    ctx: Context,
    event_name: Optional[str] = None,
    limit: int = 100,
    success_only: bool = False
) -> str:
    """Get workflow event history.

    Args:
        event_name: Filter by event name (optional)
        limit: Max entries to return (default: 100, max: 1000)
        success_only: Only return successfully processed events

    Returns:
        JSON response with event history entries
    """
    event_bus: EventBus = ctx.request_context.lifespan_context["event_bus"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Cap limit
        limit = min(limit, 1000)

        # Get history
        history = await event_bus.get_history(
            event_name=event_name,
            limit=limit,
            success_only=success_only
        )

        # Convert to response format
        entries = [
            EventHistoryEntry(
                event_name=r.event.name,
                event_id=r.event.id,
                source=r.event.source,
                timestamp=r.event.timestamp,
                success=r.success,
                handler_name=r.handler_name,
                routed_to=r.routed_to,
                duration_ms=r.duration_ms,
                error=r.error
            )
            for r in history
        ]

        response = GetEventHistoryResponse(
            entries=entries,
            total_count=len(entries)
        )

        logger.info(f"Retrieved {len(entries)} event history entries")
        return response.model_dump_json(indent=2)

    except Exception as e:
        logger.error(f"Error getting event history: {e}")
        return json.dumps({"error": str(e)}, indent=2)


@mcp.tool()
async def subscribe_to_output_pattern(
    ctx: Context,
    pattern: str,
    event_name: Optional[str] = None
) -> str:
    """Subscribe to terminal output matching a pattern.

    When terminal output matches the pattern, the specified event will be
    triggered with the matched text as payload.

    Args:
        pattern: Regex pattern to match against terminal output
        event_name: Event to trigger on pattern match (optional)

    Returns:
        JSON response with subscription ID
    """
    event_bus: EventBus = ctx.request_context.lifespan_context["event_bus"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Validate pattern
        re.compile(pattern)

        # Create callback
        async def on_match(text: str, match: Any) -> None:
            logger.debug(f"Pattern matched: {pattern} -> {match}")

        # Subscribe
        subscription_id = await event_bus.subscribe_to_pattern(
            pattern=pattern,
            callback=on_match,
            event_name=event_name
        )

        response = PatternSubscriptionResponse(
            subscription_id=subscription_id,
            pattern=pattern,
            event_name=event_name
        )

        logger.info(f"Created pattern subscription: {pattern}")
        return response.model_dump_json(indent=2)

    except re.error as e:
        logger.error(f"Invalid regex pattern: {e}")
        return json.dumps({"error": f"Invalid regex pattern: {e}"}, indent=2)
    except Exception as e:
        logger.error(f"Error creating pattern subscription: {e}")
        return json.dumps({"error": str(e)}, indent=2)


# ============================================================================
# AGENT HOOKS MANAGEMENT
# ============================================================================

@mcp.tool()
async def manage_agent_hooks(request: ManageAgentHooksRequest, ctx: Context) -> str:
    """Unified agent hooks management.

    Operations:
    - get_config: Get current global hooks configuration
    - update_config: Update global hooks configuration
    - get_repo_config: Get hooks config for a specific repo (.iterm/hooks.json)
    - trigger_path_change: Manually trigger path change hook for a session
    - get_stats: Get hook manager statistics
    - set_variable: Set an iTerm user variable to enable/disable hooks per session
    - get_variable: Get an iTerm user variable value

    Args:
        request: ManageAgentHooksRequest with operation and parameters

    Returns:
        JSON with operation result
    """
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        from core.agent_hooks import get_agent_hook_manager, GlobalHooksConfig

        hook_manager = get_agent_hook_manager()
        op = request.operation

        # GET_CONFIG operation
        if op == "get_config":
            config = hook_manager.config
            return ManageAgentHooksResponse(
                operation=op,
                success=True,
                data=config.model_dump()
            ).model_dump_json(indent=2)

        # UPDATE_CONFIG operation
        elif op == "update_config":
            config_updates = {}
            if request.enabled is not None:
                config_updates["enabled"] = request.enabled
            if request.auto_team_assignment is not None:
                config_updates["auto_team_assignment"] = request.auto_team_assignment
            if request.fallback_team_from_repo is not None:
                config_updates["fallback_team_from_repo"] = request.fallback_team_from_repo
            if request.pass_session_id_default is not None:
                config_updates["pass_session_id_default"] = request.pass_session_id_default

            if config_updates:
                # Update config
                current_data = hook_manager.config.model_dump()
                current_data.update(config_updates)
                hook_manager.config = GlobalHooksConfig(**current_data)
                hook_manager.save_global_config()
                logger.info(f"Updated agent hooks config: {config_updates}")

            return ManageAgentHooksResponse(
                operation=op,
                success=True,
                data={
                    "updated": config_updates,
                    "config": hook_manager.config.model_dump()
                }
            ).model_dump_json(indent=2)

        # GET_REPO_CONFIG operation
        elif op == "get_repo_config":
            if not request.repo_path:
                return ManageAgentHooksResponse(
                    operation=op,
                    success=False,
                    error="repo_path is required for get_repo_config operation"
                ).model_dump_json(indent=2)

            repo_config = hook_manager.load_repo_config(request.repo_path)
            return ManageAgentHooksResponse(
                operation=op,
                success=True,
                data={
                    "repo_path": request.repo_path,
                    "config": repo_config.model_dump() if repo_config else None,
                    "config_file": str(Path(request.repo_path) / hook_manager.config.repo_config_filename)
                }
            ).model_dump_json(indent=2)

        # TRIGGER_PATH_CHANGE operation
        elif op == "trigger_path_change":
            if not request.session_id:
                return ManageAgentHooksResponse(
                    operation=op,
                    success=False,
                    error="session_id is required for trigger_path_change operation"
                ).model_dump_json(indent=2)
            if not request.new_path:
                return ManageAgentHooksResponse(
                    operation=op,
                    success=False,
                    error="new_path is required for trigger_path_change operation"
                ).model_dump_json(indent=2)

            result = await hook_manager.on_path_changed(
                request.session_id,
                request.new_path,
                request.agent_name
            )
            logger.info(f"Triggered path change for {request.session_id}: {result.actions_taken}")

            return ManageAgentHooksResponse(
                operation=op,
                success=True,
                data=result.to_dict()
            ).model_dump_json(indent=2)

        # GET_STATS operation
        elif op == "get_stats":
            stats = hook_manager.get_stats()
            return ManageAgentHooksResponse(
                operation=op,
                success=True,
                data=stats
            ).model_dump_json(indent=2)

        # SET_VARIABLE operation - Set iTerm user variable to enable/disable hooks
        elif op == "set_variable":
            if not request.session_id:
                return ManageAgentHooksResponse(
                    operation=op,
                    success=False,
                    error="session_id is required for set_variable operation"
                ).model_dump_json(indent=2)
            if not request.variable_name:
                return ManageAgentHooksResponse(
                    operation=op,
                    success=False,
                    error="variable_name is required for set_variable operation"
                ).model_dump_json(indent=2)

            from core.iterm_path_monitor import set_user_variable
            connection = ctx.request_context.lifespan_context["connection"]
            success = await set_user_variable(
                connection,
                request.session_id,
                request.variable_name,
                request.variable_value or ""
            )
            logger.info(f"Set variable {request.variable_name}={request.variable_value} on session {request.session_id}: success={success}")

            return ManageAgentHooksResponse(
                operation=op,
                success=success,
                data={
                    "session_id": request.session_id,
                    "variable_name": request.variable_name,
                    "variable_value": request.variable_value
                },
                error=None if success else "Failed to set variable (session may not exist or iTerm not connected)"
            ).model_dump_json(indent=2)

        # GET_VARIABLE operation - Get iTerm user variable
        elif op == "get_variable":
            if not request.session_id:
                return ManageAgentHooksResponse(
                    operation=op,
                    success=False,
                    error="session_id is required for get_variable operation"
                ).model_dump_json(indent=2)
            if not request.variable_name:
                return ManageAgentHooksResponse(
                    operation=op,
                    success=False,
                    error="variable_name is required for get_variable operation"
                ).model_dump_json(indent=2)

            from core.iterm_path_monitor import get_user_variable
            connection = ctx.request_context.lifespan_context["connection"]
            value = await get_user_variable(connection, request.session_id, request.variable_name)
            logger.info(f"Got variable {request.variable_name} from session {request.session_id}: {value}")

            return ManageAgentHooksResponse(
                operation=op,
                success=True,
                data={
                    "session_id": request.session_id,
                    "variable_name": request.variable_name,
                    "variable_value": value
                }
            ).model_dump_json(indent=2)

        else:
            return ManageAgentHooksResponse(
                operation=op,
                success=False,
                error=f"Unknown operation: {op}"
            ).model_dump_json(indent=2)

    except Exception as e:
        logger.error(f"Error in manage_agent_hooks ({request.operation}): {e}")
        return ManageAgentHooksResponse(
            operation=request.operation,
            success=False,
            error=str(e)
        ).model_dump_json(indent=2)


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
