"""
Workspace settings manager for per-project configuration
"""
from __future__ import annotations


import json
import logging
from pathlib import Path
from typing import Any
from openclaw.config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


class WorkspaceSettings:
    """
    Manage workspace-specific settings

    Settings are stored in .openclaw/settings.json within the workspace
    and override global configuration values.

    Example:
        settings = WorkspaceSettings(Path("/path/to/project"))
        settings.set("model", "anthropic/claude-sonnet-4")
        model = settings.get("model")
    """

    DEFAULT_SETTINGS = {
        "model": "anthropic/claude-opus-4-5",
        "max_tokens": 4096,
        "temperature": 0.7,
        "thinking_mode": "off",
        "tool_format": "markdown",
        "enable_queuing": True,
        "compaction_strategy": "keep_important",
    }

    def __init__(self, workspace_dir: Path):
        """
        Initialize workspace settings

        Args:
            workspace_dir: Path to workspace directory
        """
        self.workspace_dir = workspace_dir
        self.settings_dir = workspace_dir / ".openclaw"
        self.settings_file = self.settings_dir / "settings.json"

        # Create directory if needed
        self.settings_dir.mkdir(parents=True, exist_ok=True)

        # Load settings
        self._settings: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load settings from disk"""
        if self.settings_file.exists():
            try:
                with open(self.settings_file) as f:
                    self._settings = json.load(f)
                logger.debug(f"Loaded workspace settings from {self.settings_file}")
            except Exception as e:
                logger.warning(f"Failed to load workspace settings: {e}")
                self._settings = {}
        else:
            self._settings = {}

    def _save(self) -> None:
        """Save settings to disk"""
        try:
            with open(self.settings_file, "w") as f:
                json.dump(self._settings, f, indent=2)
            logger.debug(f"Saved workspace settings to {self.settings_file}")
        except Exception as e:
            logger.error(f"Failed to save workspace settings: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a setting value

        Args:
            key: Setting key
            default: Default value if not found

        Returns:
            Setting value or default
        """
        # Check workspace settings first
        if key in self._settings:
            return self._settings[key]

        # Fall back to defaults
        if key in self.DEFAULT_SETTINGS:
            return self.DEFAULT_SETTINGS[key]

        return default

    def set(self, key: str, value: Any) -> None:
        """
        Set a setting value

        Args:
            key: Setting key
            value: Setting value
        """
        self._settings[key] = value
        self._save()
        logger.info(f"Set workspace setting: {key} = {value}")

    def delete(self, key: str) -> bool:
        """
        Delete a setting

        Args:
            key: Setting key

        Returns:
            True if deleted, False if not found
        """
        if key in self._settings:
            del self._settings[key]
            self._save()
            logger.info(f"Deleted workspace setting: {key}")
            return True
        return False

    def reset(self, key: str | None = None) -> None:
        """
        Reset setting(s) to default

        Args:
            key: Setting key to reset, or None to reset all
        """
        if key is None:
            self._settings = {}
            self._save()
            logger.info("Reset all workspace settings to default")
        elif key in self._settings:
            del self._settings[key]
            self._save()
            logger.info(f"Reset workspace setting: {key}")

    def list_all(self) -> dict[str, Any]:
        """
        List all settings (including defaults)

        Returns:
            Dictionary of all settings
        """
        result = self.DEFAULT_SETTINGS.copy()
        result.update(self._settings)
        return result

    def list_custom(self) -> dict[str, Any]:
        """
        List only custom settings (excluding defaults)

        Returns:
            Dictionary of custom settings
        """
        return self._settings.copy()

    def export_to_file(self, file_path: Path) -> None:
        """
        Export settings to a file

        Args:
            file_path: Path to export file
        """
        with open(file_path, "w") as f:
            json.dump(self.list_all(), f, indent=2)
        logger.info(f"Exported settings to {file_path}")

    def import_from_file(self, file_path: Path) -> None:
        """
        Import settings from a file

        Args:
            file_path: Path to import file
        """
        with open(file_path) as f:
            imported = json.load(f)

        self._settings.update(imported)
        self._save()
        logger.info(f"Imported settings from {file_path}")


class SettingsManager:
    """
    Global settings manager

    Manages settings for multiple workspaces with inheritance:
    workspace settings > global settings > defaults
    """

    def __init__(self, global_config_dir: Path | None = None):
        """
        Initialize settings manager

        Args:
            global_config_dir: Global config directory (defaults to ~/.openclaw)
        """
        self.global_config_dir = global_config_dir or resolve_state_dir()
        self.global_config_dir.mkdir(parents=True, exist_ok=True)

        self._workspace_settings: dict[Path, WorkspaceSettings] = {}

    def get_workspace_settings(self, workspace_dir: Path) -> WorkspaceSettings:
        """
        Get settings for a workspace

        Args:
            workspace_dir: Workspace directory

        Returns:
            WorkspaceSettings instance
        """
        if workspace_dir not in self._workspace_settings:
            self._workspace_settings[workspace_dir] = WorkspaceSettings(workspace_dir)

        return self._workspace_settings[workspace_dir]

    def list_workspaces(self) -> list[Path]:
        """
        List all known workspaces

        Returns:
            List of workspace paths
        """
        return list(self._workspace_settings.keys())
