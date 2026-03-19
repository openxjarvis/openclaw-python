"""Sandbox configuration resolution

Resolves per-agent (or global) sandbox configuration by merging agent-level
overrides → global defaults → hardcoded defaults.

Mirrors TypeScript openclaw/src/agents/sandbox/config.ts
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import (
    DEFAULT_SANDBOX_IMAGE,
    SANDBOX_AGENT_WORKSPACE_MOUNT,
)
from .context import SandboxToolPolicy
from ...config.paths import resolve_state_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults (mirrors TS constants)
# ---------------------------------------------------------------------------

_DEFAULT_SANDBOX_CONTAINER_PREFIX = "openclaw-sbx-"
_DEFAULT_SANDBOX_WORKDIR = "/workspace"
_DEFAULT_SANDBOX_IDLE_HOURS = 24
_DEFAULT_SANDBOX_MAX_AGE_DAYS = 7
_DEFAULT_SANDBOX_WORKSPACE_ROOT = str(resolve_state_dir() / "sandboxes")
_DEFAULT_SANDBOX_BROWSER_IMAGE = "openclaw-sandbox-browser:bookworm-slim"
_DEFAULT_SANDBOX_BROWSER_PREFIX = "openclaw-sbx-browser-"
_DEFAULT_SANDBOX_BROWSER_NETWORK = "openclaw-sandbox-browser"
_DEFAULT_SANDBOX_BROWSER_CDP_PORT = 9222
_DEFAULT_SANDBOX_BROWSER_VNC_PORT = 5900
_DEFAULT_SANDBOX_BROWSER_NOVNC_PORT = 6080
_DEFAULT_SANDBOX_BROWSER_AUTOSTART_TIMEOUT_MS = 12_000

# ---------------------------------------------------------------------------
# Resolved config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ResolvedSandboxDockerConfig:
    """Fully resolved Docker-level sandbox configuration."""

    image: str = DEFAULT_SANDBOX_IMAGE
    container_prefix: str = _DEFAULT_SANDBOX_CONTAINER_PREFIX
    workdir: str = _DEFAULT_SANDBOX_WORKDIR
    read_only_root: bool = True
    tmpfs: list[str] = field(default_factory=lambda: ["/tmp", "/var/tmp", "/run"])
    network: str = "none"
    user: str | None = None
    cap_drop: list[str] = field(default_factory=lambda: ["ALL"])
    env: dict[str, str] = field(default_factory=lambda: {"LANG": "C.UTF-8"})
    setup_command: str | None = None
    pids_limit: int | None = None
    memory: str | None = None
    memory_swap: str | None = None
    cpus: str | None = None
    ulimits: dict[str, dict[str, int]] | None = None
    seccomp_profile: str | None = None
    apparmor_profile: str | None = None
    dns: list[str] | None = None
    extra_hosts: list[str] | None = None
    binds: list[str] | None = None
    # Dangerous override flags
    dangerously_allow_reserved_container_targets: bool | None = None
    dangerously_allow_external_bind_sources: bool | None = None
    dangerously_allow_container_namespace_join: bool | None = None


@dataclass
class ResolvedSandboxBrowserConfig:
    """Fully resolved browser container configuration."""

    enabled: bool = False
    image: str = _DEFAULT_SANDBOX_BROWSER_IMAGE
    container_prefix: str = _DEFAULT_SANDBOX_BROWSER_PREFIX
    network: str = _DEFAULT_SANDBOX_BROWSER_NETWORK
    cdp_port: int = _DEFAULT_SANDBOX_BROWSER_CDP_PORT
    vnc_port: int = _DEFAULT_SANDBOX_BROWSER_VNC_PORT
    no_vnc_port: int = _DEFAULT_SANDBOX_BROWSER_NOVNC_PORT
    headless: bool = False
    enable_no_vnc: bool = True
    allow_host_control: bool = False
    auto_start: bool = True
    auto_start_timeout_ms: int = _DEFAULT_SANDBOX_BROWSER_AUTOSTART_TIMEOUT_MS
    binds: list[str] | None = None
    cdp_source_range: str | None = None


@dataclass
class ResolvedSandboxPruneConfig:
    """Fully resolved prune/expiry configuration."""

    idle_hours: int = _DEFAULT_SANDBOX_IDLE_HOURS
    max_age_days: int = _DEFAULT_SANDBOX_MAX_AGE_DAYS


@dataclass
class ResolvedSandboxConfig:
    """Complete resolved sandbox configuration for an agent.

    Mirrors TS ``SandboxConfig`` (the resolved form).
    """

    mode: str = "off"           # "off" | "non-main" | "all"
    scope: str = "agent"        # "session" | "agent" | "shared"
    workspace_access: str = "none"  # "none" | "ro" | "rw"
    workspace_root: str = _DEFAULT_SANDBOX_WORKSPACE_ROOT
    docker: ResolvedSandboxDockerConfig = field(default_factory=ResolvedSandboxDockerConfig)
    browser: ResolvedSandboxBrowserConfig = field(default_factory=ResolvedSandboxBrowserConfig)
    prune: ResolvedSandboxPruneConfig = field(default_factory=ResolvedSandboxPruneConfig)
    tool_policy: SandboxToolPolicy = field(default_factory=SandboxToolPolicy)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_scope(scope_val: str | None, per_session: bool | None) -> str:
    """Mirrors TS ``resolveSandboxScope()``."""
    if scope_val:
        return scope_val
    if per_session is True:
        return "session"
    if per_session is False:
        return "shared"
    return "agent"


def _merge(agent_val: Any, global_val: Any, default: Any) -> Any:
    """Return first non-None value in priority order."""
    if agent_val is not None:
        return agent_val
    if global_val is not None:
        return global_val
    return default


def _resolve_docker(
    scope: str,
    global_docker: dict | None,
    agent_docker: dict | None,
) -> ResolvedSandboxDockerConfig:
    """Mirrors TS ``resolveSandboxDockerConfig()``."""
    ad = agent_docker if scope != "shared" else None
    gd = global_docker or {}
    ad = ad or {}

    # env: merge global + agent overrides
    base_env: dict[str, str] = gd.get("env", {"LANG": "C.UTF-8"})
    if ad.get("env"):
        env = {**base_env, **ad["env"]}
    else:
        env = base_env

    # ulimits: merge
    ulimits: dict | None = None
    if gd.get("ulimits") or ad.get("ulimits"):
        ulimits = {**(gd.get("ulimits") or {}), **(ad.get("ulimits") or {})}

    # binds: concatenate
    binds = (gd.get("binds") or []) + (ad.get("binds") or [])

    return ResolvedSandboxDockerConfig(
        image=_merge(ad.get("image"), gd.get("image"), DEFAULT_SANDBOX_IMAGE),
        container_prefix=_merge(ad.get("containerPrefix"), gd.get("containerPrefix"), _DEFAULT_SANDBOX_CONTAINER_PREFIX),
        workdir=_merge(ad.get("workdir"), gd.get("workdir"), _DEFAULT_SANDBOX_WORKDIR),
        read_only_root=_merge(ad.get("readOnlyRoot"), gd.get("readOnlyRoot"), True),
        tmpfs=_merge(ad.get("tmpfs"), gd.get("tmpfs"), ["/tmp", "/var/tmp", "/run"]),
        network=_merge(ad.get("network"), gd.get("network"), "none"),
        user=_merge(ad.get("user"), gd.get("user"), None),
        cap_drop=_merge(ad.get("capDrop"), gd.get("capDrop"), ["ALL"]),
        env=env,
        setup_command=_merge(ad.get("setupCommand"), gd.get("setupCommand"), None),
        pids_limit=_merge(ad.get("pidsLimit"), gd.get("pidsLimit"), None),
        memory=_merge(ad.get("memory"), gd.get("memory"), None),
        memory_swap=_merge(ad.get("memorySwap"), gd.get("memorySwap"), None),
        cpus=_merge(ad.get("cpus"), gd.get("cpus"), None),
        ulimits=ulimits,
        seccomp_profile=_merge(ad.get("seccompProfile"), gd.get("seccompProfile"), None),
        apparmor_profile=_merge(ad.get("apparmorProfile"), gd.get("apparmorProfile"), None),
        dns=_merge(ad.get("dns"), gd.get("dns"), None),
        extra_hosts=_merge(ad.get("extraHosts"), gd.get("extraHosts"), None),
        binds=binds or None,
        dangerously_allow_reserved_container_targets=_merge(
            ad.get("dangerouslyAllowReservedContainerTargets"),
            gd.get("dangerouslyAllowReservedContainerTargets"), None,
        ),
        dangerously_allow_external_bind_sources=_merge(
            ad.get("dangerouslyAllowExternalBindSources"),
            gd.get("dangerouslyAllowExternalBindSources"), None,
        ),
        dangerously_allow_container_namespace_join=_merge(
            ad.get("dangerouslyAllowContainerNamespaceJoin"),
            gd.get("dangerouslyAllowContainerNamespaceJoin"), None,
        ),
    )


def _resolve_browser(
    scope: str,
    global_browser: dict | None,
    agent_browser: dict | None,
) -> ResolvedSandboxBrowserConfig:
    """Mirrors TS ``resolveSandboxBrowserConfig()``."""
    ab = agent_browser if scope != "shared" else None
    gb = global_browser or {}
    ab = ab or {}
    binds_gb = gb.get("binds") or []
    binds_ab = ab.get("binds") or []
    binds_configured = "binds" in gb or "binds" in ab
    binds = binds_gb + binds_ab if binds_configured else None
    return ResolvedSandboxBrowserConfig(
        enabled=_merge(ab.get("enabled"), gb.get("enabled"), False),
        image=_merge(ab.get("image"), gb.get("image"), _DEFAULT_SANDBOX_BROWSER_IMAGE),
        container_prefix=_merge(ab.get("containerPrefix"), gb.get("containerPrefix"), _DEFAULT_SANDBOX_BROWSER_PREFIX),
        network=_merge(ab.get("network"), gb.get("network"), _DEFAULT_SANDBOX_BROWSER_NETWORK),
        cdp_port=_merge(ab.get("cdpPort"), gb.get("cdpPort"), _DEFAULT_SANDBOX_BROWSER_CDP_PORT),
        vnc_port=_merge(ab.get("vncPort"), gb.get("vncPort"), _DEFAULT_SANDBOX_BROWSER_VNC_PORT),
        no_vnc_port=_merge(ab.get("noVncPort"), gb.get("noVncPort"), _DEFAULT_SANDBOX_BROWSER_NOVNC_PORT),
        headless=_merge(ab.get("headless"), gb.get("headless"), False),
        enable_no_vnc=_merge(ab.get("enableNoVnc"), gb.get("enableNoVnc"), True),
        allow_host_control=_merge(ab.get("allowHostControl"), gb.get("allowHostControl"), False),
        auto_start=_merge(ab.get("autoStart"), gb.get("autoStart"), True),
        auto_start_timeout_ms=_merge(ab.get("autoStartTimeoutMs"), gb.get("autoStartTimeoutMs"), _DEFAULT_SANDBOX_BROWSER_AUTOSTART_TIMEOUT_MS),
        binds=binds,
        cdp_source_range=_merge(ab.get("cdpSourceRange"), gb.get("cdpSourceRange"), None),
    )


def _resolve_prune(
    scope: str,
    global_prune: dict | None,
    agent_prune: dict | None,
) -> ResolvedSandboxPruneConfig:
    """Mirrors TS ``resolveSandboxPruneConfig()``."""
    ap = agent_prune if scope != "shared" else None
    gp = global_prune or {}
    ap = ap or {}
    return ResolvedSandboxPruneConfig(
        idle_hours=_merge(ap.get("idleHours"), gp.get("idleHours"), _DEFAULT_SANDBOX_IDLE_HOURS),
        max_age_days=_merge(ap.get("maxAgeDays"), gp.get("maxAgeDays"), _DEFAULT_SANDBOX_MAX_AGE_DAYS),
    )


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------


def resolve_sandbox_config_for_agent(
    cfg: Any | None,
    agent_id: str | None,
) -> ResolvedSandboxConfig:
    """Resolve the complete sandbox config for *agent_id*.

    Priority: agent-level sandbox → agents.defaults.sandbox → hardcoded defaults.

    Mirrors TS ``resolveSandboxConfigForAgent()``.
    """
    if not isinstance(cfg, dict):
        return ResolvedSandboxConfig()

    # Global defaults
    agents_section = cfg.get("agents", {}) or {}
    global_sandbox: dict = {}
    if isinstance(agents_section, dict):
        global_sandbox = agents_section.get("defaults", {}).get("sandbox", {}) or {}

    # Agent-specific overrides
    agent_sandbox: dict = {}
    if agent_id:
        try:
            from openclaw.agents.agent_scope import resolve_agent_config  # type: ignore[import]
            agent_cfg = resolve_agent_config(cfg, agent_id)
            if isinstance(agent_cfg, dict):
                agent_sandbox = agent_cfg.get("sandbox", {}) or {}
        except (ImportError, Exception):
            pass

    scope = _resolve_scope(
        scope_val=agent_sandbox.get("scope") or global_sandbox.get("scope"),
        per_session=agent_sandbox.get("perSession") if "perSession" in agent_sandbox
        else global_sandbox.get("perSession"),
    )

    # Tool policy
    try:
        from .tool_policy import resolve_sandbox_tool_policy_for_agent
        tool_policy = resolve_sandbox_tool_policy_for_agent(cfg, agent_id)
    except (ImportError, Exception):
        tool_policy = SandboxToolPolicy()

    workspace_root = (
        agent_sandbox.get("workspaceRoot")
        or global_sandbox.get("workspaceRoot")
        or _DEFAULT_SANDBOX_WORKSPACE_ROOT
    )

    return ResolvedSandboxConfig(
        mode=agent_sandbox.get("mode") or global_sandbox.get("mode") or "off",
        scope=scope,
        workspace_access=agent_sandbox.get("workspaceAccess") or global_sandbox.get("workspaceAccess") or "none",
        workspace_root=workspace_root,
        docker=_resolve_docker(
            scope=scope,
            global_docker=global_sandbox.get("docker"),
            agent_docker=agent_sandbox.get("docker"),
        ),
        browser=_resolve_browser(
            scope=scope,
            global_browser=global_sandbox.get("browser"),
            agent_browser=agent_sandbox.get("browser"),
        ),
        prune=_resolve_prune(
            scope=scope,
            global_prune=global_sandbox.get("prune"),
            agent_prune=agent_sandbox.get("prune"),
        ),
        tool_policy=tool_policy,
    )
