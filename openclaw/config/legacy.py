"""Legacy config migration (matches TypeScript config/legacy.ts + legacy-migrate.ts)

Handles migration from older config formats:
- ~/.clawdbot/clawdbot.json -> ~/.openclaw/openclaw.json
- ~/.moltbot/moltbot.json -> ~/.openclaw/openclaw.json
"""
from __future__ import annotations


import json
import logging
import shutil
from pathlib import Path
from typing import Any
from openclaw.config.paths import resolve_state_dir

logger = logging.getLogger(__name__)

# Legacy config paths to check
LEGACY_PATHS = [
    Path.home() / ".clawdbot" / "clawdbot.json",
    Path.home() / ".moltbot" / "moltbot.json",
]

# Current config path
CURRENT_PATH = resolve_state_dir() / "openclaw.json"


def detect_legacy_config() -> Path | None:
    """
    Detect if a legacy config file exists.
    
    Returns:
        Path to legacy config, or None
    """
    for path in LEGACY_PATHS:
        if path.exists():
            logger.info(f"Legacy config detected: {path}")
            return path
    return None


def migrate_legacy_config(
    legacy_path: Path | None = None,
    target_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Migrate legacy config to current format.
    
    Args:
        legacy_path: Path to legacy config (auto-detected if None)
        target_path: Target path for migrated config
        dry_run: If True, show what would be done without writing
    
    Returns:
        Migration result dict
    """
    if legacy_path is None:
        legacy_path = detect_legacy_config()
    
    if legacy_path is None:
        return {"migrated": False, "reason": "no_legacy_config_found"}
    
    if target_path is None:
        target_path = CURRENT_PATH
    
    # Check if current config already exists
    if target_path.exists():
        return {
            "migrated": False,
            "reason": "current_config_exists",
            "legacy_path": str(legacy_path),
            "current_path": str(target_path),
        }
    
    try:
        # Read legacy config
        with open(legacy_path, encoding="utf-8") as f:
            legacy_data = json.load(f)
        
        # Apply migrations
        migrated_data = _apply_migrations(legacy_data, legacy_path)
        
        if dry_run:
            return {
                "migrated": False,
                "dry_run": True,
                "legacy_path": str(legacy_path),
                "target_path": str(target_path),
                "changes": migrated_data.get("_changes", []),
            }
        
        # Remove internal tracking
        migrated_data.pop("_changes", None)
        
        # Write migrated config
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(migrated_data, f, indent=2)
        
        # Backup legacy config
        backup_path = legacy_path.with_suffix(".json.bak")
        shutil.copy2(legacy_path, backup_path)
        
        logger.info(f"Migrated config from {legacy_path} to {target_path}")
        
        return {
            "migrated": True,
            "legacy_path": str(legacy_path),
            "target_path": str(target_path),
            "backup_path": str(backup_path),
        }
    
    except Exception as e:
        logger.error(f"Config migration failed: {e}")
        return {"migrated": False, "error": str(e)}


def _apply_migrations(
    data: dict[str, Any],
    source_path: Path,
) -> dict[str, Any]:
    """Apply migration rules to legacy config"""
    changes = []
    
    # Rename known old keys
    key_renames = {
        "clawdbot": "agent",
        "moltbot": "agent",
        "bot": "agent",
    }
    
    for old_key, new_key in key_renames.items():
        if old_key in data and new_key not in data:
            data[new_key] = data.pop(old_key)
            changes.append(f"Renamed '{old_key}' -> '{new_key}'")
    
    # Migrate gateway port
    if "gateway" in data:
        gw = data["gateway"]
        if "websocketPort" in gw and "port" not in gw:
            gw["port"] = gw.pop("websocketPort")
            changes.append("Renamed 'gateway.websocketPort' -> 'gateway.port'")
    
    # Migrate tools.exec
    if "tools" in data:
        tools = data["tools"]
        if "exec" in tools and isinstance(tools["exec"], str):
            # Old format: tools.exec was just "full"/"deny"
            old_security = tools["exec"]
            tools["exec"] = {"security": old_security}
            changes.append(f"Migrated 'tools.exec' from string to object")
    
    # Add meta
    data.setdefault("meta", {})
    data["meta"]["lastTouchedVersion"] = "1.0.0"
    data["meta"]["lastTouchedAt"] = __import__("datetime").datetime.now().isoformat()
    
    data["_changes"] = changes
    
    return data
