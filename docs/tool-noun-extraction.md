# iTerm MCP Tool Name Decomposition

## Step 1: Split all 52 function names into word tuples

| # | Function Name | Word Tuple |
|---|---------------|------------|
| 1 | `list_sessions` | (list, sessions) |
| 2 | `set_session_tags` | (set, session, tags) |
| 3 | `set_active_session` | (set, active, session) |
| 4 | `create_sessions` | (create, sessions) |
| 5 | `split_session` | (split, session) |
| 6 | `modify_sessions` | (modify, sessions) |
| 7 | `check_session_status` | (check, session, status) |
| 8 | `start_monitoring_session` | (start, monitoring, session) |
| 9 | `stop_monitoring_session` | (stop, monitoring, session) |
| 10 | `start_telemetry_dashboard` | (start, telemetry, dashboard) |
| 11 | `write_to_sessions` | (write, to, sessions) |
| 12 | `read_sessions` | (read, sessions) |
| 13 | `send_cascade_message` | (send, cascade, message) |
| 14 | `send_hierarchical_message` | (send, hierarchical, message) |
| 15 | `select_panes_by_hierarchy` | (select, panes, by, hierarchy) |
| 16 | `send_control_character` | (send, control, character) |
| 17 | `send_special_key` | (send, special, key) |
| 18 | `orchestrate_playbook` | (orchestrate, playbook) |
| 19 | `register_agent` | (register, agent) |
| 20 | `list_agents` | (list, agents) |
| 21 | `remove_agent` | (remove, agent) |
| 22 | `manage_teams` | (manage, teams) |
| 23 | `manage_managers` | (manage, managers) |
| 24 | `manage_session_lock` | (manage, session, lock) |
| 25 | `list_my_locks` | (list, my, locks) |
| 26 | `assign_session_role` | (assign, session, role) |
| 27 | `get_session_role` | (get, session, role) |
| 28 | `remove_session_role` | (remove, session, role) |
| 29 | `list_session_roles` | (list, session, roles) |
| 30 | `list_available_roles` | (list, available, roles) |
| 31 | `check_tool_permission` | (check, tool, permission) |
| 32 | `get_notifications` | (get, notifications) |
| 33 | `get_agent_status_summary` | (get, agent, status, summary) |
| 34 | `notify` | (notify) |
| 35 | `wait_for_agent` | (wait, for, agent) |
| 36 | `submit_feedback` | (submit, feedback) |
| 37 | `check_feedback_triggers` | (check, feedback, triggers) |
| 38 | `query_feedback` | (query, feedback) |
| 39 | `fork_for_feedback` | (fork, for, feedback) |
| 40 | `triage_feedback_to_github` | (triage, feedback, to, github) |
| 41 | `notify_feedback_update` | (notify, feedback, update) |
| 42 | `get_feedback_config` | (get, feedback, config) |
| 43 | `manage_agent_hooks` | (manage, agent, hooks) |
| 44 | `manage_services` | (manage, services) |
| 45 | `delegate_task` | (delegate, task) |
| 46 | `execute_plan` | (execute, plan) |
| 47 | `trigger_workflow_event` | (trigger, workflow, event) |
| 48 | `list_workflow_events` | (list, workflow, events) |
| 49 | `get_workflow_event_history` | (get, workflow, event, history) |
| 50 | `subscribe_to_output_pattern` | (subscribe, to, output, pattern) |
| 51 | `manage_memory` | (manage, memory) |
| 52 | `get_sessions_by_role` | (get, sessions, by, role) |

---

## Step 2: Part-of-speech classification

### Verbs (HTTP method equivalents)

| Verb | WebSpec METHOD | Occurrences |
|------|---------------|-------------|
| list | GET | 6 (sessions, agents, locks, roles×2, workflow_events) |
| get | GET | 5 (session_role, notifications, agent_status, feedback_config, workflow_event_history) |
| read | GET | 1 (sessions) |
| query | GET | 1 (feedback) |
| check | HEAD | 3 (session_status, feedback_triggers, tool_permission) |
| create | POST | 1 (sessions) |
| submit | POST | 1 (feedback) |
| register | POST | 1 (agent) |
| trigger | POST | 1 (workflow_event) |
| subscribe | POST | 1 (output_pattern) |
| write | POST/PUT | 1 (sessions) |
| send | POST | 4 (cascade_message, hierarchical_message, control_character, special_key) |
| notify | POST | 2 (bare, feedback_update) |
| delegate | POST | 1 (task) |
| execute | POST | 1 (plan) |
| orchestrate | POST | 1 (playbook) |
| fork | POST | 1 (feedback) |
| triage | POST | 1 (feedback) |
| set | PATCH | 2 (session_tags, active_session) |
| modify | PATCH | 1 (sessions) |
| assign | PATCH | 1 (session_role) |
| manage | PATCH (multi) | 6 (teams, managers, session_lock, agent_hooks, services, memory) |
| split | POST | 1 (session) |
| select | GET/PATCH | 1 (panes) |
| start | POST | 2 (monitoring_session, telemetry_dashboard) |
| stop | DELETE | 1 (monitoring_session) |
| remove | DELETE | 2 (agent, session_role) |
| wait | GET (long-poll) | 1 (agent) |

### Verb -> METHOD mapping (WebSpec style)

| Current verb | Canonical METHOD |
|-------------|-----------------|
| list, get, read, query, check, select, wait | **GET** |
| create, submit, register, trigger, subscribe, send, notify, delegate, execute, orchestrate, fork, triage, split, start | **POST** |
| set, modify, assign, manage | **PATCH** |
| stop, remove | **DELETE** |

---

## Step 3: Extract nouns & group by shared noun

### Primary Nouns (Collections)

#### `sessions` — the dominant resource (15 tools touch it)

| Accompanying words | Function |
|--------------------|----------|
| {list} | list_sessions |
| {create} | create_sessions |
| {read} | read_sessions |
| {write, to} | write_to_sessions |
| {modify} | modify_sessions |
| {split} | split_session |
| {set, active} | set_active_session |
| {check, status} | check_session_status |
| {start, monitoring} | start_monitoring_session |
| {stop, monitoring} | stop_monitoring_session |
| {set, tags} | set_session_tags |
| {manage, lock} | manage_session_lock |
| {assign, role} | assign_session_role |
| {get, role} | get_session_role |
| {remove, role} | remove_session_role |
| {list, roles} | list_session_roles |
| {get, by, role} | get_sessions_by_role |

#### `agents` — 7 tools

| Accompanying words | Function |
|--------------------|----------|
| {register} | register_agent |
| {list} | list_agents |
| {remove} | remove_agent |
| {get, status, summary} | get_agent_status_summary |
| {wait, for} | wait_for_agent |
| {manage, hooks} | manage_agent_hooks |

#### `feedback` — 7 tools

| Accompanying words | Function |
|--------------------|----------|
| {submit} | submit_feedback |
| {query} | query_feedback |
| {check, triggers} | check_feedback_triggers |
| {fork, for} | fork_for_feedback |
| {triage, to, github} | triage_feedback_to_github |
| {notify, update} | notify_feedback_update |
| {get, config} | get_feedback_config |

#### `roles` — 5 tools

| Accompanying words | Function |
|--------------------|----------|
| {assign, session} | assign_session_role |
| {get, session} | get_session_role |
| {remove, session} | remove_session_role |
| {list, session} | list_session_roles |
| {list, available} | list_available_roles |

#### `workflow` / `events` — 3 tools

| Accompanying words | Function |
|--------------------|----------|
| {trigger, event} | trigger_workflow_event |
| {list, events} | list_workflow_events |
| {get, event, history} | get_workflow_event_history |

#### `teams` — 1 tool (multi-op)

| Accompanying words | Function |
|--------------------|----------|
| {manage} | manage_teams |

#### `managers` — 1 tool (multi-op)

| Accompanying words | Function |
|--------------------|----------|
| {manage} | manage_managers |

#### `services` — 1 tool (multi-op)

| Accompanying words | Function |
|--------------------|----------|
| {manage} | manage_services |

#### `memory` — 1 tool (multi-op)

| Accompanying words | Function |
|--------------------|----------|
| {manage} | manage_memory |

#### `locks` — 2 tools

| Accompanying words | Function |
|--------------------|----------|
| {manage, session} | manage_session_lock |
| {list, my} | list_my_locks |

#### `notifications` — 2 tools

| Accompanying words | Function |
|--------------------|----------|
| {get} | get_notifications |
| {notify} (bare verb -> notification) | notify |

#### `messages` — 2 tools

| Accompanying words | Function |
|--------------------|----------|
| {send, cascade} | send_cascade_message |
| {send, hierarchical} | send_hierarchical_message |

#### `panes` — 1 tool

| Accompanying words | Function |
|--------------------|----------|
| {select, by, hierarchy} | select_panes_by_hierarchy |

#### `permissions` — 1 tool

| Accompanying words | Function |
|--------------------|----------|
| {check, tool} | check_tool_permission |

### Verb-only (needs nominalization)

| Function | Bare verb | Nominalized noun |
|----------|-----------|-----------------|
| `notify` | notify | **notification** (→ merges with notifications) |
| `orchestrate_playbook` | orchestrate | **orchestration** / playbook is the noun |
| `delegate_task` | delegate | **delegation** / task is the noun |
| `execute_plan` | execute | **execution** / plan is the noun |
| `subscribe_to_output_pattern` | subscribe | **subscription** / pattern is the noun |

### Modifiers/Adpositions (not nouns or verbs)

| Word | Role | Found in |
|------|------|----------|
| to | adposition | write_to_sessions, subscribe_to_output_pattern, triage_feedback_to_github |
| for | adposition | wait_for_agent, fork_for_feedback |
| by | adposition | select_panes_by_hierarchy, get_sessions_by_role |
| my | possessive filter | list_my_locks |
| active | adjective/state | set_active_session |
| available | adjective/filter | list_available_roles |
| special | adjective | send_special_key |
| control | adjective | send_control_character |
| cascade | adjective | send_cascade_message |
| hierarchical | adjective | send_hierarchical_message |

---

## Step 4: WebSpec-style REST resource grammar

Applying the WebSpec pattern: **subdomain = provider, path = /collection/id/collection/id, METHOD = verb, query = filtering**

### Provider subdomain

```
iterm.local     (or iterm.gimme.tools in hosted mode)
```

### Collection hierarchy

```yaml
sessions:                          # The terminal sessions
  children:
    - status                       # GET /sessions/{id}/status
    - tags                         # GET|PATCH /sessions/{id}/tags
    - locks                        # GET|POST|DELETE /sessions/{id}/locks
    - roles                        # GET|PATCH|DELETE /sessions/{id}/roles
    - monitoring                   # POST|DELETE /sessions/{id}/monitoring
    - output                       # GET /sessions/{id}/output (read)

agents:                            # Registered agents
  children:
    - status                       # GET /agents/{name}/status
    - hooks                        # GET|PATCH /agents/{name}/hooks
    - locks                        # GET /agents/{name}/locks
    - notifications                # GET /agents/{name}/notifications

teams:                             # Agent teams
  children:
    - agents                       # GET|POST|DELETE /teams/{name}/agents

managers:                          # Manager hierarchy
  children:
    - workers                      # GET|POST|DELETE /managers/{name}/workers

messages:                          # Cascade/hierarchical messaging
  # POST /messages (with targeting in body)

feedback:                          # Feedback entries
  children:
    - triggers                     # GET /feedback/triggers
    - config                       # GET|PATCH /feedback/config
    - worktrees                    # POST /feedback/{id}/worktrees (fork)
    - issues                       # POST /feedback/{id}/issues (triage to github)

workflows:                         # Workflow events
  children:
    - events                       # GET|POST /workflows/events
    - history                      # GET /workflows/events/{name}/history

services:                          # Managed services

memory:                            # Key-value memory store
  children:
    - namespaces                   # GET /memory/namespaces
    - keys                         # GET /memory/{namespace}/keys

plans:                             # Execution plans
  # POST /plans (execute)

tasks:                             # Delegated tasks
  # POST /tasks (delegate)

playbooks:                         # Orchestration playbooks
  # POST /playbooks (orchestrate)

roles:                             # Role definitions (not session-scoped)
  # GET /roles (list available)

subscriptions:                     # Output pattern subscriptions
  # POST /subscriptions

telemetry:                         # Telemetry dashboard
  children:
    - dashboard                    # POST|DELETE /telemetry/dashboard
```

---

## Step 5: Current 52 tools -> WebSpec REST mapping

| Current tool | METHOD | WebSpec Path | Query params |
|-------------|--------|-------------|-------------|
| list_sessions | GET | /sessions | ?agents_only=&tag=&locked=&format= |
| create_sessions | POST | /sessions | |
| read_sessions | GET | /sessions/{id}/output | ?max_lines=&strip_ansi= |
| write_to_sessions | POST | /sessions/{id}/output | |
| modify_sessions | PATCH | /sessions/{id} | |
| split_session | POST | /sessions/{id}/splits | ?direction= |
| set_active_session | PATCH | /sessions/{id} | (body: {active: true}) |
| check_session_status | GET | /sessions/{id}/status | |
| start_monitoring_session | POST | /sessions/{id}/monitoring | |
| stop_monitoring_session | DELETE | /sessions/{id}/monitoring | |
| set_session_tags | PATCH | /sessions/{id}/tags | ?append= |
| manage_session_lock | POST/DELETE | /sessions/{id}/locks | |
| assign_session_role | PATCH | /sessions/{id}/roles | |
| get_session_role | GET | /sessions/{id}/roles | |
| remove_session_role | DELETE | /sessions/{id}/roles | |
| list_session_roles | GET | /sessions/roles | ?role= |
| get_sessions_by_role | GET | /sessions | ?role= |
| list_available_roles | GET | /roles | |
| check_tool_permission | GET | /sessions/{id}/roles/permissions/{tool} | |
| register_agent | POST | /agents | |
| list_agents | GET | /agents | ?team= |
| remove_agent | DELETE | /agents/{name} | |
| get_agent_status_summary | GET | /agents/status | |
| wait_for_agent | GET | /agents/{name}/status | ?poll=true&timeout= |
| manage_agent_hooks | GET/PATCH | /agents/{name}/hooks | |
| manage_teams | * | /teams, /teams/{name}/agents | |
| manage_managers | * | /managers, /managers/{name}/workers | |
| list_my_locks | GET | /agents/{name}/locks | |
| get_notifications | GET | /agents/{name}/notifications | ?level=&limit=&since= |
| notify | POST | /agents/{name}/notifications | |
| send_cascade_message | POST | /messages | (body: cascade targets) |
| send_hierarchical_message | POST | /messages | (body: hierarchical targets) |
| select_panes_by_hierarchy | GET | /sessions | ?team=&agent=&set_active= |
| send_control_character | POST | /sessions/{id}/keys | (body: {ctrl: "C"}) |
| send_special_key | POST | /sessions/{id}/keys | (body: {key: "enter"}) |
| orchestrate_playbook | POST | /playbooks | |
| submit_feedback | POST | /feedback | |
| query_feedback | GET | /feedback | ?status=&category=&agent_name= |
| check_feedback_triggers | POST | /feedback/triggers/check | |
| fork_for_feedback | POST | /feedback/{id}/worktrees | |
| triage_feedback_to_github | POST | /feedback/{id}/issues | |
| notify_feedback_update | POST | /feedback/{id}/notifications | |
| get_feedback_config | GET/PATCH | /feedback/config | |
| manage_services | * | /services, /services/{name} | |
| manage_memory | * | /memory/{namespace}/{key} | |
| delegate_task | POST | /tasks | |
| execute_plan | POST | /plans | |
| trigger_workflow_event | POST | /workflows/events | |
| list_workflow_events | GET | /workflows/events | |
| get_workflow_event_history | GET | /workflows/events/{name}/history | ?limit=&success_only= |
| subscribe_to_output_pattern | POST | /subscriptions | |
| start_telemetry_dashboard | POST | /telemetry/dashboard | ?port=&duration= |

---

## Step 6: Noun consolidation summary

The 52 tools reduce to **14 top-level collections**:

| Collection (noun) | Sub-resources | Verbs that act on it | Tool count |
|-------------------|---------------|---------------------|------------|
| **sessions** | output, status, tags, locks, roles, monitoring, keys, splits | GET POST PATCH DELETE | 17 |
| **agents** | status, hooks, locks, notifications | GET POST DELETE | 7 |
| **feedback** | triggers, config, worktrees, issues, notifications | GET POST PATCH | 7 |
| **roles** | permissions | GET | 2 |
| **teams** | agents | GET POST DELETE | 1 (multi-op) |
| **managers** | workers | GET POST DELETE | 1 (multi-op) |
| **messages** | — | POST | 2 |
| **workflows** | events, history | GET POST | 3 |
| **services** | — | GET POST PATCH DELETE | 1 (multi-op) |
| **memory** | namespaces, keys | GET POST DELETE | 1 (multi-op) |
| **playbooks** | — | POST | 1 |
| **plans** | — | POST | 1 |
| **tasks** | — | POST | 1 |
| **subscriptions** | — | POST | 1 |
| **telemetry** | dashboard | POST DELETE | 1 |
