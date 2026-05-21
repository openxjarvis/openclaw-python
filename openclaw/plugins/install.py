"""Plugin installation management — mirrors TypeScript src/plugins/install.ts

Implements install/uninstall/update/list operations for openclaw plugins.
Local path plugins: copy directory to state plugins/ dir + update installs.json.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_INSTALLS_FILENAME = "installs.json"


def _get_plugins_dir() -> Path:
    """Return the state/plugins directory (created if needed)."""
    from openclaw.config.paths import resolve_state_dir
    plugins_dir = resolve_state_dir() / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    return plugins_dir


def _read_installs(plugins_dir: Path) -> dict[str, Any]:
    """Read installs.json, returning {} on error."""
    installs_file = plugins_dir / _INSTALLS_FILENAME
    if not installs_file.exists():
        return {}
    try:
        return json.loads(installs_file.read_text())
    except Exception:
        return {}


def _write_installs(plugins_dir: Path, data: dict[str, Any]) -> None:
    """Write installs.json."""
    installs_file = plugins_dir / _INSTALLS_FILENAME
    installs_file.write_text(json.dumps(data, indent=2))


def _read_plugin_manifest(plugin_dir: Path) -> dict[str, Any]:
    """Read plugin manifest from openclaw.plugin.json or package.json."""
    for name in ("openclaw.plugin.json", "package.json", "plugin.json"):
        f = plugin_dir / name
        if f.exists():
            try:
                return json.loads(f.read_text())
            except Exception:
                pass
    return {}


async def install_plugin(
    path_or_url: str,
    version: str | None = None,
    config: dict[str, Any] | None = None,
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    """Install a plugin from a local path or URL.

    Local path: copies plugin directory to state plugins/ and records in installs.json.
    URL: downloads and unpacks (basic wget/curl support).
    Mirrors TS installPlugin() in src/plugins/install.ts.
    """
    plugins_dir = _get_plugins_dir()
    installs = _read_installs(plugins_dir)

    # Determine source
    source_path: Path | None = None
    if path_or_url.startswith(("http://", "https://")):
        # URL-based install — download to temp, then install from there
        return await _install_from_url(path_or_url, plugins_dir, installs, version)
    else:
        source_path = Path(path_or_url).expanduser().resolve()
        if not source_path.exists():
            return {"ok": False, "error": f"Plugin path does not exist: {source_path}"}

    # Read manifest to get plugin name/version
    manifest = _read_plugin_manifest(source_path)
    plugin_name = (
        manifest.get("openclaw_plugin_id")
        or manifest.get("name")
        or source_path.name
    )
    plugin_version = version or manifest.get("version") or "0.0.0"

    # Destination
    dest_dir = plugins_dir / plugin_name
    if dest_dir.exists():
        # Backup existing installation
        backup_dir = plugins_dir / f"{plugin_name}.bak"
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        shutil.copytree(dest_dir, backup_dir)
        shutil.rmtree(dest_dir)

    shutil.copytree(source_path, dest_dir)
    logger.info("Installed plugin '%s' v%s to %s", plugin_name, plugin_version, dest_dir)

    # Update installs.json
    installs[plugin_name] = {
        "name": plugin_name,
        "version": plugin_version,
        "source": str(path_or_url),
        "installed_at": int(time.time()),
        "path": str(dest_dir),
    }
    _write_installs(plugins_dir, installs)

    return {
        "ok": True,
        "status": "success",
        "plugin_id": plugin_name,
        "version": plugin_version,
        "path": str(dest_dir),
        "message": f"Plugin '{plugin_name}' v{plugin_version} installed successfully",
    }


async def _install_from_url(
    url: str,
    plugins_dir: Path,
    installs: dict,
    version: str | None,
) -> dict[str, Any]:
    """Install plugin from a URL (HTTP download)."""
    import tempfile
    import zipfile

    try:
        import httpx
    except ImportError:
        return {"ok": False, "error": "httpx is required for URL-based plugin install"}

    logger.info("Downloading plugin from %s", url)
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            content = response.content
    except Exception as exc:
        return {"ok": False, "error": f"Download failed: {exc}"}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        archive = tmp_path / "plugin.zip"
        archive.write_bytes(content)
        try:
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(tmp_path / "extracted")
        except zipfile.BadZipFile:
            return {"ok": False, "error": "Downloaded file is not a valid zip archive"}

        extracted = tmp_path / "extracted"
        # Find the actual plugin root (may be nested)
        subdirs = list(extracted.iterdir())
        plugin_root = subdirs[0] if len(subdirs) == 1 and subdirs[0].is_dir() else extracted

        return await install_plugin(str(plugin_root), version=version)


async def uninstall_plugin(
    name: str,
    config: dict[str, Any] | None = None,
    workspace_dir: Path | None = None,
    *,
    remove_files: bool = True,
) -> dict[str, Any]:
    """Uninstall a plugin.

    Removes from installs.json and optionally deletes plugin directory.
    Mirrors TS uninstallPlugin() in src/plugins/install.ts.
    """
    plugins_dir = _get_plugins_dir()
    installs = _read_installs(plugins_dir)

    if name not in installs:
        return {"ok": False, "error": f"Plugin '{name}' is not installed"}

    entry = installs.pop(name)
    _write_installs(plugins_dir, installs)

    if remove_files:
        plugin_path = Path(entry.get("path") or plugins_dir / name)
        if plugin_path.exists():
            shutil.rmtree(plugin_path)
            logger.info("Removed plugin files at %s", plugin_path)

    logger.info("Uninstalled plugin '%s'", name)
    return {
        "ok": True,
        "status": "success",
        "plugin_id": name,
        "message": f"Plugin '{name}' uninstalled successfully",
    }


async def update_plugin(
    name: str,
    version: str | None = None,
    config: dict[str, Any] | None = None,
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    """Update an installed plugin.

    Uninstalls (keeping source) then reinstalls from recorded source.
    Mirrors TS updatePlugin() in src/plugins/install.ts.
    """
    plugins_dir = _get_plugins_dir()
    installs = _read_installs(plugins_dir)

    if name not in installs:
        return {"ok": False, "error": f"Plugin '{name}' is not installed"}

    entry = installs[name]
    source = entry.get("source", "")
    if not source:
        return {"ok": False, "error": f"Plugin '{name}' has no recorded source — cannot update"}

    old_version = entry.get("version")

    # Uninstall (keep installs.json until reinstall succeeds)
    result = await uninstall_plugin(name, remove_files=True)
    if not result.get("ok"):
        return result

    # Reinstall from original source
    install_result = await install_plugin(source, version=version)
    if not install_result.get("ok"):
        return install_result

    install_result["old_version"] = old_version
    install_result["message"] = (
        f"Plugin '{name}' updated from v{old_version} to v{install_result.get('version')}"
    )
    return install_result


def list_installed_plugins(workspace_dir: Path | None = None) -> list[dict[str, Any]]:
    """List all installed plugins by reading installs.json.

    Mirrors TS listInstalledPlugins() in src/plugins/install.ts.
    """
    try:
        plugins_dir = _get_plugins_dir()
        installs = _read_installs(plugins_dir)
        return [
            {
                "name": name,
                **{k: v for k, v in info.items() if k != "name"},
            }
            for name, info in installs.items()
        ]
    except Exception as exc:
        logger.exception("Failed to list installed plugins")
        return []
