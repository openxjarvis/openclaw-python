"""GitHub Copilot and Copilot Proxy auth handlers

Handles GitHub Copilot OAuth and local proxy configuration.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..auth_choice_types import AuthChoice
    from ...config.schema import OpenClawConfig

from .base import ApplyAuthChoiceResult


async def apply_auth_choice_github_copilot(
    auth_choice: AuthChoice,
    config: OpenClawConfig,
    set_default_model: bool = True,
    agent_dir: str | None = None,
    agent_id: str | None = None,
    opts: dict | None = None,
) -> ApplyAuthChoiceResult | None:
    """Handle GitHub Copilot OAuth
    
    Supports:
    - github-copilot: GitHub Copilot device flow OAuth
    
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
    if auth_choice != "github-copilot":
        return None
    
    print("\n⚠️  GitHub Copilot OAuth is not yet implemented in Python version.")
    print("GitHub device flow authentication requires OAuth infrastructure.")
    return None


async def apply_auth_choice_copilot_proxy(
    auth_choice: AuthChoice,
    config: OpenClawConfig,
    set_default_model: bool = True,
    agent_dir: str | None = None,
    agent_id: str | None = None,
    opts: dict | None = None,
) -> ApplyAuthChoiceResult | None:
    """Handle Copilot Proxy configuration
    
    Supports:
    - copilot-proxy: Local Copilot proxy for VS Code models
    
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
    if auth_choice != "copilot-proxy":
        return None
    
    print("\n⚠️  Copilot Proxy is not yet implemented in Python version.")
    print("This requires local proxy setup for VS Code Copilot models.")
    return None
