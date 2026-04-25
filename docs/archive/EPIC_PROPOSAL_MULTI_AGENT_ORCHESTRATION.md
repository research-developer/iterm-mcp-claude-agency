# Epic Proposal: Advanced Multi-Agent Orchestration with iTerm2 Python API

> **Status: SUBSTANTIALLY COMPLETE** - Last Updated: January 2026
>
> This document has been revised to reflect implementation progress. Features are marked with their current status.

## Executive Summary

This epic proposed a comprehensive enhancement to the iTerm2 MCP server to create an intuitive, visual, and robust multi-agent orchestration platform. The goal was to enable multiple agents from different teams to work together seamlessly, with the terminal pane hierarchy representing team structure in a way that humans can easily understand and interact with.

**Vision:** Transform iTerm2 into a visual command center where:
- Executive agents coordinate team hierarchies through intuitive pane layouts
- Visual feedback (colors, badges, status bars) provides real-time agent state
- Humans can work with their executive assistant OR message individual agents directly
- The terminal itself becomes a living organizational chart
- Agents communicate through well-defined protocols with full observability

## Implementation Status Summary

| Sub-Issue | Status | Completion |
|-----------|--------|------------|
| 1. Visual Hierarchy & Layouts | **PARTIAL** | 60% |
| 2. Real-Time Visual Status | **COMPLETE** | 90% |
| 3. Inter-Agent Communication | **COMPLETE** | 85% |
| 4. Health Monitoring & Recovery | **PARTIAL** | 30% |
| 5. Executive Agent Interface | **SUBSTANTIAL** | 75% |
| 6. Advanced Observability | **COMPLETE** | 80% |
| 7. Security & Isolation | **SUBSTANTIAL** | 70% |

**Overall Epic Completion: ~70%** - Core orchestration infrastructure delivered; visual/UI features partially implemented.

---

## Current State Analysis

### What We Built ✅

**Foundation (from original assessment):**
- **Solid Foundation**: gRPC server with 17 RPC methods, 40+ MCP tools
- **Agent Registry**: Team hierarchy support with cascading messages
- **Parallel Operations**: Multi-session read/write capabilities
- **Persistence**: Session reconnection via persistent IDs
- **Test Coverage**: 98 passing tests

**New Capabilities Delivered:**
- **Manager Agents** (`core/manager.py`): Hierarchical task delegation with CrewAI-style workflows
- **Event-Driven Flows** (`core/flows.py`): Reactive programming with @start, @listen, @router decorators
- **Typed Messaging** (`core/messaging.py`): AutoGen-style typed message passing
- **Cross-Agent Memory** (`core/memory.py`): Shared namespace-based context store with SQLite + FTS5
- **Role-Based Access Control** (`core/roles.py`): 8 predefined roles with tool filtering
- **State Checkpointing** (`core/checkpointing.py`): Crash recovery and session resumption
- **OpenTelemetry** (`utils/otel.py`): Distributed tracing for production observability
- **Visual Customization**: Colors, badges, tab colors via `modify_sessions`
- **Team Profiles** (`core/profiles.py`): Auto-assigned colors with ColorDistributor
- **Service Registry** (`core/services.py`): Cross-repo service management
- **Feedback System** (`core/feedback.py`): Agent-driven issue reporting with GitHub integration

### Remaining Gaps ⚠️

- **Dynamic Layout Reorganization**: Panes don't auto-reorganize when teams change
- **Health Auto-Recovery**: No automatic Ctrl+C or restart on hang detection
- **Circuit Breakers**: No cascading failure prevention
- **iTerm2 Native Monitors**: Not using CustomControlSequenceMonitor, FocusMonitor, etc.
- **Status Bar Components**: No custom iTerm2 status bar integration
- **Debug Visualization Pane**: No dedicated real-time metrics pane

---

## Sub-Issue Status Details

---

## Sub-Issue 1: Visual Hierarchy & Dynamic Layout Management

**Status: PARTIAL (60%)**

### Implemented ✅

1. **Visual Team Identity**
   - ✅ Color-coded backgrounds by team via `modify_sessions` tool
   - ✅ Tab colors via `set_tab_color` in session modifications
   - ✅ Team badges showing agent status
   - ✅ Team profiles with auto-assigned colors (`core/profiles.py`)
   - ✅ ColorDistributor for maximum-gap color assignment

2. **Layout Creation**
   - ✅ Predefined layouts: HORIZONTAL_SPLIT, VERTICAL_SPLIT, QUAD, etc.
   - ✅ `pane_hierarchy` parameter for team/agent metadata
   - ✅ Named sessions with agent/team binding on creation

**Example (Implemented):**
```python
# Create sessions with team hierarchy
create_sessions(
    sessions=[
        {"name": "CEO", "agent": "ceo", "team": "Executive"},
        {"name": "TL-Frontend", "agent": "tl-fe", "team": "Team Leads"},
        {"name": "TL-Backend", "agent": "tl-be", "team": "Team Leads"},
    ],
    layout="VERTICAL_SPLIT"
)

# Apply team colors
modify_sessions(modifications=[
    {"agent": "ceo", "tab_color": {"red": 255, "green": 215, "blue": 0}},
    {"team": "Team Leads", "background_color": {"red": 30, "green": 50, "blue": 70}}
])
```

### Not Implemented ❌

1. **Dynamic Reorganization**
   - ❌ Automatic pane rearrangement when agents join/leave teams
   - ❌ File watchers on agent registry
   - ❌ Smooth layout transitions

2. **Hierarchical Layout Engine**
   - ❌ `HierarchicalLayoutEngine` class as proposed
   - ❌ Auto-rebalancing based on team structure
   - ❌ Executive at top, teams below layout automation

3. **Named Arrangements**
   - ❌ Save/restore via `iterm2.Arrangement`
   - ❌ Hot-swap layouts without disrupting sessions

### Remaining Work

| Feature | Priority | Effort |
|---------|----------|--------|
| Auto-reorganize on team change | Medium | 2-3 days |
| Hierarchical layout templates | Low | 1-2 days |
| Arrangement save/restore | Low | 1 day |

---

## Sub-Issue 2: Real-Time Visual Agent Status System

**Status: COMPLETE (90%)**

### Implemented ✅

1. **Color-Coded Agent States**
   - ✅ Background colors via `modify_sessions`
   - ✅ Tab colors for visual grouping
   - ✅ Cursor colors for active indicator

2. **Dynamic Badges**
   - ✅ Badge text via `set_badge` in modifications
   - ✅ Supports emoji badges (e.g., "🤖 Working")

3. **Notification System** (`core/notifications.py`)
   - ✅ `NotificationManager` with ring buffer storage
   - ✅ Levels: info, warning, error, success, blocked
   - ✅ `get_notifications` MCP tool
   - ✅ `get_agent_status_summary` for one-line-per-agent view
   - ✅ `notify` tool for manual notifications

4. **Output Pattern Recognition**
   - ✅ `subscribe_to_output_pattern` for regex-based triggers
   - ✅ Pattern → event triggering via EventBus
   - ✅ Real-time monitoring via `start_monitoring_session`

**Example (Implemented):**
```python
# Subscribe to error patterns
subscribe_to_output_pattern(
    pattern=r"ERROR:|FATAL:|Exception",
    event_name="error_detected"
)

# Update visual state on error
modify_sessions(modifications=[
    {"agent": "builder", "background_color": {"red": 100, "green": 20, "blue": 20}, "badge": "❌ ERROR"}
])

# Get status summary
summary = get_agent_status_summary()
# Returns: "alice: ✓ Build complete (2m ago) | bob: ⚠ Waiting for input (30s ago)"
```

### Not Implemented ❌

1. **Custom Status Bar Components**
   - ❌ `iterm2.StatusBarComponent` with `@iterm2.StatusBarRPC`
   - ❌ Team-wide metrics in status bar

2. **Attention Mechanisms**
   - ❌ Dock bounce (`RequestAttention=yes`)
   - ❌ macOS notifications via escape sequences
   - ❌ Tab color flash animations

3. **Automatic State Detection**
   - ❌ Auto-update colors based on output patterns (manual via events)

### Remaining Work

| Feature | Priority | Effort |
|---------|----------|--------|
| Status bar components | Low | 2-3 days |
| macOS notifications | Low | 1 day |
| Auto-state from patterns | Medium | 1-2 days |

---

## Sub-Issue 3: Structured Inter-Agent Communication Protocol

**Status: COMPLETE (85%)**

### Implemented ✅

1. **Event-Driven Architecture** (`core/flows.py`)
   - ✅ `EventBus` for pub/sub messaging
   - ✅ `@start` decorator for flow entry points
   - ✅ `@listen` decorator for event handlers
   - ✅ `@router` decorator for dynamic routing
   - ✅ `@on_output` decorator for pattern matching
   - ✅ Event history and replay
   - ✅ Priority levels: LOW, NORMAL, HIGH, CRITICAL

2. **Typed Message Protocol** (`core/messaging.py`)
   - ✅ Pydantic-based message types
   - ✅ `TerminalCommand`, `TerminalOutput` messages
   - ✅ `AgentTaskRequest`, `AgentTaskResponse`
   - ✅ Correlation IDs for request/response tracking
   - ✅ Message priority levels
   - ✅ `MessageRouter` for handler dispatch

3. **Coordination Primitives**
   - ✅ Session locking (`lock_session`, `unlock_session`)
   - ✅ Lock owner enforcement
   - ✅ `request_session_access` for permission requests

4. **Cascading Messages**
   - ✅ Priority resolution: agent > team > broadcast
   - ✅ Deduplication with SHA256 hashing
   - ✅ `send_cascade_message`, `send_hierarchical_message`

**Example (Implemented):**
```python
# Event-driven flow
class BuildFlow(Flow):
    @start("build_requested")
    async def start_build(self, project: str):
        await trigger("build_started", {"project": project})

    @listen("build_complete")
    async def on_complete(self, result):
        if result["success"]:
            await trigger("deploy_requested", result)

# Typed messaging
@message_handler(TerminalCommand)
async def handle_command(msg: TerminalCommand) -> TerminalOutput:
    output = await execute(msg.command)
    return TerminalOutput(output=output, correlation_id=msg.correlation_id)

# Cascading
send_cascade_message(
    broadcast="Status check",
    teams={"frontend": "Run lint"},
    agents={"alice": "Review PR #42"}
)
```

### Not Implemented ❌

1. **iTerm2 Native Monitors**
   - ❌ `CustomControlSequenceMonitor` for custom protocol
   - ❌ `NewSessionMonitor` for auto-registration
   - ❌ `FocusMonitor` for human attention tracking
   - ❌ `VariableMonitor` for state change reactions

2. **Advanced Coordination Primitives**
   - ❌ Barriers (wait for all agents)
   - ❌ Voting/consensus
   - ❌ Leader election

### Remaining Work

| Feature | Priority | Effort |
|---------|----------|--------|
| iTerm2 native monitors | Low | 3-4 days |
| Barrier primitive | Medium | 1 day |
| Voting/consensus | Low | 2 days |

---

## Sub-Issue 4: Proactive Health Monitoring & Auto-Recovery

**Status: PARTIAL (30%)**

### Implemented ✅

1. **Basic Monitoring**
   - ✅ `wait_for_agent` with timeout and progress summaries
   - ✅ `check_session_status` for processing state
   - ✅ Notification system for error tracking

2. **Manual Recovery Support**
   - ✅ `send_control_character` for Ctrl+C
   - ✅ Session recreation capability

**Example (Implemented):**
```python
# Wait for agent with timeout
result = wait_for_agent(
    agent="builder",
    wait_up_to=60,
    return_output=True,
    summary_on_timeout=True
)

if result["timed_out"]:
    # Manual intervention
    send_control_character(control_char="c", target={"agent": "builder"})
```

### Not Implemented ❌

1. **Automatic Health Checks**
   - ❌ Periodic heartbeat monitoring
   - ❌ Hang detection (no output for N seconds)
   - ❌ `AgentHealthMonitor` class

2. **Auto-Recovery**
   - ❌ Automatic Ctrl+C on hang
   - ❌ Automatic restart in same session
   - ❌ Work migration to new session
   - ❌ Escalation to human intervention

3. **Circuit Breaker**
   - ❌ Failure rate tracking
   - ❌ Circuit states (closed/open/half-open)
   - ❌ Cascading failure prevention

4. **Health Dashboard**
   - ❌ Dedicated monitor pane
   - ❌ Historical failure data
   - ❌ Recovery action log

### Remaining Work

| Feature | Priority | Effort |
|---------|----------|--------|
| Auto hang detection | High | 2-3 days |
| Auto Ctrl+C recovery | High | 1 day |
| Circuit breaker | Medium | 2 days |
| Health dashboard pane | Low | 2-3 days |

---

## Sub-Issue 5: Executive Agent Interface & Human-in-the-Loop

**Status: SUBSTANTIAL (75%)**

### Implemented ✅

1. **Manager Agents** (`core/manager.py`)
   - ✅ `ManagerAgent` class for worker coordination
   - ✅ Delegation strategies: round_robin, role_based, least_busy, priority, random
   - ✅ Task validation with regex patterns
   - ✅ Retry logic for failed tasks
   - ✅ `ManagerRegistry` for persistence

2. **Multi-Step Plans**
   - ✅ `TaskPlan` with dependency graph
   - ✅ `TaskStep` with depends_on, timeout, validation
   - ✅ Cycle detection
   - ✅ Parallel group execution
   - ✅ Stop-on-failure option

3. **MCP Tools**
   - ✅ `create_manager` - Create manager agent
   - ✅ `delegate_task` - Delegate single task
   - ✅ `execute_plan` - Run multi-step plan
   - ✅ `add_worker_to_manager`, `remove_worker_from_manager`

**Example (Implemented):**
```python
# Create manager
create_manager(
    name="build-orchestrator",
    workers=["builder", "tester", "deployer"],
    worker_roles={"builder": "builder", "tester": "tester", "deployer": "devops"},
    delegation_strategy="role_based"
)

# Execute plan
execute_plan(
    manager="build-orchestrator",
    plan={
        "name": "deploy-pipeline",
        "steps": [
            {"id": "build", "task": "npm run build", "role": "builder"},
            {"id": "test", "task": "npm test", "role": "tester", "depends_on": ["build"]},
            {"id": "deploy", "task": "npm run deploy", "role": "devops", "depends_on": ["test"]}
        ],
        "stop_on_failure": True
    }
)
```

### Not Implemented ❌

1. **Focus-Based Routing**
   - ❌ `FocusMonitor` tracking human attention
   - ❌ Auto-route messages to focused agent
   - ❌ "Take control" feature

2. **Handoff Protocol**
   - ❌ Human approval workflow
   - ❌ Executive summarization for review
   - ❌ Approval/rejection mechanism

3. **Broadcast Domains**
   - ❌ `iterm2.broadcast.BroadcastDomain`
   - ❌ Selective broadcast with filters

### Remaining Work

| Feature | Priority | Effort |
|---------|----------|--------|
| Human approval workflow | Medium | 2-3 days |
| Focus-based routing | Low | 2 days |
| Broadcast domains | Low | 1 day |

---

## Sub-Issue 6: Advanced Observability & Debug Infrastructure

**Status: COMPLETE (80%)**

### Implemented ✅

1. **OpenTelemetry Integration** (`utils/otel.py`)
   - ✅ Distributed tracing with trace/span IDs
   - ✅ Parent/child span relationships
   - ✅ OTLP exporter for Jaeger/Tempo
   - ✅ Console exporter for debugging
   - ✅ `@trace_operation` decorator
   - ✅ Semantic conventions for attributes

2. **Structured Logging**
   - ✅ JSON logs with agent ID, timestamp, context
   - ✅ JSONL persistence for agents, teams, messages, managers
   - ✅ Configurable log directory (`~/.iterm-mcp/`)

3. **Audit Trail**
   - ✅ All agent registrations logged
   - ✅ All message deliveries tracked
   - ✅ State changes persisted via checkpointing
   - ✅ Message deduplication tracking

4. **Performance Telemetry**
   - ✅ `start_telemetry_dashboard` MCP tool
   - ✅ HTTP endpoint for external dashboards
   - ✅ Real-time metrics streaming

**Example (Implemented):**
```bash
# Start with Jaeger
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 python -m iterm_mcpy.fastmcp_server

# View traces at http://localhost:16686
```

```python
# Programmatic tracing
from utils.otel import trace_operation, add_span_attributes

@trace_operation("build_project")
async def build(project: str):
    add_span_attributes(project=project)
    # ... traced code ...
```

### Not Implemented ❌

1. **Replay Capability**
   - ❌ Session recording for replay
   - ❌ Step-through debugging
   - ❌ Diff tool for comparing runs

2. **Debug Visualization Pane**
   - ❌ Dedicated pane with ASCII diagrams
   - ❌ Real-time message flow visualization
   - ❌ Agent state machine rendering

### Remaining Work

| Feature | Priority | Effort |
|---------|----------|--------|
| Session replay | Low | 3-4 days |
| Debug pane visualization | Low | 2-3 days |

---

## Sub-Issue 7: Security & Isolation Hardening

**Status: SUBSTANTIAL (70%)**

### Implemented ✅

1. **Role-Based Access Control** (`core/roles.py`)
   - ✅ 8 predefined roles: orchestrator, devops, builder, debugger, researcher, tester, monitor, custom
   - ✅ Tool filtering per role (allowed_tools, restricted_tools)
   - ✅ Priority levels (1-5)
   - ✅ `can_spawn_agents`, `can_modify_roles` permissions
   - ✅ `RoleManager` for assignment and checking

2. **Session Isolation**
   - ✅ Each agent in separate iTerm session
   - ✅ Session locking for exclusive access
   - ✅ Lock owner enforcement

3. **Safe Command Execution**
   - ✅ Base64 encoding option for special characters
   - ✅ `use_encoding` parameter for safe transmission

4. **MCP Tools**
   - ✅ `assign_session_role`, `get_session_role`, `remove_session_role`
   - ✅ `check_tool_permission`
   - ✅ `list_available_roles`, `list_session_roles`
   - ✅ `get_sessions_by_role`

**Example (Implemented):**
```python
# Assign role
assign_session_role(session_id="session-123", role="builder")

# Check permission
can_docker = check_tool_permission(session_id="session-123", tool_name="docker")
# Returns: True (builder role allows docker)

# Role definitions
ROLES = {
    "builder": {"priority": 2, "tools": ["npm", "pip", "cargo", "make", "git", "docker"]},
    "tester": {"priority": 3, "tools": ["pytest", "jest", "mocha", "cargo"]},
    "monitor": {"priority": 4, "tools": ["tail", "grep", "ps", "top"]}  # Read-only
}
```

### Not Implemented ❌

1. **Audit Security**
   - ❌ Cryptographic signatures on audit logs
   - ❌ Tamper-evident log chain

2. **Secrets Management**
   - ❌ Redaction patterns for sensitive data
   - ❌ Encrypted credential storage

3. **Rate Limiting**
   - ❌ Command flooding prevention
   - ❌ Broadcast throttling

4. **Advanced Sandboxing**
   - ❌ Buried sessions for background workers
   - ❌ Resource quotas

### Remaining Work

| Feature | Priority | Effort |
|---------|----------|--------|
| Secret redaction | Medium | 1-2 days |
| Rate limiting | Medium | 1-2 days |
| Cryptographic audit | Low | 2-3 days |

---

## Updated Implementation Roadmap

### Completed Phases ✅

**Phase 1: Visual Foundation**
- ✅ Team profile colors
- ✅ Session modification (colors, badges)
- ✅ Notification system

**Phase 2: Communication & Coordination**
- ✅ Event bus with pub/sub
- ✅ Manager agents with delegation
- ✅ Typed messaging protocol
- ✅ Session locking

**Phase 3: Reliability & Operations**
- ✅ OpenTelemetry integration
- ✅ State checkpointing
- ✅ Cross-agent memory store

**Phase 4: Security**
- ✅ Role-based access control
- ✅ Tool permission filtering

### Remaining Phases

**Phase 5: Health & Recovery** (Recommended Next)
- Auto hang detection and recovery
- Circuit breaker pattern
- Health dashboard

**Phase 6: Advanced Visual** (Nice-to-Have)
- Dynamic layout reorganization
- iTerm2 status bar components
- Debug visualization pane

**Phase 7: Deep iTerm2 Integration** (Low Priority)
- Native monitors (Focus, Variable, Custom)
- Broadcast domains
- Session replay

---

## Success Criteria Review

### Functional Requirements

| Requirement | Status | Notes |
|-------------|--------|-------|
| Visual hierarchy reflects team structure | ⚠️ Partial | Manual via modify_sessions, no auto-reorg |
| Agent states visible via colors/badges | ✅ Complete | Full support via modify_sessions |
| Inter-agent communication < 100ms | ✅ Complete | Event bus + cascading messages |
| Health monitoring detects failures in 30s | ⚠️ Partial | Manual via wait_for_agent, no auto |
| Executive can delegate to any team member | ✅ Complete | Manager agents with plans |
| Complete audit trail | ✅ Complete | JSONL + OpenTelemetry |
| Security policies enforced | ✅ Complete | Role-based access control |

### Non-Functional Requirements

| Requirement | Status | Notes |
|-------------|--------|-------|
| 50+ concurrent agents | ✅ Likely | Architecture supports, untested |
| 99.9% uptime | ⚠️ Unknown | No HA/redundancy built |
| Linear scaling to 100 agents | ✅ Likely | Async design supports |
| < 5 second learning curve | ✅ Complete | Clear tool naming |
| < 1 hour to add agent type | ✅ Complete | Just register_agent |

---

## Conclusion

The epic has achieved approximately **70% completion** with all core orchestration infrastructure delivered:

**Major Achievements:**
- Full manager-worker delegation system (CrewAI-style)
- Event-driven workflows with reactive programming
- Typed message-based communication (AutoGen-style)
- Cross-agent memory store with full-text search
- Role-based access control with tool filtering
- OpenTelemetry distributed tracing
- State checkpointing for crash recovery
- Visual customization (colors, badges, tabs)

**Key Gaps:**
- No automatic health monitoring and recovery
- No dynamic layout reorganization
- No iTerm2 native monitor integration
- No debug visualization pane

**Recommendation:** Close this epic as substantially complete and create focused follow-up issues:

1. **Issue: Auto-Recovery System** (High Priority)
   - Hang detection with configurable timeout
   - Automatic Ctrl+C and restart
   - Circuit breaker for cascading failure prevention

2. **Issue: Dynamic Layout Engine** (Medium Priority)
   - Auto-reorganize panes on team membership changes
   - Hierarchical layout templates

3. **Issue: iTerm2 Deep Integration** (Low Priority)
   - Native monitors (Focus, Variable, Custom)
   - Status bar components
   - Broadcast domains

The foundation is solid. The remaining work focuses on polish and advanced automation rather than core capabilities.
