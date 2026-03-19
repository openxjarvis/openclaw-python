"""
Multi-gateway profile support.

This module enables running multiple gateway instances on the same host
with isolated configuration, state, and workspaces.

Reference: openclaw/docs/gateway/multi.md
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from ..config.paths import resolve_state_dir


@dataclass
class GatewayProfile:
    """Gateway profile configuration"""
    
    name: str
    base_port: int  # Gateway WebSocket port
    browser_port: int  # base_port + 2
    canvas_port: int  # base_port + 4
    config_path: Path
    state_path: Path
    workspace_path: Path
    log_path: Path
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return {
            "name": self.name,
            "base_port": self.base_port,
            "browser_port": self.browser_port,
            "canvas_port": self.canvas_port,
            "config_path": str(self.config_path),
            "state_path": str(self.state_path),
            "workspace_path": str(self.workspace_path),
            "log_path": str(self.log_path),
        }


class ProfileManager:
    """
    Manage multiple gateway profiles.
    
    Profiles enable running multiple isolated gateway instances:
    - Separate config files
    - Separate state directories
    - Separate workspaces
    - Different ports (at least 20 apart)
    
    Usage:
        # Create profile
        profile = ProfileManager.create_profile("work", base_port=18789)
        
        # Validate profiles don't conflict
        profiles = [profile1, profile2, profile3]
        ProfileManager.validate_port_spacing(profiles)
        
        # Start gateway with profile
        gateway = GatewayServer(config=profile.config_path, ...)
    """
    
    # Minimum port spacing between profiles
    MIN_PORT_SPACING = 20
    
    @staticmethod
    def create_profile(name: str, base_port: int) -> GatewayProfile:
        """
        Create gateway profile with derived paths and ports.
        
        Port allocation:
        - base_port: Gateway WebSocket
        - base_port + 2: Browser control
        - base_port + 4: Canvas server
        
        Args:
            name: Profile name
            base_port: Base port number
            
        Returns:
            Gateway profile
        """
        home = Path.home()
        
        if name == "default":
            # Default profile uses standard paths
            profile_dir = home / ".openclaw"
        else:
            # Named profiles use profiles subdirectory
            profile_dir = home / ".openclaw" / "profiles" / name
        
        return GatewayProfile(
            name=name,
            base_port=base_port,
            browser_port=base_port + 2,
            canvas_port=base_port + 4,
            config_path=profile_dir / "openclaw.json",
            state_path=profile_dir / "state",
            workspace_path=profile_dir / "workspace",
            log_path=profile_dir / "logs"
        )
    
    @staticmethod
    def validate_port_spacing(profiles: list[GatewayProfile]) -> None:
        """
        Ensure sufficient port spacing between profiles.
        
        Args:
            profiles: List of profiles to validate
            
        Raises:
            ValueError: If port spacing is insufficient
        """
        sorted_profiles = sorted(profiles, key=lambda p: p.base_port)
        
        for i in range(len(sorted_profiles) - 1):
            current = sorted_profiles[i]
            next_profile = sorted_profiles[i + 1]
            
            port_diff = next_profile.base_port - current.base_port
            
            if port_diff < ProfileManager.MIN_PORT_SPACING:
                raise ValueError(
                    f"Profiles '{current.name}' and '{next_profile.name}' "
                    f"must have at least {ProfileManager.MIN_PORT_SPACING} "
                    f"ports spacing (current: {port_diff})"
                )
    
    @staticmethod
    def list_profiles() -> list[str]:
        """
        List available profiles.
        
        Returns:
            List of profile names
        """
        profiles = ["default"]
        
        profiles_dir = resolve_state_dir() / "profiles"
        if profiles_dir.exists():
            for item in profiles_dir.iterdir():
                if item.is_dir():
                    profiles.append(item.name)
        
        return profiles
    
    @staticmethod
    def get_profile(name: str) -> GatewayProfile | None:
        """
        Get profile by name.
        
        Args:
            name: Profile name
            
        Returns:
            Gateway profile if found, None otherwise
        """
        # Try to determine base port from config
        profile = ProfileManager.create_profile(name, base_port=18789)
        
        if profile.config_path.exists():
            try:
                import json
                with open(profile.config_path, 'r') as f:
                    config = json.load(f)
                    # Try to get port from config
                    gateway_config = config.get('gateway', {})
                    if 'port' in gateway_config:
                        profile.base_port = gateway_config['port']
                        profile.browser_port = profile.base_port + 2
                        profile.canvas_port = profile.base_port + 4
            except Exception:
                pass
        
        return profile
    
    @staticmethod
    def delete_profile(name: str) -> bool:
        """
        Delete profile (removes all data).
        
        Args:
            name: Profile name
            
        Returns:
            True if deleted, False if not found or default
        """
        if name == "default":
            raise ValueError("Cannot delete default profile")
        
        profile_dir = resolve_state_dir() / "profiles" / name
        if profile_dir.exists():
            import shutil
            shutil.rmtree(profile_dir)
            return True
        
        return False


__all__ = [
    "GatewayProfile",
    "ProfileManager",
]
