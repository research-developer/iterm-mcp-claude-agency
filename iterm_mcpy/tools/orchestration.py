"""Orchestration tools.

Provides the orchestrate_playbook tool for executing a high-level playbook
spec composed of a layout, a sequence of commands, a cascade message, and
a final read request.
"""

from typing import List

from mcp.server.fastmcp import Context

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


def _ensure_model(model_cls, payload):
    """Validate or coerce an incoming payload into a Pydantic model."""
    if isinstance(payload, model_cls):
        return payload
    return model_cls.model_validate(payload)


async def orchestrate_playbook(request: OrchestrateRequest, ctx: Context) -> str:
    """Execute a high-level playbook (layout + commands + cascade + reads)."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    layout_manager = ctx.request_context.lifespan_context["layout_manager"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    profile_manager = ctx.request_context.lifespan_context["profile_manager"]
    lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
    notification_manager = ctx.request_context.lifespan_context.get("notification_manager")
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        orchestration_request = _ensure_model(OrchestrateRequest, request)
        playbook = orchestration_request.playbook

        response = OrchestrateResponse()

        if playbook.layout:
            response.layout = await execute_create_sessions(
                playbook.layout, terminal, layout_manager, agent_registry, logger,
                profile_manager=profile_manager
            )

        command_results: List[PlaybookCommandResult] = []
        for command in playbook.commands:
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
            command_results.append(PlaybookCommandResult(name=command.name, write_result=write_result))

        response.commands = command_results

        if playbook.cascade:
            response.cascade = await execute_cascade_request(playbook.cascade, terminal, agent_registry, logger)

        if playbook.reads:
            response.reads = await execute_read_request(playbook.reads, terminal, agent_registry, logger)

        logger.info(
            "Playbook completed: layout=%s, commands=%s, cascade=%s, reads=%s",
            bool(response.layout),
            len(response.commands),
            bool(response.cascade),
            bool(response.reads),
        )

        return response.model_dump_json(indent=2, exclude_none=True)
    except Exception as e:
        logger.error(f"Error orchestrating playbook: {e}")
        return f"Error: {e}"


def register(mcp):
    """Register orchestration tools with the FastMCP instance."""
    mcp.tool()(orchestrate_playbook)
