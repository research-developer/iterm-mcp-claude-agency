"""Agent notification tools.

Provides tools for retrieving notifications, generating compact status
summaries, and manually recording notifications. These tools interface
with the NotificationManager instantiated during lifespan.
"""

from datetime import datetime
from typing import Optional

from mcp.server.fastmcp import Context

from core.models import (
    AgentNotification,
    GetNotificationsRequest,
    GetNotificationsResponse,
)


def _ensure_model(model_cls, payload):
    """Validate or coerce an incoming payload into a Pydantic model."""
    if isinstance(payload, model_cls):
        return payload
    return model_cls.model_validate(payload)


async def get_notifications(request: GetNotificationsRequest, ctx: Context) -> str:
    """Get recent agent notifications.

    Returns a list of notifications about agent status changes, errors,
    completions, and other events. Use this to stay aware of what's happening
    across all managed agents.
    """
    notification_manager = ctx.request_context.lifespan_context["notification_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        req = _ensure_model(GetNotificationsRequest, request)
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
        return response.model_dump_json(indent=2, exclude_none=True)

    except Exception as e:
        logger.error(f"Error getting notifications: {e}")
        return f"Error: {e}"


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


def register(mcp):
    """Register notification tools with the FastMCP instance."""
    mcp.tool()(get_notifications)
    mcp.tool()(get_agent_status_summary)
    mcp.tool()(notify)
