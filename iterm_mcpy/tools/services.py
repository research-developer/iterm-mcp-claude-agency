"""Service management tools.

Unified service management that consolidates service operations like
list, start, stop, add, configure, and list_inactive into a single tool.
"""

from mcp.server.fastmcp import Context

from core.models import (
    ManageServicesRequest,
    ManageServicesResponse,
)
from core.services import (
    ServicePriority,
    ServiceConfig,
)


async def manage_services(request: ManageServicesRequest, ctx: Context) -> str:
    """Unified service management - consolidates 6 service tools into one.

    Operations:
    - list: List configured services (optional: repo_path, min_priority, include_status)
    - start: Start a service (requires service_name; optional: repo_path)
    - stop: Stop a service (requires service_name)
    - add: Add new service (requires service_name, command; optional: priority, display_name, port, etc.)
    - configure: Update service config (requires service_name; optional: priority, port, command, etc.)
    - list_inactive: Get services that should be running but aren't (requires repo_path)

    Args:
        request: ManageServicesRequest with operation and relevant parameters

    Returns:
        JSON with operation result
    """
    service_manager = ctx.request_context.lifespan_context["service_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        op = request.operation

        # LIST operation
        if op == "list":
            priority = None
            if request.min_priority:
                priority = ServicePriority.from_string(request.min_priority)

            if request.repo_path:
                services = service_manager.get_merged_services(request.repo_path, priority)
            else:
                global_registry = service_manager.load_global_config()
                services = global_registry.services
                if priority:
                    priority_order = [
                        ServicePriority.QUIET,
                        ServicePriority.OPTIONAL,
                        ServicePriority.PREFERRED,
                        ServicePriority.REQUIRED
                    ]
                    min_idx = priority_order.index(priority)
                    services = [
                        s for s in services
                        if priority_order.index(s.priority) >= min_idx
                    ]

            result = []
            for service in services:
                info = {
                    "name": service.name,
                    "display_name": service.effective_display_name,
                    "priority": service.priority.value,
                    "command": service.command,
                    "port": service.port,
                    "working_directory": service.working_directory,
                }
                if request.include_status:
                    is_running = await service_manager.check_service_running(service)
                    info["is_running"] = is_running
                result.append(info)

            return ManageServicesResponse(
                operation=op,
                success=True,
                data={"services": result, "count": len(result), "repo_path": request.repo_path}
            ).model_dump_json(indent=2, exclude_none=True)

        # START operation
        elif op == "start":
            if not request.service_name:
                raise ValueError("service_name is required for start operation")

            if request.repo_path:
                services = service_manager.get_merged_services(request.repo_path)
            else:
                global_registry = service_manager.load_global_config()
                services = global_registry.services

            service = None
            for s in services:
                if s.name == request.service_name:
                    service = s
                    break

            if not service:
                return ManageServicesResponse(
                    operation=op,
                    success=False,
                    error=f"Service '{request.service_name}' not found",
                    data={"available_services": [s.name for s in services]}
                ).model_dump_json(indent=2, exclude_none=True)

            state = await service_manager.start_service(service, repo_path=request.repo_path)

            return ManageServicesResponse(
                operation=op,
                success=state.is_running,
                data={
                    "service": request.service_name,
                    "started": state.is_running,
                    "session_id": state.session_id,
                    "error": state.error_message,
                }
            ).model_dump_json(indent=2, exclude_none=True)

        # STOP operation
        elif op == "stop":
            if not request.service_name:
                raise ValueError("service_name is required for stop operation")

            success = await service_manager.stop_service(request.service_name)

            return ManageServicesResponse(
                operation=op,
                success=success,
                data={"service": request.service_name, "stopped": success}
            ).model_dump_json(indent=2, exclude_none=True)

        # ADD operation
        elif op == "add":
            if not request.service_name:
                raise ValueError("service_name is required for add operation")
            if not request.command:
                raise ValueError("command is required for add operation")

            service = ServiceConfig(
                name=request.service_name,
                display_name=request.display_name,
                command=request.command,
                priority=ServicePriority.from_string(request.priority or "optional"),
                port=request.port,
                working_directory=request.working_directory,
                repo_patterns=request.repo_patterns or [],
            )

            if request.scope == "repo":
                if not request.repo_path:
                    return ManageServicesResponse(
                        operation=op,
                        success=False,
                        error="repo_path required when scope is 'repo'"
                    ).model_dump_json(indent=2, exclude_none=True)

                registry = service_manager.load_repo_config(request.repo_path)
                registry.services = [s for s in registry.services if s.name != request.service_name]
                registry.services.append(service)
                service_manager.save_repo_config(request.repo_path, registry)
            else:
                registry = service_manager.load_global_config()
                registry.services = [s for s in registry.services if s.name != request.service_name]
                registry.services.append(service)
                service_manager.save_global_config(registry)

            logger.info(f"Added service '{request.service_name}' to {request.scope} config")

            return ManageServicesResponse(
                operation=op,
                success=True,
                data={"service": request.service_name, "scope": request.scope, "added": True}
            ).model_dump_json(indent=2, exclude_none=True)

        # CONFIGURE operation
        elif op == "configure":
            if not request.service_name:
                raise ValueError("service_name is required for configure operation")

            if request.scope == "repo":
                if not request.repo_path:
                    return ManageServicesResponse(
                        operation=op,
                        success=False,
                        error="repo_path required when scope is 'repo'"
                    ).model_dump_json(indent=2, exclude_none=True)
                registry = service_manager.load_repo_config(request.repo_path)
            else:
                registry = service_manager.load_global_config()

            found = False
            for i, service in enumerate(registry.services):
                if service.name == request.service_name:
                    found = True
                    updates = {}
                    if request.priority:
                        updates["priority"] = ServicePriority.from_string(request.priority)
                    if request.port is not None:
                        updates["port"] = request.port
                    if request.command:
                        updates["command"] = request.command
                    if request.working_directory:
                        updates["working_directory"] = request.working_directory

                    updated_data = service.model_dump()
                    updated_data.update(updates)
                    registry.services[i] = ServiceConfig.model_validate(updated_data)
                    break

            if not found:
                return ManageServicesResponse(
                    operation=op,
                    success=False,
                    error=f"Service '{request.service_name}' not found in {request.scope} config"
                ).model_dump_json(indent=2, exclude_none=True)

            if request.scope == "repo":
                service_manager.save_repo_config(request.repo_path, registry)
            else:
                service_manager.save_global_config(registry)

            logger.info(f"Updated service '{request.service_name}' in {request.scope} config")

            return ManageServicesResponse(
                operation=op,
                success=True,
                data={"service": request.service_name, "scope": request.scope, "updated": True}
            ).model_dump_json(indent=2, exclude_none=True)

        # LIST_INACTIVE operation
        elif op == "list_inactive":
            if not request.repo_path:
                raise ValueError("repo_path is required for list_inactive operation")

            priority = None
            if request.min_priority:
                priority = ServicePriority.from_string(request.min_priority)

            inactive = await service_manager.get_inactive_services(request.repo_path, priority)

            result = []
            for service in inactive:
                result.append({
                    "name": service.name,
                    "display_name": service.effective_display_name,
                    "priority": service.priority.value,
                    "command": service.command,
                })

            return ManageServicesResponse(
                operation=op,
                success=True,
                data={"inactive_services": result, "count": len(result), "repo_path": request.repo_path}
            ).model_dump_json(indent=2, exclude_none=True)

        else:
            return ManageServicesResponse(
                operation=op,
                success=False,
                error=f"Unknown operation: {op}"
            ).model_dump_json(indent=2, exclude_none=True)

    except Exception as e:
        logger.error(f"Error in manage_services ({request.operation}): {e}")
        return ManageServicesResponse(
            operation=request.operation,
            success=False,
            error=str(e)
        ).model_dump_json(indent=2, exclude_none=True)


def register(mcp):
    """Register service management tools with the FastMCP instance."""
    mcp.tool()(manage_services)
