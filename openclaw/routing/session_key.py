"""
Session key utilities

Matches TypeScript src/routing/session-key.ts

Session keys uniquely identify conversation contexts:
  - agent:main:main (default main session)
  - agent:main:direct:user123 (DM with user123)
  - agent:main:telegram:group:456 (Telegram group 456)
  - agent:main:discord:channel:789 (Discord channel 789)
"""
from __future__ import annotations

import re
from typing import NamedTuple

# Constants (matches TS lines 10-12)
DEFAULT_AGENT_ID = "main"
DEFAULT_MAIN_KEY = "main"
DEFAULT_ACCOUNT_ID = "default"

# Pre-compiled regex (matches TS lines 14-18)
VALID_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.IGNORECASE)
INVALID_CHARS_RE = re.compile(r"[^a-z0-9_-]+")
LEADING_DASH_RE = re.compile(r"^-+")
TRAILING_DASH_RE = re.compile(r"-+$")


class ParsedAgentSessionKey(NamedTuple):
    """Parsed session key components."""
    agent_id: str
    rest: str
    full_key: str


def normalize_main_key(value: str | None) -> str:
    """Normalize main key (matches TS normalizeMainKey)."""
    trimmed = (value or "").strip()
    return trimmed.lower() if trimmed else DEFAULT_MAIN_KEY


def normalize_agent_id(value: str | None) -> str:
    """
    Normalize agent ID (matches TS normalizeAgentId lines 61-78).
    
    Rules:
    - Trim and lowercase
    - Must match /^[a-z0-9][a-z0-9_-]{0,63}$/i
    - If invalid: collapse invalid chars to "-", max 64 chars
    - Empty → DEFAULT_AGENT_ID
    
    Examples:
        "Main" → "main"
        "my Agent!" → "my-agent"
        "" → "main"
    """
    trimmed = (value or "").strip()
    if not trimmed:
        return DEFAULT_AGENT_ID
    
    # Already valid
    if VALID_ID_RE.match(trimmed):
        return trimmed.lower()
    
    # Best-effort fallback: collapse invalid characters to "-"
    normalized = INVALID_CHARS_RE.sub("-", trimmed.lower())
    normalized = LEADING_DASH_RE.sub("", normalized)
    normalized = TRAILING_DASH_RE.sub("", normalized)
    normalized = normalized[:64]
    
    return normalized if normalized else DEFAULT_AGENT_ID


def sanitize_agent_id(value: str | None) -> str:
    """
    Sanitize agent ID (matches TS sanitizeAgentId lines 81-96).
    
    Same rules as normalize_agent_id (in TS, they're identical).
    """
    return normalize_agent_id(value)


def normalize_account_id(value: str | None) -> str:
    """
    Normalize account ID (matches TS normalizeAccountId lines 99-114).
    
    Same validation as agent ID.
    
    Examples:
        "Account 1" → "account-1"
        "" → "default"
    """
    trimmed = (value or "").strip()
    if not trimmed:
        return DEFAULT_ACCOUNT_ID
    
    if VALID_ID_RE.match(trimmed):
        return trimmed.lower()
    
    normalized = INVALID_CHARS_RE.sub("-", trimmed.lower())
    normalized = LEADING_DASH_RE.sub("", normalized)
    normalized = TRAILING_DASH_RE.sub("", normalized)
    normalized = normalized[:64]
    
    return normalized if normalized else DEFAULT_ACCOUNT_ID


def build_agent_main_session_key(
    agent_id: str,
    main_key: str | None = None,
) -> str:
    """
    Build main session key (matches TS buildAgentMainSessionKey lines 117-123).
    
    Format: agent:<agentId>:<mainKey>
    
    Examples:
        ("main", None) → "agent:main:main"
        ("myagent", "prod") → "agent:myagent:prod"
    """
    normalized_agent = normalize_agent_id(agent_id)
    normalized_main = normalize_main_key(main_key)
    return f"agent:{normalized_agent}:{normalized_main}"


def resolve_agent_main_session_key(cfg: Any = None, agent_id: str | None = None) -> str:
    """
    Resolve the main session key for an agent (mirrors TS resolveAgentMainSessionKey).
    
    This is used to locate an agent's main session entry in the session store,
    which contains delivery target information (lastChannel, lastTo, lastAccountId).
    
    Args:
        cfg: OpenClaw config (may contain custom mainKey)
        agent_id: Agent identifier (normalized internally)
    
    Returns:
        Main session key, e.g., "agent:main:main" or "agent:main:<customKey>"
    
    Examples:
        >>> resolve_agent_main_session_key(None, "main")
        'agent:main:main'
        >>> resolve_agent_main_session_key(cfg_with_custom_key, "jarvis")
        'agent:jarvis:custom'
    """
    aid = normalize_agent_id(agent_id)
    
    # Extract custom mainKey from config if present
    main_key = "main"
    if cfg:
        # Try dict-style access first (for dict configs)
        if isinstance(cfg, dict):
            session_cfg = cfg.get("session", {})
            if isinstance(session_cfg, dict):
                main_key = session_cfg.get("mainKey", "main")
        # Try attribute access (for object configs)
        else:
            session_cfg = getattr(cfg, "session", None)
            if session_cfg:
                if isinstance(session_cfg, dict):
                    main_key = session_cfg.get("mainKey", "main")
                else:
                    main_key = getattr(session_cfg, "mainKey", "main")
    
    return build_agent_main_session_key(agent_id=aid, main_key=main_key)


def _resolve_linked_peer_id(
    identity_links: dict | None,
    channel: str,
    peer_id: str,
) -> str | None:
    """
    Resolve a canonical peer ID via identity link mapping.

    Matches TS resolveLinkedPeerId() in routing/session-key.ts.
    """
    if not identity_links:
        return None
    peer_id_trimmed = peer_id.strip()
    if not peer_id_trimmed:
        return None

    raw_candidate = peer_id_trimmed.lower()
    channel_norm = channel.strip().lower()
    candidates = {raw_candidate}
    if channel_norm:
        candidates.add(f"{channel_norm}:{peer_id_trimmed}".lower())

    for canonical, ids in identity_links.items():
        canonical_name = canonical.strip()
        if not canonical_name:
            continue
        if not isinstance(ids, list):
            continue
        for id_entry in ids:
            normalized = (id_entry or "").strip().lower()
            if normalized and normalized in candidates:
                return canonical_name
    return None


def build_agent_peer_session_key(
    agent_id: str,
    channel: str,
    peer_kind: str = "direct",
    peer_id: str | None = None,
    account_id: str | None = None,
    main_key: str | None = None,
    dm_scope: str = "main",
    identity_links: dict | None = None,
) -> str:
    """
    Build peer session key (matches TS buildAgentPeerSessionKey).

    DM scope modes:
    - "main": agent:<agentId>:main (all DMs share main session)
    - "per-peer": agent:<agentId>:direct:<peerId>
    - "per-channel-peer": agent:<agentId>:<channel>:direct:<peerId>
    - "per-account-channel-peer": agent:<agentId>:<channel>:<accountId>:direct:<peerId>

    For groups:   agent:<agentId>:<channel>:group:<groupId>
    For channels: agent:<agentId>:<channel>:channel:<channelId>

    identity_links: optional canonical peer ID mapping (matches TS identityLinks).
    """
    normalized_agent = normalize_agent_id(agent_id)
    normalized_channel = channel.strip().lower() if channel else "unknown"
    raw_peer_id = (peer_id or "").strip()

    # DM handling
    normalized_peer_kind = (peer_kind or "").strip().lower()
    if normalized_peer_kind in ("dm", "direct"):
        if dm_scope == "main":
            return build_agent_main_session_key(agent_id, main_key)
        # Identity link resolution (only for non-main scopes, matches TS)
        effective_peer_id = raw_peer_id
        if identity_links and effective_peer_id:
            linked = _resolve_linked_peer_id(identity_links, normalized_channel, effective_peer_id)
            if linked:
                effective_peer_id = linked
        effective_peer_id = effective_peer_id.lower()
        if dm_scope == "per-peer" and effective_peer_id:
            return f"agent:{normalized_agent}:direct:{effective_peer_id}"
        if dm_scope == "per-channel-peer" and effective_peer_id:
            return f"agent:{normalized_agent}:{normalized_channel}:direct:{effective_peer_id}"
        if dm_scope == "per-account-channel-peer" and effective_peer_id:
            normalized_account = normalize_account_id(account_id)
            return f"agent:{normalized_agent}:{normalized_channel}:{normalized_account}:direct:{effective_peer_id}"
        return build_agent_main_session_key(agent_id, main_key)

    # Non-DM: group / channel / other
    normalized_channel_out = normalized_channel or "unknown"
    peer_id_out = (raw_peer_id or "unknown").lower()
    return f"agent:{normalized_agent}:{normalized_channel_out}:{normalized_peer_kind}:{peer_id_out}"


def parse_agent_session_key(session_key: str | None) -> ParsedAgentSessionKey | None:
    """
    Parse session key into components (matches TS parseAgentSessionKey).
    
    Format: agent:<agentId>:<rest>
    
    Normalizes to lowercase during parsing to ensure consistent key format.
    
    Examples:
        "agent:main:main" → ParsedAgentSessionKey("main", "main", ...)
        "AGENT:Main:Main" → ParsedAgentSessionKey("main", "main", ...)
        "agent:myagent:telegram:group:123" → ParsedAgentSessionKey("myagent", "telegram:group:123", ...)
        "invalid" → None
    """
    raw = (session_key or "").strip().lower()  # Normalize to lowercase like TS
    if not raw:
        return None
    
    if not raw.startswith("agent:"):
        return None
    
    # Split: agent:<agentId>:<rest> - filter empty parts like TS
    parts = [p for p in raw.split(":") if p]
    if len(parts) < 3:
        return None
    
    agent_id = parts[1].strip()
    rest = ":".join(parts[2:])
    
    if not agent_id or not rest:
        return None
    
    return ParsedAgentSessionKey(
        agent_id=agent_id,
        rest=rest,
        full_key=raw,
    )


def resolve_agent_id_from_session_key(session_key: str | None) -> str:
    """Extract agent ID from session key (matches TS resolveAgentIdFromSessionKey lines 56-58)."""
    parsed = parse_agent_session_key(session_key)
    return normalize_agent_id(parsed.agent_id if parsed else None)


def to_agent_store_session_key(
    agent_id: str,
    request_key: str | None,
    main_key: str | None = None,
) -> str:
    """
    Convert request key to store key (matches TS toAgentStoreSessionKey lines 53-71).
    
    Args:
        agent_id: Agent identifier
        request_key: Request session key (may be partial)
        main_key: Main key override
    
    Returns:
        Full agent store session key
    
    Examples:
        >>> to_agent_store_session_key("main", "main")
        "agent:main:main"
        >>> to_agent_store_session_key("main", "cron:job-1")
        "agent:main:cron:job-1"
        >>> to_agent_store_session_key("main", "agent:main:main")
        "agent:main:main"
    """
    raw = (request_key or "").strip()
    
    # Step 1: Empty or "main" → build main session key
    if not raw or raw.lower() == DEFAULT_MAIN_KEY:
        return build_agent_main_session_key(agent_id, main_key)
    
    # Step 2: Try to parse as agent session key - if valid, normalize it
    parsed = parse_agent_session_key(raw)
    if parsed:
        return f"agent:{parsed.agent_id}:{parsed.rest}"
    
    # Step 3: Starts with "agent:" but not valid → use as-is (lowercase)
    lowered = raw.lower()
    if lowered.startswith("agent:"):
        return lowered
    
    # Step 4: Other keys → prefix with agent scope
    return f"agent:{normalize_agent_id(agent_id)}:{lowered}"


def to_agent_request_session_key(store_key: str | None) -> str | None:
    """
    Convert store key to request key (matches TS toAgentRequestSessionKey lines 29-34).
    
    Strips "agent:<agentId>:" prefix to get the rest.
    """
    raw = (store_key or "").strip()
    if not raw:
        return None
    
    parsed = parse_agent_session_key(raw)
    return parsed.rest if parsed else raw


def looks_like_session_key(value: str | None) -> bool:
    """Check if value looks like a session key."""
    raw = (value or "").strip()
    return raw.startswith("agent:") and ":" in raw[6:]


def is_subagent_session_key(session_key: str | None) -> bool:
    """Check if session key represents a subagent."""
    parsed = parse_agent_session_key(session_key)
    if not parsed:
        return False
    return parsed.rest.startswith("subagent:")


def is_acp_session_key(session_key: str | None) -> bool:
    """Check if session key is an ACP (Agent Control Protocol) session."""
    parsed = parse_agent_session_key(session_key)
    if not parsed:
        return False
    return parsed.rest.startswith("acp:")


# ---------------------------------------------------------------------------
# Functions ported from src/sessions/session-key-utils.ts
# ---------------------------------------------------------------------------

THREAD_SESSION_MARKERS = (":thread:", ":topic:")


def is_cron_run_session_key(session_key: str | None) -> bool:
    """
    Return True if the session key identifies a single cron job run.

    Pattern: agent:<agentId>:cron:<name>:run:<runId>

    Matches TS isCronRunSessionKey().
    """
    import re
    parsed = parse_agent_session_key(session_key)
    if not parsed:
        return False
    return bool(re.match(r"^cron:[^:]+:run:[^:]+$", parsed.rest))


def is_cron_session_key(session_key: str | None) -> bool:
    """
    Return True if the session key belongs to the cron subsystem.

    Matches TS isCronSessionKey().
    """
    parsed = parse_agent_session_key(session_key)
    if not parsed:
        return False
    return parsed.rest.lower().startswith("cron:")


def get_subagent_depth(session_key: str | None) -> int:
    """
    Return nesting depth of subagent session key.

    Examples:
        "agent:main:main" → 0
        "agent:main:subagent:..." → 1
        "agent:main:subagent:...:subagent:..." → 2

    Matches TS getSubagentDepth().
    """
    raw = (session_key or "").strip().lower()
    if not raw:
        return 0
    return raw.count(":subagent:")


def resolve_thread_parent_session_key(session_key: str | None) -> str | None:
    """
    Resolve the parent session key from a thread session key.

    Finds the *last* occurrence of ':thread:' or ':topic:' and returns
    everything before it.

    Returns None if no thread marker is found.

    Matches TS resolveThreadParentSessionKey().
    """
    raw = (session_key or "").strip()
    if not raw:
        return None
    normalized = raw.lower()
    idx = -1
    for marker in THREAD_SESSION_MARKERS:
        candidate = normalized.rfind(marker)
        if candidate > idx:
            idx = candidate
    if idx <= 0:
        return None
    parent = raw[:idx].strip()
    return parent if parent else None


SessionKeyShape = str  # "missing" | "agent" | "legacy_or_alias" | "malformed_agent"


def classify_session_key_shape(session_key: str | None) -> SessionKeyShape:
    """
    Classify the shape of a session key string.

    Returns:
        "missing"          — empty/None
        "agent"            — well-formed agent:<id>:<rest>
        "malformed_agent"  — starts with "agent:" but not fully valid
        "legacy_or_alias"  — any other non-empty string

    Matches TS classifySessionKeyShape().
    """
    raw = (session_key or "").strip()
    if not raw:
        return "missing"
    if parse_agent_session_key(raw):
        return "agent"
    return "malformed_agent" if raw.lower().startswith("agent:") else "legacy_or_alias"


def build_group_history_key(
    channel: str,
    peer_kind: str,
    peer_id: str,
    account_id: str | None = None,
) -> str:
    """
    Build a group/channel history key (not a full session key, used for history lookups).

    Format: <channel>:<accountId>:<peerKind>:<peerId>

    Matches TS buildGroupHistoryKey().
    """
    channel_norm = channel.strip().lower() or "unknown"
    account_id_norm = normalize_account_id(account_id)
    peer_id_norm = peer_id.strip().lower() or "unknown"
    return f"{channel_norm}:{account_id_norm}:{peer_kind}:{peer_id_norm}"


def resolve_thread_session_keys(
    base_session_key: str,
    thread_id: str | None = None,
    parent_session_key: str | None = None,
    use_suffix: bool = True,
) -> dict:
    """
    Build thread session key with optional suffix.

    Returns:
        {"session_key": str, "parent_session_key": str | None}

    Matches TS resolveThreadSessionKeys().
    """
    thread_id_trimmed = (thread_id or "").strip()
    if not thread_id_trimmed:
        return {"session_key": base_session_key, "parent_session_key": None}
    normalized_thread_id = thread_id_trimmed.lower()
    session_key = (
        f"{base_session_key}:thread:{normalized_thread_id}" if use_suffix else base_session_key
    )
    return {"session_key": session_key, "parent_session_key": parent_session_key}


def evaluate_session_freshness(
    last_activity_ms: int | None,
    reset_policy: str | None = None,
    idle_duration_ms: int | None = None,
) -> bool:
    """
    Evaluate if a session should be reset based on freshness policy.

    Thin wrapper that delegates to the canonical implementation in
    ``openclaw.agents.session_store.evaluate_session_freshness`` (which has
    full TS parity including daily-reset-at-hour semantics).

    Args:
        last_activity_ms: Timestamp of last activity in milliseconds (Unix epoch).
        reset_policy: "daily" | "idle" | "off" | None
        idle_duration_ms: Idle duration in milliseconds for "idle" policy.

    Returns:
        True if session should be reset (is stale), False if still fresh.
    """
    import time

    from openclaw.agents.session_store import (
        SessionResetPolicy,
        evaluate_session_freshness as _evaluate,
    )

    if not reset_policy or reset_policy == "off":
        return False

    if last_activity_ms is None:
        return False

    idle_minutes: int | None = None
    if idle_duration_ms is not None:
        idle_minutes = max(1, idle_duration_ms // 60_000)
    elif reset_policy == "idle":
        idle_minutes = 240  # 4 hours default

    policy = SessionResetPolicy(
        mode=reset_policy,
        idle_minutes=idle_minutes,
    )
    now_ms = int(time.time() * 1000)
    result = _evaluate(last_activity_ms, now_ms, policy)
    return not result.fresh
