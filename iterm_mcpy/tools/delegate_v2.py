"""SP2 `delegate_v2` action tool — Task 13/14.

Replaces the legacy ``delegate_task`` and ``execute_plan`` tools, unifying
them behind a ``target`` discriminator:

    target="task" → delegate a single task to a manager's chosen worker
                    (maps to legacy delegate_task).
    target="plan" → execute a multi-step task plan through a manager
                    (maps to legacy execute_plan).

Only POST+INVOKE is supported. Any other (op, definer) pair returns an
err envelope.
"""
from typing import Any, Dict, Optional

from mcp.server.fastmcp import Context

from core.definer_verbs import DefinerError, resolve_op
from core.manager import (
    ManagerRegistry,
    SessionRole as ManagerSessionRole,
    TaskPlan,
    TaskStep,
)
from core.models import (
    DelegateTaskRequest,
    ExecutePlanRequest,
    PlanResultResponse,
    TaskResultResponse,
)
from iterm_mcpy.responses import err_envelope, ok_envelope
from iterm_mcpy.tools.managers import _setup_manager_callbacks


async def _delegate_task(
    ctx: Context,
    manager_name: str,
    task: str,
    role: Optional[str],
    validation: Optional[str],
    timeout_seconds: Optional[int],
    retry_count: int,
) -> TaskResultResponse:
    """Delegate a single task — mirrors legacy ``delegate_task`` body."""
    lifespan = ctx.request_context.lifespan_context
    terminal = lifespan["terminal"]
    agent_registry = lifespan["agent_registry"]
    manager_registry: ManagerRegistry = lifespan["manager_registry"]
    logger = lifespan["logger"]

    # Validate via Pydantic (same shape the legacy tool accepted).
    request = DelegateTaskRequest(
        manager=manager_name,
        task=task,
        role=role,
        validation=validation,
        timeout_seconds=timeout_seconds,
        retry_count=retry_count,
    )

    manager = manager_registry.get_manager(request.manager)
    if not manager:
        raise ValueError(f"Manager '{request.manager}' not found")

    _setup_manager_callbacks(manager, terminal, agent_registry, logger)

    session_role = ManagerSessionRole(request.role) if request.role else None

    result = await manager.delegate(
        task=request.task,
        required_role=session_role,
        validation=request.validation,
        timeout_seconds=request.timeout_seconds,
        retry_count=request.retry_count,
    )

    logger.info(f"delegate_v2 task: manager={request.manager} status={result.status.value}")

    return TaskResultResponse(
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


async def _execute_plan(
    ctx: Context,
    manager_name: str,
    plan: Dict[str, Any],
) -> PlanResultResponse:
    """Execute a multi-step plan — mirrors legacy ``execute_plan`` body."""
    lifespan = ctx.request_context.lifespan_context
    terminal = lifespan["terminal"]
    agent_registry = lifespan["agent_registry"]
    manager_registry: ManagerRegistry = lifespan["manager_registry"]
    logger = lifespan["logger"]

    request = ExecutePlanRequest.model_validate({"manager": manager_name, "plan": plan})

    manager = manager_registry.get_manager(request.manager)
    if not manager:
        raise ValueError(f"Manager '{request.manager}' not found")

    _setup_manager_callbacks(manager, terminal, agent_registry, logger)

    steps = []
    for step_spec in request.plan.steps:
        role = ManagerSessionRole(step_spec.role) if step_spec.role else None
        steps.append(TaskStep(
            id=step_spec.id,
            task=step_spec.task,
            role=role,
            optional=step_spec.optional,
            depends_on=step_spec.depends_on,
            validation=step_spec.validation,
            timeout_seconds=step_spec.timeout_seconds,
            retry_count=step_spec.retry_count,
        ))

    task_plan = TaskPlan(
        name=request.plan.name,
        description=request.plan.description,
        steps=steps,
        parallel_groups=request.plan.parallel_groups,
        stop_on_failure=request.plan.stop_on_failure,
    )

    plan_result = await manager.orchestrate(task_plan)

    logger.info(
        f"delegate_v2 plan: manager={request.manager} "
        f"plan={task_plan.name} success={plan_result.success}"
    )

    result_responses = [
        TaskResultResponse(
            task_id=r.task_id,
            task=r.task,
            worker=r.worker,
            status=r.status.value,
            success=r.success,
            output=r.output,
            error=r.error,
            duration_seconds=r.duration_seconds,
            validation_passed=r.validation_passed,
            validation_message=r.validation_message,
        )
        for r in plan_result.results
    ]

    return PlanResultResponse(
        plan_name=plan_result.plan_name,
        success=plan_result.success,
        results=result_responses,
        duration_seconds=plan_result.duration_seconds,
        stopped_early=plan_result.stopped_early,
        stop_reason=plan_result.stop_reason,
    )


async def delegate_v2(
    ctx: Context,
    op: str = "POST",
    definer: Optional[str] = None,
    target: str = "task",
    manager_name: Optional[str] = None,
    task: Optional[str] = None,
    role: Optional[str] = None,
    validation: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
    retry_count: int = 0,
    plan: Optional[Dict[str, Any]] = None,
) -> str:
    """Delegate a task or execute a multi-step plan through a manager.

    Replaces the legacy ``delegate_task`` and ``execute_plan`` tools. The
    ``target`` discriminator picks between the two paths:

        target='task' (default):
            Requires: manager_name, task
            Optional: role, validation, timeout_seconds, retry_count
            Mirrors legacy ``delegate_task``.

        target='plan':
            Requires: manager_name, plan (dict matching TaskPlanSpec)
            Mirrors legacy ``execute_plan``.

    Only POST+INVOKE is supported.

    Args:
        op: HTTP method or friendly verb (default "POST"). Verbs like
            "delegate", "invoke", "execute" resolve to POST+INVOKE.
        definer: Explicit definer — must be INVOKE when provided.
        target: "task" or "plan".
        manager_name: Name of the manager (required for both targets).
        task: Task description/command (required for target='task').
        role: Required worker role (target='task' only).
        validation: Validation pattern or 'success' (target='task' only).
        timeout_seconds: Execution timeout (target='task' only).
        retry_count: Retries on failure (target='task' only, 0-5).
        plan: TaskPlanSpec-shaped dict (required for target='plan').
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
                f"delegate_v2 only supports POST+INVOKE "
                f"(got {resolution.method}+{resolution.definer})"
            ),
        )

    if target not in ("task", "plan"):
        return err_envelope(
            method="POST", definer="INVOKE",
            error=f"delegate_v2 target must be 'task' or 'plan' (got {target!r})",
        )

    try:
        if target == "task":
            if not manager_name or not task:
                return err_envelope(
                    method="POST", definer="INVOKE",
                    error="delegate_v2 target='task' requires manager_name and task",
                )
            result = await _delegate_task(
                ctx,
                manager_name=manager_name,
                task=task,
                role=role,
                validation=validation,
                timeout_seconds=timeout_seconds,
                retry_count=retry_count,
            )
            return ok_envelope(method="POST", definer="INVOKE", data=result)

        # target == "plan"
        if not manager_name or plan is None:
            return err_envelope(
                method="POST", definer="INVOKE",
                error="delegate_v2 target='plan' requires manager_name and plan",
            )
        result = await _execute_plan(ctx, manager_name=manager_name, plan=plan)
        return ok_envelope(method="POST", definer="INVOKE", data=result)
    except Exception as e:
        return err_envelope(method="POST", definer="INVOKE", error=str(e))


def register(mcp):
    """Register the delegate_v2 action tool."""
    mcp.tool(name="delegate_v2")(delegate_v2)
