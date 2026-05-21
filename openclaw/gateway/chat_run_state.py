"""Chat run state management — aligned with TypeScript server-chat.ts.

Manages queuing of chat runs per session using a simple per-session FIFO queue,
draft streaming buffers, and delta debouncing.

Key design decisions (matching TS):
- ChatRunEntry has exactly two fields: session_key + client_run_id
- ChatRunRegistry is a plain dict-based FIFO (no async lock / status machine)
- ChatRunState adds rawBuffers, buffers, deltaSentAt, deltaLastBroadcastLen, abortedRuns
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Delta debounce delay — mirrors TS DELTA_DEBOUNCE_MS
DELTA_DEBOUNCE_MS = 150


@dataclass
class ChatRunEntry:
    """Chat run queue entry — mirrors TS ChatRunEntry.

    Intentionally minimal: only session_key + client_run_id.
    All run-state tracking lives in ChatRunState (buffers, etc.).
    """

    session_key: str
    client_run_id: str


class ChatRunRegistry:
    """Per-session FIFO queue of ChatRunEntry items.

    Mirrors TS ChatRunRegistry in server-chat.ts.
    Key = sessionId (the run-id / session-id used for routing).
    """

    def __init__(self) -> None:
        self._map: dict[str, list[ChatRunEntry]] = {}

    def add(self, session_id: str, entry: ChatRunEntry) -> None:
        """Push an entry onto the tail of the session's queue."""
        if session_id not in self._map:
            self._map[session_id] = []
        self._map[session_id].append(entry)
        logger.debug(
            "ChatRunRegistry.add: session=%s clientRunId=%s",
            session_id,
            entry.client_run_id,
        )

    def peek(self, session_id: str) -> ChatRunEntry | None:
        """Return the head entry without removing it."""
        queue = self._map.get(session_id)
        if queue:
            return queue[0]
        return None

    def shift(self, session_id: str) -> None:
        """Remove the head entry from the queue (dequeue)."""
        queue = self._map.get(session_id)
        if queue:
            removed = queue.pop(0)
            logger.debug(
                "ChatRunRegistry.shift: session=%s removed clientRunId=%s",
                session_id,
                removed.client_run_id,
            )
            if not queue:
                del self._map[session_id]

    def remove(
        self,
        session_id: str,
        client_run_id: str,
        session_key: str | None = None,
    ) -> None:
        """Remove a specific entry by client_run_id (and optional session_key).

        Mirrors TS ChatRunRegistry.remove().
        """
        queue = self._map.get(session_id)
        if not queue:
            return
        before = len(queue)
        self._map[session_id] = [
            e for e in queue
            if not (
                e.client_run_id == client_run_id
                and (session_key is None or e.session_key == session_key)
            )
        ]
        after = len(self._map[session_id])
        if before != after:
            logger.debug(
                "ChatRunRegistry.remove: session=%s clientRunId=%s removed=%d",
                session_id,
                client_run_id,
                before - after,
            )
        if not self._map[session_id]:
            del self._map[session_id]

    def clear(self) -> None:
        """Clear all queues."""
        self._map.clear()

    def get_all_session_ids(self) -> list[str]:
        """Return all session ids that have active queue entries."""
        return list(self._map.keys())


@dataclass
class ChatRunState:
    """Full streaming state for the gateway's chat run lifecycle.

    Mirrors TS ChatRunState in server-chat.ts.

    Fields:
      registry             — per-session FIFO queue (who's running)
      raw_buffers          — client_run_id → raw unprocessed text (pre-projection)
      buffers              — client_run_id → projected/merged text (for display)
      delta_sent_at        — client_run_id → last-sent timestamp (150ms debounce)
      delta_last_broadcast_len — client_run_id → last broadcast length (avoid dup flush)
      aborted_runs         — client_run_id → abort timestamp
    """

    registry: ChatRunRegistry = field(default_factory=ChatRunRegistry)
    raw_buffers: dict[str, str] = field(default_factory=dict)
    buffers: dict[str, str] = field(default_factory=dict)
    delta_sent_at: dict[str, float] = field(default_factory=dict)
    delta_last_broadcast_len: dict[str, int] = field(default_factory=dict)
    aborted_runs: dict[str, float] = field(default_factory=dict)

    def clear(self) -> None:
        """Clear all state (gateway reset)."""
        self.registry.clear()
        self.raw_buffers.clear()
        self.buffers.clear()
        self.delta_sent_at.clear()
        self.delta_last_broadcast_len.clear()
        self.aborted_runs.clear()

    # ------------------------------------------------------------------
    # Delta debounce helpers
    # ------------------------------------------------------------------

    def should_send_delta(self, client_run_id: str) -> bool:
        """True if enough time has elapsed since last delta send (150ms throttle)."""
        last = self.delta_sent_at.get(client_run_id, 0.0)
        return (time.monotonic() - last) * 1000 >= DELTA_DEBOUNCE_MS

    def mark_delta_sent(self, client_run_id: str, length: int) -> None:
        """Record that a delta was sent for this run."""
        self.delta_sent_at[client_run_id] = time.monotonic()
        self.delta_last_broadcast_len[client_run_id] = length

    def has_new_content(self, client_run_id: str) -> bool:
        """True if buffers[client_run_id] has grown since last broadcast."""
        current = len(self.buffers.get(client_run_id, ""))
        last = self.delta_last_broadcast_len.get(client_run_id, 0)
        return current > last

    # ------------------------------------------------------------------
    # Abort helpers
    # ------------------------------------------------------------------

    def mark_aborted(self, client_run_id: str) -> None:
        """Mark a run as aborted with the current timestamp."""
        self.aborted_runs[client_run_id] = time.monotonic()

    def is_aborted(self, client_run_id: str) -> bool:
        """True if this run has been aborted."""
        return client_run_id in self.aborted_runs

    # ------------------------------------------------------------------
    # Buffer helpers
    # ------------------------------------------------------------------

    def append_raw(self, client_run_id: str, text: str) -> None:
        """Append text to the raw (pre-projection) buffer."""
        self.raw_buffers[client_run_id] = self.raw_buffers.get(client_run_id, "") + text

    def append_projected(self, client_run_id: str, text: str) -> None:
        """Append projected text to the display buffer."""
        self.buffers[client_run_id] = self.buffers.get(client_run_id, "") + text

    def cleanup_run(self, client_run_id: str) -> None:
        """Remove all state for a completed/aborted run."""
        self.raw_buffers.pop(client_run_id, None)
        self.buffers.pop(client_run_id, None)
        self.delta_sent_at.pop(client_run_id, None)
        self.delta_last_broadcast_len.pop(client_run_id, None)
        # Keep aborted_runs briefly for idempotent abort checks


def create_chat_run_state() -> ChatRunState:
    """Create a fresh ChatRunState instance."""
    return ChatRunState()


# ---------------------------------------------------------------------------
# Backward-compat module-level helpers (used by existing tests / callers)
# ---------------------------------------------------------------------------

def should_send_delta(state: ChatRunState, client_run_id: str) -> bool:
    """Module-level alias for state.should_send_delta()."""
    return state.should_send_delta(client_run_id)


def mark_delta_sent(state: ChatRunState, client_run_id: str, length: int) -> None:
    """Module-level alias for state.mark_delta_sent()."""
    state.mark_delta_sent(client_run_id, length)


def append_to_buffer(state: ChatRunState, client_run_id: str, text: str) -> None:
    """Module-level alias for state.append_projected()."""
    state.append_projected(client_run_id, text)
