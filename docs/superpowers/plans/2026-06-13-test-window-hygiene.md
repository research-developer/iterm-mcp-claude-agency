# Plan: Test-Window Hygiene (2026-06-13)

## Goal

During testing, every iTerm2 window/session opened by the test suite is
tagged and closed on teardown — closing ONLY our own sessions, never
windows the user (or a concurrent test run) opened.

---

## Tag Scheme

Each test run mints a unique tag string once:

```
MCP-TEST·<pid>-<uuid8>
```

Example: `MCP-TEST·12345-a1b2c3d4`

- **pid**: `os.getpid()` — differentiates concurrent processes
- **uuid8**: first 8 hex chars of `uuid.uuid4()` — prevents pid-reuse collisions
- **Prefix `MCP-TEST·`**: stable prefix used for orphan sweeps (never
  used by production profiles which start with `MCP Agent` or `MCP Team:`)

The tag is stored as the iTerm2 user variable `user.mcp_test_run` on
every session the test suite opens. This is a belt-and-suspenders
approach alongside checking the session's profile name prefix.

---

## Profile Strategy

**Approach chosen: user variable tagging (option b from brief)**

We tag via `session.async_set_variable("user.mcp_test_run", tag)` rather
than writing a new dynamic profile, because:

1. Writing a dynamic profile requires file I/O + iTerm2 to reload its
   profile list before the profile name is available — introducing race
   conditions and timing issues in tests.
2. User variables are set per-session immediately and are readable back
   synchronously.
3. Profile name prefix `MCP-TEST·` is still available for orphan sweeps
   because we always check both fields; the profile name stays `MCP Agent`
   (or whatever the test's `create_window` call uses) but the user var
   confirms ownership.

---

## Module: `core/test_window_tracker.py`

### Public API

```python
TAG_PREFIX = "MCP-TEST·"

def make_run_tag() -> str:
    """Return a unique per-run tag like 'MCP-TEST·12345-a1b2c3d4'."""

async def mark_session(session: iterm2.Session, tag: str) -> None:
    """Set user.mcp_test_run = tag on the given session."""

async def close_tagged_sessions(
    connection: iterm2.Connection,
    tag: str,
    *,
    prefix_sweep: bool = False,
) -> int:
    """Enumerate all sessions; close those that belong to this run.

    Matching rules (session must satisfy at least one):
      - user.mcp_test_run variable equals tag exactly
      - If prefix_sweep=True: profile name starts with TAG_PREFIX

    Safety: Any session whose variable read fails is SKIPPED (not fatal).
    Sessions with a non-matching or absent marker are never closed.

    Returns:
        Count of sessions closed.
    """
```

### Algorithm for `close_tagged_sessions`

```
app = await iterm2.async_get_app(connection)
closed = 0
for window in app.windows:
  for tab in window.tabs:
    for session in tab.sessions:
      should_close = False
      try:
        var = await session.async_get_variable("user.mcp_test_run")
        if var == tag:
          should_close = True
      except Exception:
        pass  # skip unreadable sessions
      if not should_close and prefix_sweep:
        try:
          prof = await session.async_get_profile()
          if prof.name.startswith(TAG_PREFIX):
            should_close = True
        except Exception:
          pass
      if should_close:
        try:
          await session.async_close(force=True)
          closed += 1
        except Exception:
          pass  # already closed is fine
return closed
```

---

## Base Class: `tests/live_iterm_base.py`

`LiveItermTestCase(unittest.IsolatedAsyncioTestCase)`:

- `asyncSetUp`: creates iTerm2 connection, initialises `ItermTerminal`,
  generates `self._tag`, optionally does a prefix_sweep orphan cleanup.
- `create_tagged_window()`: calls `terminal.create_window()` then
  `mark_session(raw_session, self._tag)`. Returns `ItermSession`.
- `asyncTearDown`: calls `close_tagged_sessions(conn, self._tag)`.

---

## Live Modules — Conversion Plan

All 7 live-iTerm2 integration modules use the same pattern:
- `async_setup` creates a connection + window
- `async_teardown` closes the window
- `run_async_test` drives the event loop

**Target: subclass `LiveItermTestCase`, route window creation through
`self.create_tagged_window()`, inherit teardown.**

| Module | Conversion approach |
|--------|---------------------|
| `test_basic_functionality.py` | Full — simple async_setup pattern |
| `test_advanced_features.py` | Full — simple async_setup pattern |
| `test_persistent_session.py` | Full — simple async_setup pattern |
| `test_logging.py` | Full — simple async_setup pattern |
| `test_line_limits.py` | Full — simple async_setup pattern |
| `test_expect.py` | Full — simple async_setup pattern |
| `test_session_suspend.py` | Partial — `TestSuspendResumeIntegration` gets full conversion; `TestSuspendStateManagement` and `TestSuspendResumeAsync` are pure unit tests (no iTerm2) — leave as-is |

---

## Unit Tests: `tests/test_window_tracker.py`

Tests `close_tagged_sessions` logic with mock objects (no live iTerm2):

1. **test_closes_matching_tag** — session with correct tag gets closed
2. **test_skips_different_tag** — session with wrong tag is NOT closed
3. **test_skips_no_tag** — session with empty/None variable is NOT closed
4. **test_skips_on_var_read_error** — session that raises on `async_get_variable` is skipped
5. **test_prefix_sweep_closes_orphan_profile** — with `prefix_sweep=True`, session whose profile name starts with `MCP-TEST·` is closed
6. **test_prefix_sweep_skips_normal_profile** — with `prefix_sweep=True`, session with `MCP Agent` profile is NOT closed
7. **test_returns_correct_count** — count reflects only actually-closed sessions
8. **test_mark_session_sets_variable** — `mark_session` calls `async_set_variable` with correct args
9. **test_make_run_tag_format** — tag matches `MCP-TEST·<digits>-<hex8>` pattern
10. **test_make_run_tag_unique** — two calls produce different tags

---

## Commit Plan

1. `docs/superpowers/plans/2026-06-13-test-window-hygiene.md` — this file
2. `core/test_window_tracker.py` + `tests/test_window_tracker.py` (TDD)
3. `tests/live_iterm_base.py` — base class
4. Convert 7 live modules (surgical edits only)
