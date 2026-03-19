"""Browser profile management"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from ..config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


@dataclass
class BrowserProfile:
    """Browser profile configuration"""
    
    name: str
    user_data_dir: Path
    headless: bool = True
    viewport: dict[str, int] = field(default_factory=lambda: {"width": 1280, "height": 720})
    user_agent: str | None = None
    proxy: dict[str, str] | None = None
    extensions: list[Path] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return {
            "name": self.name,
            "user_data_dir": str(self.user_data_dir),
            "headless": self.headless,
            "viewport": self.viewport,
            "user_agent": self.user_agent,
            "proxy": self.proxy,
            "extensions": [str(e) for e in self.extensions],
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BrowserProfile:
        """Create from dictionary"""
        return cls(
            name=data["name"],
            user_data_dir=Path(data["user_data_dir"]),
            headless=data.get("headless", True),
            viewport=data.get("viewport", {"width": 1280, "height": 720}),
            user_agent=data.get("user_agent"),
            proxy=data.get("proxy"),
            extensions=[Path(e) for e in data.get("extensions", [])],
        )


class ProfileManager:
    """Manage browser profiles"""
    
    def __init__(self, profiles_dir: Path | None = None):
        """
        Initialize profile manager
        
        Args:
            profiles_dir: Directory for browser profiles
        """
        if profiles_dir is None:
            profiles_dir = resolve_state_dir() / "profiles"
        
        self.profiles_dir = profiles_dir
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        
        self.profiles: dict[str, BrowserProfile] = {}
        
        # Load profiles
        self._load_profiles()
    
    def _load_profiles(self) -> None:
        """Load profiles from disk"""
        profiles_file = self.profiles_dir / "profiles.json"
        
        if not profiles_file.exists():
            logger.debug("No profiles file found")
            return
        
        try:
            with open(profiles_file) as f:
                data = json.load(f)
            
            for profile_data in data.get("profiles", []):
                profile = BrowserProfile.from_dict(profile_data)
                self.profiles[profile.name] = profile
            
            logger.info(f"Loaded {len(self.profiles)} browser profiles")
            
        except Exception as e:
            logger.error(f"Error loading profiles: {e}", exc_info=True)
    
    def _save_profiles(self) -> None:
        """Save profiles to disk"""
        profiles_file = self.profiles_dir / "profiles.json"
        
        try:
            data = {
                "profiles": [p.to_dict() for p in self.profiles.values()]
            }
            
            with open(profiles_file, "w") as f:
                json.dump(data, f, indent=2)
            
            logger.debug("Saved browser profiles")
            
        except Exception as e:
            logger.error(f"Error saving profiles: {e}", exc_info=True)
    
    def create_profile(
        self,
        name: str,
        headless: bool = True,
        **kwargs
    ) -> BrowserProfile:
        """
        Create new profile
        
        Args:
            name: Profile name
            headless: Headless mode
            **kwargs: Additional profile options
            
        Returns:
            Created profile
        """
        if name in self.profiles:
            raise ValueError(f"Profile '{name}' already exists")
        
        user_data_dir = self.profiles_dir / name
        user_data_dir.mkdir(parents=True, exist_ok=True)
        
        profile = BrowserProfile(
            name=name,
            user_data_dir=user_data_dir,
            headless=headless,
            **kwargs
        )
        
        self.profiles[name] = profile
        self._save_profiles()
        
        logger.info(f"Created profile: {name}")
        
        return profile
    
    def get_profile(self, name: str) -> BrowserProfile | None:
        """Get profile by name"""
        return self.profiles.get(name)
    
    def delete_profile(self, name: str) -> None:
        """Delete profile"""
        if name not in self.profiles:
            raise ValueError(f"Profile '{name}' not found")
        
        profile = self.profiles[name]
        
        # Delete user data directory
        if profile.user_data_dir.exists():
            import shutil
            shutil.rmtree(profile.user_data_dir)
        
        # Remove from profiles
        del self.profiles[name]
        self._save_profiles()
        
        logger.info(f"Deleted profile: {name}")
    
    def list_profiles(self) -> list[str]:
        """List all profile names"""
        return list(self.profiles.keys())
