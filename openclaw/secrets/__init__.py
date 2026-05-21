"""Secrets management subsystem.

Mirrors TypeScript src/secrets/ -- provides secret reference resolution,
audit, and configuration support.
"""
from openclaw.secrets.types import (
    SecretRef,
    SecretResolution,
    SecretsAuditCode,
    SecretsAuditFinding,
    SecretsAuditReport,
    SecretsAuditSeverity,
    SecretsAuditStatus,
)
from openclaw.secrets.resolver import (
    resolve_secrets_for_command,
    resolve_secret_ref_value,
    resolve_secrets_resolve,
)
from openclaw.secrets.target_registry import is_known_secret_target_id, list_known_secret_target_ids
from openclaw.secrets.audit import run_secrets_audit, resolve_secrets_audit_exit_code

__all__ = [
    "SecretRef",
    "SecretResolution",
    "SecretsAuditCode",
    "SecretsAuditFinding",
    "SecretsAuditReport",
    "SecretsAuditSeverity",
    "SecretsAuditStatus",
    "resolve_secrets_for_command",
    "resolve_secret_ref_value",
    "resolve_secrets_resolve",
    "is_known_secret_target_id",
    "list_known_secret_target_ids",
    "run_secrets_audit",
    "resolve_secrets_audit_exit_code",
]
