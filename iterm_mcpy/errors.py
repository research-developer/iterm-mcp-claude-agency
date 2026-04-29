"""Structured error contract for iTerm MCP tools.

Replaces the bare `error: "<exception repr>"` shape with a typed
`{code, message, hint?}` envelope, addressing fb-20260424-157473f7
item #1b ("Errors are too terse and leak internals — wrap all errors as
{code, message, hint}").

The contract:

- `ErrorCode` is a stable string enum. Codes are part of the public API
  and clients can branch on them.
- `ToolError` is the exception tools raise. The dispatcher catches it
  and renders it via `err_envelope`.
- `ToolError.from_exception(exc)` maps common Python exceptions to the
  closest code; an existing `ToolError` passes through unchanged.

Migration is incremental: `responses.err_envelope` accepts either a
`ToolError` or a bare string. Bare strings are wrapped as
`ToolError(INTERNAL, message=<str>)` so the response shape is uniform
even on call sites that haven't been updated yet.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class ErrorCode(str, Enum):
    """Stable error codes returned in the `{code, message, hint?}` envelope."""

    INVALID_OP = "invalid_op"
    INVALID_TARGET = "invalid_target"
    INVALID_DEFINER = "invalid_definer"
    MISSING_PARAM = "missing_param"
    INVALID_PARAM = "invalid_param"
    SESSION_NOT_FOUND = "session_not_found"
    AGENT_NOT_FOUND = "agent_not_found"
    LOCKED = "locked"
    NOT_IMPLEMENTED = "not_implemented"
    INTERNAL = "internal"


class ToolError(Exception):
    """Exception type carrying a structured error.

    Tools should `raise ToolError(code, message, hint?)`; the dispatcher
    catches it and renders it via `err_envelope`.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        hint: Optional[str] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        """Serialize to the wire shape `{code, message, hint?}`."""
        out: dict = {"code": self.code.value, "message": self.message}
        if self.hint is not None:
            out["hint"] = self.hint
        return out

    @staticmethod
    def from_exception(exc: BaseException) -> "ToolError":
        """Map an arbitrary exception to a ToolError.

        Existing ToolErrors pass through unchanged (no double-wrap).
        Common Python exceptions get mapped to the closest code; anything
        else falls back to `INTERNAL`.
        """
        if isinstance(exc, ToolError):
            return exc

        if isinstance(exc, KeyError):
            key = exc.args[0] if exc.args else str(exc)
            return ToolError(
                ErrorCode.MISSING_PARAM,
                f"Missing required parameter: {key!r}",
            )

        if isinstance(exc, ValueError):
            return ToolError(ErrorCode.INVALID_PARAM, str(exc))

        # Pydantic ValidationError lives in pydantic; import lazily so this
        # module has no hard pydantic dependency.
        try:
            from pydantic import ValidationError
            if isinstance(exc, ValidationError):
                return ToolError(ErrorCode.INVALID_PARAM, str(exc))
        except ImportError:
            pass

        if isinstance(exc, NotImplementedError):
            return ToolError(ErrorCode.NOT_IMPLEMENTED, str(exc) or "Not implemented")

        return ToolError(ErrorCode.INTERNAL, str(exc) or exc.__class__.__name__)
