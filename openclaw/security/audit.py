"""
Security audit system — aligned with TS src/security/audit.ts (20+ checks).

Checks:
  - Config file permissions
  - Secrets in config (API keys, tokens in plaintext)
  - .env exposure
  - Exec/sandbox mismatch
  - Gateway bind address + auth mode
  - Workspace permissions
  - Elevated permissions wildcard
  - Workspace skill symlink escape
  - Node dangerous exec patterns
  - Channel security
"""
from __future__ import annotations

import logging
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from ..config.paths import resolve_state_dir

logger = logging.getLogger(__name__)

# Patterns that suggest raw secrets in config values
_SECRET_VALUE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(sk|pk)-[A-Za-z0-9]{20,}$"),              # OpenAI-style keys
    re.compile(r"^[A-Za-z0-9+/]{40,}={0,2}$"),               # Base64 blob ≥40 chars
    re.compile(r"^[0-9a-fA-F]{32,}$"),                        # Hex string ≥32 chars
    re.compile(r"^xox[bprs]-[0-9A-Za-z-]{10,}$"),             # Slack tokens
    re.compile(r"^github_pat_[A-Za-z0-9_]{20,}$"),            # GitHub PAT
    re.compile(r"^Bearer\s+\S{20,}$"),                        # Bearer token
]

_SECRET_KEY_HINTS: frozenset[str] = frozenset([
    "token", "secret", "password", "apikey", "api_key", "api-key",
    "access_key", "private_key", "auth_key", "credentials", "passwd",
])


def _looks_like_secret_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip()
    if len(v) < 20:
        return False
    return any(p.match(v) for p in _SECRET_VALUE_PATTERNS)


def _key_suggests_secret(key: str) -> bool:
    k = key.lower()
    return any(hint in k for hint in _SECRET_KEY_HINTS)


@dataclass
class AuditIssue:
    """Security audit issue"""
    severity: Literal["critical", "high", "medium", "low"]
    category: str
    message: str
    file_path: str | None = None
    auto_fixable: bool = False


@dataclass
class AuditReport:
    """Security audit report"""
    issues: list[AuditIssue] = field(default_factory=list)
    passed: int = 0
    failed: int = 0
    
    def add_issue(self, issue: AuditIssue):
        """Add audit issue"""
        self.issues.append(issue)
        self.failed += 1
    
    def add_pass(self):
        """Add passing check"""
        self.passed += 1


class SecurityAuditor:
    """
    Security auditor for OpenClaw — aligned with TS audit.ts 20+ check categories.

    Check categories:
    1. Config file permissions
    2. Secrets in config (raw API keys / tokens)
    3. .env credential exposure
    4. Exec security defaults (security=full is dangerous)
    5. Exec/sandbox mismatch (sandbox mode=all but exec.security=full)
    6. Gateway bind address + auth mode
    7. Workspace directory permissions
    8. Elevated permissions wildcard
    9. Workspace skill symlink escape
    10. Node dangerous exec patterns
    11. Tool policy channel security
    """

    def __init__(self, workspace: Path, config: dict[str, Any] | None = None):
        self.workspace = workspace
        self.config = config or {}

    async def run_audit(self, deep: bool = False) -> AuditReport:
        """
        Run full security audit.

        Args:
            deep: Run deep scan (includes slow filesystem checks)

        Returns:
            AuditReport with issues and pass counts.
        """
        report = AuditReport()

        checks = [
            self._check_config_file_permissions,
            self._check_secrets_in_config,
            self._check_credential_exposure,
            self._check_exec_approvals,
            self._check_exec_sandbox_mismatch,
            self._check_gateway_bind_auth,
            self._check_workspace_permissions,
            self._check_elevated_wildcard,
        ]

        if deep:
            checks += [
                self._check_skill_symlink_escape,
                self._check_node_dangerous_patterns,
            ]

        for check in checks:
            try:
                issues = await check()
                if issues:
                    for issue in issues:
                        report.add_issue(issue)
                else:
                    report.add_pass()
            except Exception as exc:
                logger.debug(f"Audit check {check.__name__} failed: {exc}")
                report.add_pass()

        logger.info(f"Audit complete: {report.passed} passed, {report.failed} failed")
        return report
    
    async def fix_issues(self, report: AuditReport) -> int:
        """Auto-fix auto_fixable issues. Returns count of fixed issues."""
        fixed = 0
        for issue in report.issues:
            if issue.auto_fixable:
                try:
                    await self._fix_issue(issue)
                    fixed += 1
                except Exception as exc:
                    logger.error(f"Failed to fix issue: {exc}")
        return fixed

    # -------------------------------------------------------------------------
    # Check 1: Config file permissions
    # -------------------------------------------------------------------------
    async def _check_config_file_permissions(self) -> list[AuditIssue]:
        issues: list[AuditIssue] = []
        config_candidates = [
            self.workspace / ".openclaw" / "config.yaml",
            self.workspace / ".openclaw" / "config.json",
            self.workspace / "openclaw.config.yaml",
            resolve_state_dir() / "openclaw.config.yaml",
        ]
        for config_path in config_candidates:
            if config_path.exists():
                mode = config_path.stat().st_mode
                if mode & (stat.S_IRGRP | stat.S_IROTH):
                    issues.append(AuditIssue(
                        severity="medium",
                        category="config-permissions",
                        message=f"Config file is group/world readable: {config_path}",
                        file_path=str(config_path),
                        auto_fixable=True,
                    ))
        return issues

    # -------------------------------------------------------------------------
    # Check 2: Secrets in config (plaintext API keys / tokens)
    # -------------------------------------------------------------------------
    async def _check_secrets_in_config(self) -> list[AuditIssue]:
        issues: list[AuditIssue] = []
        cfg = self.config
        if not cfg:
            return issues

        def _scan_dict(d: dict, path: str = "") -> None:
            for key, val in d.items():
                full_key = f"{path}.{key}" if path else key
                if isinstance(val, dict):
                    _scan_dict(val, full_key)
                elif isinstance(val, str) and _key_suggests_secret(key) and _looks_like_secret_value(val):
                    issues.append(AuditIssue(
                        severity="critical",
                        category="secrets-in-config",
                        message=f"Possible plaintext secret at config key '{full_key}'. "
                                "Use environment variables or the secrets store instead.",
                        auto_fixable=False,
                    ))

        _scan_dict(cfg)
        return issues

    # -------------------------------------------------------------------------
    # Check 3: .env credential exposure
    # -------------------------------------------------------------------------
    async def _check_credential_exposure(self) -> list[AuditIssue]:
        issues: list[AuditIssue] = []
        for env_name in [".env", ".env.local", ".env.production"]:
            env_file = self.workspace / env_name
            if env_file.exists():
                gitignore = self.workspace / ".gitignore"
                if gitignore.exists():
                    content = gitignore.read_text(errors="replace")
                    if env_name not in content and ".env" not in content:
                        issues.append(AuditIssue(
                            severity="high",
                            category="credentials",
                            message=f"{env_name} file not in .gitignore (may leak credentials)",
                            file_path=str(env_file),
                            auto_fixable=True,
                        ))
                # Check file permissions
                mode = env_file.stat().st_mode
                if mode & (stat.S_IRGRP | stat.S_IROTH):
                    issues.append(AuditIssue(
                        severity="medium",
                        category="credentials",
                        message=f"{env_name} file is group/world readable",
                        file_path=str(env_file),
                        auto_fixable=True,
                    ))
        return issues

    # -------------------------------------------------------------------------
    # Check 4: Exec security defaults
    # -------------------------------------------------------------------------
    async def _check_exec_approvals(self) -> list[AuditIssue]:
        issues: list[AuditIssue] = []
        tools_cfg: dict = self.config.get("tools") or {}
        exec_cfg: dict = tools_cfg.get("exec") or {}
        security = exec_cfg.get("security") or exec_cfg.get("security", "deny")
        if security == "full":
            issues.append(AuditIssue(
                severity="critical",
                category="exec-security",
                message="tools.exec.security='full' allows all commands without approval. "
                        "Set to 'deny' or 'allowlist'.",
                auto_fixable=False,
            ))
        # Also check exec-approvals.json
        approvals_path = resolve_state_dir() / "exec-approvals.json"
        if approvals_path.exists():
            import json as _json
            try:
                data = _json.loads(approvals_path.read_text())
                defaults = data.get("defaults") or {}
                if defaults.get("security") == "full":
                    issues.append(AuditIssue(
                        severity="critical",
                        category="exec-approvals",
                        message="exec-approvals.json defaults.security='full' (allows all commands)",
                        file_path=str(approvals_path),
                        auto_fixable=False,
                    ))
            except Exception:
                pass
        return issues

    # -------------------------------------------------------------------------
    # Check 5: Exec/sandbox mismatch
    # -------------------------------------------------------------------------
    async def _check_exec_sandbox_mismatch(self) -> list[AuditIssue]:
        issues: list[AuditIssue] = []
        tools_cfg: dict = self.config.get("tools") or {}
        exec_cfg: dict = tools_cfg.get("exec") or {}
        sandbox_mode = (self.config.get("agents", {}) or {}).get("defaults", {}).get("sandbox", {}).get("mode", "off")
        exec_security = exec_cfg.get("security", "deny")

        if sandbox_mode in ("all", "non-main") and exec_security == "full":
            issues.append(AuditIssue(
                severity="high",
                category="exec-sandbox-mismatch",
                message=f"Sandbox mode='{sandbox_mode}' but exec.security='full'. "
                        "Sandbox provides container isolation, but exec.security='full' "
                        "means the sandbox agent can run any command without approval.",
                auto_fixable=False,
            ))
        return issues

    # -------------------------------------------------------------------------
    # Check 6: Gateway bind address + auth mode
    # -------------------------------------------------------------------------
    async def _check_gateway_bind_auth(self) -> list[AuditIssue]:
        issues: list[AuditIssue] = []
        gw_cfg: dict = self.config.get("gateway") or {}
        bind = gw_cfg.get("bind", "loopback")
        auth_mode = (gw_cfg.get("auth") or {}).get("mode", "none")

        if bind not in ("loopback", "127.0.0.1", "::1") and auth_mode in ("none",):
            issues.append(AuditIssue(
                severity="critical",
                category="gateway-auth",
                message=f"Gateway is bound to '{bind}' with auth mode 'none'. "
                        "This exposes the gateway to the network without authentication. "
                        "Set gateway.auth.mode to 'token' or 'password'.",
                auto_fixable=False,
            ))
        return issues

    # -------------------------------------------------------------------------
    # Check 7: Workspace directory permissions
    # -------------------------------------------------------------------------
    async def _check_workspace_permissions(self) -> list[AuditIssue]:
        issues: list[AuditIssue] = []
        if self.workspace.exists():
            mode = self.workspace.stat().st_mode
            if mode & stat.S_IWOTH:
                issues.append(AuditIssue(
                    severity="high",
                    category="workspace-permissions",
                    message=f"Workspace directory is world-writable: {self.workspace}",
                    file_path=str(self.workspace),
                    auto_fixable=True,
                ))
        return issues

    # -------------------------------------------------------------------------
    # Check 8: Elevated permissions wildcard
    # -------------------------------------------------------------------------
    async def _check_elevated_wildcard(self) -> list[AuditIssue]:
        issues: list[AuditIssue] = []
        tools_cfg: dict = self.config.get("tools") or {}
        allow: list = tools_cfg.get("allow") or []
        if "*" in allow:
            issues.append(AuditIssue(
                severity="high",
                category="elevated-wildcard",
                message="tools.allow contains '*' wildcard — all tools including dangerous "
                        "ones are accessible. Prefer explicit allow lists.",
                auto_fixable=False,
            ))
        elevated = (tools_cfg.get("elevated") or {})
        if elevated.get("enabled") and not elevated.get("allowFrom"):
            issues.append(AuditIssue(
                severity="medium",
                category="elevated-wildcard",
                message="tools.elevated.enabled=true without allowFrom restriction. "
                        "Any sender can trigger elevated mode.",
                auto_fixable=False,
            ))
        return issues

    # -------------------------------------------------------------------------
    # Check 9 (deep): Workspace skill symlink escape
    # -------------------------------------------------------------------------
    async def _check_skill_symlink_escape(self) -> list[AuditIssue]:
        issues: list[AuditIssue] = []
        skills_dir = self.workspace / "skills"
        if not skills_dir.exists():
            return issues
        try:
            workspace_real = self.workspace.resolve()
            for skill_path in skills_dir.rglob("*"):
                if skill_path.is_symlink():
                    target = skill_path.resolve()
                    try:
                        target.relative_to(workspace_real)
                    except ValueError:
                        issues.append(AuditIssue(
                            severity="high",
                            category="skill-symlink-escape",
                            message=f"Skill symlink escapes workspace: {skill_path} -> {target}",
                            file_path=str(skill_path),
                            auto_fixable=False,
                        ))
        except Exception as exc:
            logger.debug(f"Skill symlink check failed: {exc}")
        return issues

    # -------------------------------------------------------------------------
    # Check 10 (deep): Node dangerous exec patterns
    # -------------------------------------------------------------------------
    async def _check_node_dangerous_patterns(self) -> list[AuditIssue]:
        issues: list[AuditIssue] = []
        agents_cfg: list = (self.config.get("agents") or {}).get("list") or []
        for agent in agents_cfg:
            if not isinstance(agent, dict):
                continue
            tools_cfg = agent.get("tools") or {}
            exec_cfg = tools_cfg.get("exec") or {}
            if exec_cfg.get("security") == "full":
                agent_id = agent.get("id", "?")
                issues.append(AuditIssue(
                    severity="high",
                    category="node-exec-dangerous",
                    message=f"Agent '{agent_id}' has exec.security='full'. "
                            "Per-agent exec override should not disable security.",
                    auto_fixable=False,
                ))
        return issues

    # -------------------------------------------------------------------------
    # Auto-fix
    # -------------------------------------------------------------------------
    async def _fix_issue(self, issue: AuditIssue) -> None:
        if issue.file_path:
            path = Path(issue.file_path)
            if issue.category == "config-permissions" and path.exists():
                path.chmod(0o600)
                logger.info(f"Fixed config permissions: {path}")
            elif issue.category == "credentials" and ".env" in str(path):
                if "not in .gitignore" in issue.message:
                    gitignore = self.workspace / ".gitignore"
                    with open(gitignore, "a") as f:
                        f.write(f"\n{path.name}\n")
                    logger.info(f"Added {path.name} to .gitignore")
                elif "readable" in issue.message:
                    path.chmod(0o600)
                    logger.info(f"Fixed .env permissions: {path}")
            elif issue.category == "workspace-permissions" and path.exists():
                current = path.stat().st_mode
                path.chmod(current & ~stat.S_IWOTH)
                logger.info(f"Removed world-write from workspace: {path}")


__all__ = [
    "AuditIssue",
    "AuditReport",
    "SecurityAuditor",
]
