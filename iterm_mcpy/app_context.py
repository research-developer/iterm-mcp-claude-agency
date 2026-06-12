"""Process-level application context for the iTerm MCP server.

All long-lived state (iTerm2 connection, terminal controller, registries)
lives here exactly once per process. The FastMCP lifespan only hands out a
reference — this is what makes a multi-client daemon possible, because the
mcp SDK runs the lifespan once per *client session*, not once per process.

AppContext implements the read-only mapping protocol so existing tool code
(`ctx.request_context.lifespan_context["terminal"]`) keeps working unchanged.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("iterm-mcp-server")


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
        return hasattr(self, key)


_app_context: Optional[AppContext] = None
_init_lock = asyncio.Lock()


async def _build_app_context() -> AppContext:
    """Build the real context. Body moves here from iterm_lifespan in Task 2."""
    raise NotImplementedError("populated in Task 2")


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
