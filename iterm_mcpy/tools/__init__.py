"""iTerm MCP tool modules (SP2 method-semantic surface).

Each module exposes a ``register(mcp)`` function that registers its
single tool with the FastMCP instance. The 15 tools together cover the
full method-semantic API:

  Collections (9): sessions, agents, teams, managers, feedback, memory,
                   services, roles, workflows
  Actions (6):     messages, orchestrate, delegate, wait_for, subscribe,
                   telemetry
"""


def register_all(mcp):
    """Register all 15 SP2 tools with the FastMCP instance.

    Called from fastmcp_server.py after mcp creation and before run().
    """
    from . import (
        sessions, agents, teams, managers,
        feedback, memory, services, roles, workflows,
        messages, orchestrate, delegate, wait_for, subscribe, telemetry,
    )

    _MODULES = [
        sessions, agents, teams, managers,
        feedback, memory, services, roles, workflows,
        messages, orchestrate, delegate, wait_for, subscribe, telemetry,
    ]

    for mod in _MODULES:
        mod.register(mcp)
