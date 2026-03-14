"""Agent configuration utilities

Mirrors TypeScript openclaw/src/commands/agents.config.ts
Provides helper functions for managing agent configurations.
"""
from __future__ import annotations

from typing import Any

from ..agents.agent_scope import (
    list_agent_entries,
    resolve_agent_dir,
    resolve_agent_workspace_dir,
    resolve_default_agent_id,
)
from ..agents.identity_file import load_agent_identity_from_workspace, identity_has_values
from ..config.schema import OpenClawConfig
from ..routing.session_key import normalize_agent_id


def find_agent_entry_index(agents_list: list[Any], agent_id: str) -> int:
    """Find agent entry index in list by ID
    
    Mirrors TS findAgentEntryIndex()
    """
    normalized_id = normalize_agent_id(agent_id)
    for i, entry in enumerate(agents_list):
        entry_id = entry.id if hasattr(entry, 'id') else entry.get("id", "")
        if normalize_agent_id(entry_id) == normalized_id:
            return i
    return -1


def resolve_agent_name(cfg: OpenClawConfig, agent_id: str) -> str | None:
    """Resolve agent name from config
    
    Mirrors TS resolveAgentName()
    """
    normalized_id = normalize_agent_id(agent_id)
    agents = list_agent_entries(cfg)
    
    for entry in agents:
        if normalize_agent_id(entry.id) == normalized_id:
            name = entry.name.strip() if entry.name else None
            return name if name else None
    
    return None


def resolve_agent_model(cfg: OpenClawConfig, agent_id: str) -> str | None:
    """Resolve agent model from config
    
    Mirrors TS resolveAgentModel()
    """
    normalized_id = normalize_agent_id(agent_id)
    agents = list_agent_entries(cfg)
    
    for entry in agents:
        if normalize_agent_id(entry.id) == normalized_id:
            if entry.model:
                # Handle string model
                if isinstance(entry.model, str):
                    stripped = entry.model.strip()
                    if stripped:
                        return stripped
                # Handle object model with primary
                elif isinstance(entry.model, dict):
                    primary = entry.model.get("primary", "")
                    if isinstance(primary, str):
                        stripped = primary.strip()
                        if stripped:
                            return stripped
    
    # Fallback to defaults
    if cfg.agents and cfg.agents.defaults:
        raw = cfg.agents.defaults.model
        if isinstance(raw, str):
            return raw
        elif isinstance(raw, dict):
            primary = raw.get("primary", "")
            if isinstance(primary, str):
                stripped = primary.strip()
                if stripped:
                    return stripped
    
    return None


def load_agent_identity(workspace: str) -> dict[str, Any] | None:
    """Load agent identity from workspace IDENTITY.md
    
    Mirrors TS loadAgentIdentity()
    """
    parsed = load_agent_identity_from_workspace(workspace)
    if not parsed:
        return None
    
    return parsed if identity_has_values(parsed) else None


def build_agent_summaries(cfg: OpenClawConfig) -> list[dict[str, Any]]:
    """Build agent summaries for agents.list response
    
    Mirrors TS buildAgentSummaries()
    """
    default_agent_id = normalize_agent_id(resolve_default_agent_id(cfg))
    configured_agents = list_agent_entries(cfg)
    
    # Build ordered IDs list
    if configured_agents:
        ordered_ids = [normalize_agent_id(agent.id) for agent in configured_agents]
    else:
        ordered_ids = [default_agent_id]
    
    # Count bindings per agent
    binding_counts: dict[str, int] = {}
    if cfg.bindings:
        for binding in cfg.bindings:
            agent_id = normalize_agent_id(binding.agent_id)
            binding_counts[agent_id] = binding_counts.get(agent_id, 0) + 1
    
    # Remove duplicates while preserving order
    seen = set()
    ordered = []
    for id_ in ordered_ids:
        if id_ not in seen:
            seen.add(id_)
            ordered.append(id_)
    
    # Build summaries
    summaries = []
    for id_ in ordered:
        workspace = resolve_agent_workspace_dir(cfg, id_)
        identity = load_agent_identity(workspace)
        
        # Find config identity
        config_identity = None
        for agent in configured_agents:
            if normalize_agent_id(agent.id) == id_:
                config_identity = agent.identity
                break
        
        # Resolve identity name and emoji
        identity_name = None
        identity_emoji = None
        identity_source = None
        
        if identity:
            identity_name = identity.get("name")
            identity_emoji = identity.get("emoji")
            identity_source = "identity"
        elif config_identity:
            if isinstance(config_identity, dict):
                identity_name = config_identity.get("name", "").strip() or None
                identity_emoji = config_identity.get("emoji", "").strip() or None
            if identity_name or identity_emoji:
                identity_source = "config"
        
        summaries.append({
            "id": id_,
            "name": resolve_agent_name(cfg, id_),
            "identityName": identity_name,
            "identityEmoji": identity_emoji,
            "identitySource": identity_source,
            "workspace": workspace,
            "agentDir": resolve_agent_dir(cfg, id_),
            "model": resolve_agent_model(cfg, id_),
            "bindings": binding_counts.get(id_, 0),
            "isDefault": id_ == default_agent_id,
        })
    
    return summaries


def apply_agent_config(
    cfg: OpenClawConfig,
    agent_id: str,
    name: str | None = None,
    workspace: str | None = None,
    agent_dir: str | None = None,
    model: str | None = None,
) -> OpenClawConfig:
    """Apply agent configuration (create or update)
    
    Mirrors TS applyAgentConfig()
    """
    normalized_id = normalize_agent_id(agent_id)
    name_stripped = name.strip() if name else None
    
    agents_list = list_agent_entries(cfg)
    index = find_agent_entry_index(agents_list, normalized_id)
    
    # Build base entry
    if index >= 0:
        base = agents_list[index].__dict__.copy()
    else:
        base = {"id": normalized_id}
    
    # Apply updates
    next_entry = {**base}
    if name_stripped:
        next_entry["name"] = name_stripped
    if workspace:
        next_entry["workspace"] = workspace
    if agent_dir:
        next_entry["agentDir"] = agent_dir
    if model:
        next_entry["model"] = model
    
    # Build next list
    next_list = [agent.__dict__.copy() for agent in agents_list]
    
    if index >= 0:
        next_list[index] = next_entry
    else:
        # If list is empty and we're not adding the default agent, add default first
        if len(next_list) == 0 and normalized_id != normalize_agent_id(resolve_default_agent_id(cfg)):
            next_list.append({"id": resolve_default_agent_id(cfg)})
        next_list.append(next_entry)
    
    # Build updated config
    from ..config.schema import AgentEntry, AgentsConfig
    
    # Convert dicts to AgentEntry objects
    next_agents_list = []
    for entry_dict in next_list:
        # Create AgentEntry with minimal required fields
        next_agents_list.append(AgentEntry(
            id=entry_dict["id"],
            name=entry_dict.get("name"),
            workspace=entry_dict.get("workspace"),
            agent_dir=entry_dict.get("agentDir"),
            model=entry_dict.get("model"),
        ))
    
    # Update config
    if cfg.agents:
        new_agents = AgentsConfig(
            defaults=cfg.agents.defaults,
            list=next_agents_list,
        )
    else:
        new_agents = AgentsConfig(list=next_agents_list)
    
    # Return updated config (immutable update)
    cfg_dict = cfg.model_dump(by_alias=True, exclude_none=True)
    cfg_dict["agents"] = new_agents.model_dump(by_alias=True, exclude_none=True)
    
    return OpenClawConfig(**cfg_dict)


def prune_agent_config(
    cfg: OpenClawConfig,
    agent_id: str,
) -> dict[str, Any]:
    """Remove agent from config and clean up bindings
    
    Mirrors TS pruneAgentConfig()
    Returns: {config, removedBindings, removedAllow}
    """
    normalized_id = normalize_agent_id(agent_id)
    agents = list_agent_entries(cfg)
    
    # Filter out the agent
    next_agents_list = [agent for agent in agents if normalize_agent_id(agent.id) != normalized_id]
    
    # Filter bindings
    bindings = cfg.bindings or []
    filtered_bindings = [b for b in bindings if normalize_agent_id(b.agent_id) != normalized_id]
    
    # Filter agentToAgent allow list
    allow = []
    if cfg.tools and hasattr(cfg.tools, 'agent_to_agent'):
        if hasattr(cfg.tools.agent_to_agent, 'allow'):
            allow = cfg.tools.agent_to_agent.allow or []
    
    filtered_allow = [a for a in allow if a != normalized_id]
    
    # Build updated config
    cfg_dict = cfg.model_dump(by_alias=True, exclude_none=True)
    
    # Update agents
    if next_agents_list:
        from ..config.schema import AgentsConfig, AgentEntry
        next_agents_objs = [
            AgentEntry(
                id=a.id,
                name=a.name,
                workspace=a.workspace,
                agent_dir=a.agent_dir,
                model=a.model,
            ) for a in next_agents_list
        ]
        cfg_dict["agents"] = AgentsConfig(
            defaults=cfg.agents.defaults if cfg.agents else None,
            list=next_agents_objs,
        ).model_dump(by_alias=True, exclude_none=True)
    else:
        cfg_dict["agents"] = None
    
    # Update bindings
    if filtered_bindings:
        cfg_dict["bindings"] = [b.model_dump(by_alias=True) for b in filtered_bindings]
    else:
        cfg_dict["bindings"] = None
    
    # Update tools.agentToAgent.allow
    if cfg.tools and filtered_allow:
        tools_dict = cfg_dict.get("tools", {})
        if "agentToAgent" not in tools_dict:
            tools_dict["agentToAgent"] = {}
        tools_dict["agentToAgent"]["allow"] = filtered_allow
        cfg_dict["tools"] = tools_dict
    
    return {
        "config": OpenClawConfig(**cfg_dict),
        "removedBindings": len(bindings) - len(filtered_bindings),
        "removedAllow": len(allow) - len(filtered_allow),
    }
