"""
Binding utilities for agent routing — matches openclaw/src/routing/bindings.ts
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .session_key import normalize_account_id, normalize_agent_id


def _normalize_binding_channel_id(raw: Optional[str]) -> Optional[str]:
    """Normalize a channel ID for binding lookup."""
    fallback = (raw or "").strip().lower()
    return fallback if fallback else None


def _resolve_default_agent_id_from_cfg(cfg: object) -> str:
    """
    Resolve the default agent ID from config.
    
    Enhanced to support agents.list[].default (mirrors TS resolveDefaultAgentId)
    """
    try:
        from openclaw.agents.agent_scope import resolve_default_agent_id
        return resolve_default_agent_id(cfg)
    except Exception:
        # Fallback to legacy behavior
        if hasattr(cfg, "agents") and cfg.agents:  # type: ignore[union-attr]
            default = getattr(cfg.agents, "default", None)  # type: ignore[union-attr]
            if default:
                return str(default).strip()
        if isinstance(cfg, dict):
            agents = cfg.get("agents") or {}
            if isinstance(agents, dict):
                default = agents.get("default")
                if default:
                    return str(default).strip()
        return "main"


def list_bindings(cfg: object) -> List[dict]:
    """
    List all agent bindings from config.

    Matches TS listBindings(). Bindings may be at cfg.bindings or cfg.session.bindings.
    """
    # TS accesses cfg.bindings (top-level array)
    if hasattr(cfg, "bindings"):
        bindings = cfg.bindings  # type: ignore[union-attr]
        if isinstance(bindings, list):
            return bindings
    if isinstance(cfg, dict):
        # Top-level bindings
        top_bindings = cfg.get("bindings")
        if isinstance(top_bindings, list):
            return top_bindings
        # Fallback: session.bindings (common config shape)
        session = cfg.get("session")
        if isinstance(session, dict):
            session_bindings = session.get("bindings")
            if isinstance(session_bindings, list):
                return session_bindings
    return []


def _resolve_normalized_binding_match(binding: dict) -> Optional[dict]:
    """
    Resolve normalized binding match fields.

    Returns None if binding is invalid or missing required fields.

    Matches TS resolveNormalizedBindingMatch().
    """
    if not binding or not isinstance(binding, dict):
        return None
    match = binding.get("match")
    if not match or not isinstance(match, dict):
        return None
    channel_id = _normalize_binding_channel_id(match.get("channel"))
    if not channel_id:
        return None
    account_id_raw = match.get("accountId", "")
    if not isinstance(account_id_raw, str):
        account_id_raw = ""
    account_id = account_id_raw.strip()
    if not account_id or account_id == "*":
        return None
    return {
        "agent_id": normalize_agent_id(binding.get("agentId", "")),
        "account_id": normalize_account_id(account_id),
        "channel_id": channel_id,
    }


def list_bound_account_ids(cfg: object, channel_id: str) -> List[str]:
    """
    List all account IDs bound to a channel via bindings.

    Returns sorted list of unique account IDs.

    Matches TS listBoundAccountIds().
    """
    normalized_channel = _normalize_binding_channel_id(channel_id)
    if not normalized_channel:
        return []
    ids: set = set()
    for binding in list_bindings(cfg):
        resolved = _resolve_normalized_binding_match(binding)
        if not resolved or resolved["channel_id"] != normalized_channel:
            continue
        ids.add(resolved["account_id"])
    return sorted(ids)


def resolve_default_agent_bound_account_id(
    cfg: object, channel_id: str
) -> Optional[str]:
    """
    Resolve the account ID bound to the default agent for a channel.

    Returns None if no matching binding found.

    Matches TS resolveDefaultAgentBoundAccountId().
    """
    normalized_channel = _normalize_binding_channel_id(channel_id)
    if not normalized_channel:
        return None
    default_agent_id = normalize_agent_id(_resolve_default_agent_id_from_cfg(cfg))
    for binding in list_bindings(cfg):
        resolved = _resolve_normalized_binding_match(binding)
        if (
            not resolved
            or resolved["channel_id"] != normalized_channel
            or resolved["agent_id"] != default_agent_id
        ):
            continue
        return resolved["account_id"]
    return None


def build_channel_account_bindings(cfg: object) -> Dict[str, Dict[str, List[str]]]:
    """
    Build a channel → agent → [accountId] mapping from config bindings.

    Matches TS buildChannelAccountBindings().
    """
    result: Dict[str, Dict[str, List[str]]] = {}
    for binding in list_bindings(cfg):
        resolved = _resolve_normalized_binding_match(binding)
        if not resolved:
            continue
        by_agent = result.setdefault(resolved["channel_id"], {})
        account_list = by_agent.setdefault(resolved["agent_id"], [])
        if resolved["account_id"] not in account_list:
            account_list.append(resolved["account_id"])
    return result


def resolve_preferred_account_id(
    account_ids: List[str],
    default_account_id: str,
    bound_accounts: List[str],
) -> str:
    """
    Pick the preferred account ID from available options.

    Prefers the first bound account, falls back to default.

    Matches TS resolvePreferredAccountId().
    """
    if bound_accounts:
        return bound_accounts[0]
    return default_account_id


def _get_current_cfg():
    """Load current config; returns None on failure."""
    try:
        from openclaw.gateway.config_service import get_config_service as _gcs
        svc = _gcs()
        return svc.get_config() if svc else None
    except Exception:
        return None


def resolve_first_bound_account_id(
    agent_id: str,
    channel: str,
    peer_kind: Optional[str] = None,
) -> Optional[str]:
    """Find the first account ID bound to an agent for a given channel and optional peer kind.

    Mirrors TS resolveFirstBoundAccountId().

    Args:
        agent_id: Agent ID to look for (normalized).
        channel: Channel name.
        peer_kind: Optional peer kind filter (e.g. "group", "channel").

    Returns:
        First matching account ID, or None if not found.
    """
    cfg = _get_current_cfg()
    if cfg is None:
        return None

    normalized_channel = _normalize_binding_channel_id(channel)
    normalized_agent = normalize_agent_id(agent_id)
    if not normalized_channel or not normalized_agent:
        return None

    for binding in list_bindings(cfg):
        resolved = _resolve_normalized_binding_match(binding)
        if not resolved:
            continue
        if resolved["channel_id"] != normalized_channel:
            continue
        if resolved["agent_id"] != normalized_agent:
            continue
        if peer_kind is not None:
            # Check peer kind in binding match
            match = binding.get("match", {})
            if isinstance(match, dict):
                peer = match.get("peer", {})
                binding_peer_kind = (peer.get("kind", "") if isinstance(peer, dict) else "").lower()
                # Allow group ↔ channel aliasing
                if not peer_kind_matches(binding_peer_kind, peer_kind.lower()):
                    continue
        return resolved["account_id"]
    return None


def peer_kind_matches(binding_kind: str, actual_kind: str) -> bool:
    """Check if a binding peer kind matches the actual peer kind.

    Handles group ↔ channel aliases (Discord uses "channel", Slack uses "group").

    Args:
        binding_kind: Peer kind from binding config.
        actual_kind: Actual peer kind from incoming message.

    Returns:
        True if they match (including aliases).
    """
    if not binding_kind:
        return True  # no constraint = wildcard
    if binding_kind == actual_kind:
        return True
    aliases = {frozenset({"group", "channel"})}
    for alias_set in aliases:
        if binding_kind in alias_set and actual_kind in alias_set:
            return True
    return False


__all__ = [
    "list_bindings",
    "list_bound_account_ids",
    "resolve_default_agent_bound_account_id",
    "build_channel_account_bindings",
    "resolve_preferred_account_id",
    "resolve_first_bound_account_id",
    "peer_kind_matches",
]
