"""Message channel normalization utilities."""
from __future__ import annotations

INTERNAL_MESSAGE_CHANNEL = "internal"

_CHANNEL_ALIASES: dict[str, str] = {
    "tg": "telegram",
    "slack": "slack",
    "discord": "discord",
    "matrix": "matrix",
    "openai": "openai",
    "internal": INTERNAL_MESSAGE_CHANNEL,
}

# Bundled deliverable channels (mirrors TS listDeliverableMessageChannels baseline).
_DELIVERABLE_MESSAGE_CHANNELS: frozenset[str] = frozenset(
    {
        "telegram",
        "discord",
        "slack",
        "signal",
        "whatsapp",
        "matrix",
        "imessage",
        "feishu",
        "lark",
        "googlechat",
        "line",
        "msteams",
        "nostr",
        "zalo",
    }
)


def normalize_message_channel(channel: str | None) -> str | None:
    """Normalize a channel identifier string to its canonical form."""
    if not channel:
        return channel
    lower = channel.strip().lower()
    return _CHANNEL_ALIASES.get(lower, lower)


def list_deliverable_message_channels() -> list[str]:
    """Return known deliverable message channel ids."""
    return sorted(_DELIVERABLE_MESSAGE_CHANNELS)


def is_deliverable_message_channel(value: str) -> bool:
    """Return True when *value* is a deliverable external message channel."""
    normalized = normalize_message_channel(value)
    return bool(normalized and normalized in _DELIVERABLE_MESSAGE_CHANNELS)


def is_internal_channel(channel: str | None) -> bool:
    """Return True if the channel is the internal (non-routable) channel."""
    return normalize_message_channel(channel) == INTERNAL_MESSAGE_CHANNEL
