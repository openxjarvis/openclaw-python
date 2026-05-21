"""
Agent route resolution with binding matching.

Matches openclaw/src/routing/resolve-route.ts
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Set

from .bindings import list_bindings
from .session_key import (
    DEFAULT_ACCOUNT_ID,
    DEFAULT_AGENT_ID,
    DEFAULT_MAIN_KEY,
    build_agent_main_session_key,
    build_agent_peer_session_key,
    normalize_account_id,
    normalize_agent_id,
    sanitize_agent_id,
)

logger = logging.getLogger(__name__)

MAX_EVALUATED_BINDINGS_CACHE_KEYS = 2000

# Per-config binding evaluation cache: id(cfg) → (bindings_id, dict[cache_key → list])
# We use id(cfg) as a proxy for WeakMap behaviour (cleared when cfg is replaced).
_evaluated_bindings_cache: Dict[int, tuple] = {}


@dataclass
class RoutePeer:
    """Peer information for routing."""
    kind: Literal["direct", "dm", "group", "channel"]
    id: str


@dataclass
class ResolvedAgentRoute:
    """Resolved agent route."""
    agent_id: str
    channel: str
    account_id: str
    session_key: str
    main_session_key: str
    matched_by: Literal[
        "binding.peer",
        "binding.peer.parent",
        "binding.guild+roles",
        "binding.guild",
        "binding.team",
        "binding.account",
        "binding.channel",
        "default",
    ]


# Re-export constants (matches TS `export { DEFAULT_ACCOUNT_ID, DEFAULT_AGENT_ID }`)
__all__ = [
    "RoutePeer",
    "ResolvedAgentRoute",
    "resolve_agent_route",
    "build_agent_session_key",
    "DEFAULT_ACCOUNT_ID",
    "DEFAULT_AGENT_ID",
]


def normalize_token(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def normalize_id(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(int(value)).strip()
    return ""


def _normalize_account_id_local(value: Optional[str]) -> str:
    trimmed = (value or "").strip()
    return trimmed if trimmed else DEFAULT_ACCOUNT_ID


def _matches_account_id(match_pattern: Optional[str], actual: str) -> bool:
    trimmed = (match_pattern or "").strip()
    if not trimmed:
        return actual == DEFAULT_ACCOUNT_ID
    if trimmed == "*":
        return True
    return trimmed == actual


def _matches_channel(match: Optional[dict], channel: str) -> bool:
    if not match:
        return False
    key = normalize_token(match.get("channel") if isinstance(match, dict) else getattr(match, "channel", None))
    return bool(key) and key == channel


# ---------------------------------------------------------------------------
# Normalized binding types (mirrors TS NormalizedBindingMatch)
# ---------------------------------------------------------------------------

def _normalize_peer_constraint(peer) -> dict:
    """Returns {"state": "none"|"invalid"|"valid", "kind": str, "id": str}"""
    if not peer:
        return {"state": "none"}
    kind_raw = peer.get("kind") if isinstance(peer, dict) else getattr(peer, "kind", None)
    id_raw = peer.get("id") if isinstance(peer, dict) else getattr(peer, "id", None)
    kind = normalize_token(kind_raw)
    peer_id = normalize_id(id_raw)
    if not kind or not peer_id:
        return {"state": "invalid"}
    return {"state": "valid", "kind": kind, "id": peer_id}


def _normalize_binding_match(match) -> dict:
    """Returns NormalizedBindingMatch dict."""
    if match is None:
        match = {}
    get = (lambda k: match.get(k) if isinstance(match, dict) else getattr(match, k, None))
    raw_roles = get("roles")
    roles: Optional[List[str]] = None
    if isinstance(raw_roles, list) and raw_roles:
        roles = raw_roles
    return {
        "account_pattern": (get("accountId") or "").strip(),
        "peer": _normalize_peer_constraint(get("peer")),
        "guild_id": normalize_id(get("guildId")) or None,
        "team_id": normalize_id(get("teamId")) or None,
        "roles": roles,
    }


def _matches_binding_scope(match: dict, scope_peer: Optional[RoutePeer], guild_id: str, team_id: str, member_role_ids: Set[str]) -> bool:
    """Check if a normalized binding match satisfies the current scope."""
    peer_constraint = match["peer"]
    if peer_constraint["state"] == "invalid":
        return False
    if peer_constraint["state"] == "valid":
        peer_id = peer_constraint.get("id", "")
        peer_kind = peer_constraint.get("kind", "")
        if peer_id == "*":
            # Wildcard peer: only check kind match (with group/channel alias)
            if scope_peer is not None and peer_kind:
                # Allow group ↔ channel aliases
                aliases = {frozenset({"group", "channel"})}
                kind_match = scope_peer.kind == peer_kind
                if not kind_match:
                    for alias_set in aliases:
                        if scope_peer.kind in alias_set and peer_kind in alias_set:
                            kind_match = True
                            break
                if not kind_match:
                    return False
        else:
            if (
                scope_peer is None
                or scope_peer.kind != peer_kind
                or scope_peer.id != peer_id
            ):
                return False
    if match["guild_id"] and match["guild_id"] != guild_id:
        return False
    if match["team_id"] and match["team_id"] != team_id:
        return False
    if match["roles"]:
        return any(role in member_role_ids for role in match["roles"])
    return True


# ---------------------------------------------------------------------------
# Per-config evaluated bindings cache
# ---------------------------------------------------------------------------

def _get_evaluated_bindings(cfg: object, channel: str, account_id: str) -> List[dict]:
    """
    Get evaluated (pre-filtered) bindings for a channel+account pair.

    Cache is keyed by id(cfg) and invalidated when cfg.bindings reference changes.
    Max 2000 entries; cleared on overflow (matching TS behaviour).
    """
    cfg_id = id(cfg)
    bindings_ref = id(getattr(cfg, "bindings", None) if not isinstance(cfg, dict) else cfg.get("bindings"))

    entry = _evaluated_bindings_cache.get(cfg_id)
    if entry is None or entry[0] != bindings_ref:
        # Invalidate cache for this cfg
        entry = (bindings_ref, {})
        _evaluated_bindings_cache[cfg_id] = entry

    cache_map: dict = entry[1]
    cache_key = f"{channel}\t{account_id}"
    hit = cache_map.get(cache_key)
    if hit is not None:
        return hit

    evaluated = []
    for binding in list_bindings(cfg):
        if not binding or not isinstance(binding, dict):
            continue
        match = binding.get("match")
        if not _matches_channel(match, channel):
            continue
        match_dict = match if isinstance(match, dict) else {}
        if not _matches_account_id(match_dict.get("accountId"), account_id):
            continue
        evaluated.append({
            "binding": binding,
            "match": _normalize_binding_match(match),
        })

    cache_map[cache_key] = evaluated
    if len(cache_map) > MAX_EVALUATED_BINDINGS_CACHE_KEYS:
        cache_map.clear()
        cache_map[cache_key] = evaluated

    return evaluated


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def build_agent_session_key(
    agent_id: str,
    channel: str,
    account_id: Optional[str] = None,
    peer: Optional[dict] = None,
    dm_scope: str = "main",
    identity_links: Optional[dict] = None,
) -> str:
    """
    Build agent session key from routing parameters.

    Matches TS buildAgentSessionKey() in resolve-route.ts.
    """
    channel_norm = normalize_token(channel) or "unknown"
    peer_kind = peer.get("kind", "direct") if peer else "direct"
    peer_id = normalize_id(peer.get("id") if peer else None) or None
    return build_agent_peer_session_key(
        agent_id=agent_id,
        channel=channel_norm,
        account_id=account_id,
        peer_kind=peer_kind,
        peer_id=peer_id,
        dm_scope=dm_scope,
        identity_links=identity_links,
    )


def _list_agents(cfg: object) -> List[dict]:
    if hasattr(cfg, "agents") and cfg.agents:  # type: ignore[union-attr]
        agents = getattr(cfg.agents, "list", None)  # type: ignore[union-attr]
        if isinstance(agents, list):
            return agents
    if isinstance(cfg, dict):
        agents = cfg.get("agents") or {}
        if isinstance(agents, dict):
            lst = agents.get("list", [])
            if isinstance(lst, list):
                return lst
    return []


def _resolve_default_agent_id(cfg: object) -> str:
    """
    Resolve default agent ID from config.
    
    Enhanced to support agents.list[].default (mirrors TS resolveDefaultAgentId)
    
    Priority:
    1. Agent with default=True in agents.list[]
    2. First agent in agents.list[]
    3. Fallback to "main"
    """
    try:
        from openclaw.agents.agent_scope import resolve_default_agent_id
        return resolve_default_agent_id(cfg)
    except Exception as e:
        logger.debug(f"Failed to use agent_scope.resolve_default_agent_id: {e}")
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
        return DEFAULT_AGENT_ID


def _pick_first_existing_agent_id(cfg: object, agent_id: str) -> str:
    trimmed = (agent_id or "").strip()
    if not trimmed:
        return sanitize_agent_id(_resolve_default_agent_id(cfg))
    normalized = normalize_agent_id(trimmed)
    agents = _list_agents(cfg)
    if not agents:
        return sanitize_agent_id(trimmed)
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        aid = agent.get("id", "")
        if normalize_agent_id(aid) == normalized:
            return sanitize_agent_id(aid)
    return sanitize_agent_id(_resolve_default_agent_id(cfg))


# ---------------------------------------------------------------------------
# Main routing function
# ---------------------------------------------------------------------------

def resolve_agent_route(
    cfg: object = None,
    channel: str = "",
    account_id: Optional[str] = None,
    peer: Optional[RoutePeer] = None,
    parent_peer: Optional[RoutePeer] = None,
    guild_id: Optional[str] = None,
    team_id: Optional[str] = None,
    member_role_ids: Optional[List[str]] = None,
    *,
    config: object = None,  # alias for cfg (used in tests and older call sites)
) -> ResolvedAgentRoute:
    """
    Resolve agent and session key via binding hierarchy.

    Matching order (mirrors TS resolveAgentRoute tiers):
    1. binding.peer          — exact peer ID match
    2. binding.peer.parent   — parent peer for threads
    3. binding.guild+roles   — Discord guild + member role intersection
    4. binding.guild         — Discord guild (no role constraint)
    5. binding.team          — Slack workspace
    6. binding.account       — channel account (specific account ID)
    7. binding.channel       — channel-wide (accountId="*")
    8. default               — fallback to default agent

    Matches TS resolveAgentRoute().
    """
    # Support `config` as an alias for `cfg` (legacy / test call sites)
    if cfg is None and config is not None:
        cfg = config

    channel_norm = normalize_token(channel)
    account_id_norm = _normalize_account_id_local(account_id)

    # Normalize peer
    def _norm_peer(p) -> Optional[RoutePeer]:
        if p is None:
            return None
        if isinstance(p, dict):
            return RoutePeer(kind=p.get("kind", "direct"), id=normalize_id(p.get("id", "")))
        return RoutePeer(kind=p.kind, id=normalize_id(p.id))

    peer_norm = _norm_peer(peer)
    parent_peer_norm = _norm_peer(parent_peer)
    guild_id_norm = normalize_id(guild_id)
    team_id_norm = normalize_id(team_id)
    member_role_ids_list = member_role_ids or []
    member_role_id_set: Set[str] = set(member_role_ids_list)

    bindings = _get_evaluated_bindings(cfg, channel_norm, account_id_norm)

    # Get dmScope and identityLinks from config — default "main" mirrors TS cfg.session?.dmScope ?? "main"
    dm_scope = "main"
    identity_links = None
    if hasattr(cfg, "session") and cfg.session:  # type: ignore[union-attr]
        dm_scope = getattr(cfg.session, "dmScope", "main") or "main"  # type: ignore[union-attr]
        identity_links = getattr(cfg.session, "identityLinks", None)  # type: ignore[union-attr]
    elif isinstance(cfg, dict):
        session = cfg.get("session") or {}
        dm_scope = session.get("dmScope", "main") or "main"
        identity_links = session.get("identityLinks")

    def choose(agent_id: str, matched_by: str) -> ResolvedAgentRoute:
        resolved_agent_id = _pick_first_existing_agent_id(cfg, agent_id)
        session_key = build_agent_session_key(
            agent_id=resolved_agent_id,
            channel=channel_norm,
            account_id=account_id_norm,
            peer={"kind": peer_norm.kind, "id": peer_norm.id} if peer_norm else None,
            dm_scope=dm_scope,
            identity_links=identity_links,
        ).lower()
        main_session_key = build_agent_main_session_key(
            agent_id=resolved_agent_id,
            main_key=DEFAULT_MAIN_KEY,
        ).lower()
        return ResolvedAgentRoute(
            agent_id=resolved_agent_id,
            channel=channel_norm,
            account_id=account_id_norm,
            session_key=session_key,
            main_session_key=main_session_key,
            matched_by=matched_by,  # type: ignore[arg-type]
        )

    def has_guild_constraint(match: dict) -> bool:
        return bool(match.get("guild_id"))

    def has_roles_constraint(match: dict) -> bool:
        return bool(match.get("roles"))

    def has_team_constraint(match: dict) -> bool:
        return bool(match.get("team_id"))

    def _is_wildcard_peer(e: dict) -> bool:
        """True if this binding has a wildcard peer (peer.id == '*')."""
        peer = e["match"]["peer"]
        return peer["state"] == "valid" and peer.get("id") == "*"

    def _wildcard_kind_matches(binding_kind: str, peer: Optional[RoutePeer]) -> bool:
        """True if the binding's peer kind matches the actual peer kind (with group/channel alias)."""
        if peer is None:
            return True
        if not binding_kind:
            return True
        if binding_kind == peer.kind:
            return True
        aliases = {frozenset({"group", "channel"})}
        for alias_set in aliases:
            if binding_kind in alias_set and peer.kind in alias_set:
                return True
        return False

    # Tiers — same order as TS
    tiers = [
        {
            "matched_by": "binding.peer",
            "enabled": peer_norm is not None,
            "scope_peer": peer_norm,
            "predicate": lambda e: e["match"]["peer"]["state"] == "valid" and e["match"]["peer"].get("id") != "*",
        },
        {
            "matched_by": "binding.peer.parent",
            "enabled": parent_peer_norm is not None and bool(parent_peer_norm.id),
            "scope_peer": parent_peer_norm if (parent_peer_norm and parent_peer_norm.id) else None,
            "predicate": lambda e: e["match"]["peer"]["state"] == "valid" and e["match"]["peer"].get("id") != "*",
        },
        {
            "matched_by": "binding.guild+roles",
            "enabled": bool(guild_id_norm and member_role_ids_list),
            "scope_peer": peer_norm,
            "predicate": lambda e: has_guild_constraint(e["match"]) and has_roles_constraint(e["match"]),
        },
        {
            "matched_by": "binding.guild",
            "enabled": bool(guild_id_norm),
            "scope_peer": peer_norm,
            "predicate": lambda e: has_guild_constraint(e["match"]) and not has_roles_constraint(e["match"]),
        },
        {
            "matched_by": "binding.team",
            "enabled": bool(team_id_norm),
            "scope_peer": peer_norm,
            "predicate": lambda e: has_team_constraint(e["match"]),
        },
        {
            "matched_by": "binding.account",
            "enabled": True,
            "scope_peer": peer_norm,
            "predicate": lambda e: e["match"]["account_pattern"] != "*",
        },
        {
            "matched_by": "binding.channel",
            "enabled": True,
            "scope_peer": peer_norm,
            "predicate": lambda e: e["match"]["account_pattern"] == "*" and not _is_wildcard_peer(e),
        },
        {
            "matched_by": "binding.peer",
            "enabled": peer_norm is not None,
            "scope_peer": peer_norm,
            "predicate": lambda e: _is_wildcard_peer(e) and _wildcard_kind_matches(e["match"]["peer"].get("kind", ""), peer_norm),
        },
    ]

    for tier in tiers:
        if not tier["enabled"]:
            continue
        scope_peer: Optional[RoutePeer] = tier["scope_peer"]
        for candidate in bindings:
            if not tier["predicate"](candidate):
                continue
            if _matches_binding_scope(
                candidate["match"],
                scope_peer,
                guild_id_norm,
                team_id_norm,
                member_role_id_set,
            ):
                agent_id_raw = candidate["binding"].get("agentId", DEFAULT_AGENT_ID)
                return choose(agent_id_raw, tier["matched_by"])

    return choose(_resolve_default_agent_id(cfg), "default")
