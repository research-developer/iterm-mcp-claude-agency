# Follow-up Enhancement Issues

These issues can be created if the Epic is closed, to track remaining enhancement work.

## Issue 1: Add Observability Dashboard for Agent Orchestration

**Title:** Add observability dashboard for agent orchestration

**Labels:** enhancement, observability

**Description:**
Create a real-time observability dashboard for monitoring multi-agent orchestration.

**Background:**
The iTerm MCP server has comprehensive logging infrastructure in place, but lacks visualization and metrics aggregation. This makes it difficult to monitor multi-agent workflows at scale.

**Proposed Solution:**
1. Add metrics collection using Prometheus client library
2. Create Grafana dashboard templates for:
   - Active agents and sessions
   - Message throughput (cascade messages, parallel writes)
   - Session lifecycle events
   - Command execution latency
   - Error rates by operation type

3. Add optional telemetry export:
   - OpenTelemetry support for distributed tracing
   - StatsD/Prometheus metrics endpoint
   - Structured JSON logging for log aggregation

**Benefits:**
- Real-time visibility into agent orchestration
- Performance monitoring and bottleneck detection
- Operational insights for debugging multi-agent workflows

**Scope:**
- Add `prometheus-client` to optional dependencies
- Create metrics collector in `utils/metrics.py`
- Add Grafana dashboard JSON to `docs/grafana/`
- Document setup in README
- Add integration tests for metrics

**Estimated Effort:** 2-3 days

---

## Issue 2: Audit Test Strategy Against claude-code-mcp and happy-cli ✅ COMPLETED

**Title:** Audit test strategy against claude-code-mcp and happy-cli patterns

**Labels:** testing, documentation

**Status:** ✅ COMPLETED (December 2025)

**Description:**
Perform formal comparison of our test strategy with best practices from claude-code-mcp and happy-cli projects.

**Background:**
The original epic called for adapting test strategies from these projects. We have 88 passing tests, but haven't formally compared our approach to these reference implementations.

**Completed Deliverables:**
- ✅ `docs/TEST_AUDIT.md` - Comprehensive audit of claude-code-mcp test patterns (31,887 characters)
- ✅ `docs/HAPPY_CLI_TEST_AUDIT.md` - Comprehensive audit of happy-cli test patterns
- ✅ `docs/TEST_STRATEGY_RECOMMENDATIONS.md` - Actionable recommendations with implementation roadmap (Consolidated)

**Key Findings:**
1. **MCPTestClient Pattern (Claude):** Highly applicable - enables protocol-level MCP testing
2. **Real Integration Testing (Happy-CLI):** Happy-CLI uses actual HTTP APIs and file systems instead of extensive mocking
3. **Stress & Resilience (Happy-CLI):** Tests with 20+ concurrent operations and crash recovery
4. **Test Organization (Claude):** claude-code-mcp has superior separation of unit/e2e tests
5. **Mocking Infrastructure:** Sophisticated CLI mocking adaptable to iTerm2 API

**Recommendations Implemented:**
- Consolidated implementation roadmap (Phase 1-4)
- Code examples for test utilities, stress tests, resilience tests, and mocks
- Implementation priority matrix (Quick Wins → Long Term)

**Estimated Effort:** 2 days + 4 hours ✅ (Completed)

---

## Issue 3: Add macOS CI Runner for Integration Tests

**Title:** Add macOS CI runner for integration tests

**Labels:** ci, testing

**Description:**
Enable integration tests that require iTerm2 to run in CI on macOS runners.

**Background:**
Currently, 16 integration tests are skipped in CI because they require macOS/iTerm2. These tests cover:
- Basic functionality (create window, create layout, send/receive text)
- Advanced features (screen monitoring, output filtering, multiple sessions)
- Line limits (overflow handling, custom limits)
- Logging (command tracking, output capture)
- Persistent sessions (reconnection, ID lookup)

**Proposed Solution:**
1. Add macOS runner to `.github/workflows/ci.yml`:
   ```yaml
   jobs:
     test-macos:
       runs-on: macos-latest
       steps:
         - uses: actions/checkout@v3
         - name: Install iTerm2
           run: brew install --cask iterm2
         - name: Run integration tests
           run: pytest tests/ --cov=. --cov-report=xml
   ```

2. Split tests into unit and integration:
   - `tests/unit/` - No external dependencies (run on Linux)
   - `tests/integration/` - Require iTerm2 (run on macOS)

3. Update CI to run both:
   - Linux: Fast unit tests only
   - macOS: Full integration suite

**Benefits:**
- Full test coverage in CI
- Catch iTerm2-specific regressions
- Validate cross-platform compatibility

**Considerations:**
- macOS runners cost more minutes
- May slow down CI (can run in parallel)
- Need to handle iTerm2 automation permissions

**Estimated Effort:** 1-2 days

---

## Issue 4: Create Stress and Benchmark Test Suite

**Title:** Create stress and benchmark test suite

**Labels:** testing, performance

**Description:**
Add performance testing to validate behavior under load and measure throughput.

**Background:**
Current tests validate functionality but don't measure performance. For multi-agent orchestration at scale, we need to know:
- Maximum concurrent sessions
- Message throughput limits
- Memory usage under load
- Latency distributions

**Proposed Solution:**

1. **Stress Tests** (`tests/stress/`):
   - Create 100+ sessions simultaneously
   - Send 1000+ parallel messages
   - Maintain long-running sessions (hours)
   - Test memory leaks with repeated operations

2. **Benchmark Tests** (`tests/benchmarks/`):
   - Measure session creation latency
   - Measure command execution latency
   - Measure cascade message routing performance
   - Compare gRPC vs. MCP overhead

3. **Tools**:
   - Add `pytest-benchmark` for microbenchmarks
   - Add `locust` for load testing
   - Add `memory_profiler` for memory analysis

4. **Documentation**:
   - `docs/PERFORMANCE.md` - Performance characteristics
   - Benchmark results in README
   - CI job for regression detection

**Deliverables:**
- Stress test suite with 10+ scenarios
- Benchmark suite with 20+ metrics
- Performance documentation
- CI job that fails on regressions

**Estimated Effort:** 3-4 days

---

## Issue 5: Fix Async Event Loop Issues in Integration Tests

**Title:** Fix async event loop issues in integration tests

**Labels:** bug, testing

**Description:**
Fix "no current event loop" errors in 10 integration tests.

**Affected Tests:**
- `test_line_limits.py` (6 tests)
- `test_logging.py` (6 tests)  
- `test_persistent_session.py` (5 tests)
- `test_command_output_tracking.py` (10 tests - also has uuid import issue)

**Error:**
```
RuntimeError: There is no current event loop in thread 'MainThread'.
```

**Root Cause:**
Tests use `asyncio.get_event_loop()` which is deprecated in Python 3.10+. Need to use `asyncio.new_event_loop()` or `asyncio.run()`.

**Solution:**
1. Update test fixtures to use `pytest-asyncio`:
   ```python
   @pytest.fixture
   async def terminal():
       connection = await iterm2.Connection.async_create()
       terminal = ItermTerminal(connection)
       await terminal.initialize()
       yield terminal
   ```

2. Mark async tests:
   ```python
   @pytest.mark.asyncio
   async def test_example():
       ...
   ```

3. Fix uuid import in `test_command_output_tracking.py`

**Estimated Effort:** 2-4 hours

---

## Priority Recommendation

If resources are limited, prioritize:

1. **Issue 5** (Fix async tests) - Quick win, enables CI testing
2. **Issue 3** (macOS CI) - Enables full test coverage in CI
3. **Issue 2** (Test audit) - Improves quality, low effort
4. **Issue 1** (Dashboard) - Nice-to-have, higher effort
5. **Issue 4** (Stress tests) - Nice-to-have, highest effort
