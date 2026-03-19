"""Agent scope resolution and configuration

Fully aligned with TypeScript openclaw/src/agents/agent-scope.ts

This module provides functions for resolving agent-specific configurations:
- Agent ID resolution (default, session-based)
- Agent workspace directory resolution
- Agent directory resolution  
- Agent configuration lookup
- Multi-agent listing and filtering
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from ..config.paths import resolve_state_dir

from openclaw.routing.session_key import (
    DEFAULT_AGENT_ID,
    normalize_agent_id,
    parse_agent_session_key,
)

logger = logging.getLogger(__name__)

# Track if we've warned about multiple default agents
_default_agent_warned = False


def list_agent_entries(cfg: Any) -> list[Any]:
    """
    List all configured agents from config.
    
    Mirrors TS listAgentEntries() from agent-scope.ts lines 45-51
    
    Args:
        cfg: Configuration object
    
    Returns:
        List of agent entries (may be empty)
    """
    if not cfg or not hasattr(cfg, 'agents'):
        return []
    
    agents = cfg.agents
    if not hasattr(agents, 'list'):
        return []
    
    agent_list = agents.list
    if not isinstance(agent_list, list):
        return []
    
    # Filter out None/invalid entries
    return [entry for entry in agent_list if entry is not None and hasattr(entry, 'id')]


# Alias for compatibility
list_agents = list_agent_entries


def list_agent_ids(cfg: Any) -> list[str]:
    """
    List all configured agent IDs.
    
    Mirrors TS listAgentIds() from agent-scope.ts lines 53-69
    
    Args:
        cfg: Configuration object
    
    Returns:
        List of normalized agent IDs (defaults to ["main"] if none configured)
    """
    agents = list_agent_entries(cfg)
    
    if not agents:
        return [DEFAULT_AGENT_ID]
    
    seen: set[str] = set()
    ids: list[str] = []
    
    for entry in agents:
        agent_id = normalize_agent_id(entry.id if hasattr(entry, 'id') else None)
        if agent_id in seen:
            continue
        seen.add(agent_id)
        ids.append(agent_id)
    
    return ids if ids else [DEFAULT_AGENT_ID]


def resolve_default_agent_id(cfg: Any) -> str:
    """
    Resolve default agent ID from configuration.
    
    Mirrors TS resolveDefaultAgentId() from agent-scope.ts lines 61-73
    
    Priority:
    1. Agent with default=True in agents.list[]
    2. First agent in agents.list[]
    3. Fallback to "main"
    
    Args:
        cfg: Configuration object
    
    Returns:
        Default agent ID (normalized)
    """
    global _default_agent_warned
    
    agents = list_agents(cfg)
    
    if not agents:
        return DEFAULT_AGENT_ID
    
    # Find agents marked as default
    default_agents = [a for a in agents if getattr(a, 'default', False)]
    
    if len(default_agents) > 1 and not _default_agent_warned:
        _default_agent_warned = True
        logger.warning("Multiple agents marked default=True; using the first entry as default.")
    
    # Use first default agent, or first agent in list
    chosen = default_agents[0] if default_agents else agents[0]
    chosen_id = getattr(chosen, 'id', '').strip() if chosen else ''
    
    return normalize_agent_id(chosen_id or DEFAULT_AGENT_ID)


def list_agent_workspace_dirs(cfg: Any) -> list[str]:
    """
    List all agent workspace directories.
    
    Mirrors TS listAgentWorkspaceDirs() from workspace-dirs.ts
    
    Args:
        cfg: Configuration object
    
    Returns:
        List of workspace directory paths for all configured agents
    """
    dirs: set[str] = set()
    
    # Add all configured agents' workspaces
    agents = list_agent_entries(cfg)
    for entry in agents:
        if entry and hasattr(entry, 'id'):
            dirs.add(resolve_agent_workspace_dir(cfg, entry.id))
    
    # Always include default agent workspace
    dirs.add(resolve_agent_workspace_dir(cfg, resolve_default_agent_id(cfg)))
    
    return list(dirs)


def resolve_session_agent_ids(
    session_key: str | None = None,
    config: Any = None,
) -> dict[str, str]:
    """
    Resolve both default and session agent IDs.
    
    Mirrors TS resolveSessionAgentIds() from agent-scope.ts lines 75-85
    
    Args:
        session_key: Optional session key to parse
        config: Configuration object
    
    Returns:
        Dict with 'defaultAgentId' and 'sessionAgentId' keys
    """
    default_agent_id = resolve_default_agent_id(config or {})
    
    session_key_str = session_key.strip() if session_key else ''
    normalized_key = session_key_str.lower() if session_key_str else None
    
    parsed = parse_agent_session_key(normalized_key) if normalized_key else None
    
    session_agent_id = (
        normalize_agent_id(parsed.agent_id) 
        if parsed and parsed.agent_id 
        else default_agent_id
    )
    
    return {
        'defaultAgentId': default_agent_id,
        'sessionAgentId': session_agent_id,
    }


def resolve_session_agent_id(
    session_key: str | None = None,
    config: Any = None,
) -> str:
    """
    Resolve agent ID from session key.
    
    Mirrors TS resolveSessionAgentId() from agent-scope.ts lines 87-92
    
    Args:
        session_key: Optional session key to parse
        config: Configuration object
    
    Returns:
        Session agent ID (normalized)
    """
    return resolve_session_agent_ids(session_key, config)['sessionAgentId']


def resolve_agent_entry(cfg: Any, agent_id: str) -> Any | None:
    """
    Find agent entry by ID.
    
    Mirrors TS resolveAgentEntry() from agent-scope.ts lines 94-97
    
    Args:
        cfg: Configuration object
        agent_id: Agent ID to find
    
    Returns:
        Agent entry or None if not found
    """
    normalized_id = normalize_agent_id(agent_id)
    agents = list_agents(cfg)
    
    for entry in agents:
        entry_id = getattr(entry, 'id', '')
        if normalize_agent_id(entry_id) == normalized_id:
            return entry
    
    return None


def resolve_agent_config(cfg: Any, agent_id: str) -> Any | None:
    """
    Resolve agent configuration by ID.
    
    Mirrors TS resolveAgentConfig() from agent-scope.ts lines 99-126
    
    Args:
        cfg: Configuration object
        agent_id: Agent ID to resolve
    
    Returns:
        Resolved agent config dict or None if not found
    """
    normalized_id = normalize_agent_id(agent_id)
    entry = resolve_agent_entry(cfg, normalized_id)
    
    if not entry:
        return None
    
    # Build resolved config object
    return type('ResolvedAgentConfig', (), {
        'name': getattr(entry, 'name', None),
        'workspace': getattr(entry, 'workspace', None),
        'agentDir': getattr(entry, 'agentDir', None),
        'model': getattr(entry, 'model', None),
        'skills': getattr(entry, 'skills', None),
        'memorySearch': getattr(entry, 'memorySearch', None),
        'humanDelay': getattr(entry, 'humanDelay', None),
        'heartbeat': getattr(entry, 'heartbeat', None),
        'identity': getattr(entry, 'identity', None),
        'groupChat': getattr(entry, 'groupChat', None),
        'subagents': getattr(entry, 'subagents', None),
        'sandbox': getattr(entry, 'sandbox', None),
        'tools': getattr(entry, 'tools', None),
    })()


def resolve_agent_workspace_dir(cfg: Any, agent_id: str) -> Path:
    """
    Resolve workspace directory for an agent.
    
    Mirrors TS resolveAgentWorkspaceDir() from agent-scope.ts lines 178-194
    
    Priority:
    1. agents.list[].workspace (for matching agentId)
    2. If default agent: agents.defaults.workspace
    3. If default agent: ~/.openclaw/workspace (or workspace-{profile})
    4. Otherwise: ~/.openclaw/workspace-{agentId}
    
    Args:
        cfg: Configuration object
        agent_id: Agent ID
    
    Returns:
        Resolved workspace directory path
    """
    
    normalized_id = normalize_agent_id(agent_id)
    
    # 1. Check agent-specific workspace config
    agent_config = resolve_agent_config(cfg, normalized_id)
    if agent_config and hasattr(agent_config, 'workspace') and agent_config.workspace:
        workspace_str = agent_config.workspace.strip()
        if workspace_str:
            return Path(workspace_str).expanduser().resolve()
    
    # 2. Check if this is the default agent
    default_agent_id = resolve_default_agent_id(cfg)
    
    if normalized_id == default_agent_id:
        # Try agents.defaults.workspace
        if hasattr(cfg, 'agents') and cfg.agents:
            if hasattr(cfg.agents, 'defaults') and cfg.agents.defaults:
                defaults = cfg.agents.defaults
                if hasattr(defaults, 'workspace') and defaults.workspace:
                    defaults_workspace = defaults.workspace.strip()
                    if defaults_workspace:
                        return Path(defaults_workspace).expanduser().resolve()
        
        # Fallback to default workspace with profile support
        profile = os.environ.get('OPENCLAW_PROFILE', '').strip()
        if profile:
            return Path.home() / '.openclaw' / f'workspace-{profile}'
        
        return Path.home() / '.openclaw' / 'workspace'
    
    # 3. Non-default agent: ~/.openclaw/workspace-{agentId}
    state_dir = resolve_state_dir()
    return state_dir / f'workspace-{normalized_id}'


def resolve_agent_dir(cfg: Any, agent_id: str) -> Path:
    """
    Resolve agent directory (for auth profiles, etc.).
    
    Mirrors TS resolveAgentDir() from agent-scope.ts lines 196-204
    
    Priority:
    1. agents.list[].agentDir (from config)
    2. Default: ~/.openclaw/agents/<agentId>/agent
    
    Args:
        cfg: Configuration object
        agent_id: Agent ID
    
    Returns:
        Resolved agent directory path
    """
    
    normalized_id = normalize_agent_id(agent_id)
    
    # 1. Check agent-specific agentDir config
    agent_config = resolve_agent_config(cfg, normalized_id)
    if agent_config and hasattr(agent_config, 'agentDir') and agent_config.agentDir:
        agent_dir_str = agent_config.agentDir.strip()
        if agent_dir_str:
            return Path(agent_dir_str).expanduser().resolve()
    
    # 2. Default path: ~/.openclaw/agents/<agentId>/agent
    state_dir = resolve_state_dir()
    return state_dir / "agents" / normalized_id / "agent"


def resolve_agent_skills_filter(cfg: Any, agent_id: str) -> list[str] | None:
    """
    Resolve skills filter for an agent.
    
    Mirrors TS resolveAgentSkillsFilter() from agent-scope.ts lines 128-133
    
    Args:
        cfg: Configuration object
        agent_id: Agent ID
    
    Returns:
        Skills filter list or None
    """
    agent_config = resolve_agent_config(cfg, agent_id)
    if not agent_config or not hasattr(agent_config, 'skills'):
        return None
    
    skills = agent_config.skills
    if not isinstance(skills, list):
        return None
    
    return skills


def resolve_agent_model_primary(cfg: Any, agent_id: str) -> str | None:
    """
    Resolve primary model for an agent.
    
    Mirrors TS resolveAgentModelPrimary() from agent-scope.ts lines 135-145
    
    Args:
        cfg: Configuration object
        agent_id: Agent ID
    
    Returns:
        Primary model string or None
    """
    agent_config = resolve_agent_config(cfg, agent_id)
    if not agent_config or not hasattr(agent_config, 'model'):
        return None
    
    model = agent_config.model
    
    if isinstance(model, str):
        return model.strip() or None
    
    if hasattr(model, 'primary'):
        primary = model.primary
        if isinstance(primary, str):
            return primary.strip() or None
    
    return None


def resolve_agent_model_fallbacks_override(cfg: Any, agent_id: str) -> list[str] | None:
    """
    Resolve model fallbacks override for an agent.
    
    Mirrors TS resolveAgentModelFallbacksOverride() from agent-scope.ts lines 147-160
    
    Args:
        cfg: Configuration object
        agent_id: Agent ID
    
    Returns:
        Fallbacks list or None (empty list = explicitly disabled)
    """
    agent_config = resolve_agent_config(cfg, agent_id)
    if not agent_config or not hasattr(agent_config, 'model'):
        return None
    
    model = agent_config.model
    
    if isinstance(model, str):
        return None
    
    # Check if fallbacks key exists (important: empty array is valid override)
    if hasattr(model, 'fallbacks'):
        fallbacks = model.fallbacks
        if isinstance(fallbacks, list):
            return fallbacks
    
    return None


def resolve_effective_model_fallbacks(
    cfg: Any,
    agent_id: str,
    has_session_model_override: bool,
) -> list[str] | None:
    """
    Resolve effective model fallbacks considering session overrides.
    
    Mirrors TS resolveEffectiveModelFallbacks() from agent-scope.ts lines 162-176
    
    Args:
        cfg: Configuration object
        agent_id: Agent ID
        has_session_model_override: Whether session has model override
    
    Returns:
        Effective fallbacks list or None
    """
    agent_fallbacks_override = resolve_agent_model_fallbacks_override(cfg, agent_id)
    
    if not has_session_model_override:
        return agent_fallbacks_override
    
    # Get default fallbacks
    default_fallbacks: list[str] = []
    if hasattr(cfg, 'agents') and cfg.agents:
        if hasattr(cfg.agents, 'defaults') and cfg.agents.defaults:
            defaults = cfg.agents.defaults
            if hasattr(defaults, 'model'):
                model = defaults.model
                if hasattr(model, 'fallbacks') and isinstance(model.fallbacks, list):
                    default_fallbacks = model.fallbacks
    
    return agent_fallbacks_override if agent_fallbacks_override is not None else default_fallbacks


def list_agent_workspace_dirs(cfg: Any) -> list[str]:
    """
    List all agent workspace directories.
    
    Mirrors TS listAgentWorkspaceDirs() from workspace-dirs.ts
    
    Args:
        cfg: Configuration object
    
    Returns:
        List of workspace directory paths for all configured agents
    """
    dirs: set[str] = set()
    
    # Add all configured agents' workspaces
    agents = list_agent_entries(cfg)
    for entry in agents:
        if entry and hasattr(entry, 'id'):
            dirs.add(resolve_agent_workspace_dir(cfg, entry.id))
    
    # Always include default agent workspace
    dirs.add(resolve_agent_workspace_dir(cfg, resolve_default_agent_id(cfg)))
    
    return list(dirs)
