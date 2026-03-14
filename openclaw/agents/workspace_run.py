"""Workspace directory resolution for agent runs.

Mirrors TypeScript src/agents/workspace-run.ts implementation.
Provides fallback logic when workspace directory is missing, blank, or invalid.
"""

import logging
from typing import Any, Literal, Optional, TypedDict

logger = logging.getLogger("openclaw.agents.workspace_run")

WorkspaceFallbackReason = Literal["missing", "blank", "invalid_type"]
AgentIdSource = Literal["explicit", "session_key", "default"]


class ResolveRunWorkspaceResult(TypedDict):
    """Result of workspace directory resolution."""
    workspace_dir: str
    used_fallback: bool
    fallback_reason: Optional[WorkspaceFallbackReason]
    agent_id: str
    agent_id_source: AgentIdSource


def resolve_run_agent_id(
    *,
    session_key: Optional[str] = None,
    agent_id: Optional[str] = None,
    config: Optional[Any] = None,
) -> tuple[str, AgentIdSource]:
    """Resolve agent ID for a run.
    
    Mirrors TS resolveRunAgentId function.
    
    Args:
        session_key: Session key (may contain agent ID)
        agent_id: Explicit agent ID override
        config: OpenClaw configuration
        
    Returns:
        Tuple of (agent_id, agent_id_source)
    """
    from openclaw.routing.session_key import (
        classify_session_key_shape,
        normalize_agent_id,
        parse_agent_session_key,
    )
    from openclaw.agents.agent_scope import resolve_default_agent_id
    
    raw_session_key = (session_key or "").strip()
    shape = classify_session_key_shape(raw_session_key)
    
    if shape == "malformed_agent":
        raise ValueError("Malformed agent session key; refusing workspace resolution.")
    
    # 1. Explicit agent ID override
    if agent_id and isinstance(agent_id, str) and agent_id.strip():
        return (normalize_agent_id(agent_id), "explicit")
    
    # 2. Default agent for missing or legacy keys
    default_agent_id = resolve_default_agent_id(config or {})
    if shape in ("missing", "legacy_or_alias"):
        return (default_agent_id or "main", "default")
    
    # 3. Parse agent ID from session key
    parsed = parse_agent_session_key(raw_session_key)
    if parsed:
        # ParsedAgentSessionKey is a dataclass/object, not a dict
        parsed_agent_id = getattr(parsed, "agent_id", None) or getattr(parsed, "agentId", None)
        if parsed_agent_id:
            return (normalize_agent_id(parsed_agent_id), "session_key")
    
    # 4. Defensive fallback
    return (default_agent_id or "main", "default")


def redact_run_identifier(value: Optional[str]) -> str:
    """Redact identifier for logging.
    
    Mirrors TS redactRunIdentifier function.
    
    Args:
        value: Identifier to redact
        
    Returns:
        Redacted identifier string
    """
    if not value:
        return "(none)"
    
    try:
        from openclaw.logging.redact_identifier import redact_identifier
        return redact_identifier(value, length=12)
    except Exception:
        # Fallback redaction
        if len(value) <= 12:
            return value[:4] + "…"
        return value[:6] + "…" + value[-6:]


def resolve_run_workspace_dir(
    *,
    workspace_dir: Any,
    session_key: Optional[str] = None,
    agent_id: Optional[str] = None,
    config: Optional[Any] = None,
) -> ResolveRunWorkspaceResult:
    """Resolve workspace directory for an agent run with fallback.
    
    Mirrors TS resolveRunWorkspaceDir function.
    
    Args:
        workspace_dir: Requested workspace directory (may be None/empty/invalid)
        session_key: Session key for agent ID resolution
        agent_id: Explicit agent ID override
        config: OpenClaw configuration
        
    Returns:
        ResolveRunWorkspaceResult with resolved workspace and metadata
    """
    from openclaw.agents.agent_scope import resolve_agent_workspace_dir
    from openclaw.agents.sanitize_for_prompt import sanitize_for_prompt_literal
    from openclaw.config.paths import resolve_user_path
    
    # Resolve agent ID
    resolved_agent_id, agent_id_source = resolve_run_agent_id(
        session_key=session_key,
        agent_id=agent_id,
        config=config,
    )
    
    # Try to use requested workspace
    if isinstance(workspace_dir, str):
        trimmed = workspace_dir.strip()
        if trimmed:
            sanitized = sanitize_for_prompt_literal(trimmed)
            if sanitized != trimmed:
                logger.warning(
                    "Control/format characters stripped from workspaceDir (OC-19 hardening)."
                )
            return ResolveRunWorkspaceResult(
                workspace_dir=resolve_user_path(sanitized),
                used_fallback=False,
                fallback_reason=None,
                agent_id=resolved_agent_id,
                agent_id_source=agent_id_source,
            )
    
    # Determine fallback reason
    if workspace_dir is None:
        fallback_reason: WorkspaceFallbackReason = "missing"
    elif isinstance(workspace_dir, str):
        fallback_reason = "blank"
    else:
        fallback_reason = "invalid_type"
    
    # Use fallback workspace
    fallback_workspace = str(resolve_agent_workspace_dir(config or {}, resolved_agent_id))
    sanitized_fallback = sanitize_for_prompt_literal(fallback_workspace)
    if sanitized_fallback != fallback_workspace:
        logger.warning(
            "Control/format characters stripped from fallback workspaceDir (OC-19 hardening)."
        )
    
    return ResolveRunWorkspaceResult(
        workspace_dir=resolve_user_path(sanitized_fallback),
        used_fallback=True,
        fallback_reason=fallback_reason,
        agent_id=resolved_agent_id,
        agent_id_source=agent_id_source,
    )
