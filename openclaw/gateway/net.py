"""Network utilities for gateway

Mirrors openclaw/src/gateway/net.ts
"""
from __future__ import annotations

import logging
import socket
from typing import Literal

logger = logging.getLogger(__name__)


BindMode = Literal["loopback", "all"]


def resolve_bind_host(mode: BindMode) -> str:
    """Resolve bind host from mode.
    
    Args:
        mode: Bind mode ('loopback' or 'all')
        
    Returns:
        Host address
    """
    if mode == "loopback":
        return "127.0.0.1"
    elif mode == "all":
        return "0.0.0.0"
    else:
        return "127.0.0.1"


def get_local_ip() -> str:
    """Get local IP address.
    
    Returns:
        Local IP address
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Check if port is available.
    
    Args:
        port: Port number
        host: Host address
        
    Returns:
        True if port is available
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
            return True
    except OSError:
        return False


__all__ = [
    "BindMode",
    "resolve_bind_host",
    "get_local_ip",
    "is_port_available",
]
