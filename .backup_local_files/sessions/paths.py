"""Session store paths."""

from pathlib import Path
from typing import Optional


def get_default_store_path() -> Path:
    """Get default session store path."""
    # Use .openclaw directory in home
    home = Path.home()
    store_dir = home / ".openclaw" / "sessions"
    store_dir.mkdir(parents=True, exist_ok=True)
    return store_dir / "store.json"


def get_store_path(custom_path: Optional[str] = None) -> Path:
    """Get session store path."""
    if custom_path:
        return Path(custom_path)
    return get_default_store_path()


def resolve_session_store_path(config: dict = None) -> Path:
    """Resolve session store path from config.
    
    Args:
        config: Configuration dict
        
    Returns:
        Path to session store
    """
    if config:
        custom_path = config.get("sessions", {}).get("storePath")
        if custom_path:
            return Path(custom_path)
    
    return get_default_store_path()


__all__ = ["get_default_store_path", "get_store_path", "resolve_session_store_path"]
