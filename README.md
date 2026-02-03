# iTerm MCP

[![CI](https://github.com/research-developer/iterm-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/research-developer/iterm-mcp/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/research-developer/iterm-mcp/branch/main/graph/badge.svg)](https://codecov.io/gh/research-developer/iterm-mcp)

A Python implementation for controlling iTerm2 terminal sessions with support for multiple panes and layouts. This implementation uses the iTerm2 Python API for improved reliability and functionality.

**Note:** This project provides multi-agent orchestration infrastructure, complementary to tools like [@steipete/claude-code-mcp](https://github.com/steipete/claude-code-mcp). See [docs/claude-code-mcp-analysis.md](docs/claude-code-mcp-analysis.md) for a detailed comparison.

## Status

✅ **gRPC Migration Complete** - Full gRPC server/client implementation with 17 RPC methods  
✅ **Multi-Pane Orchestration** - Parallel session operations with agent/team targeting  
✅ **Agent Registry** - Complete agent and team management with cascading messages  
✅ **Test Coverage** - 98 passing tests with 27.77% code coverage  
✅ **CI/CD** - Automated testing with coverage reporting

See [EPIC_STATUS.md](EPIC_STATUS.md) for detailed implementation status.

## Features

- Named terminal sessions with persistent identity across restarts
- Persistent session IDs for reconnection after interruptions
- Multiple pane layouts (single, horizontal split, vertical split, quad, etc.)
- Command execution and output capture with configurable line limits
- Real-time session monitoring with callback support
- Log management with filterable output using regex patterns
- Live output snapshots for LLM access with overflow handling
- Multiple session creation and parallel command execution
- Background process execution and status tracking
- Control character support (Ctrl+C, etc.)
- Lightweight telemetry with MCP resource + optional dashboard for pane and team health (see [docs/telemetry.md](docs/telemetry.md))
- **Role-based session specialization** with tool filtering and access control (see [docs/ROLES.md](docs/ROLES.md))
- **Git commit session tracking** with GitHub PR comment integration for agent notifications (see [docs/git-session-tracking.md](docs/git-session-tracking.md))

## Requirements

- Python 3.8+
- iTerm2 3.3+ with Python API enabled
- MCP Python SDK (1.3.0+)

## Installation

1. Clone this repository
2. Install dependencies:
```bash
pip install -e .
```

This will install the package with all required dependencies, including the MCP Python SDK.

## Project Structure

```
iterm-mcp/
├── pyproject.toml                # Python packaging configuration
├── core/                         # Core functionality
│   ├── __init__.py
│   ├── session.py                # iTerm session management
│   ├── terminal.py               # Terminal window/tab management
│   ├── layouts.py                # Predefined layouts
│   ├── agents.py                 # Agent/team registry
│   ├── roles.py                  # Role-based session specialization
│   └── models.py                 # Pydantic request/response models
├── iterm_mcpy/                   # Server implementations
│   ├── __init__.py
│   ├── fastmcp_server.py         # FastMCP implementation (recommended)
│   ├── grpc_server.py            # gRPC server implementation
│   ├── grpc_client.py            # gRPC client for programmatic access
│   └── iterm_mcp_pb2*.py         # Generated protobuf code
├── protos/                       # Protocol buffer definitions
│   └── iterm_mcp.proto
├── utils/                        # Utility functions
│   ├── __init__.py
│   └── logging.py                # Logging utilities
└── tests/                        # Test suite
```

## Development Setup

1. Clone the repository
2. Install dependencies:
   ```bash
   pip install -e ".[dev]"
   ```
3. Run tests:
   ```bash
   ./scripts/watch_tests.sh
   ```
4. Generate gRPC code (if modifying protos):
   ```bash
   python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. protos/iterm_mcp.proto
   ```

## Usage
### MCP Integration with the Official Python SDK

We provide two server implementations:
1. **FastMCP Implementation** (recommended) - Uses the official MCP Python SDK
2. **Legacy Implementation** - Custom MCP server implementation (for backward compatibility)

### Running the MCP Server

```bash
# Run the FastMCP server (recommended)
python -m iterm_mcp_python.server.main

# Run the legacy MCP server
python -m iterm_mcp_python.server.main --legacy

# Run the demo (not MCP server)
python -m iterm_mcp_python.server.main --demo

# Enable debug logging
python -m iterm_mcp_python.server.main --debug
```

### Installing the MCP Server for Claude Desktop

We provide a script to install the server in Claude Desktop:

```bash
# Run the installation script
python install_claude_desktop.py
```

This will:
1. Register the server in Claude Desktop's configuration
2. Check if the server is already running
3. Offer to start the server if it's not running

**IMPORTANT**: You must have the server running in a separate terminal window while using it with Claude Desktop. The server won't start automatically when Claude Desktop launches.

To start the server manually:
```bash
python -m iterm_mcp_python.server.main
```

If you encounter connection errors in Claude Desktop, you can diagnose them with:
```bash
python install_claude_desktop.py --check-error "your error message"
```

### Debugging with MCP Inspector

For development and debugging, you can use the MCP Inspector:

```bash
mcp dev -m iterm_mcp_python.server.fastmcp_server
```

### Important Implementation Notes

1. **Process Termination**:  
   The server uses SIGKILL for termination to prevent hanging on exit. This ensures clean exit but bypasses Python's normal cleanup process. If you're developing and need proper cleanup, modify the signal handler in `main.py`.

2. **New FastMCP API**:  
   The FastMCP implementation uses the decorator-based API from the official MCP Python SDK. Tools are defined with `@mcp.tool()`, resources with `@mcp.resource()`, and prompts with `@mcp.prompt()`.

3. **Lifespan Management**:  
   The FastMCP implementation uses the lifespan API to properly initialize and clean up iTerm2 connections. The lifespan context provides access to the terminal, layout manager, and logger.

4. **WebSocket Handling**:  
   The FastMCP implementation uses the official SDK which properly handles WebSocket frames, fixing the "no close frame received or sent" error that previously occurred.

5. **Port Selection**:  
   The server uses port range 12340-12349 to avoid conflicts with common services. It automatically tries the next port in the range if one is busy.

### Using in Your Own Scripts

#### Basic Usage

```python
import asyncio
import iterm2
from iterm_mcp_python.core.terminal import ItermTerminal
from iterm_mcp_python.core.layouts import LayoutManager, LayoutType

async def my_script():
    # Connect to iTerm2
    connection = await iterm2.Connection.async_create()
    
    # Initialize terminal and layout manager
    terminal = ItermTerminal(connection)
    await terminal.initialize()
    layout_manager = LayoutManager(terminal)
    
    # Create a layout with named panes
    session_map = await layout_manager.create_layout(
        layout_type=LayoutType.HORIZONTAL_SPLIT,
        pane_names=["Code", "Terminal"]
    )
    
    # Get sessions by name
    code_session = await terminal.get_session_by_name("Code")
    terminal_session = await terminal.get_session_by_name("Terminal")
    
    # Send commands to sessions
    await code_session.send_text("vim myfile.py", execute=True)
    await terminal_session.send_text("python -m http.server", execute=True)
    
    # Type text without executing (for CLIs with prompts)
    await code_session.send_text("i", execute=False)  # Enter insert mode in vim
    await code_session.send_text("print('Hello, world!')", execute=False)
    await code_session.send_special_key("escape")  # Switch to command mode

# Run the script
asyncio.run(my_script())
```

#### Advanced Features

```python
import asyncio
import iterm2
from iterm_mcp_python.core.terminal import ItermTerminal

async def my_advanced_script():
    # Connect to iTerm2
    connection = await iterm2.Connection.async_create()
    
    # Initialize terminal with custom line limits
    terminal = ItermTerminal(
        connection=connection,
        default_max_lines=100,  # Default lines to retrieve per session
        max_snapshot_lines=1000  # Maximum lines to keep in snapshot
    )
    await terminal.initialize()
    
    # Create multiple sessions with different commands and line limits
    session_configs = [
        {
            "name": "Server", 
            "command": "python -m http.server", 
            "monitor": True,
            "max_lines": 200  # Custom line limit for this session
        },
        {
            "name": "Logs", 
            "command": "tail -f server.log", 
            "layout": True, 
            "vertical": True
        },
        {
            "name": "Client", 
            "command": "curl localhost:8000", 
            "layout": True, 
            "vertical": False
        }
    ]
    
    session_map = await terminal.create_multiple_sessions(session_configs)
    
    # Get the Server session for monitoring
    server_session = await terminal.get_session_by_id(session_map["Server"])
    
    # Store the persistent ID for future reconnection
    server_persistent_id = server_session.persistent_id
    print(f"Server session persistent ID: {server_persistent_id}")
    
    # Add real-time output handling
    async def handle_server_output(content):
        if "GET /" in content:
            # React to server events
            client_session = await terminal.get_session_by_id(session_map["Client"])
            await client_session.send_text("echo 'Detected a GET request!'\n")
    
    # Register the callback
    server_session.add_monitor_callback(handle_server_output)
    
    # Add output filtering to Logs session
    logs_session = await terminal.get_session_by_id(session_map["Logs"])
    logs_session.logger.add_output_filter(r"ERROR|WARN")  # Only capture errors and warnings
    
    # Wait for events
    while True:
        await asyncio.sleep(1)
        # Get snapshot with limited lines
        snapshot = terminal.log_manager.get_snapshot(
            server_session.id,
            max_lines=50  # Only get last 50 lines
        )
        if snapshot and "Keyboard interrupt received" in snapshot:
            break

    # Example of reconnecting by persistent ID in a new session
    async def reconnect_later():
        # Create a new terminal instance (simulating a new connection)
        new_connection = await iterm2.Connection.async_create()
        new_terminal = ItermTerminal(new_connection)
        await new_terminal.initialize()
        
        # Reconnect to server session using persistent ID
        reconnected_session = await new_terminal.get_session_by_persistent_id(server_persistent_id)
        if reconnected_session:
            print(f"Successfully reconnected to session: {reconnected_session.name}")
            # Continue working with the reconnected session
            await reconnected_session.send_text("echo 'Reconnected!'\n")

# Run the script
asyncio.run(my_advanced_script())
```

#### Hierarchical team orchestration (CEO -> Team Leads -> ICs)

The layout manager and MCP tools now understand hierarchical pane specs that include
team/agent metadata. Pane titles are derived automatically (e.g., `Team Leads :: TL-Backend`),
and the MCP servers will register the agents and teams for you.

```python
# Create a 2x2 grid with explicit team/agent hierarchy
session_map = await layout_manager.create_layout(
    layout_type=LayoutType.QUAD,
    pane_hierarchy=[
        {"team": "Executive", "agent": "CEO"},
        {"team": "Team Leads", "agent": "TL-Frontend"},
        {"team": "Team Leads", "agent": "TL-Backend"},
        {"team": "ICs", "agent": "IC-Oncall"},
    ],
)

# When using the FastMCP or gRPC CreateSessions APIs the same hierarchy is
# auto-registered in AgentRegistry:
# create_sessions(layout="quad", session_configs=[...])

# Target panes by hierarchy and send cascading messages
await select_panes_by_hierarchy([
    {"team": "Team Leads", "agent": "TL-Frontend"}
])

await send_hierarchical_message(
    targets=[
        {"team": "Team Leads", "message": "Share updates for the CEO."},
        {"team": "Team Leads", "agent": "TL-Backend", "message": "Ship the API fixes."},
        {"team": "ICs", "agent": "IC-Oncall", "message": "Monitor logs for regressions."},
    ],
    broadcast="CEO broadcast: align on launch goals.",
)
```

## MCP Tools and Resources

The FastMCP implementation provides the following:

### Session Management Tools

- `list_sessions` - List all available terminal sessions (with optional `agents_only` filter)
- `set_active_session` - Set the active session for subsequent operations (with optional `focus` to bring to foreground)
- `create_sessions` - Create multiple sessions with layout and agent registration
- `check_session_status` - Check if a session is currently processing a command

### Command Execution Tools

- `write_to_sessions` - Write to multiple sessions in parallel with targeting
- `read_sessions` - Read from multiple sessions in parallel with filtering
- `send_control_character` - Send a control character (Ctrl+C, Ctrl+D, etc.)
- `send_special_key` - Send a special key (Enter, Tab, Escape, Arrow keys, etc.)

### Agent & Team Management

- `register_agent` - Register a named agent bound to a session
- `list_agents` - List all registered agents (optionally filtered by team)
- `remove_agent` - Remove an agent registration
- `create_team` - Create a new team for grouping agents
- `list_teams` - List all teams
- `remove_team` - Remove a team
- `assign_agent_to_team` - Add an agent to a team
- `remove_agent_from_team` - Remove an agent from a team

### Orchestration Tools

- `orchestrate_playbook` - Execute a high-level playbook (layout + commands + cascade + reads)
- `send_cascade_message` - Send priority-based cascading messages to agents/teams
- `select_panes_by_hierarchy` - Resolve panes by team/agent hierarchy
- `send_hierarchical_message` - Send cascading messages using hierarchical specs

### Session Modification Tools

- `modify_sessions` - Modify multiple sessions at once (appearance, focus, active state)

The modification tool allows:

- **set_active** - Set session as the active session for subsequent operations
- **focus** - Bring session to the foreground in iTerm
- **background_color** - RGB values for the session background
- **tab_color** - RGB values for the iTerm tab indicator
- **cursor_color** - RGB values for the cursor
- **badge** - Text badge displayed in the session (supports emoji)
- **reset** - Reset all colors to defaults

Example:

```json
{
  "modifications": [
    {
      "agent": "claude-1",
      "focus": true,
      "set_active": true,
      "tab_color": {"red": 100, "green": 200, "blue": 255},
      "badge": "🤖 Claude-1"
    },
    {
      "agent": "claude-2",
      "background_color": {"red": 40, "green": 30, "blue": 30},
      "tab_color": {"red": 255, "green": 150, "blue": 100}
    }
  ]
}
```

### Monitoring Tools

- `start_monitoring_session` - Start real-time monitoring for a session
- `stop_monitoring_session` - Stop real-time monitoring for a session

### Session Lock & Tag Tools

- `lock_session` - Lock a session for exclusive agent access
- `unlock_session` - Release a session lock
- `request_session_access` - Request permission to write to a locked session
- `set_session_tags` - Set or append tags on a session

### Notification Tools

- `get_notifications` - Get recent agent notifications (errors, completions, blocks)
- `get_agent_status_summary` - Compact one-line-per-agent status summary
- `notify` - Manually add a notification for an agent
- `wait_for_agent` - Wait for an agent to complete or reach idle state

### Resources

- `terminal://{session_id}/output` - Get the output from a terminal session
- `terminal://{session_id}/info` - Get information about a terminal session
- `terminal://sessions` - Get a list of all terminal sessions
- `agents://all` - Get a list of all registered agents
- `teams://all` - Get a list of all teams

### Prompts

- `orchestrate_agents` - Prompt for orchestrating multiple agents
- `monitor_team` - Prompt for monitoring a team of agents

## Multi-Agent Orchestration

The iTerm MCP server supports coordinating multiple Claude Code instances through named agents, teams, and parallel session operations.

### Team Profiles with Auto-Assigned Colors

Teams can be assigned unique iTerm profiles with automatically distributed colors. The `ColorDistributor` uses a maximum-gap algorithm to ensure colors are visually distinct.

```python
from core.profiles import ProfileManager, ColorDistributor

# Create profile manager
profile_manager = ProfileManager()

# Create profiles for teams - colors are auto-assigned
teams = ["backend", "frontend", "devops", "ml", "security"]
for team_name in teams:
    profile = profile_manager.get_or_create_team_profile(team_name)
    print(f"{team_name}: hue={profile.color.hue:.1f}°, GUID={profile.guid}")

# Save profiles to iTerm Dynamic Profiles
profile_manager.save_profiles()

# Verify: profiles saved to ~/Library/Application Support/iTerm2/DynamicProfiles/
```

**Pass Criteria**:
- 5 team profiles created with unique GUIDs
- Hues are evenly distributed (minimum gap ≥ 40°)
- Profile file created at `~/Library/Application Support/iTerm2/DynamicProfiles/iterm-mcp-profiles.json`

The color distribution algorithm starts at 180° (teal) and bisects the largest gap for each new color:

```python
from core.profiles import ColorDistributor

distributor = ColorDistributor(saturation=70, lightness=38)

# Get 6 colors - each fills the largest gap
colors = [distributor.get_next_color() for _ in range(6)]
hues = [c.hue for c in colors]  # [180.0, 0.0, 90.0, 270.0, 45.0, 225.0]

# Verify: min gap ≥ 45°, max gap ≤ 90° for 6 colors
```

### Agent & Team Concepts

Agents bind a name to an iTerm session, enabling targeted communication. Teams group agents for broadcast operations.

```python
# Register agents
register_agent(name="alice", session_id="session-123", teams=["frontend"])
register_agent(name="bob", session_id="session-456", teams=["frontend", "backend"])

# Create teams
create_team(name="frontend", description="Frontend developers")
create_team(name="backend", description="Backend developers")

# List agents by team
list_agents(team="frontend")  # Returns alice and bob
```

### Playbook Orchestration

FastMCP now exposes an `orchestrate_playbook` tool (and matching `OrchestratePlaybook` gRPC method) so you can define multi-team workflows once and execute them with a single request:

1. **Create a layout** with `CreateSessionsRequest` (pane names, optional agent/team assignment, initial commands).
2. **Run command blocks** defined as `PlaybookCommand` entries (parallel flags + `SessionMessage` targets).
3. **Fan out cascades** via `CascadeMessageRequest` to broadcast, team, or agent recipients with deduplication.
4. **Monitor results** using a `ReadSessionsRequest` to collect outputs across teams.

Example payload:

```json
{
  "playbook": {
    "layout": {"sessions": [{"name": "Ops"}, {"name": "QA"}], "layout": "VERTICAL_SPLIT"},
    "commands": [
      {"name": "bootstrap", "messages": [{"content": "echo ready", "targets": [{"name": "Ops"}]}]}
    ],
    "cascade": {"broadcast": "Deploying new build"},
    "reads": {"targets": [{"team": "qa"}], "parallel": true}
  }
}
```

**Python API (verified)**:

```python
from core.models import (
    CreateSessionsRequest, SessionConfig,
    OrchestrateRequest, Playbook, PlaybookCommand,
    SessionMessage, SessionTarget
)

# Create a multi-stage playbook
playbook = Playbook(
    commands=[
        PlaybookCommand(
            name='initial-setup',
            messages=[
                SessionMessage(
                    content='echo "Hello from playbook"',
                    targets=[SessionTarget(team='docs-testing')],
                    execute=True
                )
            ],
            parallel=True
        ),
        PlaybookCommand(
            name='verification',
            messages=[
                SessionMessage(
                    content='echo "Setup complete"',
                    targets=[SessionTarget(agent='test-profiles')],
                    execute=True
                )
            ],
            parallel=False
        )
    ]
)

# Wrap in OrchestrateRequest for MCP tool
request = OrchestrateRequest(playbook=playbook)

# Pass Criteria:
# - Commands: 2 stages created
# - Each PlaybookCommand has messages with targets
# - OrchestrateRequest validates successfully
```

### Parallel Session Operations

Write to or read from multiple sessions simultaneously:

```python
# Write to multiple sessions by different targets
write_to_sessions(
    messages=[
        {"content": "npm test", "targets": [{"team": "frontend"}]},
        {"content": "cargo test", "targets": [{"agent": "rust-agent"}]},
        {"content": "echo hello", "targets": [{"name": "Session1"}, {"name": "Session2"}]}
    ],
    parallel=True,
    skip_duplicates=True
)

# Read from multiple sessions
read_sessions(
    targets=[
        {"agent": "alice", "max_lines": 50},
        {"team": "backend", "max_lines": 100}
    ],
    parallel=True,
    filter_pattern="ERROR|WARN"
)
```

### Cascading Messages

Send priority-based messages where the most specific wins:

```python
# Cascading priority: agent > team > broadcast
send_cascade_message(
    broadcast="All agents: sync your status",
    teams={
        "frontend": "Frontend team: run lint check",
        "backend": "Backend team: run database migrations"
    },
    agents={
        "alice": "Alice, please handle the API review specifically"
    },
    skip_duplicates=True
)
```

Resolution order:
1. If agent has a specific message → use it
2. Else if agent's team has a message → use team message
3. Else if broadcast exists → use broadcast
4. Messages are deduplicated to prevent sending the same content twice

**Python API (verified)**:

```python
from core.agents import AgentRegistry, CascadingMessage

registry = AgentRegistry()

# Create teams and register agents
registry.create_team("docs-testing", "Documentation testing team")
registry.register_agent("test-profiles", "session-1", teams=["docs-testing"])
registry.register_agent("test-agents", "session-2", teams=["docs-testing"])

# Create a cascading message targeting a team
team_cascade = CascadingMessage(teams={"docs-testing": "Hello team!"})
result = registry.resolve_cascade_targets(team_cascade)
# Returns: {'Hello team!': ['test-profiles', 'test-agents']}

# Target a specific agent
agent_cascade = CascadingMessage(agents={"test-profiles": "Hello agent!"})
result = registry.resolve_cascade_targets(agent_cascade)
# Returns: {'Hello agent!': ['test-profiles']}

# Broadcast to all registered agents
broadcast_cascade = CascadingMessage(broadcast="Hello everyone!")
result = registry.resolve_cascade_targets(broadcast_cascade)
# Returns: {'Hello everyone!': ['test-profiles', 'test-agents', ...]}
```

**Pass Criteria**:
- Team messages resolve to all agents in that team
- Agent-specific messages resolve to only that agent
- Broadcasts reach all registered agents

### gRPC Client

For programmatic access outside MCP, use the gRPC client:

```python
from iterm_mcpy.grpc_client import ITermClient

# Using context manager
with ITermClient(host='localhost', port=50051) as client:
    # List sessions
    sessions = client.list_sessions()

    # Create sessions with layout
    response = client.create_sessions(
        sessions=[
            {"name": "Agent1", "agent": "alice", "team": "frontend"},
            {"name": "Agent2", "agent": "bob", "team": "backend"}
        ],
        layout="HORIZONTAL_SPLIT"
    )

    # Write to multiple sessions
    client.write_to_sessions(
        messages=[{"content": "echo hello", "targets": [{"team": "frontend"}]}],
        parallel=True
    )

    # Send cascade message
    client.send_cascade_message(
        broadcast="Status check",
        teams={"frontend": "Run tests"},
        agents={"alice": "Review PR #42"}
    )
```

### Session Locking

Agents can lock sessions for exclusive access, preventing other agents from writing:

```python
# Via MCP tools:
lock_session(session_id="session-123", agent="alice")      # Alice locks
unlock_session(session_id="session-123", agent="alice")    # Alice releases

# Other agent trying to lock is rejected
lock_session(session_id="session-123", agent="bob")        # Returns: locked=False
```

**Lock behavior**:
- Only the lock owner can unlock the session
- `request_session_access` allows requesting write permission from the owner
- Locks are enforced by the MCP server's `write_to_sessions` tool

**Python API (verified)**:

```python
# Session locking via MCP tools (lock_session, unlock_session)
# Example test flow:
#   1. lock_session(session_id, agent="test-agents") → locked=True
#   2. lock_session(session_id, agent="other-agent") → locked=False (rejected)
#   3. unlock_session(session_id, agent="test-agents") → unlocked=True
#
# Pass Criteria:
# - Lock acquired by owner
# - Lock denied to non-owner
# - Owner can release lock
```

### Data Persistence

Agents and teams are persisted to JSONL files in `~/.iterm_mcp_logs/`:
- `agents.jsonl` - Registered agents with session bindings
- `teams.jsonl` - Team definitions and hierarchies

## Testing

The project includes a comprehensive test suite with 98+ passing tests covering:
- Core session and terminal management
- Agent and team orchestration
- gRPC server and client functionality
- Command output tracking
- Model validation
- Logging infrastructure

### Running Tests

Run all tests (Linux/macOS):
```bash
python -m pytest tests/ -v
```

Run tests with coverage:
```bash
python -m pytest tests/ --cov=core --cov=iterm_mcpy --cov=utils --cov-report=term-missing
```

Run specific test files:
```bash
python -m pytest tests/test_models.py -v
python -m pytest tests/test_agent_registry.py -v
```

### Test Categories

**Unit Tests** (run on all platforms):
- `test_models.py` - Pydantic model validation
- `test_agent_registry.py` - Agent/team management
- `test_command_output_tracking.py` - Command tracking logic
- `test_grpc_smoke.py` - gRPC service smoke tests
- `test_grpc_client.py` - gRPC client tests

**Integration Tests** (require macOS + iTerm2):
- `test_basic_functionality.py` - Core session operations
- `test_advanced_features.py` - Monitoring, layouts, etc.
- `test_line_limits.py` - Output truncation
- `test_logging.py` - Logging infrastructure
- `test_persistent_session.py` - Session persistence

### Development

Install development dependencies:
```bash
pip install -e ".[dev]"
```

This includes pytest, pytest-cov, black, mypy, and isort for testing and code quality.

## Logging and Monitoring

All session activity is logged to `~/.iterm_mcp_logs` by default. This includes:
- Commands sent to sessions
- Output received from sessions
- Control characters sent
- Session lifecycle events (creation, renaming, closure)

### Real-time Monitoring

Sessions can be monitored in real-time using the `start_monitoring()` method. This allows:
- Capturing output as it happens without polling
- Setting up custom callbacks for output processing
- Reacting to terminal events dynamically

### Output Filtering

Log output can be filtered using regex patterns:
- Only capture specific patterns like errors or warnings
- Reduce log noise for better analysis
- Multiple filters can be combined

### Snapshots and Line Management

Real-time snapshots of terminal output are maintained in snapshot files:
- Separate from main log files
- Always contain the latest output
- Available for LLM or other systems to access
- Useful for state monitoring without interfering with user interaction

Output line management:
- Configure global default line limits for all sessions
- Set per-session line limits via `set_max_lines()`
- Request specific line counts for individual operations
- Overflow files for tracking historic output beyond the line limit

### Persistent Session Management

Sessions maintain persistent identities across restarts and reconnection:
- Each session has a unique UUID-based persistent ID
- IDs are stored in `~/.iterm_mcp_logs/persistent_sessions.json`
- `get_session_by_persistent_id()` allows reconnection to existing sessions
- State is preserved even after chat or connection interruptions
- Session output history is available across reconnections

## OpenTelemetry Integration

The iTerm MCP server includes optional OpenTelemetry integration for production-grade observability, enabling tracing of agent operations, message delivery, and command execution.

### Installation

Install with OpenTelemetry support:

```bash
pip install -e ".[otel]"
```

This installs:
- `opentelemetry-api` - Core tracing API
- `opentelemetry-sdk` - Tracing SDK
- `opentelemetry-exporter-otlp` - OTLP exporter for backends like Jaeger
- `opentelemetry-semantic-conventions` - Standard attribute names

### Configuration

Configure via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_ENABLED` | `true` | Enable/disable tracing |
| `OTEL_SERVICE_NAME` | `iterm-mcp` | Service name in traces |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP collector endpoint |
| `OTEL_EXPORTER_OTLP_INSECURE` | `true` | Use insecure (HTTP) connection to collector |
| `OTEL_TRACES_EXPORTER` | `otlp` | Exporter type (`otlp`, `console`, `none`) |
| `OTEL_CONSOLE_EXPORTER` | `false` | Enable console exporter for debugging |
| `OTEL_ENVIRONMENT` | `development` | Deployment environment tag |

### Traced Operations

The following operations are automatically traced:

**Session Operations:**
- `session.send_text` - Text sent to sessions
- `session.execute_command` - Command execution
- `session.get_screen_contents` - Screen content retrieval
- `session.send_control_character` - Control character sends
- `session.send_special_key` - Special key sends

**Agent/Team Operations:**
- `agent_registry.register_agent` - Agent registration
- `agent_registry.remove_agent` - Agent removal
- `agent_registry.create_team` - Team creation
- `agent_registry.remove_team` - Team removal
- `agent_registry.resolve_cascade_targets` - Cascade message resolution

**RPC/Service Operations:**
- `execute_create_sessions` - Create and initialize new sessions
- `execute_write_request` - Write data or commands to sessions
- `execute_read_request` - Read data or screen contents from sessions
- `execute_cascade_request` - Execute cascade operations across agents/teams

Each span includes relevant attributes like `agent.name`, `session.id`, `team.name`, and operation-specific metadata.

### Connecting to Observability Backends

#### Jaeger

Run Jaeger with OTLP support:

```bash
docker run -d --name jaeger \
  -p 16686:16686 \
  -p 4317:4317 \
  jaegertracing/all-in-one:latest
```

Then start the server:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 python -m iterm_mcpy.fastmcp_server
```

View traces at http://localhost:16686

#### Grafana Tempo

Configure the OTLP endpoint to your Tempo instance:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317 python -m iterm_mcpy.fastmcp_server
```

#### Console Debugging

For local debugging without a backend:

```bash
OTEL_CONSOLE_EXPORTER=true python -m iterm_mcpy.fastmcp_server
```

This prints spans to stdout.

### Programmatic Usage

Use the tracing utilities in your own code:

```python
from utils.otel import (
    init_tracing,
    trace_operation,
    add_span_attributes,
    add_span_event,
    create_span,
)

# Initialize tracing at startup
init_tracing()

# Use decorator for automatic tracing
@trace_operation("my_operation")
async def my_function(agent: str, session_id: str):
    add_span_attributes(custom_attr="value")
    # ... do work ...
    add_span_event("checkpoint_reached", {"step": 1})

# Or use context manager for manual spans
with create_span("custom_span", attributes={"key": "value"}):
    # ... traced code ...
```

### Graceful Fallback

If OpenTelemetry is not installed, the server uses no-op implementations that have zero overhead. This allows the same code to run with or without observability enabled.

## Relationship to Claude Code MCP

This project provides **multi-agent orchestration infrastructure** that complements tools like [@steipete/claude-code-mcp](https://github.com/steipete/claude-code-mcp):

### Use Case Comparison

**@steipete/claude-code-mcp** - Direct code automation
- Wraps a single Claude Code CLI instance
- One-shot code execution with permission bypass
- Stateless operation
- Best for: Direct file/code manipulation by a single agent

**iterm-mcp** - Multi-agent coordination
- Orchestrates multiple Claude Code instances in iTerm sessions
- Agent registry with teams and hierarchies
- Persistent state management
- Best for: Coordinating parallel agents, complex workflows, team-based operations

### Integration Example

You can combine both tools:
1. Use iterm-mcp to create and manage multiple iTerm sessions
2. Run `@steipete/claude-code-mcp` in each session for code automation
3. Use iterm-mcp's agent/team tools to coordinate across sessions

```python
# Create sessions for different agents
create_sessions(
    layout_type="horizontal",
    session_configs=[
        {"name": "Frontend", "agent": "frontend-dev", "team": "dev"},
        {"name": "Backend", "agent": "backend-dev", "team": "dev"}
    ]
)

# Each session can run claude-code-mcp
write_to_terminal(session_id="...", content="npx -y @steipete/claude-code-mcp@latest")

# Coordinate across sessions
send_cascade_message(
    teams={"dev": "Run tests before deployment"}
)
```

For a detailed architectural comparison, see [docs/claude-code-mcp-analysis.md](docs/claude-code-mcp-analysis.md).

## Git Commit Session Tracking

Track which terminal sessions created each commit and enable agent notifications for GitHub PR comments. See [docs/git-session-tracking.md](docs/git-session-tracking.md) for full documentation.

### Quick Start

Install the git hooks in your repository:

```bash
./scripts/install-git-hooks.sh /path/to/your/repo
```

The hooks automatically capture the iTerm session ID on each commit and store it in git notes. Query session information:

```bash
# Show session info for a commit
python scripts/query-session.py show <commit-sha>

# List commits from a specific session
python scripts/query-session.py list-session <session-id>

# Get commit info from GitHub PR comment
python scripts/query-session.py from-github owner repo comment_id
```

This enables routing PR comments and notifications back to the specific terminal/agent that created the code.

## License

[MIT](LICENSE)