"""Session access control and agent-to-agent policy

Fully aligned with TypeScript openclaw/src/agents/tools/sessions-access.ts

Provides:
- Agent-to-agent routing policy (enabled, allow patterns, ping-pong limits)
- Session visibility guards (self, tree, agent, all)
- Access control for cross-agent session operations
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal

logger = logging.getLogger(__name__)

# Type aliases
SessionToolsVisibility = Literal["self", "tree", "agent", "all"]
SessionAccessAction = Literal["history", "send", "list", "status", "kill"]


@dataclass
class AgentToAgentPolicy:
    """
    Agent-to-agent routing policy.
    
    Mirrors TS AgentToAgentPolicy from sessions-access.ts lines 11-15
    """
    enabled: bool
    allow_patterns: list[str]
    
    def matches_allow(self, agent_id: str) -> bool:
        """
        Check if agent ID matches allow patterns.
        
        Mirrors TS matchesAllow() lines 94-113
        
        Supports:
        - "*": Match all
        - Exact match: "work"
        - Wildcard: "work*", "*dev"
        """
        if not self.allow_patterns:
            return True
        
        for pattern in self.allow_patterns:
            pattern_str = str(pattern).strip()
            if not pattern_str:
                continue
            
            # "*" matches all
            if pattern_str == "*":
                return True
            
            # Exact match
            if "*" not in pattern_str:
                if pattern_str == agent_id:
                    return True
                continue
            
            # Wildcard pattern
            # Escape special regex chars except *
            escaped = re.escape(pattern_str)
            # Replace escaped \* with .*
            regex_pattern = escaped.replace(r'\*', '.*')
            
            if re.match(f"^{regex_pattern}$", agent_id, re.IGNORECASE):
                return True
        
        return False
    
    def is_allowed(self, requester_agent_id: str, target_agent_id: str) -> bool:
        """
        Check if communication is allowed between agents.
        
        Mirrors TS isAllowed() lines 114-123
        """
        # Same agent always allowed
        if requester_agent_id == target_agent_id:
            return True
        
        # Must be enabled for cross-agent
        if not self.enabled:
            return False
        
        # Both agents must match allow patterns
        return self.matches_allow(requester_agent_id) and self.matches_allow(target_agent_id)


@dataclass
class SessionAccessResult:
    """
    Session access check result.
    
    Mirrors TS SessionAccessResult from sessions-access.ts lines 19-21
    """
    allowed: bool
    error: str | None = None
    status: str | None = None


def create_agent_to_agent_policy(cfg: Any) -> AgentToAgentPolicy:
    """
    Create agent-to-agent policy from config.
    
    Mirrors TS createAgentToAgentPolicy() from sessions-access.ts lines 90-124
    
    Config structure:
      tools:
        agentToAgent:
          enabled: true
          allow: ["*"]  # or ["work*", "home"]
    
    Args:
        cfg: Configuration object or dict
    
    Returns:
        AgentToAgentPolicy instance
    """
    enabled = False
    allow_patterns: list[str] = []
    
    # Parse config - support both object attributes and dict access (mirrors TS cfg.tools?.agentToAgent)
    routing_a2a = None
    
    # Try dict access first (most common case when config is loaded as dict)
    if isinstance(cfg, dict):
        tools = cfg.get('tools', {})
        if isinstance(tools, dict):
            routing_a2a = tools.get('agentToAgent')
    # Fallback to object attribute access
    elif hasattr(cfg, 'tools'):
        tools = cfg.tools
        if hasattr(tools, 'agentToAgent'):
            routing_a2a = tools.agentToAgent
        elif isinstance(tools, dict):
            routing_a2a = tools.get('agentToAgent')
    
    if routing_a2a:
        # Support both dict and object access
        if isinstance(routing_a2a, dict):
            enabled = routing_a2a.get('enabled', False) is True
            allow_raw = routing_a2a.get('allow', [])
        else:
            enabled = getattr(routing_a2a, 'enabled', False) is True
            allow_raw = getattr(routing_a2a, 'allow', [])
        
        if isinstance(allow_raw, list):
            allow_patterns = allow_raw
    
    return AgentToAgentPolicy(
        enabled=enabled,
        allow_patterns=allow_patterns,
    )


def resolve_session_tools_visibility(cfg: Any) -> SessionToolsVisibility:
    """
    Resolve session tools visibility from config.
    
    Mirrors TS resolveSessionToolsVisibility() from sessions-access.ts lines 23-31
    
    Args:
        cfg: Configuration object or dict
    
    Returns:
        Visibility level (default: "tree")
    """
    raw = None
    
    # Support both dict and object access (mirrors TS cfg.tools?.sessions?.visibility)
    if isinstance(cfg, dict):
        tools = cfg.get('tools', {})
        if isinstance(tools, dict):
            sessions = tools.get('sessions', {})
            if isinstance(sessions, dict):
                raw = sessions.get('visibility')
    elif hasattr(cfg, 'tools'):
        tools = cfg.tools
        if hasattr(tools, 'sessions'):
            sessions = tools.sessions
            raw = getattr(sessions, 'visibility', None)
        elif isinstance(tools, dict):
            sessions = tools.get('sessions', {})
            if isinstance(sessions, dict):
                raw = sessions.get('visibility')
    
    if not raw:
        return "tree"
    
    value = str(raw).strip().lower()
    
    if value in ("self", "tree", "agent", "all"):
        return value  # type: ignore
    
    return "tree"


def resolve_effective_session_tools_visibility(
    cfg: Any,
    sandboxed: bool,
) -> SessionToolsVisibility:
    """
    Resolve effective session tools visibility considering sandbox clamp.
    
    Mirrors TS resolveEffectiveSessionToolsVisibility() from sessions-access.ts lines 33-46
    
    Args:
        cfg: Configuration object
        sandboxed: Whether session is sandboxed
    
    Returns:
        Effective visibility level
    """
    visibility = resolve_session_tools_visibility(cfg)
    
    if not sandboxed:
        return visibility
    
    # Get sandbox clamp setting
    sandbox_clamp = "spawned"
    if hasattr(cfg, 'agents') and cfg.agents:
        if hasattr(cfg.agents, 'defaults') and cfg.agents.defaults:
            defaults = cfg.agents.defaults
            if hasattr(defaults, 'sandbox') and defaults.sandbox:
                sandbox = defaults.sandbox
                if hasattr(sandbox, 'sessionToolsVisibility'):
                    sandbox_clamp = sandbox.sessionToolsVisibility or "spawned"
    
    # Clamp to "tree" if sandbox restricts to "spawned"
    if sandbox_clamp == "spawned" and visibility != "tree":
        return "tree"
    
    return visibility


def _action_prefix(action: SessionAccessAction) -> str:
    """Get action prefix for error messages (mirrors TS lines 126-134)"""
    if action == "history":
        return "Session history"
    if action == "send":
        return "Session send"
    if action == "status":
        return "Session status"
    if action == "kill":
        return "Session kill"
    return "Session list"


def _a2a_disabled_message(action: SessionAccessAction) -> str:
    """Get A2A disabled error message (mirrors TS lines 136-144)"""
    if action == "history":
        return "Agent-to-agent history is disabled. Set tools.agentToAgent.enabled=true to allow cross-agent access."
    if action == "send":
        return "Agent-to-agent messaging is disabled. Set tools.agentToAgent.enabled=true to allow cross-agent sends."
    return "Agent-to-agent listing is disabled. Set tools.agentToAgent.enabled=true to allow cross-agent visibility."


def _a2a_denied_message(action: SessionAccessAction) -> str:
    """Get A2A denied error message (mirrors TS lines 146-154)"""
    if action == "history":
        return "Agent-to-agent history denied by tools.agentToAgent.allow."
    if action == "send":
        return "Agent-to-agent messaging denied by tools.agentToAgent.allow."
    return "Agent-to-agent listing denied by tools.agentToAgent.allow."


def _cross_visibility_message(action: SessionAccessAction) -> str:
    """Get cross-visibility error message (mirrors TS lines 156-164)"""
    if action == "history":
        return "Session history visibility is restricted. Set tools.sessions.visibility=all to allow cross-agent access."
    if action == "send":
        return "Session send visibility is restricted. Set tools.sessions.visibility=all to allow cross-agent access."
    return "Session list visibility is restricted. Set tools.sessions.visibility=all to allow cross-agent access."


def _self_visibility_message(action: SessionAccessAction) -> str:
    """Get self-visibility error message (mirrors TS lines 166-168)"""
    return f"{_action_prefix(action)} visibility is restricted to the current session (tools.sessions.visibility=self)."


def _tree_visibility_message(action: SessionAccessAction) -> str:
    """Get tree-visibility error message (mirrors TS lines 170-172)"""
    return f"{_action_prefix(action)} visibility is restricted to the current session tree (tools.sessions.visibility=tree)."


async def create_session_visibility_guard(
    action: SessionAccessAction,
    requester_session_key: str,
    visibility: SessionToolsVisibility,
    a2a_policy: AgentToAgentPolicy,
) -> dict[str, Callable[[str], SessionAccessResult]]:
    """
    Create session visibility guard function.
    
    Mirrors TS createSessionVisibilityGuard() from sessions-access.ts lines 174-240
    
    Visibility levels:
    - "self": Only current session
    - "tree": Current session + spawned children (default)
    - "agent": All sessions for current agent
    - "all": All sessions (cross-agent requires agentToAgent.enabled)
    
    Args:
        action: Action being performed
        requester_session_key: Requester session key
        visibility: Visibility level
        a2a_policy: Agent-to-agent policy
    
    Returns:
        Dict with 'check' function
    """
    from openclaw.routing.session_key import resolve_agent_id_from_session_key
    
    requester_agent_id = resolve_agent_id_from_session_key(requester_session_key)
    
    # For "tree" visibility, collect spawned session keys
    spawned_keys: set[str] | None = None
    if visibility == "tree":
        try:
            # List spawned sessions via registry
            from openclaw.agents.subagent_registry import get_global_registry
            
            registry = get_global_registry()
            runs = registry.list_runs_for_requester(requester_session_key)
            spawned_keys = {run.child_session_key for run in runs}
        except Exception as e:
            logger.debug(f"Failed to list spawned sessions: {e}")
            spawned_keys = set()
    
    def check(target_session_key: str) -> SessionAccessResult:
        """Check if access to target session is allowed"""
        target_agent_id = resolve_agent_id_from_session_key(target_session_key)
        is_cross_agent = target_agent_id != requester_agent_id
        
        # Cross-agent access
        if is_cross_agent:
            # Must be visibility="all" for cross-agent
            if visibility != "all":
                return SessionAccessResult(
                    allowed=False,
                    status="forbidden",
                    error=_cross_visibility_message(action),
                )
            
            # Must be enabled
            if not a2a_policy.enabled:
                return SessionAccessResult(
                    allowed=False,
                    status="forbidden",
                    error=_a2a_disabled_message(action),
                )
            
            # Must be allowed by patterns
            if not a2a_policy.is_allowed(requester_agent_id, target_agent_id):
                return SessionAccessResult(
                    allowed=False,
                    status="forbidden",
                    error=_a2a_denied_message(action),
                )
            
            return SessionAccessResult(allowed=True)
        
        # Same agent: check visibility constraints
        if visibility == "self" and target_session_key != requester_session_key:
            return SessionAccessResult(
                allowed=False,
                status="forbidden",
                error=_self_visibility_message(action),
            )
        
        if (
            visibility == "tree"
            and target_session_key != requester_session_key
            and spawned_keys is not None
            and target_session_key not in spawned_keys
        ):
            return SessionAccessResult(
                allowed=False,
                status="forbidden",
                error=_tree_visibility_message(action),
            )
        
        # visibility=="agent" or "all" allows same-agent access
        return SessionAccessResult(allowed=True)
    
    return {"check": check}


def resolve_ping_pong_turns(cfg: Any) -> int:
    """
    Resolve max ping-pong turns from config.
    
    Mirrors TS resolvePingPongTurns() from sessions-send-helpers.ts
    
    Args:
        cfg: Configuration object
    
    Returns:
        Max ping-pong turns (0-5, default: 5)
    """
    raw = None
    
    if hasattr(cfg, 'tools') and cfg.tools:
        a2a = getattr(cfg.tools, 'agentToAgent', None)
        if a2a:
            raw = getattr(a2a, 'maxPingPongTurns', None)
    
    if isinstance(raw, int) and 0 <= raw <= 5:
        return raw
    
    return 5
