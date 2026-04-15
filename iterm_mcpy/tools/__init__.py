"""iTerm MCP tool modules.

Each module contains tool functions for a specific domain and exposes
a register(mcp) function that registers them with the FastMCP instance.
"""


def register_all(mcp):
    """Register all tool modules with the FastMCP instance.

    Called from fastmcp_server.py after mcp creation and before run().
    Tool modules are added incrementally; this file is updated as each
    batch of modules is extracted from fastmcp_server.py.
    """
    from . import (
        memory,
        agent_hooks,
        telemetry,
        workflows,
        services,
    )

    _MODULES = [
        memory, agent_hooks, telemetry, workflows, services,
    ]

    for mod in _MODULES:
        mod.register(mcp)
