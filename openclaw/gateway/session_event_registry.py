"""Session event/message subscription registries.

Mirrors TypeScript src/gateway/server-chat.ts:
  SessionEventSubscriberRegistry  — connId Set for sessions.subscribe
  SessionMessageSubscriberRegistry — sessionKey → connId Set for sessions.messages.subscribe
  ToolEventRecipientRegistry       — runId → connId Set with TTL + markFinal

These are used by the broadcast system (server.py) to target event delivery:
- session.message → union(sessionEventSubscribers.getAll(), sessionMessageSubscribers.get(sessionKey))
- sessions.changed → sessionEventSubscribers.getAll() only
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# How long to keep ToolEventRecipient entries after markFinal (seconds)
_TOOL_RECIPIENT_TTL_SECONDS = 30.0


class SessionEventSubscriberRegistry:
    """Global set of connection IDs subscribed to all session events.

    Mirrors TS SessionEventSubscriberRegistry.
    Subscribers added via sessions.subscribe RPC receive:
      - sessions.changed (all sessions)
      - session.message (all messages across all sessions)
    """

    def __init__(self) -> None:
        self._conn_ids: set[str] = set()

    def add(self, conn_id: str) -> None:
        """Subscribe a connection."""
        self._conn_ids.add(conn_id)
        logger.debug("SessionEventSubscriberRegistry.add: conn=%s", conn_id)

    def remove(self, conn_id: str) -> None:
        """Unsubscribe a connection."""
        self._conn_ids.discard(conn_id)
        logger.debug("SessionEventSubscriberRegistry.remove: conn=%s", conn_id)

    def get_all(self) -> set[str]:
        """Return all subscribed connection IDs (snapshot)."""
        return set(self._conn_ids)

    def __len__(self) -> int:
        return len(self._conn_ids)


class SessionMessageSubscriberRegistry:
    """Per-session-key message subscriber mapping.

    Mirrors TS SessionMessageSubscriberRegistry.
    Subscribers added via sessions.messages.subscribe(sessionKey) receive
    session.message events for that specific session only.
    """

    def __init__(self) -> None:
        # session_key → set of conn_ids
        self._map: dict[str, set[str]] = {}
        # conn_id → set of session_keys (for cleanup on disconnect)
        self._reverse: dict[str, set[str]] = {}

    def subscribe(self, conn_id: str, session_key: str) -> None:
        """Subscribe conn_id to messages for session_key."""
        if session_key not in self._map:
            self._map[session_key] = set()
        self._map[session_key].add(conn_id)

        if conn_id not in self._reverse:
            self._reverse[conn_id] = set()
        self._reverse[conn_id].add(session_key)

        logger.debug(
            "SessionMessageSubscriberRegistry.subscribe: conn=%s session=%s",
            conn_id,
            session_key,
        )

    def unsubscribe(self, conn_id: str, session_key: str) -> None:
        """Unsubscribe conn_id from messages for session_key."""
        if session_key in self._map:
            self._map[session_key].discard(conn_id)
            if not self._map[session_key]:
                del self._map[session_key]
        if conn_id in self._reverse:
            self._reverse[conn_id].discard(session_key)
            if not self._reverse[conn_id]:
                del self._reverse[conn_id]

    def get(self, session_key: str) -> set[str]:
        """Return connection IDs subscribed to session_key (snapshot)."""
        return set(self._map.get(session_key, set()))

    def unsubscribe_all(self, conn_id: str) -> None:
        """Remove all subscriptions for a connection (called on disconnect).

        Mirrors TS unsubscribeAllSessionEvents(connId).
        """
        session_keys = set(self._reverse.get(conn_id, set()))
        for sk in session_keys:
            self.unsubscribe(conn_id, sk)
        logger.debug(
            "SessionMessageSubscriberRegistry.unsubscribe_all: conn=%s keys=%d",
            conn_id,
            len(session_keys),
        )

    def get_all_session_keys_for_conn(self, conn_id: str) -> set[str]:
        """Return all session keys a connection is subscribed to."""
        return set(self._reverse.get(conn_id, set()))


@dataclass
class _ToolRecipientEntry:
    conn_ids: set[str]
    final: bool = False
    final_at: float | None = None


class ToolEventRecipientRegistry:
    """Maps run_id → set of connection IDs that should receive tool events.

    Mirrors TS ToolEventRecipientRegistry.
    Entries have a TTL after markFinal() to handle late-arriving events.
    """

    def __init__(self) -> None:
        self._map: dict[str, _ToolRecipientEntry] = {}

    def add(self, run_id: str, conn_ids: set[str]) -> None:
        """Register conn_ids as recipients for tool events in run_id."""
        self._map[run_id] = _ToolRecipientEntry(conn_ids=set(conn_ids))
        logger.debug(
            "ToolEventRecipientRegistry.add: run=%s conns=%d",
            run_id,
            len(conn_ids),
        )

    def get(self, run_id: str) -> set[str]:
        """Return conn_ids for run_id, or empty set if not found/expired."""
        entry = self._map.get(run_id)
        if entry is None:
            return set()
        # Expired after TTL
        if entry.final and entry.final_at is not None:
            if time.monotonic() - entry.final_at > _TOOL_RECIPIENT_TTL_SECONDS:
                del self._map[run_id]
                return set()
        return set(entry.conn_ids)

    def mark_final(self, run_id: str) -> None:
        """Mark a run as finalized — entry will be cleaned up after TTL."""
        entry = self._map.get(run_id)
        if entry:
            entry.final = True
            entry.final_at = time.monotonic()
            logger.debug("ToolEventRecipientRegistry.mark_final: run=%s", run_id)

    def remove(self, run_id: str) -> None:
        """Immediately remove an entry."""
        self._map.pop(run_id, None)

    def purge_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        now = time.monotonic()
        to_remove = [
            rid for rid, entry in self._map.items()
            if entry.final and entry.final_at is not None
            and now - entry.final_at > _TOOL_RECIPIENT_TTL_SECONDS
        ]
        for rid in to_remove:
            del self._map[rid]
        return len(to_remove)
