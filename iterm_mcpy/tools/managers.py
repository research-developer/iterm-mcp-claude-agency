"""Manager agent tools.

Provides tools for managing manager agents that orchestrate worker agents,
delegating tasks through delegation strategies, and executing multi-step
task plans. Includes helpers to wire execution callbacks to workers via
the terminal/agent registry.
"""

import asyncio
import json
import logging
from typing import Optional

from mcp.server.fastmcp import Context

from core.agents import AgentRegistry
from core.manager import (
    DelegationStrategy,
    ManagerAgent,
    ManagerRegistry,
    SessionRole as ManagerSessionRole,
    TaskPlan,
    TaskStep,
)
from core.models import (
    DelegateTaskRequest,
    ExecutePlanRequest,
    ManageManagersRequest,
    ManageManagersResponse,
    PlanResultResponse,
    TaskResultResponse,
)
from core.terminal import ItermTerminal


async def _execute_task_on_worker(
    worker: str,
    task: str,
    timeout_seconds: Optional[int],
    terminal: ItermTerminal,
    agent_registry: AgentRegistry,
    logger: logging.Logger,
) -> tuple[Optional[str], bool, Optional[str]]:
    """Execute a task on a worker agent and return result.

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

        # Wait for command to complete with proper timeout
        # Use a polling approach to check for command completion
        wait_time = timeout_seconds if timeout_seconds else 30
        poll_interval = 0.5
        elapsed = 0.0
        completed = False

        while elapsed < wait_time:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            # Check if session is no longer processing (command completed)
            if hasattr(session, 'is_processing') and not session.is_processing:
                completed = True
                break

        # Read output (always — may be partial if timed out)
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


async def manage_managers(
    request: ManageManagersRequest,
    ctx: Context,
) -> str:
    """Manage manager agents with a single consolidated tool.

    Consolidates: create_manager, list_managers, get_manager_info, remove_manager,
                  add_worker_to_manager, remove_worker_from_manager

    Operations:
    - create: Create a new manager (requires manager_name)
    - list: List all managers
    - get_info: Get info about a manager (requires manager_name)
    - remove: Remove a manager (requires manager_name)
    - add_worker: Add a worker to a manager (requires manager_name, worker_name)
    - remove_worker: Remove a worker from a manager (requires manager_name, worker_name)

    Args:
        request: The manager operation request with operation type and parameters

    Returns:
        JSON with operation results
    """
    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    manager_registry: ManagerRegistry = ctx.request_context.lifespan_context["manager_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        if request.operation == "create":
            if not request.manager_name:
                response = ManageManagersResponse(
                    operation=request.operation,
                    success=False,
                    error="manager_name is required for create operation"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            # Convert worker roles from strings to SessionRole
            worker_roles = {}
            for worker, role_str in request.worker_roles.items():
                worker_roles[worker] = ManagerSessionRole(role_str)

            # Create the manager
            manager = manager_registry.create_manager(
                name=request.manager_name,
                workers=request.workers,
                delegation_strategy=DelegationStrategy(request.delegation_strategy),
                worker_roles=worker_roles,
                metadata=request.metadata,
            )

            # Set up execution callbacks
            _setup_manager_callbacks(manager, terminal, agent_registry, logger)

            logger.info(f"Created manager '{request.manager_name}' with {len(request.workers)} workers")

            response = ManageManagersResponse(
                operation=request.operation,
                success=True,
                data={
                    "name": manager.name,
                    "workers": manager.workers,
                    "delegation_strategy": manager.strategy.value,
                    "created": True
                }
            )
            return response.model_dump_json(indent=2, exclude_none=True)

        elif request.operation == "list":
            managers = manager_registry.list_managers()

            result = []
            for manager in managers:
                result.append({
                    "name": manager.name,
                    "workers": manager.workers,
                    "delegation_strategy": manager.strategy.value,
                    "worker_count": len(manager.workers),
                })

            logger.info(f"Listed {len(managers)} managers")
            response = ManageManagersResponse(
                operation=request.operation,
                success=True,
                data={"managers": result, "count": len(result)}
            )
            return response.model_dump_json(indent=2, exclude_none=True)

        elif request.operation == "get_info":
            if not request.manager_name:
                response = ManageManagersResponse(
                    operation=request.operation,
                    success=False,
                    error="manager_name is required for get_info operation"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            manager = manager_registry.get_manager(request.manager_name)
            if not manager:
                response = ManageManagersResponse(
                    operation=request.operation,
                    success=False,
                    error=f"Manager '{request.manager_name}' not found"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            response = ManageManagersResponse(
                operation=request.operation,
                success=True,
                data={
                    "name": manager.name,
                    "workers": manager.workers,
                    "worker_roles": {k: v.value for k, v in manager.worker_roles.items()},
                    "delegation_strategy": manager.strategy.value,
                    "created_at": manager.created_at.isoformat(),
                    "metadata": manager.metadata,
                }
            )
            return response.model_dump_json(indent=2, exclude_none=True)

        elif request.operation == "remove":
            if not request.manager_name:
                response = ManageManagersResponse(
                    operation=request.operation,
                    success=False,
                    error="manager_name is required for remove operation"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            removed = manager_registry.remove_manager(request.manager_name)

            if removed:
                logger.info(f"Removed manager '{request.manager_name}'")
            else:
                logger.warning(f"Manager '{request.manager_name}' not found")

            response = ManageManagersResponse(
                operation=request.operation,
                success=removed,
                data={"manager_name": request.manager_name}
            )
            return response.model_dump_json(indent=2, exclude_none=True)

        elif request.operation == "add_worker":
            if not request.manager_name or not request.worker_name:
                response = ManageManagersResponse(
                    operation=request.operation,
                    success=False,
                    error="manager_name and worker_name are required for add_worker operation"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            manager = manager_registry.get_manager(request.manager_name)
            if not manager:
                response = ManageManagersResponse(
                    operation=request.operation,
                    success=False,
                    error=f"Manager '{request.manager_name}' not found"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            role = ManagerSessionRole(request.worker_role) if request.worker_role else None
            manager.add_worker(request.worker_name, role)

            logger.info(f"Added worker '{request.worker_name}' to manager '{request.manager_name}'")

            response = ManageManagersResponse(
                operation=request.operation,
                success=True,
                data={
                    "manager_name": request.manager_name,
                    "worker_name": request.worker_name,
                    "role": request.worker_role
                }
            )
            return response.model_dump_json(indent=2, exclude_none=True)

        elif request.operation == "remove_worker":
            if not request.manager_name or not request.worker_name:
                response = ManageManagersResponse(
                    operation=request.operation,
                    success=False,
                    error="manager_name and worker_name are required for remove_worker operation"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            manager = manager_registry.get_manager(request.manager_name)
            if not manager:
                response = ManageManagersResponse(
                    operation=request.operation,
                    success=False,
                    error=f"Manager '{request.manager_name}' not found"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            removed = manager.remove_worker(request.worker_name)

            if removed:
                logger.info(f"Removed worker '{request.worker_name}' from manager '{request.manager_name}'")
            else:
                logger.warning(f"Worker '{request.worker_name}' not found in manager '{request.manager_name}'")

            response = ManageManagersResponse(
                operation=request.operation,
                success=removed,
                data={
                    "manager_name": request.manager_name,
                    "worker_name": request.worker_name
                }
            )
            return response.model_dump_json(indent=2, exclude_none=True)

        else:
            response = ManageManagersResponse(
                operation=request.operation,
                success=False,
                error=f"Unknown operation: {request.operation}"
            )
            return response.model_dump_json(indent=2, exclude_none=True)

    except Exception as e:
        logger.error(f"Error in manage_managers: {e}")
        response = ManageManagersResponse(
            operation=request.operation,
            success=False,
            error=str(e)
        )
        return response.model_dump_json(indent=2, exclude_none=True)


async def delegate_task(
    request: DelegateTaskRequest,
    ctx: Context,
) -> str:
    """Delegate a task through a manager to an appropriate worker.

    The manager selects a worker based on its delegation strategy and the
    required role. The task is executed and optionally validated.

    Args:
        request: Task delegation request with manager, task, and options

    Returns:
        JSON with task execution result
    """
    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    manager_registry: ManagerRegistry = ctx.request_context.lifespan_context["manager_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        manager = manager_registry.get_manager(request.manager)
        if not manager:
            return json.dumps({"error": f"Manager '{request.manager}' not found"}, indent=2)

        # Ensure callbacks are set up
        _setup_manager_callbacks(manager, terminal, agent_registry, logger)

        # Convert role string to ManagerSessionRole if provided
        role = ManagerSessionRole(request.role) if request.role else None

        # Delegate the task
        result = await manager.delegate(
            task=request.task,
            required_role=role,
            validation=request.validation,
            timeout_seconds=request.timeout_seconds,
            retry_count=request.retry_count,
        )

        logger.info(f"Task delegated via manager '{request.manager}': {result.status.value}")

        response = TaskResultResponse(
            task_id=result.task_id,
            task=result.task,
            worker=result.worker,
            status=result.status.value,
            success=result.success,
            output=result.output,
            error=result.error,
            duration_seconds=result.duration_seconds,
            validation_passed=result.validation_passed,
            validation_message=result.validation_message,
        )
        return response.model_dump_json(indent=2, exclude_none=True)

    except Exception as e:
        logger.error(f"Error delegating task: {e}")
        return json.dumps({"error": str(e)}, indent=2)


async def execute_plan(
    request: ExecutePlanRequest,
    ctx: Context,
) -> str:
    """Execute a multi-step task plan through a manager.

    The manager orchestrates the execution of multiple steps, handling
    dependencies and parallel execution as specified in the plan.

    Args:
        request: Plan execution request with manager and plan specification

    Returns:
        JSON with plan execution results
    """
    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    manager_registry: ManagerRegistry = ctx.request_context.lifespan_context["manager_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        manager = manager_registry.get_manager(request.manager)
        if not manager:
            return json.dumps({"error": f"Manager '{request.manager}' not found"}, indent=2)

        # Ensure callbacks are set up
        _setup_manager_callbacks(manager, terminal, agent_registry, logger)

        # Convert plan spec to TaskPlan
        steps = []
        for step_spec in request.plan.steps:
            role = ManagerSessionRole(step_spec.role) if step_spec.role else None
            step = TaskStep(
                id=step_spec.id,
                task=step_spec.task,
                role=role,
                optional=step_spec.optional,
                depends_on=step_spec.depends_on,
                validation=step_spec.validation,
                timeout_seconds=step_spec.timeout_seconds,
                retry_count=step_spec.retry_count,
            )
            steps.append(step)

        plan = TaskPlan(
            name=request.plan.name,
            description=request.plan.description,
            steps=steps,
            parallel_groups=request.plan.parallel_groups,
            stop_on_failure=request.plan.stop_on_failure,
        )

        # Execute the plan
        plan_result = await manager.orchestrate(plan)

        logger.info(
            f"Plan '{plan.name}' completed: success={plan_result.success}, "
            f"steps={len(plan_result.results)}"
        )

        # Convert results to response
        result_responses = []
        for result in plan_result.results:
            result_responses.append(TaskResultResponse(
                task_id=result.task_id,
                task=result.task,
                worker=result.worker,
                status=result.status.value,
                success=result.success,
                output=result.output,
                error=result.error,
                duration_seconds=result.duration_seconds,
                validation_passed=result.validation_passed,
                validation_message=result.validation_message,
            ))

        response = PlanResultResponse(
            plan_name=plan_result.plan_name,
            success=plan_result.success,
            results=result_responses,
            duration_seconds=plan_result.duration_seconds,
            stopped_early=plan_result.stopped_early,
            stop_reason=plan_result.stop_reason,
        )
        return response.model_dump_json(indent=2, exclude_none=True)

    except Exception as e:
        logger.error(f"Error executing plan: {e}")
        return json.dumps({"error": str(e)}, indent=2)


def register(mcp):
    """Register manager agent tools with the FastMCP instance."""
    mcp.tool()(manage_managers)
    mcp.tool()(delegate_task)
    mcp.tool()(execute_plan)
