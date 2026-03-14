"""Session configuration store."""

from typing import Any, Dict, Optional


class SessionStore:
    """Store for session configurations."""
    
    def __init__(self):
        """Initialize session store."""
        self._sessions: Dict[str, Dict[str, Any]] = {}
    
    def get(self, session_key: str) -> Optional[Dict[str, Any]]:
        """Get session configuration."""
        return self._sessions.get(session_key)
    
    def set(self, session_key: str, config: Dict[str, Any]) -> None:
        """Set session configuration."""
        self._sessions[session_key] = config
    
    def delete(self, session_key: str) -> None:
        """Delete session configuration."""
        self._sessions.pop(session_key, None)
    
    def list(self) -> Dict[str, Dict[str, Any]]:
        """List all sessions."""
        return self._sessions.copy()


# Global store
_session_store: Optional[SessionStore] = None


def get_session_store() -> SessionStore:
    """Get global session store."""
    global _session_store
    if _session_store is None:
        _session_store = SessionStore()
    return _session_store


def load_session_store(path: Optional[str] = None) -> SessionStore:
    """Load session store from file."""
    # For now, just return the global store
    # TODO: implement actual file loading if needed
    return get_session_store()


def update_session_store(session_key: str, updates: Dict[str, Any]) -> None:
    """Update session configuration."""
    store = get_session_store()
    existing = store.get(session_key) or {}
    existing.update(updates)
    store.set(session_key, existing)
