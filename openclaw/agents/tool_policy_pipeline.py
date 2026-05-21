"""Centralised tool policy pipeline.

Mirrors TypeScript src/agents/tool-policy-pipeline.ts (~190 lines)

Provides an ordered pipeline of policy steps that filter the available
tool list for a given agent run. Steps are:
  1. Profile-level allow/deny
  2. byProvider profile allow
  3. Global allow list
  4. byProvider global allow
  5. Per-agent allow list
  6. Per-agent byProvider allow
  7. Group tools.allow

This replaces the scattered tool_policy.py / tools_policy.py / sandbox/tool_policy.py
implementations with a single, composable pipeline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class PolicyStep:
    """A single step in the tool policy pipeline.

    Mirrors TS ToolPolicyPipelineStep.
    """

    id: str
    description: str
    apply: Callable[[list[Any], dict[str, Any]], list[Any]]


def build_default_tool_policy_pipeline_steps(
    config: Any = None,
    agent_id: str | None = None,
) -> list[PolicyStep]:
    """Build the default ordered pipeline steps.

    Mirrors TS buildDefaultToolPolicyPipelineSteps().
    """
    steps: list[PolicyStep] = []

    # Step 1: profile allow list (tools.profile from agent config)
    def apply_profile_allow(tools: list[Any], opts: dict[str, Any]) -> list[Any]:
        profile = opts.get("profile")
        if not profile:
            return tools
        allowed = _get_profile_tool_names(profile, config)
        if not allowed:
            return tools
        return [t for t in tools if _tool_name(t) in allowed]

    steps.append(PolicyStep(
        id="profile",
        description="Filter by tools.profile allow list",
        apply=apply_profile_allow,
    ))

    # Step 2: global allow list (agents.defaults.tools.allow)
    def apply_global_allow(tools: list[Any], opts: dict[str, Any]) -> list[Any]:
        allowed = _get_global_allow(config)
        if allowed is None:
            return tools
        return [t for t in tools if _tool_name(t) in set(allowed)]

    steps.append(PolicyStep(
        id="global-allow",
        description="Filter by global agents.defaults.tools.allow",
        apply=apply_global_allow,
    ))

    # Step 3: per-agent allow list
    def apply_per_agent_allow(tools: list[Any], opts: dict[str, Any]) -> list[Any]:
        eff_agent_id = opts.get("agent_id") or agent_id
        if not eff_agent_id:
            return tools
        allowed = _get_agent_allow(config, eff_agent_id)
        if allowed is None:
            return tools
        return [t for t in tools if _tool_name(t) in set(allowed)]

    steps.append(PolicyStep(
        id="per-agent-allow",
        description="Filter by per-agent tools.allow",
        apply=apply_per_agent_allow,
    ))

    # Step 4: deny list (global + per-agent)
    def apply_deny(tools: list[Any], opts: dict[str, Any]) -> list[Any]:
        eff_agent_id = opts.get("agent_id") or agent_id
        denied: set[str] = set()
        global_deny = _get_global_deny(config)
        if global_deny:
            denied.update(global_deny)
        if eff_agent_id:
            agent_deny = _get_agent_deny(config, eff_agent_id)
            if agent_deny:
                denied.update(agent_deny)
        if not denied:
            return tools
        return [t for t in tools if _tool_name(t) not in denied]

    steps.append(PolicyStep(
        id="deny",
        description="Remove denied tools (global + per-agent)",
        apply=apply_deny,
    ))

    return steps


def apply_tool_policy_pipeline(
    tools: list[Any],
    steps: list[PolicyStep],
    opts: dict[str, Any] | None = None,
) -> list[Any]:
    """Apply the pipeline steps in order, returning the filtered tool list.

    Mirrors TS applyToolPolicyPipeline().
    """
    effective_opts = opts or {}
    result = list(tools)
    for step in steps:
        try:
            result = step.apply(result, effective_opts)
        except Exception:
            logger.exception("Error in tool policy step '%s'", step.id)
    return result


# ---------------------------------------------------------------------------
# Config helper functions
# ---------------------------------------------------------------------------

def _tool_name(tool: Any) -> str:
    """Extract tool name from various tool object shapes."""
    if isinstance(tool, dict):
        return str(tool.get("name") or tool.get("id") or "")
    return str(getattr(tool, "name", "") or getattr(tool, "id", "") or "")


def _get_profile_tool_names(profile: str, config: Any) -> set[str] | None:
    """Get allowed tool names for a profile from config."""
    try:
        profiles = getattr(getattr(config, "agents", None), "tool_profiles", None) or {}
        if isinstance(profiles, dict):
            entry = profiles.get(profile)
            if entry and isinstance(entry, dict):
                allow = entry.get("allow") or entry.get("tools", [])
                return set(allow) if allow else None
    except Exception:
        pass
    return None


def _get_global_allow(config: Any) -> list[str] | None:
    """Get global tools.allow list."""
    try:
        defaults = getattr(getattr(config, "agents", None), "defaults", None)
        tools_cfg = getattr(defaults, "tools", None)
        if tools_cfg:
            allow = getattr(tools_cfg, "allow", None)
            if allow is not None:
                return list(allow)
    except Exception:
        pass
    return None


def _get_global_deny(config: Any) -> list[str] | None:
    """Get global tools.deny list."""
    try:
        defaults = getattr(getattr(config, "agents", None), "defaults", None)
        tools_cfg = getattr(defaults, "tools", None)
        if tools_cfg:
            deny = getattr(tools_cfg, "deny", None)
            if deny is not None:
                return list(deny)
    except Exception:
        pass
    return None


def _get_agent_allow(config: Any, agent_id: str) -> list[str] | None:
    """Get per-agent tools.allow list."""
    try:
        agents_list = getattr(getattr(config, "agents", None), "list", None) or []
        for ac in agents_list:
            if (getattr(ac, "id", None) or getattr(ac, "agentId", None)) == agent_id:
                tools_cfg = getattr(ac, "tools", None)
                if tools_cfg:
                    allow = getattr(tools_cfg, "allow", None)
                    if allow is not None:
                        return list(allow)
    except Exception:
        pass
    return None


def _get_agent_deny(config: Any, agent_id: str) -> list[str] | None:
    """Get per-agent tools.deny list."""
    try:
        agents_list = getattr(getattr(config, "agents", None), "list", None) or []
        for ac in agents_list:
            if (getattr(ac, "id", None) or getattr(ac, "agentId", None)) == agent_id:
                tools_cfg = getattr(ac, "tools", None)
                if tools_cfg:
                    deny = getattr(tools_cfg, "deny", None)
                    if deny is not None:
                        return list(deny)
    except Exception:
        pass
    return None
