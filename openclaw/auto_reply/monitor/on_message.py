"""Message handler factory for channel integrations.

Creates message handlers that integrate routing, group gating, and broadcast.
Mirrors TypeScript src/web/auto-reply/monitor/on-message.ts
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

from openclaw.auto_reply.echo_tracker import EchoTracker
from openclaw.auto_reply.group_gating import apply_group_gating
from openclaw.auto_reply.group_history import GroupHistoryEntry
from openclaw.auto_reply.monitor.process_message import process_message
from openclaw.routing.resolve_route import resolve_agent_route
from openclaw.routing.session_key import build_group_history_key

logger = logging.getLogger(__name__)

# Module-level echo tracker shared across all handlers in the same process.
# Mirrors TS echoTracker singleton in on-message.ts.
_echo_tracker = EchoTracker(window_seconds=30)


# ---------------------------------------------------------------------------
# Peer ID resolution — mirrors TS resolvePeerId(msg) in on-message.ts
# ---------------------------------------------------------------------------

def _resolve_peer_id(msg: dict[str, Any]) -> str:
    """Derive the canonical peer ID from an inbound message.

    Mirrors TS ``resolvePeerId(msg)`` which normalises JIDs, E.164 numbers,
    and plain IDs into a single canonical peer identifier used for routing.

    TS logic (peer.ts):
    - For groups: returns conversationId ?? from (identifies the group itself)
    - For DMs: returns senderE164 or normalizes from field
    
    This peer ID is used for routing and group history keys.
    """
    chat_type = msg.get("chatType", msg.get("chat_type", "dm"))
    
    # For group messages, peer_id is the group identifier (not the sender)
    if chat_type == "group":
        # TS: return msg.conversationId ?? msg.from
        return msg.get("conversationId") or msg.get("groupId") or msg.get("from") or ""
    
    # For DMs, prioritize senderE164, then normalize from field
    sender_e164 = (msg.get("senderE164") or "").strip()
    if sender_e164:
        return sender_e164
    
    from_field = (msg.get("from") or "").strip()
    if from_field:
        # TS normalizes E164 or JID → E164
        # For simplicity, use as-is (matching TS fallback behavior)
        return from_field
    
    return ""



def get_echo_tracker() -> EchoTracker:
    """Return the process-level echo tracker (for channels to register sent text)."""
    return _echo_tracker


def update_last_route_in_background(
    cfg: dict[str, Any],
    session_key: str,
    route: dict[str, Any],
) -> None:
    """Persist the most recently resolved route for a session key.

    Mirrors TS ``updateLastRouteInBackground()`` in on-message.ts.
    Runs fire-and-forget — failures are logged and swallowed.
    """
    if not session_key or not route:
        return

    async def _persist() -> None:
        try:
            from openclaw.config.sessions.paths import resolve_store_path
            from openclaw.config.sessions.store_utils import (
                load_session_store_from_path,
                save_session_store_to_path,
            )
            _sess_cfg = (cfg.get("session", {}) if isinstance(cfg, dict) else {})
            store_path = resolve_store_path(
                _sess_cfg.get("store") if isinstance(_sess_cfg, dict) else None, {}
            )
            if not store_path:
                return
            store = load_session_store_from_path(store_path) or {}
            entry = store.get(session_key.lower()) or store.get(session_key) or {}
            if isinstance(entry, dict):
                entry["lastRoute"] = route
                store[session_key.lower()] = entry
            elif hasattr(entry, "__dict__"):
                entry.lastRoute = route
                store[session_key.lower()] = entry
            save_session_store_to_path(store_path, store)
        except Exception as exc:
            logger.debug("update_last_route_in_background: %s", exc)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_persist())
    except Exception:
        pass


def create_message_handler(
    cfg: dict[str, Any],
    channel: str,
    account_id: str | None = None,
    group_histories: dict[str, list[GroupHistoryEntry]] | None = None,
    group_history_limit: int = 50,
    owner_list: list[str] | None = None,
    process_message_fn: Callable[[dict[str, Any], dict[str, Any], str], Awaitable[bool]] | None = None,
    echo_tracker: EchoTracker | None = None,
    group_member_names: dict[str, dict[str, str]] | None = None,
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Create a message handler for a channel.

    Mirrors TS createWebOnMessageHandler() from src/web/auto-reply/monitor/on-message.ts.

    The returned handler:
    1. Resolves agent route (fresh config for bindings)
    2. Builds group history key (with accountId component)
    3. Same-phone mode logging
    4. Checks echo tracker — skips messages whose text matches a recently-sent reply
    5. Applies group gating (if group message) with groupMemberNames forwarded
    6. Checks for broadcast groups and dispatches if configured
    7. Otherwise processes message normally

    Args:
        group_member_names: Map of groupId -> {participantJid -> displayName}.
            Mirrors TS ``groupMemberNames: Map<string, Map<string, string>>``.
            Forwarded to both ``process_message_for_route`` and ``apply_group_gating``.
    """
    if group_histories is None:
        group_histories = {}
    if group_member_names is None:
        group_member_names = {}
    # Use provided echo_tracker or fall back to the module-level singleton.
    _tracker = echo_tracker or _echo_tracker

    async def handle_message(msg: dict[str, Any]) -> None:
        """Handle inbound message.

        Order mirrors TS createWebOnMessageHandler handler:
          resolvePeerId → resolveAgentRoute → buildGroupHistoryKey →
          same-phone logging → echo check → group/DM branch → broadcast → processForRoute
        """
        # ------------------------------------------------------------------
        # Step 0: Compute conversationId at top level (matches TS line 64).
        # TS: const conversationId = msg.conversationId ?? msg.from
        # This is used throughout the handler for route updates and logging.
        # ------------------------------------------------------------------
        conversation_id = msg.get("conversationId") or msg.get("from") or ""
        
        # ------------------------------------------------------------------
        # Step 1: Reload config per message + resolve peer ID and agent route.
        # TS line 68: cfg: loadConfig() — reloads on every message for dynamic binding updates.
        # TS line 70: accountId: msg.accountId — reads account from message, not closure.
        # ------------------------------------------------------------------
        from openclaw.config.loader import load_config
        fresh_cfg = load_config()
        
        peer_id = _resolve_peer_id(msg)
        chat_type = msg.get("chatType", msg.get("chat_type", "dm"))
        
        # Account ID from message takes priority over factory closure (TS line 70)
        msg_account_id = msg.get("accountId") or msg.get("account_id") or account_id

        try:
            route = resolve_agent_route(
                cfg=fresh_cfg,
                channel=channel,
                peer_id=peer_id,
                account_id=msg_account_id,
            )
        except Exception as exc:
            logger.error("Failed to resolve route: %s", exc)
            return

        # ------------------------------------------------------------------
        # Step 2: Build group history key.
        # TS line 76-84: group → buildGroupHistoryKey({channel, accountId, peerKind, peerId})
        #               DM   → route.sessionKey
        # Python uses build_group_history_key(channel, peer_kind, peer_id, account_id)
        # which produces "{channel}:{accountId}:{peerKind}:{peerId}".
        # ------------------------------------------------------------------
        route_account_id = route.get("accountId") or msg_account_id or ""
        if chat_type == "group":
            group_history_key = build_group_history_key(
                channel=channel,
                peer_kind="group",
                peer_id=peer_id,
                account_id=route_account_id,
            )
        else:
            # DM: use route.sessionKey (mirrors TS line 84)
            group_history_key = route.get("sessionKey") or ""

        # ------------------------------------------------------------------
        # Step 3: Same-phone mode logging — mirrors TS msg.from === msg.to guard.
        # TS uses logVerbose; Python uses logger.debug (no verbose flag in factory).
        # ------------------------------------------------------------------
        msg_from: str = msg.get("from") or ""
        msg_to: str = msg.get("to") or ""
        if msg_from and msg_to and msg_from == msg_to:
            logger.debug(
                "create_message_handler: same-phone message detected from=%s", msg_from[:30]
            )

        # ------------------------------------------------------------------
        # Step 4: Echo check — skip messages whose text matches an outbound reply.
        # Mirrors TS lines 92-96: echoTracker.has(msg.body) + echoTracker.forget(msg.body).
        # This runs AFTER route resolution (matching TS ordering).
        # ------------------------------------------------------------------
        inbound_text: str = (
            msg.get("body")
            or msg.get("text")
            or msg.get("Body")
            or ""
        )
        if inbound_text and _tracker.has_text(inbound_text):
            logger.debug("create_message_handler: skipping echo for text: %s", inbound_text[:60])
            _tracker.forget_text(inbound_text)
            return

        # ------------------------------------------------------------------
        # Step 5: Group vs DM branch.
        # ------------------------------------------------------------------
        if chat_type == "group":
            # Use conversationId computed at top (matches TS line 64)

            # Group lastRoute update BEFORE gating — mirrors TS lines 115-125:
            # updateLastRouteInBackground(...) with full metaCtx, called BEFORE applyGroupGating.
            session_key = route.get("sessionKey", "")
            if session_key:
                update_last_route_in_background(fresh_cfg, session_key, route)

            # Apply group gating, forwarding group_member_names — mirrors TS lines 127-141.
            gating_result = apply_group_gating(
                cfg=fresh_cfg,
                msg=msg,
                conversation_id=conversation_id,
                group_history_key=group_history_key,
                agent_id=route.get("agentId", ""),
                session_key=route.get("sessionKey", ""),
                channel=channel,
                account_id=route_account_id,
                group_histories=group_histories,
                group_history_limit=group_history_limit,
                owner_list=owner_list,
                session_state=None,
                group_member_names=group_member_names,
            )
            
            # TS line 147: params.msg.wasMentioned = mentionGate.effectiveWasMentioned;
            # Set wasMentioned on msg for downstream processors
            if gating_result.get("wasMentioned") is not None:
                msg["wasMentioned"] = gating_result["wasMentioned"]
            
            if not gating_result["shouldProcess"]:
                logger.debug("Message gated out for group %s", conversation_id)
                return
        else:
            # ------------------------------------------------------------------
            # DM: normalise senderE164 — mirrors TS lines 147-149:
            #   if (!msg.senderE164 && peerId && peerId.startsWith("+"))
            #     msg.senderE164 = normalizeE164(peerId) ?? msg.senderE164
            # Only normalize when senderE164 is ABSENT (not when already present).
            # ------------------------------------------------------------------
            sender_e164 = msg.get("senderE164") or msg.get("sender_e164") or ""
            from openclaw.auto_reply.monitor.process_message import _normalize_e164
            if not sender_e164 and peer_id and peer_id.startswith("+"):
                # Fallback: derive senderE164 from peerId when not already set
                normalized_fallback = _normalize_e164(peer_id)
                if normalized_fallback:
                    msg = {**msg, "senderE164": normalized_fallback, "sender_e164": normalized_fallback}

        # ------------------------------------------------------------------
        # Step 6: Broadcast check — mirrors TS maybeBroadcastMessage().
        # If the peer is configured as a broadcast group (2+ agent bindings),
        # dispatch to all agents and skip the single-route path.
        # ------------------------------------------------------------------
        try:
            from openclaw.auto_reply.monitor.broadcast import maybe_broadcast_message
            _fn = process_message_fn if process_message_fn is not None else _default_process_fn
            was_broadcast = await maybe_broadcast_message(
                cfg=fresh_cfg,
                msg=msg,
                peer_id=peer_id,
                route=route,
                group_history_key=group_history_key,
                process_message_fn=_fn,
                group_histories=group_histories,
            )
            if was_broadcast:
                return
        except Exception as bc_exc:
            logger.debug("maybe_broadcast_message error (non-fatal): %s", bc_exc)

        # Step 7: Single-route process (DM lastRoute is updated inside process_message).
        # Forward group_member_names so process_message_for_route can populate ctxPayload.
        await process_message(
            cfg=fresh_cfg,
            msg=msg,
            route=route,
            group_history_key=group_history_key,
            group_histories=group_histories,
            process_message_fn=process_message_fn,
            channel=channel,
            group_member_names=group_member_names,
        )

    return handle_message


async def _default_process_fn(
    msg: dict[str, Any],
    route: dict[str, Any],
    channel: str,
) -> bool:
    """Default process function used when no custom processor is provided."""
    await process_message(
        cfg={},
        msg=msg,
        route=route,
        group_history_key="",
        group_histories={},
        process_message_fn=None,
    )
    return True


__all__ = [
    "create_message_handler",
    "process_message",
    "get_echo_tracker",
    "update_last_route_in_background",
]
