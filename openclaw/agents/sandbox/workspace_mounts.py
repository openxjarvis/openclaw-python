"""Docker workspace volume mount argument builders.

Matches TypeScript openclaw/src/agents/sandbox/workspace-mounts.ts
"""
from __future__ import annotations

from typing import Literal

from .constants import SANDBOX_AGENT_WORKSPACE_MOUNT

SANDBOX_MOUNT_FORMAT_VERSION = 2

SandboxWorkspaceAccess = Literal["none", "ro", "rw"]


def format_managed_workspace_bind(
    *,
    host_path: str,
    container_path: str,
    read_only: bool,
) -> str:
    suffix = "ro,z" if read_only else "z"
    return f"{host_path}:{container_path}:{suffix}"


def append_workspace_mount_args(
    *,
    args: list[str],
    workspace_dir: str,
    agent_workspace_dir: str,
    workdir: str,
    workspace_access: SandboxWorkspaceAccess,
) -> None:
    args.extend(
        [
            "-v",
            format_managed_workspace_bind(
                host_path=workspace_dir,
                container_path=workdir,
                read_only=workspace_access != "rw",
            ),
        ]
    )
    if workspace_access != "none" and workspace_dir != agent_workspace_dir:
        args.extend(
            [
                "-v",
                format_managed_workspace_bind(
                    host_path=agent_workspace_dir,
                    container_path=SANDBOX_AGENT_WORKSPACE_MOUNT,
                    read_only=workspace_access == "ro",
                ),
            ]
        )
