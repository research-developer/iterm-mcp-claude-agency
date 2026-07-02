"""iTerm MCP tool modules (SP2 method-semantic surface).

Each module exposes a ``register(mcp)`` function that registers its
single tool with the FastMCP instance. The 17 tools together cover the
full method-semantic API:

  Collections (9): sessions, agents, teams, managers, feedback, memory,
                   services, roles, workflows
  Actions (8):     messages, orchestrate, delegate, wait_for, subscribe,
                   telemetry, bus, projects
"""


def register_all(mcp):
    """Register all 17 SP2 tools with the FastMCP instance.

    Called from fastmcp_server.py after mcp creation and before run().
    """
    from . import (
        sessions, agents, teams, managers,
        feedback, memory, services, roles, workflows,
        messages, orchestrate, delegate, wait_for, subscribe, telemetry,
        bus, projects,
    )

    _MODULES = [
        sessions, agents, teams, managers,
        feedback, memory, services, roles, workflows,
        messages, orchestrate, delegate, wait_for, subscribe, telemetry,
        bus, projects,
    ]

    for mod in _MODULES:
        mod.register(mcp)
