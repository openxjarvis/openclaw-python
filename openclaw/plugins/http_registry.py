"""Plugin HTTP route registration

Mirrors openclaw/src/plugins/http-registry.ts
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from .http_path import normalize_plugin_http_path

logger = logging.getLogger(__name__)


# Type alias for HTTP route handler
PluginHttpRouteHandler = Callable[[Any, Any], Awaitable[bool | None] | bool | None]
"""Plugin HTTP route handler: (req, res) -> bool | None | Awaitable[bool | None]"""


def register_plugin_http_route(
    registry: Any,
    plugin_id: str,
    path: str | None = None,
    handler: PluginHttpRouteHandler | None = None,
    replace_existing: bool = False,
) -> Callable[[], None]:
    """Register plugin HTTP route.
    
    Registers an HTTP route handler on the active plugin registry.
    
    Args:
        registry: Plugin registry instance
        plugin_id: Plugin identifier
        path: HTTP path (defaults to '/')
        handler: Route handler function
        replace_existing: Whether to replace existing routes
        
    Returns:
        Unregister function to remove the route
        
    Raises:
        ValueError: If handler is None or path conflicts
    """
    if handler is None:
        raise ValueError("HTTP route handler cannot be None")
    
    # Normalize path
    normalized_path = normalize_plugin_http_path(path, fallback="/")
    
    # Check for conflicts
    if hasattr(registry, "http_handlers"):
        for existing in registry.http_handlers:
            if (
                existing.path == normalized_path
                and existing.plugin_id != plugin_id
                and not replace_existing
            ):
                raise ValueError(
                    f"HTTP route '{normalized_path}' already registered by plugin "
                    f"'{existing.plugin_id}'"
                )
    
    # Register handler
    from .types import PluginHttpHandlerRegistration
    
    registration = PluginHttpHandlerRegistration(
        plugin_id=plugin_id,
        path=normalized_path,
        handler=handler,
    )
    
    if not hasattr(registry, "http_handlers"):
        registry.http_handlers = []
    
    # Remove existing handler if replacing
    if replace_existing:
        registry.http_handlers = [
            h for h in registry.http_handlers
            if not (h.path == normalized_path and h.plugin_id == plugin_id)
        ]
    
    registry.http_handlers.append(registration)
    
    logger.info(f"Registered HTTP route '{normalized_path}' for plugin '{plugin_id}'")
    
    # Return unregister function
    def unregister() -> None:
        if hasattr(registry, "http_handlers"):
            registry.http_handlers = [
                h for h in registry.http_handlers
                if not (h.path == normalized_path and h.plugin_id == plugin_id)
            ]
            logger.info(f"Unregistered HTTP route '{normalized_path}' for plugin '{plugin_id}'")
    
    return unregister


__all__ = ["PluginHttpRouteHandler", "register_plugin_http_route"]
