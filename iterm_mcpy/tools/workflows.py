"""SP2 method-semantic `workflows` tool — Task 12.

Ninth and final SP2 collection tool. Replaces the legacy
``trigger_workflow_event``, ``list_workflow_events``, and
``get_workflow_event_history`` tools:

    - list_workflow_events       -> GET              /workflows/events  (target=None)
    - get_workflow_event_history -> GET              /workflows/events  (target='history')
    - trigger_workflow_event     -> POST + TRIGGER   /workflows/events

The fourth legacy workflow tool, ``subscribe_to_output_pattern``, stays in
``workflows.py`` for now — it'll migrate as a separate action tool in
Task 14 per the SP2 plan.

Registered under the provisional name ``workflows`` to coexist with the
legacy per-verb tools; the cutover (rename to ``workflows`` and unregister
the legacy tools) happens at the end of SP2.

``list`` uses the public ``EventBus.get_event_info`` introspection API
rather than reaching into the private ``event_bus._registry`` attribute.
"""
from typing import Any, Dict, Optional

from mcp.server.fastmcp import Context

from iterm_mcpy.dispatcher import MethodDispatcher


# Priority level mapping — copied from the legacy tool so workflows can
# accept the same four priority strings ("low"/"normal"/"high"/"critical").
_PRIORITY_MAP: dict = {}


def _priority_enum(name: str):
    """Map a priority string to an ``EventPriority`` enum, cached lazily.

    Imported lazily so tests can stub out ``core.flows`` / ``EventBus``
    without pulling in the full workflow engine at import time.
    """
    global _PRIORITY_MAP
    if not _PRIORITY_MAP:
        from core.flows import EventPriority
        _PRIORITY_MAP = {
            "low": EventPriority.LOW,
            "normal": EventPriority.NORMAL,
            "high": EventPriority.HIGH,
            "critical": EventPriority.CRITICAL,
        }
    return _PRIORITY_MAP.get(name.lower(), _PRIORITY_MAP["normal"])


class WorkflowsDispatcher(MethodDispatcher):
    """Dispatcher for the `workflows` collection (SP2 method-semantic)."""

    collection = "workflows"

    METHODS = {
        "GET": {
            "aliases": ["list", "get", "read", "query"],
            "params": [
                "target=None | target='events' | target='history'",
                # history:
                "event_name?",
                "limit?=100",
                "success_only?=false",
            ],
            "description": (
                "Default (target=None or target='events'): list registered "
                "workflow events. target='history': retrieve event history "
                "(optionally filtered by event_name / success_only, capped "
                "by limit)."
            ),
        },
        "POST": {
            "definers": {
                "TRIGGER": {
                    "aliases": ["trigger", "fire", "start"],
                    "params": [
                        "event_name",
                        "payload?",
                        "source?",
                        "priority?='normal'",
                        "metadata?",
                        "immediate?=false",
                    ],
                    "description": (
                        "Trigger a workflow event (queued by default, or "
                        "processed synchronously when immediate=true)."
                    ),
                },
            },
        },
        "HEAD": {"compact_fields": ["event_name", "has_listeners"]},
        "OPTIONS": {"description": "Return this schema."},
    }

    sub_resources = ["events", "history"]

    # -------------------------------- GET -------------------------------- #

    async def on_get(self, ctx, **params):
        """Route GET by `target` — list events (default) or history."""
        target = params.get("target")
        if target == "history":
            return await self._history(ctx, **params)
        # Default (target=None or target='events') -> list events.
        return await self._list_events(ctx, **params)

    async def _list_events(self, ctx, **params):
        """GET /workflows/events — list registered workflow events.

        Mirrors the legacy ``list_workflow_events`` return shape.
        """
        lifespan = ctx.request_context.lifespan_context
        event_bus = lifespan["event_bus"]
        flow_manager = lifespan.get("flow_manager")
        logger = lifespan.get("logger")

        event_names = await event_bus.get_registered_events()

        events = []
        for name in sorted(event_names):
            events.append(await event_bus.get_event_info(name))

        flow_names = flow_manager.list_flows() if flow_manager else []

        if logger is not None:
            logger.info(f"workflows GET events: listed {len(events)} events")

        return {
            "events": events,
            "total_count": len(events),
            "flows_registered": flow_names,
        }

    async def _history(self, ctx, **params):
        """GET /workflows/events?target=history — retrieve event history.

        Mirrors the legacy ``get_workflow_event_history`` return shape.
        """
        lifespan = ctx.request_context.lifespan_context
        event_bus = lifespan["event_bus"]
        logger = lifespan.get("logger")

        event_name = params.get("event_name")
        limit_raw = params.get("limit", 100)
        success_only = params.get("success_only", False)

        # Cap limit at 1000 (matches legacy).
        try:
            limit = min(int(limit_raw), 1000)
        except (TypeError, ValueError):
            limit = 100

        history = await event_bus.get_history(
            event_name=event_name,
            limit=limit,
            success_only=success_only,
        )

        entries = [
            {
                "event_name": r.event.name,
                "event_id": r.event.id,
                "source": r.event.source,
                "timestamp": (
                    r.event.timestamp.isoformat()
                    if hasattr(r.event.timestamp, "isoformat")
                    else r.event.timestamp
                ),
                "success": r.success,
                "handler_name": r.handler_name,
                "routed_to": r.routed_to,
                "duration_ms": r.duration_ms,
                "error": r.error,
            }
            for r in history
        ]

        if logger is not None:
            logger.info(
                f"workflows GET history: {len(entries)} entries "
                f"(event_name={event_name!r}, success_only={success_only})"
            )

        return {
            "entries": entries,
            "total_count": len(entries),
        }

    # ------------------------------- POST -------------------------------- #

    async def on_post(self, ctx, definer, **params):
        """Route POST by definer — TRIGGER (trigger an event)."""
        if definer == "TRIGGER":
            return await self._trigger(ctx, **params)
        raise NotImplementedError(
            f"POST+{definer} not supported on workflows"
        )

    async def _trigger(self, ctx, **params):
        """POST /workflows/events (TRIGGER) — trigger a workflow event.

        Mirrors the legacy ``trigger_workflow_event`` return shape. When
        ``immediate=True``, the event is processed synchronously and the
        full result (routed_to / handler_name / error) is returned;
        otherwise the event is queued and the response reports queued=True.
        """
        event_name = params.get("event_name")
        if not event_name:
            raise ValueError("trigger workflow event requires event_name")

        lifespan = ctx.request_context.lifespan_context
        event_bus = lifespan["event_bus"]
        logger = lifespan.get("logger")

        priority = params.get("priority", "normal")
        immediate = params.get("immediate", False)
        payload = params.get("payload", {})
        source = params.get("source")
        metadata = params.get("metadata", {}) or {}

        result = await event_bus.trigger(
            event_name=event_name,
            payload=payload,
            source=source,
            priority=_priority_enum(priority),
            metadata=metadata,
            immediate=immediate,
        )

        if immediate and result is not None:
            event_info = {
                "name": result.event.name,
                "id": result.event.id,
                "source": result.event.source,
                "timestamp": (
                    result.event.timestamp.isoformat()
                    if hasattr(result.event.timestamp, "isoformat")
                    else result.event.timestamp
                ),
                "priority": (
                    result.event.priority.name.lower()
                    if hasattr(result.event.priority, "name")
                    else str(result.event.priority)
                ),
            }
            response = {
                "success": result.success,
                "event": event_info,
                "queued": False,
                "processed": True,
                "routed_to": result.routed_to,
                "handler_name": result.handler_name,
                "error": result.error,
            }
        else:
            # Event was queued for async processing.
            response = {
                "success": True,
                "queued": True,
                "processed": False,
                "error": (
                    "Event queued for async processing; success indicates "
                    "queue operation, not event handling"
                ),
            }

        if logger is not None:
            logger.info(
                f"workflows TRIGGER: event={event_name!r} "
                f"immediate={immediate} success={response['success']}"
            )

        return response


_dispatcher = WorkflowsDispatcher()


async def workflows(
    ctx: Context,
    op: str = "GET",
    definer: Optional[str] = None,
    target: Optional[str] = None,
    event_name: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
    priority: str = "normal",
    metadata: Optional[Dict[str, Any]] = None,
    immediate: bool = False,
    limit: int = 100,
    success_only: bool = False,
) -> dict[str, Any]:
    """Workflow event bus: list events, trigger events, get event history.

    Use op="list" (or op="GET") to list registered workflow events. Returns
      per-event metadata (has_listeners / has_router / is_start_event /
      listener_count) plus the set of registered flow class names.
    Use op="GET" + target="history" (+ event_name? / limit? / success_only?)
      to retrieve event history — the list of recently-processed events
      with their outcomes (success, handler_name, routed_to, duration_ms,
      error). Filter by event_name and/or success_only; capped at limit
      (default 100, max 1000).
    Use op="trigger" (or op="POST" + definer="TRIGGER") + event_name
      (+ payload? / source? / priority? / metadata? / immediate?) to fire
      a workflow event. When immediate=true, the handlers run
      synchronously and the response includes the full processing result;
      otherwise the event is queued for async dispatch.
    Use op="HEAD" (or "peek"/"summary") for a compact event list.
    Use op="OPTIONS" (or "schema") to discover the tool's surface.

    Args:
        op: HTTP method or friendly verb.
        definer: Explicit definer (TRIGGER for POST).
        target: Sub-resource: 'events' (default) or 'history'.
        event_name: Event name (required for POST+TRIGGER; optional filter
            for GET target='history').
        payload: Event payload delivered to listeners (TRIGGER only).
        source: Source identifier for the event (TRIGGER only).
        priority: 'low'|'normal'|'high'|'critical' (TRIGGER only).
        metadata: Additional event metadata (TRIGGER only).
        immediate: If True, process synchronously rather than queueing.
        limit: Max history entries to return (history only; max 1000).
        success_only: Only return successful history entries.

    This is SP2's ninth and final method-semantic collection tool. It
    coexists with the legacy ``trigger_workflow_event``,
    ``list_workflow_events``, and ``get_workflow_event_history`` tools and
    will eventually replace them. The fourth legacy workflow tool,
    ``subscribe_to_output_pattern``, migrates separately as an action tool
    in Task 14.
    """
    raw_params = {
        "target": target,
        "event_name": event_name,
        "payload": payload,
        "source": source,
        "priority": priority,
        "metadata": metadata,
        "immediate": immediate,
        "limit": limit,
        "success_only": success_only,
    }
    # Filter out params the user explicitly didn't set (None values),
    # but keep immediate/success_only/priority/limit even at defaults —
    # handlers read them directly from params.
    params = {k: v for k, v in raw_params.items() if v is not None}
    return await _dispatcher.dispatch(ctx=ctx, op=op, definer=definer, **params)


def register(mcp):
    """Register the workflows dispatcher tool.

    Named ``workflows`` to coexist with the legacy
    ``trigger_workflow_event``, ``list_workflow_events``, and
    ``get_workflow_event_history`` tools during the SP2 coexistence period.
    Final cutover (renaming to ``workflows``) happens at the end of SP2.
    """
    mcp.tool(name="workflows")(workflows)
