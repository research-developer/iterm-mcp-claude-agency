"""SP2 `subscribe` action tool — Task 13/14.

Replaces the legacy ``subscribe_to_output_pattern`` tool. Registers a regex
pattern subscription against the event bus; matches fire a workflow event.

Only POST+TRIGGER is supported. Any other (op, definer) pair returns an
err envelope.
"""
import re
from typing import Any, Optional

from mcp.server.fastmcp import Context

from core.definer_verbs import DefinerError, resolve_op
from core.models import PatternSubscriptionResponse
from iterm_mcpy.responses import err_envelope, ok_envelope
from iterm_mcpy.errors import ToolError


async def subscribe(
    ctx: Context,
    op: str = "POST",
    definer: Optional[str] = None,
    pattern: Optional[str] = None,
    event_name: Optional[str] = None,
) -> str:
    """Subscribe to terminal output matching a regex pattern.

    Replaces the legacy ``subscribe_to_output_pattern`` tool. When terminal
    output matches ``pattern``, the event bus triggers ``event_name`` (if
    provided) with the matched text as payload.

    Only POST+TRIGGER is supported. Friendly verbs that resolve to
    POST+TRIGGER (e.g. "subscribe", "monitor", "trigger") also work.

    Args:
        op: HTTP method or friendly verb (default "POST"). "subscribe",
            "monitor", "trigger" all resolve to POST+TRIGGER.
        definer: Explicit definer — must be TRIGGER when provided.
        pattern: Regex pattern to match against terminal output (required).
        event_name: Workflow event to trigger on pattern match (optional).
    """
    try:
        resolution = resolve_op(op, definer)
    except DefinerError as e:
        return err_envelope(method=op.upper(), error=ToolError.from_exception(e))

    if resolution.method != "POST" or resolution.definer != "TRIGGER":
        return err_envelope(
            method=resolution.method,
            definer=resolution.definer,
            error=(
                f"subscribe only supports POST+TRIGGER "
                f"(got {resolution.method}+{resolution.definer})"
            ),
        )

    if pattern is None:
        return err_envelope(
            method="POST", definer="TRIGGER",
            error="subscribe requires 'pattern' parameter",
        )

    try:
        # Fail fast on invalid regex — mirrors legacy behavior.
        re.compile(pattern)
    except re.error as e:
        return err_envelope(
            method="POST", definer="TRIGGER",
            error=f"Invalid regex pattern: {e}",
        )

    try:
        lifespan = ctx.request_context.lifespan_context
        event_bus = lifespan["event_bus"]
        logger = lifespan["logger"]

        async def on_match(text: str, match: Any) -> None:
            logger.debug(f"subscribe match: {pattern} -> {match}")

        subscription_id = await event_bus.subscribe_to_pattern(
            pattern=pattern,
            callback=on_match,
            event_name=event_name,
        )

        result = PatternSubscriptionResponse(
            subscription_id=subscription_id,
            pattern=pattern,
            event_name=event_name,
        )
        logger.info(f"subscribe: pattern={pattern!r} event={event_name!r}")
        return ok_envelope(method="POST", definer="TRIGGER", data=result)
    except Exception as e:
        return err_envelope(method="POST", definer="TRIGGER", error=ToolError.from_exception(e))


def register(mcp):
    """Register the subscribe action tool."""
    mcp.tool(name="subscribe")(subscribe)
