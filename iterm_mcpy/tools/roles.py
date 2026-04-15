"""Role management tools.

Provides tools to assign, query, and manage session roles for tool access
control. Roles include devops, builder, debugger, researcher, tester,
orchestrator, monitor, and custom.
"""

import json
from typing import Optional

from mcp.server.fastmcp import Context

from core.agents import AgentRegistry
from core.models import SessionRole
from core.roles import RoleManager


async def assign_session_role(
    ctx: Context,
    session_id: str,
    role: str,
    assigned_by: Optional[str] = None,
) -> str:
    """Assign a role to a session for tool access control.

    Args:
        session_id: The iTerm session ID to assign the role to
        role: The role name (devops, builder, debugger, researcher, tester, orchestrator, monitor, custom)
        assigned_by: Optional agent name that is assigning this role (must have can_modify_roles permission)
    """
    role_manager: RoleManager = ctx.request_context.lifespan_context["role_manager"]
    agent_registry: AgentRegistry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Permission check: if assigned_by is provided, verify they have can_modify_roles
        if assigned_by:
            caller_agent = agent_registry.get_agent(assigned_by)
            if caller_agent:
                caller_session_id = caller_agent.session_id
                if not role_manager.can_modify_roles(caller_session_id):
                    return json.dumps({
                        "error": f"Agent '{assigned_by}' does not have permission to modify roles. "
                                 "Only sessions with can_modify_roles=True (e.g., orchestrator) can assign roles."
                    }, indent=2)

        # Convert string to SessionRole enum
        try:
            session_role = SessionRole(role.lower())
        except ValueError:
            valid_roles = [r.value for r in SessionRole]
            return json.dumps({
                "error": f"Invalid role '{role}'. Valid roles are: {valid_roles}"
            }, indent=2)

        assignment = role_manager.assign_role(
            session_id=session_id,
            role=session_role,
            assigned_by=assigned_by,
        )

        logger.info(f"Assigned role {role} to session {session_id}")

        return json.dumps({
            "status": "success",
            "session_id": session_id,
            "role": assignment.role.value,
            "description": assignment.role_config.description,
            "can_spawn_agents": assignment.role_config.can_spawn_agents,
            "can_modify_roles": assignment.role_config.can_modify_roles,
            "priority": assignment.role_config.priority,
            "assigned_at": assignment.assigned_at.isoformat(),
            "assigned_by": assignment.assigned_by,
        }, indent=2)
    except Exception as e:
        logger.error(f"Error assigning role: {e}")
        return json.dumps({"error": str(e)}, indent=2)


async def get_session_role(
    ctx: Context,
    session_id: str,
) -> str:
    """Get the role assignment for a session.

    Args:
        session_id: The iTerm session ID to get the role for
    """
    role_manager: RoleManager = ctx.request_context.lifespan_context["role_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        description = role_manager.describe(session_id)
        return json.dumps(description, indent=2)
    except Exception as e:
        logger.error(f"Error getting session role: {e}")
        return json.dumps({"error": str(e)}, indent=2)


async def remove_session_role(
    ctx: Context,
    session_id: str,
    removed_by: Optional[str] = None,
) -> str:
    """Remove the role assignment from a session.

    Args:
        session_id: The iTerm session ID to remove the role from
        removed_by: Optional agent name that is removing this role (must have can_modify_roles permission)
    """
    role_manager: RoleManager = ctx.request_context.lifespan_context["role_manager"]
    agent_registry: AgentRegistry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        # Permission check: if removed_by is provided, verify they have can_modify_roles
        if removed_by:
            caller_agent = agent_registry.get_agent(removed_by)
            if caller_agent:
                caller_session_id = caller_agent.session_id
                if not role_manager.can_modify_roles(caller_session_id):
                    return json.dumps({
                        "error": f"Agent '{removed_by}' does not have permission to modify roles. "
                                 "Only sessions with can_modify_roles=True (e.g., orchestrator) can remove roles."
                    }, indent=2)

        removed = role_manager.remove_role(session_id)
        if removed:
            logger.info(f"Removed role from session {session_id}")
            return json.dumps({
                "status": "success",
                "session_id": session_id,
                "message": "Role removed successfully"
            }, indent=2)
        else:
            return json.dumps({
                "status": "not_found",
                "session_id": session_id,
                "message": "No role was assigned to this session"
            }, indent=2)
    except Exception as e:
        logger.error(f"Error removing session role: {e}")
        return json.dumps({"error": str(e)}, indent=2)


async def list_session_roles(
    ctx: Context,
    role_filter: Optional[str] = None,
) -> str:
    """List all session role assignments.

    Args:
        role_filter: Optional role name to filter by
    """
    role_manager: RoleManager = ctx.request_context.lifespan_context["role_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        filter_role = None
        if role_filter:
            try:
                filter_role = SessionRole(role_filter.lower())
            except ValueError:
                valid_roles = [r.value for r in SessionRole]
                return json.dumps({
                    "error": f"Invalid role filter '{role_filter}'. Valid roles are: {valid_roles}"
                }, indent=2)

        assignments = role_manager.list_roles(role_filter=filter_role)

        result = []
        for assignment in assignments:
            result.append({
                "session_id": assignment.session_id,
                "role": assignment.role.value,
                "description": assignment.role_config.description,
                "priority": assignment.role_config.priority,
                "assigned_at": assignment.assigned_at.isoformat(),
                "assigned_by": assignment.assigned_by,
            })

        return json.dumps({
            "count": len(result),
            "assignments": result,
        }, indent=2)
    except Exception as e:
        logger.error(f"Error listing session roles: {e}")
        return json.dumps({"error": str(e)}, indent=2)


async def list_available_roles(
    ctx: Context,
) -> str:
    """List all available session roles and their default configurations."""
    from core.models import DEFAULT_ROLE_CONFIGS

    try:
        roles = []
        for role in SessionRole:
            config = DEFAULT_ROLE_CONFIGS.get(role)
            if config:
                roles.append({
                    "role": role.value,
                    "description": config.description,
                    "available_tools": config.available_tools,
                    "restricted_tools": config.restricted_tools,
                    "default_commands": config.default_commands,
                    "can_spawn_agents": config.can_spawn_agents,
                    "can_modify_roles": config.can_modify_roles,
                    "priority": config.priority,
                })
            else:
                roles.append({
                    "role": role.value,
                    "description": f"Custom role: {role.value}",
                    "available_tools": [],
                    "restricted_tools": [],
                    "default_commands": [],
                    "can_spawn_agents": False,
                    "can_modify_roles": False,
                    "priority": 3,
                })

        return json.dumps({
            "count": len(roles),
            "roles": roles,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


async def check_tool_permission(
    ctx: Context,
    session_id: str,
    tool_name: str,
) -> str:
    """Check if a specific tool is allowed for a session based on its role.

    Args:
        session_id: The iTerm session ID to check
        tool_name: The name of the tool to check permission for
    """
    role_manager: RoleManager = ctx.request_context.lifespan_context["role_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        allowed, reason = role_manager.is_tool_allowed(session_id, tool_name)

        assignment = role_manager.get_role(session_id)
        role_info = {
            "role": assignment.role.value if assignment else None,
            "has_role": assignment is not None,
        }

        return json.dumps({
            "session_id": session_id,
            "tool_name": tool_name,
            "allowed": allowed,
            "reason": reason,
            **role_info,
        }, indent=2)
    except Exception as e:
        logger.error(f"Error checking tool permission: {e}")
        return json.dumps({"error": str(e)}, indent=2)


async def get_sessions_by_role(
    ctx: Context,
    role: str,
) -> str:
    """Get all session IDs that have a specific role assigned.

    Args:
        role: The role name to filter by
    """
    role_manager: RoleManager = ctx.request_context.lifespan_context["role_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        try:
            session_role = SessionRole(role.lower())
        except ValueError:
            valid_roles = [r.value for r in SessionRole]
            return json.dumps({
                "error": f"Invalid role '{role}'. Valid roles are: {valid_roles}"
            }, indent=2)

        session_ids = role_manager.get_sessions_by_role(session_role)

        return json.dumps({
            "role": session_role.value,
            "count": len(session_ids),
            "session_ids": session_ids,
        }, indent=2)
    except Exception as e:
        logger.error(f"Error getting sessions by role: {e}")
        return json.dumps({"error": str(e)}, indent=2)


def register(mcp):
    """Register role management tools with the FastMCP instance."""
    mcp.tool()(assign_session_role)
    mcp.tool()(get_session_role)
    mcp.tool()(remove_session_role)
    mcp.tool()(list_session_roles)
    mcp.tool()(list_available_roles)
    mcp.tool()(check_tool_permission)
    mcp.tool()(get_sessions_by_role)
