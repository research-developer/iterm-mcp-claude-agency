"""Shared callback helpers for manager-orchestrated tool execution.

Wires ``ManagerAgent._execute_callback`` to drive worker sessions through
the terminal + agent registry. Used by both the ``managers`` and
``delegate`` tools so the callback setup stays in one place rather than
duplicated in each tool module.
"""

import asyncio
import logging
from typing import Optional

from core.agents import AgentRegistry
from core.manager import ManagerAgent
from core.terminal import ItermTerminal


async def _execute_task_on_worker(
    worker: str,
    task: str,
    timeout_seconds: Optional[int],
    terminal: ItermTerminal,
    agent_registry: AgentRegistry,
    logger: logging.Logger,
) -> tuple[Optional[str], bool, Optional[str]]:
    """Execute a task on a worker agent and return (output, success, error).

    Args:
        worker: Worker agent name
        task: Command to execute
        timeout_seconds: Optional timeout
        terminal: Terminal instance
        agent_registry: Agent registry
        logger: Logger instance

    Returns:
        Tuple of (output, success, error)
    """
    agent = agent_registry.get_agent(worker)
    if not agent:
        return None, False, f"Worker agent '{worker}' not found"

    session = await terminal.get_session_by_id(agent.session_id)
    if not session:
        return None, False, f"Session for worker '{worker}' not found"

    try:
        # Send the command
        await session.send_text(task + "\n")

        # Wait for command to complete with proper timeout. Uses a polling
        # approach to check for command completion.
        wait_time = timeout_seconds if timeout_seconds else 30
        poll_interval = 0.5
        elapsed = 0.0
        completed = False

        while elapsed < wait_time:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            # Check if session is no longer processing (command completed).
            if hasattr(session, 'is_processing') and not session.is_processing:
                completed = True
                break

        # Read output (always — may be partial if timed out).
        output = await session.get_screen_contents(max_lines=100)

        # If we have is_processing and exited the loop without completion, report timeout.
        # (If is_processing isn't available, assume completion to preserve prior behavior.)
        if hasattr(session, 'is_processing') and not completed and session.is_processing:
            return output, False, f"Task timed out after {wait_time} seconds"

        return output, True, None

    except asyncio.TimeoutError:
        return None, False, f"Task timed out after {timeout_seconds} seconds"
    except Exception as e:
        logger.error(f"Error executing task on worker {worker}: {e}")
        return None, False, str(e)


def _setup_manager_callbacks(
    manager: ManagerAgent,
    terminal: ItermTerminal,
    agent_registry: AgentRegistry,
    logger: logging.Logger,
) -> None:
    """Set up execution callbacks for a manager agent."""

    async def execute_callback(
        worker: str,
        task: str,
        timeout_seconds: Optional[int],
    ) -> tuple[Optional[str], bool, Optional[str]]:
        return await _execute_task_on_worker(
            worker, task, timeout_seconds, terminal, agent_registry, logger
        )

    manager._execute_callback = execute_callback
