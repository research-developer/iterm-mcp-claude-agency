# Epic Proposal Quick Reference

## Overview
Transform iTerm2 MCP into a visual multi-agent orchestration platform leveraging advanced iTerm2 Python API features.

## Sub-Issues at a Glance

### 🎨 Sub-Issue 1: Visual Hierarchy & Dynamic Layouts
**Goal:** Make the terminal a living organizational chart  
**Duration:** 2 weeks  
**Priority:** High (Phase 1)

**What it delivers:**
- [ ] Hierarchical layout engine mapping teams → pane structure
- [ ] Color-coded backgrounds per team (blue, green, orange, purple)
- [ ] Automatic layout reorganization when team membership changes
- [ ] Named arrangement templates (save/restore)
- [ ] Visual team boundaries using unicode borders

**Key iTerm2 APIs:**
- `session.async_split_pane()` - Create nested hierarchy
- `LocalWriteOnlyProfile.set_background_color()` - Team colors
- `LocalWriteOnlyProfile.set_tab_color()` - Tab identification
- `iterm2.Arrangement.async_save/restore()` - Layout persistence
- `session.async_set_variable("user.team")` - Metadata

**Success Metric:** Layout reflects team structure within 2 seconds of changes

---

### 💡 Sub-Issue 2: Real-Time Visual Agent Status
**Goal:** Agent states visible at a glance  
**Duration:** 2 weeks  
**Priority:** High (Phase 1)

**What it delivers:**
- [ ] Color-coded agent states (idle/thinking/working/error/success)
- [ ] Dynamic badges showing current task and queue depth
- [ ] Custom status bar with team-wide metrics
- [ ] Attention mechanisms (notifications, dock bounce, fireworks)
- [ ] Pattern-based state detection from output

**Key iTerm2 APIs:**
- `LocalWriteOnlyProfile.set_background_color()` - State colors
- `LocalWriteOnlyProfile.set_badge_color/text()` - Task display
- `iterm2.StatusBarComponent` + `@StatusBarRPC` - Metrics
- `session.get_screen_streamer()` - Output monitoring
- `session.async_inject()` - Notifications

**Success Metric:** State changes visible within 500ms

---

### 🔄 Sub-Issue 3: Structured Inter-Agent Communication
**Goal:** Sophisticated coordination between agents  
**Duration:** 2 weeks  
**Priority:** Medium (Phase 2)

**What it delivers:**
- [ ] Pub/sub event bus using custom control sequences
- [ ] Request/response pattern with correlation IDs
- [ ] Coordination primitives: barriers, voting, locks, leader election
- [ ] Event monitors for lifecycle, focus, prompts, variables
- [ ] User variables for shared state

**Key iTerm2 APIs:**
- `iterm2.CustomControlSequenceMonitor` - Custom protocol
- `iterm2.VariableMonitor` - State change reactions
- `iterm2.PromptMonitor` - Command completion detection
- `iterm2.NewSessionMonitor` - Auto-registration
- `iterm2.FocusMonitor` - Human attention tracking

**Success Metric:** Event delivery latency < 100ms, 99.9% reliability

---

### 🏥 Sub-Issue 4: Health Monitoring & Auto-Recovery
**Goal:** Self-healing multi-agent system  
**Duration:** 1 week  
**Priority:** Medium (Phase 3)

**What it delivers:**
- [ ] Heartbeat monitoring with timeout detection
- [ ] 3-level recovery strategy (wake → restart → new session)
- [ ] Circuit breaker pattern to prevent cascading failures
- [ ] Hang detection (no output for N seconds)
- [ ] Crash detection (session termination)
- [ ] Dedicated health dashboard pane

**Key iTerm2 APIs:**
- `iterm2.SessionTerminationMonitor` - Crash detection
- `session.get_screen_streamer()` - Activity monitoring
- `iterm2.PromptMonitor` - Hang detection
- `session.async_send_text()` - Recovery commands

**Success Metric:** 80% automatic recovery, < 1 min MTTR

---

### 👔 Sub-Issue 5: Executive Agent Interface
**Goal:** Natural human-agent collaboration  
**Duration:** 2 weeks  
**Priority:** High (Phase 2)

**What it delivers:**
- [ ] Designated executive agent in prominent pane
- [ ] Dual interaction modes (executive vs. direct)
- [ ] Focus-based automatic message routing
- [ ] Broadcast domains for team-wide commands
- [ ] Delegation with request/approval workflow
- [ ] Mode switching via keyboard shortcuts

**Key iTerm2 APIs:**
- `iterm2.FocusMonitor` - Track human attention
- `iterm2.broadcast.BroadcastDomain` - Team broadcasts
- `session.async_send_text(suppress_broadcast=True)` - Private messages
- `LocalWriteOnlyProfile` - Distinct executive colors

**Success Metric:** < 2s delegation, 95% correct routing

---

### 🔍 Sub-Issue 6: Advanced Observability
**Goal:** Production-grade debugging infrastructure  
**Duration:** 1 week  
**Priority:** Low (Phase 3)

**What it delivers:**
- [ ] Distributed tracing with parent/child spans
- [ ] Structured JSON logging across all agents
- [ ] Complete audit trail with immutable log chain
- [ ] Replay capability for debugging sessions
- [ ] Real-time debug visualization pane
- [ ] Performance metrics (latency, throughput, queue depths)

**Key iTerm2 APIs:**
- `session.async_get_contents()` - Full history capture
- `iterm2.Transaction` - Atomic snapshots
- Custom status bar components for metrics
- Triggers for error pattern detection

**Success Metric:** 50% reduction in debug time

---

### 🔒 Sub-Issue 7: Security & Isolation
**Goal:** Defense-in-depth for multi-agent environments  
**Duration:** 1 week  
**Priority:** Medium (Phase 4)

**What it delivers:**
- [ ] Role-based access control (exec/coordinator/worker)
- [ ] Buried sessions for invisible background workers
- [ ] Cryptographic audit logging with tamper detection
- [ ] Secrets redaction in logs and terminal output
- [ ] Rate limiting to prevent agent DoS
- [ ] Command sanitization and validation

**Key iTerm2 APIs:**
- `session.async_set_buried(True)` - Hide workers
- `app.buried_sessions` - Access hidden sessions
- Base64 encoding for safe execution (already exists)

**Success Metric:** Zero unauthorized operations, 100% audit coverage

---

## Implementation Timeline

```
Week 1-2  | Phase 1: Visual Foundation
          | ├─ Sub-Issue 1: Visual Hierarchy ✓
          | └─ Sub-Issue 2: Visual Status ✓
          |
Week 3-4  | Phase 2: Communication & Coordination
          | ├─ Sub-Issue 3: Inter-Agent Comms ✓
          | └─ Sub-Issue 5: Executive Interface ✓
          |
Week 5-6  | Phase 3: Reliability & Operations
          | ├─ Sub-Issue 4: Health Monitoring ✓
          | └─ Sub-Issue 6: Observability ✓
          |
Week 7    | Phase 4: Hardening
          | ├─ Sub-Issue 7: Security ✓
          | └─ Integration Testing & Optimization ✓
```

## Success Criteria

### Functional
- ✅ Visual hierarchy automatically reflects team structure
- ✅ Agent states visible through colors/badges
- ✅ Inter-agent communication latency < 100ms
- ✅ 80% automatic failure recovery
- ✅ Executive agent delegation working
- ✅ Complete audit trail

### Performance
- ✅ Support 50+ concurrent agents
- ✅ 99.9% uptime for coordination services
- ✅ < 1 min mean time to recovery

### Usability
- ✅ < 5 second learning curve
- ✅ 50% reduction in debug time
- ✅ Seamless mode switching (executive ↔ direct)

## iTerm2 APIs Used

### Currently Using (~20%)
- ✅ `session.async_send_text()` - Send commands
- ✅ `session.async_get_screen_contents()` - Read output
- ✅ `session.async_split_pane()` - Create panes
- ✅ `iterm2.Connection.async_create()` - Connect

### Will Add (~80% more)
- 🆕 **Colors**: `set_background_color()`, `set_tab_color()`, `set_cursor_color()`
- 🆕 **Badges**: `set_badge_text()`, `set_badge_color()`
- 🆕 **Status Bar**: `StatusBarComponent`, `@StatusBarRPC`
- 🆕 **Monitors**: 8 new types (Focus, Prompt, Variable, Custom, etc.)
- 🆕 **Layouts**: `Arrangement.async_save/restore()`, `Tab.async_move_to_window()`
- 🆕 **Variables**: `async_set_variable()`, `async_get_variable()`
- 🆕 **Broadcast**: `BroadcastDomain`, `suppress_broadcast`
- 🆕 **Buried Sessions**: `async_set_buried()`, `buried_sessions`
- 🆕 **Notifications**: `async_inject()` with escape codes
- 🆕 **Transactions**: `Transaction` for atomic operations

## Key Innovations

1. **Terminal as UI Framework**
   - Colors = state machine
   - Badges = task queues
   - Layouts = org chart
   - Variables = shared state

2. **Self-Organizing Hierarchy**
   - Layout updates automatically
   - No manual pane management
   - Visual org chart in real-time

3. **Visual Event System**
   - Color pulses on events
   - Badge updates on tasks
   - Status bar for metrics
   - Everything visible

4. **Proactive vs. Reactive**
   - Health monitoring prevents issues
   - Auto-recovery before human notices
   - Circuit breakers stop cascades

## Dependencies

### Prerequisites
- iTerm2 3.5+ (latest API features)
- Python 3.10+ (modern async)
- macOS (iTerm2 requirement)

### Internal
- ✅ Current gRPC infrastructure (keep)
- ✅ Agent registry (enhance)
- ✅ Session management (extend)

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| iTerm2 API limitations | Prototype each feature first |
| Performance at scale | Early load testing, optimization budget |
| Complexity creep | Strict scope, MVP per sub-issue |
| Backward compatibility | Feature flags, gradual rollout |

## Deliverables per Sub-Issue

Each sub-issue provides:
- ✅ Implementation code
- ✅ Unit tests (90% coverage)
- ✅ Integration tests
- ✅ API documentation
- ✅ Example usage
- ✅ Migration guide

## Acceptance Criteria Template

For each sub-issue:
1. Core features implemented and tested
2. iTerm2 APIs correctly integrated
3. Success metrics achieved
4. Documentation complete
5. No regressions in existing functionality
6. Code review approved
7. Demo to stakeholders successful

## Next Actions

- [ ] Review proposal with stakeholders
- [ ] Prioritize sub-issues by business value
- [ ] Create detailed GitHub issues (1 per sub-issue)
- [ ] Set up project board with 4 phases
- [ ] Assign Phase 1 sub-issues to sprint
- [ ] Kickoff meeting with development team

## Questions to Resolve

1. **Color Scheme**: User-configurable or fixed presets?
   - *Recommendation*: 3 presets, allow custom

2. **Agent Capacity**: Optimize for 50 or 100+ agents?
   - *Recommendation*: Target 50, test to 100

3. **Executive Privilege**: Should exec bypass security?
   - *Recommendation*: Yes, but audit everything

4. **Multi-Window**: Support or single window only?
   - *Recommendation*: Single window initially, multi later

## Resources

- **Full Proposal**: `EPIC_PROPOSAL_MULTI_AGENT_ORCHESTRATION.md`
- **Summary**: `EPIC_PROPOSAL_SUMMARY.md`
- **This Document**: `EPIC_PROPOSAL_QUICK_REFERENCE.md`
- **iTerm2 API Docs**: https://iterm2.com/python-api/

---

**Total Effort**: 7 weeks  
**Total Sub-Issues**: 7  
**Total Value**: Transform terminal into AI team mission control 🚀
