"""SP2 method-semantic `teams` tool — Task 6.

Third SP2 collection tool (after sessions + agents). Replaces the
legacy ``manage_teams`` tool's 5 operations:

    - create         -> POST + CREATE  /teams
    - list           -> GET             /teams
    - remove         -> DELETE          /teams/{name}
    - assign_agent   -> POST + CREATE   /teams/{name}/agents
    - remove_agent   -> DELETE          /teams/{name}/agents

Registered under the provisional name ``teams`` to coexist with the
legacy ``manage_teams`` tool; the cutover (rename to ``teams`` and
unregister the legacy tool) happens at the end of SP2.
"""
from typing import Optional

from mcp.server.fastmcp import Context

from iterm_mcpy.dispatcher import MethodDispatcher


class TeamsDispatcher(MethodDispatcher):
    """Dispatcher for the `teams` collection (SP2 method-semantic)."""

    collection = "teams"

    METHODS = {
        "GET": {
            "aliases": ["list", "get", "read", "query"],
            "params": [],
            "description": "List all teams with their member counts and profile info.",
        },
        "POST": {
            "definers": {
                "CREATE": {
                    "aliases": ["create", "add", "register"],
                    "params": [
                        "target=None | target='agents'",
                        # target=None (create team):
                        "team_name",
                        "description?",
                        "parent_team?",
                        "repo_path?",
                        # target='agents' (add agent to team):
                        "agent_name",
                    ],
                    "description": (
                        "Create a new team (no target) or add an agent to a team "
                        "(target='agents', team_name, agent_name)."
                    ),
                },
            },
        },
        "DELETE": {
            "aliases": ["remove", "delete"],
            "params": [
                "target=None | target='agents'",
                "team_name",
                # target='agents':
                "agent_name?",
            ],
            "description": (
                "Remove a team (no target) or remove an agent from a team "
                "(target='agents')."
            ),
        },
        "HEAD": {"compact_fields": ["name", "description"]},
        "OPTIONS": {"description": "Return this schema."},
    }

    sub_resources = ["agents"]

    # -------------------------------- GET -------------------------------- #

    async def on_get(self, ctx, **params):
        """GET /teams — list teams and their members, profile info, etc."""
        lifespan = ctx.request_context.lifespan_context
        agent_registry = lifespan["agent_registry"]
        profile_manager = lifespan.get("profile_manager")
        logger = lifespan["logger"]

        teams = agent_registry.list_teams()
        result = []
        for t in teams:
            team_info = {
                "name": t.name,
                "description": t.description,
                "parent_team": t.parent_team,
                "member_count": len(agent_registry.list_agents(team=t.name)),
            }
            # Include profile info if available — mirrors legacy manage_teams list.
            if profile_manager is not None:
                team_profile = profile_manager.get_team_profile(t.name)
                if team_profile:
                    team_info["profile_guid"] = team_profile.guid
                    team_info["color_hue"] = round(team_profile.color.hue, 1)
            result.append(team_info)

        logger.info(f"teams GET: listed {len(result)} teams")
        return {"teams": result, "count": len(result)}

    async def on_head(self, ctx, **params):
        """HEAD /teams — compact projection honoring advertised compact_fields.

        GET returns plain dicts, so the default ``project_head`` pass-through
        would return the full payload. Manually project down to the
        ``compact_fields`` advertised in ``METHODS["HEAD"]`` so HEAD is
        genuinely cheaper / smaller than GET.
        """
        full = await self.on_get(ctx, **params)
        fields = self.METHODS["HEAD"]["compact_fields"]
        projected = [
            {k: t.get(k) for k in fields} for t in full.get("teams", [])
        ]
        return {"teams": projected, "count": full.get("count", len(projected))}

    # ------------------------------- POST -------------------------------- #

    async def on_post(self, ctx, definer, **params):
        """Route POST by (definer, target) — create team or assign agent."""
        target = params.get("target")

        if definer == "CREATE" and target is None:
            return await self._create_team(ctx, **params)

        if definer == "CREATE" and target == "agents":
            return await self._assign_agent(ctx, **params)

        raise NotImplementedError(
            f"POST+{definer} on target={target!r} not yet implemented"
        )

    async def _create_team(self, ctx, **params):
        """POST /teams (CREATE) — create a new team.

        Mirrors the legacy manage_teams 'create' operation, including the
        service-hook check (pre_create_team_hook) and the automatic creation
        of an iTerm team profile with an auto-assigned color.
        """
        lifespan = ctx.request_context.lifespan_context
        agent_registry = lifespan["agent_registry"]
        profile_manager = lifespan["profile_manager"]
        service_hook_manager = lifespan["service_hook_manager"]
        logger = lifespan["logger"]

        team_name = params.get("team_name")
        if not team_name:
            raise ValueError("create team requires team_name")

        description = params.get("description", "") or ""
        parent_team = params.get("parent_team")
        repo_path = params.get("repo_path")

        # Check service hooks before creating team.
        hook_result = await service_hook_manager.pre_create_team_hook(
            team_name=team_name,
            repo_path=repo_path,
        )

        response_data: dict = {}

        # If the hook requires an agent prompt, surface service info so the
        # caller can decide (same shape the legacy tool returns).
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
            logger.info(
                f"teams CREATE: service hook prompting for team "
                f"'{team_name}': {hook_result.message}"
            )

        # If the hook blocks proceeding, bail out with an error containing the
        # hook's explanation (keeps legacy behavior — raising makes dispatcher
        # return an err_envelope).
        if not hook_result.proceed:
            raise RuntimeError(hook_result.message or "Service hook blocked team creation")

        # Record auto-started services so the caller can see what happened.
        if hook_result.auto_started:
            response_data["auto_started_services"] = [
                s.name for s in hook_result.auto_started
            ]

        team = agent_registry.create_team(
            name=team_name,
            description=description,
            parent_team=parent_team,
        )

        # Create a profile for the team with auto-assigned color.
        team_profile = profile_manager.get_or_create_team_profile(team.name)
        profile_manager.save_profiles()

        logger.info(
            f"teams CREATE: created team '{team.name}' with profile "
            f"color hue={team_profile.color.hue:.1f}"
        )

        response_data.update({
            "name": team.name,
            "description": team.description,
            "parent_team": team.parent_team,
            "profile_guid": team_profile.guid,
            "color_hue": round(team_profile.color.hue, 1),
        })
        return response_data

    async def _assign_agent(self, ctx, **params):
        """POST /teams/{name}/agents (CREATE) — add an agent to a team."""
        lifespan = ctx.request_context.lifespan_context
        agent_registry = lifespan["agent_registry"]
        logger = lifespan["logger"]

        team_name = params.get("team_name")
        agent_name = params.get("agent_name")
        if not team_name:
            raise ValueError("assign agent requires team_name")
        if not agent_name:
            raise ValueError("assign agent requires agent_name")

        if not agent_registry.assign_to_team(agent_name, team_name):
            raise RuntimeError(
                "Failed to add agent to team (agent not found or already member)"
            )

        logger.info(
            f"teams CREATE agents: added agent '{agent_name}' to team "
            f"'{team_name}'"
        )
        return {"team_name": team_name, "agent_name": agent_name, "assigned": True}

    # ------------------------------- DELETE ------------------------------ #

    async def on_delete(self, ctx, **params):
        """Route DELETE by `target` — remove team or unassign agent."""
        target = params.get("target")

        if target is None:
            return await self._remove_team(ctx, **params)

        if target == "agents":
            return await self._remove_agent_from_team(ctx, **params)

        raise NotImplementedError(
            f"DELETE target={target!r} not yet implemented"
        )

    async def _remove_team(self, ctx, **params):
        """DELETE /teams/{name} — remove a team (and its profile)."""
        lifespan = ctx.request_context.lifespan_context
        agent_registry = lifespan["agent_registry"]
        profile_manager = lifespan.get("profile_manager")
        logger = lifespan["logger"]

        team_name = params.get("team_name")
        if not team_name:
            raise ValueError("remove team requires team_name")

        if not agent_registry.remove_team(team_name):
            raise RuntimeError(f"Team '{team_name}' not found")

        # Also remove the team's profile (mirrors legacy behavior).
        profile_removed = False
        if profile_manager is not None:
            profile_removed = profile_manager.remove_team_profile(team_name)
            if profile_removed:
                profile_manager.save_profiles()
                logger.info(
                    f"teams DELETE: removed team '{team_name}' and its profile"
                )
            else:
                logger.info(
                    f"teams DELETE: removed team '{team_name}' (no profile to remove)"
                )
        else:
            logger.info(
                f"teams DELETE: removed team '{team_name}' (no profile manager)"
            )

        return {"team_name": team_name, "profile_removed": profile_removed}

    async def _remove_agent_from_team(self, ctx, **params):
        """DELETE /teams/{name}/agents — remove an agent from a team."""
        lifespan = ctx.request_context.lifespan_context
        agent_registry = lifespan["agent_registry"]
        logger = lifespan["logger"]

        team_name = params.get("team_name")
        agent_name = params.get("agent_name")
        if not team_name:
            raise ValueError("remove agent from team requires team_name")
        if not agent_name:
            raise ValueError("remove agent from team requires agent_name")

        if not agent_registry.remove_from_team(agent_name, team_name):
            raise RuntimeError(
                "Failed to remove agent from team (agent not found or not a member)"
            )

        logger.info(
            f"teams DELETE agents: removed agent '{agent_name}' from "
            f"team '{team_name}'"
        )
        return {"team_name": team_name, "agent_name": agent_name, "removed": True}


_dispatcher = TeamsDispatcher()


async def teams(
    ctx: Context,
    op: str = "GET",
    definer: Optional[str] = None,
    target: Optional[str] = None,
    team_name: Optional[str] = None,
    agent_name: Optional[str] = None,
    description: Optional[str] = None,
    parent_team: Optional[str] = None,
    repo_path: Optional[str] = None,
) -> str:
    """Team management: list, create, remove, assign agent, remove agent.

    Use op="list" (or op="GET") to list all teams with member counts.
    Use op="create" (or op="POST" + definer="CREATE") + team_name
      (+ description?/parent_team?/repo_path?) to create a team.
    Use op="create" + target="agents" + team_name + agent_name to add an
      agent to an existing team.
    Use op="delete" (or op="DELETE") + team_name to remove a team.
    Use op="delete" + target="agents" + team_name + agent_name to remove
      an agent from a team.
    Use op="HEAD" for a compact team list.
    Use op="OPTIONS" (or "schema") to discover the tool's surface.

    Args:
        op: HTTP method or friendly verb (list/create/remove/add/delete).
        definer: Optional definer (CREATE for POST).
        target: None for team itself, 'agents' for team membership.
        team_name: Name of the team.
        agent_name: Name of the agent (for assign/remove).
        description: Team description (for create).
        parent_team: Parent team name (for create).
        repo_path: Optional repo path for service hook context (for create).

    This is SP2's third method-semantic collection tool. It coexists with
    the legacy ``manage_teams`` tool and will eventually replace it.
    """
    raw_params = {
        "target": target,
        "team_name": team_name,
        "agent_name": agent_name,
        "description": description,
        "parent_team": parent_team,
        "repo_path": repo_path,
    }
    params = {k: v for k, v in raw_params.items() if v is not None}

    return await _dispatcher.dispatch(ctx=ctx, op=op, definer=definer, **params)


def register(mcp):
    """Register the teams dispatcher tool.

    Named ``teams`` to coexist with the legacy ``manage_teams`` tool
    during the SP2 coexistence period. Final cutover (renaming to
    ``teams``) happens at the end of SP2.
    """
    mcp.tool(name="teams")(teams)
