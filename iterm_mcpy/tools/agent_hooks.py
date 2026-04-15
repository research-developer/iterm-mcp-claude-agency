"""Agent hooks management tool.

Provides the manage_agent_hooks tool for unified agent hooks management
including global config, repo config, path change triggers, stats, and
iTerm user variable access.
"""

from pathlib import Path

from mcp.server.fastmcp import Context

from core.models import (
    ManageAgentHooksRequest,
    ManageAgentHooksResponse,
)


async def manage_agent_hooks(request: ManageAgentHooksRequest, ctx: Context) -> str:
    """Unified agent hooks management.

    Operations:
    - get_config: Get current global hooks configuration
    - update_config: Update global hooks configuration
    - get_repo_config: Get hooks config for a specific repo (.iterm/hooks.json)
    - trigger_path_change: Manually trigger path change hook for a session
    - get_stats: Get hook manager statistics
    - set_variable: Set an iTerm user variable to enable/disable hooks per session
    - get_variable: Get an iTerm user variable value

    Args:
        request: ManageAgentHooksRequest with operation and parameters

    Returns:
        JSON with operation result
    """
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        from core.agent_hooks import get_agent_hook_manager, GlobalHooksConfig

        hook_manager = get_agent_hook_manager()
        op = request.operation

        # GET_CONFIG operation
        if op == "get_config":
            config = hook_manager.config
            return ManageAgentHooksResponse(
                operation=op,
                success=True,
                data=config.model_dump()
            ).model_dump_json(indent=2, exclude_none=True)

        # UPDATE_CONFIG operation
        elif op == "update_config":
            config_updates = {}
            if request.enabled is not None:
                config_updates["enabled"] = request.enabled
            if request.auto_team_assignment is not None:
                config_updates["auto_team_assignment"] = request.auto_team_assignment
            if request.fallback_team_from_repo is not None:
                config_updates["fallback_team_from_repo"] = request.fallback_team_from_repo
            if request.pass_session_id_default is not None:
                config_updates["pass_session_id_default"] = request.pass_session_id_default

            if config_updates:
                # Update config
                current_data = hook_manager.config.model_dump()
                current_data.update(config_updates)
                hook_manager.config = GlobalHooksConfig(**current_data)
                hook_manager.save_global_config()
                logger.info(f"Updated agent hooks config: {config_updates}")

            return ManageAgentHooksResponse(
                operation=op,
                success=True,
                data={
                    "updated": config_updates,
                    "config": hook_manager.config.model_dump()
                }
            ).model_dump_json(indent=2, exclude_none=True)

        # GET_REPO_CONFIG operation
        elif op == "get_repo_config":
            if not request.repo_path:
                return ManageAgentHooksResponse(
                    operation=op,
                    success=False,
                    error="repo_path is required for get_repo_config operation"
                ).model_dump_json(indent=2, exclude_none=True)

            repo_config = hook_manager.load_repo_config(request.repo_path)
            return ManageAgentHooksResponse(
                operation=op,
                success=True,
                data={
                    "repo_path": request.repo_path,
                    "config": repo_config.model_dump() if repo_config else None,
                    "config_file": str(Path(request.repo_path) / hook_manager.config.repo_config_filename)
                }
            ).model_dump_json(indent=2, exclude_none=True)

        # TRIGGER_PATH_CHANGE operation
        elif op == "trigger_path_change":
            if not request.session_id:
                return ManageAgentHooksResponse(
                    operation=op,
                    success=False,
                    error="session_id is required for trigger_path_change operation"
                ).model_dump_json(indent=2, exclude_none=True)
            if not request.new_path:
                return ManageAgentHooksResponse(
                    operation=op,
                    success=False,
                    error="new_path is required for trigger_path_change operation"
                ).model_dump_json(indent=2, exclude_none=True)

            result = await hook_manager.on_path_changed(
                request.session_id,
                request.new_path,
                request.agent_name
            )
            logger.info(f"Triggered path change for {request.session_id}: {result.actions_taken}")

            return ManageAgentHooksResponse(
                operation=op,
                success=True,
                data=result.to_dict()
            ).model_dump_json(indent=2, exclude_none=True)

        # GET_STATS operation
        elif op == "get_stats":
            stats = hook_manager.get_stats()
            return ManageAgentHooksResponse(
                operation=op,
                success=True,
                data=stats
            ).model_dump_json(indent=2, exclude_none=True)

        # SET_VARIABLE operation - Set iTerm user variable to enable/disable hooks
        elif op == "set_variable":
            if not request.session_id:
                return ManageAgentHooksResponse(
                    operation=op,
                    success=False,
                    error="session_id is required for set_variable operation"
                ).model_dump_json(indent=2, exclude_none=True)
            if not request.variable_name:
                return ManageAgentHooksResponse(
                    operation=op,
                    success=False,
                    error="variable_name is required for set_variable operation"
                ).model_dump_json(indent=2, exclude_none=True)

            from core.iterm_path_monitor import set_user_variable
            connection = ctx.request_context.lifespan_context["connection"]
            success = await set_user_variable(
                connection,
                request.session_id,
                request.variable_name,
                request.variable_value or ""
            )
            logger.info(f"Set variable {request.variable_name}={request.variable_value} on session {request.session_id}: success={success}")

            return ManageAgentHooksResponse(
                operation=op,
                success=success,
                data={
                    "session_id": request.session_id,
                    "variable_name": request.variable_name,
                    "variable_value": request.variable_value
                },
                error=None if success else "Failed to set variable (session may not exist or iTerm not connected)"
            ).model_dump_json(indent=2, exclude_none=True)

        # GET_VARIABLE operation - Get iTerm user variable
        elif op == "get_variable":
            if not request.session_id:
                return ManageAgentHooksResponse(
                    operation=op,
                    success=False,
                    error="session_id is required for get_variable operation"
                ).model_dump_json(indent=2, exclude_none=True)
            if not request.variable_name:
                return ManageAgentHooksResponse(
                    operation=op,
                    success=False,
                    error="variable_name is required for get_variable operation"
                ).model_dump_json(indent=2, exclude_none=True)

            from core.iterm_path_monitor import get_user_variable
            connection = ctx.request_context.lifespan_context["connection"]
            value = await get_user_variable(connection, request.session_id, request.variable_name)
            logger.info(f"Got variable {request.variable_name} from session {request.session_id}: {value}")

            return ManageAgentHooksResponse(
                operation=op,
                success=True,
                data={
                    "session_id": request.session_id,
                    "variable_name": request.variable_name,
                    "variable_value": value
                }
            ).model_dump_json(indent=2, exclude_none=True)

        else:
            return ManageAgentHooksResponse(
                operation=op,
                success=False,
                error=f"Unknown operation: {op}"
            ).model_dump_json(indent=2, exclude_none=True)

    except Exception as e:
        logger.error(f"Error in manage_agent_hooks ({request.operation}): {e}")
        return ManageAgentHooksResponse(
            operation=request.operation,
            success=False,
            error=str(e)
        ).model_dump_json(indent=2, exclude_none=True)


def register(mcp):
    """Register agent hooks management tool with the FastMCP instance."""
    mcp.tool()(manage_agent_hooks)
