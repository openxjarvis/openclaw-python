"""Path utilities for displaying and manipulating paths.

Mirrors TypeScript path utilities from openclaw/src/infra/
"""
from pathlib import Path
from typing import Union
import os


def shorten_home_path(path: Union[str, Path]) -> str:
    """
    Shorten path by replacing home directory with ~
    
    Mirrors TypeScript shortenHomePath() behavior from 
    openclaw/src/commands/onboard-helpers.ts
    
    Args:
        path: Path to shorten (str or Path object)
    
    Returns:
        Shortened path string with ~ replacing home directory
    
    Examples:
        >>> shorten_home_path("/Users/username/.openclaw/workspace")
        "~/.openclaw/workspace"
        
        >>> shorten_home_path(Path.home() / ".openclaw" / "agents")
        "~/.openclaw/agents"
        
        >>> shorten_home_path("/opt/openclaw")
        "/opt/openclaw"  # Not under home, returned unchanged
    """
    if isinstance(path, Path):
        path_str = str(path)
    else:
        path_str = str(path)
    
    # Get home directory
    home = os.path.expanduser("~")
    
    # If path starts with home directory, replace with ~
    if path_str.startswith(home):
        # Use Path to ensure proper path separator handling
        relative = Path(path_str).relative_to(home)
        return str(Path("~") / relative)
    
    return path_str


def expand_home_path(path: Union[str, Path]) -> Path:
    """
    Expand ~ to home directory in path
    
    Args:
        path: Path that may contain ~ (str or Path object)
    
    Returns:
        Path with ~ expanded to home directory
    
    Examples:
        >>> expand_home_path("~/.openclaw")
        Path("/Users/username/.openclaw")
        
        >>> expand_home_path("/opt/openclaw")
        Path("/opt/openclaw")
    """
    if isinstance(path, Path):
        path_str = str(path)
    else:
        path_str = str(path)
    
    return Path(os.path.expanduser(path_str))


__all__ = [
    "shorten_home_path",
    "expand_home_path",
]
