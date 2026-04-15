"""SP2 method-semantic `agents_v2` tool — Task 5.

Second SP2 collection tool (after sessions_v2). Replaces 8 legacy tools:
    - register_agent          -> POST + CREATE  /agents
    - list_agents             -> GET             /agents
    - remove_agent            -> DELETE          /agents/{name}
    - get_agent_status_summary-> GET             /agents/status (sub-resource)
    - manage_agent_hooks      -> GET/PATCH/POST  /agents/{name}/hooks (sub-ops via hooks_op)
    - get_notifications       -> GET             /agents/{name}/notifications
    - notify                  -> POST + SEND     /agents/{name}/notifications
    - list_my_locks           -> GET             /agents/{name}/locks

Registered under the provisional name ``agents_v2`` to coexist with the
legacy per-verb agent tools; the cutover (rename to ``agents`` and
unregister the legacy tools) happens at the end of SP2.
"""
import json
from datetime import datetime
from typing import List, Optional

from mcp.server.fastmcp import Context

from core.models import (
    AgentNotification,
    GetNotificationsRequest,
    GetNotificationsResponse,
    ManageAgentHooksRequest,
    RegisterAgentRequest,
)
from iterm_mcpy.dispatcher import MethodDispatcher
from iterm_mcpy.tools.agent_hooks import manage_agent_hooks as _manage_agent_hooks_legacy


class AgentsDispatcher(MethodDispatcher):
    """Dispatcher for the `agents` collection (SP2 method-semantic)."""

    collection = "agents"

    METHODS = {
        "GET": {
            "aliases": ["list", "get", "read", "query"],
            "params": [
                "team?",
                "target='status' | 'notifications' | 'hooks' | 'locks'",
                "agent? (required for notifications/hooks/locks)",
                "level?", "limit?", "since?",        # for notifications
                "hooks_op?", "repo_path?",            # for hooks
            ],
            "description": (
                "List agents (no target), fetch compact status summary "
                "(target='status'), retrieve notifications, hooks config, "
                "or locks held by a specific agent."
            ),
        },
        "POST": {
            "definers": {
                "CREATE": {
                    "aliases": ["create", "register", "add"],
                    "params": [
                        "agent_name", "session_id",
                        "team? | teams?=[...]",
                        "metadata?",
                    ],
                    "description": "Register a new agent for a session.",
                },
                "SEND": {
                    "aliases": ["notify", "send", "dispatch"],
                    "params": [
                        "target='notifications'",
                        "agent", "level", "summary",
                        "context?", "action_hint?",
                    ],
                    "description": (
                        "Send a notification for an agent "
                        "(target='notifications')."
                    ),
                },
                "TRIGGER": {
                    "aliases": ["trigger"],
                    "params": [
                        "target='hooks'", "hooks_op",
                        "session_id?", "agent?", "new_path?",
                        "variable_name?", "variable_value?",
                    ],
                    "description": (
                        "Trigger a hook sub-operation "
                        "(target='hooks' + hooks_op=...)."
                    ),
                },
            },
        },
        "PATCH": {
            "definers": {
                "MODIFY": {
                    "aliases": ["update", "patch", "modify"],
                    "params": [
                        "target='hooks'",
                        "hooks_op?",        # default update_config
                        "enabled?", "auto_team_assignment?",
                        "fallback_team_from_repo?", "pass_session_id_default?",
                        "session_id?", "variable_name?", "variable_value?",
                    ],
                    "description": (
                        "Update hook configuration (global or per-session) — "
                        "target='hooks', hooks_op='update_config' (default) "
                        "or 'set_variable'."
                    ),
                },
            },
        },
        "DELETE": {
            "aliases": ["remove", "delete"],
            "params": [
                "agent_name",
            ],
            "description": "Remove an agent registration.",
        },
        "HEAD": {"compact_fields": ["name", "session_id", "teams"]},
        "OPTIONS": {"description": "Return this schema."},
    }

    sub_resources = ["status", "notifications", "hooks", "locks"]

    # -------------------------------- GET -------------------------------- #

    async def on_get(self, ctx, **params):
        """Route GET by `target` — list, status, notifications, hooks, locks."""
        target = params.get("target")
        if target == "status":
            return await self._get_status_summary(ctx, **params)
        if target == "notifications":
            return await self._get_notifications(ctx, **params)
        if target == "hooks":
            return await self._get_hooks(ctx, **params)
        if target == "locks":
            return await self._get_locks(ctx, **params)
        return await self._list_agents(ctx, **params)

    async def _list_agents(self, ctx, **params):
        """GET /agents — list registered agents, optionally filtered by team.

        Returns List[Agent] directly so the envelope serializes via Agent's
        HEAD_FIELDS for HEAD, and the full model for GET.
        """
        agent_registry = ctx.request_context.lifespan_context["agent_registry"]
        logger = ctx.request_context.lifespan_context["logger"]

        team = params.get("team")
        agents = agent_registry.list_agents(team=team)
        logger.info(
            f"agents_v2 GET: listed {len(agents)} agents"
            + (f" in team '{team}'" if team else "")
        )
        return agents

    async def _get_status_summary(self, ctx, **params):
        """GET /agents/status — compact status summary of all agents.

        Returns the formatted string produced by the legacy
        get_agent_status_summary, preserved verbatim so downstream tooling
        that parses the formatted output keeps working.
        """
        lifespan = ctx.request_context.lifespan_context
        notification_manager = lifespan["notification_manager"]
        agent_registry = lifespan["agent_registry"]
        lock_manager = lifespan.get("tag_lock_manager")
        logger = lifespan["logger"]

        latest = await notification_manager.get_latest_per_agent()

        # Fill in agents with no notifications.
        all_agents = agent_registry.list_agents()
        for agent in all_agents:
            if agent.name not in latest:
                latest[agent.name] = AgentNotification(
                    agent=agent.name,
                    timestamp=datetime.now(),
                    level="info",
                    summary="No activity recorded",
                )

        notifications = list(latest.values())
        if not notifications:
            return "━━━ No notifications ━━━"

        lines = ["━━━ Agent Status ━━━"]
        for n in notifications:
            icon = notification_manager.STATUS_ICONS.get(n.level, "?")
            lock_info = ""
            if lock_manager:
                locks = lock_manager.get_locks_by_agent(n.agent)
                lock_count = len(locks)
                if lock_count == 0:
                    lock_info = "[0 locks]"
                elif lock_count == 1:
                    lock_info = f"[1 lock: {locks[0][:12]}]"
                else:
                    lock_info = f"[{lock_count} locks]"
            agent_name = n.agent[:12].ljust(12)
            summary = (
                n.summary[:20].ljust(20)
                if len(n.summary) > 20
                else n.summary.ljust(20)
            )
            lines.append(f"{agent_name} {icon} {summary} {lock_info}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        formatted = "\n".join(lines)

        logger.info(
            f"agents_v2 GET status: summary for {len(notifications)} agents"
        )
        return formatted

    async def _get_notifications(self, ctx, **params):
        """GET /agents/{name}/notifications — recent notifications.

        Delegates to notification_manager.get with filter params.
        """
        notification_manager = ctx.request_context.lifespan_context["notification_manager"]
        logger = ctx.request_context.lifespan_context["logger"]

        # Build/validate the request; agent is optional (filters to that agent
        # if provided, but you can also list notifications across all agents).
        req_kwargs: dict = {}
        if params.get("agent") is not None:
            req_kwargs["agent"] = params["agent"]
        if params.get("level") is not None:
            req_kwargs["level"] = params["level"]
        if params.get("limit") is not None:
            req_kwargs["limit"] = params["limit"]
        if params.get("since") is not None:
            req_kwargs["since"] = params["since"]

        req = GetNotificationsRequest.model_validate(req_kwargs)
        notifications = await notification_manager.get(
            limit=req.limit,
            level=req.level,
            agent=req.agent,
            since=req.since,
        )
        response = GetNotificationsResponse(
            notifications=notifications,
            total_count=len(notifications),
            has_more=len(notifications) == req.limit,
        )
        logger.info(
            f"agents_v2 GET notifications: returned {len(notifications)} "
            f"(agent={req.agent or 'any'})"
        )
        return response

    async def _get_hooks(self, ctx, **params):
        """GET /agents/{name}/hooks — read hook config.

        Delegates to the legacy manage_agent_hooks with operation=get_config
        (default) or the caller-specified hooks_op. Result is parsed back
        from JSON since the legacy tool returns a JSON string.
        """
        hooks_op = params.get("hooks_op") or "get_config"
        return await self._run_manage_agent_hooks(ctx, op=hooks_op, **params)

    async def _get_locks(self, ctx, **params):
        """GET /agents/{name}/locks — list sessions locked by an agent.

        Reuses the same logic as the legacy list_my_locks tool: fetches
        session IDs from the lock manager, enriches with session names.
        """
        lifespan = ctx.request_context.lifespan_context
        lock_manager = lifespan.get("tag_lock_manager")
        terminal = lifespan.get("terminal")
        logger = lifespan["logger"]

        agent = params.get("agent")
        if not agent:
            raise ValueError("get locks requires agent=<name>")
        if not lock_manager:
            raise RuntimeError("tag_lock_manager not available")

        locked_session_ids = lock_manager.get_locks_by_agent(agent)
        locks = []
        for session_id in locked_session_ids:
            lock_info = lock_manager.get_lock_info(session_id)
            session_name = None
            if terminal is not None:
                try:
                    session = await terminal.get_session_by_id(session_id)
                    if session:
                        session_name = session.name
                except Exception as e:
                    logger.debug(
                        f"Could not get session name for {session_id}: {e}"
                    )
            locks.append({
                "session_id": session_id,
                "session_name": session_name,
                "locked_at": (
                    lock_info.locked_at.isoformat() if lock_info else None
                ),
                "pending_requests": (
                    sorted(lock_info.pending_requests) if lock_info else []
                ),
            })

        logger.info(f"agents_v2 GET locks: {len(locks)} for agent '{agent}'")
        return {"agent": agent, "lock_count": len(locks), "locks": locks}

    # ------------------------------- POST -------------------------------- #

    async def on_post(self, ctx, definer, **params):
        """Route POST by (definer, target) — register, notify, trigger hooks."""
        target = params.get("target")

        if definer == "CREATE" and not target:
            return await self._register_agent(ctx, **params)

        if definer == "SEND" and target == "notifications":
            return await self._notify(ctx, **params)

        if definer == "TRIGGER" and target == "hooks":
            return await self._trigger_hook(ctx, **params)

        raise NotImplementedError(
            f"POST+{definer} on target={target!r} not yet implemented"
        )

    async def _register_agent(self, ctx, **params):
        """POST /agents (CREATE) — register an agent for a session."""
        lifespan = ctx.request_context.lifespan_context
        terminal = lifespan["terminal"]
        agent_registry = lifespan["agent_registry"]
        logger = lifespan["logger"]

        agent_name = params.get("agent_name") or params.get("name") or params.get("agent")
        session_id = params.get("session_id")
        if not agent_name:
            raise ValueError("register agent requires agent_name")
        if not session_id:
            raise ValueError("register agent requires session_id")

        # `team` is a convenience single-team input; `teams=[...]` is the
        # authoritative list. Either is accepted.
        teams = params.get("teams")
        if teams is None and params.get("team") is not None:
            teams = [params["team"]]
        metadata = params.get("metadata")

        # Build and validate via RegisterAgentRequest so any shape checks
        # (name length, etc.) stay centralized.
        req = RegisterAgentRequest.model_validate({
            "name": agent_name,
            "session_id": session_id,
            "teams": teams or [],
            "metadata": metadata or {},
        })

        session = await terminal.get_session_by_id(req.session_id)
        if session is None:
            raise ValueError(
                f"No matching session found for session_id={req.session_id}"
            )

        agent = agent_registry.register_agent(
            name=req.name,
            session_id=session.id,
            teams=req.teams,
            metadata=req.metadata,
        )
        logger.info(
            f"agents_v2 CREATE: registered agent '{agent.name}' "
            f"for session {session.name}"
        )
        return {
            "agent": agent.name,
            "session_id": agent.session_id,
            "session_name": session.name,
            "teams": agent.teams,
            "metadata": agent.metadata,
        }

    async def _notify(self, ctx, **params):
        """POST /agents/{name}/notifications (SEND) — record a notification."""
        notification_manager = ctx.request_context.lifespan_context["notification_manager"]
        logger = ctx.request_context.lifespan_context["logger"]

        agent = params.get("agent")
        level = params.get("level")
        summary = params.get("summary")
        if not agent:
            raise ValueError("notify requires agent=<name>")
        if not level:
            raise ValueError("notify requires level")
        if not summary:
            raise ValueError("notify requires summary")

        await notification_manager.add_simple(
            agent=agent,
            level=level,
            summary=summary,
            context=params.get("context"),
            action_hint=params.get("action_hint"),
        )
        logger.info(
            f"agents_v2 notify: agent={agent} level={level} summary={summary!r}"
        )
        return {"agent": agent, "level": level, "added": True}

    async def _trigger_hook(self, ctx, **params):
        """POST /agents/{name}/hooks (TRIGGER) — execute a hook sub-operation.

        For fire-and-forget-style hooks (trigger_path_change is the main one
        today). Caller passes `hooks_op` to pick which sub-operation to run.
        """
        hooks_op = params.get("hooks_op")
        if not hooks_op:
            raise ValueError(
                "trigger hooks requires hooks_op=<operation>"
            )
        return await self._run_manage_agent_hooks(ctx, op=hooks_op, **params)

    # ------------------------------- PATCH ------------------------------- #

    async def on_patch(self, ctx, definer, **params):
        """Route PATCH by `target` — hooks is the only target today."""
        target = params.get("target")
        if target == "hooks":
            # Default PATCH operation is update_config; callers can override
            # (e.g. hooks_op='set_variable' to mutate a session variable).
            hooks_op = params.get("hooks_op") or "update_config"
            return await self._run_manage_agent_hooks(ctx, op=hooks_op, **params)

        raise NotImplementedError(
            f"PATCH target={target!r} not yet implemented"
        )

    # ------------------------------- DELETE ------------------------------ #

    async def on_delete(self, ctx, **params):
        """DELETE /agents/{name} — remove an agent registration."""
        agent_registry = ctx.request_context.lifespan_context["agent_registry"]
        logger = ctx.request_context.lifespan_context["logger"]

        agent_name = (
            params.get("agent_name")
            or params.get("name")
            or params.get("agent")
        )
        if not agent_name:
            raise ValueError("delete agent requires agent_name")

        removed = agent_registry.remove_agent(agent_name)
        logger.info(
            f"agents_v2 DELETE: removed agent '{agent_name}'"
            if removed
            else f"agents_v2 DELETE: agent '{agent_name}' not found"
        )
        return {"agent": agent_name, "removed": bool(removed)}

    # -------------------- shared helper: manage_agent_hooks --------------- #

    async def _run_manage_agent_hooks(self, ctx, *, op: str, **params):
        """Thin wrapper around the legacy manage_agent_hooks tool.

        Builds a ManageAgentHooksRequest, delegates to the legacy tool, and
        re-parses its JSON response into a native dict. This preserves all
        per-op validation and error handling without duplicating the 200+
        line dispatch inside manage_agent_hooks.

        The v2 ``agent`` param is mapped to the legacy ``agent_name`` field.
        """
        req_kwargs: dict = {"operation": op}

        # Per-op inputs — only forward the ones the legacy request model knows.
        fields = (
            "enabled", "auto_team_assignment", "fallback_team_from_repo",
            "pass_session_id_default", "repo_path", "session_id",
            "new_path", "variable_name", "variable_value",
        )
        for key in fields:
            if params.get(key) is not None:
                req_kwargs[key] = params[key]
        # v2 exposes the agent as "agent"; the legacy model calls it "agent_name".
        if params.get("agent") is not None:
            req_kwargs.setdefault("agent_name", params["agent"])

        request = ManageAgentHooksRequest.model_validate(req_kwargs)
        result_json = await _manage_agent_hooks_legacy(request, ctx)
        # manage_agent_hooks always returns a JSON string of ManageAgentHooksResponse.
        return json.loads(result_json)


_dispatcher = AgentsDispatcher()


async def agents_v2(
    ctx: Context,
    op: str = "GET",
    definer: Optional[str] = None,
    # Routing.
    target: Optional[str] = None,
    # Core agent identity / filters.
    agent: Optional[str] = None,          # name for notifications/hooks/locks
    agent_name: Optional[str] = None,     # alias for register/remove
    session_id: Optional[str] = None,
    team: Optional[str] = None,
    teams: Optional[List[str]] = None,
    metadata: Optional[dict] = None,
    # Notifications.
    level: Optional[str] = None,
    summary: Optional[str] = None,
    context: Optional[str] = None,
    action_hint: Optional[str] = None,
    limit: Optional[int] = None,
    since: Optional[str] = None,
    # Hooks.
    hooks_op: Optional[str] = None,
    enabled: Optional[bool] = None,
    auto_team_assignment: Optional[bool] = None,
    fallback_team_from_repo: Optional[bool] = None,
    pass_session_id_default: Optional[bool] = None,
    repo_path: Optional[str] = None,
    new_path: Optional[str] = None,
    variable_name: Optional[str] = None,
    variable_value: Optional[str] = None,
) -> str:
    """Agent operations: list, register, remove, notify, status, notifications,
    hooks, locks, HEAD, OPTIONS.

    Use op="list" (or op="GET") to list registered agents (optionally team-filtered).
    Use op="GET" + target="status" for a compact multi-agent status summary.
    Use op="GET" + target="notifications" (+ agent?) to fetch notifications.
    Use op="GET" + target="hooks" (+ hooks_op?) to read hook config.
    Use op="GET" + target="locks" + agent=... to list sessions locked by that agent.
    Use op="register" (or op="POST" + definer="CREATE") + agent_name + session_id
      (+ team?/teams?/metadata?) to register an agent.
    Use op="notify" (or op="POST" + definer="SEND") + target="notifications"
      + agent + level + summary (+ context?/action_hint?) to add a notification.
    Use op="trigger" (or op="POST" + definer="TRIGGER") + target="hooks"
      + hooks_op=... to run a hook sub-operation (e.g. trigger_path_change).
    Use op="update" (or op="PATCH") + target="hooks" (+ hooks_op?) to update
      hook config (default op is update_config).
    Use op="delete" (or op="DELETE") + agent_name to remove an agent.
    Use op="HEAD" (or "peek"/"summary") for a compact list.
    Use op="OPTIONS" (or "schema"/"discover") to discover the tool's surface.

    This is SP2's second method-semantic collection tool. It coexists with
    the legacy per-verb agent tools (register_agent, list_agents,
    remove_agent, notify, get_notifications, get_agent_status_summary,
    manage_agent_hooks, list_my_locks) and will eventually replace them.
    """
    raw_params = {
        "target": target,
        "agent": agent,
        "agent_name": agent_name,
        "session_id": session_id,
        "team": team,
        "teams": teams,
        "metadata": metadata,
        "level": level,
        "summary": summary,
        "context": context,
        "action_hint": action_hint,
        "limit": limit,
        "since": since,
        "hooks_op": hooks_op,
        "enabled": enabled,
        "auto_team_assignment": auto_team_assignment,
        "fallback_team_from_repo": fallback_team_from_repo,
        "pass_session_id_default": pass_session_id_default,
        "repo_path": repo_path,
        "new_path": new_path,
        "variable_name": variable_name,
        "variable_value": variable_value,
    }
    params = {k: v for k, v in raw_params.items() if v is not None}

    return await _dispatcher.dispatch(ctx=ctx, op=op, definer=definer, **params)


def register(mcp):
    """Register the agents_v2 dispatcher tool.

    Named ``agents_v2`` to coexist with the 8 legacy agent-related tools
    during the SP2 coexistence period. Final cutover (renaming to
    ``agents``) happens at the end of SP2.
    """
    mcp.tool(name="agents_v2")(agents_v2)
