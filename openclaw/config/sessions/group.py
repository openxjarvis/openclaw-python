"""Group session key resolution — mirrors TypeScript src/config/sessions/group.ts."""
from __future__ import annotations

import re
from typing import Any, TypedDict

from openclaw.auto_reply.inbound_context import MsgContext
from openclaw.shared.string_coerce import (
    normalize_lowercase_string_or_empty,
    normalize_optional_lowercase_string,
    normalize_optional_string,
)
from openclaw.utils.message_channel import list_deliverable_message_channels, normalize_message_channel

_GROUP_SURFACES = frozenset([*list_deliverable_message_channels(), "webchat"])


class GroupKeyResolution(TypedDict):
    key: str
    channel: str
    id: str
    chatType: str


def _normalize_group_label(raw: str | None) -> str:
    trimmed = normalize_optional_string(raw) or ""
    if not trimmed:
        return ""
    slug = re.sub(r"[^a-z0-9]+", "-", trimmed.lower()).strip("-")
    return slug


def _shorten_group_id(value: str | None) -> str:
    trimmed = normalize_optional_string(value) or ""
    if not trimmed:
        return ""
    if len(trimmed) <= 14:
        return trimmed
    return f"{trimmed[:6]}...{trimmed[-4:]}"


def build_group_display_name(
    *,
    provider: str | None = None,
    subject: str | None = None,
    group_channel: str | None = None,
    space: str | None = None,
    id: str | None = None,
    key: str,
) -> str:
    provider_key = normalize_optional_lowercase_string(provider) or "group"
    group_channel_val = normalize_optional_string(group_channel)
    space_val = normalize_optional_string(space)
    subject_val = normalize_optional_string(subject)
    if group_channel_val and space_val:
        prefix = "" if group_channel_val.startswith("#") else "#"
        detail = f"{space_val}{prefix}{group_channel_val}"
    else:
        detail = group_channel_val or subject_val or space_val or ""
    fallback_id = normalize_optional_string(id) or key
    raw_label = detail or fallback_id
    token = _normalize_group_label(raw_label) or _normalize_group_label(_shorten_group_id(raw_label))
    if not group_channel_val and token.startswith("#"):
        token = token.lstrip("#")
    if token and not re.match(r"^[@#]", token) and not token.startswith("g-") and "#" not in token:
        token = f"g-{token}"
    return f"{provider_key}:{token}" if token else provider_key


def resolve_group_session_key(ctx: MsgContext) -> GroupKeyResolution | None:
    from_val = normalize_optional_string(getattr(ctx, "From", None)) or ""
    chat_type = normalize_optional_lowercase_string(getattr(ctx, "ChatType", None))
    normalized_chat_type = (
        "channel" if chat_type == "channel" else "group" if chat_type == "group" else None
    )

    legacy_resolution: GroupKeyResolution | None = None
    try:
        from openclaw.channels.plugins import list_channel_plugins  # noqa: PLC0415

        for plugin in list_channel_plugins() or []:
            messaging = getattr(plugin, "messaging", None)
            resolve_legacy = getattr(messaging, "resolve_legacy_group_session_key", None)
            if callable(resolve_legacy):
                resolved = resolve_legacy(ctx)
                if resolved:
                    legacy_resolution = resolved
                    break
    except Exception:
        legacy_resolution = None

    looks_like_group = (
        normalized_chat_type in ("group", "channel")
        or ":group:" in from_val
        or ":channel:" in from_val
        or legacy_resolution is not None
    )
    if not looks_like_group:
        return None

    provider_hint = normalize_optional_lowercase_string(getattr(ctx, "Provider", None))
    parts = [p for p in from_val.split(":") if p]
    head = normalize_lowercase_string_or_empty(parts[0] if parts else None)
    head_is_surface = bool(head and head in _GROUP_SURFACES)

    if not head_is_surface and not provider_hint and legacy_resolution:
        return legacy_resolution

    provider = head if head_is_surface else (provider_hint or (legacy_resolution or {}).get("channel"))
    if not provider:
        return None

    second = normalize_optional_lowercase_string(parts[1] if len(parts) > 1 else None)
    second_is_kind = second in ("group", "channel")
    if second_is_kind:
        kind = second
    elif ":channel:" in from_val or normalized_chat_type == "channel":
        kind = "channel"
    else:
        kind = "group"
    if head_is_surface:
        peer_parts = parts[2:] if second_is_kind else parts[1:]
        peer_id = ":".join(peer_parts)
    else:
        peer_id = from_val
    final_id = normalize_lowercase_string_or_empty(peer_id)
    if not final_id:
        return None
    return {
        "key": f"{provider}:{kind}:{final_id}",
        "channel": provider,
        "id": final_id,
        "chatType": "channel" if kind == "channel" else "group",
    }
