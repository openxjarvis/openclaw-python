"""User onboarding for channels

Mirrors openclaw/src/channels/plugins/onboarding-types.ts

Note: This package provides onboarding types and management.
Channel-specific onboarding implementations are in this directory (e.g. telegram.py).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class OnboardingStep:
    """Onboarding step"""
    
    id: str
    """Step identifier"""
    
    message: str
    """Step message"""
    
    required: bool = True
    """Whether step is required"""


@dataclass
class OnboardingFlow:
    """Onboarding flow configuration"""
    
    channel_id: str
    """Channel identifier"""
    
    steps: list[OnboardingStep]
    """Onboarding steps"""
    
    enabled: bool = True
    """Whether onboarding is enabled"""


class OnboardingManager:
    """Manage user onboarding flows."""
    
    def __init__(self):
        self.flows: dict[str, OnboardingFlow] = {}
        self.user_progress: dict[str, int] = {}
    
    def register_flow(self, flow: OnboardingFlow) -> None:
        """Register onboarding flow.
        
        Args:
            flow: Onboarding flow
        """
        self.flows[flow.channel_id] = flow
        logger.info(f"Registered onboarding flow for {flow.channel_id}")
    
    def get_next_step(
        self,
        channel_id: str,
        user_id: str,
    ) -> OnboardingStep | None:
        """Get next onboarding step for user.
        
        Args:
            channel_id: Channel identifier
            user_id: User identifier
            
        Returns:
            Next step or None if complete
        """
        flow = self.flows.get(channel_id)
        if not flow or not flow.enabled:
            return None
        
        progress_key = f"{channel_id}:{user_id}"
        current_step = self.user_progress.get(progress_key, 0)
        
        if current_step >= len(flow.steps):
            return None
        
        return flow.steps[current_step]
    
    def advance_step(
        self,
        channel_id: str,
        user_id: str,
    ) -> None:
        """Advance user to next onboarding step.
        
        Args:
            channel_id: Channel identifier
            user_id: User identifier
        """
        progress_key = f"{channel_id}:{user_id}"
        current = self.user_progress.get(progress_key, 0)
        self.user_progress[progress_key] = current + 1


__all__ = [
    "OnboardingStep",
    "OnboardingFlow",
    "OnboardingManager",
]
