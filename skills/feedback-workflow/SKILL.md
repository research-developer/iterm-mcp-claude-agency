---
name: feedback-workflow
description: >
  Use when submitting, querying, or triaging feedback about the iTerm MCP system.
  Triggers on "submit feedback", "bug report", "query feedback", "triage to github".
---

## iTerm Feedback Workflow

All feedback operations go through a single `feedback` tool using WebSpec
method semantics. For the live schema call `feedback(op="OPTIONS")`.

### Submitting feedback

- `feedback(op="submit", title="...", description="...", category="bug")` — submit
  feedback (aliases: `op="POST"` + `definer="CREATE"`, or `op="create"`)
  - Optional: `agent_name`, `session_id`, `reproduction_steps`,
    `suggested_improvement`, `error_messages`

### Querying

- `feedback(op="list")` — list feedback entries (aliases: `op="GET"`, `op="query"`)
  - Optional filters: `status`, `category`, `agent_name`, `limit`
- `feedback(op="GET", target="config")` — view feedback trigger configuration
- `feedback(op="PATCH", target="config", ...)` — update feedback trigger config

### Triage and forking

- `feedback(op="triage", target="issues", feedback_id="fbk-123")` — create a
  GitHub issue from a feedback entry (aliases: `op="POST"` + `definer="SEND"`)
- `feedback(op="fork", target="worktrees", feedback_id="fbk-123",
  session_id="sid")` — fork a git worktree for isolated testing

### Triggers

- `feedback(op="invoke", target="triggers", agent_name="alice", session_id="sid")` —
  record an event and check whether auto-feedback triggers should fire.
  Optional: `error_message`, `tool_call_name`, `output_text`.

### Notifications

- `feedback(op="notify", target="notifications", feedback_id="fbk-123")` — notify
  the submitting agent about a feedback status update
