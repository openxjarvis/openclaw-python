"""Session delivery routing — mirrors TypeScript src/auto-reply/reply/session-delivery.ts."""
from __future__ import annotations

from typing import Any, TypedDict

from openclaw.agents.session_entry import SessionEntry
from openclaw.auto_reply.inbound_context import MsgContext
from openclaw.routing.session_key import build_agent_main_session_key, parse_agent_session_key
from openclaw.shared.string_coerce import (
    normalize_lowercase_string_or_empty,
    normalize_optional_lowercase_string,
    normalize_optional_string,
)
from openclaw.utils.delivery_context import (
    delivery_context_from_session,
    delivery_context_key,
    normalize_delivery_context,
)
from openclaw.utils.message_channel import (
    INTERNAL_MESSAGE_CHANNEL,
    is_deliverable_message_channel,
    normalize_message_channel,
)

DIRECT_SESSION_MARKERS = frozenset({"direct", "dm"})
THREAD_SESSION_MARKERS = frozenset({"thread", "topic"})


class LegacyMainDeliveryRetirement(TypedDict):
    key: str
    entry: SessionEntry | dict[str, Any]


def _resolve_session_key_channel_hint(session_key: str | None) -> str | None:
    parsed = parse_agent_session_key(session_key)
    if not parsed or not parsed.rest:
        return None
    head = normalize_optional_lowercase_string(parsed.rest.split(":")[0])
    if not head or head in ("main", "cron", "subagent", "acp"):
        return None
    return normalize_message_channel(head)


def _is_main_session_key(session_key: str | None) -> bool:
    parsed = parse_agent_session_key(session_key)
    if not parsed:
        return normalize_lowercase_string_or_empty(session_key) == "main"
    return normalize_lowercase_string_or_empty(parsed.rest) == "main"


def _has_strict_direct_session_tail(parts: list[str], marker_index: int) -> bool:
    peer_id = normalize_optional_string(parts[marker_index + 1] if marker_index + 1 < len(parts) else None)
    if not peer_id:
        return False
    tail = parts[marker_index + 2 :]
    if not tail:
        return True
    return (
        len(tail) == 2
        and tail[0] in THREAD_SESSION_MARKERS
        and bool(normalize_optional_string(tail[1]))
    )


def _is_direct_session_key(session_key: str | None) -> bool:
    raw = normalize_lowercase_string_or_empty(session_key)
    if not raw:
        return False
    parsed = parse_agent_session_key(raw)
    scoped = parsed.rest if parsed else raw
    parts = [p for p in scoped.split(":") if p]
    if len(parts) < 2:
        return False
    if parts[0] in DIRECT_SESSION_MARKERS:
        return _has_strict_direct_session_tail(parts, 0)
    channel = normalize_message_channel(parts[0])
    if not channel or not is_deliverable_message_channel(channel):
        return False
    if len(parts) > 1 and parts[1] in DIRECT_SESSION_MARKERS:
        return _has_strict_direct_session_tail(parts, 1)
    if len(parts) > 2 and normalize_optional_string(parts[1]) and parts[2] in DIRECT_SESSION_MARKERS:
        return _has_strict_direct_session_tail(parts, 2)
    return False


def _is_external_routing_channel(channel: str | None) -> bool:
    return bool(
        channel
        and channel != INTERNAL_MESSAGE_CHANNEL
        and is_deliverable_message_channel(channel)
    )


def resolve_last_channel_raw(
    *,
    originating_channel_raw: str | None = None,
    persisted_last_channel: str | None = None,
    session_key: str | None = None,
    is_inter_session: bool = False,
) -> str | None:
    originating_channel = normalize_message_channel(originating_channel_raw)
    persisted_channel = normalize_message_channel(persisted_last_channel)
    session_key_channel_hint = _resolve_session_key_channel_hint(session_key)
    has_established_external_route = _is_external_routing_channel(
        persisted_channel
    ) or _is_external_routing_channel(session_key_channel_hint)
    if is_inter_session and has_established_external_route:
        return persisted_channel or session_key_channel_hint
    if (
        originating_channel == INTERNAL_MESSAGE_CHANNEL
        and not has_established_external_route
        and (_is_main_session_key(session_key) or _is_direct_session_key(session_key))
    ):
        return originating_channel_raw
    resolved = originating_channel_raw or persisted_last_channel
    if not _is_external_routing_channel(originating_channel):
        if _is_external_routing_channel(persisted_channel):
            resolved = persisted_channel
        elif _is_external_routing_channel(session_key_channel_hint):
            resolved = session_key_channel_hint
    return resolved


def resolve_last_to_raw(
    *,
    originating_channel_raw: str | None = None,
    originating_to_raw: str | None = None,
    to_raw: str | None = None,
    persisted_last_to: str | None = None,
    persisted_last_channel: str | None = None,
    session_key: str | None = None,
    is_inter_session: bool = False,
) -> str | None:
    originating_channel = normalize_message_channel(originating_channel_raw)
    persisted_channel = normalize_message_channel(persisted_last_channel)
    session_key_channel_hint = _resolve_session_key_channel_hint(session_key)
    has_established_external_route_for_to = _is_external_routing_channel(
        persisted_channel
    ) or _is_external_routing_channel(session_key_channel_hint)
    if is_inter_session and has_established_external_route_for_to and persisted_last_to:
        return persisted_last_to
    if (
        originating_channel == INTERNAL_MESSAGE_CHANNEL
        and not has_established_external_route_for_to
        and (_is_main_session_key(session_key) or _is_direct_session_key(session_key))
    ):
        return originating_to_raw or to_raw
    if not _is_external_routing_channel(originating_channel):
        has_external_fallback = _is_external_routing_channel(
            persisted_channel
        ) or _is_external_routing_channel(session_key_channel_hint)
        if has_external_fallback and persisted_last_to:
            return persisted_last_to
    return originating_to_raw or to_raw or persisted_last_to


def maybe_retire_legacy_main_delivery_route(
    *,
    session_cfg: dict[str, Any] | None,
    session_key: str,
    session_store: dict[str, SessionEntry | dict[str, Any]],
    agent_id: str,
    main_key: str,
    is_group: bool,
    ctx: MsgContext,
) -> LegacyMainDeliveryRetirement | None:
    dm_scope = (session_cfg or {}).get("dmScope", "main")
    if dm_scope == "main" or is_group:
        return None
    canonical_main_session_key = build_agent_main_session_key(agent_id=agent_id, main_key=main_key)
    if session_key == canonical_main_session_key:
        return None
    legacy_main = session_store.get(canonical_main_session_key)
    if not legacy_main:
        return None
    legacy_route_key = delivery_context_key(delivery_context_from_session(legacy_main))
    if not legacy_route_key:
        return None
    active_direct_route_key = delivery_context_key(
        normalize_delivery_context(
            {
                "channel": getattr(ctx, "OriginatingChannel", None),
                "to": getattr(ctx, "OriginatingTo", None) or getattr(ctx, "To", None),
                "accountId": getattr(ctx, "AccountId", None),
                "threadId": getattr(ctx, "MessageThreadId", None),
            }
        )
    )
    if not active_direct_route_key or active_direct_route_key != legacy_route_key:
        return None

    def _field(entry: SessionEntry | dict[str, Any], name: str) -> Any:
        if isinstance(entry, dict):
            return entry.get(name)
        return getattr(entry, name, None)

    if (
        _field(legacy_main, "deliveryContext") is None
        and _field(legacy_main, "lastChannel") is None
        and _field(legacy_main, "lastTo") is None
        and _field(legacy_main, "lastAccountId") is None
        and _field(legacy_main, "lastThreadId") is None
    ):
        return None

    if isinstance(legacy_main, SessionEntry):
        updated = legacy_main.model_copy(
            update={
                "deliveryContext": None,
                "lastChannel": None,
                "lastTo": None,
                "lastAccountId": None,
                "lastThreadId": None,
            }
        )
        entry_out: SessionEntry | dict[str, Any] = updated
    else:
        entry_out = {
            **legacy_main,
            "deliveryContext": None,
            "lastChannel": None,
            "lastTo": None,
            "lastAccountId": None,
            "lastThreadId": None,
        }
    return {"key": canonical_main_session_key, "entry": entry_out}
