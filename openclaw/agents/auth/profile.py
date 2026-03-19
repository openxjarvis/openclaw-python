"""
Auth profile data structures
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
import sys

# Python 3.9 compatibility
if sys.version_info >= (3, 11):
    from datetime import UTC
else:
    UTC = timezone.utc
from pathlib import Path
from typing import Any, Optional, Dict, List
from openclaw.config.paths import resolve_state_dir


def calculate_auth_profile_cooldown_ms(error_count: int) -> int:
    """Compute exponential cooldown in milliseconds for an auth profile.

    Mirrors TS calculateAuthProfileCooldownMs():
      schedule: 1min, 5min, 25min, 1h (capped)
      formula:  min(1h, 1min * 5^(min(errorCount-1, 3)))
    """
    normalized = max(1, error_count)
    ms = 60_000 * (5 ** min(normalized - 1, 3))
    return min(60 * 60 * 1000, ms)  # cap at 1 hour


@dataclass
class AuthProfile:
    """Authentication profile for API access.

    Aligned with TS AuthProfileEntry in auth-profiles/usage.ts.

    Attributes:
        id: Unique profile identifier
        provider: Provider name (anthropic, openai, etc.)
        api_key: API key (can be env var name)
        type: Credential type (api_key, token, or oauth) - mirrors TS
        last_used: Last time this profile was used
        failure_count: Deprecated alias for error_count
        error_count: Number of consecutive API errors (drives cooldown schedule)
        cooldown_until: When this profile becomes available again (transient)
        disabled_until: Long-term billing-related disable expiry (e.g. 5h/24h)
        disabled_reason: Reason for billing-level disable
        metadata: Additional profile metadata
    """

    id: str
    provider: str
    api_key: str
    type: str = "api_key"           # NEW: mirrors TS type field (api_key|token|oauth)
    last_used: Optional[datetime] = None
    failure_count: int = 0          # legacy alias
    error_count: int = 0            # mirrors TS errorCount
    cooldown_until: Optional[datetime] = None
    disabled_until: Optional[datetime] = None    # mirrors TS disabledUntil
    disabled_reason: Optional[str] = None        # mirrors TS disabledReason
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Keep failure_count and error_count in sync
        if self.failure_count and not self.error_count:
            self.error_count = self.failure_count
        elif self.error_count and not self.failure_count:
            self.failure_count = self.error_count

    def is_available(self) -> bool:
        """Check if profile is available (not in cooldown AND not billing-disabled)."""
        now = datetime.now(UTC)
        if self.disabled_until is not None and now < self.disabled_until:
            return False
        if self.cooldown_until is not None and now < self.cooldown_until:
            return False
        return True

    def is_billing_disabled(self) -> bool:
        """Return True if currently in a billing-level disable window."""
        if self.disabled_until is None:
            return False
        return datetime.now(UTC) < self.disabled_until

    def get_api_key(self) -> str:
        """
        Get actual API key value

        If api_key starts with '$', treat as env var name
        """
        if self.api_key.startswith("$"):
            env_var = self.api_key[1:]
            return os.getenv(env_var, "")
        return self.api_key

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "provider": self.provider,
            "api_key": self.api_key,
            "type": self.type,  # NEW: include type field
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "failure_count": self.failure_count,
            "errorCount": self.error_count,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            "disabledUntil": self.disabled_until.isoformat() if self.disabled_until else None,
            "disabledReason": self.disabled_reason,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AuthProfile":
        """Create from dictionary."""
        error_count = data.get("errorCount", data.get("error_count", data.get("failure_count", 0)))
        disabled_until_raw = data.get("disabledUntil") or data.get("disabled_until")
        return cls(
            id=data["id"],
            provider=data["provider"],
            api_key=data["api_key"],
            type=data.get("type", "api_key"),  # NEW: load type field
            last_used=datetime.fromisoformat(data["last_used"]) if data.get("last_used") else None,
            failure_count=data.get("failure_count", error_count),
            error_count=error_count,
            cooldown_until=(
                datetime.fromisoformat(data["cooldown_until"])
                if data.get("cooldown_until")
                else None
            ),
            disabled_until=(
                datetime.fromisoformat(disabled_until_raw)
                if disabled_until_raw
                else None
            ),
            disabled_reason=data.get("disabledReason") or data.get("disabled_reason"),
            metadata=data.get("metadata", {}),
        )


class ProfileStore:
    """Store and manage authentication profiles.
    
    Aligned with TS AuthProfileStore in auth-profiles/types.ts:60-72.
    """

    def __init__(self, config_dir: Optional[Path] = None):
        """
        Initialize profile store

        Args:
            config_dir: Directory to store profile config
        """
        self.config_dir = config_dir or resolve_state_dir()
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "auth_profiles.json"

        self.profiles: Dict[str, AuthProfile] = {}
        
        # NEW: Align with TS AuthProfileStore (types.ts:60-72)
        self.usage_stats: Dict[str, Dict[str, Any]] = {}  # Per-profile usage statistics
        self.order: Dict[str, List[str]] = {}              # Per-agent preferred profile order
        self.last_good: Dict[str, str] = {}                 # Last successful profile per provider
        
        self._load()

    def add_profile(self, profile: AuthProfile) -> None:
        """Add or update a profile"""
        self.profiles[profile.id] = profile
        self._save()

    def get_profile(self, profile_id: str) -> Optional[AuthProfile]:
        """Get profile by ID"""
        return self.profiles.get(profile_id)

    def list_profiles(self, provider: Optional[str] = None) -> List[AuthProfile]:
        """
        List profiles, optionally filtered by provider

        Args:
            provider: Filter by provider (optional)

        Returns:
            List of profiles
        """
        profiles = list(self.profiles.values())
        if provider:
            profiles = [p for p in profiles if p.provider == provider]
        return profiles

    def remove_profile(self, profile_id: str) -> bool:
        """Remove a profile"""
        if profile_id in self.profiles:
            del self.profiles[profile_id]
            self._save()
            return True
        return False

    def _load(self) -> None:
        """Load profiles from disk"""
        if not self.config_file.exists():
            return

        try:
            with open(self.config_file) as f:
                data = json.load(f)
                
                # Load profiles
                if "profiles" in data:
                    self.profiles = {pid: AuthProfile.from_dict(pdata) for pid, pdata in data["profiles"].items()}
                else:
                    # Legacy format (flat dict)
                    self.profiles = {pid: AuthProfile.from_dict(pdata) for pid, pdata in data.items()}
                
                # Load usage_stats (NEW)
                self.usage_stats = data.get("usageStats", {})
                
                # Load order (NEW)
                self.order = data.get("order", {})
                
                # Load last_good (NEW)
                self.last_good = data.get("lastGood", {})
                
        except Exception as e:
            print(f"Warning: Failed to load profiles: {e}")

    def _save(self) -> None:
        """Save profiles to disk"""
        try:
            data = {
                "version": 1,
                "profiles": {pid: p.to_dict() for pid, p in self.profiles.items()},
                "usageStats": self.usage_stats,  # NEW
                "order": self.order,              # NEW
                "lastGood": self.last_good,       # NEW
            }
            with open(self.config_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Warning: Failed to save profiles: {e}")
