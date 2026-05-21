"""Remote macOS node skill eligibility — mirrors openclaw/src/infra/skills-remote.ts."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class _RemoteNodeRecord:
    node_id: str
    display_name: str | None = None
    platform: str | None = None
    device_family: str | None = None
    commands: list[str] = field(default_factory=list)
    bins: set[str] = field(default_factory=set)
    connected: bool = False
    remote_ip: str | None = None


_remote_nodes: dict[str, _RemoteNodeRecord] = {}


def _normalize_lower(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _is_mac_platform(platform: str | None = None, device_family: str | None = None) -> bool:
    platform_norm = _normalize_lower(platform)
    family_norm = _normalize_lower(device_family)
    if "mac" in platform_norm or "darwin" in platform_norm:
        return True
    return family_norm == "mac"


def _supports_system_run(commands: list[str] | None) -> bool:
    return isinstance(commands, list) and "system.run" in commands


def _upsert_node(
    *,
    node_id: str,
    display_name: str | None = None,
    platform: str | None = None,
    device_family: str | None = None,
    commands: list[str] | None = None,
    remote_ip: str | None = None,
    bins: list[str] | None = None,
    connected: bool | None = None,
) -> None:
    existing = _remote_nodes.get(node_id)
    merged_bins = set(bins or (list(existing.bins) if existing else []))
    _remote_nodes[node_id] = _RemoteNodeRecord(
        node_id=node_id,
        display_name=display_name or (existing.display_name if existing else None),
        platform=platform or (existing.platform if existing else None),
        device_family=device_family or (existing.device_family if existing else None),
        commands=list(commands or (existing.commands if existing else [])),
        remote_ip=remote_ip or (existing.remote_ip if existing else None),
        bins=merged_bins,
        connected=connected if connected is not None else (existing.connected if existing else False),
    )


def record_remote_node_info(
    *,
    node_id: str,
    display_name: str | None = None,
    platform: str | None = None,
    device_family: str | None = None,
    commands: list[str] | None = None,
    remote_ip: str | None = None,
) -> None:
    _upsert_node(
        node_id=node_id,
        display_name=display_name,
        platform=platform,
        device_family=device_family,
        commands=commands,
        remote_ip=remote_ip,
        connected=True,
    )


def record_remote_node_bins(node_id: str, bins: list[str]) -> None:
    _upsert_node(node_id=node_id, bins=bins)


def remove_remote_node_info(node_id: str) -> None:
    _remote_nodes.pop(node_id, None)


def sync_remote_nodes_from_registry(registry: Any) -> None:
    """Populate connected node metadata from a gateway NodeRegistry."""
    if registry is None:
        return
    list_connected = getattr(registry, "list_connected", None)
    if not callable(list_connected):
        return
    seen: set[str] = set()
    for node in list_connected():
        node_id = getattr(node, "nodeId", None) or getattr(node, "node_id", None)
        if not isinstance(node_id, str) or not node_id:
            continue
        seen.add(node_id)
        metadata = getattr(node, "metadata", None) or {}
        if not isinstance(metadata, dict):
            metadata = {}
        commands = metadata.get("commands")
        if not isinstance(commands, list):
            commands = []
        record_remote_node_info(
            node_id=node_id,
            display_name=metadata.get("displayName"),
            platform=metadata.get("platform"),
            device_family=metadata.get("deviceFamily"),
            commands=[str(c) for c in commands],
            remote_ip=metadata.get("remoteIp"),
        )
    for node_id in list(_remote_nodes):
        if node_id not in seen:
            _remote_nodes[node_id].connected = False


def get_remote_skill_eligibility(
    *,
    advertise_exec_node: bool | None = None,
) -> dict[str, Any] | None:
    mac_nodes = [
        node
        for node in _remote_nodes.values()
        if node.connected
        and _is_mac_platform(node.platform, node.device_family)
        and _supports_system_run(node.commands)
    ]
    if not mac_nodes:
        return None

    bins: set[str] = set()
    for node in mac_nodes:
        bins.update(node.bins)

    labels = [
        (node.display_name or node.node_id)
        for node in mac_nodes
        if (node.display_name or node.node_id)
    ]
    note = None
    if advertise_exec_node is not False:
        if labels:
            note = (
                f"Remote macOS node available ({', '.join(labels)}). "
                "Run macOS-only skills via exec host=node on that node."
            )
        else:
            note = (
                "Remote macOS node available. "
                "Run macOS-only skills via exec host=node on that node."
            )

    result: dict[str, Any] = {
        "platforms": ["darwin"],
        "hasBin": lambda b: b in bins,
        "hasAnyBin": lambda required: any(r in bins for r in required),
    }
    if note:
        result["note"] = note
    return result


def can_exec_request_node(cfg: dict[str, Any] | None, agent_id: str | None = None) -> bool:
    """Whether exec host=node is allowed — simplified mirror of TS canExecRequestNode."""
    if not cfg:
        return False
    tools = cfg.get("tools") or {}
    global_exec = tools.get("exec") or {}
    host = global_exec.get("host", "auto")
    if agent_id:
        for agent in (cfg.get("agents") or {}).get("list") or []:
            if isinstance(agent, dict) and agent.get("id") == agent_id:
                agent_exec = (agent.get("tools") or {}).get("exec") or {}
                host = agent_exec.get("host", host)
                break
    return host in ("node", "auto")


__all__ = [
    "can_exec_request_node",
    "get_remote_skill_eligibility",
    "record_remote_node_bins",
    "record_remote_node_info",
    "remove_remote_node_info",
    "sync_remote_nodes_from_registry",
]
