"""
Session utilities for gateway operations

This module provides utility functions for session resolution, listing,
classification, and title derivation matching the TypeScript implementation.
"""

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, NamedTuple
from dataclasses import dataclass

from openclaw.agents.session_entry import SessionEntry
from openclaw.config.sessions.store_utils import load_session_store
from openclaw.config.sessions.paths import (
    get_default_store_path,
    resolve_session_store_path,
)
from openclaw.config.sessions.transcripts import (
    read_first_user_message,
    read_last_message_preview,
    read_transcript_preview,
)
from openclaw.routing.session_key import (
    parse_agent_session_key,
    normalize_agent_id,
)
from openclaw.config.paths import resolve_state_dir

logger = logging.getLogger(__name__)

DERIVED_TITLE_MAX_LEN = 60


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class GatewaySessionsDefaults:
    """Default values for sessions"""
    model_provider: Optional[str]
    model: Optional[str]
    context_tokens: Optional[int]


@dataclass
class GatewaySessionRow:
    """Session row for gateway list response"""
    key: str
    kind: Literal["direct", "group", "global", "unknown"]
    label: Optional[str] = None
    display_name: Optional[str] = None
    derived_title: Optional[str] = None
    last_message_preview: Optional[str] = None
    channel: Optional[str] = None
    subject: Optional[str] = None
    group_channel: Optional[str] = None
    space: Optional[str] = None
    chat_type: Optional[str] = None
    origin: Optional[Dict[str, Any]] = None
    updated_at: Optional[int] = None
    session_id: Optional[str] = None
    system_sent: Optional[bool] = None
    aborted_last_run: Optional[bool] = None
    thinking_level: Optional[str] = None
    verbose_level: Optional[str] = None
    reasoning_level: Optional[str] = None
    elevated_level: Optional[str] = None
    send_policy: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    response_usage: Optional[str] = None
    model_provider: Optional[str] = None
    model: Optional[str] = None
    context_tokens: Optional[int] = None
    delivery_context: Optional[Dict[str, Any]] = None
    last_channel: Optional[str] = None
    last_to: Optional[str] = None
    last_account_id: Optional[str] = None


@dataclass
class SessionsListResult:
    """Result of sessions.list operation"""
    ts: int
    path: str
    count: int
    defaults: GatewaySessionsDefaults
    sessions: List[GatewaySessionRow]


@dataclass
class GatewayStoreTarget:
    """Target store information for a session key"""
    agent_id: str
    store_path: str
    canonical_key: str
    store_keys: List[str]  # Alternative keys to check


class LoadedSessionEntry(NamedTuple):
    """Loaded session entry with context"""
    entry: SessionEntry
    store_path: str
    canonical_key: str
    store: Dict[str, SessionEntry]


# ============================================================================
# Session Key Resolution
# ============================================================================

def resolve_session_store_key(session_key: str, main_key: str = "main") -> str:
    """
    Resolve and canonicalize session key
    
    Args:
        session_key: Raw session key
        main_key: Main session key (default: "main")
        
    Returns:
        Canonical session key
    """
    key = session_key.strip()
    
    # Special keys pass through
    if key in ("global", "unknown"):
        return key
    
    # Parse agent session key
    parsed = parse_agent_session_key(key)
    if parsed:
        agent_id = parsed.agent_id
        rest = parsed.rest
        
        # Canonicalize "main" alias
        if rest == "main" or rest == main_key:
            return f"agent:{agent_id}:main"
        
        return f"agent:{agent_id}:{rest}"
    
    # Treat as simple key
    return key


def resolve_main_session_key(agent_id: str = "main", main_key: str = "main") -> str:
    """
    Resolve main session key for an agent
    
    Args:
        agent_id: Agent identifier
        main_key: Main session key name
        
    Returns:
        Main session key: agent:{agentId}:main
    """
    normalized_agent_id = normalize_agent_id(agent_id)
    return f"agent:{normalized_agent_id}:main"


def resolve_gateway_session_store_target(
    key: str,
    agent_id: str = "main",
    workspace_root: Optional[Path] = None
) -> GatewayStoreTarget:
    """
    Resolve store path and canonical key for a session
    
    Args:
        key: Session key
        agent_id: Default agent ID
        workspace_root: Workspace root directory
        
    Returns:
        GatewayStoreTarget with store information
    """
    # Parse agent from key
    parsed = parse_agent_session_key(key)
    if parsed:
        target_agent_id = parsed.agent_id
    else:
        target_agent_id = agent_id
    
    # Resolve store path
    store_path = get_default_store_path(target_agent_id)
    
    # Canonical key
    canonical_key = resolve_session_store_key(key)
    
    # Alternative keys to check (for migration)
    store_keys = [canonical_key]
    
    return GatewayStoreTarget(
        agent_id=target_agent_id,
        store_path=str(store_path),
        canonical_key=canonical_key,
        store_keys=store_keys
    )


# ============================================================================
# Session Loading
# ============================================================================

def load_session_entry(
    session_key: str,
    agent_id: str = "main",
    workspace_root: Optional[Path] = None
) -> Optional[LoadedSessionEntry]:
    """
    Load complete session entry with store context
    
    Args:
        session_key: Session key to load
        agent_id: Default agent ID
        workspace_root: Workspace root directory
        
    Returns:
        LoadedSessionEntry or None if not found
    """
    target = resolve_gateway_session_store_target(session_key, agent_id, workspace_root)
    store = load_session_store(target.store_path)
    
    # Try to find entry with alternative keys
    entry = None
    for key in target.store_keys:
        if key in store:
            entry = store[key]
            break
    
    if not entry:
        return None
    
    return LoadedSessionEntry(
        entry=entry,
        store_path=target.store_path,
        canonical_key=target.canonical_key,
        store=store
    )


def load_combined_session_store(
    agent_ids: Optional[List[str]] = None,
    workspace_root: Optional[Path] = None
) -> Dict[str, SessionEntry]:
    """
    Load and merge session stores from multiple agents
    
    Args:
        agent_ids: List of agent IDs to load (default: ["main"])
        workspace_root: Workspace root directory
        
    Returns:
        Combined store dictionary
    """
    if agent_ids is None:
        agent_ids = ["main"]
    
    combined: Dict[str, SessionEntry] = {}
    
    for agent_id in agent_ids:
        store_path = get_default_store_path(agent_id)
        if not Path(store_path).exists():
            continue
        
        store = load_session_store(str(store_path))
        
        # Merge with conflict resolution (latest updatedAt wins)
        for key, entry in store.items():
            if key not in combined or entry.updatedAt > combined[key].updatedAt:
                combined[key] = entry
    
    return combined


# ============================================================================
# Session Classification
# ============================================================================

def classify_session_key(key: str, entry: Optional[SessionEntry] = None) -> Literal["direct", "group", "global", "unknown"]:
    """
    Classify session by key pattern
    
    Args:
        key: Session key
        entry: Optional session entry for additional context
        
    Returns:
        Session kind: direct, group, global, or unknown
    """
    if key == "global":
        return "global"
    
    if key == "unknown":
        return "unknown"
    
    # Parse agent session key
    parsed = parse_agent_session_key(key)
    if not parsed:
        return "unknown"
    
    rest = parsed.rest
    
    # Group patterns
    if "group" in rest or "channel" in rest:
        return "group"
    
    # Check entry for group indicators
    if entry:
        if entry.chatType in ("group", "channel", "supergroup"):
            return "group"
        if entry.groupId or entry.groupChannel:
            return "group"
    
    # Default to direct
    return "direct"


# ============================================================================
# Session Title Derivation
# ============================================================================

def _truncate_title(text: str, max_len: int) -> str:
    """Truncate title at word boundary. Mirrors TS truncateTitle()."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len - 1]
    last_space = cut.rfind(" ")
    if last_space > max_len * 0.6:
        return cut[:last_space] + "…"
    return cut + "…"


def _format_session_id_prefix(session_id: str, updated_at: Optional[int] = None) -> str:
    """Format a short session ID prefix with optional date."""
    prefix = session_id[:8]
    if updated_at and updated_at > 0:
        from datetime import datetime, timezone
        d = datetime.fromtimestamp(updated_at / 1000, tz=timezone.utc)
        return f"{prefix} ({d.strftime('%Y-%m-%d')})"
    return prefix


def derive_session_title(
    entry: Optional[SessionEntry],
    first_user_message: Optional[str] = None
) -> Optional[str]:
    """
    Derive display title for session.
    Mirrors TS deriveSessionTitle() exactly (60-char word-boundary truncation).

    Priority:
    1. displayName
    2. subject
    3. first user message (word-boundary truncated to 60 chars)
    4. sessionId prefix with date
    """
    if entry is None:
        return None

    if entry.displayName and entry.displayName.strip():
        return entry.displayName.strip()

    if entry.subject and entry.subject.strip():
        return entry.subject.strip()

    if first_user_message and first_user_message.strip():
        import re as _re
        normalized = _re.sub(r"\s+", " ", first_user_message).strip()
        return _truncate_title(normalized, DERIVED_TITLE_MAX_LEN)

    if entry.sessionId:
        return _format_session_id_prefix(entry.sessionId, getattr(entry, "updatedAt", None))

    return None


# ============================================================================
# Session Listing
# ============================================================================

@dataclass
class SessionsListOptions:
    """Options for listing sessions"""
    agent_id: Optional[str] = None
    spawned_by: Optional[str] = None
    label: Optional[str] = None
    search: Optional[str] = None
    include_global: bool = True
    include_unknown: bool = True
    active_minutes: Optional[int] = None
    add_derived_titles: bool = False
    add_last_message_preview: bool = False
    limit: Optional[int] = None
    offset: int = 0


def list_sessions_from_store(
    store_path: str,
    store: Dict[str, SessionEntry],
    opts: Optional[SessionsListOptions] = None
) -> SessionsListResult:
    """
    Filter, search, and sort sessions from store
    
    Args:
        store_path: Path to sessions.json
        store: Session store dictionary
        opts: List options
        
    Returns:
        SessionsListResult with filtered and sorted sessions
    """
    if opts is None:
        opts = SessionsListOptions()
    
    # Filter sessions
    filtered_sessions: List[tuple[str, SessionEntry]] = []
    
    for key, entry in store.items():
        # Agent ID filter
        if opts.agent_id:
            parsed = parse_agent_session_key(key)
            if not parsed or parsed.agent_id != opts.agent_id:
                continue
        
        # Spawned by filter
        if opts.spawned_by and entry.spawnedBy != opts.spawned_by:
            continue
        
        # Label filter
        if opts.label and entry.label != opts.label:
            continue
        
        # Search filter (case-insensitive)
        if opts.search:
            search_lower = opts.search.lower()
            searchable = " ".join([
                entry.sessionId,
                entry.label or "",
                entry.displayName or "",
                entry.subject or "",
                key,
            ]).lower()
            if search_lower not in searchable:
                continue
        
        # Include global/unknown
        if key == "global" and not opts.include_global:
            continue
        if key == "unknown" and not opts.include_unknown:
            continue
        
        # Active minutes filter
        if opts.active_minutes:
            now_ms = int(time.time() * 1000)
            cutoff_ms = now_ms - (opts.active_minutes * 60 * 1000)
            if entry.updatedAt < cutoff_ms:
                continue
        
        filtered_sessions.append((key, entry))
    
    # Sort by updatedAt (newest first)
    filtered_sessions.sort(key=lambda x: x[1].updatedAt, reverse=True)
    
    # Apply offset and limit
    if opts.offset and opts.offset > 0:
        filtered_sessions = filtered_sessions[opts.offset:]
    if opts.limit:
        filtered_sessions = filtered_sessions[:opts.limit]
    
    # Convert to GatewaySessionRow
    rows: List[GatewaySessionRow] = []
    for key, entry in filtered_sessions:
        kind = classify_session_key(key, entry)
        
        # Optionally add derived title
        derived_title = None
        if opts.add_derived_titles:
            first_message = None
            if entry.sessionFile or entry.sessionId:
                first_message = read_first_user_message(
                    entry.sessionId,
                    store_path,
                    entry.sessionFile
                )
            derived_title = derive_session_title(entry, first_message)
        
        # Optionally add last message preview
        last_message_preview = None
        if opts.add_last_message_preview:
            if entry.sessionFile or entry.sessionId:
                last_message_preview = read_last_message_preview(
                    entry.sessionId,
                    store_path,
                    entry.sessionFile
                )
        
        row = GatewaySessionRow(
            key=key,
            kind=kind,
            label=entry.label,
            display_name=entry.displayName,
            derived_title=derived_title,
            last_message_preview=last_message_preview,
            channel=entry.channel,
            subject=entry.subject,
            group_channel=entry.groupChannel,
            space=entry.space,
            chat_type=entry.chatType,
            origin=entry.origin.model_dump() if entry.origin else None,
            updated_at=entry.updatedAt,
            session_id=entry.sessionId,
            system_sent=entry.systemSent,
            aborted_last_run=entry.abortedLastRun,
            thinking_level=entry.thinkingLevel,
            verbose_level=entry.verboseLevel,
            reasoning_level=entry.reasoningLevel,
            elevated_level=entry.elevatedLevel,
            send_policy=entry.sendPolicy,
            input_tokens=entry.inputTokens,
            output_tokens=entry.outputTokens,
            total_tokens=entry.totalTokens,
            response_usage=entry.responseUsage,
            model_provider=entry.modelProvider,
            model=entry.model,
            context_tokens=entry.contextTokens,
            delivery_context=entry.deliveryContext.model_dump() if entry.deliveryContext else None,
            last_channel=entry.lastChannel,
            last_to=entry.lastTo,
            last_account_id=entry.lastAccountId,
        )
        rows.append(row)
    
    # Compute defaults (from first entry or None)
    defaults = GatewaySessionsDefaults(
        model_provider=rows[0].model_provider if rows else None,
        model=rows[0].model if rows else None,
        context_tokens=rows[0].context_tokens if rows else None,
    )
    
    return SessionsListResult(
        ts=int(time.time() * 1000),
        path=store_path,
        count=len(rows),
        defaults=defaults,
        sessions=rows
    )


# ============================================================================
# Session Preview Items
# ============================================================================

@dataclass
class SessionPreviewItem:
    """Preview item for session transcript"""
    role: Literal["user", "assistant", "tool", "system", "other"]
    text: str


# ============================================================================
# TS-aligned utility functions (added for full parity)
# ============================================================================

def find_store_keys_ignore_case(
    store: Dict[str, Any],
    target_key: str,
) -> List[str]:
    """
    Find all store keys that match target_key case-insensitively.
    Mirrors TS findStoreKeysIgnoreCase().
    """
    lowered = target_key.lower()
    return [k for k in store if k.lower() == lowered]


def prune_legacy_store_keys(
    store: Dict[str, Any],
    canonical_key: str,
    candidates: Any,
) -> None:
    """
    Remove legacy key variants from the store, keeping only canonical_key.
    Mirrors TS pruneLegacyStoreKeys().
    """
    keys_to_delete: set[str] = set()
    for candidate in candidates:
        trimmed = str(candidate or "").strip()
        if not trimmed:
            continue
        if trimmed != canonical_key:
            keys_to_delete.add(trimmed)
        for match in find_store_keys_ignore_case(store, trimmed):
            if match != canonical_key:
                keys_to_delete.add(match)
    for key in keys_to_delete:
        store.pop(key, None)


def parse_group_key(
    key: str,
) -> Optional[Dict[str, str]]:
    """
    Parse a group/channel session key into {channel, kind, id}.
    Returns None if not a group/channel key.
    Mirrors TS parseGroupKey().
    """
    from openclaw.routing.session_key import parse_agent_session_key as _parse_ask
    parsed = _parse_ask(key)
    raw_key = parsed.rest if parsed else key
    parts = [p for p in raw_key.split(":") if p]
    if len(parts) >= 3:
        channel, kind, *rest = parts
        if kind in ("group", "channel"):
            return {"channel": channel, "kind": kind, "id": ":".join(rest)}
    return None


def list_existing_agent_ids_from_disk() -> list[str]:
    """
    List agent IDs from disk (state directory).
    Mirrors TS listExistingAgentIdsFromDisk().
    """
    try:
        state_dir = Path(resolve_state_dir())
        agents_dir = state_dir / "agents"
        if not agents_dir.is_dir():
            return []
        
        agent_ids = []
        for entry in agents_dir.iterdir():
            if entry.is_dir():
                normalized = normalize_agent_id(entry.name)
                if normalized:
                    agent_ids.append(normalized)
        return agent_ids
    except Exception:
        return []


def list_configured_agent_ids(cfg: Any) -> list[str]:
    """
    List all configured agent IDs from config and disk.
    Mirrors TS listConfiguredAgentIds().
    """
    from openclaw.agents.agent_scope import resolve_default_agent_id
    
    # Extract agents list from config
    agents_list = []
    if hasattr(cfg, 'agents') and cfg.agents:
        agents_list = cfg.agents.list or []
    elif isinstance(cfg, dict):
        agents_section = cfg.get("agents") or {}
        agents_list = agents_section.get("list") or []
    
    if agents_list:
        ids = set()
        for entry in agents_list:
            entry_dict = entry.__dict__ if hasattr(entry, '__dict__') else entry
            if isinstance(entry_dict, dict) and entry_dict.get("id"):
                ids.add(normalize_agent_id(str(entry_dict["id"])))
        
        default_id = normalize_agent_id(resolve_default_agent_id(cfg))
        ids.add(default_id)
        
        sorted_ids = sorted(ids)
        # Ensure default comes first
        if default_id in sorted_ids:
            return [default_id] + [id_ for id_ in sorted_ids if id_ != default_id]
        return sorted_ids
    
    # No configured agents - scan disk and include default
    ids = set()
    default_id = normalize_agent_id(resolve_default_agent_id(cfg))
    ids.add(default_id)
    
    for disk_id in list_existing_agent_ids_from_disk():
        ids.add(disk_id)
    
    sorted_ids = sorted(ids)
    if default_id in sorted_ids:
        return [default_id] + [id_ for id_ in sorted_ids if id_ != default_id]
    return sorted_ids


def resolve_identity_avatar_url(
    cfg: Any,
    agent_id: str,
    avatar: str | None,
) -> str | None:
    """
    Resolve avatar URL for identity.
    Mirrors TS resolveIdentityAvatarUrl().
    """
    if not avatar:
        return None
    
    trimmed = avatar.strip()
    if not trimmed:
        return None
    
    # Check if it's a data URL or HTTP URL
    from openclaw.shared.avatar_policy import (
        is_avatar_data_url,
        is_avatar_http_url,
        is_workspace_relative_avatar_path,
    )
    
    if is_avatar_data_url(trimmed) or is_avatar_http_url(trimmed):
        return trimmed
    
    if not is_workspace_relative_avatar_path(trimmed):
        return None
    
    # Resolve workspace-relative path
    from openclaw.agents.agent_scope import resolve_agent_workspace_dir
    workspace_dir = resolve_agent_workspace_dir(cfg, agent_id)
    
    try:
        workspace_root = Path(workspace_dir).resolve()
    except Exception:
        workspace_root = Path(workspace_dir).absolute()
    
    try:
        resolved_candidate = (workspace_root / trimmed).resolve()
        
        # Security: ensure path is within workspace
        from openclaw.shared.avatar_policy import is_path_within_root
        if not is_path_within_root(str(resolved_candidate), str(workspace_root)):
            return None
        
        # Check if file exists and is a file
        if not resolved_candidate.is_file():
            return None
        
        # Check file size
        from openclaw.shared.avatar_policy import AVATAR_MAX_BYTES
        if resolved_candidate.stat().st_size > AVATAR_MAX_BYTES:
            return None
        
        # Resolve MIME type
        from openclaw.shared.avatar_policy import resolve_avatar_mime
        mime = resolve_avatar_mime(str(resolved_candidate))
        if not mime:
            return None
        
        # Read and encode as data URL
        with open(resolved_candidate, "rb") as f:
            import base64
            encoded = base64.b64encode(f.read()).decode("utf-8")
            return f"data:{mime};base64,{encoded}"
    except Exception:
        return None


def list_agents_for_gateway(cfg: Any) -> Dict[str, Any]:
    """
    List agents configured for the gateway with identity info.
    Mirrors TS listAgentsForGateway().

    Returns:
        {"defaultId": str, "mainKey": str, "scope": str, "agents": list}
    """
    from openclaw.agents.agent_scope import resolve_default_agent_id
    from openclaw.routing.session_key import normalize_main_key
    
    default_id = normalize_agent_id(resolve_default_agent_id(cfg))
    
    # Resolve main key and scope
    main_key = "main"
    scope = "per-sender"
    if hasattr(cfg, 'session') and cfg.session:
        main_key = normalize_main_key(cfg.session.main_key if hasattr(cfg.session, 'main_key') else "main")
        scope = cfg.session.scope if hasattr(cfg.session, 'scope') else "per-sender"
    elif isinstance(cfg, dict):
        session_section = cfg.get("session") or {}
        main_key = normalize_main_key(session_section.get("mainKey") or "main")
        scope = session_section.get("scope") or "per-sender"
    
    # Build map of configured agents
    configured_by_id: dict[str, dict[str, Any]] = {}
    
    agents_list = []
    if hasattr(cfg, 'agents') and cfg.agents:
        agents_list = cfg.agents.list or []
    elif isinstance(cfg, dict):
        agents_section = cfg.get("agents") or {}
        agents_list = agents_section.get("list") or []
    
    for entry in agents_list:
        entry_dict = entry.__dict__ if hasattr(entry, '__dict__') else entry
        if not isinstance(entry_dict, dict) or not entry_dict.get("id"):
            continue
        
        agent_id = normalize_agent_id(str(entry_dict["id"]))
        identity_raw = entry_dict.get("identity")
        identity = None
        
        if identity_raw:
            identity_dict = identity_raw.__dict__ if hasattr(identity_raw, '__dict__') else identity_raw
            if isinstance(identity_dict, dict):
                identity = {
                    "name": (identity_dict.get("name") or "").strip() or None,
                    "theme": (identity_dict.get("theme") or "").strip() or None,
                    "emoji": (identity_dict.get("emoji") or "").strip() or None,
                    "avatar": (identity_dict.get("avatar") or "").strip() or None,
                    "avatarUrl": resolve_identity_avatar_url(
                        cfg,
                        agent_id,
                        (identity_dict.get("avatar") or "").strip() or None,
                    ),
                }
        
        name_raw = entry_dict.get("name")
        configured_by_id[agent_id] = {
            "name": name_raw.strip() if isinstance(name_raw, str) and name_raw.strip() else None,
            "identity": identity,
        }
    
    # Build explicit IDs set
    explicit_ids = set()
    for entry in agents_list:
        entry_dict = entry.__dict__ if hasattr(entry, '__dict__') else entry
        if isinstance(entry_dict, dict) and entry_dict.get("id"):
            explicit_ids.add(normalize_agent_id(str(entry_dict["id"])))
    
    allowed_ids = explicit_ids | {default_id} if explicit_ids else None
    
    # Get all agent IDs
    agent_ids = [
        id_ for id_ in list_configured_agent_ids(cfg)
        if not allowed_ids or id_ in allowed_ids
    ]
    
    # Add main_key if it's not in the list and allowed
    if main_key and main_key not in agent_ids and (not allowed_ids or main_key in allowed_ids):
        agent_ids.append(main_key)
    
    # Build agent rows
    agents = []
    for agent_id in agent_ids:
        meta = configured_by_id.get(agent_id, {})
        agents.append({
            "id": agent_id,
            "name": meta.get("name"),
            "identity": meta.get("identity"),
        })
    
    return {"defaultId": default_id, "mainKey": main_key, "scope": scope, "agents": agents}


def resolve_session_model_ref(
    cfg: Any,
    entry: Any = None,
    agent_id: Optional[str] = None,
) -> Dict[str, str]:
    """
    Resolve the effective {provider, model} for a session entry.
    Mirrors TS resolveSessionModelRef().

    Priority:
    1. Runtime model recorded on entry (entry.model)
    2. Per-session model override (entry.modelOverride)
    3. Configured default for agent
    """
    from openclaw.agents.model_selection import (
        resolve_default_model_for_agent,
        resolve_configured_model_ref,
        parse_model_ref,
        DEFAULT_PROVIDER,
        DEFAULT_MODEL,
    )

    if agent_id:
        resolved = resolve_default_model_for_agent(cfg, agent_id)
    else:
        resolved = resolve_configured_model_ref(cfg)

    provider = resolved.provider
    model = resolved.model

    if isinstance(entry, dict) or hasattr(entry, "model"):
        entry_dict = entry if isinstance(entry, dict) else {}
        if not isinstance(entry, dict):
            for attr in ("model", "modelProvider", "modelOverride", "providerOverride"):
                if hasattr(entry, attr):
                    entry_dict[attr] = getattr(entry, attr)

        runtime_model = (entry_dict.get("model") or "").strip()
        runtime_provider = (entry_dict.get("modelProvider") or "").strip()
        if runtime_model:
            parsed_runtime = parse_model_ref(
                runtime_model,
                runtime_provider or provider or DEFAULT_PROVIDER,
            )
            if parsed_runtime:
                provider = parsed_runtime.provider
                model = parsed_runtime.model
            else:
                provider = runtime_provider or provider
                model = runtime_model
            return {"provider": provider, "model": model}

        stored_override = (entry_dict.get("modelOverride") or "").strip()
        if stored_override:
            override_provider = (entry_dict.get("providerOverride") or "").strip() or provider or DEFAULT_PROVIDER
            parsed_override = parse_model_ref(stored_override, override_provider)
            if parsed_override:
                provider = parsed_override.provider
                model = parsed_override.model
            else:
                provider = override_provider
                model = stored_override

    return {"provider": provider, "model": model}


def archive_file_on_disk(file_path: str, reason: str) -> str:
    """
    Move a file to an archived copy with timestamp suffix.
    Mirrors TS archiveFileOnDisk().

    reason: "bak" | "reset" | "deleted"
    Returns the archived file path.
    """
    import shutil
    from datetime import datetime, timezone
    ts = datetime.now(tz=timezone.utc).isoformat().replace(":", "-")
    archived = f"{file_path}.{reason}.{ts}"
    shutil.move(file_path, archived)
    return archived


def archive_session_transcripts(
    session_id: str,
    store_path: Optional[str],
    session_file: Optional[str] = None,
    agent_id: Optional[str] = None,
    reason: str = "reset",
) -> List[str]:
    """
    Archive all transcript files for a session. Best-effort.
    Mirrors TS archiveSessionTranscripts().
    """
    archived: List[str] = []
    candidates: List[str] = []

    if session_file:
        candidates.append(session_file)
    if store_path and session_id:
        candidates.append(str(Path(store_path).parent / f"{session_id}.jsonl"))
    if session_id:
        # Legacy location
        import os
        home = Path.home()
        candidates.append(str(home / ".openclaw" / "sessions" / f"{session_id}.jsonl"))

    for candidate in candidates:
        p = Path(candidate)
        if not p.exists():
            continue
        try:
            archived.append(archive_file_on_disk(str(p), reason))
        except Exception:
            pass

    return archived


def read_session_preview_items(
    session_id: str,
    store_path: str,
    session_file: Optional[str] = None,
    limit: int = 12,
    max_chars: int = 240
) -> List[SessionPreviewItem]:
    """
    Read session preview items from transcript
    
    Args:
        session_id: Session identifier
        store_path: Path to sessions.json
        session_file: Optional session file path
        limit: Number of messages to preview
        max_chars: Maximum characters per message
        
    Returns:
        List of preview items
    """
    messages = read_transcript_preview(
        session_id,
        store_path,
        session_file,
        limit=limit,
        max_chars=max_chars
    )
    
    items: List[SessionPreviewItem] = []
    for msg in messages:
        role = msg.get("role", "other")
        if role not in ("user", "assistant", "tool", "system"):
            role = "other"
        
        text = msg.get("content", "")
        items.append(SessionPreviewItem(role=role, text=text))
    
    return items
