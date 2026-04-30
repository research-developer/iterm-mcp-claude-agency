"""SP2 `subscribe` action tool.

Pattern subscriptions on terminal output. Replaces the legacy
``subscribe_to_output_pattern`` tool and adds a full lifecycle:

- ``op="POST"`` (or ``"subscribe"``/``"monitor"``/``"trigger"``) — arm a
  subscription. Returns ``subscription_id``. Optional cross-agent
  filtering via ``target_session_id`` / ``target_agent``. Optional
  agent-feed via ``notify_agent``: when the pattern matches, push a
  notification onto that agent's queue.
- ``op="GET"`` (or ``"list"``) — return active subscriptions.
- ``op="DELETE"`` (or ``"stop"``/``"cancel"``/``"unsubscribe"``) +
  ``subscription_id`` — cancel a subscription.
- ``op="OPTIONS"`` — self-describe.

Resolves fb-20260424-157473f7 item #10 (no id, no list/cancel — leak
risk) and the user's follow-up ask (cross-agent pattern feed).
"""
import re
from typing import Any, Optional

from mcp.server.fastmcp import Context

from core.definer_verbs import DefinerError, resolve_op
from core.models import PatternSubscriptionResponse
from iterm_mcpy.errors import ErrorCode, ToolError
from iterm_mcpy.responses import err_envelope, ok_envelope


_OPTIONS_SCHEMA = {
    "tool": "subscribe",
    "kind": "action",
    "methods": {
        "POST": {
            "definer": "TRIGGER",
            "aliases": ["subscribe", "monitor", "trigger"],
            "params": {
                "pattern": "regex (required)",
                "event_name?": "workflow event to trigger on match",
                "target_session_id?": "only fire for this session's output",
                "target_agent?": "only fire for this agent's session output",
                "notify_agent?": "agent to notify when pattern matches",
                "notify_level?": "notification level (info/warning/error). default 'info'",
            },
        },
        "GET": {
            "aliases": ["list", "get"],
            "params": {},
            "description": "List active pattern subscriptions.",
        },
        "DELETE": {
            "aliases": ["stop", "cancel", "unsubscribe"],
            "params": {"subscription_id": "id returned from POST"},
        },
        "OPTIONS": {"description": "This schema."},
    },
}


async def subscribe(
    ctx: Context,
    op: str = "POST",
    definer: Optional[str] = None,
    pattern: Optional[str] = None,
    event_name: Optional[str] = None,
    subscription_id: Optional[str] = None,
    target_session_id: Optional[str] = None,
    target_agent: Optional[str] = None,
    notify_agent: Optional[str] = None,
    notify_level: str = "info",
) -> dict[str, Any]:
    """Pattern subscriptions on terminal output (lifecycle).

    See module docstring for the full op surface.
    """
    try:
        resolution = resolve_op(op, definer)
    except DefinerError as e:
        return err_envelope(method=op.upper(), error=ToolError.from_exception(e))

    method = resolution.method

    if method == "OPTIONS":
        return ok_envelope(method="OPTIONS", data=_OPTIONS_SCHEMA)

    try:
        lifespan = ctx.request_context.lifespan_context
        event_bus = lifespan["event_bus"]
        logger = lifespan["logger"]
    except (AttributeError, KeyError) as e:
        return err_envelope(
            method=method,
            error=ToolError(ErrorCode.INTERNAL, f"event_bus not available: {e}"),
        )

    if method == "GET":
        subs = event_bus.list_pattern_subscriptions()
        return ok_envelope(method="GET", data={"count": len(subs), "subscriptions": subs})

    if method == "DELETE":
        if not subscription_id:
            return err_envelope(
                method="DELETE",
                error=ToolError(
                    ErrorCode.MISSING_PARAM,
                    "DELETE requires 'subscription_id'",
                    hint="get one from `op='list'`",
                ),
            )
        removed = await event_bus.unsubscribe_from_pattern(subscription_id)
        if not removed:
            return err_envelope(
                method="DELETE",
                error=ToolError(
                    ErrorCode.SESSION_NOT_FOUND,
                    f"no subscription with id {subscription_id!r}",
                ),
            )
        logger.info(f"subscribe DELETE: removed {subscription_id}")
        return ok_envelope(method="DELETE", data={"subscription_id": subscription_id, "cancelled": True})

    if method != "POST" or resolution.definer != "TRIGGER":
        return err_envelope(
            method=method,
            definer=resolution.definer,
            error=ToolError(
                ErrorCode.INVALID_OP,
                f"subscribe does not support {method}+{resolution.definer}",
                hint="use POST/subscribe to arm, GET/list to enumerate, DELETE/stop to cancel",
            ),
        )

    if pattern is None:
        return err_envelope(
            method="POST", definer="TRIGGER",
            error=ToolError(ErrorCode.MISSING_PARAM, "subscribe requires 'pattern' parameter"),
        )

    try:
        re.compile(pattern)
    except re.error as e:
        return err_envelope(
            method="POST", definer="TRIGGER",
            error=ToolError(
                ErrorCode.INVALID_PARAM,
                f"Invalid regex pattern: {e}",
                hint="test your pattern with python's re.compile() before subscribing",
            ),
        )

    notification_manager = lifespan.get("notification_manager")

    async def on_match(matched_text: str, match: Any) -> None:
        logger.debug(f"subscribe match: {pattern!r} -> {matched_text!r}")
        if notify_agent and notification_manager:
            summary = f"pattern {pattern!r} matched: {matched_text}"
            context_parts = []
            if target_session_id:
                context_parts.append(f"session={target_session_id}")
            if target_agent:
                context_parts.append(f"agent={target_agent}")
            try:
                await notification_manager.add_simple(
                    agent=notify_agent,
                    level=notify_level,
                    summary=summary,
                    context=", ".join(context_parts) or None,
                )
            except Exception as nerr:
                logger.error(f"subscribe: failed to notify {notify_agent!r}: {nerr}")

    try:
        sub_id = await event_bus.subscribe_to_pattern(
            pattern=pattern,
            callback=on_match,
            event_name=event_name,
            target_session_id=target_session_id,
            target_agent=target_agent,
            notify_agent=notify_agent,
            # Only persist notify_level when it actually drives behavior.
            notify_level=notify_level if notify_agent else None,
        )
        result = PatternSubscriptionResponse(
            subscription_id=sub_id,
            pattern=pattern,
            event_name=event_name,
            target_session_id=target_session_id,
            target_agent=target_agent,
            notify_agent=notify_agent,
            notify_level=notify_level if notify_agent else None,
        )
        logger.info(
            "subscribe: pattern=%r event=%r target_session=%r target_agent=%r notify=%r id=%s",
            pattern, event_name, target_session_id, target_agent, notify_agent, sub_id,
        )
        return ok_envelope(method="POST", definer="TRIGGER", data=result)
    except Exception as e:
        return err_envelope(method="POST", definer="TRIGGER", error=ToolError.from_exception(e))


def register(mcp):
    """Register the subscribe action tool."""
    mcp.tool(name="subscribe")(subscribe)
