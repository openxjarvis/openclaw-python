"""Secrets audit -- mirrors TS src/secrets/audit.ts.

Scans config, .env files, and auth stores for plaintext secrets,
unresolved refs, shadowed refs, and legacy residue.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from .resolver import KNOWN_SECRET_ENV_VARS, resolve_secret_ref_value
from .types import (
    SecretRef,
    SecretsAuditFinding,
    SecretsAuditReport,
    SecretsAuditStatus,
    SecretsAuditSummary,
)

logger = logging.getLogger(__name__)

_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def run_secrets_audit(config: Any = None, env: dict[str, str] | None = None) -> SecretsAuditReport:
    """Run a full secrets audit and return a report.

    Scans:
    1. Config file for plaintext secrets and unresolved refs
    2. .env file for known secret env vars stored in plaintext
    3. Auth profile stores for plaintext credentials
    4. Legacy auth.json files for residual credentials
    """
    effective_env = env or dict(os.environ)
    findings: list[SecretsAuditFinding] = []
    files_scanned: list[str] = []

    home = Path.home()
    openclaw_dir = home / ".openclaw"
    config_path = openclaw_dir / "openclaw.json"
    state_dir = openclaw_dir / "state"
    env_path = openclaw_dir / ".env"

    if config_path.exists():
        files_scanned.append(str(config_path))

    if config is not None:
        _collect_config_secrets(config, str(config_path), findings)

    _collect_env_plaintext(str(env_path), findings, files_scanned)
    _collect_auth_store_secrets(state_dir, findings, files_scanned)
    _collect_legacy_auth_json(state_dir, findings, files_scanned)

    summary = _summarize(findings)
    status: SecretsAuditStatus = (
        "unresolved" if summary.unresolved_ref_count > 0
        else "findings" if findings
        else "clean"
    )

    return SecretsAuditReport(
        version=1,
        status=status,
        files_scanned=sorted(set(files_scanned)),
        summary=summary,
        findings=findings,
    )


def resolve_secrets_audit_exit_code(report: SecretsAuditReport, check: bool) -> int:
    if report.summary.unresolved_ref_count > 0:
        return 2
    if check and report.findings:
        return 1
    return 0


def _collect_config_secrets(
    config: Any,
    config_path: str,
    findings: list[SecretsAuditFinding],
) -> None:
    """Walk config looking for plaintext secret values."""
    channels = getattr(config, "channels", None)
    if channels is None:
        return

    secret_fields = {"token", "apiKey", "api_key", "botToken", "bot_token", "secret", "webhookSecret"}
    cfg_dict = vars(channels) if hasattr(channels, "__dict__") else {}

    for ch_name, ch_cfg in cfg_dict.items():
        if ch_name.startswith("_") or ch_cfg is None:
            continue
        inner = ch_cfg if isinstance(ch_cfg, dict) else (vars(ch_cfg) if hasattr(ch_cfg, "__dict__") else {})
        for field_name in secret_fields:
            val = inner.get(field_name)
            if isinstance(val, str) and val.strip():
                if not val.startswith("$ref:") and not val.startswith("env:"):
                    findings.append(SecretsAuditFinding(
                        code="PLAINTEXT_FOUND",
                        severity="warn",
                        file=config_path,
                        json_path=f"channels.{ch_name}.{field_name}",
                        message=f"channels.{ch_name}.{field_name} is stored as plaintext.",
                        provider=ch_name,
                    ))


def _collect_env_plaintext(
    env_path: str,
    findings: list[SecretsAuditFinding],
    files_scanned: list[str],
) -> None:
    """Scan .env file for known secret env var names."""
    p = Path(env_path)
    if not p.exists():
        return
    files_scanned.append(env_path)

    known_keys = set(KNOWN_SECRET_ENV_VARS.values())
    try:
        for line in p.read_text().splitlines():
            m = _ENV_LINE_RE.match(line)
            if not m:
                continue
            key = m.group(1)
            val = m.group(2).strip().strip("'\"")
            if key in known_keys and val:
                findings.append(SecretsAuditFinding(
                    code="PLAINTEXT_FOUND",
                    severity="warn",
                    file=env_path,
                    json_path=f"$env.{key}",
                    message=f"Potential secret found in .env ({key}).",
                ))
    except Exception as e:
        logger.debug("Failed to read %s: %s", env_path, e)


def _collect_auth_store_secrets(
    state_dir: Path,
    findings: list[SecretsAuditFinding],
    files_scanned: list[str],
) -> None:
    """Scan auth profile store files for plaintext credentials."""
    auth_profiles_path = state_dir / "auth-profiles.json"
    if not auth_profiles_path.exists():
        return
    files_scanned.append(str(auth_profiles_path))

    try:
        data = json.loads(auth_profiles_path.read_text())
    except Exception as e:
        findings.append(SecretsAuditFinding(
            code="REF_UNRESOLVED",
            severity="error",
            file=str(auth_profiles_path),
            json_path="<root>",
            message=f"Invalid JSON in auth-profiles store: {e}",
        ))
        return

    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        return

    for profile_id, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        provider = profile.get("provider", "")
        for key_field in ("apiKey", "api_key", "token"):
            val = profile.get(key_field)
            if isinstance(val, str) and val.strip():
                findings.append(SecretsAuditFinding(
                    code="PLAINTEXT_FOUND",
                    severity="warn",
                    file=str(auth_profiles_path),
                    json_path=f"profiles.{profile_id}.{key_field}",
                    message="Auth profile credential is stored as plaintext.",
                    provider=provider,
                    profile_id=profile_id,
                ))

        if profile.get("accessToken") or profile.get("refreshToken"):
            findings.append(SecretsAuditFinding(
                code="LEGACY_RESIDUE",
                severity="info",
                file=str(auth_profiles_path),
                json_path=f"profiles.{profile_id}",
                message="OAuth credentials are present (out of scope for static SecretRef migration).",
                provider=provider,
                profile_id=profile_id,
            ))


def _collect_legacy_auth_json(
    state_dir: Path,
    findings: list[SecretsAuditFinding],
    files_scanned: list[str],
) -> None:
    """Check for legacy auth.json files with residual credentials."""
    for auth_json in state_dir.glob("**/auth.json"):
        files_scanned.append(str(auth_json))
        try:
            data = json.loads(auth_json.read_text())
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for provider_id, value in data.items():
            if not isinstance(value, dict):
                continue
            if value.get("type") == "api_key" and value.get("key"):
                findings.append(SecretsAuditFinding(
                    code="LEGACY_RESIDUE",
                    severity="warn",
                    file=str(auth_json),
                    json_path=provider_id,
                    message="Legacy auth.json contains static api_key credentials.",
                    provider=provider_id,
                ))


def _summarize(findings: list[SecretsAuditFinding]) -> SecretsAuditSummary:
    return SecretsAuditSummary(
        plaintext_count=sum(1 for f in findings if f.code == "PLAINTEXT_FOUND"),
        unresolved_ref_count=sum(1 for f in findings if f.code == "REF_UNRESOLVED"),
        shadowed_ref_count=sum(1 for f in findings if f.code == "REF_SHADOWED"),
        legacy_residue_count=sum(1 for f in findings if f.code == "LEGACY_RESIDUE"),
    )
