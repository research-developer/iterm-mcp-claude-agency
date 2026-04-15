---
name: agent-orchestration
description: >
  Use when orchestrating multi-agent workflows in iTerm2 — registering agents,
  managing teams, delegating tasks, executing plans, workflow events. Triggers on
  "register agent", "create team", "delegate task", "orchestrate", "workflow".
---

## iTerm Agent Orchestration

The SP2 surface exposes a handful of method-semantic tools. Each takes
an `op` parameter that accepts either an HTTP method (GET/POST/DELETE)
or a friendly verb alias (list/create/remove/send/invoke). For a live
schema on any tool, call it with `op="OPTIONS"`.

### Agents (`agents`)

- `agents(op="list")` — list agents (optionally filter by `team`)
- `agents(op="GET", target="status")` — compact status summary for all agents
- `agents(op="create", agent_name="alice", session_id="sid", teams=["backend"])` — register
- `agents(op="delete", agent_name="alice")` — remove
- `agents(op="GET", target="notifications", agent="alice", limit=20)` — read notifications
- `agents(op="send", target="notifications", agent="alice", level="info",
  summary="build done")` — notify an agent
- `agents(op="GET", target="locks", agent="alice")` — list locks held by an agent
- `agents(op="GET", target="hooks", hooks_op="get_stats")` — hooks state / stats
- `agents(op="update", target="hooks", hooks_op="update_config", enabled=true)` — tune hooks

### Teams (`teams`)

- `teams(op="list")` — list teams with member counts
- `teams(op="create", team_name="backend", description="Backend squad")` — create
- `teams(op="create", target="agents", team_name="backend", agent_name="alice")` — assign
- `teams(op="delete", target="agents", team_name="backend", agent_name="alice")` — remove member
- `teams(op="delete", team_name="backend")` — delete team

### Managers (`managers`)

- `managers(op="list")` — list managers
- `managers(op="GET", manager_name="m1")` — get manager info
- `managers(op="create", manager_name="m1", workers=["w1","w2"],
  delegation_strategy="round_robin")` — create manager
- `managers(op="create", target="workers", manager_name="m1", worker_name="w3",
  worker_role="builder")` — add worker
- `managers(op="delete", target="workers", manager_name="m1", worker_name="w3")` — remove worker
- `managers(op="delete", manager_name="m1")` — remove manager

### Delegation (`delegate`)

- `delegate(op="POST", target="task", manager_name="m1", task="run tests",
  role="tester", timeout_seconds=120)` — delegate single task
- `delegate(op="POST", target="plan", manager_name="m1", plan={...})` — execute plan
  (plan shape matches the legacy ExecutePlanRequest.plan)

### Orchestration (`orchestrate`)

- `orchestrate(op="POST", playbook={...})` — run a playbook combining
  layout/commands/cascade/reads

### Messaging (`messages`)

- `messages(op="send", targets=[...], content="...")` — cascade or hierarchical delivery

### Workflows (`workflows`)

- `workflows(op="POST", target="events", event_name="build_failed",
  payload={...})` — trigger an event
- `workflows(op="GET", target="events")` — list recent events
- `workflows(op="GET", target="history", event_name="build_failed")` — event history

### Waiting and monitoring

- `wait_for(op="GET", agent_name="alice", wait_up_to=60)` — long-poll for idle
- `subscribe(op="POST", pattern="ERROR:.*", event_name="error_seen")` — subscribe
  to a terminal output regex (arms an event-bus pattern subscription)

### Roles (`roles`)

- `roles(op="list")` — list the session-role catalog (read-only)
  (role _assignment_ to a session uses `sessions(op="assign", target="roles", ...)`)
