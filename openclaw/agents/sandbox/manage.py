"""Sandbox container management

Lists and removes sandbox containers, cross-referencing the persistent
registry with live Docker state.

Mirrors TypeScript openclaw/src/agents/sandbox/manage.ts
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .docker import exec_docker, docker_container_state
from ...config.paths import resolve_state_dir

logger = logging.getLogger(__name__)

# Registry paths (mirrors TS SANDBOX_REGISTRY_PATH)
_STATE_DIR = str(resolve_state_dir() / "state")
_SANDBOX_REGISTRY_PATH = str(Path(_STATE_DIR) / "sandbox" / "containers.json")
_SANDBOX_BROWSER_REGISTRY_PATH = str(Path(_STATE_DIR) / "sandbox" / "browsers.json")


@dataclass
class SandboxContainerInfo:
    """Live info about a tracked sandbox container.

    Mirrors TS ``SandboxContainerInfo``.
    """

    container_name: str
    image: str
    running: bool
    image_match: bool
    created_at_ms: int
    last_used_at_ms: int
    session_key: str
    scope_key: str | None = None
    browser: Any | None = None  # SandboxBrowserInfo placeholder


async def _read_registry(path: str) -> list[dict]:
    """Read sandbox registry file; return empty list on any error."""
    try:
        data = json.loads(Path(path).read_text())
        entries = data.get("entries", []) if isinstance(data, dict) else []
        return entries if isinstance(entries, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


async def _resolve_agent_id(session_key: str) -> str:
    """Extract agent id from session key (mirrors TS resolveSandboxAgentId)."""
    return session_key.split(":")[0] if ":" in session_key else session_key


async def list_sandbox_containers(
    registry_path: str | None = None,
) -> list[SandboxContainerInfo]:
    """Return live status for every entry in the sandbox registry.

    Mirrors TS ``listSandboxContainers()``.
    """
    rpath = registry_path or _SANDBOX_REGISTRY_PATH
    entries = await _read_registry(rpath)

    # Try to get configured image from config for image-match check
    configured_image: str | None = None
    try:
        from openclaw.config.loader import load_config  # type: ignore[import]
        from .config import resolve_sandbox_config_for_agent
        cfg = load_config()
        # Use default agent config; refined per-entry below
        default_cfg = resolve_sandbox_config_for_agent(cfg, None)
        configured_image = default_cfg.docker.image
    except (ImportError, Exception):
        pass

    results: list[SandboxContainerInfo] = []
    for entry in entries:
        cname = entry.get("containerName", "")
        state = await docker_container_state(cname)
        actual_image = entry.get("image", "")

        # Try to get actual image from running container
        if state.get("exists"):
            try:
                r = await exec_docker(
                    ["inspect", "-f", "{{.Config.Image}}", cname],
                    allow_failure=True,
                )
                if r["code"] == 0:
                    actual_image = r["stdout"].strip()
            except Exception:
                pass

        # Per-entry configured image
        per_entry_image = configured_image or entry.get("image", "")
        try:
            session_key = entry.get("sessionKey", "")
            agent_id = await _resolve_agent_id(session_key)
            from openclaw.config.loader import load_config  # type: ignore[import]
            from .config import resolve_sandbox_config_for_agent
            cfg = load_config()
            per_entry_image = resolve_sandbox_config_for_agent(cfg, agent_id).docker.image
        except (ImportError, Exception):
            pass

        results.append(SandboxContainerInfo(
            container_name=cname,
            image=actual_image,
            running=state.get("running", False),
            image_match=actual_image == per_entry_image,
            created_at_ms=entry.get("createdAtMs", 0),
            last_used_at_ms=entry.get("lastUsedAtMs", 0),
            session_key=entry.get("sessionKey", ""),
        ))

    return results


async def remove_sandbox_container(
    container_name: str,
    registry_path: str | None = None,
) -> None:
    """Force-remove a container and delete its registry entry.

    Mirrors TS ``removeSandboxContainer()``.
    """
    # Stop/remove the container (ignore errors)
    try:
        await exec_docker(["rm", "-f", container_name], allow_failure=True)
    except Exception:
        pass

    # Remove from persistent registry
    rpath = registry_path or _SANDBOX_REGISTRY_PATH
    await _remove_registry_entry(container_name, rpath)


async def _remove_registry_entry(container_name: str, registry_path: str) -> None:
    """Remove a single entry from the JSON registry file."""
    try:
        p = Path(registry_path)
        if not p.exists():
            return
        data = json.loads(p.read_text())
        if not isinstance(data, dict):
            return
        entries: list[dict] = data.get("entries", [])
        data["entries"] = [e for e in entries if e.get("containerName") != container_name]
        p.write_text(json.dumps(data, indent=2))
    except (FileNotFoundError, json.JSONDecodeError, OSError, PermissionError) as exc:
        logger.warning("Could not update sandbox registry %s: %s", registry_path, exc)


async def ensure_docker_container_is_running(container_name: str) -> None:
    """Start a stopped container.

    Mirrors TS ``ensureDockerContainerIsRunning()``.
    """
    state = await docker_container_state(container_name)
    if state.get("exists") and not state.get("running"):
        await exec_docker(["start", container_name])
