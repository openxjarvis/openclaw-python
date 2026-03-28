"""Node Event Handler.

Python equivalent of TypeScript src/gateway/server-node-events.ts.

Handles events received from connected nodes (clients like mobile apps, desktop apps)
and routes them to the appropriate gateway handlers.

Key event types handled:
  - agent.request      — Run agent prompt triggered by a node
  - chat.subscribe     — Node subscribes to a session's chat events
  - chat.unsubscribe   — Node unsubscribes from a session
  - voice.transcript   — Voice-to-text transcript from node
  - exec.started       — Command execution started on node
  - exec.finished      — Command execution finished on node
  - exec.denied        — Command execution denied
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger(__name__)


class NodeEventHandler:
    """
    Routes inbound node events to the appropriate gateway handlers.

    Mirrors TypeScript createNodeEventHandlers() in server-node-events.ts.

    The handler processes events sent by nodes (e.g., mobile/desktop clients)
    via the 'node.event' gateway method. These are distinct from channel events
    (messages from Telegram, Discord, etc.).
    """

    def __init__(
        self,
        node_registry: Any | None = None,
        subscription_manager: Any | None = None,
        session_manager: Any | None = None,
        agent_runtime: Any | None = None,
    ) -> None:
        """
        Args:
            node_registry: NodeRegistry instance for looking up connected nodes
            subscription_manager: NodeSubscriptionManager for session subscriptions
            session_manager: Session manager for creating/retrieving sessions
            agent_runtime: Agent runtime for executing agent prompts
        """
        self._node_registry = node_registry
        self._subscription_manager = subscription_manager
        self._session_manager = session_manager
        self._agent_runtime = agent_runtime

        # Voice dedup: track last transcript per node to avoid duplicate firing
        # Structure: nodeId → {"text": str, "last_seen_at": float}
        self._voice_dedup: dict[str, dict[str, Any]] = {}

        # Registered event type handlers
        self._handlers: dict[str, Callable] = {
            "agent.request": self._handle_agent_request,
            "chat.subscribe": self._handle_chat_subscribe,
            "chat.unsubscribe": self._handle_chat_unsubscribe,
            "voice.transcript": self._handle_voice_transcript,
            "exec.started": self._handle_exec_started,
            "exec.finished": self._handle_exec_finished,
            "exec.denied": self._handle_exec_denied,
        }

    # =========================================================================
    # Main dispatch
    # =========================================================================

    async def handle_node_event(
        self,
        node_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Dispatch a node event to the appropriate handler.

        Called by the 'node.event' gateway method handler.

        Args:
            node_id: The sending node's identifier
            event_type: Event type string (e.g. 'agent.request')
            payload: Event payload dict

        Returns:
            Optional response dict, or None
        """
        handler = self._handlers.get(event_type)
        if handler is None:
            logger.debug(f"Unknown node event type: {event_type!r} from node {node_id!r}")
            return None

        try:
            return await handler(node_id, payload)
        except Exception as exc:
            logger.error(f"Node event handler for {event_type!r} failed: {exc}", exc_info=True)
            return {"ok": False, "error": str(exc)}

    def register_event_handler(
        self,
        event_type: str,
        handler: Callable,
    ) -> None:
        """Register a custom event handler for a given event type.

        Allows extending the built-in event handlers (e.g. for plugin-registered events).

        Args:
            event_type: Event type string to handle
            handler: Async callable (node_id, payload) -> dict | None
        """
        self._handlers[event_type] = handler
        logger.debug(f"Registered node event handler for {event_type!r}")

    # =========================================================================
    # Event Handlers
    # =========================================================================

    async def _handle_agent_request(
        self,
        node_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Handle agent.request — run agent prompt triggered by a node.

        Mirrors TS handleAgentRequest() in server-node-events.ts.
        """
        message = payload.get("message") or payload.get("text", "")
        session_key = payload.get("sessionKey") or payload.get("session_key", "")
        model = payload.get("model")
        images = payload.get("images")

        if not message:
            return {"ok": False, "error": "message is required"}
        if not session_key:
            return {"ok": False, "error": "sessionKey is required"}

        if self._agent_runtime is None:
            return {"ok": False, "error": "agent runtime not available"}

        logger.info(f"Node {node_id!r} triggered agent.request for session {session_key!r}")

        try:
            session = None
            if self._session_manager is not None:
                session = self._session_manager.get_session(session_key)
            if session is None:
                # Create a minimal session proxy if session manager unavailable
                session = _MinimalSession(session_key)

            # Run the agent turn (non-blocking, fire-and-forget)
            import asyncio
            asyncio.create_task(
                self._run_agent_and_notify(node_id, session, message, model, images)
            )

            return {"ok": True, "queued": True, "session_key": session_key}
        except Exception as exc:
            logger.error(f"agent.request failed: {exc}", exc_info=True)
            return {"ok": False, "error": str(exc)}

    async def _run_agent_and_notify(
        self,
        node_id: str,
        session: Any,
        message: str,
        model: str | None,
        images: list | None,
    ) -> None:
        """Run agent turn and send events back to the requesting node.
        
        Note: Events are now automatically forwarded to subscribed nodes via
        Gateway._handle_infra_agent_event_async, so we don't need to manually
        send them here. We just run the agent turn.
        """
        session_key = getattr(session, "session_id", "") or ""
        try:
            async for event in self._agent_runtime.run_turn(
                session, message, model=model, images=images
            ):
                # Events are automatically forwarded via infra/agent_events subscription
                # No need to manually send here - this was causing duplicate events
                pass
        except Exception as exc:
            logger.error(f"Agent run for node {node_id!r} failed: {exc}", exc_info=True)

    async def _handle_chat_subscribe(
        self,
        node_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Handle chat.subscribe — node subscribes to a session's events.

        Mirrors TS handleChatSubscribe() in server-node-events.ts.
        """
        session_key = payload.get("sessionKey") or payload.get("session_key", "")
        if not session_key:
            return {"ok": False, "error": "sessionKey is required"}

        if self._subscription_manager is not None:
            self._subscription_manager.subscribe(node_id, session_key)
            logger.info(f"Node {node_id!r} subscribed to chat session {session_key!r}")
            return {"ok": True, "session_key": session_key}

        return {"ok": False, "error": "subscription manager not available"}

    async def _handle_chat_unsubscribe(
        self,
        node_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Handle chat.unsubscribe — node unsubscribes from a session."""
        session_key = payload.get("sessionKey") or payload.get("session_key", "")

        if self._subscription_manager is not None:
            if session_key:
                self._subscription_manager.unsubscribe(node_id, session_key)
            else:
                self._subscription_manager.unsubscribe_all(node_id)
            return {"ok": True}

        return {"ok": False, "error": "subscription manager not available"}

    async def _handle_voice_transcript(
        self,
        node_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Handle voice.transcript — deduplicate and optionally trigger agent.

        Mirrors TS voice transcript dedup logic in server-node-events.ts.
        """
        transcript_text = payload.get("text", "").strip()
        session_key = payload.get("sessionKey") or payload.get("session_key", "")
        is_final = bool(payload.get("isFinal", payload.get("is_final", False)))

        if not transcript_text:
            return None

        # Deduplicate: skip if same text as last seen for this node
        last = self._voice_dedup.get(node_id)
        if last and last.get("text") == transcript_text and not is_final:
            return None

        self._voice_dedup[node_id] = {"text": transcript_text, "last_seen_at": time.time()}

        if is_final and session_key and self._subscription_manager is not None:
            await self._subscription_manager.send_to_session(
                session_key,
                "voice.transcript",
                {"text": transcript_text, "node_id": node_id, "is_final": True},
            )

        return {"ok": True}

    async def _handle_exec_started(
        self,
        node_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Handle exec.started — command execution started on node."""
        exec_id = payload.get("execId") or payload.get("exec_id", "")
        command = payload.get("command", "")
        session_key = payload.get("sessionKey") or payload.get("session_key", "")

        logger.info(f"Node {node_id!r} exec started: {exec_id!r} cmd={command!r}")

        if session_key and self._subscription_manager is not None:
            await self._subscription_manager.send_to_session(
                session_key,
                "exec.started",
                {"exec_id": exec_id, "command": command, "node_id": node_id},
            )

        return {"ok": True}

    async def _handle_exec_finished(
        self,
        node_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Handle exec.finished — command execution finished on node."""
        exec_id = payload.get("execId") or payload.get("exec_id", "")
        exit_code = payload.get("exitCode", payload.get("exit_code", 0))
        output = payload.get("output", "")
        session_key = payload.get("sessionKey") or payload.get("session_key", "")

        logger.info(f"Node {node_id!r} exec finished: {exec_id!r} exit_code={exit_code}")

        if session_key and self._subscription_manager is not None:
            await self._subscription_manager.send_to_session(
                session_key,
                "exec.finished",
                {
                    "exec_id": exec_id,
                    "exit_code": exit_code,
                    "output": output,
                    "node_id": node_id,
                },
            )

        return {"ok": True}

    async def _handle_exec_denied(
        self,
        node_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Handle exec.denied — command execution denied by node."""
        exec_id = payload.get("execId") or payload.get("exec_id", "")
        reason = payload.get("reason", "denied")
        session_key = payload.get("sessionKey") or payload.get("session_key", "")

        logger.info(f"Node {node_id!r} exec denied: {exec_id!r} reason={reason!r}")

        if session_key and self._subscription_manager is not None:
            await self._subscription_manager.send_to_session(
                session_key,
                "exec.denied",
                {"exec_id": exec_id, "reason": reason, "node_id": node_id},
            )

        return {"ok": True}

    # =========================================================================
    # Node disconnect cleanup
    # =========================================================================

    def on_node_disconnect(self, node_id: str) -> None:
        """Called when a node disconnects — clean up event state."""
        self._voice_dedup.pop(node_id, None)
        if self._subscription_manager is not None:
            self._subscription_manager.unsubscribe_all(node_id)
        logger.debug(f"Cleaned up node event state for {node_id!r}")


class _MinimalSession:
    """Minimal session proxy for when session_manager is not available."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.id = session_id


__all__ = ["NodeEventHandler"]
