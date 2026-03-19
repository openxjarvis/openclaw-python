"""Channel health policy

Mirrors openclaw/src/gateway/channel-health-policy.ts
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)


ChannelHealthAction = Literal["none", "restart", "disable", "alert"]


@dataclass
class ChannelHealthPolicy:
    """Channel health policy configuration"""
    
    max_errors: int = 3
    """Maximum errors before action"""
    
    error_window_ms: int = 60000
    """Error window in milliseconds"""
    
    action: ChannelHealthAction = "alert"
    """Action to take on unhealthy channel"""
    
    cooldown_ms: int = 300000
    """Cooldown before retry (ms)"""


def evaluate_channel_health_policy(
    error_count: int,
    policy: ChannelHealthPolicy,
) -> ChannelHealthAction:
    """Evaluate channel health and determine action.
    
    Args:
        error_count: Number of errors in window
        policy: Health policy configuration
        
    Returns:
        Action to take
    """
    if error_count >= policy.max_errors:
        return policy.action
    
    return "none"


__all__ = [
    "ChannelHealthPolicy",
    "ChannelHealthAction",
    "evaluate_channel_health_policy",
]
