"""Node host config — mirrors src/node-host/config.ts"""
from __future__ import annotations

import json
import os
import stat
import uuid
from dataclasses import dataclass
from ..config.paths import resolve_state_dir


@dataclass
class NodeHostGatewayConfig:
    host: str | None = None
    port: int | None = None
    tls: bool = False
    tls_fingerprint: str | None = None


@dataclass
class NodeHostConfig:
    version: int
    node_id: str
    token: str | None = None
    display_name: str | None = None
    gateway: NodeHostGatewayConfig | None = None


NODE_HOST_FILE = "node.json"


def resolve_node_host_config_path() -> str:
    try:
        state_dir = str(resolve_state_dir())
    except (ImportError, Exception):
        state_dir = os.path.expanduser("~/.openclaw")
    return os.path.join(state_dir, NODE_HOST_FILE)


def _normalize_config(raw: dict | None) -> NodeHostConfig:
    node_id = ""
    if raw and raw.get("version") == 1 and isinstance(raw.get("nodeId"), str):
        node_id = raw["nodeId"].strip()
    if not node_id:
        node_id = str(uuid.uuid4())

    gw_raw = raw.get("gateway") if raw else None
    gateway: NodeHostGatewayConfig | None = None
    if isinstance(gw_raw, dict):
        gateway = NodeHostGatewayConfig(
            host=gw_raw.get("host"),
            port=gw_raw.get("port"),
            tls=bool(gw_raw.get("tls", False)),
            tls_fingerprint=gw_raw.get("tlsFingerprint"),
        )

    return NodeHostConfig(
        version=1,
        node_id=node_id,
        token=raw.get("token") if raw else None,
        display_name=raw.get("displayName") if raw else None,
        gateway=gateway,
    )


def _config_to_dict(config: NodeHostConfig) -> dict:
    d: dict = {
        "version": config.version,
        "nodeId": config.node_id,
    }
    if config.token is not None:
        d["token"] = config.token
    if config.display_name is not None:
        d["displayName"] = config.display_name
    if config.gateway:
        gw: dict = {}
        if config.gateway.host is not None:
            gw["host"] = config.gateway.host
        if config.gateway.port is not None:
            gw["port"] = config.gateway.port
        if config.gateway.tls:
            gw["tls"] = config.gateway.tls
        if config.gateway.tls_fingerprint:
            gw["tlsFingerprint"] = config.gateway.tls_fingerprint
        d["gateway"] = gw
    return d


async def load_node_host_config() -> NodeHostConfig | None:
    import asyncio
    import functools

    file_path = resolve_node_host_config_path()

    def _read() -> dict | None:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    loop = asyncio.get_running_loop()
    raw = await loop.run_in_executor(None, _read)
    if raw is None:
        return None
    return _normalize_config(raw)


async def save_node_host_config(config: NodeHostConfig) -> None:
    import asyncio

    file_path = resolve_node_host_config_path()

    def _write() -> None:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        payload = json.dumps(_config_to_dict(config), indent=2)
        # Write with mode 0o600 (owner read/write only)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(file_path, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload + "\n")
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            raise
        try:
            os.chmod(file_path, 0o600)
        except OSError:
            pass  # best-effort

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _write)


async def ensure_node_host_config() -> NodeHostConfig:
    existing = await load_node_host_config()
    raw = _config_to_dict(existing) if existing else None
    normalized = _normalize_config(raw)
    await save_node_host_config(normalized)
    return normalized
