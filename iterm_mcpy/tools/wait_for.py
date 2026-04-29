"""SP2 `wait_for` action tool — Task 13/14.

Replaces the legacy ``wait_for_agent`` tool. GET-style long-poll that waits
until an agent's session becomes idle or the timeout elapses.

Only GET is supported (no state change). Any other (op, definer) pair
returns an err envelope.
"""
import asyncio
import time
from typing import Optional

from mcp.server.fastmcp import Context

from core.definer_verbs import DefinerError, resolve_op
from core.models import WaitResult
from iterm_mcpy.responses import err_envelope, ok_envelope
from iterm_mcpy.errors import ToolError


async def _wait_for_agent_impl(
    ctx: Context,
    agent_name: str,
    wait_up_to: int,
    return_output: bool,
    summary_on_timeout: bool,
) -> WaitResult:
    """Long-poll for an agent's session to become idle — legacy body port."""
    lifespan = ctx.request_context.lifespan_context
    terminal = lifespan["terminal"]
    agent_registry = lifespan["agent_registry"]
    notification_manager = lifespan["notification_manager"]
    logger = lifespan["logger"]

    agent = agent_registry.get_agent(agent_name)
    if not agent:
        return WaitResult(
            agent=agent_name,
            completed=False,
            timed_out=False,
            elapsed_seconds=0,
            status="unknown",
            summary=f"Agent '{agent_name}' not found",
            can_continue_waiting=False,
        )

    session = await terminal.get_session_by_id(agent.session_id)
    if not session:
        return WaitResult(
            agent=agent_name,
            completed=False,
            timed_out=False,
            elapsed_seconds=0,
            status="unknown",
            summary=f"Session for agent '{agent_name}' not found",
            can_continue_waiting=False,
        )

    logger.info(f"wait_for: waiting up to {wait_up_to}s for agent {agent_name}")

    initial_output = await session.get_screen_contents()
    start_time = time.time()
    poll_interval = 0.5
    last_output = initial_output

    while True:
        elapsed = time.time() - start_time

        if elapsed >= wait_up_to:
            current_output = await session.get_screen_contents()

            summary = None
            if summary_on_timeout:
                if current_output != initial_output:
                    lines = current_output.strip().split("\n")
                    last_lines = lines[-3:] if len(lines) > 3 else lines
                    summary = f"Still running. Last output: {' | '.join(last_lines)}"
                else:
                    summary = "No output change detected during wait period"

            await notification_manager.add_simple(
                agent=agent_name,
                level="info",
                summary=f"Wait timed out after {int(elapsed)}s",
                context=summary,
            )

            logger.info(f"wait_for: {agent_name} timed out after {elapsed:.1f}s")
            return WaitResult(
                agent=agent_name,
                completed=False,
                timed_out=True,
                elapsed_seconds=elapsed,
                status="running",
                output=current_output if return_output else None,
                summary=summary,
                can_continue_waiting=True,
            )

        is_processing = getattr(session, "is_processing", False)
        if not is_processing:
            current_output = await session.get_screen_contents()
            if current_output == last_output:
                await notification_manager.add_simple(
                    agent=agent_name,
                    level="success",
                    summary=f"Completed after {int(elapsed)}s",
                )
                logger.info(f"wait_for: {agent_name} completed after {elapsed:.1f}s")
                return WaitResult(
                    agent=agent_name,
                    completed=True,
                    timed_out=False,
                    elapsed_seconds=elapsed,
                    status="idle",
                    output=current_output if return_output else None,
                    summary="Agent completed successfully",
                    can_continue_waiting=False,
                )
            last_output = current_output

        await asyncio.sleep(poll_interval)


async def wait_for(
    ctx: Context,
    op: str = "GET",
    agent_name: Optional[str] = None,
    wait_up_to: int = 30,
    return_output: bool = True,
    summary_on_timeout: bool = True,
) -> str:
    """Long-poll until an agent becomes idle or the timeout elapses.

    Replaces the legacy ``wait_for_agent`` tool. Only GET is supported —
    waiting is an inherently safe/idempotent read-like operation.

    Args:
        op: HTTP method or friendly verb (default "GET"). Verbs like "get",
            "read", "check", "query" resolve to GET.
        agent_name: Agent to wait for (required).
        wait_up_to: Max seconds to wait (default 30, 1–600).
        return_output: Include recent output in the result (default True).
        summary_on_timeout: Include a progress summary on timeout
            (default True).
    """
    try:
        resolution = resolve_op(op, definer=None)
    except DefinerError as e:
        return err_envelope(method=op.upper(), error=ToolError.from_exception(e))

    if resolution.method != "GET":
        return err_envelope(
            method=resolution.method,
            definer=resolution.definer,
            error=f"wait_for only supports GET (got {resolution.method})",
        )

    if not agent_name:
        return err_envelope(
            method="GET",
            error="wait_for requires 'agent_name' parameter",
        )

    try:
        # Validate the bounds via the legacy request model — same
        # constraints (1–600s) the legacy tool enforced.
        if wait_up_to < 1 or wait_up_to > 600:
            return err_envelope(
                method="GET",
                error="wait_for: wait_up_to must be between 1 and 600 seconds",
            )

        result = await _wait_for_agent_impl(
            ctx=ctx,
            agent_name=agent_name,
            wait_up_to=wait_up_to,
            return_output=return_output,
            summary_on_timeout=summary_on_timeout,
        )
        return ok_envelope(method="GET", data=result)
    except Exception as e:
        return err_envelope(method="GET", error=ToolError.from_exception(e))


def register(mcp):
    """Register the wait_for action tool."""
    mcp.tool(name="wait_for")(wait_for)
