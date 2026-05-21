"""Delivery context helpers — mirrors TypeScript src/utils/delivery-context.shared.ts."""
from __future__ import annotations

from typing import Any, TypedDict

from openclaw.routing.session_key import normalize_account_id
from openclaw.shared.string_coerce import normalize_optional_string
from openclaw.utils.message_channel import normalize_message_channel


class DeliveryContext(TypedDict, total=False):
    channel: str
    to: str
    accountId: str
    threadId: str | int


class DeliveryContextSessionSource(TypedDict, total=False):
    channel: str
    lastChannel: str
    lastTo: str
    lastAccountId: str
    lastThreadId: str | int
    deliveryContext: DeliveryContext | dict[str, Any]
    origin: dict[str, Any]


def normalize_delivery_context(context: DeliveryContext | dict[str, Any] | None) -> DeliveryContext | None:
    if not context:
        return None
    channel_raw = context.get("channel")
    channel = (
        normalize_message_channel(channel_raw) or channel_raw.strip()
        if isinstance(channel_raw, str) and channel_raw.strip()
        else None
    )
    to = normalize_optional_string(context.get("to"))
    account_id = normalize_account_id(context.get("accountId"))
    thread_raw = context.get("threadId")
    thread_id: str | int | None
    if isinstance(thread_raw, int) and thread_raw == thread_raw:  # finite int
        thread_id = int(thread_raw)
    elif isinstance(thread_raw, str):
        thread_id = normalize_optional_string(thread_raw)
    else:
        thread_id = None
    if isinstance(thread_id, str) and not thread_id:
        thread_id = None
    if not channel and not to and not account_id and thread_id is None:
        return None
    normalized: DeliveryContext = {}
    if channel:
        normalized["channel"] = channel
    if to:
        normalized["to"] = to
    if account_id:
        normalized["accountId"] = account_id
    if thread_id is not None:
        normalized["threadId"] = thread_id
    return normalized


def merge_delivery_context(
    primary: DeliveryContext | dict[str, Any] | None,
    fallback: DeliveryContext | dict[str, Any] | None,
) -> DeliveryContext | None:
    normalized_primary = normalize_delivery_context(primary)
    normalized_fallback = normalize_delivery_context(fallback)
    if not normalized_primary and not normalized_fallback:
        return None
    channels_conflict = (
        bool(normalized_primary and normalized_primary.get("channel"))
        and bool(normalized_fallback and normalized_fallback.get("channel"))
        and normalized_primary.get("channel") != normalized_fallback.get("channel")
    )
    return normalize_delivery_context(
        {
            "channel": (normalized_primary or {}).get("channel") or (normalized_fallback or {}).get("channel"),
            "to": (normalized_primary or {}).get("to")
            if channels_conflict
            else (normalized_primary or {}).get("to") or (normalized_fallback or {}).get("to"),
            "accountId": (normalized_primary or {}).get("accountId")
            if channels_conflict
            else (normalized_primary or {}).get("accountId") or (normalized_fallback or {}).get("accountId"),
            "threadId": (normalized_primary or {}).get("threadId")
            if channels_conflict
            else (normalized_primary or {}).get("threadId") or (normalized_fallback or {}).get("threadId"),
        }
    )


def normalize_session_delivery_fields(
    source: DeliveryContextSessionSource | dict[str, Any] | None,
) -> dict[str, Any]:
    if not source:
        return {
            "deliveryContext": None,
            "lastChannel": None,
            "lastTo": None,
            "lastAccountId": None,
            "lastThreadId": None,
        }
    merged = merge_delivery_context(
        normalize_delivery_context(
            {
                "channel": source.get("lastChannel") or source.get("channel"),
                "to": source.get("lastTo"),
                "accountId": source.get("lastAccountId"),
                "threadId": source.get("lastThreadId"),
            }
        ),
        normalize_delivery_context(source.get("deliveryContext")),
    )
    if not merged:
        return {
            "deliveryContext": None,
            "lastChannel": None,
            "lastTo": None,
            "lastAccountId": None,
            "lastThreadId": None,
        }
    return {
        "deliveryContext": merged,
        "lastChannel": merged.get("channel"),
        "lastTo": merged.get("to"),
        "lastAccountId": merged.get("accountId"),
        "lastThreadId": merged.get("threadId"),
    }


def delivery_context_from_session(
    entry: DeliveryContextSessionSource | dict[str, Any] | Any | None,
) -> DeliveryContext | None:
    if not entry:
        return None
    if hasattr(entry, "model_dump"):
        entry_dict = entry.model_dump(exclude_none=True)
    elif isinstance(entry, dict):
        entry_dict = entry
    else:
        entry_dict = {
            "channel": getattr(entry, "channel", None),
            "lastChannel": getattr(entry, "lastChannel", None),
            "lastTo": getattr(entry, "lastTo", None),
            "lastAccountId": getattr(entry, "lastAccountId", None),
            "lastThreadId": getattr(entry, "lastThreadId", None),
            "origin": getattr(entry, "origin", None),
            "deliveryContext": getattr(entry, "deliveryContext", None),
        }
    origin = entry_dict.get("origin") or {}
    if hasattr(origin, "model_dump"):
        origin = origin.model_dump(exclude_none=True)
    source: DeliveryContextSessionSource = {
        "channel": entry_dict.get("channel") or origin.get("provider"),
        "lastChannel": entry_dict.get("lastChannel"),
        "lastTo": entry_dict.get("lastTo"),
        "lastAccountId": entry_dict.get("lastAccountId") or origin.get("accountId"),
        "lastThreadId": entry_dict.get("lastThreadId")
        or (entry_dict.get("deliveryContext") or {}).get("threadId")
        or origin.get("threadId"),
        "origin": origin,
        "deliveryContext": entry_dict.get("deliveryContext"),
    }
    return normalize_session_delivery_fields(source).get("deliveryContext")


def delivery_context_key(context: DeliveryContext | dict[str, Any] | None) -> str | None:
    normalized = normalize_delivery_context(context)
    if not normalized or not normalized.get("channel") or not normalized.get("to"):
        return None
    thread_id = normalized.get("threadId")
    thread_str = "" if thread_id is None or thread_id == "" else str(thread_id)
    return f"{normalized['channel']}|{normalized['to']}|{normalized.get('accountId') or ''}|{thread_str}"
