"""SP2 method-semantic `sessions_v2` tool — Tasks 4a + 4b + 4c + 4d.

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
              OR send control char / special key when target="keys"
              (unifies old send_control_character / send_special_key)
    PATCH   — update sub-resources: tags (MODIFY replaces, APPEND adds),
              roles (assign), locks (acquire / request access), or the
              session itself (target='active' + focus=true). Replaces
              set_session_tags, assign_session_role, manage_session_lock
              (lock / request_access), and set_active_session.
    DELETE  — remove sub-resources: roles (removes assignment) or locks
              (releases the lock). Replaces remove_session_role and
              manage_session_lock (unlock).

Remaining sub-resources (monitoring, splits, status) and the remaining
POST/PUT definers are implemented in subsequent SP2 tasks (4e).

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
    resolve_session,
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
                        "target='output' | target='keys'",
                        # output target:
                        "messages? | content? + (session_id|agent|name|team)",
                        "parallel?", "skip_duplicates?", "execute?", "use_encoding?",
                        # keys target:
                        "control_char? | key? + (session_id|agent|name|team)",
                    ],
                    "description": (
                        "Write to session(s). target='output' -> text/commands; "
                        "target='keys' -> control char or named special key."
                    ),
                },
            },
        },
        "PATCH": {
            "definers": {
                "MODIFY": {
                    "aliases": ["update", "patch", "assign"],
                    "params": [
                        "target='tags' | 'roles' | 'locks' | 'active' (default)",
                        "session_id",
                        # tags:
                        "tags?=[...]",
                        # roles:
                        "role?", "assigned_by?",
                        # locks:
                        "agent?", "action?='lock'|'request_access'",
                        # active:
                        "focus?=true",
                    ],
                    "description": "Update session fields or sub-resources.",
                },
                "APPEND": {
                    "aliases": ["append"],
                    "params": [
                        "target='tags'",
                        "session_id",
                        "tags=[...]",
                    ],
                    "description": "Append to session tags (vs MODIFY which replaces).",
                },
            },
        },
        "DELETE": {
            "aliases": ["remove", "unlock"],
            "params": [
                "target='roles' | 'locks'",
                "session_id",
                # locks:
                "agent?",
                # roles:
                "removed_by?",
            ],
            "description": "Remove role assignment or release a session lock.",
        },
        "HEAD": {"compact_fields": ["session_id", "name", "agent", "is_processing", "locked"]},
        "OPTIONS": {"description": "Return this schema."},
    }

    sub_resources = ["output", "status", "tags", "locks", "roles", "monitoring", "splits", "keys", "active"]

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
        """Route POST by `(definer, target)` — create sessions, write output, send keys."""
        target = params.get("target")

        if definer == "CREATE" and not target:
            return await self._create_sessions(ctx, **params)

        if definer == "SEND" and target == "output":
            return await self._write_output(ctx, **params)

        if definer == "SEND" and target == "keys":
            return await self._send_keys(ctx, **params)

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

    async def _send_keys(self, ctx, **params):
        """POST /sessions/keys — send control char or special key to sessions.

        Accepts exactly one of:
          - control_char: single letter for Ctrl+X (e.g., "C" for Ctrl+C)
          - key: named special key (e.g., "enter", "tab", "escape", "up", ...)

        Targets via session_id / agent / name / team (same as resolve_session).
        """
        control_char = params.get("control_char")
        key = params.get("key")

        if control_char and key:
            raise ValueError("send keys: pass either control_char or key, not both")
        if not control_char and not key:
            raise ValueError("send keys: requires control_char=... or key=...")

        terminal = ctx.request_context.lifespan_context["terminal"]
        agent_registry = ctx.request_context.lifespan_context["agent_registry"]
        logger = ctx.request_context.lifespan_context["logger"]

        sessions = await resolve_session(
            terminal,
            agent_registry,
            session_id=params.get("session_id"),
            name=params.get("name"),
            agent=params.get("agent"),
            team=params.get("team"),
        )
        if not sessions:
            raise ValueError("send keys: no matching session found")

        results = []
        for session in sessions:
            if control_char:
                await session.send_control_character(control_char)
                label = f"Ctrl+{control_char.upper()}"
            else:
                await session.send_special_key(key)
                label = f"key '{key}'"
            logger.info(f"Sent {label} to session {session.name}")
            results.append({"session_id": session.id, "name": session.name, "sent": label})

        return {"sent": results, "count": len(results)}

    # ---------------------- PATCH / DELETE (Task 4d) ---------------------- #

    async def on_patch(self, ctx, definer, **params):
        """Route PATCH by `target` — tags, roles, locks, or the session itself."""
        target = params.get("target")

        if target == "tags":
            return await self._patch_tags(ctx, definer, **params)

        if target == "roles":
            return await self._patch_roles(ctx, definer, **params)

        if target == "locks":
            return await self._patch_locks(ctx, definer, **params)

        if target == "active" or target is None:
            # PATCH on the session itself (e.g., set active/focus).
            return await self._patch_session(ctx, definer, **params)

        raise NotImplementedError(f"PATCH target={target!r} not yet implemented")

    async def on_delete(self, ctx, **params):
        """Route DELETE by `target` — roles or locks (no whole-session DELETE yet)."""
        target = params.get("target")

        if target == "roles":
            return await self._delete_role(ctx, **params)

        if target == "locks":
            return await self._delete_lock(ctx, **params)

        # DELETE on the session itself is NOT supported in SP2 (there was no
        # legacy remove_session tool). Reserved for a future task.
        raise NotImplementedError(f"DELETE target={target!r} not yet implemented")

    async def _patch_tags(self, ctx, definer, **params):
        """PATCH /sessions/{id}/tags. MODIFY replaces, APPEND adds."""
        session_id = params.get("session_id")
        if not session_id:
            raise ValueError("patch tags requires session_id")

        tags = params.get("tags")
        if tags is None:
            raise ValueError("patch tags requires tags=[...]")

        lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
        if not lock_manager:
            raise RuntimeError("tag_lock_manager not available")

        # MODIFY → replace (append=False). APPEND → append=True.
        append = (definer == "APPEND")
        updated = lock_manager.set_tags(session_id, tags, append=append)
        return {"session_id": session_id, "tags": updated, "appended": append}

    async def _patch_session(self, ctx, definer, **params):
        """PATCH on a session — e.g., focus/activate."""
        session_id = params.get("session_id")
        if not session_id:
            raise ValueError("patch session requires session_id")

        focus = params.get("focus")
        if focus is not True:
            # Only thing supported in 4d is setting active/focus. Everything
            # else (appearance, monitoring) is reserved for later tasks.
            raise NotImplementedError(
                "patch session: only focus=true supported in this task"
            )

        lifespan = ctx.request_context.lifespan_context
        terminal = lifespan["terminal"]
        await terminal.focus_session(session_id)
        return {"session_id": session_id, "focused": True}

    async def _patch_roles(self, ctx, definer, **params):
        """PATCH /sessions/{id}/roles — assign a role."""
        session_id = params.get("session_id")
        role = params.get("role")
        if not session_id:
            raise ValueError("patch roles requires session_id")
        if not role:
            raise ValueError("patch roles requires role")

        role_manager = ctx.request_context.lifespan_context.get("role_manager")
        if not role_manager:
            raise RuntimeError("role_manager not available")

        assigned_by = params.get("assigned_by")
        role_manager.assign_role(session_id, role, assigned_by=assigned_by)
        return {"session_id": session_id, "role": role}

    async def _delete_role(self, ctx, **params):
        """DELETE /sessions/{id}/roles — remove role assignment."""
        session_id = params.get("session_id")
        if not session_id:
            raise ValueError("delete roles requires session_id")

        role_manager = ctx.request_context.lifespan_context.get("role_manager")
        if not role_manager:
            raise RuntimeError("role_manager not available")

        removed_by = params.get("removed_by")
        role_manager.remove_role(session_id, removed_by=removed_by)
        return {"session_id": session_id, "removed": True}

    async def _patch_locks(self, ctx, definer, **params):
        """PATCH /sessions/{id}/locks — acquire a lock or request access."""
        session_id = params.get("session_id")
        agent = params.get("agent")
        if not session_id:
            raise ValueError("patch locks requires session_id")
        if not agent:
            raise ValueError("patch locks requires agent")

        lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
        if not lock_manager:
            raise RuntimeError("tag_lock_manager not available")

        action = params.get("action", "lock")  # "lock" or "request_access"

        if action == "lock":
            acquired, owner = lock_manager.lock_session(session_id, agent)
            return {
                "session_id": session_id,
                "agent": agent,
                "acquired": acquired,
                "owner": owner,
            }

        if action == "request_access":
            allowed, owner = lock_manager.check_permission(session_id, agent)
            return {
                "session_id": session_id,
                "agent": agent,
                "allowed": allowed,
                "owner": owner,
            }

        raise ValueError(
            f"patch locks: unknown action={action!r} "
            "(expected 'lock' or 'request_access')"
        )

    async def _delete_lock(self, ctx, **params):
        """DELETE /sessions/{id}/locks — release lock."""
        session_id = params.get("session_id")
        agent = params.get("agent")
        if not session_id:
            raise ValueError("delete locks requires session_id")
        if not agent:
            raise ValueError("delete locks requires agent")

        lock_manager = ctx.request_context.lifespan_context.get("tag_lock_manager")
        if not lock_manager:
            raise RuntimeError("tag_lock_manager not available")

        success = lock_manager.unlock_session(session_id, agent)
        return {"session_id": session_id, "agent": agent, "unlocked": success}


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
    # NEW for 4c: keys sub-resource.
    control_char: Optional[str] = None,
    key: Optional[str] = None,
    # NEW for 4d: tags/roles/locks/active sub-resources. Note that `role` is
    # already in the signature as a GET filter (Task 4a); the same slot is
    # reused here for PATCH input.
    assigned_by: Optional[str] = None,
    removed_by: Optional[str] = None,
    action: Optional[str] = None,       # "lock" | "request_access" for locks
    focus: Optional[bool] = None,       # for target="active"
) -> str:
    """Session operations: list, read, write, send keys, create, patch, delete, HEAD, OPTIONS.

    Use op="list" or op="GET" to list sessions with filters.
    Use op="GET" + target="output" to read terminal output.
    Use op="send" (or op="POST" + definer="SEND") + target="output" to write.
    Use op="send" + target="keys" + control_char=... | key=... to send control
      characters or named special keys to session(s).
    Use op="update" (or op="PATCH") + target="tags" to replace tags,
      op="append" + target="tags" to add tags.
    Use op="assign" (or op="PATCH") + target="roles" + role=... to assign a role.
    Use op="update" + target="locks" + agent=... + action="lock"|"request_access"
      to acquire a lock or request access.
    Use op="update" + target="active" + focus=true + session_id=... to focus.
    Use op="delete" (or op="DELETE") + target="roles" to remove a role.
    Use op="unlock" (or op="DELETE") + target="locks" + agent=... to release a lock.
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
        "control_char": control_char,
        "key": key,
        "assigned_by": assigned_by,
        "removed_by": removed_by,
        "action": action,
        "focus": focus,
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
