---
description: Display the current iTerm Session ID and Claude Code session ID
---

# Session IDs

Display session identification information for debugging and correlation purposes.

## Instructions

1. First, get the iTerm session information using the `check_session_status` tool (or `list_sessions` with format="json" for multiple sessions)

2. Display the following information:
   - **iTerm Session ID** (`id`) - The native iTerm2 session identifier (UUID format)
   - **Persistent ID** (`persistent_id`) - Persistent session ID for reconnection across restarts
   - **Session Name** (`name`) - Human-readable session name
   - **Agent Name** (`agent`) - Associated agent name (if registered)
   - **Active Status** (`is_active`) - Whether this is the currently active session

3. For the Claude Code session ID, check the environment variable:
   - Run: `echo $CLAUDE_SESSION_ID` in the terminal to get the Claude session ID
   - This is set by iTerm MCP hooks when entering a repository

## Example Output Format

Present the results clearly:

```
Session IDs
-----------
iTerm Session ID:   ABC123-DEF456-789...
Persistent ID:      persistent-uuid-here...
Session Name:       main
Agent:              worker-1
Active:             Yes

Claude Code Session: (check $CLAUDE_SESSION_ID in terminal)
```
