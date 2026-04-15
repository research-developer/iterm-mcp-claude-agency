"""SP2 method-semantic `services` tool — Task 10.

Seventh SP2 collection tool (after sessions + agents + teams +
managers + feedback + memory). Replaces the legacy
``manage_services`` tool's 6 operations:

    - list           -> GET              /services
    - list_inactive  -> GET              /services  (target='inactive')
    - start          -> POST + TRIGGER   /services/{name}/runs
    - stop           -> DELETE           /services/{name}/runs
    - add            -> POST + CREATE    /services
    - configure      -> PATCH + MODIFY   /services/{name}

Registered under the provisional name ``services`` to coexist with the
legacy ``manage_services`` tool; the cutover (rename to ``services`` and
unregister the legacy tool) happens at the end of SP2.
"""
from typing import List, Optional

from mcp.server.fastmcp import Context

from iterm_mcpy.dispatcher import MethodDispatcher


class ServicesDispatcher(MethodDispatcher):
    """Dispatcher for the `services` collection (SP2 method-semantic)."""

    collection = "services"

    METHODS = {
        "GET": {
            "aliases": ["list", "get", "read", "query"],
            "params": [
                "target=None | target='inactive'",
                "service_name?",
                "repo_path?",
                "min_priority?",
                "include_status?=true",
            ],
            "description": (
                "List services (no target) or list inactive services "
                "(target='inactive', requires repo_path)."
            ),
        },
        "POST": {
            "definers": {
                "CREATE": {
                    "aliases": ["add", "create", "register"],
                    "params": [
                        "service_name",
                        "command",
                        "priority?",
                        "display_name?",
                        "port?",
                        "working_directory?",
                        "repo_patterns?",
                        "scope?='global'|'repo'",
                        "repo_path? (required when scope='repo')",
                    ],
                    "description": (
                        "Add a new service to the global or repo registry."
                    ),
                },
                "TRIGGER": {
                    "aliases": ["start", "trigger", "spawn"],
                    "params": [
                        "service_name",
                        "repo_path?",
                    ],
                    "description": (
                        "Start a configured service in an iTerm session."
                    ),
                },
            },
        },
        "PATCH": {
            "definers": {
                "MODIFY": {
                    "aliases": ["configure", "update", "modify", "edit"],
                    "params": [
                        "service_name",
                        "priority?",
                        "command?",
                        "display_name?",
                        "port?",
                        "working_directory?",
                        "scope?='global'|'repo'",
                        "repo_path? (required when scope='repo')",
                    ],
                    "description": (
                        "Update an existing service's configuration."
                    ),
                },
            },
        },
        "DELETE": {
            "aliases": ["stop", "remove", "delete"],
            "params": ["service_name"],
            "description": "Stop a running service.",
        },
        "HEAD": {"compact_fields": ["name", "priority"]},
        "OPTIONS": {"description": "Return this schema."},
    }

    sub_resources = ["inactive"]

    # -------------------------------- GET -------------------------------- #

    async def on_get(self, ctx, **params):
        """Route GET by `target` — list services or list inactive services."""
        target = params.get("target")
        if target == "inactive":
            return await self._list_inactive(ctx, **params)
        return await self._list(ctx, **params)

    async def _list(self, ctx, **params):
        """GET /services — list configured services (global or merged w/ repo)."""
        from core.services import ServicePriority

        lifespan = ctx.request_context.lifespan_context
        service_manager = lifespan["service_manager"]
        logger = lifespan["logger"]

        repo_path = params.get("repo_path")
        min_priority_raw = params.get("min_priority")
        include_status = params.get("include_status", True)

        priority = None
        if min_priority_raw:
            priority = ServicePriority.from_string(min_priority_raw)

        if repo_path:
            services = service_manager.get_merged_services(repo_path, priority)
        else:
            global_registry = service_manager.load_global_config()
            services = global_registry.services
            if priority:
                priority_order = [
                    ServicePriority.QUIET,
                    ServicePriority.OPTIONAL,
                    ServicePriority.PREFERRED,
                    ServicePriority.REQUIRED,
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
            if include_status:
                info["is_running"] = await service_manager.check_service_running(service)
            result.append(info)

        logger.info(f"services GET: listed {len(result)} services")
        return {
            "services": result,
            "count": len(result),
            "repo_path": repo_path,
        }

    async def _list_inactive(self, ctx, **params):
        """GET /services?target=inactive — list services that should run but aren't."""
        from core.services import ServicePriority

        lifespan = ctx.request_context.lifespan_context
        service_manager = lifespan["service_manager"]
        logger = lifespan["logger"]

        repo_path = params.get("repo_path")
        if not repo_path:
            raise ValueError("list_inactive requires repo_path")

        priority = None
        min_priority_raw = params.get("min_priority")
        if min_priority_raw:
            priority = ServicePriority.from_string(min_priority_raw)

        inactive = await service_manager.get_inactive_services(repo_path, priority)

        result = [
            {
                "name": service.name,
                "display_name": service.effective_display_name,
                "priority": service.priority.value,
                "command": service.command,
            }
            for service in inactive
        ]

        logger.info(
            f"services GET inactive: {len(result)} inactive in {repo_path}"
        )
        return {
            "inactive_services": result,
            "count": len(result),
            "repo_path": repo_path,
        }

    # ------------------------------- POST -------------------------------- #

    async def on_post(self, ctx, definer, **params):
        """Route POST by definer — CREATE (add) or TRIGGER (start)."""
        if definer == "CREATE":
            return await self._add(ctx, **params)
        if definer == "TRIGGER":
            return await self._start(ctx, **params)
        raise NotImplementedError(
            f"POST+{definer} not supported on services"
        )

    async def _add(self, ctx, **params):
        """POST /services (CREATE) — add a service to global or repo registry."""
        from core.services import ServiceConfig, ServicePriority

        lifespan = ctx.request_context.lifespan_context
        service_manager = lifespan["service_manager"]
        logger = lifespan["logger"]

        service_name = params.get("service_name")
        command = params.get("command")
        if not service_name:
            raise ValueError("add service requires service_name")
        if not command:
            raise ValueError("add service requires command")

        scope = params.get("scope", "global")
        repo_path = params.get("repo_path")

        if scope == "repo" and not repo_path:
            raise ValueError("repo_path required when scope is 'repo'")

        service = ServiceConfig(
            name=service_name,
            display_name=params.get("display_name"),
            command=command,
            priority=ServicePriority.from_string(params.get("priority") or "optional"),
            port=params.get("port"),
            working_directory=params.get("working_directory"),
            repo_patterns=params.get("repo_patterns") or [],
        )

        if scope == "repo":
            registry = service_manager.load_repo_config(repo_path)
            registry.services = [s for s in registry.services if s.name != service_name]
            registry.services.append(service)
            service_manager.save_repo_config(repo_path, registry)
        else:
            registry = service_manager.load_global_config()
            registry.services = [s for s in registry.services if s.name != service_name]
            registry.services.append(service)
            service_manager.save_global_config(registry)

        logger.info(
            f"services CREATE: added service '{service_name}' to {scope} config"
        )
        return {
            "service": service_name,
            "scope": scope,
            "added": True,
        }

    async def _start(self, ctx, **params):
        """POST /services/{name}/runs (TRIGGER) — start a configured service."""
        lifespan = ctx.request_context.lifespan_context
        service_manager = lifespan["service_manager"]
        logger = lifespan["logger"]

        service_name = params.get("service_name")
        if not service_name:
            raise ValueError("start service requires service_name")

        repo_path = params.get("repo_path")
        if repo_path:
            services = service_manager.get_merged_services(repo_path)
        else:
            global_registry = service_manager.load_global_config()
            services = global_registry.services

        service = next((s for s in services if s.name == service_name), None)
        if service is None:
            available = ", ".join(s.name for s in services) or "(none)"
            raise RuntimeError(
                f"Service '{service_name}' not found. "
                f"Available: {available}"
            )

        state = await service_manager.start_service(service, repo_path=repo_path)

        logger.info(
            f"services TRIGGER: start '{service_name}' -> "
            f"running={state.is_running}"
        )
        return {
            "service": service_name,
            "started": state.is_running,
            "session_id": state.session_id,
            "error": state.error_message,
        }

    # ------------------------------- PATCH ------------------------------- #

    async def on_patch(self, ctx, definer, **params):
        """Route PATCH by definer — MODIFY (configure)."""
        if definer == "MODIFY":
            return await self._configure(ctx, **params)
        raise NotImplementedError(
            f"PATCH+{definer} not supported on services"
        )

    async def _configure(self, ctx, **params):
        """PATCH /services/{name} (MODIFY) — update a service's config."""
        from core.services import ServiceConfig, ServicePriority

        lifespan = ctx.request_context.lifespan_context
        service_manager = lifespan["service_manager"]
        logger = lifespan["logger"]

        service_name = params.get("service_name")
        if not service_name:
            raise ValueError("configure service requires service_name")

        scope = params.get("scope", "global")
        repo_path = params.get("repo_path")

        if scope == "repo":
            if not repo_path:
                raise ValueError("repo_path required when scope is 'repo'")
            registry = service_manager.load_repo_config(repo_path)
        else:
            registry = service_manager.load_global_config()

        updates: dict = {}
        if params.get("priority"):
            updates["priority"] = ServicePriority.from_string(params["priority"])
        if params.get("port") is not None:
            updates["port"] = params["port"]
        if params.get("command"):
            updates["command"] = params["command"]
        if params.get("display_name"):
            updates["display_name"] = params["display_name"]
        if params.get("working_directory"):
            updates["working_directory"] = params["working_directory"]

        found = False
        for i, service in enumerate(registry.services):
            if service.name == service_name:
                found = True
                updated_data = service.model_dump()
                updated_data.update(updates)
                registry.services[i] = ServiceConfig.model_validate(updated_data)
                break

        if not found:
            raise RuntimeError(
                f"Service '{service_name}' not found in {scope} config"
            )

        if scope == "repo":
            service_manager.save_repo_config(repo_path, registry)
        else:
            service_manager.save_global_config(registry)

        logger.info(
            f"services MODIFY: updated service '{service_name}' in {scope} config"
        )
        return {
            "service": service_name,
            "scope": scope,
            "updated": True,
        }

    # ------------------------------ DELETE ------------------------------- #

    async def on_delete(self, ctx, **params):
        """DELETE /services/{name}/runs — stop a running service."""
        return await self._stop(ctx, **params)

    async def _stop(self, ctx, **params):
        """DELETE /services/{name}/runs — stop a running service."""
        lifespan = ctx.request_context.lifespan_context
        service_manager = lifespan["service_manager"]
        logger = lifespan["logger"]

        service_name = params.get("service_name")
        if not service_name:
            raise ValueError("stop service requires service_name")

        success = await service_manager.stop_service(service_name)
        logger.info(
            f"services DELETE: stop '{service_name}' -> success={success}"
        )
        return {
            "service": service_name,
            "stopped": success,
        }


_dispatcher = ServicesDispatcher()


# Legacy `manage_services` op strings that aren't in the central VERB_ATLAS.
# Mapped locally to (method, definer, implied_target) so callers can keep
# using the legacy vocabulary during the SP2 coexistence period. Verbs that
# already exist in VERB_ATLAS (list/add/start/stop) pass through untouched.
_LEGACY_OP_MAP: dict[str, tuple[str, Optional[str], Optional[str]]] = {
    # configure -> PATCH+MODIFY (PATCH canonical)
    "configure": ("PATCH", "MODIFY", None),
    # list_inactive -> GET target='inactive'
    "list_inactive": ("GET", None, "inactive"),
}


async def services(
    ctx: Context,
    op: str = "GET",
    definer: Optional[str] = None,
    target: Optional[str] = None,
    service_name: Optional[str] = None,
    repo_path: Optional[str] = None,
    min_priority: Optional[str] = None,
    include_status: bool = True,
    command: Optional[str] = None,
    priority: Optional[str] = None,
    display_name: Optional[str] = None,
    port: Optional[int] = None,
    working_directory: Optional[str] = None,
    repo_patterns: Optional[List[str]] = None,
    scope: str = "global",
) -> str:
    """Service management: list, start, stop, add, configure, list_inactive.

    Use op="list" (or op="GET") to list configured services. Pass repo_path
      to get the merged global+repo view; optionally filter by min_priority
      and include per-service is_running status.
    Use op="GET" + target="inactive" + repo_path to list services that
      should be running but aren't (aka the legacy list_inactive op).
    Use op="add" (or op="POST" + definer="CREATE") + service_name + command
      (+ priority?/display_name?/port?/working_directory?/repo_patterns?/
      scope?/repo_path?) to add a new service to the global or repo
      registry.
    Use op="start" (or op="POST" + definer="TRIGGER") + service_name
      (+ repo_path?) to start a configured service.
    Use op="configure" (or op="PATCH" + definer="MODIFY") + service_name
      (+ any of priority/command/display_name/port/working_directory/scope/
      repo_path) to update an existing service's configuration.
    Use op="stop" (or op="DELETE") + service_name to stop a running service.
    Use op="HEAD" (or "peek"/"summary") for a compact service list.
    Use op="OPTIONS" (or "schema") to discover the tool's surface.

    Args:
        op: HTTP method or friendly verb.
        definer: Explicit definer (CREATE / TRIGGER for POST, MODIFY for PATCH).
        target: Sub-resource: 'inactive' for GET. Omit for other ops.
        service_name: Service identifier (required for add/start/stop/configure).
        repo_path: Repository path (list merging, list_inactive, start context,
            or scope='repo' persistence).
        min_priority: Minimum priority filter for list / list_inactive
            ('quiet'|'optional'|'preferred'|'required').
        include_status: Whether to include is_running status in list output.
        command: Command string (required for add; optional for configure).
        priority: Priority string for add / configure.
        display_name: Human-readable name (add / configure).
        port: Port number for status checks (add / configure).
        working_directory: Working dir for the service (add / configure).
        repo_patterns: Glob patterns for matching repos (add only).
        scope: Where to persist config: 'global' (default) or 'repo'.

    This is SP2's seventh method-semantic collection tool. It coexists with
    the legacy ``manage_services`` tool and will eventually replace it.
    """
    # Translate services-specific legacy op strings (configure /
    # list_inactive) to their (method, definer, target) triple so the
    # shared dispatcher + VERB_ATLAS stays clean.
    legacy = _LEGACY_OP_MAP.get(op.lower())
    if legacy is not None:
        method, mapped_definer, mapped_target = legacy
        op = method
        if mapped_definer is not None and definer is None:
            definer = mapped_definer
        if mapped_target is not None and target is None:
            target = mapped_target

    raw_params = {
        "target": target,
        "service_name": service_name,
        "repo_path": repo_path,
        "min_priority": min_priority,
        "include_status": include_status,
        "command": command,
        "priority": priority,
        "display_name": display_name,
        "port": port,
        "working_directory": working_directory,
        "repo_patterns": repo_patterns,
        "scope": scope,
    }
    # Keep `include_status` and `scope` even at defaults — handlers need them.
    # Filter out params the user explicitly didn't set (None values).
    params = {k: v for k, v in raw_params.items() if v is not None}

    return await _dispatcher.dispatch(ctx=ctx, op=op, definer=definer, **params)


def register(mcp):
    """Register the services dispatcher tool.

    Named ``services`` to coexist with the legacy ``manage_services``
    tool during the SP2 coexistence period. Final cutover (renaming to
    ``services``) happens at the end of SP2.
    """
    mcp.tool(name="services")(services)
