"""SP2 method-semantic `sessions_v2` tool — Tasks 4a + 4b.

This module introduces the first collection tool built on the
MethodDispatcher base class. It currently implements:

    GET     — list/filter sessions (delegates to _list_sessions_core)
              OR read terminal output when target="output"
    HEAD    — compact projection of GET (auto via HEAD_FIELDS)
    OPTIONS — self-describing schema (auto)
    POST + CREATE — create new sessions from a layout
              (delegates to execute_create_sessions)
    POST + SEND   — write to session(s) when target="output"
              (delegates to execute_write_request)

Sub-resources (keys, tags, locks, roles, monitoring, splits, status)
and the remaining POST/PATCH/PUT/DELETE definers are implemented in
subsequent SP2 tasks (4c–4e).

The tool is registered under the provisional name ``sessions_v2`` so it
coexists with the 17 legacy session-related tools. The final cutover
(renaming to ``sessions``) happens at the end of SP2.
"""
from typing import List, Optional

from mcp.server.fastmcp import Context

from core.models import (
    CreateSessionsRequest,
    ReadSessionsRequest,
    ReadTarget,
    SessionMessage,
    SessionTarget,
    WriteToSessionsRequest,
)
from iterm_mcpy.dispatcher import MethodDispatcher
from iterm_mcpy.helpers import (
    execute_create_sessions,
    execute_read_request,
    execute_write_request,
)
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
                "target?",
                "targets?",
                "max_lines?", "strip_ansi?", "parallel?",
            ],
            "description": "List sessions or read output (target='output').",
        },
        "POST": {
            "definers": {
                "CREATE": {
                    "aliases": ["create"],
                    "params": ["layout", "sessions", "register_agents?", "shell?"],
                    "description": "Create new sessions from a layout.",
                },
                "SEND": {
                    "aliases": ["send", "write", "dispatch"],
                    "params": [
                        "target='output'",
                        "messages? | content? + (session_id|agent|name|team)",
                        "parallel?", "skip_duplicates?", "execute?", "use_encoding?",
                    ],
                    "description": "Write text/commands to session(s). target='output' required.",
                },
            },
        },
        "HEAD": {"compact_fields": ["session_id", "name", "agent", "is_processing", "locked"]},
        "OPTIONS": {"description": "Return this schema."},
    }

    sub_resources = ["output", "status", "tags", "locks", "roles", "monitoring", "splits", "keys"]

    async def on_get(self, ctx, **params):
        """Route GET by `target` — list sessions (default) or read output."""
        target = params.get("target")
        if target == "output":
            return await self._get_output(ctx, **params)
        return await self._list_sessions(ctx, **params)

    async def _list_sessions(self, ctx, **params):
        """List sessions with optional filters.

        Params are a superset of _list_sessions_core's signature. Display-only
        params (format, group_by, shortcuts) are irrelevant here because the
        envelope renders the raw SessionInfo list; they're accepted so the tool
        wrapper signature stays consistent and simply ignored.
        """
        core_params = {k: v for k, v in params.items() if k in _GET_CORE_PARAMS}
        response = await _list_sessions_core(ctx, **core_params)
        return response.sessions

    async def _get_output(self, ctx, **params):
        """GET /sessions/output — read terminal output via execute_read_request."""
        # Build a ReadSessionsRequest. Accept either an explicit `targets=[...]`
        # list (matching old read_sessions) or shortcut params identifying a
        # single session.
        targets = params.get("targets")
        if not targets:
            target_spec: dict = {}
            for key in ("session_id", "agent", "name", "team"):
                val = params.get(key)
                if val is not None:
                    target_spec[key] = val
            if not target_spec:
                raise ValueError(
                    "read output requires at least one of: "
                    "session_id, agent, name, team, or targets"
                )
            # Allow per-shortcut max_lines override.
            if params.get("max_lines") is not None:
                target_spec["max_lines"] = params["max_lines"]
            targets = [target_spec]

        coerced_targets = [
            ReadTarget(**t) if isinstance(t, dict) else t for t in targets
        ]

        request_kwargs: dict = {"targets": coerced_targets}
        if params.get("parallel") is not None:
            request_kwargs["parallel"] = params["parallel"]
        if params.get("filter_pattern") is not None:
            request_kwargs["filter_pattern"] = params["filter_pattern"]

        request = ReadSessionsRequest(**request_kwargs)

        terminal = ctx.request_context.lifespan_context["terminal"]
        agent_registry = ctx.request_context.lifespan_context["agent_registry"]
        logger = ctx.request_context.lifespan_context["logger"]

        return await execute_read_request(request, terminal, agent_registry, logger)

    async def on_post(self, ctx, definer, **params):
        """Route POST by `(definer, target)` — create sessions or write output."""
        target = params.get("target")

        if definer == "CREATE" and not target:
            return await self._create_sessions(ctx, **params)

        if definer == "SEND" and target == "output":
            return await self._write_output(ctx, **params)

        raise NotImplementedError(
            f"POST+{definer} on target={target!r} not yet implemented"
        )

    async def _create_sessions(self, ctx, **params):
        """POST + CREATE — delegate to execute_create_sessions."""
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

    async def _write_output(self, ctx, **params):
        """POST + SEND on target='output' — delegate to execute_write_request.

        Accepts either a structured `messages=[...]` list (matching the legacy
        write_to_sessions schema) or shortcut params: `content` plus a single
        target identifier (session_id/agent/name/team).
        """
        messages = params.get("messages")
        if not messages:
            content = params.get("content")
            if not content:
                raise ValueError(
                    "write output requires either messages=[...] or content=..."
                )
            target_spec: dict = {}
            for key in ("session_id", "agent", "name", "team"):
                val = params.get(key)
                if val is not None:
                    target_spec[key] = val
            if not target_spec:
                raise ValueError(
                    "write output requires at least one of: "
                    "session_id, agent, name, team (or explicit messages)"
                )
            message: dict = {
                "content": content,
                "targets": [target_spec],
            }
            if params.get("execute") is not None:
                message["execute"] = params["execute"]
            if params.get("use_encoding") is not None:
                message["use_encoding"] = params["use_encoding"]
            messages = [message]

        # Coerce dict messages into Pydantic models. Pydantic also accepts the
        # raw dicts directly via model_validate, but explicit coercion keeps
        # the request shape obvious to readers.
        coerced_messages = []
        for m in messages:
            if isinstance(m, dict):
                # Coerce nested target dicts into SessionTarget models too.
                m_targets = m.get("targets") or []
                coerced_targets = [
                    SessionTarget(**t) if isinstance(t, dict) else t for t in m_targets
                ]
                m_kwargs = {**m, "targets": coerced_targets}
                coerced_messages.append(SessionMessage(**m_kwargs))
            else:
                coerced_messages.append(m)

        request_kwargs: dict = {"messages": coerced_messages}
        if params.get("parallel") is not None:
            request_kwargs["parallel"] = params["parallel"]
        if params.get("skip_duplicates") is not None:
            request_kwargs["skip_duplicates"] = params["skip_duplicates"]

        request = WriteToSessionsRequest(**request_kwargs)

        terminal = ctx.request_context.lifespan_context["terminal"]
        agent_registry = ctx.request_context.lifespan_context["agent_registry"]
        logger = ctx.request_context.lifespan_context["logger"]
        lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
        notification_manager = ctx.request_context.lifespan_context.get(
            "notification_manager"
        )

        return await execute_write_request(
            request,
            terminal,
            agent_registry,
            logger,
            lock_manager=lock_manager,
            notification_manager=notification_manager,
        )


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
    # NEW (4b): output sub-resource (read + write).
    target: Optional[str] = None,
    targets: Optional[List[dict]] = None,
    max_lines: Optional[int] = None,
    strip_ansi: bool = True,
    parallel: Optional[bool] = None,
    filter_pattern: Optional[str] = None,
    messages: Optional[List[dict]] = None,
    content: Optional[str] = None,
    name: Optional[str] = None,
    skip_duplicates: Optional[bool] = None,
    execute: Optional[bool] = None,
    use_encoding: Optional[bool] = None,
) -> str:
    """Session operations: list, read output, write output, create, HEAD, OPTIONS.

    Use op="list" or op="GET" to list sessions with filters.
    Use op="GET" + target="output" to read terminal output.
    Use op="send" (or op="POST" + definer="SEND") + target="output" to write.
    Use op="HEAD" (or "peek"/"summary") for a compact list.
    Use op="OPTIONS" (or "schema"/"discover") to discover the tool's surface.
    Use op="create" (or op="POST") to create new sessions from a layout.

    This is SP2's first method-semantic collection tool. It coexists with the
    legacy per-verb session tools (list_sessions, create_sessions,
    read_sessions, write_to_sessions, etc.) and will eventually replace them.
    """
    # Build a params dict of non-None values so handlers don't have to juggle
    # defaults. Use `is not None` so booleans (False) and ints (0) survive.
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
        "target": target,
        "targets": targets,
        "max_lines": max_lines,
        "strip_ansi": strip_ansi,
        "parallel": parallel,
        "filter_pattern": filter_pattern,
        "messages": messages,
        "content": content,
        "name": name,
        "skip_duplicates": skip_duplicates,
        "execute": execute,
        "use_encoding": use_encoding,
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
