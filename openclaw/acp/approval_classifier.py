"""ACP Approval Classifier.

Mirrors TypeScript src/acp/approval-classifier.ts

Classifies agent operations to determine if they require user approval
before execution. This is the security gate for sensitive operations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ApprovalClassification = Literal["auto", "required", "blocked"]
ApprovalReason = Literal[
    "file_write",
    "bash_exec",
    "plugin_install",
    "network_request",
    "sensitive_data_access",
    "agent_spawn",
    "memory_write",
    "config_change",
    "none",
]


@dataclass
class ApprovalRequest:
    """An operation that may require approval."""

    operation: str            # e.g. "bash_exec", "file_write"
    description: str          # human-readable description
    params: dict[str, Any] = field(default_factory=dict)
    agent_id: str | None = None
    session_key: str | None = None


@dataclass
class ApprovalClassificationResult:
    """Result from classify_approval_request()."""

    classification: ApprovalClassification
    reason: ApprovalReason
    message: str | None = None


# Operations that always require approval
_ALWAYS_REQUIRE: frozenset[str] = frozenset({
    "bash_exec",
    "plugin_install",
    "plugin_uninstall",
    "config_write",
    "gateway_shutdown",
    "gateway_restart",
})

# Operations that are blocked by default
_BLOCKED: frozenset[str] = frozenset({
    "rm_rf",
    "format_disk",
    "delete_all",
})

# Operations that are automatically allowed
_AUTO_ALLOWED: frozenset[str] = frozenset({
    "file_read",
    "memory_search",
    "web_fetch",
    "web_search",
    "sessions_list",
    "tool_call",
})


def classify_approval_request(
    operation: str,
    params: dict[str, Any] | None = None,
    *,
    allow_list: list[str] | None = None,
    require_list: list[str] | None = None,
) -> ApprovalClassificationResult:
    """Classify whether an operation needs user approval.

    Mirrors TS classifyApprovalRequest() in approval-classifier.ts.

    Args:
        operation: Operation name (e.g. "bash_exec", "file_write")
        params: Operation parameters (used for context-specific rules)
        allow_list: Additional auto-allowed operations
        require_list: Additional operations requiring approval

    Returns:
        ApprovalClassificationResult with classification and reason
    """
    params = params or {}
    op = operation.lower().strip()

    # Blocked operations
    if op in _BLOCKED:
        return ApprovalClassificationResult(
            classification="blocked",
            reason="bash_exec",
            message=f"Operation '{op}' is blocked by policy",
        )

    # Custom require list
    if require_list and op in {r.lower() for r in require_list}:
        return ApprovalClassificationResult(
            classification="required",
            reason="config_change",
            message=f"Operation '{op}' requires approval (custom policy)",
        )

    # Always-require operations
    if op in _ALWAYS_REQUIRE:
        reason: ApprovalReason = "bash_exec" if "exec" in op or "bash" in op else "config_change"
        return ApprovalClassificationResult(
            classification="required",
            reason=reason,
            message=f"Operation '{op}' requires approval",
        )

    # Custom allow list
    if allow_list and op in {a.lower() for a in allow_list}:
        return ApprovalClassificationResult(
            classification="auto",
            reason="none",
        )

    # Auto-allowed
    if op in _AUTO_ALLOWED:
        return ApprovalClassificationResult(
            classification="auto",
            reason="none",
        )

    # File write requires approval for paths outside workspace
    if "file_write" in op or op == "write":
        path = str(params.get("path") or params.get("file") or "")
        if path.startswith("/etc") or path.startswith("/usr") or "~" in path:
            return ApprovalClassificationResult(
                classification="required",
                reason="file_write",
                message=f"File write to '{path}' requires approval",
            )

    # Default: auto-allowed
    return ApprovalClassificationResult(
        classification="auto",
        reason="none",
    )
