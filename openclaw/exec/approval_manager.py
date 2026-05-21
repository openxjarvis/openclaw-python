"""
Exec approval management — mirrors TypeScript ExecApprovalManager contract.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_EXEC_APPROVAL_TIMEOUT_MS = 120_000
VALID_DECISIONS = frozenset({"allow-once", "allow-always", "deny"})
RESERVED_PLUGIN_PREFIX = "plugin:"


@dataclass
class ApprovalRequest:
    """Command approval request snapshot."""

    id: str
    command: str
    context: Dict[str, Any] = field(default_factory=dict)
    requested_at: float = field(default_factory=time.time)
    status: str = "pending"
    approved_by: Optional[str] = None
    resolved_at: Optional[float] = None
    expires_at_ms: Optional[int] = None
    decision: Optional[str] = None


@dataclass
class ApprovalPolicy:
    """Approval policy for commands."""

    pattern: str
    auto_approve: bool = False
    require_approval: bool = True
    allowed_users: Optional[List[str]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


ApprovalCallback = Callable[[ApprovalRequest, bool], Awaitable[None]]


class ExecApprovalManager:
    """Exec approval management service."""

    def __init__(self) -> None:
        self.pending_approvals: Dict[str, ApprovalRequest] = {}
        self.policies: Dict[str, ApprovalPolicy] = {}
        self.callbacks: List[ApprovalCallback] = []
        self._waiters: Dict[str, asyncio.Event] = {}
        self._decisions: Dict[str, str] = {}
        self.approval_timeout = 300

    def _normalize_id(self, raw: Any) -> str:
        if isinstance(raw, str):
            return raw.strip()
        return ""

    def lookup_pending_id(self, input_id: str) -> str | None:
        """Resolve approval id (exact or unique prefix)."""
        tid = self._normalize_id(input_id)
        if not tid:
            return None
        if tid in self.pending_approvals:
            return tid
        matches = [k for k in self.pending_approvals if k.startswith(tid)]
        if len(matches) == 1:
            return matches[0]
        return None

    def get_snapshot(self, approval_id: str) -> Optional[ApprovalRequest]:
        return self.pending_approvals.get(approval_id)

    def get(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """Get approval for gateway handler (alias)."""
        req = self.pending_approvals.get(approval_id)
        if not req:
            resolved_id = self.lookup_pending_id(approval_id)
            if resolved_id:
                req = self.pending_approvals.get(resolved_id)
        if not req:
            return None
        return self._record_to_get_payload(req)

    def _resolve_allowed_decisions(self, req: ApprovalRequest) -> List[str]:
        """Derive allowed decisions from request context/policy — mirrors resolveExecApprovalRequestAllowedDecisions."""
        security = req.context.get("security") or {}
        if isinstance(security, str):
            try:
                import json
                security = json.loads(security)
            except Exception:
                security = {}
        # If policy disables persistent allow-always, omit it
        allow_always_allowed = security.get("allowAlways", True)
        decisions: List[str] = ["allow-once"]
        if allow_always_allowed:
            decisions.append("allow-always")
        decisions.append("deny")
        return decisions

    def _record_to_get_payload(self, req: ApprovalRequest) -> Dict[str, Any]:
        return {
            "id": req.id,
            "commandText": req.command,
            "commandPreview": req.command[:200] if req.command else "",
            "allowedDecisions": self._resolve_allowed_decisions(req),
            "host": req.context.get("host"),
            "nodeId": req.context.get("nodeId"),
            "agentId": req.context.get("agentId"),
            "expiresAtMs": req.expires_at_ms,
            "createdAtMs": int(req.requested_at * 1000),
        }

    def list_pending_records(self) -> List[Dict[str, Any]]:
        """TS exec.approval.list shape."""
        out: List[Dict[str, Any]] = []
        for req in self.pending_approvals.values():
            out.append(
                {
                    "id": req.id,
                    "request": {
                        "command": req.command,
                        **{k: v for k, v in req.context.items() if k != "command"},
                    },
                    "createdAtMs": int(req.requested_at * 1000),
                    "expiresAtMs": req.expires_at_ms,
                }
            )
        return out

    def list_pending(self) -> List[Dict[str, Any]]:
        return self.list_pending_records()

    def request_approval(
        self,
        command: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Legacy sync request — returns approval id."""
        record = self._create_record(command, context or {}, None, DEFAULT_EXEC_APPROVAL_TIMEOUT_MS)
        return record.id

    def _create_record(
        self,
        command: str,
        context: Dict[str, Any],
        explicit_id: Optional[str],
        timeout_ms: int,
    ) -> ApprovalRequest:
        approval_id = explicit_id or secrets.token_urlsafe(16)
        if approval_id.startswith(RESERVED_PLUGIN_PREFIX):
            raise ValueError(f"approval ids starting with {RESERVED_PLUGIN_PREFIX} are reserved")
        if approval_id in self.pending_approvals:
            raise ValueError("approval id already pending")

        now_ms = int(time.time() * 1000)
        request = ApprovalRequest(
            id=approval_id,
            command=command,
            context=context,
            expires_at_ms=now_ms + max(1000, timeout_ms),
        )
        self.pending_approvals[approval_id] = request
        self._waiters[approval_id] = asyncio.Event()

        try:
            from openclaw.gateway.events import broadcast as _broadcast

            _broadcast(
                "exec.approval.requested",
                {
                    "id": approval_id,
                    "request": {"command": command, **context},
                    "createdAtMs": now_ms,
                    "expiresAtMs": request.expires_at_ms,
                },
            )
        except Exception:
            pass

        logger.info("Approval requested: %s for command: %s", approval_id, command)
        return request

    async def request_approval_from_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """TS exec.approval.request — accepts full params dict."""
        command = str(params.get("command") or "").strip()
        if not command:
            raise ValueError("command is required")

        explicit_id = self._normalize_id(params.get("id")) or None
        timeout_ms = int(params.get("timeoutMs") or DEFAULT_EXEC_APPROVAL_TIMEOUT_MS)
        two_phase = params.get("twoPhase") is True

        context: Dict[str, Any] = {
            k: params.get(k)
            for k in (
                "host",
                "nodeId",
                "agentId",
                "sessionKey",
                "cwd",
                "security",
                "ask",
                "resolvedPath",
            )
            if params.get(k) is not None
        }

        record = self._create_record(command, context, explicit_id, timeout_ms)

        created_at_ms = int(record.requested_at * 1000)
        expires_at_ms = record.expires_at_ms

        if two_phase:
            # twoPhase: return immediately without blocking — mirrors TS exec-approval.ts
            return {
                "status": "accepted",
                "id": record.id,
                "createdAtMs": created_at_ms,
                "expiresAtMs": expires_at_ms,
            }

        decision = await self.await_decision(record.id, timeout_ms)
        if decision is None:
            return {
                "id": record.id,
                "decision": None,
                "createdAtMs": created_at_ms,
                "expiresAtMs": expires_at_ms,
            }
        return {
            "id": record.id,
            "decision": decision,
            "createdAtMs": created_at_ms,
            "expiresAtMs": expires_at_ms,
        }

    async def await_decision(
        self,
        approval_id: str,
        timeout_ms: int = DEFAULT_EXEC_APPROVAL_TIMEOUT_MS,
    ) -> Optional[str]:
        """Wait for approval decision (TS exec.approval.waitDecision)."""
        resolved = self.lookup_pending_id(approval_id) or approval_id
        if resolved in self._decisions:
            return self._decisions[resolved]

        req = self.pending_approvals.get(resolved)
        if not req:
            return None

        event = self._waiters.get(resolved)
        if not event:
            return None

        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.001, timeout_ms / 1000.0))
        except asyncio.TimeoutError:
            self._expire(resolved)
            return None

        return self._decisions.get(resolved)

    def resolve_sync(self, approval_id: str, decision: str) -> Dict[str, Any]:
        """Apply decision synchronously (TS exec.approval.resolve)."""
        if decision not in VALID_DECISIONS:
            raise ValueError("invalid decision")

        resolved_id = self.lookup_pending_id(approval_id) or approval_id
        req = self.pending_approvals.get(resolved_id)
        if not req or req.status != "pending":
            raise ValueError("unknown or expired approval id")

        req.decision = decision
        req.status = "resolved"
        req.resolved_at = time.time()
        approved = decision != "deny"
        self._decisions[resolved_id] = decision
        event = self._waiters.pop(resolved_id, None)
        del self.pending_approvals[resolved_id]

        if event:
            event.set()

        self._trigger_callbacks(req, approved)
        self._broadcast_resolved(resolved_id, decision)
        return {"ok": True}

    async def resolve(self, approval_id: str, decision: str) -> None:
        """Async resolve wrapper."""
        self.resolve_sync(approval_id, decision)

    def approve(self, approval_id: str, approved_by: Optional[str] = None) -> bool:
        try:
            self.resolve_sync(approval_id, "allow-once")
            req = ApprovalRequest(id=approval_id, command="")
            if approval_id in self._decisions:
                pass
            return True
        except ValueError:
            return False

    def reject(self, approval_id: str, rejected_by: Optional[str] = None) -> bool:
        try:
            self.resolve_sync(approval_id, "deny")
            return True
        except ValueError:
            return False

    def _expire(self, approval_id: str) -> None:
        req = self.pending_approvals.pop(approval_id, None)
        self._waiters.pop(approval_id, None)
        if req:
            req.status = "expired"
            try:
                from openclaw.gateway.events import broadcast as _broadcast

                _broadcast("exec.approval.expired", {"id": approval_id})
            except Exception:
                pass

    def _broadcast_resolved(self, approval_id: str, decision: str) -> None:
        try:
            from openclaw.gateway.events import broadcast as _broadcast

            _broadcast(
                "exec.approval.resolved",
                {"id": approval_id, "decision": decision},
            )
        except Exception:
            pass

    def set_policy(self, policy_id: str, policy: ApprovalPolicy) -> None:
        self.policies[policy_id] = policy

    def get_policy(self, policy_id: str) -> Optional[ApprovalPolicy]:
        return self.policies.get(policy_id)

    def list_policies(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": pid,
                "pattern": p.pattern,
                "autoApprove": p.auto_approve,
                "requireApproval": p.require_approval,
                "allowedUsers": p.allowed_users,
                "metadata": p.metadata,
            }
            for pid, p in self.policies.items()
        ]

    def register_callback(self, callback: ApprovalCallback) -> None:
        self.callbacks.append(callback)

    def _trigger_callbacks(self, request: ApprovalRequest, approved: bool) -> None:
        for callback in self.callbacks:
            try:
                asyncio.create_task(callback(request, approved))
            except Exception as e:
                logger.error("Callback error: %s", e, exc_info=True)


_approval_manager: Optional[ExecApprovalManager] = None


def get_approval_manager() -> ExecApprovalManager:
    global _approval_manager
    if _approval_manager is None:
        _approval_manager = ExecApprovalManager()
    return _approval_manager


def set_approval_manager(manager: ExecApprovalManager) -> None:
    global _approval_manager
    _approval_manager = manager
