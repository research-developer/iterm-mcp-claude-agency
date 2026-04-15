---
name: session-management
description: >
  Use when managing iTerm2 terminal sessions — creating layouts, reading output,
  writing commands, monitoring status, managing tags and locks. Triggers on
  "terminal session", "iTerm pane", "read output", "send command", "create layout".
---

## iTerm Session Management

The iTerm MCP server provides session lifecycle and I/O tools:

- **list_sessions** — List all sessions with filtering by agent, tag, lock status
- **create_sessions** — Create new terminal sessions with optional agent registration
- **read_sessions** — Read terminal output from one or more sessions
- **write_to_sessions** — Send commands/text to sessions
- **split_session** — Split an existing session directionally
- **modify_sessions** — Change session appearance, focus, suspend/resume
- **check_session_status** — Check if a session is processing
- **set_session_tags** — Tag sessions for filtering
- **manage_session_lock** — Lock/unlock sessions for exclusive agent access
- **start_monitoring_session / stop_monitoring_session** — Real-time output monitoring
- **send_control_character** — Send Ctrl+C etc.
- **send_special_key** — Send Enter, Tab, Escape, arrows
