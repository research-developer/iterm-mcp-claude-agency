"""SP2 method-semantic `managers` tool — Task 7.

Fourth SP2 collection tool (after sessions + agents + teams).
Replaces the legacy ``manage_managers`` tool's 6 operations:

    - create         -> POST + CREATE  /managers
    - list           -> GET             /managers
    - get_info       -> GET             /managers/{name}
    - remove         -> DELETE          /managers/{name}
    - add_worker     -> POST + CREATE   /managers/{name}/workers
    - remove_worker  -> DELETE          /managers/{name}/workers

Registered under the provisional name ``managers`` to coexist with the
legacy ``manage_managers`` tool; the cutover (rename to ``managers`` and
unregister the legacy tool) happens at the end of SP2.

Note: ``delegate_task`` and ``execute_plan`` (other tools in the legacy
``managers`` module) are action tools and are handled by Task 14, not
this task.
"""
from typing import Dict, List, Optional

from mcp.server.fastmcp import Context

from iterm_mcpy.dispatcher import MethodDispatcher


class ManagersDispatcher(MethodDispatcher):
    """Dispatcher for the `managers` collection (SP2 method-semantic)."""

    collection = "managers"

    METHODS = {
        "GET": {
            "aliases": ["list", "get", "read", "query"],
            "params": [
                "manager_name? (without: list all; with: get_info)",
            ],
            "description": (
                "List managers (no manager_name) or get a single manager's "
                "details (with manager_name)."
            ),
        },
        "POST": {
            "definers": {
                "CREATE": {
                    "aliases": ["create", "add", "register"],
                    "params": [
                        "target=None | target='workers'",
                        # target=None (create manager):
                        "manager_name",
                        "workers?=[...]",
                        "delegation_strategy?='role_based'|'round_robin'|"
                        "'least_busy'|'random'|'priority'",
                        "worker_roles?={worker: role}",
                        "metadata?={...}",
                        # target='workers' (add worker):
                        "worker_name",
                        "worker_role?",
                    ],
                    "description": (
                        "Create a new manager (no target) or add a worker "
                        "to a manager (target='workers')."
                    ),
                },
            },
        },
        "DELETE": {
            "aliases": ["remove", "delete"],
            "params": [
                "target=None | target='workers'",
                "manager_name",
                # target='workers':
                "worker_name?",
            ],
            "description": (
                "Remove a manager (no target) or remove a worker from a "
                "manager (target='workers')."
            ),
        },
        "HEAD": {"compact_fields": ["name", "worker_count"]},
        "OPTIONS": {"description": "Return this schema."},
    }

    sub_resources = ["workers"]

    # -------------------------------- GET -------------------------------- #

    async def on_get(self, ctx, **params):
        """GET /managers — list managers, or get one by name."""
        manager_name = params.get("manager_name")
        if manager_name:
            return await self._get_manager_info(ctx, manager_name)
        return await self._list_managers(ctx)

    async def _list_managers(self, ctx):
        """GET /managers — list all registered managers."""
        lifespan = ctx.request_context.lifespan_context
        manager_registry = lifespan["manager_registry"]
        logger = lifespan["logger"]

        managers = manager_registry.list_managers()
        result = []
        for manager in managers:
            result.append({
                "name": manager.name,
                "workers": manager.workers,
                "delegation_strategy": manager.strategy.value,
                "worker_count": len(manager.workers),
            })

        logger.info(f"managers GET: listed {len(result)} managers")
        return {"managers": result, "count": len(result)}

    async def _get_manager_info(self, ctx, manager_name: str):
        """GET /managers/{name} — detailed info for a single manager."""
        lifespan = ctx.request_context.lifespan_context
        manager_registry = lifespan["manager_registry"]
        logger = lifespan["logger"]

        manager = manager_registry.get_manager(manager_name)
        if not manager:
            raise RuntimeError(f"Manager '{manager_name}' not found")

        logger.info(f"managers GET: info for manager '{manager_name}'")
        return {
            "name": manager.name,
            "workers": manager.workers,
            "worker_roles": {k: v.value for k, v in manager.worker_roles.items()},
            "delegation_strategy": manager.strategy.value,
            "created_at": manager.created_at.isoformat(),
            "metadata": manager.metadata,
        }

    # ------------------------------- POST -------------------------------- #

    async def on_post(self, ctx, definer, **params):
        """Route POST by (definer, target) — create manager or add worker."""
        target = params.get("target")

        if definer == "CREATE" and target is None:
            return await self._create_manager(ctx, **params)

        if definer == "CREATE" and target == "workers":
            return await self._add_worker(ctx, **params)

        raise NotImplementedError(
            f"POST+{definer} on target={target!r} not yet implemented"
        )

    async def _create_manager(self, ctx, **params):
        """POST /managers (CREATE) — create a new manager and wire callbacks."""
        # Imports are lazy so tests that only exercise OPTIONS / list paths
        # don't need the manager module to even be importable (it isn't — it
        # always is — but keeping parity with teams style keeps this
        # predictable).
        from core.manager import DelegationStrategy
        from core.manager import SessionRole as ManagerSessionRole

        lifespan = ctx.request_context.lifespan_context
        manager_registry = lifespan["manager_registry"]
        terminal = lifespan["terminal"]
        agent_registry = lifespan["agent_registry"]
        logger = lifespan["logger"]

        manager_name = params.get("manager_name")
        if not manager_name:
            raise ValueError("create manager requires manager_name")

        workers = params.get("workers", []) or []
        delegation_strategy_raw = params.get("delegation_strategy", "role_based")
        worker_roles_raw = params.get("worker_roles", {}) or {}
        metadata = params.get("metadata", {}) or {}

        # Convert worker_roles values from strings to ManagerSessionRole enums.
        worker_roles = {
            name: (
                ManagerSessionRole(role) if isinstance(role, str) else role
            )
            for name, role in worker_roles_raw.items()
        }

        # Convert delegation_strategy string to DelegationStrategy enum.
        delegation_strategy = (
            DelegationStrategy(delegation_strategy_raw)
            if isinstance(delegation_strategy_raw, str)
            else delegation_strategy_raw
        )

        manager = manager_registry.create_manager(
            name=manager_name,
            workers=workers,
            delegation_strategy=delegation_strategy,
            worker_roles=worker_roles,
            metadata=metadata,
        )

        # Set up execution callbacks. The shared helper wires the manager's
        # ``_execute_callback`` to drive workers through the terminal.
        from iterm_mcpy.tools._callbacks import _setup_manager_callbacks
        _setup_manager_callbacks(manager, terminal, agent_registry, logger)

        logger.info(
            f"managers CREATE: created manager '{manager.name}' with "
            f"{len(workers)} workers (strategy={manager.strategy.value})"
        )
        return {
            "name": manager.name,
            "workers": manager.workers,
            "delegation_strategy": manager.strategy.value,
            "created": True,
        }

    async def _add_worker(self, ctx, **params):
        """POST /managers/{name}/workers (CREATE) — add a worker to a manager."""
        from core.manager import SessionRole as ManagerSessionRole

        lifespan = ctx.request_context.lifespan_context
        manager_registry = lifespan["manager_registry"]
        logger = lifespan["logger"]

        manager_name = params.get("manager_name")
        worker_name = params.get("worker_name")
        if not manager_name:
            raise ValueError("add worker requires manager_name")
        if not worker_name:
            raise ValueError("add worker requires worker_name")

        manager = manager_registry.get_manager(manager_name)
        if not manager:
            raise RuntimeError(f"Manager '{manager_name}' not found")

        worker_role_raw = params.get("worker_role")
        role = (
            ManagerSessionRole(worker_role_raw)
            if worker_role_raw is not None
            else None
        )
        manager.add_worker(worker_name, role)

        logger.info(
            f"managers CREATE workers: added worker '{worker_name}' to "
            f"manager '{manager_name}'"
        )
        return {
            "manager_name": manager_name,
            "worker_name": worker_name,
            "role": worker_role_raw,
            "added": True,
        }

    # ------------------------------- DELETE ------------------------------ #

    async def on_delete(self, ctx, **params):
        """Route DELETE by `target` — remove manager or remove worker."""
        target = params.get("target")

        if target is None:
            return await self._remove_manager(ctx, **params)

        if target == "workers":
            return await self._remove_worker(ctx, **params)

        raise NotImplementedError(
            f"DELETE target={target!r} not yet implemented"
        )

    async def _remove_manager(self, ctx, **params):
        """DELETE /managers/{name} — remove a manager."""
        lifespan = ctx.request_context.lifespan_context
        manager_registry = lifespan["manager_registry"]
        logger = lifespan["logger"]

        manager_name = params.get("manager_name")
        if not manager_name:
            raise ValueError("remove manager requires manager_name")

        removed = manager_registry.remove_manager(manager_name)
        if not removed:
            raise RuntimeError(f"Manager '{manager_name}' not found")

        logger.info(f"managers DELETE: removed manager '{manager_name}'")
        return {"manager_name": manager_name, "removed": True}

    async def _remove_worker(self, ctx, **params):
        """DELETE /managers/{name}/workers — remove a worker from a manager."""
        lifespan = ctx.request_context.lifespan_context
        manager_registry = lifespan["manager_registry"]
        logger = lifespan["logger"]

        manager_name = params.get("manager_name")
        worker_name = params.get("worker_name")
        if not manager_name:
            raise ValueError("remove worker requires manager_name")
        if not worker_name:
            raise ValueError("remove worker requires worker_name")

        manager = manager_registry.get_manager(manager_name)
        if not manager:
            raise RuntimeError(f"Manager '{manager_name}' not found")

        removed = manager.remove_worker(worker_name)
        if not removed:
            raise RuntimeError(
                f"Worker '{worker_name}' not found in manager '{manager_name}'"
            )

        logger.info(
            f"managers DELETE workers: removed worker '{worker_name}' "
            f"from manager '{manager_name}'"
        )
        return {
            "manager_name": manager_name,
            "worker_name": worker_name,
            "removed": True,
        }


_dispatcher = ManagersDispatcher()


async def managers(
    ctx: Context,
    op: str = "GET",
    definer: Optional[str] = None,
    target: Optional[str] = None,
    manager_name: Optional[str] = None,
    worker_name: Optional[str] = None,
    workers: Optional[List[str]] = None,
    delegation_strategy: Optional[str] = None,
    worker_roles: Optional[Dict[str, str]] = None,
    worker_role: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None,
) -> str:
    """Manager agent management: list, get info, create, remove, add/remove
    workers, HEAD, OPTIONS.

    Use op="list" (or op="GET") to list all managers with their worker
      counts and delegation strategies.
    Use op="GET" + manager_name=<name> to get detailed info for a single
      manager (aka the legacy get_info operation).
    Use op="create" (or op="POST" + definer="CREATE") + manager_name
      (+ workers?/delegation_strategy?/worker_roles?/metadata?) to create
      a new manager.
    Use op="create" + target="workers" + manager_name + worker_name
      (+ worker_role?) to add a worker to an existing manager.
    Use op="delete" (or op="DELETE") + manager_name to remove a manager.
    Use op="delete" + target="workers" + manager_name + worker_name to
      remove a worker from a manager.
    Use op="HEAD" (or "peek"/"summary") for a compact list.
    Use op="OPTIONS" (or "schema") to discover the tool's surface.

    Args:
        op: HTTP method or friendly verb (list/create/remove/add/delete).
        definer: Optional definer (CREATE for POST).
        target: None for manager itself, 'workers' for worker membership.
        manager_name: Name of the manager.
        worker_name: Name of the worker (for add/remove worker).
        workers: Initial worker list (for create).
        delegation_strategy: Delegation strategy string (for create).
        worker_roles: Mapping of worker name -> role string (for create).
        worker_role: Role for a single worker (for add worker).
        metadata: Additional metadata dict (for create).

    This is SP2's fourth method-semantic collection tool. It coexists with
    the legacy ``manage_managers`` tool and will eventually replace it.
    Note: ``delegate_task`` and ``execute_plan`` are action tools and remain
    separate; they will be handled by Task 14.
    """
    raw_params = {
        "target": target,
        "manager_name": manager_name,
        "worker_name": worker_name,
        "workers": workers,
        "delegation_strategy": delegation_strategy,
        "worker_roles": worker_roles,
        "worker_role": worker_role,
        "metadata": metadata,
    }
    params = {k: v for k, v in raw_params.items() if v is not None}

    return await _dispatcher.dispatch(ctx=ctx, op=op, definer=definer, **params)


def register(mcp):
    """Register the managers dispatcher tool.

    Named ``managers`` to coexist with the legacy ``manage_managers``
    tool during the SP2 coexistence period. Final cutover (renaming to
    ``managers``) happens at the end of SP2.
    """
    mcp.tool(name="managers")(managers)
