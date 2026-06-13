"""Process-level application context for the iTerm MCP server.

All long-lived state (iTerm2 connection, terminal controller, registries)
lives here exactly once per process. The FastMCP lifespan only hands out a
reference — this is what makes a multi-client daemon possible, because the
mcp SDK runs the lifespan once per *client session*, not once per process.

AppContext implements the read-only mapping protocol so existing tool code
(`ctx.request_context.lifespan_context["terminal"]`) keeps working unchanged.
"""

import asyncio
import dataclasses
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import iterm2

from core.layouts import LayoutManager
from core.terminal import ItermTerminal
from core.agents import AgentRegistry
from core.feedback import (
    FeedbackHookManager,
    FeedbackRegistry,
    FeedbackForker,
    GitHubIntegration,
)
from core.flows import get_event_bus, get_flow_manager
from core.manager import ManagerRegistry
from core.memory import SQLiteMemoryStore
from core.models import AgentNotification
from core.bus import AgentMessageBus
from core.profiles import get_profile_manager
from core.roles import RoleManager
from core.service_hooks import get_service_hook_manager
from core.services import get_service_manager
from core.tags import SessionTagLockManager, FocusCooldownManager
from utils.otel import init_tracing, shutdown_tracing
from utils.telemetry import TelemetryEmitter

logger = logging.getLogger("iterm-mcp-server")


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
        # Wired by _build_app_context() after both objects are constructed.
        # When set, add() also enqueues into the bus (non-destructively).
        self._message_bus: Optional[Any] = None

    async def add(self, notification: AgentNotification) -> None:
        """Add a notification, maintaining ring buffer limits.

        Also enqueues a ``kind="notification"`` envelope onto the message bus
        if one has been wired via ``_message_bus``.  The bus write is
        fire-and-forget (scheduled as a background task) so it cannot block
        the ring-buffer write or raise.
        """
        async with self._lock:
            self._notifications.append(notification)
            # Trim per-agent ring buffer: drop oldest notifications for this agent
            agent_items = [n for n in self._notifications if n.agent == notification.agent]
            if len(agent_items) > self._max_per_agent:
                excess = set(map(id, agent_items[:-self._max_per_agent]))
                self._notifications = [
                    n for n in self._notifications if id(n) not in excess
                ]
            # Trim to max total
            if len(self._notifications) > self._max_total:
                self._notifications = self._notifications[-self._max_total:]

        # Bus adapter — additive, does not alter existing behavior.
        if self._message_bus is not None:
            try:
                body = {
                    "level": notification.level,
                    "summary": notification.summary,
                    "context": notification.context,
                    "action_hint": notification.action_hint,
                    "timestamp": notification.timestamp.isoformat(),
                }
                asyncio.create_task(
                    self._message_bus.send(
                        sender="system",
                        recipient=f"agent:{notification.agent}",
                        kind="notification",
                        body=body,
                    )
                )
            except Exception:
                logger.exception(
                    "NotificationManager: failed to enqueue bus notification"
                )

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


# ============================================================================
# APP CONTEXT
# ============================================================================

@dataclass
class AppContext:
    connection: Any = None
    terminal: Any = None
    layout_manager: Any = None
    agent_registry: Any = None
    telemetry: Any = None
    notification_manager: Any = None
    tag_lock_manager: Any = None
    focus_cooldown: Any = None
    feedback_registry: Any = None
    feedback_hook_manager: Any = None
    feedback_forker: Any = None
    github_integration: Any = None
    profile_manager: Any = None
    service_manager: Any = None
    service_hook_manager: Any = None
    manager_registry: Any = None
    event_bus: Any = None
    flow_manager: Any = None
    role_manager: Any = None
    memory_store: Any = None
    message_bus: Any = None
    logger: Any = None
    log_dir: Optional[str] = None

    # -- mapping protocol (back-compat with the old lifespan dict) --------
    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key) from None

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        return key in _FIELD_NAMES


# Field-name set for __contains__ — avoids dunder false-positives from hasattr
_FIELD_NAMES = {f.name for f in dataclasses.fields(AppContext)}


# ============================================================================
# SINGLETON
# ============================================================================

_app_context: Optional[AppContext] = None
_init_lock = asyncio.Lock()


async def _build_app_context() -> AppContext:
    """Build the process-wide AppContext.

    This runs exactly once per process lifetime (subsequent calls return the
    cached singleton via get_app_context). Exceptions propagate — callers
    handle failure.
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
    build_logger = logging.getLogger("iterm-mcp-server")
    build_logger.info("Initializing iTerm2 connection...")

    # Initialize OpenTelemetry tracing
    tracing_enabled = init_tracing()
    if tracing_enabled:
        build_logger.info("OpenTelemetry tracing initialized successfully")
    else:
        build_logger.info(
            "OpenTelemetry tracing not available "
            "(install with: pip install iterm-mcp[otel])"
        )

    # Initialize iTerm2 connection
    try:
        connection = await iterm2.Connection.async_create()
        build_logger.info("iTerm2 connection established successfully")
    except Exception as conn_error:
        build_logger.error(f"Failed to establish iTerm2 connection: {str(conn_error)}")
        raise

    # Initialize terminal controller
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
        build_logger.info("iTerm terminal controller initialized successfully")
    except Exception as term_error:
        build_logger.error(f"Failed to initialize iTerm terminal controller: {str(term_error)}")
        raise

    # Initialize layout manager
    layout_manager = LayoutManager(terminal)

    # Initialize agent registry
    lock_manager = SessionTagLockManager()
    agent_registry = AgentRegistry(lock_manager=lock_manager)

    # Initialize telemetry emitter
    telemetry = TelemetryEmitter(
        log_manager=getattr(terminal, "log_manager", None),
        agent_registry=agent_registry,
    )

    # Initialize notification manager
    notification_manager = NotificationManager()

    # Initialize focus cooldown manager
    focus_cooldown = FocusCooldownManager()

    # Initialize feedback system
    feedback_registry = FeedbackRegistry()
    feedback_hook_manager = FeedbackHookManager()
    feedback_forker = FeedbackForker()
    github_integration = GitHubIntegration()

    # Initialize profile manager
    profile_manager = get_profile_manager(build_logger)
    build_logger.info(
        f"Profile manager initialized with "
        f"{len(profile_manager.list_team_profiles())} team profiles"
    )

    # Initialize service manager and hooks
    service_manager = get_service_manager(logger=build_logger)
    service_manager.set_terminal(terminal)
    service_manager.load_global_config()
    service_hook_manager = get_service_hook_manager(service_manager, build_logger)

    # Initialize manager registry for hierarchical task delegation
    manager_registry = ManagerRegistry()

    # Initialize event bus and flow manager
    event_bus = get_event_bus()
    flow_manager = get_flow_manager()
    await event_bus.start()

    # Initialize role manager
    role_manager = RoleManager(agent_registry=agent_registry)
    build_logger.info(
        f"Role manager initialized with "
        f"{len(role_manager.list_roles())} role assignments"
    )

    # Initialize memory store
    memory_store = SQLiteMemoryStore()
    build_logger.info("Memory store initialized (SQLite with FTS5)")

    # Initialize message bus (durable, addressed, long-poll inbox)
    message_bus = AgentMessageBus()
    build_logger.info("Message bus initialized (SQLite, durable inbox)")

    # Wire the notification-manager → bus adapter.
    notification_manager._message_bus = message_bus

    return AppContext(
        connection=connection,
        terminal=terminal,
        layout_manager=layout_manager,
        agent_registry=agent_registry,
        telemetry=telemetry,
        notification_manager=notification_manager,
        tag_lock_manager=lock_manager,
        focus_cooldown=focus_cooldown,
        feedback_registry=feedback_registry,
        feedback_hook_manager=feedback_hook_manager,
        feedback_forker=feedback_forker,
        github_integration=github_integration,
        profile_manager=profile_manager,
        service_manager=service_manager,
        service_hook_manager=service_hook_manager,
        manager_registry=manager_registry,
        event_bus=event_bus,
        flow_manager=flow_manager,
        role_manager=role_manager,
        memory_store=memory_store,
        message_bus=message_bus,
        logger=build_logger,
        log_dir=log_dir,
    )


async def get_app_context() -> AppContext:
    """Return the process-wide AppContext, building it on first call.

    Double-checked lock: concurrent client sessions during daemon startup
    must not each build an iTerm2 connection.
    """
    global _app_context
    if _app_context is not None:
        return _app_context
    async with _init_lock:
        if _app_context is None:
            _app_context = await _build_app_context()
    return _app_context


async def shutdown_app_context() -> None:
    """Tear down shared resources. Called at process exit, never per-session."""
    global _app_context
    ctx = _app_context
    _app_context = None
    if ctx is None:
        return
    if ctx.event_bus is not None:
        try:
            await ctx.event_bus.stop()
        except Exception:
            logger.exception("Error stopping event bus during shutdown")
    if ctx.message_bus is not None:
        try:
            ctx.message_bus.close()
        except Exception:
            logger.exception("Error closing message bus during shutdown")
    shutdown_tracing()
