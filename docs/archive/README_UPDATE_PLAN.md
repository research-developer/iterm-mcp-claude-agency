# README.md Update Plan: Testable Code Examples

## Overview

This plan outlines a comprehensive update to the README.md with Test-Driven Development (TDD) style code examples. Each example will include:
- The code snippet
- Expected behavior/output
- Pass/fail criteria
- Setup requirements
- Verification steps

## Target Audience

- Developers implementing multi-agent orchestration
- Users setting up team-based iTerm workflows
- Contributors wanting to understand feature capabilities

---

## Section 1: Profile Management & Team Colors

### Feature 1.1: Team Profile Creation with Auto-Assigned Colors

**Location in README**: After "Multi-Agent Orchestration" section

**Code Example**:

```python
import asyncio
import iterm2
from core.profiles import ProfileManager, ColorDistributor

async def test_team_profile_creation():
    """Create team profiles with evenly-distributed colors."""

    # Setup
    profile_manager = ProfileManager()
    teams = ["backend", "frontend", "devops", "ml", "security"]

    # Execute
    profiles = {}
    for team_name in teams:
        profile = profile_manager.get_or_create_team_profile(team_name)
        profiles[team_name] = profile

    # Save profiles to iTerm Dynamic Profiles
    profile_manager.save_profiles()

    # Verify
    print("Created profiles:")
    for team_name, profile in profiles.items():
        hue = profile.color.hue
        print(f"  {team_name}: hue={hue:.1f}°, GUID={profile.guid}")

    return profiles

# Expected Output:
# Created profiles:
#   backend: hue=180.0°, GUID=<uuid>
#   frontend: hue=270.0°, GUID=<uuid>
#   devops: hue=0.0°, GUID=<uuid>
#   ml: hue=90.0°, GUID=<uuid>
#   security: hue=225.0°, GUID=<uuid>
```

**Pass Criteria**:
- ✅ 5 team profiles created
- ✅ Each profile has unique GUID
- ✅ Hues are well distributed across the color wheel (minimum gap ≥ 40°)
- ✅ Profile file created at `~/Library/Application Support/iTerm2/DynamicProfiles/iterm-mcp-profiles.json`

**Fail Criteria**:
- ❌ Duplicate GUIDs
- ❌ Hues overlap (< 30° separation)
- ❌ Profile file not written

**Setup Requirements**:
```bash
# No iTerm2 connection needed - profiles work standalone
pip install -e .
python -c "from core.profiles import ProfileManager; ProfileManager()"
```

**Verification**:
```bash
# Check profile file was created
ls -la ~/Library/Application\ Support/iTerm2/DynamicProfiles/
cat ~/Library/Application\ Support/iTerm2/DynamicProfiles/iterm-mcp-profiles.json | jq '.Profiles[].Name'
```

---

### Feature 1.2: Maximum-Gap Color Distribution

**Location in README**: After team profile creation example

**Code Example**:

```python
from core.profiles import ColorDistributor

def test_color_distribution():
    """Verify maximum-gap color distribution algorithm."""

    # Setup
    distributor = ColorDistributor(saturation=70, lightness=38)

    # Execute - add colors sequentially
    colors = []
    for i in range(6):
        color = distributor.get_next_color()
        colors.append(color)
        print(f"Color {i+1}: hue={color.hue:.1f}°")

    # Verify gaps between consecutive hues
    hues = [c.hue for c in colors]
    hues_sorted = sorted(hues)

    gaps = []
    for i in range(len(hues_sorted)):
        start = hues_sorted[i]
        end = hues_sorted[(i + 1) % len(hues_sorted)]

        if end <= start:
            gap = (360 - start) + end
        else:
            gap = end - start
        gaps.append(gap)

    print(f"\nGaps between hues: {[f'{g:.1f}°' for g in gaps]}")
    print(f"Min gap: {min(gaps):.1f}°")
    print(f"Max gap: {max(gaps):.1f}°")

    return gaps

# Expected Output:
# Color 1: hue=180.0°
# Color 2: hue=0.0°
# Color 3: hue=90.0°
# Color 4: hue=270.0°
# Color 5: hue=45.0°
# Color 6: hue=225.0°
#
# Gaps between hues: ['45.0°', '45.0°', '90.0°', '45.0°', '45.0°', '90.0°']
# Min gap: 45.0°
# Max gap: 90.0°
```

**Pass Criteria**:
- ✅ First color starts at 180° (teal)
- ✅ Each subsequent color fills largest gap
- ✅ Minimum gap ≥ 40° for 6 colors
- ✅ Maximum gap ≤ 100° for 6 colors

**Fail Criteria**:
- ❌ Colors cluster together (gap < 30°)
- ❌ Uneven distribution (max gap > 2.5x min gap)

**Setup Requirements**:
```python
pip install -e .
```

**Verification**:
```python
# Run the test
gaps = test_color_distribution()
assert min(gaps) >= 40, "Colors too close together"
assert max(gaps) <= 100, "Gap too large"
print("✓ Color distribution test passed")
```

---

### Feature 1.3: Profile-to-Session Integration

**Location in README**: In "Session Management" section

**Code Example**:

```python
import asyncio
import iterm2
from core.terminal import ItermTerminal
from core.profiles import get_profile_manager
from core.agents import AgentRegistry

async def test_create_session_with_team_profile():
    """Create an iTerm session with team-specific profile."""

    # Setup
    connection = await iterm2.Connection.async_create()
    terminal = ItermTerminal(connection)
    await terminal.initialize()

    profile_manager = get_profile_manager()
    agent_registry = AgentRegistry()

    # Create team and profile
    team = agent_registry.create_team("backend", "Backend developers")
    profile = profile_manager.get_or_create_team_profile("backend")
    profile_manager.save_profiles()

    # Create session with team profile
    app = await iterm2.async_get_app(connection)

    # Get the iTerm profile by GUID
    iterm_profile = await iterm2.Profile.async_get(app, profile.guid)

    # Create new session with this profile
    window = app.current_terminal_window
    if window:
        session = await window.async_create_tab(profile=profile.guid)
        print(f"Created session with profile: {iterm_profile.name_}")

        # Verify tab color is set
        assert iterm_profile.use_tab_color == True
        assert iterm_profile.tab_color is not None

        hue = iterm_profile.tab_color.hue
        print(f"Tab color hue: {hue:.1f}°")
        print(f"Badge text: {iterm_profile.badge_text_}")

        return session

# Expected Output:
# Created session with profile: MCP Team: backend
# Tab color hue: 180.0°
# Badge text: 🤖 backend
```

**Pass Criteria**:
- ✅ Session created successfully
- ✅ Tab color enabled (`use_tab_color == True`)
- ✅ Tab color matches team color
- ✅ Badge text contains team name

**Fail Criteria**:
- ❌ Session creation fails
- ❌ Tab color not visible
- ❌ Badge text empty or incorrect

**Setup Requirements**:
```bash
# Requires iTerm2 running with Python API enabled
pip install -e .
# Enable Python API in iTerm2: Preferences → General → Magic → Enable Python API
```

**Verification**:
```python
asyncio.run(test_create_session_with_team_profile())
# Manually verify in iTerm2:
# - Tab has colored indicator on the left
# - Badge shows "🤖 backend" in top-right of terminal
```

---

## Section 2: Agent Registry & Team Management

### Feature 2.1: Agent Registration with Team Assignment

**Location in README**: After "Agent & Team Concepts" section

**Code Example**:

```python
from core.agents import AgentRegistry

def test_agent_registration():
    """Register agents and assign to teams."""

    # Setup
    registry = AgentRegistry()

    # Create teams
    backend_team = registry.create_team("backend", "Backend engineers")
    frontend_team = registry.create_team("frontend", "Frontend engineers")

    # Register agents
    alice = registry.register_agent(
        name="alice",
        session_id="session-001",
        teams=["backend"],
        metadata={"role": "senior", "specialty": "databases"}
    )

    bob = registry.register_agent(
        name="bob",
        session_id="session-002",
        teams=["frontend", "backend"],  # Multi-team agent
        metadata={"role": "fullstack"}
    )

    # Verify
    print("Registered agents:")
    for agent in registry.list_agents():
        print(f"  {agent.name}: teams={agent.teams}, session={agent.session_id}")

    # Test team queries
    backend_agents = registry.list_agents(team="backend")
    print(f"\nBackend team has {len(backend_agents)} agents")

    # Test agent lookup
    retrieved = registry.get_agent("alice")
    assert retrieved.name == "alice"
    assert "backend" in retrieved.teams

    return registry

# Expected Output:
# Registered agents:
#   alice: teams=['backend'], session=session-001
#   bob: teams=['frontend', 'backend'], session=session-002
#
# Backend team has 2 agents
```

**Pass Criteria**:
- ✅ Agents created with correct session IDs
- ✅ Team assignments recorded
- ✅ Metadata stored
- ✅ Team query returns correct agents
- ✅ Data persisted to `~/.iterm_mcp_logs/agents.jsonl`

**Fail Criteria**:
- ❌ Agent lookup returns None
- ❌ Team membership not recorded
- ❌ JSONL file not created

**Setup Requirements**:
```python
pip install -e .
```

**Verification**:
```bash
# Check persistence
cat ~/.iterm_mcp_logs/agents.jsonl
# Should contain 2 JSON lines with agent data

# Verify teams file
cat ~/.iterm_mcp_logs/teams.jsonl
# Should contain 2 JSON lines with team data
```

---

### Feature 2.2: Cascading Message Resolution

**Location in README**: In "Cascading Messages" section

**Code Example**:

```python
from core.agents import AgentRegistry, CascadingMessage

def test_cascading_message_resolution():
    """Test priority-based message cascading: agent > team > broadcast."""

    # Setup
    registry = AgentRegistry()

    # Create teams and agents
    registry.create_team("backend", "Backend team")
    registry.create_team("frontend", "Frontend team")

    registry.register_agent("alice", "session-1", teams=["backend"])
    registry.register_agent("bob", "session-2", teams=["backend"])
    registry.register_agent("charlie", "session-3", teams=["frontend"])
    registry.register_agent("diana", "session-4", teams=["frontend"])

    # Create cascading message
    cascade = CascadingMessage(
        broadcast="All hands: status update",
        teams={
            "backend": "Backend team: run migrations",
            "frontend": "Frontend team: run tests"
        },
        agents={
            "alice": "Alice: review the PR #123 specifically"
        }
    )

    # Resolve to specific targets
    targets = registry.resolve_cascade_targets(cascade)

    # Verify
    print("Message distribution:")
    for message, agents in targets.items():
        print(f"  '{message}' → {agents}")

    # Check priority resolution
    assert any("Alice: review" in msg for msg in targets.keys())
    assert "alice" in targets["Alice: review the PR #123 specifically"]

    # Bob gets team message (no specific agent message)
    assert any("Backend team" in msg for msg in targets.keys())

    # Charlie gets team message
    assert any("Frontend team" in msg for msg in targets.keys())

    return targets

# Expected Output:
# Message distribution:
#   'Alice: review the PR #123 specifically' → ['alice']
#   'Backend team: run migrations' → ['bob']
#   'Frontend team: run tests' → ['charlie', 'diana']
```

**Pass Criteria**:
- ✅ Alice receives agent-specific message (highest priority)
- ✅ Bob receives backend team message (no agent override)
- ✅ Charlie and Diana receive frontend team message
- ✅ No duplicate messages to same agent

**Fail Criteria**:
- ❌ Agent receives multiple messages
- ❌ Priority order incorrect
- ❌ Broadcast overrides team/agent messages

**Setup Requirements**:
```python
pip install -e .
```

**Verification**:
```python
targets = test_cascading_message_resolution()
# Verify no agent appears in multiple message targets
all_agents = []
for agents in targets.values():
    all_agents.extend(agents)
assert len(all_agents) == len(set(all_agents)), "Agent received duplicate messages"
print("✓ Cascading message test passed")
```

---

## Section 3: Multi-Agent Orchestration

### Feature 3.1: Parallel Session Operations

**Location in README**: In "Parallel Session Operations" section

**Code Example**:

```python
import asyncio
import iterm2
from core.terminal import ItermTerminal
from core.agents import AgentRegistry
from core.models import SessionMessage, SessionTarget, WriteToSessionsRequest

async def test_parallel_writes():
    """Write to multiple sessions in parallel."""

    # Setup
    connection = await iterm2.Connection.async_create()
    terminal = ItermTerminal(connection)
    await terminal.initialize()

    registry = AgentRegistry()
    registry.create_team("dev", "Development team")

    # Create 3 sessions
    sessions = []
    for i, name in enumerate(["alice", "bob", "charlie"]):
        session = await terminal.create_tab()
        await session.send_text(f"echo 'Agent {name} ready'", execute=True)
        registry.register_agent(name, session.id, teams=["dev"])
        sessions.append(session)

    # Parallel write to all dev team members
    request = WriteToSessionsRequest(
        messages=[
            SessionMessage(
                content="echo 'Running tests...'",
                targets=[SessionTarget(team="dev")],
                execute=True
            )
        ],
        parallel=True,
        skip_duplicates=False
    )

    # Execute (simulated - in real MCP this would be a tool call)
    import time
    start = time.time()

    # Send to all sessions simultaneously
    tasks = []
    for session in sessions:
        task = session.send_text("echo 'Running tests...'", execute=True)
        tasks.append(task)

    await asyncio.gather(*tasks)
    elapsed = time.time() - start

    print(f"Sent to {len(sessions)} sessions in {elapsed:.2f}s")
    print(f"Average time per session: {elapsed/len(sessions):.3f}s")

    # Verify all received the command
    await asyncio.sleep(0.5)
    for i, session in enumerate(sessions):
        output = await session.get_screen_contents()
        assert "Running tests..." in output
        print(f"  Session {i+1}: ✓ received command")

    return elapsed

# Expected Output:
# Sent to 3 sessions in 0.15s
# Average time per session: 0.050s
#   Session 1: ✓ received command
#   Session 2: ✓ received command
#   Session 3: ✓ received command
```

**Pass Criteria**:
- ✅ All sessions receive command
- ✅ Parallel execution faster than sequential (< 0.3s for 3 sessions)
- ✅ No errors during execution
- ✅ All outputs contain expected text

**Fail Criteria**:
- ❌ Any session missing command
- ❌ Execution time > 1s
- ❌ Exceptions raised

**Setup Requirements**:
```bash
pip install -e .
# iTerm2 must be running with Python API enabled
```

**Verification**:
```python
elapsed = asyncio.run(test_parallel_writes())
assert elapsed < 0.5, "Parallel writes too slow"
print("✓ Parallel write test passed")
```

---

### Feature 3.2: Playbook Orchestration

**Location in README**: In "Playbook Orchestration" section

**Code Example**:

```python
from core.models import (
    OrchestrateRequest,
    Playbook,
    CreateSessionsRequest,
    SessionConfig,
    PlaybookCommand,
    SessionMessage,
    SessionTarget,
    CascadeMessageRequest,
    ReadSessionsRequest,
    ReadTarget
)

def test_playbook_creation():
    """Create a multi-stage orchestration playbook."""

    # Define playbook
    playbook = Playbook(
        # Stage 1: Create layout
        layout=CreateSessionsRequest(
            sessions=[
                SessionConfig(name="Backend", agent="backend-agent", team="dev"),
                SessionConfig(name="Frontend", agent="frontend-agent", team="dev"),
                SessionConfig(name="QA", agent="qa-agent", team="qa")
            ],
            layout="HORIZONTAL_SPLIT"
        ),

        # Stage 2: Run commands
        commands=[
            PlaybookCommand(
                name="bootstrap",
                messages=[
                    SessionMessage(
                        content="cd ~/project && git pull",
                        targets=[SessionTarget(team="dev")],
                        execute=True
                    )
                ],
                parallel=True
            ),
            PlaybookCommand(
                name="test",
                messages=[
                    SessionMessage(
                        content="npm test",
                        targets=[SessionTarget(agent="frontend-agent")],
                        execute=True
                    ),
                    SessionMessage(
                        content="pytest",
                        targets=[SessionTarget(agent="backend-agent")],
                        execute=True
                    )
                ],
                parallel=True
            )
        ],

        # Stage 3: Cascade status update
        cascade=CascadeMessageRequest(
            broadcast="Tests complete - check results",
            teams={"qa": "QA team: begin smoke tests"}
        ),

        # Stage 4: Read outputs
        reads=ReadSessionsRequest(
            targets=[
                ReadTarget(team="dev", max_lines=50)
            ],
            parallel=True,
            filter_pattern="PASS|FAIL|ERROR"
        )
    )

    # Verify structure
    assert len(playbook.commands) == 2
    assert playbook.layout.sessions[0].name == "Backend"
    assert playbook.cascade.broadcast is not None

    # Convert to request
    request = OrchestrateRequest(playbook=playbook)

    print("Playbook structure:")
    print(f"  Layout: {len(request.playbook.layout.sessions)} sessions")
    print(f"  Commands: {len(request.playbook.commands)} stages")
    print(f"  Cascade: broadcast + {len(request.playbook.cascade.teams)} teams")
    print(f"  Reads: {len(request.playbook.reads.targets)} targets")

    return request

# Expected Output:
# Playbook structure:
#   Layout: 3 sessions
#   Commands: 2 stages
#   Cascade: broadcast + 1 teams
#   Reads: 1 targets
```

**Pass Criteria**:
- ✅ Playbook contains all stages (layout, commands, cascade, reads)
- ✅ Sessions configured with agent/team assignments
- ✅ Commands target correct agents/teams
- ✅ Cascade includes broadcast and team-specific messages

**Fail Criteria**:
- ❌ Missing any stage
- ❌ Invalid session configuration
- ❌ Incorrect targeting

**Setup Requirements**:
```python
pip install -e .
```

**Verification**:
```python
request = test_playbook_creation()
# Validate Pydantic model
request.model_validate(request.model_dump())
print("✓ Playbook validation passed")
```

---

## Section 4: Session Management & Monitoring

### Feature 4.1: Session Creation with Agent Types

**Location in README**: In "Session Management Tools" section

**Code Example**:

```python
import asyncio
import iterm2
from core.terminal import ItermTerminal
from core.models import CreateSessionsRequest, SessionConfig

async def test_agent_type_launch():
    """Create sessions with different AI agent CLIs."""

    # Setup
    connection = await iterm2.Connection.async_create()
    terminal = ItermTerminal(connection)
    await terminal.initialize()

    # Create sessions with agent types
    request = CreateSessionsRequest(
        sessions=[
            SessionConfig(
                name="Claude Agent",
                agent="claude-1",
                agent_type="claude",  # Launches "claude" CLI
                team="ai-agents"
            ),
            SessionConfig(
                name="Gemini Agent",
                agent="gemini-1",
                agent_type="gemini",  # Launches "gemini" CLI
                team="ai-agents"
            )
        ],
        layout="VERTICAL_SPLIT"
    )

    # Simulate session creation (actual implementation in MCP server)
    sessions = []
    for config in request.sessions:
        session = await terminal.create_tab()

        # Launch agent CLI
        from core.models import AGENT_CLI_COMMANDS
        command = AGENT_CLI_COMMANDS.get(config.agent_type, "")
        if command:
            await session.send_text(f"{command}", execute=True)

        sessions.append(session)
        print(f"Created: {config.name} → launched '{command}'")

    # Verify CLIs launched
    await asyncio.sleep(1.0)

    for i, session in enumerate(sessions):
        output = await session.get_screen_contents()
        config = request.sessions[i]

        # Check for CLI prompt
        print(f"  {config.name}: {'✓' if len(output) > 0 else '✗'} CLI active")

    return sessions

# Expected Output:
# Created: Claude Agent → launched 'claude'
# Created: Gemini Agent → launched 'gemini'
#   Claude Agent: ✓ CLI active
#   Gemini Agent: ✓ CLI active
```

**Pass Criteria**:
- ✅ Sessions created with correct layout
- ✅ Agent CLIs launched in each session
- ✅ Sessions respond to input

**Fail Criteria**:
- ❌ CLI command not found
- ❌ Session creation fails
- ❌ No output after CLI launch

**Setup Requirements**:
```bash
# Install AI agent CLIs first
brew install claude-cli  # or npm install -g @anthropic/claude-cli
pip install google-generativeai  # for gemini

# Then install iterm-mcp
pip install -e .
```

**Verification**:
```python
asyncio.run(test_agent_type_launch())
# Manually verify in iTerm2:
# - Two panes visible (vertical split)
# - Each pane shows AI agent prompt
```

---

### Feature 4.2: Session Lock Management

**Location in README**: New subsection under "Agent & Team Management"

**Code Example**:

```python
from core.tags import SessionTagLockManager
from core.agents import AgentRegistry

def test_session_locking():
    """Test session lock enforcement for agent exclusivity."""

    # Setup
    lock_manager = SessionTagLockManager()
    registry = AgentRegistry(lock_manager=lock_manager)

    # Register agents
    alice = registry.register_agent("alice", "session-1")
    bob = registry.register_agent("bob", "session-2")

    # Alice locks her session
    success = lock_manager.lock_session("session-1", "alice")
    assert success, "Lock acquisition failed"
    print("✓ Alice locked session-1")

    # Bob tries to lock Alice's session
    success = lock_manager.lock_session("session-1", "bob")
    assert not success, "Lock should be denied"
    print("✓ Bob correctly denied access to session-1")

    # Check lock status
    lock_info = lock_manager.get_lock("session-1")
    assert lock_info is not None
    print(f"✓ Lock info confirmed: {lock_info}")

    # Alice releases lock
    released = lock_manager.unlock_session("session-1", "alice")
    assert released
    print("✓ Alice released session-1")

    # Bob can now lock
    success = lock_manager.lock_session("session-1", "bob")
    assert success
    print("✓ Bob successfully locked session-1")

    return lock_manager

# Expected Output:
# ✓ Alice locked session-1
# ✓ Bob correctly denied access to session-1
# ✓ Lock info confirmed: {...}
# ✓ Alice released session-1
# ✓ Bob successfully locked session-1
```

**Pass Criteria**:
- ✅ Lock acquired successfully by owner
- ✅ Lock denied to non-owner
- ✅ Owner can release lock
- ✅ New owner can acquire after release

**Fail Criteria**:
- ❌ Multiple agents can lock same session
- ❌ Lock persists after release
- ❌ Non-owner can release lock

**Setup Requirements**:
```python
pip install -e .
```

**Verification**:
```python
lock_manager = test_session_locking()
# Verify Bob's lock is active
assert lock_manager.get_lock("session-1") is not None  # Bob's lock
print("✓ Lock management test passed")
```

---

## Section 5: Notification System

### Feature 5.1: Agent Status Notifications

**Location in README**: New section "Agent Notifications & Monitoring"

**Code Example**:

```python
import asyncio
from datetime import datetime
from core.models import AgentNotification
from iterm_mcpy.fastmcp_server import NotificationManager

async def test_notification_system():
    """Test agent notification management."""

    # Setup
    notifier = NotificationManager(max_per_agent=50, max_total=200)

    # Add notifications for different agents
    await notifier.add_simple(
        agent="alice",
        level="info",
        summary="Started task: code review",
        context="Reviewing PR #123",
        action_hint="Waiting for CI to complete"
    )

    await notifier.add_simple(
        agent="bob",
        level="error",
        summary="Build failed",
        context="ESLint errors in main.ts",
        action_hint="Run: npm run lint --fix"
    )

    await notifier.add_simple(
        agent="charlie",
        level="success",
        summary="Tests passed",
        context="All 47 tests green"
    )

    await notifier.add_simple(
        agent="alice",
        level="blocked",
        summary="Waiting for approval",
        context="PR #123 needs review from tech lead"
    )

    # Get all notifications
    all_notifs = await notifier.get(limit=10)
    print(f"Total notifications: {len(all_notifs)}")

    # Get notifications by level
    errors = await notifier.get(level="error")
    print(f"Error notifications: {len(errors)}")

    # Get latest per agent
    latest = await notifier.get_latest_per_agent()
    print(f"\nLatest status per agent:")
    for agent, notif in latest.items():
        icon = NotificationManager.STATUS_ICONS[notif.level]
        print(f"  {agent}: {icon} {notif.summary}")

    # Format compact display
    compact = notifier.format_compact(all_notifs[:3])
    print(f"\nCompact display:\n{compact}")

    return notifier

# Expected Output:
# Total notifications: 4
# Error notifications: 1
#
# Latest status per agent:
#   alice: ⏸ Waiting for approval
#   bob: ✗ Build failed
#   charlie: ✓ Tests passed
#
# Compact display:
# ━━━ Agent Status ━━━
# alice        ⏸ Waiting for approval
# bob          ✗ Build failed
# charlie      ✓ Tests passed
# ━━━━━━━━━━━━━━━━━━━━
```

**Pass Criteria**:
- ✅ Notifications added for all agents
- ✅ Latest notification per agent retrieved correctly
- ✅ Filtering by level works
- ✅ Compact format displays correctly

**Fail Criteria**:
- ❌ Notifications lost or duplicated
- ❌ Wrong agent associated with notification
- ❌ Filtering returns incorrect results

**Setup Requirements**:
```python
pip install -e .
```

**Verification**:
```python
notifier = asyncio.run(test_notification_system())
# Verify ring buffer limits work
for i in range(250):  # Exceed max_total=200
    asyncio.run(notifier.add_simple(f"agent-{i%10}", "info", f"Task {i}"))
all_notifs = asyncio.run(notifier.get(limit=300))
assert len(all_notifs) <= 200, "Ring buffer exceeded limit"
print("✓ Notification system test passed")
```

---

## Section 6: Integration Examples

### Feature 6.1: Complete Multi-Agent Workflow

**Location in README**: New section "Complete Examples" at end

**Code Example**:

```python
import asyncio
import iterm2
from core.terminal import ItermTerminal
from core.agents import AgentRegistry
from core.profiles import get_profile_manager

async def complete_workflow_example():
    """Complete workflow: profiles → teams → agents → orchestration."""

    print("=== Multi-Agent Workflow Example ===\n")

    # 1. Setup profiles
    print("1. Creating team profiles...")
    profile_manager = get_profile_manager()

    backend_profile = profile_manager.get_or_create_team_profile("backend")
    frontend_profile = profile_manager.get_or_create_team_profile("frontend")
    profile_manager.save_profiles()
    print(f"   ✓ Backend profile (hue={backend_profile.color.hue:.0f}°)")
    print(f"   ✓ Frontend profile (hue={frontend_profile.color.hue:.0f}°)")

    # 2. Setup teams
    print("\n2. Creating teams...")
    registry = AgentRegistry()
    registry.create_team("backend", "Backend developers")
    registry.create_team("frontend", "Frontend developers")
    print("   ✓ Teams created")

    # 3. Create sessions
    print("\n3. Creating iTerm sessions...")
    connection = await iterm2.Connection.async_create()
    terminal = ItermTerminal(connection)
    await terminal.initialize()

    # Create backend sessions
    backend_session = await terminal.create_tab()
    await backend_session.send_text("echo 'Backend agent ready'", execute=True)

    # Create frontend session
    frontend_session = await terminal.create_tab()
    await frontend_session.send_text("echo 'Frontend agent ready'", execute=True)

    print("   ✓ Sessions created")

    # 4. Register agents
    print("\n4. Registering agents...")
    alice = registry.register_agent(
        "alice",
        backend_session.id,
        teams=["backend"],
        metadata={"profile_guid": backend_profile.guid}
    )

    bob = registry.register_agent(
        "bob",
        frontend_session.id,
        teams=["frontend"],
        metadata={"profile_guid": frontend_profile.guid}
    )
    print(f"   ✓ Registered: {alice.name} (backend)")
    print(f"   ✓ Registered: {bob.name} (frontend)")

    # 5. Send cascading message
    print("\n5. Sending cascading messages...")
    from core.agents import CascadingMessage

    cascade = CascadingMessage(
        broadcast="All: Daily standup in 10 minutes",
        teams={
            "backend": "Backend: Deploy hotfix before standup",
            "frontend": "Frontend: Update staging environment"
        },
        agents={
            "alice": "Alice: Review database migration PR first"
        }
    )

    targets = registry.resolve_cascade_targets(cascade)
    for message, agents in targets.items():
        print(f"   → '{message[:40]}...' → {agents}")

    # 6. Read outputs
    print("\n6. Reading session outputs...")
    alice_output = await backend_session.get_screen_contents()
    bob_output = await frontend_session.get_screen_contents()

    print(f"   Alice's session: {len(alice_output)} chars")
    print(f"   Bob's session: {len(bob_output)} chars")

    print("\n=== Workflow Complete ===")

    return {
        "profiles": [backend_profile, frontend_profile],
        "agents": [alice, bob],
        "sessions": [backend_session, frontend_session]
    }

# Run the complete workflow
# asyncio.run(complete_workflow_example())

# Expected Output:
# === Multi-Agent Workflow Example ===
#
# 1. Creating team profiles...
#    ✓ Backend profile (hue=180°)
#    ✓ Frontend profile (hue=270°)
#
# 2. Creating teams...
#    ✓ Teams created
#
# 3. Creating iTerm sessions...
#    ✓ Sessions created
#
# 4. Registering agents...
#    ✓ Registered: alice (backend)
#    ✓ Registered: bob (frontend)
#
# 5. Sending cascading messages...
#    → 'Alice: Review database migration PR fi...' → ['alice']
#    → 'Backend: Deploy hotfix before standup...' → []
#    → 'Frontend: Update staging environment...' → ['bob']
#
# 6. Reading session outputs...
#    Alice's session: 245 chars
#    Bob's session: 239 chars
#
# === Workflow Complete ===
```

**Pass Criteria**:
- ✅ All 6 stages complete without errors
- ✅ Profiles created with distinct colors
- ✅ Teams and agents registered
- ✅ Cascading messages resolved correctly
- ✅ Session outputs readable

**Fail Criteria**:
- ❌ Any stage raises exception
- ❌ Agents not assigned to teams
- ❌ Messages not cascaded properly

**Setup Requirements**:
```bash
pip install -e .
# iTerm2 running with Python API enabled
```

**Verification**:
```python
result = asyncio.run(complete_workflow_example())
assert len(result["profiles"]) == 2
assert len(result["agents"]) == 2
assert len(result["sessions"]) == 2
print("✓ Complete workflow test passed")
```

---

## Implementation Checklist

### Phase 1: Core Features (Week 1)
- [ ] Add Profile Management section with 3 examples
- [ ] Add Agent Registry section with 2 examples
- [ ] Update existing "Multi-Agent Orchestration" with testable examples

### Phase 2: Advanced Features (Week 2)
- [ ] Add Session Management examples
- [ ] Add Notification System section
- [ ] Add Session Lock Management section

### Phase 3: Integration (Week 3)
- [ ] Add complete workflow example
- [ ] Create "Testing Your Setup" section
- [ ] Add troubleshooting guide for common failures

### Phase 4: Testing & Validation (Week 4)
- [ ] Create test runner script for all examples
- [ ] Add CI job to run testable examples
- [ ] Update documentation based on test results
- [ ] Create video walkthrough of examples

---

## Testing Infrastructure

### Test Runner Script

Create `scripts/test_readme_examples.py`:

```python
#!/usr/bin/env python3
"""Run all README examples and verify pass criteria."""

import asyncio
import sys
from pathlib import Path

# Import all test functions
from examples.profile_management import (
    test_team_profile_creation,
    test_color_distribution,
    test_create_session_with_team_profile
)

from examples.agent_registry import (
    test_agent_registration,
    test_cascading_message_resolution
)

# ... etc

async def run_all_tests():
    """Run all README examples."""

    results = []

    # Run each test
    tests = [
        ("Profile Creation", test_team_profile_creation),
        ("Color Distribution", test_color_distribution),
        ("Agent Registration", test_agent_registration),
        # ... etc
    ]

    for name, test_func in tests:
        print(f"\nRunning: {name}")
        try:
            if asyncio.iscoroutinefunction(test_func):
                await test_func()
            else:
                test_func()
            print(f"  ✓ PASSED")
            results.append((name, True))
        except Exception as e:
            print(f"  ✗ FAILED: {e}")
            results.append((name, False))

    # Summary
    print("\n" + "="*50)
    print("SUMMARY")
    print("="*50)

    passed = sum(1 for _, p in results if p)
    total = len(results)

    for name, passed in results:
        status = "✓" if passed else "✗"
        print(f"{status} {name}")

    print(f"\nPassed: {passed}/{total}")

    return passed == total

if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
```

### CI Integration

Add to `.github/workflows/test-examples.yml`:

```yaml
name: Test README Examples

on: [push, pull_request]

jobs:
  test-examples:
    runs-on: macos-latest

    steps:
      - uses: actions/checkout@v3

      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install -e ".[dev]"

      - name: Run README examples
        run: |
          python scripts/test_readme_examples.py
```

---

## Documentation Standards

### Example Format

Each example must include:

1. **Title**: Clear, descriptive name
2. **Code Block**: Complete, runnable code
3. **Expected Output**: Exact output with formatting
4. **Pass Criteria**: Bulleted list with ✅
5. **Fail Criteria**: Bulleted list with ❌
6. **Setup Requirements**: Commands to run before test
7. **Verification**: How to confirm it worked

### Code Style

- Use descriptive variable names
- Include type hints where helpful
- Add inline comments for complex logic
- Keep examples under 50 lines when possible
- Use async/await consistently

### Output Format

- Show realistic output with actual values
- Use consistent formatting (timestamps, IDs, etc.)
- Highlight key values with formatting
- Show both success and failure cases where relevant

---

## Migration Path

### For Existing README

1. **Keep current structure**: Don't remove existing content
2. **Add new sections**: Insert testable examples after narrative text
3. **Update cross-references**: Link to new examples from feature descriptions
4. **Add "Try It" boxes**: Highlight runnable examples with special formatting

### Example Migration

**Before**:
```markdown
## Multi-Agent Orchestration

The iTerm MCP server supports coordinating multiple Claude Code instances...
```

**After**:
```markdown
## Multi-Agent Orchestration

The iTerm MCP server supports coordinating multiple Claude Code instances...

### Try It: Cascading Messages

> **Runnable Example** - Copy-paste this code to test cascading messages

[Full testable example here with pass/fail criteria]
```

---

## Success Metrics

- [ ] At least 15 testable examples added
- [ ] 100% of examples have pass/fail criteria
- [ ] All examples run in CI without errors
- [ ] Documentation build time < 30s
- [ ] User feedback: "examples helped me get started"

---

## Future Enhancements

1. **Interactive examples**: Add Jupyter notebook versions
2. **Video walkthroughs**: Screen recordings of examples running
3. **Playground**: Web-based example runner
4. **Community examples**: Section for user-contributed examples
5. **Language ports**: Examples in TypeScript, Go, etc.
