# Improvement Roadmap

This document outlines the strategic roadmap for the iTerm MCP project, consolidating recommendations from recent audits and feature developments.

## 🌟 Current Status

The "Improve iTerm" epic has delivered:
- ✅ **gRPC Integration**: Full remote procedure call support.
- ✅ **Agent Orchestration**: Multi-pane, multi-agent capabilities.
- ✅ **Telemetry**: Basic metrics and dashboarding infrastructure.
- ✅ **Test Coverage**: 88% passing tests with established patterns.

## 🗺️ Roadmap Overview

### Phase 1: Test Infrastructure Hardening (Immediate)
*Focus: Reliability and Developer Experience*

- [ ] **Implement MCPTestClient**
    - Create a Python equivalent of the `MCPTestClient` from Claude Code.
    - Enables protocol-level testing without manual JSON-RPC.
- [ ] **Separate Unit & Integration Tests**
    - `tests/unit`: Run fast on Linux CI (no iTerm2 req).
    - `tests/integration`: Run on macOS CI (requires iTerm2).
- [ ] **Test Utility Module**
    - Add `tests/helpers.py` with `wait_for` and other async utilities.
    - reduces boilerplate in async tests.

### Phase 2: Resilience & Edge Cases (Short Term)
*Focus: Robustness*

- [ ] **Edge Case Suite**
    - Input validation, special characters, and concurrency tests.
- [ ] **Stress Testing**
    - Validate behavior with 20+ concurrent sessions.
    - Test message queue limits and throughput.
- [ ] **Mocking Infrastructure**
    - Create comprehensive iTerm2 mocks (`ITerm2ConnectionMock`) to allow more unit testing of core logic.

### Phase 3: Observability & Performance (Medium Term)
*Focus: Production Readiness*

- [ ] **Observability Dashboard**
    - Build Grafana dashboards for agent orchestration metrics.
    - Visualize cascade message throughput and error rates.
- [ ] **Performance Benchmarks**
    - Ongoing regression testing for latency and memory usage.

## 📚 Reference Documents

Detailed findings and specific implementation guides can be found in:

- **[Test Strategy Recommendations](docs/TEST_STRATEGY_RECOMMENDATIONS.md)**
    - Deep dive into test architecture, directory structure, and specific code examples.
- **[Follow-up Issues](FOLLOWUP_ISSUES.md)**
    - Specific GitHub issue templates and broken-down tasks.
- **[Epic Recommendation](EPIC_RECOMMENDATION.md)**
    - Closure report for the "Improve iTerm" epic.
