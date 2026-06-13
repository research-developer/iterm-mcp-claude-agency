"""SP2 `bus` action tool — Phase 1 agent message bus.

Exposes the process-global ``AgentMessageBus`` (``AppContext.message_bus``)
as a single MCP tool with WebSpec method-semantic ops.

Ops summary:

    bus op="POST"           Send a message to an inbox (POST+SEND).
        to="agent:builder" | "team:backend" | "broadcast" | arbitrary
        kind="instruction"  (or "notification", "event", "reply", ...)
        body=<str|dict>
        sender?             defaults to "caller"
        correlation_id?
        ttl_seconds?

    bus op="GET"            Long-poll receive from an inbox.
        agent="alice"       (required)
        wait_up_to?=0       0 = non-blocking drain; max 600 s
        since_cursor?       override the durable cursor
        kinds?=[...]        kind filter
        limit?=50

    bus op="GET" target="status"   Inbox depths + oldest unacked age.

    bus op="GET" target="peek"     Non-blocking 5-item drain.
        agent="alice"

    bus op="POST" definer="TRIGGER"   Advance the durable read cursor.
        agent="alice"       (required)
        up_to_cursor=<int>  rowid to ack through (inclusive)

    bus op="OPTIONS"        Self-describe (consistent with SP2 tools).

Registration: ``register(mcp)`` follows the same pattern as all other tools.
The tool count moves from 15 → 16.
"""
from typing import Any, List, Optional

from mcp.server.fastmcp import Context

from core.definer_verbs import DefinerError, resolve_op
from iterm_mcpy.errors import ErrorCode, ToolError
from iterm_mcpy.responses import err_envelope, ok_envelope


_OPTIONS_SCHEMA = {
    "tool": "bus",
    "kind": "action",
    "description": (
        "Shared durable message bus. Send addressed envelopes to "
        "agent:/team:/broadcast inboxes; receive with long-poll; "
        "acknowledge to advance a durable cursor."
    ),
    "methods": {
        "POST": {
            "definer": "SEND",
            "aliases": ["send", "post", "notify", "dispatch"],
            "params": {
                "to": "recipient: agent:<name> | team:<name> | broadcast (required)",
                "kind?": "envelope kind: instruction|notification|event|reply (default: instruction)",
                "body?": "message payload (str or JSON-serializable dict)",
                "sender?": "originator address (default: 'caller')",
                "correlation_id?": "ties replies to a request",
                "ttl_seconds?": "expiry; None = never expires",
            },
            "returns": "{message_id, accepted_recipients: [...]}",
        },
        "GET": {
            "aliases": ["receive", "get", "list", "read", "drain"],
            "params": {
                "agent": "inbox owner — required for receive/peek (omit for status)",
                "wait_up_to?": "long-poll timeout seconds (0=non-blocking, max 600)",
                "since_cursor?": "override durable cursor (rowid integer)",
                "kinds?": "list of kind strings to filter by",
                "limit?": "max messages to return (default 50)",
                "target?": "'status' for inbox stats; 'peek' for 5-item drain",
            },
            "returns": "{messages: [...], next_cursor: int, has_more: bool}",
        },
        "POST_TRIGGER": {
            "definer": "TRIGGER",
            "aliases": ["ack", "acknowledge"],
            "params": {
                "agent": "inbox owner (required)",
                "up_to_cursor": "rowid to ack through, inclusive (required)",
            },
            "returns": "{acked_through: int}",
        },
        "OPTIONS": {"description": "This schema."},
    },
    "future_phases": [
        "Phase 2: route messages tool through bus; daemon delivery worker",
        "Phase 3: Claude Code Channels ingress/egress bridge (external:<session_id>)",
        "Phase 4: SSE/WebSocket push transport replacing long-poll",
    ],
}


async def bus(
    ctx: Context,
    op: str = "GET",
    definer: Optional[str] = None,
    # --- send params ---
    to: Optional[str] = None,
    kind: str = "instruction",
    body: Optional[Any] = None,
    sender: Optional[str] = None,
    correlation_id: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
    # --- receive params ---
    agent: Optional[str] = None,
    wait_up_to: int = 0,
    since_cursor: Optional[int] = None,
    kinds: Optional[List[str]] = None,
    limit: int = 50,
    target: Optional[str] = None,
    # --- ack params ---
    up_to_cursor: Optional[int] = None,
) -> dict[str, Any]:
    """Shared durable message bus for inter-agent communication.

    Send structured envelopes to addressed inboxes (agent:/team:/broadcast),
    receive with optional long-polling, and acknowledge to advance a durable
    read cursor that survives daemon restarts.

    See the module docstring for the full op surface and parameter details.

    Args:
        op: HTTP method or friendly verb (GET, POST, send, receive, ack, …).
        definer: Explicit definer (SEND, TRIGGER). Usually inferred from ``op``.
        to: Recipient address for SEND (``agent:<n>``, ``team:<n>``,
            ``broadcast``, or arbitrary string).
        kind: Envelope kind (``instruction``, ``notification``, ``event``,
            ``reply``, or any string).
        body: Payload — str or JSON-serializable value.
        sender: Originator label.  Defaults to ``"caller"``.
        correlation_id: Optional request-correlation token.
        ttl_seconds: Expiry in seconds.  ``None`` = never expires.
        agent: Inbox owner for GET / ack operations.
        wait_up_to: Long-poll timeout in seconds (0 = non-blocking).
        since_cursor: Override the durable cursor (rowid integer).
        kinds: List of kind strings to filter received messages by.
        limit: Maximum messages per receive call (default 50).
        target: Sub-operation: ``"status"`` for inbox stats, ``"peek"``
            for a 5-item non-blocking drain.
        up_to_cursor: Rowid to acknowledge through (inclusive).
    """
    # ------------------------------------------------------------------
    # OPTIONS — always available without AppContext.
    # ------------------------------------------------------------------
    op_upper = op.upper() if op else "GET"
    if op_upper == "OPTIONS":
        return ok_envelope(method="OPTIONS", data=_OPTIONS_SCHEMA)

    # ------------------------------------------------------------------
    # Resolve op.
    # ------------------------------------------------------------------
    try:
        resolution = resolve_op(op, definer)
    except DefinerError as e:
        return err_envelope(method=op_upper, error=ToolError.from_exception(e))

    method = resolution.method
    resolved_definer = resolution.definer

    # ------------------------------------------------------------------
    # Get the bus from AppContext.
    # ------------------------------------------------------------------
    try:
        lifespan = ctx.request_context.lifespan_context
        message_bus = lifespan.get("message_bus")
        agent_registry = lifespan.get("agent_registry")
    except Exception as e:
        return err_envelope(
            method=method,
            definer=resolved_definer,
            error=ToolError(ErrorCode.INTERNAL, f"Could not access AppContext: {e}"),
        )

    if message_bus is None:
        return err_envelope(
            method=method,
            definer=resolved_definer,
            error=ToolError(
                ErrorCode.INTERNAL,
                "message_bus not available — daemon may not be initialized",
            ),
        )

    # ------------------------------------------------------------------
    # Dispatch.
    # ------------------------------------------------------------------
    try:
        # ---- SEND ----
        # Accept POST+SEND explicitly, or plain POST when `to` is supplied
        # (the caller clearly intended a send even without the definer).
        if method == "POST" and (
            resolved_definer == "SEND"
            or (resolved_definer == "CREATE" and to is not None)
        ):
            if not to:
                return err_envelope(
                    method=method, definer=resolved_definer,
                    error=ToolError(
                        ErrorCode.MISSING_PARAM,
                        "'to' is required for send (POST+SEND)",
                        hint="Pass definer='SEND' or use op='send'",
                    ),
                )
            result = await message_bus.send(
                sender=sender or "caller",
                recipient=to,
                kind=kind,
                body=body,
                correlation_id=correlation_id,
                ttl_seconds=ttl_seconds,
                agent_registry=agent_registry,
            )
            return ok_envelope(method=method, definer="SEND", data=result)

        # ---- ACK ----
        if method == "POST" and resolved_definer == "TRIGGER":
            if not agent:
                return err_envelope(
                    method=method, definer=resolved_definer,
                    error=ToolError(ErrorCode.MISSING_PARAM, "'agent' is required for ack"),
                )
            if up_to_cursor is None:
                return err_envelope(
                    method=method, definer=resolved_definer,
                    error=ToolError(
                        ErrorCode.MISSING_PARAM,
                        "'up_to_cursor' is required for ack",
                    ),
                )
            result = await message_bus.ack(
                recipient=f"agent:{agent}" if not agent.startswith("agent:") else agent,
                up_to_cursor=up_to_cursor,
            )
            return ok_envelope(method=method, definer=resolved_definer, data=result)

        # ---- GET ----
        if method == "GET":
            # status sub-op
            if target == "status":
                inboxes = await message_bus.list_inboxes()
                return ok_envelope(method=method, data={"inboxes": inboxes})

            # peek sub-op
            if target == "peek":
                if not agent:
                    return err_envelope(
                        method=method,
                        error=ToolError(
                            ErrorCode.MISSING_PARAM, "'agent' is required for peek"
                        ),
                    )
                recipient_addr = (
                    f"agent:{agent}"
                    if not agent.startswith("agent:")
                    else agent
                )
                result = await message_bus.peek(recipient_addr, limit=5)
                return ok_envelope(method=method, data=result)

            # receive (default GET)
            if not agent:
                return err_envelope(
                    method=method,
                    error=ToolError(
                        ErrorCode.MISSING_PARAM,
                        "'agent' is required for receive "
                        "(use target='status' for inbox stats)",
                    ),
                )
            recipient_addr = (
                f"agent:{agent}"
                if not agent.startswith("agent:")
                else agent
            )
            clamped_wait = max(0, min(wait_up_to, 600))
            result = await message_bus.receive(
                recipient=recipient_addr,
                wait_up_to=clamped_wait,
                since_cursor=since_cursor,
                kinds=kinds,
                limit=max(1, min(limit, 500)),
            )
            return ok_envelope(method=method, data=result)

        # ---- Unsupported method ----
        return err_envelope(
            method=method,
            definer=resolved_definer,
            error=ToolError(
                ErrorCode.INVALID_OP,
                f"bus does not support {method}"
                + (f"+{resolved_definer}" if resolved_definer else ""),
                hint="Supported ops: GET (receive/status/peek), POST+SEND, POST+TRIGGER (ack), OPTIONS",
            ),
        )

    except Exception as e:
        return err_envelope(
            method=method,
            definer=resolved_definer,
            error=ToolError.from_exception(e),
        )


def register(mcp) -> None:
    """Register the bus action tool with the FastMCP instance."""
    mcp.tool(name="bus")(bus)
