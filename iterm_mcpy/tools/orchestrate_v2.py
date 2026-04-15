"""SP2 `orchestrate_v2` action tool — Task 13/14.

Replaces the legacy ``orchestrate_playbook`` tool. Executes a high-level
playbook (layout + commands + cascade + reads) via a single POST+INVOKE
action.

Only POST+INVOKE is supported. Any other (op, definer) pair returns an
err envelope.
"""
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context

from core.definer_verbs import DefinerError, resolve_op
from core.models import (
    OrchestrateRequest,
    OrchestrateResponse,
    PlaybookCommandResult,
    WriteToSessionsRequest,
)
from iterm_mcpy.helpers import (
    execute_cascade_request,
    execute_create_sessions,
    execute_read_request,
    execute_write_request,
)
from iterm_mcpy.responses import err_envelope, ok_envelope


async def orchestrate_v2(
    ctx: Context,
    op: str = "POST",
    definer: Optional[str] = None,
    playbook: Optional[Dict[str, Any]] = None,
) -> str:
    """Orchestrate a playbook (layout + commands + cascade + reads).

    Replaces the legacy ``orchestrate_playbook`` tool. A playbook bundles
    an optional session layout, a sequence of command blocks, an optional
    cascade broadcast, and an optional final read — all executed in order
    with a single tool call.

    Only POST+INVOKE is supported.

    Args:
        op: HTTP method or friendly verb (default "POST"). Verbs like
            "invoke", "execute", "run", "orchestrate" resolve to POST+INVOKE.
        definer: Explicit definer — must be INVOKE when provided.
        playbook: Playbook spec (dict) with optional layout, commands,
            cascade, and reads sections. Validated into a
            :class:`core.models.Playbook` under the hood.
    """
    try:
        resolution = resolve_op(op, definer)
    except DefinerError as e:
        return err_envelope(method=op.upper(), error=str(e))

    if resolution.method != "POST" or resolution.definer != "INVOKE":
        return err_envelope(
            method=resolution.method,
            definer=resolution.definer,
            error=(
                f"orchestrate_v2 only supports POST+INVOKE "
                f"(got {resolution.method}+{resolution.definer})"
            ),
        )

    if playbook is None:
        return err_envelope(
            method="POST", definer="INVOKE",
            error="orchestrate_v2 requires 'playbook' parameter",
        )

    try:
        lifespan = ctx.request_context.lifespan_context
        terminal = lifespan["terminal"]
        layout_manager = lifespan["layout_manager"]
        agent_registry = lifespan["agent_registry"]
        profile_manager = lifespan["profile_manager"]
        lock_manager = lifespan.get("tag_lock_manager")
        notification_manager = lifespan.get("notification_manager")
        logger = lifespan["logger"]

        request = OrchestrateRequest.model_validate({"playbook": playbook})
        pb = request.playbook

        response = OrchestrateResponse()

        if pb.layout:
            response.layout = await execute_create_sessions(
                pb.layout, terminal, layout_manager, agent_registry, logger,
                profile_manager=profile_manager,
            )

        command_results: List[PlaybookCommandResult] = []
        for command in pb.commands:
            write_request = WriteToSessionsRequest(
                messages=command.messages,
                parallel=command.parallel,
                skip_duplicates=command.skip_duplicates,
            )
            write_result = await execute_write_request(
                write_request,
                terminal,
                agent_registry,
                logger,
                lock_manager=lock_manager,
                notification_manager=notification_manager,
            )
            command_results.append(
                PlaybookCommandResult(name=command.name, write_result=write_result)
            )
        response.commands = command_results

        if pb.cascade:
            response.cascade = await execute_cascade_request(
                pb.cascade, terminal, agent_registry, logger
            )

        if pb.reads:
            response.reads = await execute_read_request(
                pb.reads, terminal, agent_registry, logger
            )

        logger.info(
            "orchestrate_v2: layout=%s commands=%s cascade=%s reads=%s",
            bool(response.layout),
            len(response.commands),
            bool(response.cascade),
            bool(response.reads),
        )

        return ok_envelope(method="POST", definer="INVOKE", data=response)
    except Exception as e:
        return err_envelope(method="POST", definer="INVOKE", error=str(e))


def register(mcp):
    """Register the orchestrate_v2 action tool."""
    mcp.tool(name="orchestrate_v2")(orchestrate_v2)
