# Agent Message Bus — Phase 1 Implementation Plan

**Date:** 2026-06-12
**Status:** Implementation
**Branch:** agent-af5ac01d7f0af1ffc (worktree from main)

---

## Overview

Build a shared, addressed, durable, push/long-poll message bus that lives in
the singleton daemon's process-global state. Phase 1 builds only the internal
bus; external ingress/egress (Channels bridge) is deferred to Phase 3.

The existing `core/messaging.py` `MessageRouter` handles typed
request/response routing between Python objects. The new `AgentMessageBus`
is a different, complementary primitive: it provides **addressed durable
delivery to named recipients** (agent:, team:, broadcast), **long-poll
receive**, and **ack/cursor advancement**. It does not replace
`MessageRouter`; both coexist. The bus is what tool-layer consumers (MCP
tool callers) interact with; `MessageRouter` remains a module-internal
handler-dispatch mechanism.

---

## 1. Envelope Shape

```python
class BusEnvelope(BaseModel):
    message_id: str          # UUID4
    sender: str              # "agent:builder" | "system" | arbitrary
    recipient: str           # "agent:<name>" | "team:<name>" | "broadcast"
    kind: str                # "instruction" | "notification" | "event" | "reply" | str
    body: Any                # str or JSON-serializable dict
    correlation_id: Optional[str]   # ties replies to a request
    created_at: datetime     # UTC, set by bus on enqueue
    ttl_seconds: Optional[int]      # expiry; None = never expire
    # delivery tracking (set by bus, not caller)
    attempts: int = 0
```

Stored as JSON in SQLite `bus_messages` table. `body` is JSON-encoded text.

---

## 2. SQLite Schema

Database: `~/.iterm-mcp/bus.db` (env override: `ITERM_MCP_BUS_DB_PATH`)

```sql
-- Inbox: one row per (recipient, message). recipient is the canonical
-- addressee after fan-out (broadcast → one row per registered agent).
CREATE TABLE IF NOT EXISTS bus_messages (
    rowid       INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  TEXT NOT NULL,
    recipient   TEXT NOT NULL,        -- "agent:<name>" | "broadcast"
    sender      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    body        TEXT NOT NULL,        -- JSON
    correlation_id TEXT,
    created_at  TEXT NOT NULL,        -- ISO8601 UTC
    ttl_seconds INTEGER,
    attempts    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_bus_recipient
    ON bus_messages(recipient, rowid);

-- Durable read cursors: last acked rowid per recipient.
CREATE TABLE IF NOT EXISTS bus_cursors (
    recipient   TEXT PRIMARY KEY,
    acked_rowid INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL
);
```

Cursor semantics: `acked_rowid` is the highest rowid the recipient has
acknowledged. `receive` returns `WHERE rowid > acked_rowid` for the
recipient, ordered by `rowid ASC`.

---

## 3. Bus Class API  (`core/bus.py`)

```python
class AgentMessageBus:
    def __init__(self, db_path: Optional[str] = None): ...

    # ---- write path ----
    async def send(
        self,
        sender: str,
        recipient: str,           # "agent:<name>" | "team:<name>" | "broadcast"
        kind: str,
        body: Any,
        correlation_id: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        agent_registry=None,      # needed to fan out team: / broadcast
    ) -> dict:
        """Enqueue envelope(s). Returns {message_id, accepted_recipients: [...]}."""

    # ---- read path ----
    async def receive(
        self,
        recipient: str,           # "agent:<name>"
        wait_up_to: int = 0,      # seconds; 0 = non-blocking drain
        since_cursor: Optional[int] = None,  # rowid; None = use durable cursor
        kinds: Optional[list[str]] = None,
        limit: int = 50,
    ) -> dict:
        """Return {messages: [...BusEnvelope dicts], next_cursor: int, has_more: bool}.
        Long-polls (asyncio.Event) if wait_up_to > 0 and inbox is empty.
        Does NOT advance the durable cursor automatically."""

    # ---- ack ----
    async def ack(
        self,
        recipient: str,
        up_to_cursor: int,        # rowid to ack through (inclusive)
    ) -> dict:
        """Advance the durable cursor. Returns {acked_through: int}."""

    # ---- introspection ----
    async def list_inboxes(self) -> list[dict]:
        """Inbox depths + oldest unacked age per recipient."""

    async def peek(
        self,
        recipient: str,
        limit: int = 5,
    ) -> dict:
        """Non-blocking drain (same as receive(wait_up_to=0))."""

    def close(self) -> None:
        """Close the SQLite connection."""
```

### Fan-out rules
- `agent:<name>` → one row for that agent.
- `team:<name>` → one row per registered member of the team.
- `broadcast` → one row per registered agent in the registry, plus one
  `recipient="broadcast"` row for consumers with no specific name.
  If `agent_registry` is None, only the `broadcast` row is written.

### Long-poll implementation
Each recipient gets one `asyncio.Event` in `_wakeup_events: dict[str, asyncio.Event]`.
`send()` sets the event for each recipient it writes to (and for "broadcast").
`receive()` waits on the event with `asyncio.wait_for(event.wait(), timeout)`.
On wake, it clears the event before querying (race-safe: at worst, it re-polls
once more and finds nothing, then waits again).

---

## 4. Tool Module: `iterm_mcpy/tools/bus.py`

Action tool (not a collection dispatcher). Ops:

| op                      | method+definer   | description |
|-------------------------|------------------|-------------|
| `"POST"` / `"send"`     | POST + SEND      | Enqueue a message |
| `"GET"` / `"receive"`   | GET              | Long-poll drain inbox |
| `"POST"` + `"TRIGGER"` / `"ack"` | POST + TRIGGER | Advance cursor |
| `"GET"` + `target="status"` | GET         | Inbox stats |
| `"GET"` + `target="peek"`   | GET         | Non-blocking drain |
| `"OPTIONS"`             | OPTIONS          | Self-describe |

Function signature:
```python
async def bus(
    ctx: Context,
    op: str = "GET",
    definer: Optional[str] = None,
    # send params
    to: Optional[str] = None,
    kind: str = "instruction",
    body: Optional[Any] = None,
    sender: Optional[str] = None,
    correlation_id: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
    # receive params
    agent: Optional[str] = None,
    wait_up_to: int = 0,
    since_cursor: Optional[int] = None,
    kinds: Optional[list[str]] = None,
    limit: int = 50,
    target: Optional[str] = None,    # "status" | "peek" | "ack"
    # ack params
    up_to_cursor: Optional[int] = None,
) -> dict[str, Any]:
```

---

## 5. AppContext Wiring

In `iterm_mcpy/app_context.py`:
1. Add `message_bus: Any = None` field to `AppContext` dataclass.
2. In `_build_app_context()`: construct `AgentMessageBus()` and assign to `message_bus`.
3. In `shutdown_app_context()`: call `ctx.message_bus.close()` if set.

---

## 6. NotificationManager Adapter

In `NotificationManager.add()` (after the existing ring-buffer write):
- If `_message_bus` is set, call `asyncio.create_task(bus.send(..., kind="notification", ...))`.
- The adapter is wired by `_build_app_context()` after both objects are constructed:
  `notification_manager._message_bus = message_bus`.
- This is additive: if `_message_bus` is None, nothing changes.
- Notifications also continue to be written to the ring buffer.

---

## 7. Test List (`tests/test_bus.py`)

All tests are `unittest.IsolatedAsyncioTestCase`; DB is `":memory:"` or a
`tempfile.mkstemp()` path that is deleted in `tearDown`.

1. **test_send_to_agent** — send `agent:alice` → one row in inbox.
2. **test_send_to_team** — fan-out to 2 team members → 2 rows.
3. **test_broadcast** — fan-out to 3 registered agents → 3 rows.
4. **test_receive_nonblocking** — drain empty inbox → `{messages: [], next_cursor: 0}`.
5. **test_receive_returns_messages** — send 3, receive → 3 envelopes in FIFO order.
6. **test_ack_advances_cursor** — ack through cursor, receive again → empty.
7. **test_cursor_durability** — reopen DB (new `AgentMessageBus(path)`), receive unacked → still there.
8. **test_long_poll_wakeup** — `receive(wait_up_to=5)` in task, `send` in another → resolves before timeout.
9. **test_long_poll_timeout** — `receive(wait_up_to=0.1)` with no producer → returns empty after timeout.
10. **test_per_recipient_fifo** — send A, B, C to alice, B, C to bob → each gets only their own in order.
11. **test_kinds_filter** — send "instruction" and "event", receive(kinds=["event"]) → only event.
12. **test_ttl_expiry** — send with `ttl_seconds=1`, wait 2s, receive → empty (expired rows filtered).
13. **test_notification_adapter** — `NotificationManager.add_simple(agent="alice")` with bus wired → bus inbox gets a notification envelope.
14. **test_bus_tool_send_op** — call `bus(op="POST", to="agent:alice", kind="instruction", body="hello")` → ok envelope, message_id in data.
15. **test_bus_tool_receive_op** — send then `bus(op="GET", agent="alice")` → messages in data.
16. **test_bus_tool_ack_op** — receive, then `bus(op="POST", definer="TRIGGER", agent="alice", up_to_cursor=N)` → acked_through.
17. **test_bus_tool_options** — `bus(op="OPTIONS")` → ok envelope with tool schema.

---

## 8. What Is Designed-For-But-Deferred

- **Phase 2 — write/push path:** Route `messages` tool through bus. Daemon delivery worker for keystroke fallback. `agent.idle` events from `wait_for`.
- **Phase 3 — Claude Code ingress/egress:** Channel-style adapter. External CC session sends/receives via `bus`. The envelope's `sender`/`recipient` address scheme is designed to accommodate an `external:<session_id>` prefix without schema changes.
- **Phase 4 — SSE/WebSocket push:** Replace long-poll with SSE stream from the daemon. The `asyncio.Event` wakeup mechanism is the same primitive an SSE loop would use; swapping is a transport-only change.
- **`send_multi` fan-out to in-process handlers:** The existing `MessageRouter` is kept as-is. Future work could make the bus call `router.publish()` for typed handler dispatch when the kind maps to a known topic.
