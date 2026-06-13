# Agent Comms Tightening + Claude Code Message Integration

**Date:** 2026-06-12
**Status:** Research + design (no production code in this doc)
**Scope:** (1) Map current inter-agent communication in iterm-mcp-claude-agency, find the rough edges, and propose how to tighten the loop. (2) Research what Claude Code's "new message feature" actually is and design an integration. Factor in the in-flight singleton-daemon refactor (PR #129).

---

## Part 1 — Current inter-agent communication (as it actually works)

There are **four overlapping communication surfaces** in the repo, plus a fifth (`core/messaging.py`) that is built but effectively unused at the tool layer. None of them is a true message *bus* for agents; they are independently-grown mechanisms.

### 1.1 Surface inventory

| Surface | Code | Mechanism | Direction | Who consumes it |
|---|---|---|---|---|
| **Cascade / hierarchical messaging** | `core/agents.py` (`CascadingMessage`, `resolve_cascade_targets`, dedup), `iterm_mcpy/tools/messages.py`, `helpers.execute_cascade_request` | Resolve targets → `session.send_text()` (types keystrokes into the pane) | Orchestrator → agent panes | The agent's CLI, as if a human typed |
| **Notifications** | `NotificationManager` (in `fastmcp_server.py`, moving to `app_context.py`), `core/models.AgentNotification` | In-memory ring buffer; written by producers, read by `agents GET notifications` | Any producer → ring buffer → polling reader | Orchestrator / TUI polling `agents GET target=notifications` |
| **Pattern subscriptions** | `core/flows.py` (`EventBus.subscribe_to_pattern`, `process_terminal_output`), `iterm_mcpy/tools/subscribe.py` | Per-session polling loop diffs screen → regex match → callback (optionally writes a notification) | Terminal output → subscription callback | A workflow event and/or an agent's notification queue |
| **wait_for (long-poll idle)** | `iterm_mcpy/tools/wait_for.py` | Server-side 0.5s poll loop on `session.is_processing` until idle/timeout | Orchestrator blocks on one agent | Caller of `wait_for` |
| **EventBus / FlowManager** | `core/flows.py` | In-process async queue + listener registry (`@start/@listen/@router/@on_output`) | Intra-process event fan-out | `Flow` subclasses (only the demo `BuildDeployFlow` ships) |
| **Typed MessageRouter** *(latent)* | `core/messaging.py`, `core/message_handlers.py` | AutoGen-style typed request/response + topic pub/sub, content-hash dedup | Handler-based | **Not wired into any MCP tool** — parallel design, dead at the tool layer |

### 1.2 The actual data flow (the load-bearing chain)

The only path by which an agent "hears" something asynchronously is:

```
session.start_monitoring(0.2–0.5s poll)        # core/session.py:668
  → get_screen_contents() diff
  → monitor callbacks
  → event_bus.process_terminal_output()         # iterm_mcpy/tools/sessions.py:528
  → EventBus pattern subscriptions (regex)       # core/flows.py:654
  → on_match → notification_manager.add_simple() # subscribe.py:163
  → ring buffer (in memory)
  → agent/orchestrator polls `agents GET notifications`
```

**Every hop except the in-process callback dispatch is poll-based.** There is no point in the system where a producer pushes a payload that lands directly in a consumer's context. The "subscribe" tool (PR #125) is the closest thing to push, but it only converts *terminal text* into *a ring-buffer entry that still has to be polled*.

### 1.3 Mechanism, latency, reliability — per surface

**Cascade / hierarchical messaging (`messages` tool)**
- *Push or poll:* Push, but the transport is **simulated keystrokes** — `session.send_text(message, execute=True)` literally types the message into the target pane's CLI. There is no structured channel; the "message" is whatever the agent's REPL does with typed text.
- *Latency:* Immediate send; delivery "success" only means text was sent, not read or acted on.
- *Reliability:* Content-hash dedup via `AgentRegistry._message_history` (deque, `maxlen=1000`, persisted to `messages.jsonl`). Dedup is **per (content_hash, recipient)** — identical re-sends are silently dropped, which is a footgun (a legitimate repeat instruction is swallowed).
- *Rough edges:* No delivery ack, no read ack, no backpressure. Team resolution picks `agents[0]` arbitrarily (documented in `resolve_session`). If the target pane is mid-command, typed text corrupts the running command. No structure — the receiving agent can't distinguish an orchestrator message from user input.

**Notifications (`NotificationManager`)**
- *Push or poll:* Producer-push to buffer, **consumer-poll** to read. Ring buffer caps: `max_per_agent=50`, `max_total=200` (note: `add()` only enforces `max_total` — the `max_per_agent` field is declared but **not actually enforced** in `add()`; a chatty agent can evict others' notifications).
- *Latency:* Bounded by the consumer's poll cadence; nothing wakes the consumer.
- *Reliability:* In-memory only — **lost on daemon restart**. No durable cursor: a reader has no "mark as read"/offset, so it re-reads or uses fragile `since=<timestamp>` filtering. `get()` sorts by timestamp and slices `[:limit]`, so a burst > limit between polls silently drops the overflow from that read.
- *Rough edges:* No per-recipient delivery guarantee, no ack, no read state, no durability, unenforced per-agent cap.

**Pattern subscriptions (`subscribe` / EventBus)**
- *Push or poll:* The match is event-driven *within* the process, but it is **fed by the per-session polling monitor** (0.2–0.5s). So end-to-end it is poll-gated, and only fires on a **screen diff** — if output scrolls past between polls or doesn't change the visible screen, matches are missed.
- *Latency:* Up to one poll interval + screen-settle.
- *Reliability:* `process_terminal_output` snapshots the subscription list (good — allows self-unsubscribe), logs and swallows callback errors. Subscriptions live only in `EventBus._pattern_subscriptions` (in memory) — **lost on restart**, and there is no persistence of which patterns were armed. Regex is matched against the whole screen snapshot, so multi-match/partial-line matches are imprecise.
- *Rough edges:* Missed matches under fast output; no replay; cross-agent `target_agent` filter depends on `agent_registry` being passed into the monitor callback (else it always rejects). The notification it produces still has to be polled.

**wait_for (long-poll idle)**
- *Push or poll:* Server-side poll loop (0.5s) on `session.is_processing`, with a "settle" check (output must be unchanged across two reads) before declaring idle.
- *Latency:* Up to `wait_up_to` (1–600s). Blocks one tool call for the duration.
- *Reliability:* Idle detection is heuristic (`is_processing` + output-stability) — a process that pauses for input looks idle; an agent printing a spinner looks busy forever. One waiter per call; no multiplexing.
- *Rough edges:* Ties up a request slot; no "notify me when idle" push equivalent; can't wait on N agents in one call (the `messages`/cascade side can fan out but `wait_for` cannot fan in).

**EventBus / FlowManager**
- *Push or poll:* In-process async queue; genuine push **to in-process listeners only**.
- *Latency:* Sub-ms once an event is queued.
- *Reliability:* `max_history=1000`, routing-cycle detection, per-listener error isolation. But listeners are **Python objects registered in this process** — an agent (a separate Claude Code process in a pane) cannot register a listener. So the EventBus cannot deliver to agents directly; it can only end in a side effect (notification write, command send).
- *Rough edges:* No external subscriber model; flows auto-instantiate with a no-arg constructor (documented caveat); only the demo flow exists.

**Typed MessageRouter (`core/messaging.py`)** — Well-designed (correlation IDs, content-hash dedup with FIFO eviction, `send`/`send_multi`/`publish`/`broadcast`, a full message-type registry and (de)serializers). **But it is not registered by any tool in `iterm_mcpy/tools/`.** It is latent infrastructure: the natural home for a unified bus if we choose to build on it rather than greenfield.

### 1.4 Cross-cutting gaps (the "rough edges" summary)

1. **Poll everywhere.** The async illusion is built on polling: session monitor poll → notification poll → wait_for poll. Latency floors at the poll interval and CPU scales with session count.
2. **No delivery or read acknowledgement.** Cascade "delivered" = "typed into pane." Notifications have no read cursor. Nothing closes the loop back to the sender.
3. **No durability.** Notifications, pattern subscriptions, and EventBus history are all in-memory. A daemon restart loses every in-flight message and every armed subscription. (Only cascade dedup + agents/teams are JSONL-persisted.)
4. **No backpressure.** A fast producer silently evicts buffer entries (`max_total` trim) or drops overflow at read time (`[:limit]`).
5. **Five surfaces, no spine.** Cascade, notifications, subscriptions, wait_for, and EventBus don't share a message identity, addressing scheme, or envelope. `core/messaging.py` defines a spine but nothing uses it.
6. **Keystroke transport is fragile.** The only orchestrator→agent payload path types text into a REPL. It can't carry structured data, can't be addressed to "the agent" vs "the shell," and collides with in-flight commands.

### 1.5 What the singleton daemon (PR #129) changes

PR #129 collapses all clients into **one process with one set of registries** (`AppContext` singleton + shared streamable-HTTP daemon on 127.0.0.1:12340–12349). This is the enabling change for a real bus:

- **State is already process-global.** `NotificationManager`, `EventBus`, `AgentRegistry`, `ManagerRegistry` become genuinely shared across every connected Claude Code client. Cross-client/cross-agent messaging stops being a per-session illusion and becomes a single in-memory truth.
- **A bus would be a singleton too.** We get exactly one queue/topic table, so "send to agent X" resolves the same way regardless of which client called it. No cross-process sync needed.
- **The daemon is a natural push hub.** It already holds the iTerm2 connection and the monitor tasks. It is the one place that can both *observe* output and *deliver* to any pane — i.e., the obvious host for a push-based delivery loop and for an external ingress (channel) endpoint.
- **Caveat:** durability still matters more, not less — one daemon restart now wipes shared state for *all* clients at once. The buffer/subscription persistence gap is now a shared-blast-radius problem.

---

## Part 2 — Claude Code's "new message feature"

Research (knowledge cutoff Jan 2026 plus current docs/issue checks via the claude-code-guide agent) disambiguates several candidates. Findings, most-to-least relevant:

### 2.1 Channels — the most likely "new message feature" (REAL, research preview)
- **What:** An MCP-server class that **pushes external events into a running Claude Code session** — Telegram, Discord, iMessage, webhooks, CI results — delivered via a special `<channel>` element so Claude can react in real time, and **reply back through the same channel** (two-way).
- **Invocation:** `claude --channels plugin:telegram@claude-plugins-official` (plus credential pairing).
- **Status:** Research preview (~v2.1.80+), org-gated on Team/Enterprise. Only delivers while a session is open.
- **Why it matters here:** This is the surface most plausibly branded "new message feature," and it maps *directly* onto what this server already does — turn external events into in-session signals. An iterm-mcp Channel would let an external Claude Code session push a message that lands in a target agent's context.

### 2.2 Agent Teams mailbox (REAL, experimental, team-scoped)
- **What:** Agent Teams (experimental, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, ~v2.1.32+) run multiple independent sessions with a shared task list and a **mailbox**: teammates message each other by name; delivery is automatic (the lead does not poll).
- **Limits:** Only within an enabled team; no bridge to arbitrary independent sessions; known resumption bugs.
- **Why it matters:** This is the closest official analogue to what this repo is hand-rolling (named agents messaging each other). Our `teams`/`agents` model and a real bus would essentially be a self-hosted, iTerm-backed version of the Teams mailbox.

### 2.3 `SendMessage` to a running subagent (PARTIALLY REAL / unstable)
- **What:** Continue an already-running subagent with its context intact via `SendMessage({to: <agent_id>, message: ...})` instead of spawning a fresh `Agent`. The agent id is surfaced in subagent results.
- **Status:** This is the mechanism this very orchestration harness exposes (the prompt's system reminder documents `SendMessage` continuation). Note: there are public reports of it being referenced-but-unavailable in some CLI builds (GitHub issues #37051, #38183), so treat availability as build-dependent.
- **Why it matters:** If the user is thinking "the SendMessage thing," they want *resumable, context-preserving* messaging to a specific worker — which our cascade keystroke transport explicitly is *not*.

### 2.4 Agent SDK messaging primitives (REAL, session-level, not new)
- `ClaudeSDKClient` long-lived session + `query()` streaming input (async-iterable of user messages pushed into a live query), `resume`/session-id continuation, and a `SDKMessage` union (including `SDKTaskStarted/Progress/Notification` push events for background tasks). These are *session continuation* primitives, not agent-to-agent messaging, and predate "new."

### 2.5 Verdict
**Most likely interpretation:** the user means **Channels** — push external messages into a session and reply back. Design for that as the integration ingress/egress.

**Alternatives to keep in scope:** (a) **`SendMessage`-style context-preserving delivery to a named agent** (the orchestration harness primitive) and (b) the **Agent Teams mailbox** semantics (named peer-to-peer delivery). All three converge on the same need: *a structured, addressed, push-delivered message to a specific agent that lands in its context and can be acknowledged.* That convergence is the design target below.

---

## Part 3 — Proposal: tighten the loop + integrate CC messaging

### 3.1 Design target
A single **process-global Message Bus** (one per daemon, enabled by PR #129) that:
- Has one addressing scheme (`agent:`, `team:`, `session:`, `broadcast`).
- Carries **structured envelopes** (id, from, to, kind, body, correlation_id, timestamps), not raw keystrokes.
- Supports **push delivery** (long-poll now; SSE/WebSocket later) and **read/delivery acks**.
- Is **durable** (append-only JSONL per the existing `~/.iterm-mcp/` convention, replacaeable by SQLite — `SQLiteMemoryStore` already proves the pattern).
- Subsumes cascade, notifications, and subscribe outputs as *producers into the same bus*, so the five surfaces share a spine.
- Exposes an **external ingress** so a separate Claude Code session (via a Channel-style MCP client) can send into the bus and receive replies.

Build it on the latent `core/messaging.py` `AgentMessage`/`MessageRouter` (already has correlation IDs, dedup, pub/sub) rather than greenfield — promote it from dead code to the spine.

### 3.2 Top 3 proposals

**Proposal A — Unified `bus` tool + durable inbox (replaces poll-the-buffer with long-poll + ack).**
Introduce one action/collection tool `bus` that owns send + receive + ack, backed by a durable per-agent inbox. Notifications, cascade results, and subscribe matches all *write into the same inbox* as typed envelopes. Readers `receive` via **long-poll** (server holds the request open until a message arrives or timeout — like `wait_for`, but for messages, and multiplexed), then `ack` to advance a durable cursor. This kills the "poll the ring buffer and hope" pattern and gives delivery + read acknowledgement and backpressure (bounded inbox with explicit overflow policy, not silent eviction).

**Proposal B — Push delivery loop in the daemon (replaces simulated keystrokes for control messages).**
Because the daemon (PR #129) owns the iTerm2 connection and the monitor tasks, add a single delivery worker that, on `bus.send` to an agent, *pushes* the envelope to that agent — preferring a structured path (the agent polls/long-polls its inbox via the MCP tool from inside its own loop) and falling back to a clearly-delimited keystroke injection (`>>> [bus from=orchestrator id=… ] …`) only when no structured reader is attached. Pattern subscriptions and `wait_for` become bus producers (`agent.idle`, `pattern.matched` envelopes) instead of notification-only side effects. One delivery path, structured-first, keystroke-fallback.

**Proposal C — Claude Code ingress/egress (Channels-compatible bridge).**
Add a `bus` ingress so an external Claude Code session can: (1) **send** a message addressed to `agent:<name>` through this MCP server (the message becomes a durable envelope and is push-delivered by Proposal B), and (2) **receive** replies via long-poll or a Channel. Implement the egress as a thin Channel-style adapter: the daemon exposes a `<channel>`-shaped stream (or a long-poll `bus receive` endpoint) so a remote CC session reacts to bus traffic the same way it would to Telegram/Discord. This makes "an external Claude Code session sends/receives messages through this MCP server" a first-class, structured, acknowledged flow — and aligns us with the official Channels direction so we can later swap our long-poll for a native channel.

### 3.3 API sketch (tool ops / signatures)

New action tool `bus` (WebSpec verb semantics, consistent with the existing 15-tool surface):

```
bus op="POST"  definer="SEND"
    to="agent:builder" | "team:backend" | "session:<id>" | "broadcast"
    kind="instruction" | "notification" | "event" | "reply"
    body=<str|dict>
    correlation_id?=<id>          # ties replies to a request
    require_ack?=bool             # delivery semantics
    ttl_seconds?=int              # expiry / backpressure
  -> { message_id, accepted_targets: [...], rejected: [...] }

bus op="GET"   (long-poll receive)
    agent=<name>                  # the caller's inbox
    wait_up_to?=int (0–600)       # 0 = non-blocking drain
    since_cursor?=<cursor>        # durable offset; default = last acked
    kinds?=[...]                  # filter
  -> { messages: [Envelope...], next_cursor, has_more }

bus op="POST"  definer="TRIGGER"  target="ack"
    agent=<name> up_to_cursor=<cursor>   # advance durable read cursor
  -> { acked_through: <cursor> }

bus op="GET"   target="status"     # inbox depths, oldest unacked age, per-agent lag
bus op="OPTIONS"                    # self-describe (consistent with all SP2 tools)

Envelope = {
  message_id, from, to, kind, body,
  correlation_id?, created_at, delivered_at?, acked_at?,
  attempts, ttl_seconds?
}
```

Migration shims (keep the existing surface working):
- `messages` (cascade/hierarchical) → continues to work, but internally calls `bus.send(kind="instruction")`; keystroke send becomes the *fallback* transport, not the only one.
- `agents GET notifications` → reads from the bus inbox filtered to `kind="notification"`; old `NotificationManager.get()` becomes a thin adapter over `bus`.
- `subscribe` `notify_agent` → emits a `kind="event"` envelope onto the bus instead of a bare ring-buffer write.
- `wait_for` → unchanged externally, but also emits `agent.idle` events so other agents can await without holding a request slot.

### 3.4 Phased rollout

- **Phase 0 (no behavior change):** Promote `core/messaging.py` to the spine. Define the `Envelope` model and a durable `InboxStore` (JSONL first, mirroring `agents.jsonl`/`messages.jsonl`; SQLite later). Wire it into `AppContext` so it's a daemon singleton (depends on / lands after PR #129).
- **Phase 1 — read path:** Add `bus GET` long-poll + `ack` + durable cursor. Make `agents GET notifications` and the `subscribe` notify path write/read through it. Net win immediately: durable, acked, backpressured notifications with no new producer behavior.
- **Phase 2 — write/push path:** Add `bus POST SEND` + the daemon delivery worker (structured-first, keystroke-fallback). Route `messages` through it. Add `agent.idle`/`pattern.matched` event emission from `wait_for`/`subscribe`.
- **Phase 3 — Claude Code ingress/egress:** Channel-style adapter: external CC session sends/receives via `bus`. Start with long-poll egress; add a `<channel>`-compatible stream once the Channels API stabilizes out of research preview.
- **Phase 4 — transport upgrade:** Replace long-poll with SSE/WebSocket push from the daemon; optionally back the inbox with SQLite + FTS (reuse `SQLiteMemoryStore` patterns) for replay/search.

### 3.5 Risks

1. **Keystroke fallback is irreducibly fragile.** Until agents run a structured bus reader inside their own loop, "delivery" still bottoms out in typed text colliding with in-flight commands. Mitigation: detect prompt-idle (reuse `wait_for` heuristics) before injecting; clearly delimit injected envelopes.
2. **Durability vs. the shared blast radius (PR #129).** One daemon now serves all clients; a durable store is now *required*, not nice-to-have, or one restart drops everyone's traffic. Mitigation: land the durable inbox in the same release as the singleton, fsync/append discipline.
3. **Channels is a research preview.** The egress API may change; don't hard-couple to it. Mitigation: long-poll first, Channel adapter behind an interface.
4. **`SendMessage` build-dependence.** If the user specifically means subagent `SendMessage` continuation, note it's reported unavailable in some CLI builds — our bus can *emulate* the semantics (addressed, context-preserving delivery to a named agent) regardless, which is arguably more robust.
5. **Surface sprawl.** Adding `bus` while keeping `messages`/`subscribe`/`agents notifications` risks a sixth surface unless they are demoted to adapters. Mitigation: the migration shims in 3.3 are mandatory, not optional — the whole point is one spine.
6. **Long-poll request-slot pressure.** Many agents long-polling `bus GET` simultaneously can exhaust the daemon's request concurrency (same failure mode `wait_for` has at scale). Mitigation: cap concurrent long-polls per agent; move to SSE in Phase 4.

---

## Key open question for the user

**Which "new message feature" do you actually mean — Channels (push external messages into a session, reply back), or `SendMessage`-style context-preserving delivery to a named/running agent?** They point the integration in different directions: Channels makes this server an *ingress/egress bridge* for external CC sessions; `SendMessage` semantics make it an *internal addressed-delivery bus* for the agents it already manages. The proposal above builds the internal bus first (it's the shared spine both need) — but the Phase 3 ingress design and how much we invest in a Channel adapter hinges on your answer.
