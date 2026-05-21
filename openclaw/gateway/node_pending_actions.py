"""Pending foreground node invoke actions (pull/ack queue).

Mirrors the pending-node-action helpers in openclaw/src/gateway/server-methods/nodes.ts
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

NODE_PENDING_ACTION_TTL_MS = 10 * 60_000
NODE_PENDING_ACTION_MAX_PER_NODE = 64


@dataclass
class PendingNodeAction:
    id: str
    nodeId: str
    command: str
    idempotencyKey: str
    enqueuedAtMs: int
    paramsJSON: str | None = None


_pending_node_actions_by_id: dict[str, list[PendingNodeAction]] = {}


def _prune_pending_node_actions(node_id: str, now_ms: int) -> list[PendingNodeAction]:
    queue = _pending_node_actions_by_id.get(node_id, [])
    min_timestamp_ms = now_ms - NODE_PENDING_ACTION_TTL_MS
    live = [entry for entry in queue if entry.enqueuedAtMs >= min_timestamp_ms]
    if not live:
        _pending_node_actions_by_id.pop(node_id, None)
        return []
    _pending_node_actions_by_id[node_id] = live
    return live


def enqueue_pending_node_action(
    *,
    node_id: str,
    command: str,
    params: Any = None,
    idempotency_key: str,
) -> PendingNodeAction:
    now_ms = int(time.time() * 1000)
    queue = _prune_pending_node_actions(node_id, now_ms)
    for existing in queue:
        if existing.idempotencyKey == idempotency_key:
            return existing

    params_json: str | None = None
    if params is not None:
        try:
            params_json = json.dumps(params)
        except Exception:
            params_json = None

    entry = PendingNodeAction(
        id=str(uuid.uuid4()),
        nodeId=node_id,
        command=command,
        paramsJSON=params_json,
        idempotencyKey=idempotency_key,
        enqueuedAtMs=now_ms,
    )
    queue.append(entry)
    if len(queue) > NODE_PENDING_ACTION_MAX_PER_NODE:
        del queue[: len(queue) - NODE_PENDING_ACTION_MAX_PER_NODE]
    _pending_node_actions_by_id[node_id] = queue
    return entry


def list_pending_node_actions(node_id: str) -> list[PendingNodeAction]:
    return _prune_pending_node_actions(node_id, int(time.time() * 1000))


def resolve_allowed_pending_node_actions(
    *,
    node_id: str,
    declared_commands: list[str] | None,
) -> list[PendingNodeAction]:
    pending = list_pending_node_actions(node_id)
    if not pending:
        return pending

    declared = set(declared_commands or [])
    if not declared:
        return pending

    allowed = [entry for entry in pending if entry.command in declared]
    if len(allowed) != len(pending):
        if allowed:
            _pending_node_actions_by_id[node_id] = allowed
        else:
            _pending_node_actions_by_id.pop(node_id, None)
    return allowed


def ack_pending_node_actions(node_id: str, ids: list[str]) -> list[PendingNodeAction]:
    if not ids:
        return list_pending_node_actions(node_id)

    pending = _prune_pending_node_actions(node_id, int(time.time() * 1000))
    id_set = set(ids)
    remaining = [entry for entry in pending if entry.id not in id_set]
    if not remaining:
        _pending_node_actions_by_id.pop(node_id, None)
        return []
    _pending_node_actions_by_id[node_id] = remaining
    return remaining


def pending_action_to_dict(entry: PendingNodeAction) -> dict[str, Any]:
    return {
        "id": entry.id,
        "command": entry.command,
        "paramsJSON": entry.paramsJSON,
        "enqueuedAtMs": entry.enqueuedAtMs,
    }


def reset_pending_node_actions_for_tests() -> None:
    _pending_node_actions_by_id.clear()
