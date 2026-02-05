# Research Synthesis Addendum: Features Beyond Current Epic

> **Status**: PROPOSAL - Supplement to EPIC_PROPOSAL_MULTI_AGENT_ORCHESTRATION.md
>
> **Date**: January 2026
>
> This document identifies features found in community research that are **NOT** covered in the current epic proposal or codebase, representing opportunities for future development.

## Research Sources Analyzed

| Source | Focus |
|--------|-------|
| `research/synthesized-findings.md` | LangGraph, AutoGen, CrewAI, AG2, Agency Swarm patterns |
| `research/github-findings.md` | GitHub framework analysis (AutoGen, CrewAI, LangGraph, etc.) |
| `research/pypi-findings.md` | PyPI package survey (AG2, pexpect, dramatiq) |
| `research/community-insights.md` | Developer pain points, best practices, MCP adoption |

---

## Executive Summary

After comparing research recommendations against `EPIC_PROPOSAL_MULTI_AGENT_ORCHESTRATION.md` and the current codebase (commits through January 2026), we identified **12 features** that are either:
- Not mentioned in the epic proposal at all
- Mentioned but marked as "Not Implemented" with no follow-up issue

### Implementation Status Cross-Reference

| Research Recommendation | In Epic? | Implemented? | Status |
|------------------------|----------|--------------|--------|
| State Persistence & Checkpointing | Yes | Yes | Issue #57, #71 |
| OpenTelemetry Integration | Yes | Yes | Issue #58, #70 |
| Cross-Agent Memory Store | Yes | Yes | Issue #74 |
| Typed Message Communication | Yes | Yes | Issue #62, #67 |
| Role-Based Session Specialization | Yes | Yes | Issue #61, #72 |
| Expect-Style Pattern Matching | Yes | Yes | Issue #59, #66 |
| Hierarchical Task Delegation | Yes | Yes | Issue #65, #69, #75 |
| Event-Driven Flow Control | Yes | Yes | Issue #64, #73 |
| **Termination Conditions** | No | No | **NEW** |
| **Agent Handoff Protocol** | Partial | No | **NEW** |
| **Pipeline Architecture** | No | No | **NEW** |
| **Cost/Token Tracking** | No | No | **NEW** |
| **Intent Classification** | No | No | **NEW** |
| **No-Code GUI** | No | No | **NEW** |
| **Distributed Task Queue** | No | No | **NEW** |
| **Barrier Primitive** | Yes | No | Epic mentions, no impl |
| **Voting/Consensus** | Yes | No | Epic mentions, no impl |
| **Leader Election** | No | No | **NEW** |
| **Secret Redaction** | Yes | No | Epic mentions, no impl |
| **Session Recording/Replay** | Yes | No | Epic mentions, no impl |

---

## New Feature Proposals

### 1. Termination Conditions

**Source**: AutoGen, AG2
**Priority**: High
**Effort**: 2-3 days

**Description**: Composable conditions for automatic session/workflow termination.

**Pattern from AG2**:
```python
from autogen import MaxMessageTermination, TextMentionTermination, TimeoutTermination

await crew.run(
    termination=MaxMessageTermination(100) | TextMentionTermination("DONE") | TimeoutTermination(300)
)
```

**Proposed Implementation**:
```python
# core/termination.py
from abc import ABC, abstractmethod
from typing import Union

class TerminationCondition(ABC):
    @abstractmethod
    async def check(self, context: dict) -> bool:
        """Return True if termination condition is met."""
        pass

    def __or__(self, other: "TerminationCondition") -> "OrCondition":
        return OrCondition(self, other)

    def __and__(self, other: "TerminationCondition") -> "AndCondition":
        return AndCondition(self, other)

class MaxMessages(TerminationCondition):
    def __init__(self, max_count: int):
        self.max_count = max_count

    async def check(self, context: dict) -> bool:
        return context.get("message_count", 0) >= self.max_count

class TextMention(TerminationCondition):
    def __init__(self, text: str):
        self.text = text

    async def check(self, context: dict) -> bool:
        return self.text in context.get("last_output", "")

class Timeout(TerminationCondition):
    def __init__(self, seconds: int):
        self.seconds = seconds

    async def check(self, context: dict) -> bool:
        elapsed = context.get("elapsed_seconds", 0)
        return elapsed >= self.seconds

class OutputPattern(TerminationCondition):
    def __init__(self, pattern: str):
        self.pattern = re.compile(pattern)

    async def check(self, context: dict) -> bool:
        return bool(self.pattern.search(context.get("last_output", "")))
```

**MCP Tool**:
```python
@mcp.tool()
async def set_session_termination(
    target: SessionTarget,
    conditions: List[TerminationConditionSpec],
    operator: Literal["or", "and"] = "or"
) -> dict:
    """Set termination conditions for a session."""
```

**Benefits**:
- Prevent runaway sessions
- Flexible exit criteria
- Resource management
- Composable logic (OR/AND combinations)

---

### 2. Agent Handoff Protocol

**Source**: OpenAI Swarm, Agency Swarm
**Priority**: High
**Effort**: 2-3 days

**Description**: Explicit session-to-session control transfer with context passing.

**Pattern from Swarm**:
```python
# Agent A completes task, hands off to Agent B
def transfer_to_agent_b():
    return AgentB  # Swarm pattern - return agent to transfer

# With context
handoff = Handoff(
    target=agent_b,
    context={"task": "continue build", "artifacts": ["/path/to/output"]},
    reason="Build complete, ready for testing"
)
```

**Proposed Implementation**:
```python
# core/handoff.py
from pydantic import BaseModel
from typing import Optional, Dict, Any

class Handoff(BaseModel):
    """Represents a control transfer between agents."""
    source_agent: str
    target_agent: str
    context: Dict[str, Any] = {}
    reason: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class HandoffManager:
    def __init__(self, registry: AgentRegistry):
        self.registry = registry
        self.handoff_history: List[Handoff] = []

    async def initiate_handoff(
        self,
        source: str,
        target: str,
        context: Dict[str, Any],
        reason: str = ""
    ) -> Handoff:
        """Transfer control from source agent to target agent."""
        # 1. Validate both agents exist
        # 2. Capture source session state
        # 3. Send context to target
        # 4. Focus target session
        # 5. Record handoff
        handoff = Handoff(
            source_agent=source,
            target_agent=target,
            context=context,
            reason=reason
        )
        self.handoff_history.append(handoff)

        # Notify via cascading message
        await self._notify_handoff(handoff)
        return handoff
```

**MCP Tools**:
```python
@mcp.tool()
async def handoff_to_agent(
    source_agent: str,
    target_agent: str,
    context: Dict[str, Any],
    reason: str = "",
    focus_target: bool = True
) -> dict:
    """Transfer control from one agent to another with context."""

@mcp.tool()
async def get_handoff_history(
    agent: Optional[str] = None,
    limit: int = 10
) -> List[dict]:
    """Get handoff history, optionally filtered by agent."""
```

**Benefits**:
- Clear responsibility transfer
- Context preservation across agent boundaries
- Audit trail for handoffs
- Supports complex multi-step workflows

---

### 3. Pipeline Architecture

**Source**: Haystack
**Priority**: Medium
**Effort**: 3-4 days

**Description**: Chainable terminal operations as reusable components.

**Pattern from Haystack**:
```python
from haystack import Pipeline
from haystack.components import Retriever, Reader

pipeline = Pipeline()
pipeline.add("retriever", Retriever())
pipeline.add("reader", Reader())
pipeline.connect("retriever", "reader")

result = pipeline.run({"query": "What is X?"})
```

**Proposed Implementation**:
```python
# core/pipeline.py
from typing import Callable, Dict, Any, List

class TerminalOperation(ABC):
    """Base class for pipeline operations."""

    @abstractmethod
    async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        pass

class CommandOperation(TerminalOperation):
    """Execute a command in a session."""

    def __init__(self, session_target: SessionTarget, command_template: str):
        self.session_target = session_target
        self.command_template = command_template

    async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        command = self.command_template.format(**input_data)
        output = await execute_in_session(self.session_target, command)
        return {**input_data, "output": output}

class FilterOperation(TerminalOperation):
    """Filter output using regex."""

    def __init__(self, pattern: str, extract_group: int = 0):
        self.pattern = re.compile(pattern)
        self.extract_group = extract_group

    async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        match = self.pattern.search(input_data.get("output", ""))
        extracted = match.group(self.extract_group) if match else None
        return {**input_data, "extracted": extracted}

class ConditionalBranch(TerminalOperation):
    """Branch based on condition."""

    def __init__(self, condition: Callable, if_true: str, if_false: str):
        self.condition = condition
        self.if_true = if_true
        self.if_false = if_false

class TerminalPipeline:
    """Chain terminal operations together."""

    def __init__(self, name: str):
        self.name = name
        self.operations: Dict[str, TerminalOperation] = {}
        self.connections: List[Tuple[str, str]] = []

    def add(self, name: str, operation: TerminalOperation):
        self.operations[name] = operation
        return self

    def connect(self, source: str, target: str):
        self.connections.append((source, target))
        return self

    async def run(self, initial_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the pipeline."""
        # Topological sort and execute
        pass
```

**MCP Tools**:
```python
@mcp.tool()
async def create_pipeline(
    name: str,
    operations: List[OperationSpec],
    connections: List[Tuple[str, str]]
) -> dict:
    """Create a reusable terminal pipeline."""

@mcp.tool()
async def run_pipeline(
    pipeline_name: str,
    input_data: Dict[str, Any]
) -> dict:
    """Execute a saved pipeline with input data."""

@mcp.tool()
async def list_pipelines() -> List[dict]:
    """List all saved pipelines."""
```

**Benefits**:
- Reusable operation chains
- Composable workflows
- Easy to test individual components
- Visual representation possible

---

### 4. Cost/Token Tracking

**Source**: Community insights (Stack Overflow survey)
**Priority**: Medium
**Effort**: 1-2 days

**Description**: Track command execution metrics for cost estimation.

**Proposed Implementation**:
```python
# core/cost_tracking.py
from dataclasses import dataclass, field
from datetime import datetime, timedelta

@dataclass
class SessionMetrics:
    session_id: str
    agent: Optional[str] = None
    command_count: int = 0
    total_output_chars: int = 0
    total_duration_seconds: float = 0.0
    start_time: datetime = field(default_factory=datetime.utcnow)
    commands: List[CommandMetric] = field(default_factory=list)

@dataclass
class CommandMetric:
    command: str
    output_chars: int
    duration_seconds: float
    timestamp: datetime

class CostTracker:
    def __init__(self):
        self.sessions: Dict[str, SessionMetrics] = {}

    def record_command(
        self,
        session_id: str,
        command: str,
        output_chars: int,
        duration: float
    ):
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionMetrics(session_id=session_id)

        metrics = self.sessions[session_id]
        metrics.command_count += 1
        metrics.total_output_chars += output_chars
        metrics.total_duration_seconds += duration
        metrics.commands.append(CommandMetric(
            command=command,
            output_chars=output_chars,
            duration_seconds=duration,
            timestamp=datetime.utcnow()
        ))

    def get_summary(self, session_id: Optional[str] = None) -> dict:
        """Get cost summary for session(s)."""
        pass

    def estimate_cost(self, rate_per_1k_chars: float = 0.001) -> float:
        """Estimate cost based on output volume."""
        pass
```

**MCP Tools**:
```python
@mcp.tool()
async def get_session_metrics(
    session_id: Optional[str] = None,
    agent: Optional[str] = None,
    since: Optional[datetime] = None
) -> dict:
    """Get execution metrics for sessions."""

@mcp.tool()
async def get_cost_estimate(
    rate_per_command: float = 0.01,
    rate_per_1k_output: float = 0.001
) -> dict:
    """Estimate costs based on session activity."""
```

**Benefits**:
- Budget management
- Usage transparency
- Optimization insights
- Per-agent cost attribution

---

### 5. Intent Classification for Routing

**Source**: AWS Multi-Agent Orchestrator (agent-squad)
**Priority**: Low
**Effort**: 3-4 days

**Description**: Route commands to appropriate sessions based on intent analysis.

**Proposed Implementation**:
```python
# core/intent.py
from enum import Enum
from typing import List, Tuple

class Intent(Enum):
    BUILD = "build"
    TEST = "test"
    DEPLOY = "deploy"
    DEBUG = "debug"
    MONITOR = "monitor"
    GENERAL = "general"

class IntentClassifier:
    """Classify command intent for routing."""

    def __init__(self):
        # Keyword-based classification (could be ML-based later)
        self.patterns = {
            Intent.BUILD: ["npm run build", "cargo build", "make", "go build"],
            Intent.TEST: ["pytest", "npm test", "cargo test", "go test"],
            Intent.DEPLOY: ["deploy", "kubectl", "docker push", "terraform"],
            Intent.DEBUG: ["debug", "gdb", "lldb", "pdb"],
            Intent.MONITOR: ["tail", "watch", "htop", "top", "logs"],
        }

    def classify(self, command: str) -> Tuple[Intent, float]:
        """Classify command intent with confidence score."""
        # Simple keyword matching (could use embeddings)
        for intent, keywords in self.patterns.items():
            for keyword in keywords:
                if keyword in command.lower():
                    return intent, 0.9
        return Intent.GENERAL, 0.5

    def route_to_agent(self, command: str, available_agents: List[Agent]) -> Optional[Agent]:
        """Route command to best-fit agent based on intent and role."""
        intent, confidence = self.classify(command)

        # Map intents to roles
        intent_role_map = {
            Intent.BUILD: SessionRole.BUILDER,
            Intent.TEST: SessionRole.TESTER,
            Intent.DEPLOY: SessionRole.DEVOPS,
            Intent.DEBUG: SessionRole.DEBUGGER,
            Intent.MONITOR: SessionRole.MONITOR,
        }

        target_role = intent_role_map.get(intent)
        if target_role:
            for agent in available_agents:
                if agent.role == target_role:
                    return agent

        return None
```

**MCP Tools**:
```python
@mcp.tool()
async def classify_intent(command: str) -> dict:
    """Classify command intent."""

@mcp.tool()
async def route_command(
    command: str,
    available_agents: Optional[List[str]] = None
) -> dict:
    """Route command to appropriate agent based on intent."""
```

---

### 6. Coordination Primitives

**Source**: EPIC_PROPOSAL (mentioned but not implemented)
**Priority**: Medium
**Effort**: 2-3 days

**Description**: Advanced coordination patterns for multi-agent workflows.

#### 6a. Barrier Primitive

```python
# core/coordination.py

class Barrier:
    """Wait for all specified agents before proceeding."""

    def __init__(self, agents: List[str], timeout: int = 60):
        self.agents = set(agents)
        self.ready: Set[str] = set()
        self.timeout = timeout
        self._event = asyncio.Event()

    async def mark_ready(self, agent: str):
        if agent in self.agents:
            self.ready.add(agent)
            if self.ready == self.agents:
                self._event.set()

    async def wait(self) -> bool:
        try:
            await asyncio.wait_for(self._event.wait(), timeout=self.timeout)
            return True
        except asyncio.TimeoutError:
            return False
```

#### 6b. Voting/Consensus

```python
class VotingRound:
    """Collect votes from agents on a decision."""

    def __init__(self, question: str, options: List[str], voters: List[str]):
        self.question = question
        self.options = options
        self.voters = set(voters)
        self.votes: Dict[str, str] = {}

    async def cast_vote(self, agent: str, option: str):
        if agent in self.voters and option in self.options:
            self.votes[agent] = option

    def get_result(self) -> Optional[str]:
        if len(self.votes) < len(self.voters):
            return None  # Not all votes in

        counts = Counter(self.votes.values())
        winner, count = counts.most_common(1)[0]
        return winner
```

#### 6c. Leader Election

```python
class LeaderElection:
    """Simple leader election for dynamic orchestrator selection."""

    def __init__(self, candidates: List[str]):
        self.candidates = candidates
        self.leader: Optional[str] = None
        self.term: int = 0

    async def elect(self) -> str:
        """Elect leader (simple: highest priority or first available)."""
        # Could implement Raft-like algorithm for robustness
        for candidate in sorted(self.candidates):
            if await self._is_healthy(candidate):
                self.leader = candidate
                self.term += 1
                return candidate
        raise NoHealthyCandidateError()
```

**MCP Tools**:
```python
@mcp.tool()
async def create_barrier(
    name: str,
    agents: List[str],
    timeout: int = 60
) -> dict:
    """Create a barrier for agent synchronization."""

@mcp.tool()
async def barrier_ready(barrier_name: str, agent: str) -> dict:
    """Mark an agent as ready at a barrier."""

@mcp.tool()
async def wait_at_barrier(barrier_name: str) -> dict:
    """Wait for all agents at a barrier."""

@mcp.tool()
async def start_vote(
    question: str,
    options: List[str],
    voters: List[str]
) -> dict:
    """Start a voting round among agents."""

@mcp.tool()
async def cast_vote(vote_id: str, agent: str, option: str) -> dict:
    """Cast a vote in an active voting round."""
```

---

### 7. Secret Redaction

**Source**: EPIC_PROPOSAL Sub-Issue 7 (mentioned but not implemented)
**Priority**: Medium
**Effort**: 1-2 days

**Description**: Pattern-based sensitive data filtering in output.

**Proposed Implementation**:
```python
# core/redaction.py
import re
from typing import List, Pattern

DEFAULT_PATTERNS = [
    r'(?i)api[_-]?key["\s:=]+["\']?([a-zA-Z0-9_-]{20,})["\']?',
    r'(?i)secret["\s:=]+["\']?([a-zA-Z0-9_-]{20,})["\']?',
    r'(?i)password["\s:=]+["\']?([^\s"\']{8,})["\']?',
    r'(?i)token["\s:=]+["\']?([a-zA-Z0-9_.-]{20,})["\']?',
    r'(?i)bearer\s+([a-zA-Z0-9_.-]{20,})',
    r'(?i)aws_access_key_id["\s:=]+([A-Z0-9]{20})',
    r'(?i)aws_secret_access_key["\s:=]+([a-zA-Z0-9/+=]{40})',
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # Email
]

class SecretRedactor:
    def __init__(self, patterns: Optional[List[str]] = None):
        self.patterns: List[Pattern] = []
        for p in (patterns or DEFAULT_PATTERNS):
            self.patterns.append(re.compile(p))

    def redact(self, text: str, replacement: str = "[REDACTED]") -> str:
        result = text
        for pattern in self.patterns:
            result = pattern.sub(replacement, result)
        return result

    def add_pattern(self, pattern: str):
        self.patterns.append(re.compile(pattern))
```

**MCP Tools**:
```python
@mcp.tool()
async def configure_redaction(
    enabled: bool = True,
    additional_patterns: Optional[List[str]] = None,
    replacement: str = "[REDACTED]"
) -> dict:
    """Configure secret redaction settings."""

@mcp.tool()
async def redact_output(text: str) -> dict:
    """Manually redact sensitive data from text."""
```

---

### 8. Session Recording/Replay

**Source**: EPIC_PROPOSAL Sub-Issue 6 (mentioned but not implemented)
**Priority**: Low
**Effort**: 3-4 days

**Description**: Record terminal sessions for debugging and replay.

**Proposed Implementation**:
```python
# core/recording.py
from dataclasses import dataclass, field
from typing import List
import json

@dataclass
class SessionEvent:
    timestamp: datetime
    event_type: str  # "input", "output", "control"
    data: str
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class SessionRecording:
    session_id: str
    agent: Optional[str]
    start_time: datetime
    events: List[SessionEvent] = field(default_factory=list)

    def add_input(self, command: str):
        self.events.append(SessionEvent(
            timestamp=datetime.utcnow(),
            event_type="input",
            data=command
        ))

    def add_output(self, output: str):
        self.events.append(SessionEvent(
            timestamp=datetime.utcnow(),
            event_type="output",
            data=output
        ))

    def save(self, path: Path):
        with open(path, 'w') as f:
            json.dump(asdict(self), f, default=str)

    @classmethod
    def load(cls, path: Path) -> "SessionRecording":
        with open(path) as f:
            data = json.load(f)
        return cls(**data)

class RecordingManager:
    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self.active_recordings: Dict[str, SessionRecording] = {}

    def start_recording(self, session_id: str, agent: Optional[str] = None):
        recording = SessionRecording(
            session_id=session_id,
            agent=agent,
            start_time=datetime.utcnow()
        )
        self.active_recordings[session_id] = recording

    def stop_recording(self, session_id: str) -> Path:
        recording = self.active_recordings.pop(session_id)
        filename = f"{session_id}_{recording.start_time.isoformat()}.json"
        path = self.storage_dir / filename
        recording.save(path)
        return path
```

**MCP Tools**:
```python
@mcp.tool()
async def start_recording(
    session_id: Optional[str] = None,
    agent: Optional[str] = None
) -> dict:
    """Start recording a session."""

@mcp.tool()
async def stop_recording(session_id: str) -> dict:
    """Stop recording and save to file."""

@mcp.tool()
async def list_recordings(
    agent: Optional[str] = None,
    since: Optional[datetime] = None
) -> List[dict]:
    """List available recordings."""

@mcp.tool()
async def replay_recording(
    recording_id: str,
    target_session: Optional[str] = None,
    speed: float = 1.0
) -> dict:
    """Replay a recorded session."""
```

---

## Implementation Roadmap

### Phase 1: Core Workflow Enhancements (1-2 weeks)
- [ ] Issue: Termination Conditions
- [ ] Issue: Agent Handoff Protocol
- [ ] Issue: Secret Redaction

### Phase 2: Coordination & Tracking (1-2 weeks)
- [ ] Issue: Coordination Primitives (Barrier, Voting)
- [ ] Issue: Cost/Token Tracking
- [ ] Issue: Session Recording/Replay

### Phase 3: Advanced Features (2-3 weeks)
- [ ] Issue: Pipeline Architecture
- [ ] Issue: Intent Classification
- [ ] Issue: Leader Election

### Phase 4: Future Exploration
- [ ] No-Code GUI / Visual Workflow Builder
- [ ] Distributed Task Queue (Dramatiq integration)
- [ ] Multi-Language SDK

---

## Code Location Suggestions

| Feature | Suggested Location | Integration Points |
|---------|-------------------|-------------------|
| Termination Conditions | `core/termination.py` | `manager.py`, `flows.py` |
| Agent Handoff | `core/handoff.py` | `agents.py`, `fastmcp_server.py` |
| Pipeline | `core/pipeline.py` | `fastmcp_server.py` |
| Cost Tracking | `core/cost_tracking.py` | `session.py`, `fastmcp_server.py` |
| Intent Classification | `core/intent.py` | `roles.py`, `agents.py` |
| Coordination | `core/coordination.py` | `manager.py`, `fastmcp_server.py` |
| Secret Redaction | `core/redaction.py` | `session.py`, `logging.py` |
| Session Recording | `core/recording.py` | `session.py`, `fastmcp_server.py` |

---

## Conclusion

This addendum identifies 12 features from community research that would expand iterm-mcp's capabilities beyond the current epic scope. The highest-priority additions are:

1. **Termination Conditions** - Essential for resource management and preventing runaway sessions
2. **Agent Handoff Protocol** - Critical for complex multi-step workflows
3. **Secret Redaction** - Important security feature already acknowledged in the epic

These features align with patterns from industry-leading frameworks (LangGraph, AutoGen, CrewAI) and address community pain points identified in the research.
