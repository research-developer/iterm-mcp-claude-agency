"""SP2 method-semantic `memory` tool — Task 9.

Sixth SP2 collection tool (after sessions + agents + teams +
managers + feedback). Replaces the legacy ``manage_memory`` tool's
8 operations:

    - store            -> POST + CREATE      /memory
    - retrieve         -> GET                /memory
    - search           -> GET                /memory  (target='search')
    - list_keys        -> GET                /memory  (target='keys')
    - list_namespaces  -> GET                /memory  (target='namespaces')
    - stats            -> GET                /memory  (target='stats')
    - delete           -> DELETE             /memory/{namespace}/{key}
    - clear            -> DELETE             /memory  (target='namespace',
                                                       confirm=true)

Registered under the provisional name ``memory`` to coexist with the
legacy ``manage_memory`` tool; the cutover (rename to ``memory`` and
unregister the legacy tool) happens at the end of SP2.

The module-local validators ``_validate_namespace`` and ``_validate_key``
enforce safe-character conventions on namespace parts and keys before
hitting the memory store.
"""
import re
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context

from iterm_mcpy.dispatcher import MethodDispatcher


# Pattern for safe namespace and key characters.
# Allows alphanumeric, underscore, hyphen, and dot.
_SAFE_MEMORY_PATTERN = re.compile(r'^[a-zA-Z0-9_\-\.]+$')


def _validate_namespace(namespace: List[str]) -> None:
    """Validate namespace parts contain only safe characters.

    Args:
        namespace: List of namespace parts

    Raises:
        ValueError: If any part contains invalid characters
    """
    if not namespace:
        return  # Empty namespace is valid (root)

    for part in namespace:
        if not part:
            raise ValueError("Namespace parts cannot be empty strings")
        if not _SAFE_MEMORY_PATTERN.match(part):
            raise ValueError(
                f"Invalid namespace part '{part}': only alphanumeric, underscore, hyphen, and dot allowed"
            )


def _validate_key(key: str) -> None:
    """Validate key contains only safe characters.

    Args:
        key: The memory key

    Raises:
        ValueError: If key contains invalid characters
    """
    if not key:
        raise ValueError("Key cannot be empty")
    if not _SAFE_MEMORY_PATTERN.match(key):
        raise ValueError(
            f"Invalid key '{key}': only alphanumeric, underscore, hyphen, and dot allowed"
        )


class MemoryDispatcher(MethodDispatcher):
    """Dispatcher for the `memory` collection (SP2 method-semantic)."""

    collection = "memory"

    METHODS = {
        "GET": {
            "aliases": ["retrieve", "get", "list", "search", "read", "query"],
            "params": [
                "target=None | 'search' | 'keys' | 'namespaces' | 'stats'",
                # retrieve (target=None + namespace + key):
                "namespace?=[str]",
                "key?",
                # search (target='search'):
                "query?",
                "limit?=10",
                # list_namespaces (target='namespaces'):
                "prefix?=[str]  (via namespace param)",
            ],
            "description": (
                "Retrieve a value (no target), run full-text search "
                "(target='search'), list keys in a namespace "
                "(target='keys'), list namespaces with optional prefix "
                "(target='namespaces'), or get store stats (target='stats')."
            ),
        },
        "POST": {
            "definers": {
                "CREATE": {
                    "aliases": ["store", "create", "add"],
                    "params": [
                        "namespace",
                        "key",
                        "value",
                        "metadata?={...}",
                    ],
                    "description": (
                        "Store a value at namespace/key with optional metadata."
                    ),
                },
            },
        },
        "DELETE": {
            "aliases": ["delete", "remove", "clear"],
            "params": [
                "target=None | target='namespace'",
                # target=None (delete single key):
                "namespace",
                "key?",
                # target='namespace' (clear namespace):
                "confirm=true (required for clear)",
            ],
            "description": (
                "Delete a single key (no target + namespace + key), or "
                "clear an entire namespace (target='namespace' + "
                "confirm=true)."
            ),
        },
        "HEAD": {"compact_fields": ["namespace", "key"]},
        "OPTIONS": {"description": "Return this schema."},
    }

    sub_resources = ["namespaces", "keys", "stats"]

    # -------------------------------- GET -------------------------------- #

    async def on_get(self, ctx, **params):
        """Route GET by `target` — retrieve / search / keys / namespaces / stats."""
        target = params.get("target")
        if target == "search":
            return await self._search(ctx, **params)
        if target == "keys":
            return await self._list_keys(ctx, **params)
        if target == "namespaces":
            return await self._list_namespaces(ctx, **params)
        if target == "stats":
            return await self._stats(ctx, **params)
        # Default: retrieve by namespace + key
        return await self._retrieve(ctx, **params)

    async def _retrieve(self, ctx, **params):
        """GET /memory — retrieve a memory by namespace + key."""
        namespace = params.get("namespace")
        key = params.get("key")
        if not namespace:
            raise ValueError("retrieve requires namespace")
        if not key:
            raise ValueError("retrieve requires key")

        _validate_namespace(namespace)
        _validate_key(key)

        lifespan = ctx.request_context.lifespan_context
        memory_store = lifespan["memory_store"]
        logger = lifespan["logger"]

        memory = await memory_store.retrieve(tuple(namespace), key)

        if memory:
            logger.info(
                f"memory GET: retrieved {'/'.join(namespace)}/{key}"
            )
            return {
                "found": True,
                "key": memory.key,
                "value": memory.value,
                "timestamp": memory.timestamp.isoformat(),
                "metadata": memory.metadata,
                "namespace": list(memory.namespace),
            }
        logger.info(
            f"memory GET: not found {'/'.join(namespace)}/{key}"
        )
        return {"found": False, "namespace": namespace, "key": key}

    async def _search(self, ctx, **params):
        """GET /memory?target=search — full-text search within a namespace."""
        namespace = params.get("namespace")
        query = params.get("query")
        limit = params.get("limit", 10)
        if not namespace:
            raise ValueError("search requires namespace")
        if not query:
            raise ValueError("search requires query")

        _validate_namespace(namespace)

        lifespan = ctx.request_context.lifespan_context
        memory_store = lifespan["memory_store"]
        logger = lifespan["logger"]

        results = await memory_store.search(tuple(namespace), query, limit)
        logger.info(
            f"memory GET search: '{query}' in "
            f"{'/'.join(namespace)} -> {len(results)} results"
        )
        return {
            "query": query,
            "namespace": namespace,
            "count": len(results),
            "results": [
                {
                    "key": r.memory.key,
                    "value": r.memory.value,
                    "score": r.score,
                    "match_context": r.match_context,
                    "timestamp": r.memory.timestamp.isoformat(),
                    "metadata": r.memory.metadata,
                    "namespace": list(r.memory.namespace),
                }
                for r in results
            ],
        }

    async def _list_keys(self, ctx, **params):
        """GET /memory?target=keys — list all keys in a namespace."""
        namespace = params.get("namespace")
        if not namespace:
            raise ValueError("list_keys requires namespace")

        _validate_namespace(namespace)

        lifespan = ctx.request_context.lifespan_context
        memory_store = lifespan["memory_store"]
        logger = lifespan["logger"]

        keys = await memory_store.list_keys(tuple(namespace))
        logger.info(
            f"memory GET keys: {len(keys)} in {'/'.join(namespace)}"
        )
        return {"namespace": namespace, "count": len(keys), "keys": keys}

    async def _list_namespaces(self, ctx, **params):
        """GET /memory?target=namespaces — list namespaces (optional prefix)."""
        namespace = params.get("namespace")
        if namespace:
            _validate_namespace(namespace)

        lifespan = ctx.request_context.lifespan_context
        memory_store = lifespan["memory_store"]
        logger = lifespan["logger"]

        prefix_tuple = tuple(namespace) if namespace else None
        namespaces = await memory_store.list_namespaces(prefix_tuple)
        logger.info(
            f"memory GET namespaces: {len(namespaces)} listed"
        )
        return {
            "prefix": namespace,
            "count": len(namespaces),
            "namespaces": [list(ns) for ns in namespaces],
        }

    async def _stats(self, ctx, **params):
        """GET /memory?target=stats — memory store statistics."""
        lifespan = ctx.request_context.lifespan_context
        memory_store = lifespan["memory_store"]
        logger = lifespan["logger"]

        stats = await memory_store.get_stats()
        logger.info("memory GET stats: returning stats")
        return stats

    # ------------------------------- POST -------------------------------- #

    async def on_post(self, ctx, definer, **params):
        """Route POST by (definer) — only CREATE (store) is supported."""
        if definer == "CREATE":
            return await self._store(ctx, **params)
        raise NotImplementedError(
            f"POST+{definer} not yet implemented for memory"
        )

    async def _store(self, ctx, **params):
        """POST /memory (CREATE) — store a value at namespace/key."""
        namespace = params.get("namespace")
        key = params.get("key")
        value = params.get("value")
        metadata = params.get("metadata")

        if not namespace:
            raise ValueError("store requires namespace")
        if not key:
            raise ValueError("store requires key")
        if value is None:
            raise ValueError("store requires value")

        _validate_namespace(namespace)
        _validate_key(key)

        lifespan = ctx.request_context.lifespan_context
        memory_store = lifespan["memory_store"]
        logger = lifespan["logger"]

        await memory_store.store(tuple(namespace), key, value, metadata)
        logger.info(f"memory CREATE: stored {'/'.join(namespace)}/{key}")
        return {
            "status": "stored",
            "namespace": namespace,
            "key": key,
            "metadata": metadata or {},
        }

    # ------------------------------- DELETE ------------------------------ #

    async def on_delete(self, ctx, **params):
        """Route DELETE by `target` — delete single key or clear namespace."""
        target = params.get("target")
        if target == "namespace":
            return await self._clear_namespace(ctx, **params)
        return await self._delete_key(ctx, **params)

    async def _delete_key(self, ctx, **params):
        """DELETE /memory/{namespace}/{key} — delete a single memory."""
        namespace = params.get("namespace")
        key = params.get("key")
        if not namespace:
            raise ValueError("delete requires namespace")
        if not key:
            raise ValueError("delete requires key")

        _validate_namespace(namespace)
        _validate_key(key)

        lifespan = ctx.request_context.lifespan_context
        memory_store = lifespan["memory_store"]
        logger = lifespan["logger"]

        deleted = await memory_store.delete(tuple(namespace), key)
        if deleted:
            logger.info(
                f"memory DELETE: removed {'/'.join(namespace)}/{key}"
            )
        else:
            logger.info(
                f"memory DELETE: not found {'/'.join(namespace)}/{key}"
            )
        return {
            "deleted": deleted,
            "namespace": namespace,
            "key": key,
            "message": None if deleted else "Memory not found",
        }

    async def _clear_namespace(self, ctx, **params):
        """DELETE /memory?target=namespace — clear an entire namespace."""
        namespace = params.get("namespace")
        confirm = params.get("confirm", False)
        if not namespace:
            raise ValueError("clear namespace requires namespace")

        _validate_namespace(namespace)

        if not confirm:
            raise ValueError(
                "Confirmation required. Set confirm=true to clear the "
                "namespace. This permanently deletes all memories within it."
            )

        lifespan = ctx.request_context.lifespan_context
        memory_store = lifespan["memory_store"]
        logger = lifespan["logger"]

        count = await memory_store.clear_namespace(tuple(namespace))
        logger.info(
            f"memory DELETE namespace: cleared "
            f"{'/'.join(namespace)} ({count} memories)"
        )
        return {
            "cleared": True,
            "namespace": namespace,
            "deleted_count": count,
        }


_dispatcher = MemoryDispatcher()


# Legacy `manage_memory` op strings that aren't in the central VERB_ATLAS.
# Mapped locally to (method, definer, implied_target) so callers can keep
# using the legacy vocabulary during the SP2 coexistence period. Verbs that
# already exist in VERB_ATLAS (retrieve / search / delete) pass through
# untouched.
_LEGACY_OP_MAP: dict[str, tuple[str, Optional[str], Optional[str]]] = {
    # store -> POST+CREATE
    "store": ("POST", "CREATE", None),
    # list_keys / keys -> GET target='keys'
    "list_keys": ("GET", None, "keys"),
    # list_namespaces / namespaces -> GET target='namespaces'
    "list_namespaces": ("GET", None, "namespaces"),
    # stats -> GET target='stats'
    "stats": ("GET", None, "stats"),
    # clear -> DELETE target='namespace' (destructive, needs confirm=true)
    "clear": ("DELETE", None, "namespace"),
}


async def memory(
    ctx: Context,
    op: str = "GET",
    definer: Optional[str] = None,
    target: Optional[str] = None,
    namespace: Optional[List[str]] = None,
    key: Optional[str] = None,
    value: Optional[Any] = None,
    metadata: Optional[Dict[str, Any]] = None,
    query: Optional[str] = None,
    limit: int = 10,
    confirm: bool = False,
) -> str:
    """Memory store ops: retrieve, store, search, list, delete, clear, stats.

    Use op="retrieve" (or op="GET") + namespace + key to fetch one value.
    Use op="search" (or op="GET" + target="search") + namespace + query
      (+ limit?) to run a full-text search.
    Use op="GET" + target="keys" + namespace to list keys in a namespace.
    Use op="GET" + target="namespaces" (+ namespace as prefix?) to list
      namespaces, optionally filtered by a namespace prefix.
    Use op="GET" + target="stats" to get memory store statistics.
    Use op="store" (or op="POST" + definer="CREATE") + namespace + key +
      value (+ metadata?) to store a value.
    Use op="delete" (or op="DELETE") + namespace + key to delete a single
      memory.
    Use op="DELETE" + target="namespace" + namespace + confirm=true to
      clear an entire namespace (destructive).
    Use op="HEAD" (or "peek") for a compact summary envelope.
    Use op="OPTIONS" (or "schema") to discover the tool's surface.

    Args:
        op: HTTP method or friendly verb.
        definer: Explicit definer (only CREATE applies here).
        target: Sub-resource: 'search', 'keys', 'namespaces', 'stats' for
            GET, or 'namespace' for DELETE. Omit for retrieve / delete key
            / store.
        namespace: Hierarchical namespace (e.g., ['project-x', 'agent']).
            For list_namespaces, acts as an optional prefix filter.
        key: Key within the namespace (retrieve / store / delete).
        value: JSON-serializable value to store (store).
        metadata: Optional metadata dict attached to a stored value.
        query: Full-text search query (search).
        limit: Max results for search (default 10).
        confirm: Required True to clear a namespace (target='namespace').

    This is SP2's sixth method-semantic collection tool. It coexists with
    the legacy ``manage_memory`` tool and will eventually replace it.
    """
    # Translate memory-specific legacy op strings (store/clear/list_keys/
    # list_namespaces/stats) to their (method, definer, target) triple so
    # the shared dispatcher + VERB_ATLAS stays clean.
    legacy = _LEGACY_OP_MAP.get(op.lower())
    if legacy is not None:
        method, mapped_definer, mapped_target = legacy
        op = method
        # Explicit definer wins over the mapping's default.
        if mapped_definer is not None and definer is None:
            definer = mapped_definer
        # Explicit target wins over the mapping's implied target.
        if mapped_target is not None and target is None:
            target = mapped_target

    raw_params = {
        "target": target,
        "namespace": namespace,
        "key": key,
        "value": value,
        "metadata": metadata,
        "query": query,
        "limit": limit,
        "confirm": confirm,
    }
    # Keep `limit` and `confirm` even at defaults — handlers need them.
    # Filter out params the user explicitly didn't set (None values).
    params = {k: v for k, v in raw_params.items() if v is not None}

    return await _dispatcher.dispatch(ctx=ctx, op=op, definer=definer, **params)


def register(mcp):
    """Register the memory dispatcher tool.

    Named ``memory`` to coexist with the legacy ``manage_memory`` tool
    during the SP2 coexistence period. Final cutover (renaming to
    ``memory``) happens at the end of SP2.
    """
    mcp.tool(name="memory")(memory)
