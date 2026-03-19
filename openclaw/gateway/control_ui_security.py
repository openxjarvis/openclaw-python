"""Control UI security headers

Mirrors openclaw/src/gateway/control-ui-csp.ts and control-ui.ts
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web
    from ..config.schema import GatewayControlUiConfig


def build_control_ui_csp_header(config: "GatewayControlUiConfig") -> str:
    """Build Content-Security-Policy header for Control UI.
    
    Mirrors TypeScript buildControlUiCspHeader() in control-ui-csp.ts
    
    Args:
        config: Gateway Control UI configuration
        
    Returns:
        CSP header value
    """
    # Base CSP directives
    directives = [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'",  # Needed for Vite dev/build
        "style-src 'self' 'unsafe-inline'",  # Needed for inline styles
        "img-src 'self' data: blob: https:",  # Support data URLs and external images
        "font-src 'self' data:",
        "connect-src 'self' ws: wss:",  # WebSocket connections
        "frame-ancestors 'none'",  # Prevent embedding
        "base-uri 'self'",
        "form-action 'self'",
    ]
    
    # Add allowed origins for WebSocket if configured
    if config and hasattr(config, 'allowed_origins') and config.allowed_origins:
        origins = ' '.join(config.allowed_origins)
        directives[6] = f"connect-src 'self' ws: wss: {origins}"
    
    return "; ".join(directives)


def apply_control_ui_security_headers(
    response: "web.Response",
    config: "GatewayControlUiConfig | None" = None,
) -> None:
    """Apply security headers to Control UI responses.
    
    Mirrors TypeScript applyControlUiSecurityHeaders() in control-ui.ts
    
    Args:
        response: aiohttp Response object
        config: Optional Gateway Control UI configuration
    """
    # X-Frame-Options: Prevent clickjacking
    response.headers['X-Frame-Options'] = 'DENY'
    
    # X-Content-Type-Options: Prevent MIME sniffing
    response.headers['X-Content-Type-Options'] = 'nosniff'
    
    # Referrer-Policy: Control referer information
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    
    # Content-Security-Policy: Comprehensive content policy
    if config:
        response.headers['Content-Security-Policy'] = build_control_ui_csp_header(config)
    else:
        # Fallback CSP without config
        response.headers['Content-Security-Policy'] = build_control_ui_csp_header(None)


__all__ = [
    "build_control_ui_csp_header",
    "apply_control_ui_security_headers",
]
