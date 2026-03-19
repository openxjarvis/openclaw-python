"""Configuration management service.

Handles saving, loading, and applying Gateway configuration changes.
Mirrors TypeScript openclaw/src/config/io.ts including:
- Atomic writes (temp file + rename)
- .bak backup file on every write
- Append-only audit log at ~/.openclaw/logs/config-audit.jsonl
"""

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from ..config.paths import resolve_state_dir

logger = logging.getLogger(__name__)

CONFIG_AUDIT_LOG_FILENAME = "config-audit.jsonl"


class ConfigService:
    """Manages configuration persistence and updates"""
    
    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize config service
        
        Args:
            config_path: Path to config file (optional)
        """
        self.config_path = config_path
        self._current_config: Optional[dict] = None
    
    def load_config(self) -> dict:
        """
        Load configuration from file
        
        Returns:
            Configuration dictionary
        """
        if not self.config_path or not self.config_path.exists():
            logger.warning("No config file found, returning empty config")
            return {}
        
        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            self._current_config = config
            return config
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return {}
    
    def save_config(self, config: dict) -> bool:
        """Save configuration to file.

        Uses atomic write (temp + rename), creates a .bak backup, and appends
        an entry to the audit log — mirrors TS config/io.ts writeConfigFile().

        Args:
            config: Configuration dictionary

        Returns:
            True if successful
        """
        if not self.config_path:
            logger.warning("No config path set, cannot save")
            return False

        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            payload = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
            new_bytes = payload.encode("utf-8")
            new_hash = hashlib.sha256(new_bytes).hexdigest()

            # Compute previous hash for audit record
            prev_hash: Optional[str] = None
            if self.config_path.exists():
                try:
                    prev_bytes = self.config_path.read_bytes()
                    prev_hash = hashlib.sha256(prev_bytes).hexdigest()
                except Exception:
                    pass

            # Create .bak backup
            if self.config_path.exists():
                try:
                    bak_path = Path(str(self.config_path) + ".bak")
                    import shutil
                    shutil.copy2(self.config_path, bak_path)
                except Exception as exc:
                    logger.debug("Config backup failed (non-fatal): %s", exc)

            # Atomic write: temp file → rename
            fd, tmp_path = tempfile.mkstemp(
                dir=self.config_path.parent,
                prefix=".openclaw-cfg-",
                suffix=".json.tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                os.replace(tmp_path, self.config_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            self._current_config = config
            logger.info("Saved configuration to %s", self.config_path)

            # Audit log — mirrors TS config/io.ts lines 381-383
            self._append_audit_log(action="write", prev_hash=prev_hash, next_hash=new_hash)
            return True
        except Exception as exc:
            logger.error("Failed to save config: %s", exc)
            return False

    def _append_audit_log(
        self,
        *,
        action: str,
        prev_hash: Optional[str] = None,
        next_hash: Optional[str] = None,
    ) -> None:
        """Append a JSONL record to ~/.openclaw/logs/config-audit.jsonl.

        Mirrors TS config/io.ts audit log (lines 381-383, 944-990).
        Non-fatal: failures are silently logged at DEBUG level.
        """
        try:
            audit_dir = resolve_state_dir() / "logs"
            audit_dir.mkdir(parents=True, exist_ok=True)
            audit_path = audit_dir / CONFIG_AUDIT_LOG_FILENAME
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "action": action,
                "configPath": str(self.config_path) if self.config_path else None,
                "prevHash": prev_hash,
                "nextHash": next_hash,
            }
            with open(audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as exc:
            logger.debug("Config audit log write failed (non-fatal): %s", exc)
    
    def patch_config(self, patch: dict) -> dict:
        """
        Apply patch to configuration
        
        Args:
            patch: Dictionary of key-value pairs to update
            
        Returns:
            Updated configuration
        """
        if self._current_config is None:
            self._current_config = self.load_config()
        
        # Apply patch (simple merge for now)
        for key, value in patch.items():
            self._set_nested_value(self._current_config, key, value)
        
        # Save updated config
        self.save_config(self._current_config)
        
        return self._current_config
    
    def _set_nested_value(self, config: dict, key_path: str, value: Any):
        """
        Set nested configuration value using dot notation
        
        Args:
            config: Configuration dictionary
            key_path: Key path (e.g., "gateway.auth.token")
            value: Value to set
        """
        keys = key_path.split('.')
        current = config
        
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        
        current[keys[-1]] = value
    
    def get_config_schema(self) -> dict:
        """
        Get configuration schema
        
        Returns:
            JSON schema for configuration
        """
        # Simplified schema - in production this should be comprehensive
        return {
            "type": "object",
            "properties": {
                "gateway": {
                    "type": "object",
                    "properties": {
                        "auth": {
                            "type": "object",
                            "properties": {
                                "mode": {"type": "string", "enum": ["token", "password"]},
                                "token": {"type": "string"},
                                "password": {"type": "string"}
                            }
                        },
                        "bindHost": {"type": "string"},
                        "bindPort": {"type": "integer"}
                    }
                },
                "agents": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "model": {"type": "string"},
                            "temperature": {"type": "number"},
                            "maxTokens": {"type": "integer"}
                        }
                    }
                }
            }
        }


# Global config service instance
_config_service: Optional[ConfigService] = None


def get_config_service() -> ConfigService:
    """Get global config service instance"""
    global _config_service
    if _config_service is None:
        _config_service = ConfigService()
    return _config_service


def set_config_service(service: ConfigService):
    """Set global config service instance"""
    global _config_service
    _config_service = service
