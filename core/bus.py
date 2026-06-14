"""Shared, addressed, durable message bus for the iTerm MCP daemon.

Phase 1 of the agent comms tightening plan (2026-06-12).  Provides:

- **Addressed delivery** to ``agent:<name>``, ``team:<name>``, ``broadcast``,
  or any arbitrary recipient string.
- **Durable inbox** backed by SQLite — messages survive daemon restarts until
  they are explicitly acknowledged.
- **Long-poll receive** that blocks (async, non-blocking to the event loop)
  until a message arrives or a timeout elapses.
- **Ack / cursor advancement** so readers have a stable, per-recipient read
  offset.

This bus is a *complement* to the existing ``core/messaging.py``
``MessageRouter`` (which handles in-process typed handler dispatch).  The bus
is what MCP tool callers interact with for durable, cross-agent delivery.

Future phases (not built here):
  - Phase 2: route ``messages`` tool through the bus; daemon delivery worker.
  - Phase 3: external ingress/egress bridge (Claude Code Channels adapter).
  - Phase 4: replace long-poll with SSE/WebSocket push.

The ``sender`` / ``recipient`` address scheme uses ``agent:<name>``,
``team:<name>``, ``broadcast``, and is extensible to ``external:<session_id>``
without schema changes.
"""

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("iterm-mcp-bus")


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


class BusEnvelope(BaseModel):
    """Structured message envelope carried by the bus.

    The bus writes ``created_at`` and ``rowid`` on enqueue; callers should
    not set them directly.

    Attributes:
        message_id: UUID4 string, unique per message.
        sender: Originator address, e.g. ``agent:builder`` or ``system``.
        recipient: Canonical recipient after fan-out, e.g. ``agent:alice``.
        kind: Semantic category — ``instruction``, ``notification``,
            ``event``, ``reply``, or any caller-defined string.
        body: The message payload.  Must be JSON-serializable.
        correlation_id: Optional ID tying a reply back to a request.
        created_at: UTC timestamp set by the bus on enqueue.
        ttl_seconds: Seconds before the message expires.  ``None`` = never.
        attempts: Delivery attempt counter (incremented by future push paths).
        rowid: SQLite rowid assigned on insert (used as the durable cursor).
    """

    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sender: str
    recipient: str
    kind: str = "instruction"
    body: Any = None
    correlation_id: Optional[str] = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    ttl_seconds: Optional[int] = None
    attempts: int = 0
    rowid: Optional[int] = None  # set after INSERT


# ---------------------------------------------------------------------------
# AgentMessageBus
# ---------------------------------------------------------------------------


class AgentMessageBus:
    """Process-global, durable agent message bus backed by SQLite.

    One instance lives in ``AppContext.message_bus``; all daemon clients share
    it.  The SQLite database is opened once and kept open for the daemon's
    lifetime.

    Args:
        db_path: Path to the SQLite database file.  Defaults to the
            ``ITERM_MCP_BUS_DB_PATH`` env var, or
            ``~/.iterm-mcp/bus.db``.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.environ.get(
                "ITERM_MCP_BUS_DB_PATH",
                os.path.expanduser("~/.iterm-mcp/bus.db"),
            )
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn: sqlite3.Connection = sqlite3.connect(
            str(self.db_path), check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()

        # Per-recipient asyncio.Event used for long-poll wakeup.
        # Event key is the exact recipient string stored in the DB.
        self._wakeup_events: Dict[str, asyncio.Event] = {}
        # A single "broadcast" event wakes all "broadcast" recipients.
        self._broadcast_event: asyncio.Event = asyncio.Event()

        self._init_db()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Initialize the SQLite schema (idempotent)."""
        c = self._conn
        c.executescript("""
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS bus_messages (
                rowid       INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id  TEXT NOT NULL,
                recipient   TEXT NOT NULL,
                sender      TEXT NOT NULL,
                kind        TEXT NOT NULL,
                body        TEXT NOT NULL,
                correlation_id TEXT,
                created_at  TEXT NOT NULL,
                ttl_seconds INTEGER,
                attempts    INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_bus_recipient
                ON bus_messages(recipient, rowid);

            CREATE TABLE IF NOT EXISTS bus_cursors (
                recipient   TEXT PRIMARY KEY,
                acked_rowid INTEGER NOT NULL DEFAULT 0,
                updated_at  TEXT NOT NULL
            );
        """)
        c.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _wakeup_event_for(self, recipient: str) -> asyncio.Event:
        """Get or create a wakeup event for a recipient."""
        if recipient not in self._wakeup_events:
            self._wakeup_events[recipient] = asyncio.Event()
        return self._wakeup_events[recipient]

    def _notify_recipient(self, recipient: str) -> None:
        """Set the wakeup event for a recipient so long-poll unblocks."""
        if recipient in self._wakeup_events:
            self._wakeup_events[recipient].set()
        if recipient == "broadcast":
            self._broadcast_event.set()

    def _resolve_fan_out_recipients(
        self,
        recipient: str,
        agent_registry=None,
    ) -> List[str]:
        """Expand ``team:`` and ``broadcast`` addresses into individual inboxes.

        Args:
            recipient: Raw recipient address from the caller.
            agent_registry: Optional ``AgentRegistry`` instance for lookups.

        Returns:
            List of canonical recipient strings to write inbox rows for.
        """
        if recipient.startswith("agent:"):
            return [recipient]

        if recipient == "broadcast":
            if agent_registry is None:
                return ["broadcast"]
            agents = agent_registry.list_agents()
            if not agents:
                return ["broadcast"]
            # Fan out to each registered agent AND keep a "broadcast" row for
            # consumers that subscribe without a specific name.
            return [f"agent:{a.name}" for a in agents] + ["broadcast"]

        # Future (manager phase): a ``project:<id>`` branch mirrors team: —
        # strip the prefix and fan out via agent_registry.list_agents(project=...).
        # Needs a first-class Agent.project field (deferred), so not added here.
        if recipient.startswith("team:"):
            team_name = recipient[len("team:"):]
            if agent_registry is None:
                # Can't resolve; fall back to the team address itself so
                # callers polling "team:<name>" can still receive.
                return [recipient]
            members = agent_registry.list_agents(team=team_name)
            if not members:
                return [recipient]
            return [f"agent:{a.name}" for a in members]

        # Arbitrary address (e.g. "system", "external:...")
        return [recipient]

    def _insert_envelope(
        self,
        envelope: BusEnvelope,
    ) -> int:
        """Write one envelope row to the DB. Returns the assigned rowid."""
        body_json = json.dumps(
            envelope.body, default=str
        ) if not isinstance(envelope.body, str) else envelope.body
        cursor = self._conn.execute(
            """
            INSERT INTO bus_messages
                (message_id, recipient, sender, kind, body,
                 correlation_id, created_at, ttl_seconds, attempts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                envelope.message_id,
                envelope.recipient,
                envelope.sender,
                envelope.kind,
                body_json,
                envelope.correlation_id,
                envelope.created_at.isoformat(),
                envelope.ttl_seconds,
                envelope.attempts,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid

    def _get_cursor(self, recipient: str) -> int:
        """Return the current acked_rowid for a recipient (0 if never acked)."""
        row = self._conn.execute(
            "SELECT acked_rowid FROM bus_cursors WHERE recipient = ?",
            (recipient,),
        ).fetchone()
        return row["acked_rowid"] if row else 0

    def _set_cursor(self, recipient: str, acked_rowid: int) -> None:
        """Upsert the durable cursor for a recipient."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO bus_cursors (recipient, acked_rowid, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(recipient) DO UPDATE
                SET acked_rowid = excluded.acked_rowid,
                    updated_at  = excluded.updated_at
            """,
            (recipient, acked_rowid, now),
        )
        self._conn.commit()

    def _query_messages(
        self,
        recipient: str,
        since_rowid: int,
        kinds: Optional[List[str]],
        limit: int,
        now_iso: str,
    ) -> List[sqlite3.Row]:
        """Fetch unread, non-expired messages for a recipient."""
        params: List[Any] = [recipient, since_rowid]

        kind_clause = ""
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            kind_clause = f"AND kind IN ({placeholders})"
            params.extend(kinds)

        # TTL filter: include rows where ttl_seconds IS NULL, or where the
        # message is not yet expired.  Expiry = created_at + ttl_seconds.
        # We compute expiry in Python-free SQL using datetime arithmetic:
        # datetime(created_at, '+N seconds') > datetime(?).
        # Both sides must be wrapped in datetime() so SQLite compares
        # normalised "YYYY-MM-DD HH:MM:SS" strings rather than doing a raw
        # lexical compare against the ISO-8601 string (which may include a
        # timezone offset that confuses ordering).
        params.append(now_iso)

        rows = self._conn.execute(
            f"""
            SELECT rowid, message_id, recipient, sender, kind, body,
                   correlation_id, created_at, ttl_seconds, attempts
            FROM bus_messages
            WHERE recipient = ?
              AND rowid > ?
              {kind_clause}
              AND (
                  ttl_seconds IS NULL
                  OR datetime(created_at, '+' || ttl_seconds || ' seconds') > datetime(?)
              )
            ORDER BY rowid ASC
            LIMIT ?
            """,
            params + [limit],
        ).fetchall()
        return rows

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        """Convert a DB row to a plain dict (BusEnvelope-compatible)."""
        try:
            body = json.loads(row["body"])
        except (json.JSONDecodeError, TypeError):
            body = row["body"]
        return {
            "rowid": row["rowid"],
            "message_id": row["message_id"],
            "recipient": row["recipient"],
            "sender": row["sender"],
            "kind": row["kind"],
            "body": body,
            "correlation_id": row["correlation_id"],
            "created_at": row["created_at"],
            "ttl_seconds": row["ttl_seconds"],
            "attempts": row["attempts"],
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send(
        self,
        sender: str,
        recipient: str,
        kind: str = "instruction",
        body: Any = None,
        correlation_id: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        agent_registry=None,
    ) -> Dict[str, Any]:
        """Enqueue a message to one or more inboxes.

        Fan-out:
          - ``agent:<name>`` → one inbox row.
          - ``team:<name>`` → one row per team member.
          - ``broadcast`` → one row per registered agent + a ``broadcast`` row.
          - Any other string → one row for that literal recipient.

        Args:
            sender: Originator address (``agent:<name>`` or arbitrary).
            recipient: Destination address.
            kind: Semantic category (``instruction``, ``notification``, etc.).
            body: JSON-serializable payload.
            correlation_id: Optional request-correlation token.
            ttl_seconds: Expiry in seconds.  ``None`` = never expires.
            agent_registry: Optional ``AgentRegistry`` for fan-out resolution.

        Returns:
            ``{message_id, accepted_recipients: [...]}``.
        """
        message_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc)

        recipients = self._resolve_fan_out_recipients(recipient, agent_registry)
        accepted: List[str] = []

        async with self._lock:
            for r in recipients:
                env = BusEnvelope(
                    message_id=message_id,
                    sender=sender,
                    recipient=r,
                    kind=kind,
                    body=body,
                    correlation_id=correlation_id,
                    created_at=created_at,
                    ttl_seconds=ttl_seconds,
                )
                try:
                    self._insert_envelope(env)
                    accepted.append(r)
                    logger.debug(
                        "bus.send: %s → %s kind=%s mid=%s",
                        sender, r, kind, message_id,
                    )
                except Exception:
                    logger.exception(
                        "bus.send: failed to insert envelope for %s", r
                    )

        # Notify outside the lock to avoid holding it during event dispatch.
        for r in accepted:
            self._notify_recipient(r)

        return {"message_id": message_id, "accepted_recipients": accepted}

    async def receive(
        self,
        recipient: str,
        wait_up_to: int = 0,
        since_cursor: Optional[int] = None,
        kinds: Optional[List[str]] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Return pending messages for a recipient, optionally long-polling.

        Reads messages with ``rowid > cursor`` (durable cursor if
        ``since_cursor`` is ``None``).  Does NOT advance the cursor
        automatically — call :meth:`ack` to commit a read position.

        Args:
            recipient: The inbox to read, e.g. ``agent:alice``.
            wait_up_to: Seconds to block waiting for a message.  0 = drain
                and return immediately even if the inbox is empty.
            since_cursor: Override the durable cursor; ``None`` uses the last
                acked rowid from the DB.
            kinds: Optional whitelist of ``kind`` values to return.
            limit: Maximum messages per call (default 50).

        Returns:
            ``{messages: [...], next_cursor: int, has_more: bool}``
            where ``next_cursor`` is the highest rowid returned (or the
            current cursor if no messages were returned).
        """
        deadline = wait_up_to  # seconds remaining

        async with self._lock:
            start_cursor = (
                since_cursor
                if since_cursor is not None
                else self._get_cursor(recipient)
            )

        now_iso = datetime.now(timezone.utc).isoformat()

        async def _drain() -> List[sqlite3.Row]:
            async with self._lock:
                return self._query_messages(
                    recipient, start_cursor, kinds, limit + 1, now_iso
                )

        rows = await _drain()

        if not rows and deadline > 0:
            # Long-poll: wait for a wakeup event or timeout.
            event = self._wakeup_event_for(recipient)
            try:
                await asyncio.wait_for(event.wait(), timeout=deadline)
            except asyncio.TimeoutError:
                pass
            finally:
                event.clear()
            rows = await _drain()

        has_more = len(rows) > limit
        rows = rows[:limit]

        messages = [self._row_to_dict(r) for r in rows]
        next_cursor = rows[-1]["rowid"] if rows else start_cursor

        return {
            "messages": messages,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    async def ack(
        self,
        recipient: str,
        up_to_cursor: int,
    ) -> Dict[str, Any]:
        """Advance the durable read cursor.

        Only moves the cursor forward — passing a cursor lower than the
        current position is a no-op (idempotent).

        Args:
            recipient: The inbox owner.
            up_to_cursor: Rowid to acknowledge through (inclusive).

        Returns:
            ``{acked_through: int}``.

        Note — Phase 1 unbounded growth:
            Acknowledged and TTL-expired rows are **never deleted** from
            ``bus_messages`` in Phase 1.  The table will grow indefinitely
            under heavy traffic.  A future ``purge(before_rowid)`` /
            TTL-sweep utility should be added (Phase 2) to reclaim space
            and keep query performance stable.
        """
        async with self._lock:
            current = self._get_cursor(recipient)
            new_cursor = max(current, up_to_cursor)
            self._set_cursor(recipient, new_cursor)
        return {"acked_through": new_cursor}

    async def peek(
        self,
        recipient: str,
        limit: int = 5,
    ) -> Dict[str, Any]:
        """Non-blocking drain.  Alias for ``receive(wait_up_to=0, limit=limit)``."""
        return await self.receive(recipient, wait_up_to=0, limit=limit)

    async def list_inboxes(self) -> List[Dict[str, Any]]:
        """Return inbox depths and oldest unacked message age per recipient.

        Returns:
            List of ``{recipient, depth, oldest_unacked_age_seconds}``.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    m.recipient,
                    COUNT(*) AS depth,
                    MIN(m.rowid) AS oldest_rowid,
                    MIN(m.created_at) AS oldest_created_at,
                    COALESCE(c.acked_rowid, 0) AS acked_rowid
                FROM bus_messages m
                LEFT JOIN bus_cursors c ON c.recipient = m.recipient
                WHERE m.rowid > COALESCE(c.acked_rowid, 0)
                  AND (
                      m.ttl_seconds IS NULL
                      OR datetime(m.created_at,
                             '+' || m.ttl_seconds || ' seconds') > datetime(?)
                  )
                GROUP BY m.recipient
                ORDER BY m.recipient
                """,
                (now_iso,),
            ).fetchall()

        result = []
        for row in rows:
            age_seconds: Optional[float] = None
            if row["oldest_created_at"]:
                try:
                    ts = datetime.fromisoformat(row["oldest_created_at"])
                    age_seconds = (
                        datetime.now(timezone.utc) - ts
                    ).total_seconds()
                except ValueError:
                    pass
            result.append({
                "recipient": row["recipient"],
                "depth": row["depth"],
                "oldest_unacked_age_seconds": age_seconds,
                "acked_rowid": row["acked_rowid"],
            })
        return result

    def close(self) -> None:
        """Close the SQLite connection.

        Called by ``shutdown_app_context()`` at process exit.
        """
        try:
            self._conn.close()
        except Exception:
            logger.exception("bus.close: error closing connection")
