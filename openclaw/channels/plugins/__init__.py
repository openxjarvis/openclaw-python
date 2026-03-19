"""Channels plugins system

Provides outbound handling, onboarding, actions, and adapters.
"""
# Import from outbound/ directory (channel-specific adapters)
from .outbound import (
    TelegramOutboundAdapter,
    DiscordOutboundAdapter,
    SignalOutboundAdapter,
    SlackOutboundAdapter,
    load_outbound_adapter,
)
from .onboarding import (
    OnboardingStep,
    OnboardingFlow,
    OnboardingManager,
)
from .actions import (
    MessageAction,
    ActionHandler,
    MessageActionsManager,
)
from .adapters import (
    ChannelAdapter,
    AdapterRegistration,
    ChannelAdapterRegistry,
)

# Alias for compatibility with deliver.py
get_channel_plugin = load_outbound_adapter

__all__ = [
    # Outbound adapters (from outbound/ directory)
    "TelegramOutboundAdapter",
    "DiscordOutboundAdapter",
    "SignalOutboundAdapter",
    "SlackOutboundAdapter",
    "load_outbound_adapter",
    "get_channel_plugin",  # Alias
    # Onboarding
    "OnboardingStep",
    "OnboardingFlow",
    "OnboardingManager",
    # Actions
    "MessageAction",
    "ActionHandler",
    "MessageActionsManager",
    # Adapters
    "ChannelAdapter",
    "AdapterRegistration",
    "ChannelAdapterRegistry",
]


