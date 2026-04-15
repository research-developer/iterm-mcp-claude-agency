"""Agent and team management tools.

Provides tools for registering agents, listing agents, removing agents,
and managing teams (create/list/remove/assign_agent/remove_agent) via a
single consolidated manage_teams tool.
"""

import json
from typing import Optional

from mcp.server.fastmcp import Context

from core.models import (
    ManageTeamsRequest,
    ManageTeamsResponse,
    RegisterAgentRequest,
)


def _ensure_model(model_cls, payload):
    """Validate or coerce an incoming payload into a Pydantic model."""
    if isinstance(payload, model_cls):
        return payload
    return model_cls.model_validate(payload)


async def register_agent(request: RegisterAgentRequest, ctx: Context) -> str:
    """Register an agent for a session."""

    terminal = ctx.request_context.lifespan_context["terminal"]
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        req = _ensure_model(RegisterAgentRequest, request)
        session = await terminal.get_session_by_id(req.session_id)
        if not session:
            return "No matching session found. Provide a valid session_id."

        agent = agent_registry.register_agent(
            name=req.name,
            session_id=session.id,
            teams=req.teams,
            metadata=req.metadata,
        )

        logger.info(f"Registered agent '{agent.name}' for session {session.name}")

        return json.dumps({
            "agent": agent.name,
            "session_id": agent.session_id,
            "session_name": session.name,
            "teams": agent.teams,
            "metadata": agent.metadata,
        }, indent=2)
    except Exception as e:
        logger.error(f"Error registering agent: {e}")
        return f"Error: {e}"


async def list_agents(ctx: Context, team: Optional[str] = None) -> str:
    """List all registered agents.

    Args:
        team: Filter by team name (optional)
    """
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        agents = agent_registry.list_agents(team=team)
        result = [
            {
                "name": a.name,
                "session_id": a.session_id,
                "teams": a.teams
            }
            for a in agents
        ]

        logger.info(f"Listed {len(result)} agents" + (f" in team '{team}'" if team else ""))
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error listing agents: {e}")
        return f"Error: {e}"


async def remove_agent(
    agent_name: str,
    ctx: Context
) -> str:
    """Remove an agent registration.

    Args:
        agent_name: Name of the agent to remove
    """
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        if agent_registry.remove_agent(agent_name):
            logger.info(f"Removed agent '{agent_name}'")
            return f"Agent '{agent_name}' removed successfully"
        else:
            return f"Agent '{agent_name}' not found"
    except Exception as e:
        logger.error(f"Error removing agent: {e}")
        return f"Error: {e}"


async def manage_teams(
    request: ManageTeamsRequest,
    ctx: Context,
) -> str:
    """Manage teams with a single consolidated tool.

    Consolidates: create_team, list_teams, remove_team, assign_agent_to_team, remove_agent_from_team

    Operations:
    - create: Create a new team (requires team_name)
    - list: List all teams
    - remove: Remove a team (requires team_name)
    - assign_agent: Add an agent to a team (requires team_name and agent_name)
    - remove_agent: Remove an agent from a team (requires team_name and agent_name)

    Args:
        request: The team operation request with operation type and parameters

    Returns:
        JSON with operation results
    """
    agent_registry = ctx.request_context.lifespan_context["agent_registry"]
    profile_manager = ctx.request_context.lifespan_context["profile_manager"]
    service_hook_manager = ctx.request_context.lifespan_context["service_hook_manager"]
    logger = ctx.request_context.lifespan_context["logger"]

    try:
        if request.operation == "create":
            if not request.team_name:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="team_name is required for create operation"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            # Check service hooks before creating team
            hook_result = await service_hook_manager.pre_create_team_hook(
                team_name=request.team_name,
                repo_path=request.repo_path
            )

            # Build response with hook information
            response_data = {}

            # If hook requires prompt, return the hook result for agent to decide
            if hook_result.prompt_required:
                response_data["service_prompt"] = {
                    "message": hook_result.message,
                    "inactive_services": [
                        {
                            "name": s.name,
                            "display_name": s.effective_display_name,
                            "priority": s.priority.value,
                        }
                        for s in hook_result.inactive_services
                    ],
                    "action_required": True,
                }
                logger.info(f"Service hook prompting for team '{request.team_name}': {hook_result.message}")

            # If hook says don't proceed, return error
            if not hook_result.proceed:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error=hook_result.message,
                    data={"proceed": False}
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            # Add info about auto-started services
            if hook_result.auto_started:
                response_data["auto_started_services"] = [
                    s.name for s in hook_result.auto_started
                ]

            team = agent_registry.create_team(
                name=request.team_name,
                description=request.description,
                parent_team=request.parent_team,
            )

            # Create a profile for the team with auto-assigned color
            team_profile = profile_manager.get_or_create_team_profile(team.name)
            profile_manager.save_profiles()

            logger.info(f"Created team '{team.name}' with profile color hue={team_profile.color.hue:.1f}")

            response_data.update({
                "name": team.name,
                "description": team.description,
                "parent_team": team.parent_team,
                "profile_guid": team_profile.guid,
                "color_hue": round(team_profile.color.hue, 1)
            })

            response = ManageTeamsResponse(
                operation=request.operation,
                success=True,
                data=response_data
            )
            return response.model_dump_json(indent=2, exclude_none=True)

        elif request.operation == "list":
            teams = agent_registry.list_teams()
            result = []
            for t in teams:
                team_info = {
                    "name": t.name,
                    "description": t.description,
                    "parent_team": t.parent_team,
                    "member_count": len(agent_registry.list_agents(team=t.name))
                }
                # Include profile info if available
                team_profile = profile_manager.get_team_profile(t.name)
                if team_profile:
                    team_info["profile_guid"] = team_profile.guid
                    team_info["color_hue"] = round(team_profile.color.hue, 1)
                result.append(team_info)

            logger.info(f"Listed {len(result)} teams")
            response = ManageTeamsResponse(
                operation=request.operation,
                success=True,
                data={"teams": result, "count": len(result)}
            )
            return response.model_dump_json(indent=2, exclude_none=True)

        elif request.operation == "remove":
            if not request.team_name:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="team_name is required for remove operation"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            if agent_registry.remove_team(request.team_name):
                # Also remove the team's profile
                profile_removed = profile_manager.remove_team_profile(request.team_name)
                if profile_removed:
                    profile_manager.save_profiles()
                    logger.info(f"Removed team '{request.team_name}' and its profile")
                else:
                    logger.info(f"Removed team '{request.team_name}' (no profile to remove)")

                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=True,
                    data={"team_name": request.team_name, "profile_removed": profile_removed}
                )
                return response.model_dump_json(indent=2, exclude_none=True)
            else:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error=f"Team '{request.team_name}' not found"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

        elif request.operation == "assign_agent":
            if not request.team_name or not request.agent_name:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="team_name and agent_name are required for assign_agent operation"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            if agent_registry.assign_to_team(request.agent_name, request.team_name):
                logger.info(f"Added agent '{request.agent_name}' to team '{request.team_name}'")
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=True,
                    data={"team_name": request.team_name, "agent_name": request.agent_name}
                )
                return response.model_dump_json(indent=2, exclude_none=True)
            else:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="Failed to add agent to team (agent not found or already member)"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

        elif request.operation == "remove_agent":
            if not request.team_name or not request.agent_name:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="team_name and agent_name are required for remove_agent operation"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

            if agent_registry.remove_from_team(request.agent_name, request.team_name):
                logger.info(f"Removed agent '{request.agent_name}' from team '{request.team_name}'")
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=True,
                    data={"team_name": request.team_name, "agent_name": request.agent_name}
                )
                return response.model_dump_json(indent=2, exclude_none=True)
            else:
                response = ManageTeamsResponse(
                    operation=request.operation,
                    success=False,
                    error="Failed to remove agent from team (agent not found or not a member)"
                )
                return response.model_dump_json(indent=2, exclude_none=True)

        else:
            response = ManageTeamsResponse(
                operation=request.operation,
                success=False,
                error=f"Unknown operation: {request.operation}"
            )
            return response.model_dump_json(indent=2, exclude_none=True)

    except Exception as e:
        logger.error(f"Error in manage_teams: {e}")
        response = ManageTeamsResponse(
            operation=request.operation,
            success=False,
            error=str(e)
        )
        return response.model_dump_json(indent=2, exclude_none=True)


def register(mcp):
    """Register agent and team management tools with the FastMCP instance."""
    mcp.tool()(register_agent)
    mcp.tool()(list_agents)
    mcp.tool()(remove_agent)
    mcp.tool()(manage_teams)
