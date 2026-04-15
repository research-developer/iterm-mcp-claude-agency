"""SP2 method-semantic `sessions_v2` tool — Tasks 4a + 4b + 4c + 4d + 4e.

This module introduces the first collection tool built on the
MethodDispatcher base class. It currently implements:

    GET     — list/filter sessions (delegates to _list_sessions_core),
              read terminal output when target="output", or return
              processing state when target="status".
    HEAD    — compact projection of GET (auto via HEAD_FIELDS)
    OPTIONS — self-describing schema (auto)
    POST + CREATE — create new sessions from a layout
              (delegates to execute_create_sessions), or split an
              existing pane when target="splits".
    POST + SEND   — write to session(s) when target="output"
              (delegates to execute_write_request)
              OR send control char / special key when target="keys"
              (unifies old send_control_character / send_special_key)
    POST + TRIGGER — start monitoring a session when target="monitoring"
              (delegates to _start_monitoring_core).
    PATCH   — update sub-resources: tags (MODIFY replaces, APPEND adds),
              roles (assign), locks (acquire / request access), the
              session itself (target='active' + focus=true), or
              appearance/modifications (target='appearance' or None)
              covering colors, suspend/resume, badge, and focus cooldown.
              Replaces set_session_tags, assign_session_role,
              manage_session_lock (lock / request_access),
              set_active_session, and modify_sessions.
    DELETE  — remove sub-resources: roles (removes assignment), locks
              (releases the lock), or monitoring (stops the monitor).

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
from iterm_mcpy.tools.sessions import _list_sessions_core, _split_session_core
from iterm_mcpy.tools.monitoring import (
    _start_monitoring_core,
    _stop_monitoring_core,
)


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
                "max_lines?", "parallel?",
                "target='status'",
            ],
            "description": (
                "List sessions (no target), read output (target='output'), "
                "or fetch session status (target='status')."
            ),
        },
        "POST": {
            "definers": {
                "CREATE": {
                    "aliases": ["create", "split"],
                    "params": [
                        "layout", "sessions", "register_agents?", "shell?",
                        "target='splits' + direction='below'|'above'|'left'|'right' + session_id",
                        "name?", "agent?", "team?", "register_agent?",
                    ],
                    "description": (
                        "Create sessions (no target) or split an existing "
                        "session (target='splits')."
                    ),
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
                "TRIGGER": {
                    "aliases": ["start", "trigger", "monitor"],
                    "params": [
                        "target='monitoring'",
                        "session_id | agent | name",
                        "enable_event_bus?",
                    ],
                    "description": "Start monitoring a session (target='monitoring').",
                },
            },
        },
        "PATCH": {
            "definers": {
                "MODIFY": {
                    "aliases": ["update", "patch", "assign"],
                    "params": [
                        (
                            "target='tags' | 'roles' | 'locks' | 'active' | "
                            "'appearance' | None (default appearance/focus)"
                        ),
                        "session_id",
                        # tags:
                        "tags?=[...]",
                        # roles:
                        "role?", "assigned_by?",
                        # locks:
                        "agent?", "action?='lock'|'request_access'",
                        # active / appearance:
                        "focus?", "suspended?", "tab_color?", "cursor_color?",
                        "background_color?", "tab_color_enabled?", "badge?",
                        "name?", "reset?",
                    ],
                    "description": (
                        "Update session fields or sub-resources, including "
                        "appearance (colors/badge), suspend/resume, focus, "
                        "and active state."
                    ),
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
            "aliases": ["remove", "unlock", "stop"],
            "params": [
                "target='roles' | 'locks' | 'monitoring'",
                "session_id",
                # locks:
                "agent?",
                # roles:
                "removed_by?",
                # monitoring: session_id|agent|name (no body)
            ],
            "description": (
                "Remove role assignment, release a session lock, or stop "
                "monitoring a session (target='monitoring')."
            ),
        },
        "HEAD": {"compact_fields": ["session_id", "name", "agent", "is_processing", "locked"]},
        "OPTIONS": {"description": "Return this schema."},
    }

    sub_resources = [
        "output", "status", "tags", "locks", "roles", "monitoring",
        "splits", "keys", "appearance", "active",
    ]

    async def on_get(self, ctx, **params):
        """Route GET by `target` — list sessions, read output, or get status."""
        target = params.get("target")
        if target == "output":
            return await self._get_output(ctx, **params)
        if target == "status":
            return await self._get_status(ctx, **params)
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
        """GET /sessions/output — read terminal output via execute_read_request.

        Three ways to specify targets, in precedence order:
        1. Explicit `targets=[...]` list (matching legacy read_sessions)
        2. Shortcut params (session_id / agent / name / team) → built into a
           single target
        3. Neither provided → delegate to ReadSessionsRequest's built-in
           "active session" semantics (pass an empty targets list)
        """
        targets = params.get("targets")
        if targets is None:
            target_spec: dict = {}
            for key in ("session_id", "agent", "name", "team"):
                val = params.get(key)
                if val is not None:
                    target_spec[key] = val
            if target_spec:
                # Allow per-shortcut max_lines override.
                if params.get("max_lines") is not None:
                    target_spec["max_lines"] = params["max_lines"]
                targets = [target_spec]
            else:
                # Fall through to the active-session case.
                targets = []

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
        """Route POST by `(definer, target)` — create, split, write, send keys, monitor."""
        target = params.get("target")

        if definer == "CREATE" and not target:
            return await self._create_sessions(ctx, **params)

        if definer == "CREATE" and target == "splits":
            return await self._create_split(ctx, **params)

        if definer == "SEND" and target == "output":
            return await self._write_output(ctx, **params)

        if definer == "SEND" and target == "keys":
            return await self._send_keys(ctx, **params)

        if definer == "TRIGGER" and target == "monitoring":
            return await self._start_monitoring(ctx, **params)

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
        """Route PATCH by (definer, target).

        Per WebSpec definer semantics, we reject definer/target combinations
        that are not supported. Only `tags` supports APPEND (adding vs
        replacing). All other PATCH targets require MODIFY.
        """
        target = params.get("target")

        # Only tags supports APPEND; everything else is MODIFY-only.
        if definer == "APPEND" and target != "tags":
            raise ValueError(
                f"PATCH+APPEND is only valid with target='tags'. "
                f"Use PATCH+MODIFY for target={target!r}."
            )
        if definer not in ("MODIFY", "APPEND"):
            raise ValueError(
                f"PATCH+{definer} is not supported on sessions. "
                f"Valid definers are MODIFY (and APPEND for tags)."
            )

        if target == "tags":
            return await self._patch_tags(ctx, definer, **params)

        if target == "roles":
            # MODIFY only (already guarded above).
            return await self._patch_roles(ctx, definer, **params)

        if target == "locks":
            # MODIFY only (already guarded above).
            return await self._patch_locks(ctx, definer, **params)

        # PATCH on the session itself — appearance (target='appearance'),
        # active/focus (target='active'), or default (target=None).
        if target in (None, "active", "appearance"):
            return await self._patch_session(ctx, definer, **params)

        raise NotImplementedError(f"PATCH target={target!r} not yet implemented")

    async def on_delete(self, ctx, **params):
        """Route DELETE by `target` — roles, locks, or monitoring."""
        target = params.get("target")

        if target == "roles":
            return await self._delete_role(ctx, **params)

        if target == "locks":
            return await self._delete_lock(ctx, **params)

        if target == "monitoring":
            return await self._stop_monitoring(ctx, **params)

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
        """PATCH on a session — focus/activate, appearance, suspend/resume.

        Routes:
          - target='active' + focus=True → terminal.focus_session(session_id)
            (fast-path preserved for back-compat with Task 4d tests).
          - target='appearance' or target=None with any appearance/process
            modification fields → delegates to `_apply_session_modification`
            from `iterm_mcpy.tools.modifications`.
        """
        session_id = params.get("session_id")
        if not session_id:
            raise ValueError("patch session requires session_id")

        target = params.get("target")
        focus = params.get("focus")

        lifespan = ctx.request_context.lifespan_context
        terminal = lifespan["terminal"]

        # Collect the set of modification fields the caller actually passed.
        # These are the attributes understood by SessionModification.
        modification_fields = (
            "set_active", "focus", "suspend", "resume", "suspend_by",
            "background_color", "tab_color", "tab_color_enabled",
            "cursor_color", "badge", "reset",
        )
        # Map our tool-level `suspended` flag onto the model's suspend/resume
        # pair before looking at the rest of the fields. This is a small
        # convenience so callers can say `suspended=True` / `suspended=False`
        # rather than picking between suspend= and resume=.
        if "suspended" in params and "suspend" not in params and "resume" not in params:
            if params["suspended"]:
                params["suspend"] = True
            else:
                params["resume"] = True

        passed_mods = {
            k: params[k] for k in modification_fields if k in params
        }

        # Fast path: target='active' + focus=True with no other modification
        # fields — preserve the Task 4d lightweight response.
        if (
            target == "active"
            and focus is True
            and len(passed_mods) == 1
        ):
            await terminal.focus_session(session_id)
            return {"session_id": session_id, "focused": True}

        # Without any modification fields and without an explicit target, the
        # request is a no-op — surface NotImplemented as in 4d.
        if not passed_mods and target in (None, "active"):
            raise NotImplementedError(
                "patch session: no modification fields provided"
            )

        # Otherwise delegate to the legacy _apply_session_modification helper.
        from core.models import SessionModification
        from iterm_mcpy.tools.modifications import _apply_session_modification

        agent_registry = lifespan["agent_registry"]
        focus_cooldown = lifespan.get("focus_cooldown")
        logger = lifespan["logger"]

        # Build a SessionModification. Identity always comes from session_id
        # (sessions_v2 is session-centric; the modification helper itself
        # resolves session_id -> session).
        mod_kwargs = {"session_id": session_id}
        # Forward supported fields. Use model_validate so Pydantic handles
        # nested ColorSpec dicts (tab_color/cursor_color/background_color).
        for key in modification_fields:
            if key in params:
                mod_kwargs[key] = params[key]

        modification = SessionModification.model_validate(mod_kwargs)

        sessions = await resolve_session(
            terminal, agent_registry, session_id=session_id,
        )
        if not sessions:
            raise ValueError(f"patch session: no session found with id={session_id}")

        session = sessions[0]
        result = await _apply_session_modification(
            session, modification, terminal, agent_registry, logger, focus_cooldown,
        )
        return result

    async def _patch_roles(self, ctx, definer, **params):
        """PATCH /sessions/{id}/roles — assign a role."""
        from core.models import SessionRole

        session_id = params.get("session_id")
        role = params.get("role")
        if not session_id:
            raise ValueError("patch roles requires session_id")
        if not role:
            raise ValueError("patch roles requires role")

        role_manager = ctx.request_context.lifespan_context.get("role_manager")
        if not role_manager:
            raise RuntimeError("role_manager not available")

        # Coerce raw strings to the SessionRole enum (case-insensitive match on value).
        role_enum = role if isinstance(role, SessionRole) else SessionRole(role.lower())
        assigned_by = params.get("assigned_by")
        role_manager.assign_role(session_id, role_enum, assigned_by=assigned_by)
        return {"session_id": session_id, "role": role_enum.value}

    async def _delete_role(self, ctx, **params):
        """DELETE /sessions/{id}/roles — remove role assignment."""
        session_id = params.get("session_id")
        if not session_id:
            raise ValueError("delete roles requires session_id")

        role_manager = ctx.request_context.lifespan_context.get("role_manager")
        if not role_manager:
            raise RuntimeError("role_manager not available")

        # NOTE: core/roles.py RoleManager.remove_role only takes session_id.
        # `removed_by` is accepted by the tool signature but not persisted;
        # audit trail for removals can be added in a follow-up.
        removed = role_manager.remove_role(session_id)
        return {"session_id": session_id, "removed": bool(removed)}

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

    # ---------------------- Status / splits / monitoring (Task 4e) ---- #

    async def _get_status(self, ctx, **params):
        """GET /sessions/{id}/status — returns processing state.

        Replaces the legacy ``check_session_status`` tool. Resolves the target
        session (by session_id / agent / name) and returns a compact status
        record per match.
        """
        session_id = params.get("session_id")
        name = params.get("name")
        agent = params.get("agent")
        if not any([session_id, name, agent]):
            raise ValueError(
                "get status requires at least one of: session_id, name, agent"
            )

        lifespan = ctx.request_context.lifespan_context
        terminal = lifespan["terminal"]
        agent_registry = lifespan["agent_registry"]
        lock_manager = lifespan.get("tag_lock_manager")

        sessions = await resolve_session(
            terminal, agent_registry,
            session_id=session_id, name=name, agent=agent,
        )
        if not sessions:
            raise ValueError("get status: no matching session found")

        active_id = getattr(agent_registry, "active_session", None)

        statuses = []
        for s in sessions:
            agent_obj = agent_registry.get_agent_by_session(s.id)
            status: dict = {
                "session_id": s.id,
                "name": s.name,
                "persistent_id": getattr(s, "persistent_id", None),
                "agent": agent_obj.name if agent_obj else None,
                "teams": agent_obj.teams if agent_obj else [],
                "is_processing": getattr(s, "is_processing", False),
                "is_monitoring": getattr(s, "is_monitoring", False),
                "is_active": s.id == active_id,
                "suspended": getattr(s, "is_suspended", False),
            }
            if lock_manager is not None:
                lock_info = lock_manager.get_lock_info(s.id)
                status["tags"] = lock_manager.get_tags(s.id)
                status["locked"] = lock_info is not None
                status["locked_by"] = lock_info.owner if lock_info else None
                status["locked_at"] = (
                    lock_info.locked_at.isoformat() if lock_info else None
                )
                status["pending_access_requests"] = (
                    len(lock_info.pending_requests) if lock_info else 0
                )
            statuses.append(status)

        return statuses

    async def _create_split(self, ctx, **params):
        """POST /sessions/{id}/splits (CREATE) — split a pane.

        Replaces the legacy ``split_session`` tool. Delegates to the shared
        ``_split_session_core`` helper in ``iterm_mcpy.tools.sessions``.
        """
        from core.models import SplitSessionRequest

        session_id = params.get("session_id")
        if not session_id:
            raise ValueError("create split requires session_id")

        direction = params.get("direction", "below")

        lifespan = ctx.request_context.lifespan_context
        terminal = lifespan["terminal"]
        agent_registry = lifespan["agent_registry"]
        role_manager = lifespan["role_manager"]
        logger = lifespan["logger"]
        profile_manager = lifespan.get("profile_manager")

        # Build SessionTarget from the scalar session_id (the dispatcher surface
        # is identity-by-id, not the legacy nested target object).
        request_kwargs: dict = {
            "target": {"session_id": session_id},
            "direction": direction,
        }
        for key in (
            "name", "profile", "command", "agent", "agent_type",
            "team", "monitor", "role", "role_config",
        ):
            if key in params:
                request_kwargs[key] = params[key]

        split_request = SplitSessionRequest.model_validate(request_kwargs)

        response = await _split_session_core(
            split_request,
            terminal,
            agent_registry,
            role_manager,
            logger,
            profile_manager=profile_manager,
        )
        return response

    async def _start_monitoring(self, ctx, **params):
        """POST /sessions/{id}/monitoring (TRIGGER) — start monitoring a session."""
        session_id = params.get("session_id")
        name = params.get("name")
        agent = params.get("agent")
        if not any([session_id, name, agent]):
            raise ValueError(
                "start monitoring requires at least one of: session_id, name, agent"
            )

        lifespan = ctx.request_context.lifespan_context
        terminal = lifespan["terminal"]
        agent_registry = lifespan["agent_registry"]
        event_bus = lifespan.get("event_bus")
        logger = lifespan["logger"]

        sessions = await resolve_session(
            terminal, agent_registry,
            session_id=session_id, name=name, agent=agent,
        )
        if not sessions:
            raise ValueError("start monitoring: no matching session found")

        enable_event_bus = params.get("enable_event_bus", True)
        # The callback only gets wired up if both the caller opted in AND an
        # event_bus exists in the lifespan — report the effective state, not
        # just the requested one, so callers can detect missing infrastructure.
        event_bus_attached = enable_event_bus and event_bus is not None

        results = []
        for session in sessions:
            started = await _start_monitoring_core(
                session,
                event_bus,
                logger,
                enable_event_bus=enable_event_bus,
                # Settle delay mostly matters for the legacy blocking tool;
                # sessions_v2 returns the result structurally so skip the wait.
                settle_delay=0,
            )
            results.append({
                "session_id": session.id,
                "name": session.name,
                "started": started,
                "event_bus_requested": enable_event_bus,
                "event_bus_attached": event_bus_attached,
            })

        return {"monitoring": results, "count": len(results)}

    async def _stop_monitoring(self, ctx, **params):
        """DELETE /sessions/{id}/monitoring — stop monitoring a session."""
        session_id = params.get("session_id")
        name = params.get("name")
        agent = params.get("agent")
        if not any([session_id, name, agent]):
            raise ValueError(
                "stop monitoring requires at least one of: session_id, name, agent"
            )

        lifespan = ctx.request_context.lifespan_context
        terminal = lifespan["terminal"]
        agent_registry = lifespan["agent_registry"]
        logger = lifespan["logger"]

        sessions = await resolve_session(
            terminal, agent_registry,
            session_id=session_id, name=name, agent=agent,
        )
        if not sessions:
            raise ValueError("stop monitoring: no matching session found")

        results = []
        for session in sessions:
            stopped = await _stop_monitoring_core(session, logger)
            results.append({
                "session_id": session.id,
                "name": session.name,
                "stopped": stopped,
            })

        return {"monitoring": results, "count": len(results)}


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
    # NEW for 4e: splits / monitoring / status / appearance (full modify).
    direction: Optional[str] = None,            # split direction
    enable_event_bus: Optional[bool] = None,    # monitoring toggle
    register_agent: Optional[bool] = None,      # (reserved for split / create)
    # Appearance & process-control modification fields. Pydantic handles nested
    # dicts for color specs (e.g. tab_color={"red":..,"green":..,"blue":..}).
    tab_color: Optional[dict] = None,
    cursor_color: Optional[dict] = None,
    background_color: Optional[dict] = None,
    tab_color_enabled: Optional[bool] = None,
    badge: Optional[str] = None,
    suspended: Optional[bool] = None,           # shortcut for suspend/resume
    suspend: Optional[bool] = None,
    resume: Optional[bool] = None,
    suspend_by: Optional[str] = None,
    set_active: Optional[bool] = None,
    reset: Optional[bool] = None,
) -> str:
    """Session operations: list, read, write, send keys, create, split, monitor,
    modify, patch, delete, HEAD, OPTIONS.

    Use op="list" or op="GET" to list sessions with filters.
    Use op="GET" + target="output" to read terminal output.
    Use op="GET" + target="status" + session_id=... to fetch processing state.
    Use op="send" (or op="POST" + definer="SEND") + target="output" to write.
    Use op="send" + target="keys" + control_char=... | key=... to send control
      characters or named special keys to session(s).
    Use op="create" + target="splits" + session_id=... + direction=... to split
      an existing pane (below/above/left/right).
    Use op="start" (or op="POST" + definer="TRIGGER") + target="monitoring" to
      begin real-time output monitoring. Use op="stop" + target="monitoring"
      (DELETE) to end it.
    Use op="update" (or op="PATCH") + target="tags" to replace tags,
      op="append" + target="tags" to add tags.
    Use op="assign" (or op="PATCH") + target="roles" + role=... to assign a role.
    Use op="update" + target="locks" + agent=... + action="lock"|"request_access"
      to acquire a lock or request access.
    Use op="update" + target="active" + focus=true + session_id=... to focus.
    Use op="update" + target="appearance" + session_id=... + (tab_color,
      cursor_color, badge, suspended, reset, ...) to change session visuals
      and process state.
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
        # 4e
        "direction": direction,
        "enable_event_bus": enable_event_bus,
        "register_agent": register_agent,
        "tab_color": tab_color,
        "cursor_color": cursor_color,
        "background_color": background_color,
        "tab_color_enabled": tab_color_enabled,
        "badge": badge,
        "suspended": suspended,
        "suspend": suspend,
        "resume": resume,
        "suspend_by": suspend_by,
        "set_active": set_active,
        "reset": reset,
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
