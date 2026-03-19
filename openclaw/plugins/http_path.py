"""HTTP path normalization for plugins

Mirrors openclaw/src/plugins/http-path.ts
"""
from __future__ import annotations


def normalize_plugin_http_path(path: str | None = None, fallback: str = "/") -> str:
    """Normalize HTTP path for plugin routes.
    
    Ensures path has a leading slash and trims whitespace.
    Falls back to fallback path if input is empty.
    
    Args:
        path: HTTP path to normalize
        fallback: Fallback path if input is empty (default: "/")
        
    Returns:
        Normalized path with leading slash
    
    Example:
        >>> normalize_plugin_http_path("api/test")
        "/api/test"
        >>> normalize_plugin_http_path("")
        "/"
        >>> normalize_plugin_http_path(None, "/default")
        "/default"
    """
    if not path or not path.strip():
        path = fallback
    
    path = path.strip()
    
    # Ensure leading slash
    if not path.startswith("/"):
        path = "/" + path
    
    return path


__all__ = ["normalize_plugin_http_path"]
