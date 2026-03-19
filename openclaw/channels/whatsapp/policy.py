"""WhatsApp access control policies.

Implements DM and group gating, pairing store, and mention checking.
Mirrors TypeScript: src/web/inbound/access-control.ts and src/web/auto-reply/monitor/group-gating.ts
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from ...config.paths import resolve_state_dir

if TYPE_CHECKING:
    from .config import ResolvedWhatsAppAccount

logger = logging.getLogger(__name__)

# History grace period: don't send pairing replies to messages older than 30s
_PAIRING_HISTORY_GRACE_MS = 30_000


# ---------------------------------------------------------------------------
# Pairing store (persistent)
# ---------------------------------------------------------------------------

def _pairing_store_path(account_id: str) -> Path:
    return resolve_state_dir() / "pairing" / f"{account_id}.json"


def _load_pairing_store(account_id: str) -> dict[str, str]:
    """Load pairing store: maps E.164 → approval timestamp ISO string."""
    p = _pairing_store_path(account_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_pairing_store(account_id: str, store: dict[str, str]) -> None:
    p = _pairing_store_path(account_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(store))
    except Exception as e:
        logger.warning("[whatsapp] Failed to save pairing store: %s", e)


def is_pairing_approved(account_id: str, e164: str) -> bool:
    """Return True if this E.164 number has been approved in the pairing store."""
    store = _load_pairing_store(account_id)
    return e164 in store


def approve_pairing(account_id: str, e164: str) -> None:
    """Approve a pairing request from the given E.164 number."""
    store = _load_pairing_store(account_id)
    store[e164] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save_pairing_store(account_id, store)


# ---------------------------------------------------------------------------
# Allowlist matching
# ---------------------------------------------------------------------------

def _normalize_e164(phone: str) -> str:
    """Normalize to digits-only (no +)."""
    return re.sub(r"\D", "", phone)


def _allowlist_match(candidate: str, allowlist: list[str]) -> bool:
    """Check if candidate matches any entry in the allowlist.

    Supports:
    - Exact E.164 match
    - Wildcard "*"
    """
    if not allowlist:
        return False
    if "*" in allowlist:
        return True
    candidate_digits = _normalize_e164(candidate)
    for entry in allowlist:
        if entry == "*":
            return True
        if _normalize_e164(entry) == candidate_digits:
            return True
    return False


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class DmPolicyResult:
    allowed: bool
    should_mark_read: bool
    is_self_chat: bool
    send_pairing_reply: bool = False


# ---------------------------------------------------------------------------
# DM policy
# ---------------------------------------------------------------------------

def check_dm_policy(
    account: ResolvedWhatsAppAccount,
    sender_e164: str | None,
    is_from_me: bool,
    message_timestamp_ms: int | None = None,
    connected_at_ms: int | None = None,
) -> DmPolicyResult:
    """
    Check if an inbound DM is allowed based on account's dmPolicy.
    Mirrors checkInboundAccessControl for DMs in access-control.ts.
    """
    policy = account.dm_policy

    # Detect self-chat mode: bot's own phone is in allowFrom, or selfChatMode flag
    is_self_chat = account.self_chat_mode

    if is_from_me and not is_self_chat:
        return DmPolicyResult(allowed=False, should_mark_read=False, is_self_chat=False)

    if policy == "disabled":
        return DmPolicyResult(allowed=False, should_mark_read=False, is_self_chat=is_self_chat)

    if policy == "open":
        # open requires allowFrom: ["*"]
        if "*" not in account.allow_from:
            logger.warning(
                "[whatsapp] dmPolicy=open but allowFrom doesn't contain '*' for account %s",
                account.account_id,
            )
            return DmPolicyResult(allowed=False, should_mark_read=False, is_self_chat=is_self_chat)
        return DmPolicyResult(allowed=True, should_mark_read=True, is_self_chat=is_self_chat)

    if policy == "allowlist":
        if not sender_e164:
            return DmPolicyResult(allowed=False, should_mark_read=False, is_self_chat=is_self_chat)
        if _allowlist_match(sender_e164, account.allow_from):
            return DmPolicyResult(allowed=True, should_mark_read=True, is_self_chat=is_self_chat)
        return DmPolicyResult(allowed=False, should_mark_read=False, is_self_chat=is_self_chat)

    # policy == "pairing" (default)
    if not sender_e164:
        return DmPolicyResult(allowed=False, should_mark_read=False, is_self_chat=is_self_chat)

    # Check allowlist first (dynamic approvals)
    if _allowlist_match(sender_e164, account.allow_from):
        return DmPolicyResult(allowed=True, should_mark_read=True, is_self_chat=is_self_chat)

    # Check pairing store
    if is_pairing_approved(account.account_id, sender_e164):
        return DmPolicyResult(allowed=True, should_mark_read=True, is_self_chat=is_self_chat)

    # Possibly send pairing reply — but only for "recent" messages, not history catch-up
    send_pairing = True
    if message_timestamp_ms is not None and connected_at_ms is not None:
        age_ms = connected_at_ms - message_timestamp_ms
        if age_ms > _PAIRING_HISTORY_GRACE_MS:
            send_pairing = False

    return DmPolicyResult(
        allowed=False,
        should_mark_read=False,
        is_self_chat=is_self_chat,
        send_pairing_reply=send_pairing,
    )


# ---------------------------------------------------------------------------
# Group policy
# ---------------------------------------------------------------------------

def check_group_sender_allowed(
    account: ResolvedWhatsAppAccount,
    sender_e164: str | None,
) -> bool:
    """Check if a group sender is in the allowed list."""
    policy = account.group_policy

    if policy == "disabled":
        return False

    # Effective group allowlist (falls back to DM allowFrom)
    effective_list = account.group_allow_from or account.allow_from

    if policy == "open":
        if "*" in effective_list or not effective_list:
            return True
        if not sender_e164:
            return False
        return _allowlist_match(sender_e164, effective_list)

    # allowlist (default)
    if not sender_e164:
        return False
    return _allowlist_match(sender_e164, effective_list)


def apply_group_gating(
    account: ResolvedWhatsAppAccount,
    group_jid: str,
    sender_e164: str | None,
    mentioned_jids: list[str] | None,
    bot_jid: str | None,
    is_reply_to_bot: bool = False,
) -> bool:
    """
    Apply group gating logic: sender allowlist + require_mention check.
    Mirrors applyGroupGating in group-gating.ts.

    Returns True if the message should be processed.
    """
    if account.group_policy == "disabled":
        return False

    # Sender must be in allowlist
    if not check_group_sender_allowed(account, sender_e164):
        return False

    # Per-group config overrides
    group_cfg = account.groups.get(group_jid)
    require_mention: bool
    if group_cfg is not None and group_cfg.require_mention is not None:
        require_mention = group_cfg.require_mention
    else:
        require_mention = True  # default: require mention in groups

    if not require_mention:
        return True

    # Check if bot is mentioned
    if bot_jid and mentioned_jids:
        bot_bare = bot_jid.split("@")[0]
        for jid in mentioned_jids:
            if jid.split("@")[0] == bot_bare:
                return True

    # Implicit mention: reply to a bot message counts
    if is_reply_to_bot:
        return True

    return False
