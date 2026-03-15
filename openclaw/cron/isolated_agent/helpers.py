"""Helper utilities for cron isolated agent payload processing.

Mirrors TypeScript: openclaw/src/cron/isolated-agent/helpers.ts
"""
from __future__ import annotations

from typing import Any

DEFAULT_HEARTBEAT_ACK_MAX_CHARS = 300

HEARTBEAT_OK_TOKEN = "HEARTBEAT_OK"


def _truncate_utf8_safe(text: str, limit: int) -> str:
    """Truncate text to at most `limit` characters (code-point safe)."""
    if len(text) <= limit:
        return text
    return text[:limit]


def pick_summary_from_output(text: str | None) -> str | None:
    """Extract best summary text from agent output.

    Mirrors TS pickSummaryFromOutput.
    """
    clean = (text or "").strip()
    if not clean:
        return None
    limit = 2000
    if len(clean) > limit:
        return f"{_truncate_utf8_safe(clean, limit)}\u2026"
    return clean


def pick_summary_from_payloads(
    payloads: list[dict[str, Any]],
) -> str | None:
    """Pick last non-empty summary from a list of payloads.

    Mirrors TS pickSummaryFromPayloads.
    """
    for payload in reversed(payloads):
        summary = pick_summary_from_output(payload.get("text"))
        if summary:
            return summary
    return None


def pick_last_non_empty_text_from_payloads(
    payloads: list[dict[str, Any]],
) -> str | None:
    """Return the last non-empty text from a list of payloads.

    Mirrors TS pickLastNonEmptyTextFromPayloads.
    """
    for payload in reversed(payloads):
        clean = (payload.get("text") or "").strip()
        if clean:
            return clean
    return None


def pick_last_deliverable_payload(
    payloads: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the last payload that has text, media, or channel data.

    Mirrors TS pickLastDeliverablePayload.
    """
    for payload in reversed(payloads):
        text = (payload.get("text") or "").strip()
        has_media = bool(payload.get("mediaUrl")) or bool(payload.get("mediaUrls"))
        has_channel_data = bool(payload.get("channelData"))
        if text or has_media or has_channel_data:
            return payload
    return None


def _strip_heartbeat_token(text: str | None, max_ack_chars: int) -> bool:
    """Return True if the text is only a heartbeat ack (should be skipped).

    Simplified version of TS stripHeartbeatToken logic:
    - If the stripped text equals HEARTBEAT_OK, it is a heartbeat ack.
    - If the text is shorter than max_ack_chars and contains only HEARTBEAT_OK, skip.
    """
    if not text:
        return True
    stripped = text.strip()
    if not stripped:
        return True
    if stripped.upper() == HEARTBEAT_OK_TOKEN:
        return True
    if len(stripped) <= max_ack_chars and HEARTBEAT_OK_TOKEN in stripped.upper():
        remainder = stripped.upper().replace(HEARTBEAT_OK_TOKEN, "").strip()
        if not remainder:
            return True
    return False


def is_heartbeat_only_response(
    payloads: list[dict[str, Any]],
    ack_max_chars: int,
) -> bool:
    """Check if all payloads are just heartbeat ack responses.

    Returns True if delivery should be skipped because there is no real content.

    Mirrors TS isHeartbeatOnlyResponse.
    """
    if not payloads:
        return True
    for payload in payloads:
        has_media = (
            bool(payload.get("mediaUrls")) or bool(payload.get("mediaUrl"))
        )
        if has_media:
            return False
        if not _strip_heartbeat_token(payload.get("text"), ack_max_chars):
            return False
    return True


def resolve_heartbeat_ack_max_chars(
    agent_cfg: dict[str, Any] | None = None,
) -> int:
    """Resolve the heartbeat ack max-chars setting.

    Mirrors TS resolveHeartbeatAckMaxChars.
    """
    if not agent_cfg or not isinstance(agent_cfg, dict):
        return DEFAULT_HEARTBEAT_ACK_MAX_CHARS
    
    heartbeat_cfg = agent_cfg.get("heartbeat")
    if not isinstance(heartbeat_cfg, dict):
        return DEFAULT_HEARTBEAT_ACK_MAX_CHARS
    
    raw = heartbeat_cfg.get("ackMaxChars", DEFAULT_HEARTBEAT_ACK_MAX_CHARS)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_HEARTBEAT_ACK_MAX_CHARS
