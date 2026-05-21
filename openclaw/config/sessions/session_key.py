"""Session key derivation — mirrors TypeScript src/config/sessions/session-key.ts."""
from __future__ import annotations

from typing import Literal

from openclaw.auto_reply.inbound_context import MsgContext
from openclaw.config.sessions.explicit_session_key_normalization import normalize_explicit_session_key
from openclaw.config.sessions.group import resolve_group_session_key
from openclaw.routing.session_key import (
    DEFAULT_AGENT_ID,
    build_agent_main_session_key,
    normalize_main_key,
)

SessionScope = Literal["per-sender", "global"]


def _normalize_e164(phone: str | None) -> str:
    if not phone:
        return ""
    digits = "".join(c for c in phone if c.isdigit() or c == "+")
    if not digits:
        return ""
    if not digits.startswith("+"):
        digits = "+" + digits
    return digits if len(digits) >= 7 else ""


def derive_session_key(scope: SessionScope, ctx: MsgContext) -> str:
    if scope == "global":
        return "global"
    resolved_group = resolve_group_session_key(ctx)
    if resolved_group:
        return resolved_group["key"]
    from_val = getattr(ctx, "From", None)
    normalized_from = _normalize_e164(from_val) if from_val else ""
    return normalized_from or "unknown"


def resolve_session_key(
    scope: SessionScope,
    ctx: MsgContext,
    main_key: str | None = None,
) -> str:
    explicit = (getattr(ctx, "SessionKey", None) or "").strip()
    if explicit:
        return normalize_explicit_session_key(explicit, ctx)
    raw = derive_session_key(scope, ctx)
    if scope == "global":
        return raw
    canonical_main_key = normalize_main_key(main_key)
    canonical = build_agent_main_session_key(
        agent_id=DEFAULT_AGENT_ID,
        main_key=canonical_main_key,
    )
    is_group = ":group:" in raw or ":channel:" in raw
    if not is_group:
        return canonical
    return f"agent:{DEFAULT_AGENT_ID}:{raw}"
