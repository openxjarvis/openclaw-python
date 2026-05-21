"""Explicit session key normalization — mirrors TS explicit-session-key-normalization.ts."""
from __future__ import annotations

from openclaw.auto_reply.inbound_context import MsgContext
from openclaw.shared.string_coerce import (
    normalize_lowercase_string_or_empty,
    normalize_optional_lowercase_string,
)
from openclaw.utils.message_channel import normalize_message_channel


def _resolve_explicit_session_key_normalizer_candidates(
    session_key: str,
    ctx: MsgContext,
) -> list[str]:
    normalized_provider = normalize_optional_lowercase_string(getattr(ctx, "Provider", None))
    normalized_surface = normalize_optional_lowercase_string(getattr(ctx, "Surface", None))
    normalized_from = normalize_lowercase_string_or_empty(getattr(ctx, "From", None))
    candidates: set[str] = set()

    def maybe_add(value: str | None) -> None:
        normalized = normalize_message_channel(value)
        if normalized:
            candidates.add(normalized)

    maybe_add(normalized_surface)
    maybe_add(normalized_provider)
    from_head = normalized_from.split(":", 1)[0] if normalized_from else ""
    maybe_add(from_head or None)
    try:
        from openclaw.channels.plugins import list_channel_plugins  # noqa: PLC0415

        for plugin in list_channel_plugins() or []:
            plugin_id = normalize_message_channel(getattr(plugin, "id", None))
            if not plugin_id:
                continue
            if session_key.startswith(f"{plugin_id}:") or f":{plugin_id}:" in session_key:
                candidates.add(plugin_id)
    except Exception:
        pass
    return list(candidates)


def normalize_explicit_session_key(session_key: str, ctx: MsgContext) -> str:
    normalized = normalize_lowercase_string_or_empty(session_key)
    for channel_id in _resolve_explicit_session_key_normalizer_candidates(normalized, ctx):
        try:
            from openclaw.channels.plugins import get_channel_plugin  # noqa: PLC0415

            plugin = get_channel_plugin(channel_id)
            messaging = getattr(plugin, "messaging", None) if plugin else None
            normalize_fn = getattr(messaging, "normalize_explicit_session_key", None)
            if callable(normalize_fn):
                next_key = normalize_fn(session_key=normalized, ctx=ctx)
                if isinstance(next_key, str) and next_key.strip():
                    return normalize_lowercase_string_or_empty(next_key)
        except Exception:
            continue
    return normalized
