"""Memory store tools.

Provides the manage_memory tool that consolidates 8 memory operations
(store, retrieve, search, list_keys, list_namespaces, delete, clear, stats)
into a single tool. Includes private validators for safe namespace and key
characters.
"""

import re
from typing import List

from mcp.server.fastmcp import Context

from core.models import (
    ManageMemoryRequest,
    ManageMemoryResponse,
)

# Pattern for safe namespace and key characters
# Allows alphanumeric, underscore, hyphen, and dot
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


async def manage_memory(request: ManageMemoryRequest, ctx: Context) -> str:
    """Unified memory store operations - consolidates 8 memory tools into one.

    Operations:
    - store: Save a value (requires namespace, key, value; optional metadata)
    - retrieve: Get a value (requires namespace, key)
    - search: Full-text search (requires namespace, query; optional limit)
    - list_keys: List all keys in namespace (requires namespace)
    - list_namespaces: List namespaces (optional namespace as prefix filter)
    - delete: Delete a key (requires namespace, key)
    - clear: Clear namespace (requires namespace, confirm=True)
    - stats: Get store statistics (no params required)

    Args:
        request: ManageMemoryRequest with operation and relevant parameters

    Returns:
        JSON with operation result
    """
    memory_store_instance = ctx.request_context.lifespan_context.get("memory_store")
    logger = ctx.request_context.lifespan_context["logger"]

    if not memory_store_instance:
        return ManageMemoryResponse(
            operation=request.operation,
            success=False,
            error="Memory store not initialized"
        ).model_dump_json(indent=2, exclude_none=True)

    try:
        op = request.operation

        # STORE operation
        if op == "store":
            if not request.namespace:
                raise ValueError("namespace is required for store operation")
            if not request.key:
                raise ValueError("key is required for store operation")
            if request.value is None:
                raise ValueError("value is required for store operation")

            _validate_namespace(request.namespace)
            _validate_key(request.key)
            ns_tuple = tuple(request.namespace)
            await memory_store_instance.store(ns_tuple, request.key, request.value, request.metadata)
            logger.info(f"Stored memory: {'/'.join(request.namespace)}/{request.key}")

            return ManageMemoryResponse(
                operation=op,
                success=True,
                data={
                    "status": "stored",
                    "namespace": request.namespace,
                    "key": request.key,
                    "metadata": request.metadata or {}
                }
            ).model_dump_json(indent=2, exclude_none=True)

        # RETRIEVE operation
        elif op == "retrieve":
            if not request.namespace:
                raise ValueError("namespace is required for retrieve operation")
            if not request.key:
                raise ValueError("key is required for retrieve operation")

            _validate_namespace(request.namespace)
            _validate_key(request.key)
            ns_tuple = tuple(request.namespace)
            memory = await memory_store_instance.retrieve(ns_tuple, request.key)

            if memory:
                logger.info(f"Retrieved memory: {'/'.join(request.namespace)}/{request.key}")
                return ManageMemoryResponse(
                    operation=op,
                    success=True,
                    data={
                        "found": True,
                        "key": memory.key,
                        "value": memory.value,
                        "timestamp": memory.timestamp.isoformat(),
                        "metadata": memory.metadata,
                        "namespace": list(memory.namespace)
                    }
                ).model_dump_json(indent=2, exclude_none=True)
            else:
                logger.info(f"Memory not found: {'/'.join(request.namespace)}/{request.key}")
                return ManageMemoryResponse(
                    operation=op,
                    success=True,
                    data={"found": False, "namespace": request.namespace, "key": request.key}
                ).model_dump_json(indent=2, exclude_none=True)

        # SEARCH operation
        elif op == "search":
            if not request.namespace:
                raise ValueError("namespace is required for search operation")
            if not request.query:
                raise ValueError("query is required for search operation")

            _validate_namespace(request.namespace)
            ns_tuple = tuple(request.namespace)
            results = await memory_store_instance.search(ns_tuple, request.query, request.limit)
            logger.info(f"Memory search '{request.query}' in {'/'.join(request.namespace)}: {len(results)} results")

            return ManageMemoryResponse(
                operation=op,
                success=True,
                data={
                    "query": request.query,
                    "namespace": request.namespace,
                    "count": len(results),
                    "results": [
                        {
                            "key": r.memory.key,
                            "value": r.memory.value,
                            "score": r.score,
                            "match_context": r.match_context,
                            "timestamp": r.memory.timestamp.isoformat(),
                            "metadata": r.memory.metadata,
                            "namespace": list(r.memory.namespace)
                        }
                        for r in results
                    ]
                }
            ).model_dump_json(indent=2, exclude_none=True)

        # LIST_KEYS operation
        elif op == "list_keys":
            if not request.namespace:
                raise ValueError("namespace is required for list_keys operation")

            _validate_namespace(request.namespace)
            ns_tuple = tuple(request.namespace)
            keys = await memory_store_instance.list_keys(ns_tuple)
            logger.info(f"Listed {len(keys)} keys in namespace {'/'.join(request.namespace)}")

            return ManageMemoryResponse(
                operation=op,
                success=True,
                data={"namespace": request.namespace, "count": len(keys), "keys": keys}
            ).model_dump_json(indent=2, exclude_none=True)

        # LIST_NAMESPACES operation
        elif op == "list_namespaces":
            if request.namespace:
                _validate_namespace(request.namespace)
            prefix_tuple = tuple(request.namespace) if request.namespace else None
            namespaces = await memory_store_instance.list_namespaces(prefix_tuple)
            logger.info(f"Listed {len(namespaces)} namespaces")

            return ManageMemoryResponse(
                operation=op,
                success=True,
                data={
                    "prefix": request.namespace,
                    "count": len(namespaces),
                    "namespaces": [list(ns) for ns in namespaces]
                }
            ).model_dump_json(indent=2, exclude_none=True)

        # DELETE operation
        elif op == "delete":
            if not request.namespace:
                raise ValueError("namespace is required for delete operation")
            if not request.key:
                raise ValueError("key is required for delete operation")

            _validate_namespace(request.namespace)
            _validate_key(request.key)
            ns_tuple = tuple(request.namespace)
            deleted = await memory_store_instance.delete(ns_tuple, request.key)

            if deleted:
                logger.info(f"Deleted memory: {'/'.join(request.namespace)}/{request.key}")
            else:
                logger.info(f"Memory not found for deletion: {'/'.join(request.namespace)}/{request.key}")

            return ManageMemoryResponse(
                operation=op,
                success=True,
                data={
                    "deleted": deleted,
                    "namespace": request.namespace,
                    "key": request.key,
                    "message": None if deleted else "Memory not found"
                }
            ).model_dump_json(indent=2, exclude_none=True)

        # CLEAR operation
        elif op == "clear":
            if not request.namespace:
                raise ValueError("namespace is required for clear operation")

            _validate_namespace(request.namespace)

            if not request.confirm:
                return ManageMemoryResponse(
                    operation=op,
                    success=False,
                    error="Confirmation required. Set confirm=True to clear namespace. This permanently deletes all memories.",
                    data={"namespace": request.namespace}
                ).model_dump_json(indent=2, exclude_none=True)

            ns_tuple = tuple(request.namespace)
            count = await memory_store_instance.clear_namespace(ns_tuple)
            logger.info(f"Cleared namespace {'/'.join(request.namespace)}: {count} memories deleted")

            return ManageMemoryResponse(
                operation=op,
                success=True,
                data={"cleared": True, "namespace": request.namespace, "deleted_count": count}
            ).model_dump_json(indent=2, exclude_none=True)

        # STATS operation
        elif op == "stats":
            stats = await memory_store_instance.get_stats()
            logger.info("Retrieved memory store stats")

            return ManageMemoryResponse(
                operation=op,
                success=True,
                data=stats
            ).model_dump_json(indent=2, exclude_none=True)

        else:
            return ManageMemoryResponse(
                operation=op,
                success=False,
                error=f"Unknown operation: {op}"
            ).model_dump_json(indent=2, exclude_none=True)

    except Exception as e:
        logger.error(f"Error in manage_memory ({request.operation}): {e}")
        return ManageMemoryResponse(
            operation=request.operation,
            success=False,
            error=str(e)
        ).model_dump_json(indent=2, exclude_none=True)


def register(mcp):
    """Register memory store tools with the FastMCP instance."""
    mcp.tool()(manage_memory)
