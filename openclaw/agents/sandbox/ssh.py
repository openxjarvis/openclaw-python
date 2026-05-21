"""SSH sandbox session and command helpers.

Matches TypeScript openclaw/src/agents/sandbox/ssh.ts
"""
from __future__ import annotations

import asyncio
import logging
import os
import posixpath
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .sanitize_env_vars import sanitize_env_vars

logger = logging.getLogger(__name__)

SSH_STUB_ENV = "OPENCLAW_SANDBOX_SSH_STUB"


@dataclass
class SshSandboxSettings:
    command: str
    target: str
    strict_host_key_checking: bool
    update_host_keys: bool
    identity_file: Optional[str] = None
    certificate_file: Optional[str] = None
    known_hosts_file: Optional[str] = None
    identity_data: Optional[str] = None
    certificate_data: Optional[str] = None
    known_hosts_data: Optional[str] = None


@dataclass
class SshSandboxSession:
    command: str
    config_path: str
    host: str


@dataclass
class SandboxBackendCommandResult:
    stdout: bytes
    stderr: bytes
    code: int


@dataclass
class SshParsedTarget:
    host: str
    port: int
    user: Optional[str] = None


def ssh_stub_enabled() -> bool:
    if os.environ.get(SSH_STUB_ENV, "").strip().lower() in ("1", "true", "yes"):
        return True
    return shutil.which("ssh") is None


def parse_ssh_target(raw: str) -> Optional[SshParsedTarget]:
    trimmed = raw.strip()
    if trimmed.lower().startswith("ssh "):
        trimmed = trimmed[4:].strip()
    if not trimmed:
        return None

    user: Optional[str] = None
    host_part = trimmed
    if "@" in trimmed:
        at = trimmed.index("@")
        user = trimmed[:at].strip() or None
        host_part = trimmed[at + 1 :].strip()

    colon_idx = host_part.rfind(":")
    if colon_idx > 0 and colon_idx < len(host_part) - 1:
        host = host_part[:colon_idx].strip()
        port_raw = host_part[colon_idx + 1 :].strip()
        try:
            port = int(port_raw)
        except ValueError:
            return None
        if not host or port <= 0 or host.startswith("-"):
            return None
        return SshParsedTarget(user=user, host=host, port=port)

    if not host_part or host_part.startswith("-"):
        return None
    return SshParsedTarget(user=user, host=host_part, port=22)


def shell_escape(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def build_remote_command(argv: list[str]) -> str:
    return " ".join(shell_escape(entry) for entry in argv)


def build_exec_remote_command(
    *,
    command: str,
    workdir: Optional[str] = None,
    env: dict[str, str],
) -> str:
    body = f"cd {shell_escape(workdir)} && {command}" if workdir else command
    if env:
        argv = ["env", *[f"{k}={v}" for k, v in env.items()], "/bin/sh", "-c", body]
    else:
        argv = ["/bin/sh", "-c", body]
    return build_remote_command(argv)


def build_ssh_sandbox_argv(
    *,
    session: SshSandboxSession,
    remote_command: str,
    tty: bool = False,
) -> list[str]:
    tty_args = (
        ["-tt", "-o", "RequestTTY=force", "-o", "SetEnv=TERM=xterm-256color"]
        if tty
        else ["-T", "-o", "RequestTTY=no"]
    )
    return [
        session.command,
        "-F",
        session.config_path,
        *tty_args,
        session.host,
        remote_command,
    ]


async def create_ssh_sandbox_session_from_config_text(
    *,
    config_text: str,
    host: Optional[str] = None,
    command: Optional[str] = None,
) -> SshSandboxSession:
    resolved_host = (host or "").strip() or _parse_ssh_config_host(config_text)
    if not resolved_host:
        raise ValueError("Failed to parse SSH config output.")
    config_dir = tempfile.mkdtemp(prefix="openclaw-sandbox-ssh-", dir=_resolve_ssh_tmp_root())
    config_path = os.path.join(config_dir, "config")
    Path(config_path).write_text(config_text, encoding="utf-8")
    os.chmod(config_path, 0o600)
    return SshSandboxSession(
        command=(command or "").strip() or "ssh",
        config_path=config_path,
        host=resolved_host,
    )


async def create_ssh_sandbox_session_from_settings(
    settings: SshSandboxSettings,
) -> SshSandboxSession:
    parsed = parse_ssh_target(settings.target)
    if not parsed:
        raise ValueError(f"Invalid sandbox SSH target: {settings.target}")

    config_dir = tempfile.mkdtemp(prefix="openclaw-sandbox-ssh-", dir=_resolve_ssh_tmp_root())
    try:
        identity_file = (
            await _write_secret_material(config_dir, "identity", settings.identity_data)
            if settings.identity_data
            else _resolve_optional_local_path(settings.identity_file)
        )
        certificate_file = (
            await _write_secret_material(config_dir, "certificate.pub", settings.certificate_data)
            if settings.certificate_data
            else _resolve_optional_local_path(settings.certificate_file)
        )
        known_hosts_file = (
            await _write_secret_material(config_dir, "known_hosts", settings.known_hosts_data)
            if settings.known_hosts_data
            else _resolve_optional_local_path(settings.known_hosts_file)
        )
        host_alias = "openclaw-sandbox"
        config_path = os.path.join(config_dir, "config")
        lines = [
            f"Host {host_alias}",
            f"  HostName {parsed.host}",
            f"  Port {parsed.port}",
            "  BatchMode yes",
            "  ConnectTimeout 5",
            "  ServerAliveInterval 15",
            "  ServerAliveCountMax 3",
            f"  StrictHostKeyChecking {'yes' if settings.strict_host_key_checking else 'no'}",
            f"  UpdateHostKeys {'yes' if settings.update_host_keys else 'no'}",
        ]
        if parsed.user:
            lines.append(f"  User {parsed.user}")
        if known_hosts_file:
            lines.append(f"  UserKnownHostsFile {known_hosts_file}")
        elif not settings.strict_host_key_checking:
            lines.append("  UserKnownHostsFile /dev/null")
        if identity_file:
            lines.append(f"  IdentityFile {identity_file}")
        if certificate_file:
            lines.append(f"  CertificateFile {certificate_file}")
        if identity_file or certificate_file:
            lines.append("  IdentitiesOnly yes")
        Path(config_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(config_path, 0o600)
        return SshSandboxSession(
            command=settings.command.strip() or "ssh",
            config_path=config_path,
            host=host_alias,
        )
    except Exception:
        shutil.rmtree(config_dir, ignore_errors=True)
        raise


async def dispose_ssh_sandbox_session(session: SshSandboxSession) -> None:
    config_dir = os.path.dirname(session.config_path)
    shutil.rmtree(config_dir, ignore_errors=True)


async def run_ssh_sandbox_command(
    *,
    session: SshSandboxSession,
    remote_command: str,
    stdin: Optional[bytes | str] = None,
    allow_failure: bool = False,
    signal: Any = None,
    tty: bool = False,
) -> SandboxBackendCommandResult:
    if ssh_stub_enabled():
        logger.debug("SSH sandbox stub: skipping remote command")
        return SandboxBackendCommandResult(stdout=b"", stderr=b"", code=0)

    argv = build_ssh_sandbox_argv(
        session=session,
        remote_command=remote_command,
        tty=tty,
    )
    ssh_env = sanitize_env_vars(dict(os.environ)).allowed

    proc = await asyncio.create_subprocess_exec(
        argv[0],
        *argv[1:],
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=ssh_env,
    )
    stdin_payload: Optional[bytes] = None
    if stdin is not None:
        stdin_payload = stdin if isinstance(stdin, bytes) else stdin.encode("utf-8")
    stdout_bytes, stderr_bytes = await proc.communicate(input=stdin_payload)
    exit_code = proc.returncode or 0
    if exit_code != 0 and not allow_failure:
        message = _build_ssh_failure_message(stderr_bytes.decode("utf-8", errors="replace"), exit_code)
        err = RuntimeError(message)
        setattr(err, "code", exit_code)
        setattr(err, "stdout", stdout_bytes)
        setattr(err, "stderr", stderr_bytes)
        raise err
    return SandboxBackendCommandResult(stdout=stdout_bytes, stderr=stderr_bytes, code=exit_code)


async def upload_directory_to_ssh_target(
    *,
    session: SshSandboxSession,
    local_dir: str,
    remote_dir: str,
    signal: Any = None,
) -> None:
    if ssh_stub_enabled():
        logger.debug("SSH sandbox stub: skipping directory upload to %s", remote_dir)
        return

    await _assert_safe_upload_symlinks(local_dir)
    remote_command = build_remote_command(
        [
            "/bin/sh",
            "-c",
            'mkdir -p -- "$1" && tar -xf - -C "$1"',
            "openclaw-sandbox-upload",
            remote_dir,
        ]
    )
    ssh_argv = build_ssh_sandbox_argv(session=session, remote_command=remote_command)
    ssh_env = sanitize_env_vars(dict(os.environ)).allowed

    tar = await asyncio.create_subprocess_exec(
        "tar",
        "-C",
        local_dir,
        "-cf",
        "-",
        ".",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    ssh = await asyncio.create_subprocess_exec(
        ssh_argv[0],
        *ssh_argv[1:],
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=ssh_env,
    )
    assert tar.stdout is not None and ssh.stdin is not None
    tar_stdout, tar_stderr = await tar.communicate()
    ssh.stdin.write(tar_stdout)
    await ssh.stdin.drain()
    ssh.stdin.close()
    ssh_stdout, ssh_stderr = await ssh.communicate()
    tar_code = tar.returncode or 0
    ssh_code = ssh.returncode or 0
    if tar_code != 0:
        msg = tar_stderr.decode("utf-8", errors="replace").strip() or f"tar exited with code {tar_code}"
        raise RuntimeError(msg)
    if ssh_code != 0:
        msg = ssh_stderr.decode("utf-8", errors="replace").strip() or f"ssh exited with code {ssh_code}"
        raise RuntimeError(msg)
    del ssh_stdout, signal


def _build_ssh_failure_message(stderr: str, exit_code: Optional[int] = None) -> str:
    trimmed = stderr.strip()
    if (
        "error in libcrypto" in trimmed
        and ('Load key "' in trimmed or "Permission denied (publickey)" in trimmed)
    ):
        return (
            f"{trimmed}\nSSH sandbox failed to load the configured identity. "
            "The private key contents may be malformed (for example CRLF or escaped newlines). "
            "Prefer identityFile when possible."
        )
    if trimmed:
        return trimmed
    if exit_code is not None:
        return f"ssh exited with code {exit_code}"
    return "ssh exited with a non-zero status"


def _normalize_inline_ssh_material(contents: str, filename: str) -> str:
    without_bom = contents.lstrip("\ufeff")
    normalized_newlines = re.sub(r"\r\n?", "\n", without_bom)
    normalized_escaped = normalized_newlines.replace("\\r\\n", "\\n").replace("\\r", "\\n")
    if filename in ("identity", "certificate.pub"):
        expanded = normalized_escaped.replace("\\n", "\n")
    else:
        expanded = normalized_escaped
    return expanded if expanded.endswith("\n") else expanded + "\n"


def _parse_ssh_config_host(config_text: str) -> Optional[str]:
    match = re.search(r"^\s*Host\s+(\S+)", config_text, re.MULTILINE)
    return match.group(1).strip() if match else None


def _resolve_ssh_tmp_root() -> str:
    return os.path.realpath(os.environ.get("TMPDIR") or tempfile.gettempdir())


def _resolve_optional_local_path(value: Optional[str]) -> Optional[str]:
    trimmed = (value or "").strip()
    if not trimmed:
        return None
    return os.path.expanduser(trimmed)


async def _write_secret_material(dir_path: str, filename: str, contents: str) -> str:
    pathname = os.path.join(dir_path, filename)
    Path(pathname).write_text(
        _normalize_inline_ssh_material(contents, filename),
        encoding="utf-8",
    )
    os.chmod(pathname, 0o600)
    return pathname


async def _assert_safe_upload_symlinks(local_dir: str) -> None:
    root_dir = os.path.realpath(local_dir)

    def walk(current_dir: str) -> None:
        for entry in os.scandir(current_dir):
            entry_path = os.path.join(current_dir, entry.name)
            if entry.is_symlink():
                try:
                    resolved = os.path.realpath(entry_path)
                    if resolved != root_dir and not resolved.startswith(root_dir + os.sep):
                        rel = os.path.relpath(entry_path, root_dir).replace(os.sep, "/")
                        raise RuntimeError(
                            f"SSH sandbox upload refuses symlink escaping the workspace: {rel}"
                        )
                except OSError as exc:
                    rel = os.path.relpath(entry_path, root_dir).replace(os.sep, "/")
                    raise RuntimeError(
                        f"SSH sandbox upload refuses symlink escaping the workspace: {rel}"
                    ) from exc
                continue
            if entry.is_dir(follow_symlinks=False):
                walk(entry_path)

    walk(root_dir)
