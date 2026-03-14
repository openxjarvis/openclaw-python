"""LLM Provider utilities for accessing API keys"""
import os
from pathlib import Path


def get_api_key(provider: str) -> str | None:
    """
    Get API key for a provider.
    
    Checks environment variables and config files.
    
    Args:
        provider: Provider name ('anthropic', 'openai', 'google', etc.)
        
    Returns:
        API key or None if not found
    """
    provider = provider.lower().strip()
    
    # Check environment variables
    env_keys = {
        "anthropic": ["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"],
        "openai": ["OPENAI_API_KEY"],
        "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    }
    
    for env_var in env_keys.get(provider, []):
        key = os.getenv(env_var)
        if key:
            return key.strip()
    
    # Check .env file in agent directory
    try:
        from dotenv import load_dotenv
        agent_dir = Path.home() / ".openclaw" / "agents"
        env_file = agent_dir / ".env"
        if env_file.exists():
            load_dotenv(env_file)
            for env_var in env_keys.get(provider, []):
                key = os.getenv(env_var)
                if key:
                    return key.strip()
    except ImportError:
        pass
    
    return None
