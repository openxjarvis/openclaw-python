"""Plugin approval management — mirrors ExecApprovalManager but for plugins."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PLUGIN_ID_PREFIX = "plugin:"
DEFAULT_PLUGIN_APPROVAL_TIMEOUT_MS = 120_000
VALID_PLUGIN_DECISIONS = frozenset({"allow", "deny", "allow-always"})


@dataclass
class PluginApprovalRecord:
    """A pending plugin approval request."""

    id: str
    plugin_id: str
    action: str
    context: Dict[str, Any] = field(default_factory=dict)
    requested_at: float = field(default_factory=time.time)
    status: str = "pending"
    decision: Optional[str] = None
    expires_at_ms: Optional[int] = None


class PluginApprovalsManager:
    """Plugin approval management service — mirrors ExecApprovalManager contract."""

    def __init__(self) -> None:
        self._pending: Dict[str, PluginApprovalRecord] = {}
        self._waiters: Dict[str, asyncio.Event] = {}
        self._decisions: Dict[str, str] = {}

    def _generate_id(self) -> str:
        return PLUGIN_ID_PREFIX + str(uuid.uuid4())

    def list_pending_records(self) -> List[Dict[str, Any]]:
        """Return list of pending plugin approval records."""
        out: List[Dict[str, Any]] = []
        for rec in self._pending.values():
            out.append(
                {
                    "id": rec.id,
                    "request": {
                        "pluginId": rec.plugin_id,
                        "action": rec.action,
                        **{k: v for k, v in rec.context.items()},
                    },
                    "createdAtMs": int(rec.requested_at * 1000),
                    "expiresAtMs": rec.expires_at_ms,
                }
            )
        return out

    def request_approval_from_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Create a plugin approval request (sync — returns immediately if twoPhase)."""
        plugin_id = str(params.get("pluginId") or params.get("plugin_id") or "").strip()
        action = str(params.get("action") or "").strip()
        timeout_ms = int(params.get("timeoutMs") or DEFAULT_PLUGIN_APPROVAL_TIMEOUT_MS)
        two_phase = params.get("twoPhase") is True

        approval_id = self._generate_id()
        now_ms = int(time.time() * 1000)
        rec = PluginApprovalRecord(
            id=approval_id,
            plugin_id=plugin_id,
            action=action,
            context={k: params[k] for k in params if k not in ("pluginId", "action", "timeoutMs", "twoPhase")},
            expires_at_ms=now_ms + max(1000, timeout_ms),
        )
        self._pending[approval_id] = rec
        self._waiters[approval_id] = asyncio.Event()

        try:
            from openclaw.gateway.events import broadcast as _broadcast
            _broadcast(
                "plugin.approval.requested",
                {
                    "id": approval_id,
                    "pluginId": plugin_id,
                    "action": action,
                    "createdAtMs": now_ms,
                    "expiresAtMs": rec.expires_at_ms,
                },
            )
        except Exception:
            pass

        if two_phase:
            return {"status": "accepted", "id": approval_id}
        return {"id": approval_id, "status": "pending"}

    async def await_decision(
        self,
        approval_id: str,
        timeout_ms: int = DEFAULT_PLUGIN_APPROVAL_TIMEOUT_MS,
    ) -> Optional[str]:
        """Wait for a plugin approval decision."""
        if approval_id in self._decisions:
            return self._decisions[approval_id]

        rec = self._pending.get(approval_id)
        if not rec:
            return None

        event = self._waiters.get(approval_id)
        if not event:
            return None

        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.001, timeout_ms / 1000.0))
        except asyncio.TimeoutError:
            self._expire(approval_id)
            return None

        return self._decisions.get(approval_id)

    def resolve_sync(self, approval_id: str, decision: str) -> Dict[str, Any]:
        """Apply a decision synchronously."""
        if decision not in VALID_PLUGIN_DECISIONS:
            raise ValueError(f"invalid decision: {decision!r}; must be one of {sorted(VALID_PLUGIN_DECISIONS)}")

        rec = self._pending.get(approval_id)
        if not rec or rec.status != "pending":
            raise ValueError("unknown or expired approval id")

        rec.decision = decision
        rec.status = "resolved"
        self._decisions[approval_id] = decision
        event = self._waiters.pop(approval_id, None)
        del self._pending[approval_id]

        if event:
            event.set()

        self._broadcast_resolved(approval_id, decision)
        return {"id": approval_id, "decision": decision}

    def _expire(self, approval_id: str) -> None:
        rec = self._pending.pop(approval_id, None)
        self._waiters.pop(approval_id, None)
        if rec:
            rec.status = "expired"
            try:
                from openclaw.gateway.events import broadcast as _broadcast
                _broadcast("plugin.approval.expired", {"id": approval_id})
            except Exception:
                pass

    def _broadcast_resolved(self, approval_id: str, decision: str) -> None:
        try:
            from openclaw.gateway.events import broadcast as _broadcast
            _broadcast("plugin.approval.resolved", {"id": approval_id, "decision": decision})
        except Exception:
            pass


_plugin_approvals_manager: Optional[PluginApprovalsManager] = None


def get_plugin_approvals_manager() -> PluginApprovalsManager:
    global _plugin_approvals_manager
    if _plugin_approvals_manager is None:
        _plugin_approvals_manager = PluginApprovalsManager()
    return _plugin_approvals_manager
