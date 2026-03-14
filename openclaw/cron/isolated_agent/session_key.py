"""Cron agent session key resolution (mirrors TS session-key.ts)."""

from openclaw.routing.session_key import to_agent_store_session_key


def resolve_cron_agent_session_key(
    session_key: str,
    agent_id: str,
    main_key: str | None = None,
) -> str:
    """
    Resolve cron session key to agent store format.
    
    Mirrors TS resolveCronAgentSessionKey from src/cron/isolated-agent/session-key.ts:3-13
    
    Converts partial session keys like "cron:job-1" or "main" to the canonical
    agent store format "agent:<agentId>:<rest>".
    
    Args:
        session_key: The session key to resolve (e.g., "cron:job-1", "main")
        agent_id: The agent identifier (e.g., "main", "jarvis-clone")
        main_key: Optional main key override (default: "main")
    
    Returns:
        Canonical agent store session key in format "agent:<agentId>:<rest>"
    
    Examples:
        >>> resolve_cron_agent_session_key("cron:job-1", "main")
        "agent:main:cron:job-1"
        >>> resolve_cron_agent_session_key("agent:main:main", "main")
        "agent:main:main"
        >>> resolve_cron_agent_session_key("main", "jarvis-clone")
        "agent:jarvis-clone:main"
    """
    return to_agent_store_session_key(
        agent_id=agent_id,
        request_key=session_key.strip(),
        main_key=main_key,
    )
