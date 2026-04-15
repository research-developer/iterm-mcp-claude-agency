"""iTerm MCP tool modules.

Each module contains tool functions for a specific domain and exposes
a register(mcp) function that registers them with the FastMCP instance.
"""


def register_all(mcp):
    """Register all tool modules with the FastMCP instance.

    Called from fastmcp_server.py after mcp creation and before run().
    """
    from . import (
        memory,
        agent_hooks,
        telemetry,
        workflows,
        services,
        roles,
        notifications,
        wait,
        feedback,
        managers,
        agents,
        control,
        monitoring,
        modifications,
        orchestration,
        commands,
        sessions,
        sessions_v2,  # SP2 method-semantic tool (coexists with legacy sessions).
        agents_v2,    # SP2 method-semantic tool (coexists with legacy agents).
        teams_v2,     # SP2 method-semantic tool (coexists with legacy manage_teams).
        managers_v2,  # SP2 method-semantic tool (coexists with legacy manage_managers).
        feedback_v2,  # SP2 method-semantic tool (coexists with legacy feedback tools).
        memory_v2,    # SP2 method-semantic tool (coexists with legacy manage_memory).
    )

    _MODULES = [
        memory, agent_hooks, telemetry, workflows,
        services, roles, notifications, wait,
        feedback, managers, agents, control,
        monitoring, modifications, orchestration,
        commands, sessions, sessions_v2, agents_v2,
        teams_v2, managers_v2, feedback_v2, memory_v2,
    ]

    for mod in _MODULES:
        mod.register(mcp)
