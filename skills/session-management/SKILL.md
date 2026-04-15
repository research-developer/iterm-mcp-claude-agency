---
name: session-management
description: >
  Use when managing iTerm2 terminal sessions — creating layouts, reading output,
  writing commands, monitoring status, managing tags and locks. Triggers on
  "terminal session", "iTerm pane", "read output", "send command", "create layout".
---

## iTerm Session Management

All session operations go through a single `sessions` tool using WebSpec
method semantics (GET/POST/PATCH/DELETE/HEAD/OPTIONS) with Definer Verbs
(CREATE/SEND/TRIGGER/MODIFY/APPEND) for state-mutating operations.

If you need a detail this skill doesn't spell out, call
`sessions(op="OPTIONS")` to get the live tool schema.

### Listing and inspection

- `sessions(op="list")` — list all sessions (alias: `op="GET"`)
- `sessions(op="list", agent="alice")` — filter by agent/team/tag/role/locked
- `sessions(op="HEAD")` — compact list (just id/name/agent/is_processing/locked)
- `sessions(op="OPTIONS")` — discover the full tool surface
- `sessions(op="GET", target="status", session_id="sid")` — processing state

### Creating and splitting

- `sessions(op="create", layout="quad", sessions=[...])` — create sessions from a layout
- `sessions(op="create", target="splits", session_id="sid", direction="below")` — split a pane
  - Optional: `name`, `agent`, `team`, `agent_type`, `command`, `role`

### Reading and writing output

- `sessions(op="GET", target="output", session_id="sid")` — read terminal output
- `sessions(op="send", target="output", content="ls\n", session_id="sid")` — write text
- `sessions(op="send", target="output", messages=[...])` — multi-session write
  (same shape as the old write_to_sessions `messages` list)

### Keys

- `sessions(op="send", target="keys", key="enter", session_id="sid")` — named key
  (enter, tab, escape, up/down/left/right)
- `sessions(op="send", target="keys", control_char="C", session_id="sid")` — Ctrl+C

### Tags, roles, locks

- `sessions(op="update", target="tags", session_id="sid", tags=["x"])` — replace
- `sessions(op="append", target="tags", session_id="sid", tags=["x"])` — append
- `sessions(op="assign", target="roles", session_id="sid", role="builder")` — assign role
- `sessions(op="delete", target="roles", session_id="sid")` — remove role
- `sessions(op="update", target="locks", session_id="sid", agent="alice", action="lock")` — acquire
- `sessions(op="update", target="locks", session_id="sid", agent="alice", action="request_access")` — request
- `sessions(op="unlock", target="locks", session_id="sid", agent="alice")` — release

### Appearance, focus, suspend

- `sessions(op="update", target="active", session_id="sid", focus=true)` — focus
- `sessions(op="update", target="appearance", session_id="sid", badge="Worker",
  tab_color={"red":100,"green":200,"blue":255})` — appearance + colors
- `sessions(op="update", target="appearance", session_id="sid", suspended=true)` — suspend
- `sessions(op="update", target="appearance", session_id="sid", suspended=false)` — resume

### Monitoring

- `sessions(op="start", target="monitoring", session_id="sid")` — begin monitoring
- `sessions(op="stop", target="monitoring", session_id="sid")` — stop monitoring
