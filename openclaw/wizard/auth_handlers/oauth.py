"""OAuth handlers for portal flows

Handles OAuth authentication for Chutes, Qwen Portal, etc.
Mirrors openclaw/src/commands/auth-choice.apply.oauth.ts
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..auth_choice_types import AuthChoice
    from ...config.schema import OpenClawConfig

from .base import ApplyAuthChoiceResult


async def apply_auth_choice_oauth(
    auth_choice: AuthChoice,
    config: OpenClawConfig,
    set_default_model: bool = True,
    agent_dir: str | None = None,
    agent_id: str | None = None,
    opts: dict | None = None,
) -> ApplyAuthChoiceResult | None:
    """Handle OAuth portal flows
    
    Supports:
    - chutes: Chutes OAuth
    - qwen-portal: Qwen OAuth Portal
    - minimax-portal: MiniMax OAuth Portal (handled in minimax.py)
    
    Args:
        auth_choice: Selected authentication choice
        config: Current configuration
        set_default_model: Whether to set default model
        agent_dir: Optional agent directory
        agent_id: Optional agent ID
        opts: Optional parameters
        
    Returns:
        ApplyAuthChoiceResult if handled, None otherwise
    """
    if auth_choice not in ("chutes", "qwen-portal"):
        return None
    
    # Chutes OAuth
    if auth_choice == "chutes":
        print("\n⚠️  Chutes OAuth is not yet implemented in Python version.")
        print("OAuth portal flows require browser-based authentication.")
        return None
    
    # Qwen Portal OAuth
    if auth_choice == "qwen-portal":
        print("\n⚠️  Qwen OAuth Portal is not yet implemented in Python version.")
        print("OAuth portal flows require browser-based authentication.")
        return None
    
    return None
