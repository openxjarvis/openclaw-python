"""Install-time security scanning — mirrors openclaw/src/plugins/install-security-scan.ts (skill path)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from openclaw.security.skill_scanner import scan_skill_directory

logger = logging.getLogger(__name__)


class InstallScanBlocked(TypedDict, total=False):
    code: str
    reason: str


class InstallSecurityScanResult(TypedDict, total=False):
    blocked: InstallScanBlocked


@dataclass
class SkillInstallSpecMetadata:
    id: str | None = None
    kind: str = ""
    label: str | None = None
    bins: list[str] | None = None


async def scan_skill_install_source(
    *,
    skill_name: str,
    source_dir: str,
    origin: str,
    install_id: str,
    install_spec: SkillInstallSpecMetadata | dict[str, Any] | None = None,
    dangerously_force_unsafe_install: bool = False,
    scan_logger: Any | None = None,
) -> InstallSecurityScanResult | None:
    """Scan a skill directory before install — mirrors TS scanSkillInstallSource."""
    del install_id, install_spec, origin  # reserved for hook parity

    root = Path(source_dir).resolve()
    if not root.is_dir():
        return {
            "blocked": {
                "code": "security_scan_failed",
                "reason": f'Skill "{skill_name}" source directory is missing.',
            }
        }

    findings = scan_skill_directory(root)
    critical = [f for f in findings if f.matches]
    if not critical:
        return None

    count = sum(len(f.matches) for f in critical)
    reason = (
        f'Skill "{skill_name}" has {count} suspicious code pattern(s). '
        'Run "openclaw security audit --deep" for details.'
    )
    if scan_logger and hasattr(scan_logger, "warn"):
        scan_logger.warn(f'WARNING: Skill "{skill_name}" contains dangerous code patterns')

    if dangerously_force_unsafe_install:
        if scan_logger and hasattr(scan_logger, "warn"):
            scan_logger.warn(
                f'DANGEROUS OVERRIDE: forcing install of Skill "{skill_name}" despite security findings.'
            )
        return None

    return {"blocked": {"code": "security_scan_blocked", "reason": reason}}


__all__ = [
    "InstallSecurityScanResult",
    "SkillInstallSpecMetadata",
    "scan_skill_install_source",
]
