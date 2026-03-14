"""CLI session ID management for OpenClaw Python.

Mirrors TypeScript src/agents/cli-session.ts implementation.
Handles reading and writing CLI session IDs from/to SessionEntry.
"""

from typing import Any, Optional


def get_cli_session_id(
    entry: Optional[Any],
    provider: str,
) -> Optional[str]:
    """Get CLI session ID from session entry.
    
    Mirrors TS getCliSessionId function.
    
    Args:
        entry: SessionEntry object (or dict-like)
        provider: Provider name (e.g., "claude-cli", "codex-cli")
        
    Returns:
        CLI session ID if found, None otherwise
    """
    if not entry:
        return None
    
    # Normalize provider ID
    from openclaw.agents.model_selection import normalize_provider_id
    normalized = normalize_provider_id(provider)
    
    # Try to get from cliSessionIds map
    cli_session_ids = getattr(entry, "cliSessionIds", None) or (
        entry.get("cliSessionIds") if isinstance(entry, dict) else None
    )
    
    if cli_session_ids and isinstance(cli_session_ids, dict):
        from_map = cli_session_ids.get(normalized)
        if from_map and isinstance(from_map, str) and from_map.strip():
            return from_map.strip()
    
    # Legacy fallback for claude-cli
    if normalized == "claude-cli":
        legacy = getattr(entry, "claudeCliSessionId", None) or (
            entry.get("claudeCliSessionId") if isinstance(entry, dict) else None
        )
        if legacy and isinstance(legacy, str) and legacy.strip():
            return legacy.strip()
    
    return None


def set_cli_session_id(
    entry: Any,
    provider: str,
    session_id: str,
) -> None:
    """Set CLI session ID in session entry.
    
    Mirrors TS setCliSessionId function.
    
    Args:
        entry: SessionEntry object (or dict-like)
        provider: Provider name (e.g., "claude-cli", "codex-cli")
        session_id: CLI session ID to save
    """
    # Normalize provider ID
    from openclaw.agents.model_selection import normalize_provider_id
    normalized = normalize_provider_id(provider)
    
    # Trim session ID
    trimmed = session_id.strip() if session_id else ""
    if not trimmed:
        return
    
    # Get or create cliSessionIds map
    if hasattr(entry, "cliSessionIds"):
        # SessionEntry object
        existing = getattr(entry, "cliSessionIds", None) or {}
        if not isinstance(existing, dict):
            existing = {}
        # Create new dict (immutable update)
        entry.cliSessionIds = {**existing, normalized: trimmed}
    elif isinstance(entry, dict):
        # Dict-like entry
        existing = entry.get("cliSessionIds") or {}
        if not isinstance(existing, dict):
            existing = {}
        entry["cliSessionIds"] = {**existing, normalized: trimmed}
    
    # Legacy support for claude-cli
    if normalized == "claude-cli":
        if hasattr(entry, "claudeCliSessionId"):
            entry.claudeCliSessionId = trimmed
        elif isinstance(entry, dict):
            entry["claudeCliSessionId"] = trimmed
