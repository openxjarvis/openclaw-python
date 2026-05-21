"""Dreaming workspace resolution.

Mirrors TypeScript memory-host-sdk/dreaming.ts resolveDreamingWorkspaces().

Groups agents by workspace for parallel dreaming execution.
"""
from __future__ import annotations

import logging
from typing import Any

from .config import DreamingConfig

logger = logging.getLogger(__name__)


def resolve_dreaming_workspaces(
    config: DreamingConfig,
    agents: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Resolve agent-workspace groups for dreaming.

    Mirrors TS resolveDreamingWorkspaces().
    Returns list of { agent_id, workspace_dir } groups.
    """
    if not agents:
        return []

    workspaces: dict[str, list[str]] = {}  # workspace_dir → [agent_id, ...]
    for agent in agents:
        agent_id = agent.get("id") or agent.get("agentId") or ""
        workspace = agent.get("workspace") or agent.get("workspace_dir") or ""
        if not agent_id:
            continue
        if workspace not in workspaces:
            workspaces[workspace] = []
        workspaces[workspace].append(agent_id)

    groups = []
    for workspace_dir, agent_ids in workspaces.items():
        groups.append({
            "workspace_dir": workspace_dir,
            "agent_ids": agent_ids,
        })

    return groups
