"""Control UI request routing

Mirrors openclaw/src/gateway/control-ui-routing.ts

Classifies incoming HTTP requests to determine if they should be handled
by the Control UI, API routes, plugins, or other handlers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class ControlUiRequestClassification:
    """Classification result for Control UI routing"""
    kind: Literal["not-control-ui", "not-found", "redirect", "serve"]
    location: str | None = None


def is_read_http_method(method: str | None) -> bool:
    """Check if HTTP method is safe/read-only.
    
    Mirrors TypeScript isReadHttpMethod() in control-ui-http-utils.ts
    """
    if not method:
        return True
    return method.upper() in ("GET", "HEAD", "OPTIONS")


def classify_control_ui_request(
    base_path: str,
    pathname: str,
    search: str = "",
    method: str | None = None,
) -> ControlUiRequestClassification:
    """Classify a request for Control UI routing.
    
    Mirrors TypeScript classifyControlUiRequest()
    
    Args:
        base_path: Base path for Control UI (e.g., "/ui" or "")
        pathname: Request pathname (e.g., "/ui/sessions")
        search: Query string (e.g., "?tab=active")
        method: HTTP method (e.g., "GET", "POST")
        
    Returns:
        Classification result:
        - "not-control-ui": Request should be handled by other routes (API, plugins)
        - "not-found": Request is for Control UI but path not found
        - "redirect": Request should redirect (e.g., base path without trailing slash)
        - "serve": Request should be served by Control UI
    """
    # Root-mounted Control UI (basePath = "")
    if not base_path:
        # /ui/* is not valid for root-mounted UI
        if pathname == "/ui" or pathname.startswith("/ui/"):
            return ControlUiRequestClassification(kind="not-found")
        
        # Plugin routes are not Control UI
        if pathname == "/plugins" or pathname.startsWith("/plugins/"):
            return ControlUiRequestClassification(kind="not-control-ui")
        
        # API routes are not Control UI
        if pathname == "/api" or pathname.startswith("/api/"):
            return ControlUiRequestClassification(kind="not-control-ui")
        
        # Non-read methods are not Control UI
        if not is_read_http_method(method):
            return ControlUiRequestClassification(kind="not-control-ui")
        
        # Everything else is served by Control UI (SPA fallback)
        return ControlUiRequestClassification(kind="serve")
    
    # Non-root-mounted Control UI (basePath = "/ui" or similar)
    # Request must start with base path
    if not pathname.startswith(f"{base_path}/") and pathname != base_path:
        return ControlUiRequestClassification(kind="not-control-ui")
    
    # Non-read methods are not Control UI
    if not is_read_http_method(method):
        return ControlUiRequestClassification(kind="not-control-ui")
    
    # Base path without trailing slash should redirect
    if pathname == base_path:
        return ControlUiRequestClassification(
            kind="redirect",
            location=f"{base_path}/{search}",
        )
    
    # Serve Control UI
    return ControlUiRequestClassification(kind="serve")


__all__ = [
    "ControlUiRequestClassification",
    "is_read_http_method",
    "classify_control_ui_request",
]
