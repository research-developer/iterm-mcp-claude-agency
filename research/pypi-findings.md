# PyPI Agent Orchestration Research Findings

## Research Date: January 2, 2026

This document summarizes research on PyPI packages relevant to agent orchestration, terminal integration, session management, and message passing for the iterm-mcp project.

---

## 1. Multi-Agent Orchestration Frameworks

### AG2 (formerly AutoGen)
**PyPI:** https://pypi.org/project/ag2/
**Version:** 0.10.3 (Dec 2025)
**License:** Apache 2.0
**Status:** Production/Stable, Very Active

**Key Features:**
- Open-source AgentOS evolved from Microsoft's AutoGen
- ConversableAgent as the core building block for message exchange
- Multiple orchestration patterns: swarms, group chats, nested chats, sequential chats
- Human-in-the-loop support via UserProxyAgent
- Tool registration with Pydantic validation
- Code execution capabilities
- MCP integration via `ag2[mcp]` extra
- Extensive LLM support (OpenAI, Anthropic, Bedrock, Gemini, etc.)

**Relevance to iterm-mcp:**
- The orchestration patterns (group chats, sequential flows) could inform multi-agent terminal session design
- Tool registration pattern is similar to MCP tool patterns
- ConversableAgent's message handling architecture is a good reference

**Integration Patterns:**
```python
from autogen import ConversableAgent, UserProxyAgent
# Define agents with specific roles
# Use communication flows for orchestration
```

---

### Agency Swarm
**PyPI:** https://pypi.org/project/agency-swarm/
**Version:** 1.6.0 (Dec 2025)
**License:** MIT
**Status:** Active, 92% test coverage

**Key Features:**
- Built on OpenAI Agents SDK
- Organizational structure metaphor (CEO, Developer, VA, etc.)
- Explicit directional communication flows (`ceo > dev` syntax)
- Type-safe tools using Pydantic
- State persistence via callbacks (`load_threads_callback`, `save_threads_callback`)
- Terminal demo mode: `agency.terminal_demo()`
- Copilot web UI demo: `agency.copilot_demo()`
- Full control over agent prompts/instructions

**Relevance to iterm-mcp:**
- **Terminal demo capability** is directly relevant
- Communication flow patterns could map to terminal session routing
- State persistence callbacks pattern for session history

**Integration Patterns:**
```python
from agency_swarm import Agency, Agent
agency = Agency(
    ceo,
    communication_flows=[ceo > dev, ceo > va, dev > va],
)
agency.terminal_demo()  # CLI interface
```

---

### CrewAI
**PyPI:** https://pypi.org/project/crewai/
**License:** MIT
**Status:** Production, Very Popular

**Key Features:**
- Lightweight, standalone Python framework
- Role-based agent design with goals and backstories
- Sequential and hierarchical task orchestration
- Built-in memory and context management
- Process delegation between agents
- Tool integration via decorators

**Relevance to iterm-mcp:**
- Role-based agent design could inform session specialization
- Task orchestration patterns for multi-session workflows

---

### Multi-Agent Orchestrator (agent-squad)
**PyPI:** https://pypi.org/project/multi-agent-orchestrator/ (DEPRECATED)
**New Package:** https://pypi.org/project/agent-squad/
**Maintainer:** AWS Labs
**License:** Apache 2.0

**Key Features:**
- Intelligent intent classification for routing
- Streaming and non-streaming agent responses
- Context management across multiple agents
- Universal deployment (Lambda, local, any cloud)
- Pre-built agents and classifiers
- TypeScript support

**Relevance to iterm-mcp:**
- Intent classification could route commands to appropriate terminal sessions
- Context management patterns for conversation continuity

---

### Swarms
**PyPI:** https://pypi.org/project/swarms/
**Status:** Enterprise-grade, Production

**Key Features:**
- Enterprise multi-agent infrastructure platform
- Production-scale deployment focus
- Multiple swarm architectures
- Extensive monitoring and logging

**Relevance to iterm-mcp:**
- Production patterns for scaling multi-agent systems
- Monitoring approaches for agent health

---

### LangGraph Swarm
**PyPI:** https://pypi.org/project/langgraph-swarm/
**Status:** Active

**Key Features:**
- Multi-agent architecture on LangGraph
- Graph-based agent orchestration
- State management built on LangGraph primitives

---

## 2. Terminal/CLI Integration Packages

### Pexpect
**PyPI:** https://pypi.org/project/pexpect/
**Version:** 4.9.0 (Nov 2023)
**License:** ISC
**Status:** Production/Stable, Mature

**Key Features:**
- Pure Python module for spawning child applications
- Pattern-based output matching (expect patterns)
- SSH automation via pxssh extension
- Interactive application control (ssh, ftp, passwd, telnet)
- Requires Unix-like systems for full functionality (pty module)

**Relevance to iterm-mcp:**
- **Core relevance** - Similar goal of controlling terminal sessions
- Pattern matching for command output detection
- Could be used as fallback for non-iTerm environments
- Expect-style programming model is proven

**Integration Patterns:**
```python
import pexpect
child = pexpect.spawn('ssh user@host')
child.expect('password:')
child.sendline('secret')
child.expect('$')
child.sendline('ls -la')
```

---

### subprocess-monitor
**PyPI:** https://pypi.org/project/subprocess-monitor/

**Key Features:**
- Subprocess management with lifecycle features
- Python API and CLI interface
- Advanced lifecycle management

**Relevance to iterm-mcp:**
- Process lifecycle patterns for session management

---

### agent-cli
**PyPI:** https://pypi.org/project/agent-cli/

**Key Features:**
- Local-first AI-powered command-line agents
- Voice and text interaction
- Runs entirely on local machine

---

### cli-automation
**PyPI:** https://pypi.org/project/cli-automation/

**Key Features:**
- Async Typer-based CLI automation
- Infrastructure automation from command line

---

## 3. Message Passing & Communication

### Dramatiq
**PyPI:** https://pypi.org/project/dramatiq/
**Version:** 2.0.0
**Status:** Production

**Key Features:**
- Fast, reliable distributed task processing
- Focus on simplicity and reliability
- Concurrent worker processes
- Message broker support (RabbitMQ, Redis)
- Callback support for success/failure handling

**Relevance to iterm-mcp:**
- Task queue patterns for command distribution
- Worker process model for parallel execution
- Callback patterns for completion handling

**Integration Patterns:**
```python
import dramatiq

@dramatiq.actor
def execute_command(session_id, command):
    # Execute command in session
    pass
```

---

### async-dramatiq
**PyPI:** https://pypi.org/project/async-dramatiq/

**Key Features:**
- Dramatiq with native asyncio support
- Better for async terminal operations

---

### Celery
**Alternative:** Popular task queue, but heavier than Dramatiq

---

## 4. Model Context Protocol (MCP)

### mcp (Official SDK)
**PyPI:** https://pypi.org/project/mcp/
**Version:** 1.7.1+
**Maintainer:** Anthropic
**License:** MIT

**Key Features:**
- Official Python SDK for Model Context Protocol
- Build servers exposing data/functionality to LLMs
- Secure, standardized integration approach
- FastMCP decorator-based syntax

**Relevance to iterm-mcp:**
- **iterm-mcp already uses FastMCP** for its implementation
- Standard patterns for tool definitions
- Resource URI patterns for terminal state

---

### mcp-agent
**PyPI:** https://pypi.org/project/mcp-agent/
**Version:** 0.0.9+

**Key Features:**
- Streamlined agent building using MCP capabilities
- Higher-level abstraction over MCP servers
- Agent patterns for MCP integration

**Relevance to iterm-mcp:**
- Could inform agent-level abstractions on top of iterm-mcp
- Patterns for combining multiple MCP servers

---

## 5. Supporting Libraries

### Langroid
**PyPI:** https://pypi.org/project/langroid/
**Maintainer:** CMU & UW-Madison researchers

**Key Features:**
- Intuitive, lightweight LLM application framework
- Multi-agent orchestration
- Task delegation patterns

---

### Semantic Kernel
**PyPI:** https://pypi.org/project/semantic-kernel/
**Maintainer:** Microsoft

**Key Features:**
- Flexible agent framework
- Multi-agent system support
- Azure integration

---

## 6. Key Patterns for iterm-mcp Adoption

### Communication Flow Patterns
From **Agency Swarm**:
```python
# Directional flows define who can talk to whom
communication_flows = [
    orchestrator > terminal_agent,
    terminal_agent > file_agent,
]
```

**Application:** Define which agents can send commands to which terminal sessions.

### Session State Management
From **AG2** and **Agency Swarm**:
```python
# Callback-based persistence
agency = Agency(
    agents,
    load_threads_callback=load_from_db,
    save_threads_callback=save_to_db,
)
```

**Application:** Persist terminal session history and context across reconnections.

### Tool Registration Pattern
From **AG2**:
```python
from autogen import register_function

register_function(
    get_weekday,
    caller=date_agent,
    executor=executor_agent,
    description="Get the day of the week",
)
```

**Application:** Register terminal operations as callable tools for agents.

### Expect-Style Pattern Matching
From **Pexpect**:
```python
child.expect(['password:', 'Permission denied', pexpect.TIMEOUT])
if child.match_index == 0:
    child.sendline(password)
```

**Application:** Use pattern matching for detecting command completion, prompts, errors.

### Human-in-the-Loop
From **AG2**:
```python
human_validator = UserProxyAgent(
    name="human_validator",
    human_input_mode="ALWAYS",  # or "NEVER", "TERMINATE"
)
```

**Application:** Allow human intervention in terminal session workflows.

---

## 7. Recommendations for iterm-mcp

### High Priority Integrations

1. **Adopt AG2 orchestration patterns** for multi-terminal coordination
   - Group chat patterns for parallel terminal sessions
   - Sequential patterns for dependent command execution

2. **Implement expect-style monitoring** (inspired by pexpect)
   - Pattern-based output detection
   - Timeout handling
   - Success/failure detection

3. **Add state persistence callbacks** (Agency Swarm pattern)
   - Session history persistence
   - Context restoration on reconnection

### Medium Priority

4. **Consider Dramatiq for distributed execution**
   - Task queue for command distribution
   - Worker pool for parallel processing

5. **Enhance MCP tool definitions** with AG2-style registration
   - Caller/executor separation
   - Better type validation

### Lower Priority

6. **Explore intent classification** (multi-agent-orchestrator pattern)
   - Route commands to appropriate sessions
   - Context-aware session selection

---

## 8. Package Comparison Summary

| Package | Focus | Terminal Support | Active | License |
|---------|-------|------------------|--------|---------|
| AG2 | Multi-agent orchestration | Via tools | Yes | Apache 2.0 |
| Agency Swarm | OpenAI SDK extension | terminal_demo() | Yes | MIT |
| CrewAI | Role-based agents | Via tools | Yes | MIT |
| Pexpect | Terminal control | Native | Mature | ISC |
| MCP SDK | LLM integration protocol | Via tools | Yes | MIT |
| Dramatiq | Task distribution | No | Yes | LGPL |

---

## 9. Next Steps

1. Review AG2's group chat implementation for multi-session patterns
2. Evaluate Agency Swarm's terminal_demo() for CLI patterns
3. Consider pexpect patterns for output monitoring enhancement
4. Explore callback-based state persistence for session recovery
5. Investigate Dramatiq for distributed command execution scenarios
