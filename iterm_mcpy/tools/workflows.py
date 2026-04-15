"""Workflow event tools.

Provides tools for triggering and querying workflow events, listing
registered events, retrieving event history, and subscribing to output
patterns that fire events when matched.
"""

import json
import re
from typing import Any, Dict, Optional

from mcp.server.fastmcp import Context

from core.flows import EventBus, EventPriority, FlowManager
from core.models import (
    EventHistoryEntry,
    EventInfo,
    GetEventHistoryResponse,
    ListWorkflowEventsResponse,
    PatternSubscriptionResponse,
    TriggerEventResponse,
    WorkflowEventInfo,
)

# Priority level mapping
PRIORITY_MAP = {
    "low": EventPriority.LOW,
    "normal": EventPriority.NORMAL,
    "high": EventPriority.HIGH,
    "critical": EventPriority.CRITICAL,
}


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
        return response.model_dump_json(indent=2, exclude_none=True)

    except Exception as e:
        logger.error(f"Error triggering workflow event: {e}")
        return TriggerEventResponse(
            success=False,
            error=str(e)
        ).model_dump_json(indent=2, exclude_none=True)


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
            info = await event_bus.get_event_info(name)

            events.append(WorkflowEventInfo(
                event_name=info["event_name"],
                has_listeners=info["has_listeners"],
                has_router=info["has_router"],
                is_start_event=info["is_start_event"],
                listener_count=info["listener_count"]
            ))

        # Get registered flows
        flow_names = flow_manager.list_flows()

        response = ListWorkflowEventsResponse(
            events=events,
            total_count=len(events),
            flows_registered=flow_names
        )

        logger.info(f"Listed {len(events)} workflow events")
        return response.model_dump_json(indent=2, exclude_none=True)

    except Exception as e:
        logger.error(f"Error listing workflow events: {e}")
        return json.dumps({"error": str(e)}, indent=2)


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
        return response.model_dump_json(indent=2, exclude_none=True)

    except Exception as e:
        logger.error(f"Error getting event history: {e}")
        return json.dumps({"error": str(e)}, indent=2)


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
        return response.model_dump_json(indent=2, exclude_none=True)

    except re.error as e:
        logger.error(f"Invalid regex pattern: {e}")
        return json.dumps({"error": f"Invalid regex pattern: {e}"}, indent=2)
    except Exception as e:
        logger.error(f"Error creating pattern subscription: {e}")
        return json.dumps({"error": str(e)}, indent=2)


def register(mcp):
    """Register workflow event tools with the FastMCP instance."""
    mcp.tool()(trigger_workflow_event)
    mcp.tool()(list_workflow_events)
    mcp.tool()(get_workflow_event_history)
    mcp.tool()(subscribe_to_output_pattern)
