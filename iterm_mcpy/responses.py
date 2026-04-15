"""Centralized response serialization for MCP tools.

All tool responses should use ok_json() to ensure consistent
formatting and token-efficient serialization (no null fields).
"""
from pydantic import BaseModel


def ok_json(model: BaseModel) -> str:
    """Serialize a Pydantic model to JSON, excluding None fields.

    Args:
        model: Any Pydantic BaseModel response instance.

    Returns:
        JSON string with indent=2, no null fields.
    """
    return model.model_dump_json(indent=2, exclude_none=True)
