"""Secret types -- mirrors TS src/config/types.secrets.ts and src/secrets/audit.ts types."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SecretsAuditCode = Literal[
    "PLAINTEXT_FOUND",
    "REF_UNRESOLVED",
    "REF_SHADOWED",
    "LEGACY_RESIDUE",
]

SecretsAuditSeverity = Literal["info", "warn", "error"]
SecretsAuditStatus = Literal["clean", "findings", "unresolved"]


@dataclass
class SecretRef:
    """A reference to a secret value stored externally."""
    source: str
    provider: str
    id: str
    env: str | None = None
    file: str | None = None
    exec: str | None = None
    pointer: str | None = None  # RFC 6901 JSON pointer for value extraction from file

    def key(self) -> str:
        return f"{self.source}:{self.provider}:{self.id}"


@dataclass
class SecretResolution:
    """Result of resolving a single secret reference."""
    ref: SecretRef
    value: str | None = None
    error: str | None = None

    @property
    def resolved(self) -> bool:
        return self.value is not None and self.error is None


@dataclass
class SecretsAuditFinding:
    """A single finding from the secrets audit."""
    code: SecretsAuditCode
    severity: SecretsAuditSeverity
    file: str
    json_path: str
    message: str
    provider: str | None = None
    profile_id: str | None = None


@dataclass
class SecretsAuditSummary:
    plaintext_count: int = 0
    unresolved_ref_count: int = 0
    shadowed_ref_count: int = 0
    legacy_residue_count: int = 0


@dataclass
class SecretsAuditReport:
    """Full audit report -- mirrors TS SecretsAuditReport."""
    version: int = 1
    status: SecretsAuditStatus = "clean"
    files_scanned: list[str] = field(default_factory=list)
    summary: SecretsAuditSummary = field(default_factory=SecretsAuditSummary)
    findings: list[SecretsAuditFinding] = field(default_factory=list)


@dataclass
class CommandSecretAssignment:
    """A resolved secret assignment for a CLI command."""
    path: str
    path_segments: list[str] = field(default_factory=list)
    value: Any = None
