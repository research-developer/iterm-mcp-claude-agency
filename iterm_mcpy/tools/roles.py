"""SP2 method-semantic `roles` tool — Task 11.

Eighth SP2 collection tool (after sessions + agents + teams +
managers + feedback + memory + services). Replaces the legacy
``list_available_roles`` and ``check_tool_permission`` tools.

Roles are a read-only catalog in SP2 — role *assignment* happens through
``sessions`` (target='roles'), which already exposes PATCH/DELETE for
role assignment and GET for listing a session's role. What remains here:

    - list_available_roles   -> GET /roles                  (target=None)
    - check_tool_permission  -> GET /roles/permissions      (target='permissions')

Note: ``check_tool_permission`` is session-centric (it takes a session_id
and checks what that session's assigned role allows). For SP2 it's kept
under ``roles`` to preserve legacy semantics — a future refactor may
move it into ``sessions``.

Registered under the provisional name ``roles`` to coexist with the
legacy per-verb tools; the cutover (rename to ``roles`` and unregister
the legacy tools) happens at the end of SP2.
"""
from typing import Optional, Any

from mcp.server.fastmcp import Context

from iterm_mcpy.dispatcher import MethodDispatcher


class RolesDispatcher(MethodDispatcher):
    """Dispatcher for the `roles` collection (SP2 method-semantic).

    Read-only — roles are a catalog, not a mutable collection. Role
    assignment to sessions happens through sessions (target='roles').
    """

    collection = "roles"

    METHODS = {
        "GET": {
            "aliases": ["list", "get", "read", "check"],
            "params": [
                "target=None | target='permissions'",
                # permissions:
                "session_id?",
                "tool_name?",
            ],
            "description": (
                "List available role definitions (target=None) or check "
                "whether a specific session's role allows a given tool "
                "(target='permissions', requires session_id + tool_name)."
            ),
        },
        "HEAD": {"compact_fields": ["role", "description"]},
        "OPTIONS": {"description": "Return this schema."},
    }

    sub_resources = ["permissions"]

    # -------------------------------- GET -------------------------------- #

    async def on_get(self, ctx, **params):
        """Route GET by `target` — list role defs or check tool permission."""
        target = params.get("target")
        if target == "permissions":
            return await self._check_permission(ctx, **params)
        return await self._list_available(ctx, **params)

    async def on_head(self, ctx, **params):
        """HEAD /roles — compact projection honoring advertised compact_fields.

        GET returns plain dicts, so the default ``project_head`` pass-through
        would return the full payload. Manually project down to the
        ``compact_fields`` advertised in ``METHODS["HEAD"]`` so HEAD is
        genuinely cheaper / smaller than GET.
        """
        full = await self.on_get(ctx, **params)
        fields = self.METHODS["HEAD"]["compact_fields"]
        projected = [
            {k: r.get(k) for k in fields} for r in full.get("roles", [])
        ]
        return {"roles": projected, "count": full.get("count", len(projected))}

    async def _list_available(self, ctx, **params):
        """GET /roles — list available role definitions (the catalog).

        Mirrors the legacy ``list_available_roles`` return shape: iterates
        ``SessionRole`` enum and looks up each role's config in
        ``DEFAULT_ROLE_CONFIGS``, falling back to a generic custom-role
        payload for roles without a default config.
        """
        from core.models import DEFAULT_ROLE_CONFIGS, SessionRole

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

        return {
            "count": len(roles),
            "roles": roles,
        }

    async def _check_permission(self, ctx, **params):
        """GET /roles/permissions — check a tool permission for a session.

        Mirrors the legacy ``check_tool_permission`` return shape:
        {session_id, tool_name, allowed, reason, role, has_role}.
        """
        session_id = params.get("session_id")
        tool_name = params.get("tool_name")
        if not session_id:
            raise ValueError("check permission requires session_id")
        if not tool_name:
            raise ValueError("check permission requires tool_name")

        lifespan = ctx.request_context.lifespan_context
        role_manager = lifespan.get("role_manager")
        if role_manager is None:
            raise RuntimeError("role_manager not available")

        # is_tool_allowed returns (allowed: bool, reason: Optional[str])
        allowed, reason = role_manager.is_tool_allowed(session_id, tool_name)

        assignment = role_manager.get_role(session_id)
        role_info = {
            "role": assignment.role.value if assignment else None,
            "has_role": assignment is not None,
        }

        return {
            "session_id": session_id,
            "tool_name": tool_name,
            "allowed": allowed,
            "reason": reason,
            **role_info,
        }


_dispatcher = RolesDispatcher()


async def roles(
    ctx: Context,
    op: str = "GET",
    definer: Optional[str] = None,
    target: Optional[str] = None,
    session_id: Optional[str] = None,
    tool_name: Optional[str] = None,
) -> dict[str, Any]:
    """Roles catalog: list role definitions, check tool permissions.

    Use op="list" (or op="GET") to list all available role definitions —
      the catalog of roles (devops/builder/debugger/... + custom) with
      their default tool allowlists, restrictions, and capabilities.
    Use op="GET" + target="permissions" + session_id + tool_name to check
      whether a specific session's assigned role allows a given tool
      (aka the legacy check_tool_permission op). Returns the decision
      plus the session's current role (or has_role=false if unassigned).
    Use op="HEAD" (or "peek"/"summary") for a compact list.
    Use op="OPTIONS" (or "schema") to discover the tool's surface.

    Args:
        op: HTTP method or friendly verb.
        definer: Explicit definer (not used — roles is read-only).
        target: Sub-resource: 'permissions' for a tool-permission check.
        session_id: Session id (required for target='permissions').
        tool_name: Tool name (required for target='permissions').

    This is SP2's eighth method-semantic collection tool. It coexists with
    the legacy ``list_available_roles`` and ``check_tool_permission``
    tools and will eventually replace them.

    Note: role *assignment* (assign/remove a role to a session) is handled
    by ``sessions`` (target='roles'), not here — roles is read-only.
    """
    raw_params = {
        "target": target,
        "session_id": session_id,
        "tool_name": tool_name,
    }
    params = {k: v for k, v in raw_params.items() if v is not None}
    return await _dispatcher.dispatch(ctx=ctx, op=op, definer=definer, **params)


def register(mcp):
    """Register the roles dispatcher tool.

    Named ``roles`` to coexist with the legacy ``list_available_roles``
    and ``check_tool_permission`` tools during the SP2 coexistence period.
    Final cutover (renaming to ``roles``) happens at the end of SP2.
    """
    mcp.tool(name="roles")(roles)
