"""SP2 `messages` action tool — Task 13/14.

Replaces the legacy ``send_cascade_message`` and ``send_hierarchical_message``
tools. Both were POST-like broadcasts to sessions; v2 unifies them under a
single ``POST+SEND /messages`` surface.

Shape discrimination:
    - If the caller supplies a ``cascade`` dict (broadcast / teams / agents),
      route through ``execute_cascade_request`` — the same path the legacy
      ``send_cascade_message`` used.
    - If the caller supplies hierarchical ``targets`` (a list of SendTarget-
      shaped dicts with team/agent/message), route through the hierarchical
      cascade delivery logic (mirrors legacy ``send_hierarchical_message``).

Only POST+SEND is supported. Any other (op, definer) pair returns an
err envelope.
"""
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context

from core.agents import CascadingMessage, SendTarget
from core.definer_verbs import DefinerError, resolve_op
from core.models import CascadeMessageRequest
from iterm_mcpy.helpers import execute_cascade_request
from iterm_mcpy.responses import err_envelope, ok_envelope


async def _deliver_hierarchical(
    terminal,
    agent_registry,
    targets: List[Dict[str, Any]],
    broadcast: Optional[str],
    skip_duplicates: bool,
    execute: bool,
    logger,
) -> Dict[str, Any]:
    """Deliver a hierarchical cascade — mirrors legacy ``send_hierarchical_message``.

    Builds a ``CascadingMessage`` from a list of ``SendTarget``-shaped dicts
    (each with optional team/agent/message) and delivers it via the agent
    registry's cascade resolver.
    """
    send_targets = [SendTarget(**t) for t in targets]

    cascade = CascadingMessage(broadcast=broadcast, teams={}, agents={})

    for target in send_targets:
        if target.team and target.agent:
            agent_obj = agent_registry.get_agent(target.agent)
            if not agent_obj or not agent_obj.is_member_of(target.team):
                logger.error(
                    f"Agent '{target.agent}' is not a member of team "
                    f"'{target.team}'. Skipping."
                )
                continue
            cascade.agents[target.agent] = target.message or broadcast or ""
        elif target.agent:
            cascade.agents[target.agent] = target.message or broadcast or ""
        elif target.team:
            cascade.teams[target.team] = target.message or broadcast or ""

    message_targets = agent_registry.resolve_cascade_targets(cascade)

    results: List[Dict[str, Any]] = []
    delivered = 0
    skipped = 0

    for message, agent_names in message_targets.items():
        if skip_duplicates:
            agent_names = agent_registry.filter_unsent_recipients(message, agent_names)

        actually_delivered: List[str] = []

        for agent_name in agent_names:
            agent = agent_registry.get_agent(agent_name)
            if not agent:
                continue

            session = await terminal.get_session_by_id(agent.session_id)
            if not session:
                results.append({
                    "agent": agent_name,
                    "delivered": False,
                    "skipped_reason": "session_not_found",
                })
                continue

            await session.send_text(message, execute=execute)
            agent_registry.record_message_sent(message, [agent_name])
            delivered += 1
            actually_delivered.append(agent_name)

        # One skip per non-delivered target — single source of truth, no
        # per-iteration +1s to avoid double-counting when the whole group
        # fails.
        skipped += len(agent_names) - len(actually_delivered)

        results.append({
            "message": message,
            "targets": list(agent_names),
            "delivered": actually_delivered,
        })

    logger.info(f"Delivered {delivered} hierarchical messages ({skipped} skipped)")

    return {
        "results": results,
        "delivered": delivered,
        "skipped": skipped,
    }


async def messages(
    ctx: Context,
    op: str = "POST",
    definer: Optional[str] = None,
    cascade: Optional[Dict[str, Any]] = None,
    targets: Optional[List[Dict[str, Any]]] = None,
    broadcast: Optional[str] = None,
    skip_duplicates: bool = True,
    execute: bool = True,
) -> str:
    """Send messages to sessions using cascade or hierarchical targeting.

    Unifies the legacy ``send_cascade_message`` and
    ``send_hierarchical_message`` tools under one POST+SEND action tool.

    Shape discrimination:
        - Pass ``cascade`` (a dict with optional broadcast/teams/agents) to
          use the cascade delivery path. Equivalent to legacy
          ``send_cascade_message``.
        - Pass ``targets`` (a list of SendTarget-shaped dicts with
          team/agent/message) to use hierarchical delivery. Equivalent to
          legacy ``send_hierarchical_message``. Combine with ``broadcast``
          for a default message to all resolved targets.

    Only POST+SEND is supported.

    Args:
        op: HTTP method or friendly verb (default "POST"). Friendly verbs
            like "send", "notify", "dispatch" also resolve to POST+SEND.
        definer: Explicit definer — must be SEND when provided.
        cascade: Cascade dict (broadcast / teams / agents / skip_duplicates
            / execute). Uses ``execute_cascade_request`` under the hood.
        targets: List of hierarchical target dicts (team/agent/message).
        broadcast: Default message for hierarchical targets when they don't
            carry their own.
        skip_duplicates: Skip already-delivered messages (hierarchical only;
            cascade carries its own skip_duplicates in the dict).
        execute: Press Enter after sending (hierarchical only).
    """
    # Resolve and validate op.
    try:
        resolution = resolve_op(op, definer)
    except DefinerError as e:
        return err_envelope(method=op.upper(), error=str(e))

    if resolution.method != "POST" or resolution.definer != "SEND":
        return err_envelope(
            method=resolution.method,
            definer=resolution.definer,
            error=(
                f"messages only supports POST+SEND "
                f"(got {resolution.method}+{resolution.definer})"
            ),
        )

    # Exactly one of cascade/targets must be supplied.
    if cascade is None and not targets:
        return err_envelope(
            method="POST", definer="SEND",
            error="messages requires either 'cascade' or 'targets'",
        )
    if cascade is not None and targets:
        return err_envelope(
            method="POST", definer="SEND",
            error="messages accepts either 'cascade' or 'targets', not both",
        )

    try:
        lifespan = ctx.request_context.lifespan_context
        terminal = lifespan["terminal"]
        agent_registry = lifespan["agent_registry"]
        logger = lifespan["logger"]

        if cascade is not None:
            cascade_request = CascadeMessageRequest.model_validate(cascade)
            result = await execute_cascade_request(
                cascade_request, terminal, agent_registry, logger
            )
            return ok_envelope(
                method="POST",
                definer="SEND",
                data=result,
            )

        # Hierarchical path.
        result = await _deliver_hierarchical(
            terminal=terminal,
            agent_registry=agent_registry,
            targets=targets or [],
            broadcast=broadcast,
            skip_duplicates=skip_duplicates,
            execute=execute,
            logger=logger,
        )
        return ok_envelope(method="POST", definer="SEND", data=result)
    except Exception as e:
        return err_envelope(
            method="POST",
            definer="SEND",
            error=str(e),
        )


def register(mcp):
    """Register the messages action tool."""
    mcp.tool(name="messages")(messages)
