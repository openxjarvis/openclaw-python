"""
Tool policy for subagents and sandbox

Matches TypeScript src/agents/pi-tools.policy.ts

Determines which tools are allowed/denied based on:
- Subagent depth (orchestrator vs leaf)
- Sandbox restrictions
- User configuration
"""
from __future__ import annotations

from typing import Literal, Optional

# Default max spawn depth (matches TS DEFAULT_SUBAGENT_MAX_SPAWN_DEPTH)
DEFAULT_SUBAGENT_MAX_SPAWN_DEPTH = 1

# Tools always denied for subagents (matches TS SUBAGENT_TOOL_DENY_ALWAYS)
# Lines 46-60 in pi-tools.policy.ts
SUBAGENT_TOOL_DENY_ALWAYS = [
    # System admin - dangerous from subagent
    "gateway",
    "agents_list",
    # Interactive setup - not a task
    "whatsapp_login",
    # Status/scheduling - main agent coordinates
    "session_status",
    "cron",
    # Memory - pass relevant info in spawn prompt instead
    "memory_search",
    "memory_get",
    # Direct session sends - subagents communicate through announce chain
    "sessions_send",
]

# Additional tools denied for leaf subagents (matches TS SUBAGENT_TOOL_DENY_LEAF)
# Lines 66 in pi-tools.policy.ts
SUBAGENT_TOOL_DENY_LEAF = [
    "sessions_list",
    "sessions_history",
    "sessions_spawn",
]


def normalize_tool_name(name: str) -> str:
    """
    Normalize tool name for matching.
    
    Args:
        name: Tool name
    
    Returns:
        Normalized tool name (lowercase, trimmed)
    """
    return name.strip().lower()


def resolve_subagent_deny_list(depth: int, max_spawn_depth: int) -> list[str]:
    """
    Build the deny list for a subagent at given depth.
    
    Matches TS resolveSubagentDenyList() lines 76-84.
    
    Strategy:
    - Depth 1 with maxSpawnDepth >= 2 (orchestrator): allowed to use sessions_spawn,
      subagents, sessions_list, sessions_history so it can manage its children.
    - Depth >= maxSpawnDepth (leaf): denied sessions_spawn and
      session management tools. Still allowed subagents (for list/status visibility).
    
    Args:
        depth: Current subagent depth
        max_spawn_depth: Maximum allowed spawn depth
    
    Returns:
        List of denied tool names
    """
    is_leaf = depth >= max(1, int(max_spawn_depth))
    
    if is_leaf:
        return [*SUBAGENT_TOOL_DENY_ALWAYS, *SUBAGENT_TOOL_DENY_LEAF]
    
    # Orchestrator subagent: only deny the always-denied tools
    # sessions_spawn, subagents, sessions_list, sessions_history are allowed
    return [*SUBAGENT_TOOL_DENY_ALWAYS]


class ToolPolicy:
    """
    Tool policy with allow/deny lists.
    
    Matches TS SandboxToolPolicy type.
    """
    
    def __init__(
        self,
        allow: Optional[list[str]] = None,
        deny: Optional[list[str]] = None,
    ):
        self.allow = allow
        self.deny = deny or []
    
    def is_allowed(self, tool_name: str) -> bool:
        """
        Check if tool is allowed by this policy.
        
        Matches TS makeToolPolicyMatcher() logic lines 15-40.
        
        Args:
            tool_name: Tool name to check
        
        Returns:
            True if allowed, False if denied
        """
        normalized = normalize_tool_name(tool_name)
        
        # Check deny list first
        for denied in self.deny:
            if normalize_tool_name(denied) == normalized:
                return False
        
        # If no allow list, everything not denied is allowed
        if self.allow is None:
            return True
        
        # Check allow list
        for allowed in self.allow:
            if normalize_tool_name(allowed) == normalized:
                return True
        
        # Special case: apply_patch allowed if exec is allowed
        if normalized == "apply_patch":
            for allowed in self.allow:
                if normalize_tool_name(allowed) == "exec":
                    return True
        
        return False


def resolve_subagent_tool_policy(
    config: Optional[dict] = None,
    depth: Optional[int] = None,
) -> ToolPolicy:
    """
    Resolve tool policy for a subagent.
    
    Matches TS resolveSubagentToolPolicy() lines 86-103.
    
    Args:
        config: OpenClaw configuration
        depth: Subagent depth (None = assume depth 1)
    
    Returns:
        ToolPolicy instance
    """
    if config is None:
        config = {}
    
    # Get configuration
    configured = config.get("tools", {}).get("subagents", {}).get("tools", {})
    max_spawn_depth = (
        config.get("agents", {})
        .get("defaults", {})
        .get("subagents", {})
        .get("maxSpawnDepth", DEFAULT_SUBAGENT_MAX_SPAWN_DEPTH)
    )
    
    effective_depth = depth if isinstance(depth, int) and depth >= 0 else 1
    
    # Build base deny list
    base_deny = resolve_subagent_deny_list(effective_depth, max_spawn_depth)
    
    # Get explicit allow/alsoAllow
    allow = configured.get("allow") if isinstance(configured.get("allow"), list) else None
    also_allow = configured.get("alsoAllow") if isinstance(configured.get("alsoAllow"), list) else None
    
    explicit_allow = set()
    if allow:
        explicit_allow.update(normalize_tool_name(t) for t in allow)
    if also_allow:
        explicit_allow.update(normalize_tool_name(t) for t in also_allow)
    
    # Filter base deny by explicit allows
    deny = [
        tool_name for tool_name in base_deny
        if normalize_tool_name(tool_name) not in explicit_allow
    ]
    
    # Add explicit denies
    if isinstance(configured.get("deny"), list):
        deny.extend(configured["deny"])
    
    # Merge allow and alsoAllow
    merged_allow = None
    if allow and also_allow:
        merged_allow = list(set([*allow, *also_allow]))
    elif allow:
        merged_allow = allow
    
    return ToolPolicy(allow=merged_allow, deny=deny)


def is_tool_allowed_by_policy(
    tool_name: str,
    policy: Optional[ToolPolicy] = None,
) -> bool:
    """
    Check if tool is allowed by policy.
    
    Matches TS isToolAllowedByPolicyName() lines 105-110.
    
    Args:
        tool_name: Tool name to check
        policy: Tool policy (None = allow all)
    
    Returns:
        True if allowed, False if denied
    """
    if policy is None:
        return True
    
    return policy.is_allowed(tool_name)


def filter_tools_by_policy(
    tools: list,
    policy: Optional[ToolPolicy] = None,
) -> list:
    """
    Filter tools list by policy.
    
    Matches TS filterToolsByPolicy() lines 112-118.
    
    Args:
        tools: List of tool objects (must have 'name' attribute)
        policy: Tool policy (None = allow all)
    
    Returns:
        Filtered tools list
    """
    if policy is None:
        return tools
    
    return [
        tool for tool in tools
        if hasattr(tool, 'name') and policy.is_allowed(tool.name)
    ]


__all__ = [
    "DEFAULT_SUBAGENT_MAX_SPAWN_DEPTH",
    "SUBAGENT_TOOL_DENY_ALWAYS",
    "SUBAGENT_TOOL_DENY_LEAF",
    "ToolPolicy",
    "resolve_subagent_deny_list",
    "resolve_subagent_tool_policy",
    "is_tool_allowed_by_policy",
    "filter_tools_by_policy",
    "normalize_tool_name",
]
