"""MCP server implementation for iTerm2 controller using the official MCP Python SDK.

This version supports parallel multi-session operations with agent/team management.
"""

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator, Dict, List, Optional, Any

import iterm2
from mcp.server.fastmcp import FastMCP

from core.layouts import LayoutManager
from core.terminal import ItermTerminal
from core.agents import AgentRegistry
from utils.telemetry import TelemetryEmitter
from utils.otel import init_tracing, shutdown_tracing
from core.tags import SessionTagLockManager, FocusCooldownManager
from core.profiles import ProfileManager, get_profile_manager
from core.feedback import (
    FeedbackHookManager,
    FeedbackRegistry,
    FeedbackForker,
    GitHubIntegration,
)
from core.services import (
    ServiceManager,
    get_service_manager,
)
from core.service_hooks import (
    ServiceHookManager,
    get_service_hook_manager,
)
from core.memory import SQLiteMemoryStore
from core.models import AgentNotification
from core.manager import ManagerRegistry
from core.flows import (
    EventBus,
    FlowManager,
    get_event_bus,
    get_flow_manager,
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


# Shared helpers (resolve_session, execute_write_request, etc.) live in
# iterm_mcpy/helpers.py and are imported directly by the tool modules.
# See iterm_mcpy/helpers.py for the full list.


# All tool implementations live in iterm_mcpy/tools/ — see tools/__init__.py
# for the module list. The SP2 surface is 15 method-semantic tools:
#
# Collections (9):
#   sessions.py        — GET/HEAD/POST/PATCH/DELETE sessions + sub-resources
#                        (output, keys, tags, roles, locks, monitoring, splits,
#                         appearance, active, status)
#   agents.py          — register/list/remove agents; notifications; hooks;
#                        locks held by an agent
#   teams.py           — create/list/remove teams; assign/remove team agents
#   managers.py        — create/list/remove manager agents; add/remove workers
#   feedback.py        — submit/query/triage/fork feedback; config; triggers
#   memory.py          — store/retrieve/search/list/delete memories
#   services.py        — list/add/configure/start/stop/remove services
#   roles.py           — discover available roles (read-only catalog)
#   workflows.py       — trigger/list/history workflow events
#
# Actions (6):
#   messages.py        — send cascade / hierarchical messages between sessions
#   orchestrate.py     — orchestrate multi-step playbooks
#   delegate.py        — delegate a task or execute a plan through a manager
#   wait_for.py        — long-poll for an agent to complete
#   subscribe.py       — subscribe to terminal output patterns
#   telemetry.py       — start/stop the telemetry dashboard


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
