"""Sandbox context resolution

Builds a :class:`SandboxContext` that describes the running sandbox container,
workspace paths, access mode, and file-system bridge.  Mirrors the TypeScript
``resolveSandboxContext()`` in ``openclaw/src/agents/sandbox/context.ts``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import DEFAULT_SANDBOX_IMAGE, SANDBOX_AGENT_WORKSPACE_MOUNT
from .docker import DockerSandbox, DockerSandboxConfig, docker_container_state
from .fs_bridge import SandboxFsBridge, create_sandbox_fs_bridge
from openclaw.config.paths import resolve_state_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class SandboxToolPolicy:
    """Allow / deny lists for tools inside a sandbox session.

    Matches TS ``SandboxToolPolicy``.
    """

    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


@dataclass
class SandboxBrowserContext:
    """Browser bridge details for a sandbox container."""

    bridge_url: str
    no_vnc_url: str | None = None
    container_name: str = ""


@dataclass
class SandboxContext:
    """Complete runtime context for a sandboxed session.

    Mirrors TS ``SandboxContext``.
    """

    enabled: bool
    session_key: str
    workspace_dir: str
    agent_workspace_dir: str
    workspace_access: str  # "none" | "ro" | "rw"
    container_name: str
    container_workdir: str  # e.g. /workspace
    docker: DockerSandboxConfig
    tools: SandboxToolPolicy = field(default_factory=SandboxToolPolicy)
    browser_allow_host_control: bool = False
    browser: SandboxBrowserContext | None = None
    fs_bridge: SandboxFsBridge | None = None


@dataclass
class SandboxWorkspaceInfo:
    """Minimal workspace info (no container required)."""

    workspace_dir: str
    container_workdir: str


# ---------------------------------------------------------------------------
# Scope helpers
# ---------------------------------------------------------------------------

_DEFAULT_WORKSPACE_ROOT = str(resolve_state_dir() / "sandboxes")


def resolve_sandbox_scope_key(scope: str, session_key: str) -> str:
    """Map *scope* + *session_key* to a stable directory-safe key.

    Delegates to the canonical implementation in session_workspace.py which
    correctly mirrors TS resolveSandboxScopeKey() — including using
    resolveAgentIdFromSessionKey for scope="agent".
    """
    from openclaw.agents.session_workspace import resolve_sandbox_scope_key as _canonical
    return _canonical(scope, session_key)


def resolve_sandbox_workspace_dir(workspace_root: str, scope_key: str) -> str:
    """Build the per-scope workspace directory path.

    Delegates to the canonical slugify-based implementation in session_workspace.py.
    Mirrors TS ``resolveSandboxWorkspaceDir()``.
    """
    from openclaw.agents.session_workspace import resolve_session_workspace_dir
    return str(resolve_session_workspace_dir(workspace_root, scope_key))


# ---------------------------------------------------------------------------
# Main factory
# ---------------------------------------------------------------------------


async def resolve_sandbox_context(
    session_key: str,
    workspace_dir: str | None = None,
    config: dict[str, Any] | None = None,
) -> SandboxContext | None:
    """Build a :class:`SandboxContext` for *session_key*.

    Returns ``None`` if sandboxing is disabled for this session.

    Args:
        session_key: Active session identifier.
        workspace_dir: Host-side agent workspace directory (overrides default).
        config: Optional OpenClaw config dict (``tools``, ``sandbox``, …).

    Mirrors TS ``resolveSandboxContext()``.
    """
    cfg = config or {}
    sandbox_cfg = cfg.get("sandbox", {})

    mode = sandbox_cfg.get("mode", "off")
    if mode == "off":
        logger.debug("Sandbox mode is 'off'; skipping sandbox context resolution")
        return None

    # Resolve workspace paths (defaults aligned with TS config.ts)
    scope = sandbox_cfg.get("scope", "agent")
    workspace_access = sandbox_cfg.get("workspaceAccess", "none")
    workspace_root = sandbox_cfg.get("workspaceRoot", _DEFAULT_WORKSPACE_ROOT)

    agent_workspace_dir = str(
        Path(workspace_dir).resolve() if workspace_dir else resolve_state_dir() / "workspace"
    )
    scope_key = resolve_sandbox_scope_key(scope, session_key)
    sandbox_workspace_dir = (
        workspace_root
        if scope == "shared"
        else resolve_sandbox_workspace_dir(workspace_root, scope_key)
    )

    # When workspace_access is rw the agent workspace IS the container workspace
    effective_workspace_dir = (
        agent_workspace_dir if workspace_access == "rw" else sandbox_workspace_dir
    )

    # Ensure workspace directory exists
    Path(effective_workspace_dir).mkdir(parents=True, exist_ok=True)

    # Docker configuration
    docker_section = sandbox_cfg.get("docker", {})
    docker_config = DockerSandboxConfig(
        image=docker_section.get("image", DEFAULT_SANDBOX_IMAGE),
        memory=docker_section.get("memory"),
        cpus=docker_section.get("cpus"),
        workspace_access=workspace_access,
        network_mode=docker_section.get("network", "none"),
        env=docker_section.get("env", {}),
        volumes=docker_section.get("volumes", {}),
    )

    container_workdir = docker_section.get("workdir", SANDBOX_AGENT_WORKSPACE_MOUNT)

    # Ensure sandbox container is running
    sandbox = DockerSandbox(config=docker_config, workspace_dir=Path(effective_workspace_dir))
    try:
        container_name = await sandbox.start()
    except Exception as exc:
        logger.error(f"Failed to start sandbox container: {exc}", exc_info=True)
        return None

    # Tool policy
    tools_section = sandbox_cfg.get("tools", {})
    tool_policy = SandboxToolPolicy(
        allow=tools_section.get("allow", []),
        deny=tools_section.get("deny", []),
    )

    # Build context
    context = SandboxContext(
        enabled=True,
        session_key=session_key,
        workspace_dir=effective_workspace_dir,
        agent_workspace_dir=agent_workspace_dir,
        workspace_access=workspace_access,
        container_name=container_name,
        container_workdir=container_workdir,
        docker=docker_config,
        tools=tool_policy,
    )

    # Attach fs bridge
    context.fs_bridge = create_sandbox_fs_bridge(
        container_name=container_name,
        workspace_dir=effective_workspace_dir,
        container_workdir=container_workdir,
        workspace_access=workspace_access,
    )

    return context


async def get_sandbox_workspace_info(
    session_key: str,
    workspace_dir: str | None = None,
    config: dict[str, Any] | None = None,
) -> SandboxWorkspaceInfo | None:
    """Return workspace info without starting a container.

    Mirrors TS ``ensureSandboxWorkspaceForSession()``.
    """
    cfg = config or {}
    sandbox_cfg = cfg.get("sandbox", {})
    if sandbox_cfg.get("mode", "off") == "off":
        return None

    scope = sandbox_cfg.get("scope", "agent")
    workspace_access = sandbox_cfg.get("workspaceAccess", "none")
    workspace_root = sandbox_cfg.get("workspaceRoot", _DEFAULT_WORKSPACE_ROOT)
    docker_section = sandbox_cfg.get("docker", {})
    container_workdir = docker_section.get("workdir", SANDBOX_AGENT_WORKSPACE_MOUNT)

    agent_workspace_dir = str(
        Path(workspace_dir).resolve() if workspace_dir else resolve_state_dir() / "workspace"
    )
    scope_key = resolve_sandbox_scope_key(scope, session_key)
    sandbox_workspace_dir = (
        workspace_root
        if scope == "shared"
        else resolve_sandbox_workspace_dir(workspace_root, scope_key)
    )
    effective_workspace_dir = (
        agent_workspace_dir if workspace_access == "rw" else sandbox_workspace_dir
    )

    return SandboxWorkspaceInfo(
        workspace_dir=effective_workspace_dir,
        container_workdir=container_workdir,
    )
