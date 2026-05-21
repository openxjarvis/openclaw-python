"""In-memory pending work queue for dormant/offline nodes.

Mirrors openclaw/src/gateway/node-pending-work.ts
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

NodePendingWorkType = Literal["status.request", "location.request"]
NodePendingWorkPriority = Literal["default", "normal", "high"]

NODE_PENDING_WORK_TYPES: tuple[NodePendingWorkType, ...] = ("status.request", "location.request")
NODE_PENDING_WORK_PRIORITIES: tuple[NodePendingWorkPriority, ...] = ("default", "normal", "high")

DEFAULT_STATUS_ITEM_ID = "baseline-status"
DEFAULT_STATUS_PRIORITY: NodePendingWorkPriority = "default"
DEFAULT_PRIORITY: NodePendingWorkPriority = "normal"
DEFAULT_MAX_ITEMS = 4
MAX_ITEMS = 10
PRIORITY_RANK: dict[NodePendingWorkPriority, int] = {
    "high": 3,
    "normal": 2,
    "default": 1,
}


@dataclass
class NodePendingWorkItem:
    id: str
    type: NodePendingWorkType
    priority: NodePendingWorkPriority
    createdAtMs: int
    expiresAtMs: int | None
    payload: dict[str, Any] | None = None


@dataclass
class _NodePendingWorkState:
    revision: int = 0
    items_by_id: dict[str, NodePendingWorkItem] = field(default_factory=dict)


@dataclass
class DrainResult:
    revision: int
    items: list[dict[str, Any]]
    hasMore: bool


_state_by_node_id: dict[str, _NodePendingWorkState] = {}


def _get_or_create_state(node_id: str) -> _NodePendingWorkState:
    state = _state_by_node_id.get(node_id)
    if state is None:
        state = _NodePendingWorkState()
        _state_by_node_id[node_id] = state
    return state


def _prune_expired(state: _NodePendingWorkState, now_ms: int) -> bool:
    changed = False
    for item_id, item in list(state.items_by_id.items()):
        if item.expiresAtMs is not None and item.expiresAtMs <= now_ms:
            del state.items_by_id[item_id]
            changed = True
    if changed:
        state.revision += 1
    return changed


def _prune_state_if_empty(node_id: str, state: _NodePendingWorkState) -> None:
    if not state.items_by_id:
        _state_by_node_id.pop(node_id, None)


def _sorted_items(state: _NodePendingWorkState) -> list[NodePendingWorkItem]:
    return sorted(
        state.items_by_id.values(),
        key=lambda item: (
            -PRIORITY_RANK[item.priority],
            item.createdAtMs,
            item.id,
        ),
    )


def _make_baseline_status_item(now_ms: int) -> NodePendingWorkItem:
    return NodePendingWorkItem(
        id=DEFAULT_STATUS_ITEM_ID,
        type="status.request",
        priority=DEFAULT_STATUS_PRIORITY,
        createdAtMs=now_ms,
        expiresAtMs=None,
    )


def _item_to_dict(item: NodePendingWorkItem) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": item.id,
        "type": item.type,
        "priority": item.priority,
        "createdAtMs": item.createdAtMs,
        "expiresAtMs": item.expiresAtMs,
    }
    if item.payload is not None:
        result["payload"] = item.payload
    return result


def enqueue_node_pending_work(
    *,
    node_id: str,
    type: NodePendingWorkType,
    priority: NodePendingWorkPriority | None = None,
    expires_in_ms: int | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_node_id = node_id.strip()
    if not normalized_node_id:
        raise ValueError("nodeId required")

    now_ms = int(time.time() * 1000)
    state = _get_or_create_state(normalized_node_id)
    _prune_expired(state, now_ms)

    for existing in state.items_by_id.values():
        if existing.type == type:
            return {
                "revision": state.revision,
                "item": _item_to_dict(existing),
                "deduped": True,
            }

    expires_at_ms: int | None = None
    if isinstance(expires_in_ms, (int, float)) and expires_in_ms == expires_in_ms:  # finite
        expires_at_ms = now_ms + max(1_000, int(expires_in_ms))

    item = NodePendingWorkItem(
        id=str(uuid.uuid4()),
        type=type,
        priority=priority or DEFAULT_PRIORITY,
        createdAtMs=now_ms,
        expiresAtMs=expires_at_ms,
        payload=payload,
    )
    state.items_by_id[item.id] = item
    state.revision += 1
    return {
        "revision": state.revision,
        "item": _item_to_dict(item),
        "deduped": False,
    }


def drain_node_pending_work(
    node_id: str,
    *,
    max_items: int | None = None,
    include_default_status: bool = True,
    now_ms: int | None = None,
) -> DrainResult:
    normalized_node_id = node_id.strip()
    if not normalized_node_id:
        return DrainResult(revision=0, items=[], hasMore=False)

    effective_now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    state = _state_by_node_id.get(normalized_node_id)
    revision = state.revision if state else 0
    if state:
        _prune_expired(state, effective_now_ms)
        _prune_state_if_empty(normalized_node_id, state)

    limit = min(MAX_ITEMS, max(1, int(max_items if max_items is not None else DEFAULT_MAX_ITEMS)))
    explicit_items = _sorted_items(state) if state else []
    items = explicit_items[:limit]
    has_explicit_status = any(item.type == "status.request" for item in explicit_items)
    include_baseline = include_default_status and not has_explicit_status
    if include_baseline and len(items) < limit:
        items.append(_make_baseline_status_item(effective_now_ms))

    explicit_returned_count = sum(1 for item in items if item.id != DEFAULT_STATUS_ITEM_ID)
    baseline_included = any(item.id == DEFAULT_STATUS_ITEM_ID for item in items)
    has_more = len(explicit_items) > explicit_returned_count or (include_baseline and not baseline_included)
    return DrainResult(
        revision=revision,
        items=[_item_to_dict(item) for item in items],
        hasMore=has_more,
    )


def acknowledge_node_pending_work(*, node_id: str, item_ids: list[str]) -> dict[str, Any]:
    normalized_node_id = node_id.strip()
    if not normalized_node_id:
        return {"revision": 0, "removedItemIds": []}

    state = _state_by_node_id.get(normalized_node_id)
    if state is None:
        return {"revision": 0, "removedItemIds": []}

    removed_item_ids: list[str] = []
    for item_id in item_ids:
        trimmed_id = item_id.strip()
        if not trimmed_id or trimmed_id == DEFAULT_STATUS_ITEM_ID:
            continue
        if state.items_by_id.pop(trimmed_id, None) is not None:
            removed_item_ids.append(trimmed_id)

    if removed_item_ids:
        state.revision += 1
    revision = state.revision
    _prune_state_if_empty(normalized_node_id, state)
    return {"revision": revision, "removedItemIds": removed_item_ids}


def reset_node_pending_work_for_tests() -> None:
    _state_by_node_id.clear()


def get_node_pending_work_state_count_for_tests() -> int:
    return len(_state_by_node_id)
