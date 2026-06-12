"""MCP server implementation for iTerm2 controller using the official MCP Python SDK.

This version supports parallel multi-session operations with agent/team management.
"""

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator, Any

from mcp.server.fastmcp import FastMCP

from iterm_mcpy.app_context import (
    AppContext,
    NotificationManager,  # re-export: callers historically import it from here
    get_app_context,
)


@asynccontextmanager
async def iterm_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Hand each client session a reference to the shared AppContext.

    The mcp SDK enters this once per client session. It must stay cheap and
    must NOT tear anything down on exit — other clients are still connected.
    """
    yield await get_app_context()


# Create an MCP server
mcp = FastMCP(
    name="iTerm2 Controller",
    instructions="Control iTerm2 terminal sessions with parallel multi-agent orchestration",
    lifespan=iterm_lifespan,
    dependencies=["iterm2", "asyncio", "pydantic"]
)

# Register tool modules extracted from this file.
# See iterm_mcpy/tools/__init__.py
from iterm_mcpy.tools import register_all  # noqa: E402
register_all(mcp)


# ============================================================================
# OAUTH METADATA ENDPOINTS (for HTTP transport compatibility)
# ============================================================================
# These routes return proper JSON 404 responses for OAuth discovery endpoints.
# Without these, the MCP client receives plain text "Not Found" which it cannot
# parse as JSON, causing: "Invalid OAuth error response: SyntaxError"

from starlette.requests import Request
from starlette.responses import JSONResponse


@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def oauth_authorization_server_metadata(request: Request) -> JSONResponse:
    """Return JSON 404 for OAuth authorization server metadata.

    The MCP client checks this endpoint for OAuth configuration.
    Returning a proper JSON response allows the client to proceed without auth.
    """
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "error_description": "OAuth authorization server metadata not configured"
        }
    )


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_protected_resource_metadata(request: Request) -> JSONResponse:
    """Return JSON 404 for OAuth protected resource metadata (root path)."""
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "error_description": "OAuth protected resource metadata not configured"
        }
    )


@mcp.custom_route("/.well-known/oauth-protected-resource/mcp", methods=["GET"])
async def oauth_protected_resource_mcp_metadata(request: Request) -> JSONResponse:
    """Return JSON 404 for OAuth protected resource metadata (MCP path)."""
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "error_description": "OAuth protected resource metadata not configured"
        }
    )


# Shared helpers (resolve_session, execute_write_request, etc.) live in
# iterm_mcpy/helpers.py and are imported directly by the tool modules.
# See iterm_mcpy/helpers.py for the full list.


# All tool implementations live in iterm_mcpy/tools/ — see tools/__init__.py
# for the module list. The SP2 surface is 15 method-semantic tools:
#
# Collections (9):
#   sessions.py        — GET/HEAD/POST/PATCH/DELETE sessions + sub-resources
#                        (output, keys, tags, roles, locks, monitoring, splits,
#                         appearance, active, status)
#   agents.py          — register/list/remove agents; notifications; hooks;
#                        locks held by an agent
#   teams.py           — create/list/remove teams; assign/remove team agents
#   managers.py        — create/list/remove manager agents; add/remove workers
#   feedback.py        — submit/query/triage/fork feedback; config; triggers
#   memory.py          — store/retrieve/search/list/delete memories
#   services.py        — list/add/configure/start/stop/remove services
#   roles.py           — discover available roles (read-only catalog)
#   workflows.py       — trigger/list/history workflow events
#
# Actions (6):
#   messages.py        — send cascade / hierarchical messages between sessions
#   orchestrate.py     — orchestrate multi-step playbooks
#   delegate.py        — delegate a task or execute a plan through a manager
#   wait_for.py        — long-poll for an agent to complete
#   subscribe.py       — subscribe to terminal output patterns
#   telemetry.py       — start/stop the telemetry dashboard


# ============================================================================
# RESOURCES
# ============================================================================

@mcp.resource("terminal://{session_id}/output")
async def get_terminal_output(session_id: str) -> str:
    """Get output from a terminal session."""
    app = await get_app_context()
    terminal = app.terminal
    logger = app.logger

    try:
        session = await terminal.get_session_by_id(session_id)
        if not session:
            return f"No session found with ID: {session_id}"

        output = await session.get_screen_contents()
        return output
    except Exception as e:
        logger.error(f"Error getting terminal output: {e}")
        return f"Error: {e}"


@mcp.resource("terminal://{session_id}/info")
async def get_terminal_info(session_id: str) -> str:
    """Get information about a terminal session."""
    app = await get_app_context()
    terminal = app.terminal
    agent_registry = app.agent_registry
    logger = app.logger

    try:
        session = await terminal.get_session_by_id(session_id)
        if not session:
            return f"No session found with ID: {session_id}"

        agent = agent_registry.get_agent_by_session(session.id)

        info = {
            "name": session.name,
            "id": session.id,
            "persistent_id": session.persistent_id,
            "agent": agent.name if agent else None,
            "teams": agent.teams if agent else [],
            "is_processing": getattr(session, "is_processing", False),
            "is_monitoring": getattr(session, "is_monitoring", False),
            "max_lines": session.max_lines
        }

        return json.dumps(info, indent=2)
    except Exception as e:
        logger.error(f"Error getting terminal info: {e}")
        return f"Error: {e}"


@mcp.resource("terminal://sessions")
async def list_all_sessions_resource() -> str:
    """Get a list of all terminal sessions."""
    app = await get_app_context()
    terminal = app.terminal
    agent_registry = app.agent_registry
    logger = app.logger

    try:
        sessions = list(terminal.sessions.values())
        result = []

        for session in sessions:
            agent = agent_registry.get_agent_by_session(session.id)
            result.append({
                "id": session.id,
                "name": session.name,
                "persistent_id": session.persistent_id,
                "agent": agent.name if agent else None,
                "teams": agent.teams if agent else [],
                "is_processing": getattr(session, "is_processing", False),
                "is_monitoring": getattr(session, "is_monitoring", False)
            })

        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        return f"Error: {e}"


@mcp.resource("agents://all")
async def list_all_agents_resource() -> str:
    """Get a list of all registered agents."""
    app = await get_app_context()
    agent_registry = app.agent_registry
    logger = app.logger

    try:
        agents = agent_registry.list_agents()
        result = [
            {
                "name": a.name,
                "session_id": a.session_id,
                "teams": a.teams
            }
            for a in agents
        ]
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error listing agents: {e}")
        return f"Error: {e}"


@mcp.resource("teams://all")
async def list_all_teams_resource() -> str:
    """Get a list of all teams."""
    app = await get_app_context()
    agent_registry = app.agent_registry
    logger = app.logger

    try:
        teams = agent_registry.list_teams()
        result = [
            {
                "name": t.name,
                "description": t.description,
                "parent_team": t.parent_team,
                "member_count": len(agent_registry.list_agents(team=t.name))
            }
            for t in teams
        ]
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error listing teams: {e}")
        return f"Error: {e}"


# ============================================================================
# PROMPTS
# ============================================================================

@mcp.prompt("orchestrate_agents")
def orchestrate_agents_prompt(task: str) -> str:
    """Prompt for orchestrating multiple agents.

    Args:
        task: The task to orchestrate
    """
    return f"""
You're orchestrating multiple Claude agents through iTerm2 sessions.

Task: {task}

Use the following tools to coordinate:
- create_sessions: Create new sessions with agents
- write_to_sessions: Send commands to multiple sessions
- read_sessions: Read output from sessions
- send_cascade_message: Send hierarchical messages to teams/agents

Remember:
- Use teams for logical groupings
- Cascade messages from broad to specific
- Check for duplicates to avoid redundant work
"""


@mcp.prompt("monitor_team")
def monitor_team_prompt(team_name: str) -> str:
    """Prompt for monitoring a team of agents.

    Args:
        team_name: The team to monitor
    """
    return f"""
You're monitoring the '{team_name}' team of agents.

Use read_sessions with team='{team_name}' to check all members.
Watch for:
- Error messages
- Completion signals
- Progress indicators

Coordinate responses as needed using write_to_sessions or send_cascade_message.
"""


@mcp.resource("telemetry://dashboard")
async def telemetry_dashboard() -> str:
    """Get the current telemetry dashboard state as JSON."""
    app = await get_app_context()
    terminal = app.terminal
    telemetry = app.telemetry
    logger = app.logger

    try:
        state = telemetry.dashboard_state(terminal)
        return json.dumps(state, indent=2)
    except Exception as e:
        logger.error(f"Error getting telemetry dashboard: {e}")
        return json.dumps({"error": str(e)}, indent=2)


@mcp.resource("memory://stats")
async def memory_stats_resource() -> str:
    """Get memory store statistics as a resource."""
    app = await get_app_context()
    memory_store = app.memory_store
    logger = app.logger

    try:
        stats = await memory_store.get_stats()
        return json.dumps(stats, indent=2)
    except Exception as e:
        logger.error(f"Error getting memory stats resource: {e}")
        return json.dumps({"error": str(e)}, indent=2)


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Run the MCP server."""
    try:
        mcp.run()
    except KeyboardInterrupt:
        if os.environ.get("ITERM_MCP_CLEAN_EXIT"):
            return
        print("\nServer stopped by user", file=sys.stderr)
        os._exit(0)
    except Exception as e:
        print(f"Error running MCP server: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
