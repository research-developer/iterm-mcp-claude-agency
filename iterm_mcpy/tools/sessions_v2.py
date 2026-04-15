"""SP2 method-semantic `sessions_v2` tool — Task 4a (core read/create).

This module introduces the first collection tool built on the
MethodDispatcher base class. It currently implements:

    GET     — list/filter sessions (delegates to _list_sessions_core)
    HEAD    — compact projection of GET (auto via HEAD_FIELDS)
    OPTIONS — self-describing schema (auto)
    POST + CREATE — create new sessions from a layout
              (delegates to execute_create_sessions)

Sub-resources (output, keys, tags, locks, roles, monitoring, splits,
status) and the remaining POST/PATCH/PUT/DELETE definers are implemented
in subsequent SP2 tasks (4b–4e).

The tool is registered under the provisional name ``sessions_v2`` so it
coexists with the 17 legacy session-related tools. The final cutover
(renaming to ``sessions``) happens at the end of SP2.
"""
from typing import List, Optional

from mcp.server.fastmcp import Context

from core.models import CreateSessionsRequest
from iterm_mcpy.dispatcher import MethodDispatcher
from iterm_mcpy.helpers import execute_create_sessions
from iterm_mcpy.tools.sessions import _list_sessions_core


# Parameters that _list_sessions_core accepts. Anything outside this set is
# dropped from the GET handler's kwargs to keep the helper signature tight.
_GET_CORE_PARAMS = {
    "agents_only",
    "tag",
    "tags",
    "match",
    "locked",
    "locked_by",
    "session_id",
    "agent",
    "team",
    "role",
    "include_message",
}


class SessionsDispatcher(MethodDispatcher):
    """Dispatcher for the `sessions` collection (SP2 method-semantic)."""

    collection = "sessions"

    METHODS = {
        "GET": {
            "aliases": ["list", "get", "read", "query"],
            "params": [
                "session_id?", "agent?", "team?", "role?",
                "tag?", "tags?", "match?", "locked?", "locked_by?",
                "format?", "group_by?", "include_message?", "shortcuts?",
                "agents_only?",
            ],
            "description": "List or filter sessions.",
        },
        "POST": {
            "definers": {
                "CREATE": {
                    "aliases": ["create"],
                    "params": ["layout", "sessions", "register_agents?", "shell?"],
                    "description": "Create new sessions from a layout.",
                },
            },
        },
        "HEAD": {"compact_fields": ["session_id", "name", "agent", "status"]},
        "OPTIONS": {"description": "Return this schema."},
    }

    sub_resources = ["output", "status", "tags", "locks", "roles", "monitoring", "splits", "keys"]

    async def on_get(self, ctx, **params):
        """List sessions with optional filters.

        Params are a superset of _list_sessions_core's signature. Display-only
        params (format, group_by, shortcuts) are irrelevant here because the
        envelope renders the raw SessionInfo list; they're accepted so the tool
        wrapper signature stays consistent and simply ignored.
        """
        core_params = {k: v for k, v in params.items() if k in _GET_CORE_PARAMS}
        response = await _list_sessions_core(ctx, **core_params)
        return response.sessions

    async def on_post(self, ctx, definer, **params):
        """Handle POST+CREATE — delegate to execute_create_sessions.

        Other POST definers (SEND, INVOKE, TRIGGER, UPLOAD) come in later
        tasks and currently raise NotImplementedError (handled by the base
        dispatcher into an err_envelope).
        """
        if definer != "CREATE":
            raise NotImplementedError(
                f"POST definer {definer} not yet implemented on sessions"
            )

        terminal = ctx.request_context.lifespan_context["terminal"]
        layout_manager = ctx.request_context.lifespan_context["layout_manager"]
        agent_registry = ctx.request_context.lifespan_context["agent_registry"]
        profile_manager = ctx.request_context.lifespan_context["profile_manager"]
        logger = ctx.request_context.lifespan_context["logger"]

        # Build the CreateSessionsRequest. `register_agents` and `shell` are
        # accepted by the tool signature for forward-compat but are not part
        # of the existing CreateSessionsRequest — they're ignored here and
        # will be wired up in a later task if needed.
        create_request = CreateSessionsRequest.model_validate({
            "layout": params["layout"],
            "sessions": params["sessions"],
        })
        result = await execute_create_sessions(
            create_request,
            terminal,
            layout_manager,
            agent_registry,
            logger,
            profile_manager=profile_manager,
        )
        logger.info(f"sessions_v2 CREATE: created {len(result.sessions)} sessions")
        return result


_dispatcher = SessionsDispatcher()


async def sessions_v2(
    ctx: Context,
    op: str = "GET",
    definer: Optional[str] = None,
    # Identity/filter params for GET.
    session_id: Optional[str] = None,
    agent: Optional[str] = None,
    team: Optional[str] = None,
    role: Optional[str] = None,
    tag: Optional[str] = None,
    tags: Optional[List[str]] = None,
    match: str = "any",
    locked: Optional[bool] = None,
    locked_by: Optional[str] = None,
    format: str = "grouped",
    group_by: str = "directory",
    include_message: bool = True,
    shortcuts: bool = True,
    agents_only: bool = False,
    # POST+CREATE params.
    layout: Optional[str] = None,
    sessions: Optional[List[dict]] = None,
    register_agents: bool = True,
    shell: Optional[str] = None,
) -> str:
    """Session operations: list, get, create, HEAD, OPTIONS.

    Use op="list" or op="GET" to list sessions with filters.
    Use op="HEAD" (or "peek"/"summary") for a compact list
    (session_id, name, agent only).
    Use op="OPTIONS" (or "schema"/"discover") to discover the tool's surface.
    Use op="create" (or op="POST") to create new sessions from a layout.

    This is SP2's first method-semantic collection tool. It coexists with the
    legacy per-verb session tools (list_sessions, create_sessions, etc.) and
    will eventually replace them.
    """
    # Build a params dict of non-None values so handlers don't have to juggle
    # defaults. `match` and the bool defaults are always included since they
    # have sensible values.
    raw_params = {
        "session_id": session_id,
        "agent": agent,
        "team": team,
        "role": role,
        "tag": tag,
        "tags": tags,
        "match": match,
        "locked": locked,
        "locked_by": locked_by,
        "format": format,
        "group_by": group_by,
        "include_message": include_message,
        "shortcuts": shortcuts,
        "agents_only": agents_only,
        "layout": layout,
        "sessions": sessions,
        "register_agents": register_agents,
        "shell": shell,
    }
    params = {k: v for k, v in raw_params.items() if v is not None}

    return await _dispatcher.dispatch(ctx=ctx, op=op, definer=definer, **params)


def register(mcp):
    """Register the sessions_v2 dispatcher tool.

    Named `sessions_v2` to coexist with the legacy session tools during
    the SP2 coexistence period. At final cutover this gets renamed to
    `sessions` (with the old list_sessions et al. unregistered).
    """
    mcp.tool(name="sessions_v2")(sessions_v2)
