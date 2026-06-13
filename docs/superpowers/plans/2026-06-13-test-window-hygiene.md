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

**Approach: stable visible profile (primary for human ID) + user variable (primary for programmatic teardown)**

Two complementary mechanisms work together:

### 1. Stable `MCP-TEST` Dynamic Profile (visual identification)

`core/test_window_tracker.ensure_test_profile()` writes a single-file Dynamic
Profile (`~/Library/Application Support/iTerm2/DynamicProfiles/iterm-mcp-test-profile.json`)
with a stable GUID.  The profile has:
- An amber/orange tab colour (visually distinct from `MCP Agent` and `MCP Team:` profiles)
- Badge text `"MCP-TEST"` (readable even when tabs are narrow)

`ensure_test_profile()` is called once from `LiveItermTestCase.async_setup()`,
**before** any window is created, giving iTerm2 the best chance to load it.
It is idempotent — it checks for the file first and skips writing if already present,
avoiding per-run reload races.

`create_tagged_window()` passes `profile="MCP-TEST"` to `create_window()`.
`create_window()` already falls back to the default profile on any exception,
so if iTerm2 hasn't loaded the profile yet the window still opens.

### 2. `user.mcp_test_run` variable (programmatic teardown key)

`mark_session()` sets `session.async_set_variable("user.mcp_test_run", tag)`
immediately after window creation.  This is the **primary teardown key**:

- Races to zero — no reload lag.
- Exact-match only — the teardown never closes a window it doesn't own.
- Works even when the `MCP-TEST` profile hasn't loaded (window opened under default).

### Orphan sweep (`prefix_sweep=True`)

For sessions left over from previously crashed runs (where the profile loaded
but no variable remains readable), `close_tagged_sessions(prefix_sweep=True)` checks
whether the profile name `startswith("MCP-TEST")`.  This matches both:
- `"MCP-TEST"` — the stable profile
- `"MCP-TEST·…"` — any per-run-named variants (historical / future)

Production profiles (`"MCP Agent"`, `"MCP Team: …"`) never start with `"MCP-TEST"`
so they are always safe.

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

Tests `close_tagged_sessions` and `ensure_test_profile` with mock/temp objects
(no live iTerm2 required).  34 tests total.

### `make_run_tag` (5 tests)
- format matches `MCP-TEST·<digits>-<hex8>` pattern
- embeds current PID
- two calls differ
- starts with TAG_PREFIX
- UUID portion is 8 lowercase hex chars

### `mark_session` (2 tests)
- calls `async_set_variable("user.mcp_test_run", tag)` correctly
- tolerates `async_set_variable` failure (no re-raise)

### `close_tagged_sessions` matching (10 tests)
- closes session with matching tag
- closes multiple matching sessions
- `prefix_sweep` closes orphan profile (name starts with `MCP-TEST·`)
- skips session with different tag
- skips session with empty tag
- skips session where variable raises (absent)
- skips normal profile without prefix_sweep
- `prefix_sweep` skips `MCP Agent` profile
- `prefix_sweep` skips `MCP Team: Foo` profile
- skips session where profile read raises during prefix_sweep
- mixed batch: only matching session is closed

### `close_tagged_sessions` robustness (4 tests)
- continues after `async_close` error
- returns 0 when `async_get_app` fails
- returns 0 when app has no windows
- returns correct count

### `ensure_test_profile` (6 tests, filesystem — uses temp dir)
- writes profile file
- written profile contains `MCP-TEST` name
- idempotent: second call does not overwrite
- creates directory if missing
- tolerates write failure (no raise)
- profile has distinctive badge text `"MCP-TEST"`

### `prefix_sweep` broadened matching (6 tests)
- matches stable `"MCP-TEST"` profile name
- matches `"MCP-TEST·orphan"` variant
- NEVER matches `"MCP Agent"`
- NEVER matches `"MCP Team: Engineering"`
- NEVER matches `"MCP Team:"` (colon only)
- NEVER matches `"Default"`

---

## Commit Plan

1. `docs/superpowers/plans/2026-06-13-test-window-hygiene.md` — this file
2. `core/test_window_tracker.py` + `tests/test_window_tracker.py` (TDD)
3. `tests/live_iterm_base.py` — base class
4. Convert 7 live modules (surgical edits only)
