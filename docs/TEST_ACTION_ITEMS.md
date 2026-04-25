# Test Improvement Action Items

**Based on:** claude-code-mcp Test Pattern Audit  
**Created:** December 5, 2025  
**Status:** Proposed

This document tracks actionable items from the test strategy audit.

---

## Phase 1: Foundation (Week 1) - HIGH PRIORITY

### 1. Create MCPTestClient for Python
- [ ] Create `tests/utils/mcp_test_client.py`
- [ ] Implement subprocess-based MCP client
- [ ] Add methods: `connect()`, `disconnect()`, `send_request()`, `call_tool()`, `list_tools()`
- [ ] Add timeout handling (30s default)
- [ ] Add error handling for failed tool calls
- [ ] Write unit tests for MCPTestClient itself
- [ ] Document usage with examples

**Effort:** 4-6 hours  
**Owner:** TBD  
**Issue:** #TBD

---

### 2. Reorganize Test Directory Structure
- [ ] Create `tests/unit/` directory
- [ ] Create `tests/integration/` directory
- [ ] Create `tests/edge_cases/` directory
- [ ] Create `tests/utils/` directory
- [ ] Create `tests/__init__.py` files
- [ ] Move `test_models.py` to `tests/unit/`
- [ ] Move `test_grpc_client.py` to `tests/unit/` (with mocks)
- [ ] Keep iTerm2-dependent tests in `tests/integration/`
- [ ] Update import paths in all test files
- [ ] Update CI to run unit and integration separately

**Effort:** 1 day  
**Owner:** TBD  
**Issue:** #TBD

---

### 3. Set Up pytest Configuration
- [ ] Add pytest to `pyproject.toml` dev dependencies
- [ ] Add pytest-asyncio for async test support
- [ ] Create `pytest.ini` with markers (unit, integration, edge, slow)
- [ ] Configure coverage settings
- [ ] Create separate pytest configs for unit vs integration
- [ ] Document how to run different test suites
- [ ] Update README with new test commands

**Effort:** 2-4 hours  
**Owner:** TBD  
**Issue:** #TBD

---

## Phase 2: Mocking (Week 2) - HIGH PRIORITY

### 4. Create iTerm2 Mock Infrastructure
- [ ] Create `tests/utils/iterm_mock.py`
- [ ] Implement `ITerm2ConnectionMock`
- [ ] Implement `ITerm2SessionMock` with core methods
- [ ] Implement `ITerm2AppMock` and `ITerm2WindowMock`
- [ ] Implement `ITerm2TabMock`
- [ ] Add output buffer simulation
- [ ] Add is_processing state simulation
- [ ] Write unit tests for mock classes
- [ ] Document mock usage with examples

**Effort:** 2-3 days  
**Owner:** TBD  
**Issue:** #TBD

---

### 5. Convert Tests to Use Mocks
- [ ] Convert `test_models.py` (already unit-friendly)
- [ ] Convert `test_layouts.py` logic tests
- [ ] Convert `test_agent_registry.py` with mock storage
- [ ] Convert `test_grpc_client.py` with mock stubs
- [ ] Create unit version of `test_basic_functionality.py`
- [ ] Verify all unit tests pass without iTerm2

**Effort:** 2-3 days  
**Owner:** TBD  
**Issue:** #TBD

---

## Phase 3: Edge Cases (Week 3) - MEDIUM PRIORITY

### 6. Add Input Validation Tests
- [ ] Create `tests/edge_cases/test_input_validation.py`
- [ ] Test empty session names
- [ ] Test null/None session IDs
- [ ] Test invalid max_lines values
- [ ] Test oversized input strings (>1MB)
- [ ] Test invalid agent names
- [ ] Test invalid team names
- [ ] Test missing required parameters
- [ ] Test invalid parameter types

**Effort:** 1 day  
**Owner:** TBD  
**Issue:** #TBD

---

### 7. Add Special Character Tests
- [ ] Create `tests/edge_cases/test_special_characters.py`
- [ ] Test Unicode in session names
- [ ] Test control characters in commands
- [ ] Test ANSI escape sequences
- [ ] Test newlines in commands
- [ ] Test quotes in commands
- [ ] Test shell metacharacters ($, &, |, etc.)
- [ ] Test path separators in names

**Effort:** 1 day  
**Owner:** TBD  
**Issue:** #TBD

---

### 8. Add Concurrency Tests
- [ ] Create `tests/edge_cases/test_concurrency.py`
- [ ] Test parallel session creation (10+ simultaneous)
- [ ] Test concurrent writes to different sessions
- [ ] Test concurrent reads from multiple sessions
- [ ] Test read-write race conditions on same session
- [ ] Test cascade message contention
- [ ] Test agent registry concurrent access
- [ ] Test team operations concurrent access

**Effort:** 1-2 days  
**Owner:** TBD  
**Issue:** #TBD

---

## Phase 4: Polish (Week 4) - LOW PRIORITY

### 9. Add Test Helper Utilities
- [ ] Create `tests/utils/helpers.py`
- [ ] Add `create_temp_dir()` and `cleanup_temp_dir()`
- [ ] Add `wait_for_condition()` async helper
- [ ] Add `wait_for_output()` helper
- [ ] Add `assert_session_exists()` custom assertion
- [ ] Add `assert_agent_registered()` custom assertion
- [ ] Add `build_session_config()` test data builder
- [ ] Add `build_agent_config()` test data builder
- [ ] Document helpers with examples

**Effort:** 1 day  
**Owner:** TBD  
**Issue:** #TBD

---

### 10. Update CI Configuration
- [ ] Create `.github/workflows/unit-tests.yml`
- [ ] Create `.github/workflows/integration-tests.yml`
- [ ] Configure unit tests to run on Linux (ubuntu-latest)
- [ ] Configure integration tests to run on macOS (macos-latest)
- [ ] Add Python version matrix (3.8, 3.9, 3.10, 3.11)
- [ ] Add coverage upload to Codecov
- [ ] Set up separate CI jobs for edge case tests
- [ ] Configure PR status checks
- [ ] Add badges to README

**Effort:** 4-6 hours  
**Owner:** TBD  
**Issue:** #TBD

---

### 11. Create Test Documentation
- [ ] Create `docs/TESTING_GUIDE.md`
- [ ] Document how to run tests
- [ ] Document test organization
- [ ] Document writing unit tests
- [ ] Document writing integration tests
- [ ] Document using test utilities
- [ ] Document mocking best practices
- [ ] Document debugging test failures
- [ ] Add contributing guidelines for tests

**Effort:** 4-6 hours  
**Owner:** TBD  
**Issue:** #TBD

---

## Optional Enhancements (Future)

### 12. Add Performance Tests
- [ ] Create `tests/performance/` directory
- [ ] Add throughput tests (1000+ messages)
- [ ] Add stress tests (100+ sessions)
- [ ] Add latency benchmarks
- [ ] Add memory usage tests
- [ ] Configure to run separately (marked as `slow`)

**Effort:** 3-4 days  
**Owner:** TBD  
**Issue:** #TBD

---

### 13. Add Regression Tests
- [ ] Create `tests/regression/` directory
- [ ] Add tests for known bugs
- [ ] Add tests for edge cases that caused issues
- [ ] Document each regression test with context

**Effort:** Ongoing  
**Owner:** TBD  
**Issue:** #TBD

---

## Success Criteria

After completing Phase 1-4, we should have:

- ✅ MCPTestClient for protocol testing
- ✅ Separated unit and integration tests
- ✅ iTerm2 mocks for unit testing
- ✅ Unit tests running in < 30 seconds
- ✅ Integration tests clearly separated
- ✅ Edge case test coverage
- ✅ Improved CI configuration
- ✅ Test documentation

**Metrics:**
- Unit test coverage: > 90%
- Integration test coverage: > 80%
- CI unit test time: < 30 seconds
- CI integration test time: < 5 minutes
- Test flakiness: < 1%

---

## Related Documents

- [TEST_AUDIT.md](./TEST_AUDIT.md) - Full audit report
- [TEST_STRATEGY_RECOMMENDATIONS.md](./TEST_STRATEGY_RECOMMENDATIONS.md) - Detailed recommendations
- [FOLLOWUP_ISSUES.md](archive/FOLLOWUP_ISSUES.md) - Epic tracking

---

## Notes

- All tasks should be tracked as GitHub issues
- Link issues to this checklist for tracking
- Update status as tasks are completed
- Estimated total effort: 3-4 weeks (1 engineer)

**Last Updated:** December 5, 2025
