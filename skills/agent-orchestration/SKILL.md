---
name: agent-orchestration
description: >
  Use when orchestrating multi-agent workflows in iTerm2 — registering agents,
  managing teams, delegating tasks, executing plans, workflow events. Triggers on
  "register agent", "create team", "delegate task", "orchestrate", "workflow".
---

## iTerm Agent Orchestration

Tools for multi-agent coordination:

- **register_agent / list_agents / remove_agent** — Agent lifecycle
- **manage_teams** — Create/list/remove teams, assign agents
- **manage_managers** — Hierarchical manager-worker relationships
- **delegate_task** — Route tasks through managers to workers
- **execute_plan** — Multi-step plan execution with dependency handling
- **orchestrate_playbook** — High-level playbook (layout + commands + reads)
- **send_cascade_message / send_hierarchical_message** — Multi-target messaging
- **trigger_workflow_event / list_workflow_events** — Event bus
- **wait_for_agent** — Long-poll until agent completes
- **assign_session_role / list_available_roles** — Role-based access control
