"""Wait for agent tool.

Provides a single tool to wait for an agent's session to complete work
or reach an idle state. Polls for output changes and emits a notification
when the wait completes (success or timeout).
"""

import asyncio
import time

from mcp.server.fastmcp import Context

from core.models import (
    WaitForAgentRequest,
    WaitResult,
)


def _ensure_model(model_cls, payload):
    """Validate or coerce an incoming payload into a Pydantic model."""
    if isinstance(payload, model_cls):
        return payload
    return model_cls.model_validate(payload)


async def wait_for_agent(request: WaitForAgentRequest, ctx: Context) -> str:
    """Wait for an agent to complete or reach idle state.

    This allows an orchestrator to wait for a subagent to finish its current
    task. If the wait times out, returns a progress summary so you can decide
    whether to wait longer or take action.

    Args:
        request: Contains agent name, timeout, and output options

    Returns:
        WaitResult with completion status, elapsed time, and optional output/summary
    """
    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    notification_manager = ctx.request_context.lifespan_context["notification_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        req = _ensure_model(WaitForAgentRequest, request)

        # Find the agent
        agent = agent_registry.get_agent(req.agent)
        if not agent:
            return WaitResult(
                agent=req.agent,
                completed=False,
                timed_out=False,
                elapsed_seconds=0,
                status="unknown",
                summary=f"Agent '{req.agent}' not found",
                can_continue_waiting=False,
            ).model_dump_json(indent=2, exclude_none=True)

        # Get the session
        session = await terminal.get_session_by_id(agent.session_id)
        if not session:
            return WaitResult(
                agent=req.agent,
                completed=False,
                timed_out=False,
                elapsed_seconds=0,
                status="unknown",
                summary=f"Session for agent '{req.agent}' not found",
                can_continue_waiting=False,
            ).model_dump_json(indent=2, exclude_none=True)

        logger.info(f"Waiting up to {req.wait_up_to}s for agent {req.agent}")

        # Capture initial output for comparison
        initial_output = await session.get_screen_contents()

        # Poll for completion
        start_time = time.time()
        poll_interval = 0.5  # Check every 500ms
        last_output = initial_output

        while True:
            elapsed = time.time() - start_time

            # Check if timed out
            if elapsed >= req.wait_up_to:
                # Timed out - generate summary
                current_output = await session.get_screen_contents()

                summary = None
                if req.summary_on_timeout:
                    # Generate a simple summary based on output changes
                    if current_output != initial_output:
                        lines = current_output.strip().split('\n')
                        last_lines = lines[-3:] if len(lines) > 3 else lines
                        summary = f"Still running. Last output: {' | '.join(last_lines)}"
                    else:
                        summary = "No output change detected during wait period"

                # Add notification
                await notification_manager.add_simple(
                    agent=req.agent,
                    level="info",
                    summary=f"Wait timed out after {int(elapsed)}s",
                    context=summary,
                )

                result = WaitResult(
                    agent=req.agent,
                    completed=False,
                    timed_out=True,
                    elapsed_seconds=elapsed,
                    status="running",
                    output=current_output if req.return_output else None,
                    summary=summary,
                    can_continue_waiting=True,
                )
                logger.info(f"Wait for {req.agent} timed out after {elapsed:.1f}s")
                return result.model_dump_json(indent=2, exclude_none=True)

            # Check if processing has stopped (idle)
            is_processing = getattr(session, 'is_processing', False)
            if not is_processing:
                # Check if output has stabilized
                current_output = await session.get_screen_contents()
                if current_output == last_output:
                    # Agent appears idle
                    await notification_manager.add_simple(
                        agent=req.agent,
                        level="success",
                        summary=f"Completed after {int(elapsed)}s",
                    )

                    result = WaitResult(
                        agent=req.agent,
                        completed=True,
                        timed_out=False,
                        elapsed_seconds=elapsed,
                        status="idle",
                        output=current_output if req.return_output else None,
                        summary="Agent completed successfully",
                        can_continue_waiting=False,
                    )
                    logger.info(f"Agent {req.agent} completed after {elapsed:.1f}s")
                    return result.model_dump_json(indent=2, exclude_none=True)

                last_output = current_output

            await asyncio.sleep(poll_interval)

    except Exception as e:
        logger.error(f"Error waiting for agent: {e}")
        return WaitResult(
            agent=request.agent if hasattr(request, 'agent') else "unknown",
            completed=False,
            timed_out=False,
            elapsed_seconds=0,
            status="error",
            summary=str(e),
            can_continue_waiting=False,
        ).model_dump_json(indent=2, exclude_none=True)


def register(mcp):
    """Register wait tools with the FastMCP instance."""
    mcp.tool()(wait_for_agent)
