"""Centralized response serialization for MCP tools.

SP1 shipped `ok_json()` for token-efficient model serialization.
SP2 adds the envelope format used by method-semantic tools. As of the
fb-20260424-157473f7 item #13 fix, the envelope helpers return native
``dict`` so FastMCP can produce structured tool output (no double JSON).
"""
from typing import Any, Optional

from pydantic import BaseModel

from iterm_mcpy.errors import ErrorCode, ToolError


def ok_json(model: BaseModel) -> str:
    """Serialize a Pydantic model to JSON, excluding None fields.

    Args:
        model: Any Pydantic BaseModel response instance.

    Returns:
        JSON string with indent=2, no null fields.
    """
    return model.model_dump_json(indent=2, exclude_none=True)


def ok_envelope(
    method: str,
    data: Any,
    definer: Optional[str] = None,
) -> dict[str, Any]:
    """Build a successful tool result in the SP2 envelope.

    Envelope shape: ``{"method", "definer"?, "ok": true, "data"}``

    Returns a native ``dict``; FastMCP serializes it for the wire as
    ``structuredContent`` plus a JSON-stringified ``TextContent`` block.
    Returning ``dict`` (as opposed to a stringified envelope) avoids the
    double-encoded ``{"result": "<JSON>"}`` shape MCP clients surface
    when tool functions are typed ``-> str``. See fb-20260424-157473f7
    item #13.

    Args:
        method: The HTTP method that ran (normalized uppercase).
        data: Response payload — Pydantic model, list thereof, dict, or scalar.
        definer: Optional definer verb (for POST/PUT/PATCH). Omitted if None.

    Returns:
        Envelope dict (not a JSON string).
    """
    payload: dict[str, Any] = {"method": method, "ok": True}
    if definer is not None:
        payload["definer"] = definer
    payload["data"] = _to_jsonable(data)
    return payload


def err_envelope(
    method: str,
    error,
    definer: Optional[str] = None,
) -> dict[str, Any]:
    """Build an error result in the SP2 envelope.

    Envelope shape::

        {"method", "definer"?, "ok": false,
         "error": {"code", "message", "hint"?}}

    The ``error`` argument can be a :class:`iterm_mcpy.errors.ToolError`
    (preferred) or a bare string (legacy). Bare strings are wrapped as
    ``ToolError(INTERNAL, message=<str>)`` so the response shape is
    uniform regardless of caller migration state. See
    fb-20260424-157473f7 item #1b.

    Returns a native ``dict``; FastMCP handles serialization.

    Args:
        method: The HTTP method that was attempted.
        error: Either a ``ToolError`` or a bare human-readable string.
        definer: Optional definer verb (if it was resolved before the error).

    Returns:
        Envelope dict (not a JSON string).
    """
    if isinstance(error, str):
        error = ToolError(ErrorCode.INTERNAL, error)

    payload: dict[str, Any] = {"method": method, "ok": False}
    if definer is not None:
        payload["definer"] = definer
    payload["error"] = error.to_dict()
    return payload


def project_head(model_or_list: Any) -> Any:
    """Project a Pydantic model (or list) to its HEAD_FIELDS subset.

    When HEAD is requested, tools fetch the same data as GET but return
    only a compact subset. Each model declares its compact fields as
    `HEAD_FIELDS: ClassVar[set[str]]`. If undeclared, falls back to the
    first two scalar fields.

    Non-BaseModel inputs (dicts, strings, numbers) pass through unchanged.

    Args:
        model_or_list: A BaseModel, list of BaseModels, or any other value.

    Returns:
        The projected dict (or list of dicts), or the original value if
        not a BaseModel.
    """
    if isinstance(model_or_list, list):
        return [project_head(m) for m in model_or_list]
    if not isinstance(model_or_list, BaseModel):
        return model_or_list

    cls = type(model_or_list)
    head_fields = getattr(cls, "HEAD_FIELDS", None)
    if not head_fields:
        head_fields = _fallback_head_fields(cls)

    return model_or_list.model_dump(include=head_fields, exclude_none=True)


def options_schema(
    collection: str,
    methods: dict,
    sub_resources: Optional[list[str]] = None,
) -> dict:
    """Build the OPTIONS response schema for a collection tool.

    OPTIONS is self-describing: it tells callers what methods, definers,
    and sub-resources the collection supports.

    Args:
        collection: Name of the collection (e.g., "sessions").
        methods: Per-method metadata dict.
        sub_resources: Optional list of sub-resources the collection exposes.

    Returns:
        Schema dict suitable for passing to ok_envelope().
    """
    schema: dict[str, Any] = {"collection": collection, "methods": methods}
    if sub_resources:
        schema["sub_resources"] = list(sub_resources)
    return schema


# --- internals ---

_SCALAR_TYPES = (str, int, float, bool)


def _fallback_head_fields(cls: type) -> set[str]:
    """Pick the first two scalar fields for models that don't declare HEAD_FIELDS."""
    scalar_names: list[str] = []
    for name, field_info in cls.model_fields.items():
        ann = field_info.annotation
        if ann in _SCALAR_TYPES:
            scalar_names.append(name)
        elif hasattr(ann, "__args__") and any(a in _SCALAR_TYPES for a in ann.__args__):
            scalar_names.append(name)
        if len(scalar_names) >= 2:
            break
    return set(scalar_names)


def _to_jsonable(obj: Any) -> Any:
    """Convert Pydantic models (recursively) to JSON-serializable structures.

    Applies `exclude_none=True` and `mode='json'` to every model dumped so
    types like ``datetime`` and ``UUID`` come out as ISO strings rather than
    raw Python objects (which the stdlib ``json`` encoder cannot handle).
    """
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json", exclude_none=True)
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj
