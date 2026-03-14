"""Base types and protocols for auth handlers

Defines the handler protocol and result types.
Mirrors openclaw/src/commands/auth-choice.apply.ts types.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..auth_choice_types import AuthChoice
    from ...config.schema import OpenClawConfig


@dataclass
class ApplyAuthChoiceResult:
    """Result from applying an auth choice
    
    Attributes:
        config: Updated configuration
        agent_model_override: Optional model override for specific agent
    """
    config: "OpenClawConfig"
    agent_model_override: str | None = None


class AuthChoiceHandler(Protocol):
    """Protocol for auth choice handlers
    
    Each handler is responsible for:
    1. Recognizing if it handles the given auth_choice
    2. Collecting necessary configuration (API keys, OAuth tokens, etc.)
    3. Writing to config (models.providers, agents.defaults.model, etc.)
    4. Returning updated config or None if not handled
    
    Aligns with TS ApplyAuthChoiceParams interface.
    """
    
    async def __call__(
        self,
        auth_choice: AuthChoice,
        config: OpenClawConfig,
        set_default_model: bool = True,
        agent_dir: str | None = None,
        agent_id: str | None = None,
        opts: dict | None = None,
    ) -> ApplyAuthChoiceResult | None:
        """Apply authentication choice to configuration
        
        Args:
            auth_choice: Selected authentication choice
            config: Current configuration
            set_default_model: Whether to set default model
            agent_dir: Optional agent directory
            agent_id: Optional agent ID
            opts: Optional parameters (API keys, tokens, etc.)
            
        Returns:
            ApplyAuthChoiceResult if handled, None otherwise
        """
        ...


async def apply_auth_choice_chain(
    handlers: list[AuthChoiceHandler],
    auth_choice: AuthChoice,
    config: OpenClawConfig,
    set_default_model: bool = True,
    agent_dir: str | None = None,
    agent_id: str | None = None,
    opts: dict | None = None,
) -> ApplyAuthChoiceResult:
    """Apply auth choice using handler chain
    
    Tries each handler in order until one returns a result.
    Mirrors TS applyAuthChoice function.
    
    Args:
        handlers: List of handlers to try
        auth_choice: Selected authentication choice
        config: Current configuration
        set_default_model: Whether to set default model
        agent_dir: Optional agent directory
        agent_id: Optional agent ID
        opts: Optional parameters
        
    Returns:
        ApplyAuthChoiceResult from first matching handler, or unchanged config
    """
    for handler in handlers:
        result = await handler(
            auth_choice=auth_choice,
            config=config,
            set_default_model=set_default_model,
            agent_dir=agent_dir,
            agent_id=agent_id,
            opts=opts or {},
        )
        if result is not None:
            return result
    
    # No handler matched - return unchanged config
    return ApplyAuthChoiceResult(config=config)
