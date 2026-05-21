"""ACP Conversation ID utilities.

Mirrors TypeScript src/acp/conversation-id.ts

Conversation IDs are stable, human-readable identifiers for ACP
conversation threads. They have a specific format so they can be
distinguished from session IDs and other identifiers.
"""
from __future__ import annotations

import re
import secrets
import time

# Format: conv_{timestamp_hex}_{random_hex}
_CONV_ID_RE = re.compile(r"^conv_[0-9a-f]{8}_[0-9a-f]{8}$")

_PREFIX = "conv"


def generate_conversation_id() -> str:
    """Generate a new conversation ID.

    Mirrors TS generateConversationId().
    Format: conv_{8-char-timestamp}_{8-char-random}
    """
    ts_hex = format(int(time.time()), "08x")
    rand_hex = secrets.token_hex(4)
    return f"{_PREFIX}_{ts_hex}_{rand_hex}"


def parse_conversation_id(raw: str) -> dict | None:
    """Parse a conversation ID into its components.

    Mirrors TS parseConversationId().
    Returns None if not a valid conversation ID.
    """
    if not raw or not raw.startswith(f"{_PREFIX}_"):
        return None

    parts = raw.split("_")
    if len(parts) != 3:
        return None

    try:
        ts_seconds = int(parts[1], 16)
        random_part = parts[2]
        return {
            "id": raw,
            "prefix": _PREFIX,
            "timestamp_seconds": ts_seconds,
            "random": random_part,
        }
    except (ValueError, IndexError):
        return None


def is_valid_conversation_id(raw: str) -> bool:
    """Return True if raw is a valid conversation ID.

    Mirrors TS isValidConversationId().
    """
    if not raw:
        return False
    return bool(_CONV_ID_RE.match(raw))
