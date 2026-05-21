"""SSH sandbox backend implementation.

Matches TypeScript openclaw/src/agents/sandbox/ssh-backend.ts
"""
from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Protocol

from .config import resolve_sandbox_config_for_agent
from .remote_fs_bridge import (
    RemoteShellSandboxHandle,
    create_remote_shell_sandbox_fs_bridge,
)
from .sanitize_env_vars import sanitize_env_vars
from .ssh import (
    SshSandboxSession,
    SshSandboxSettings,
    build_exec_remote_command,
    build_remote_command,
    build_ssh_sandbox_argv,
    create_ssh_sandbox_session_from_settings,
    dispose_ssh_sandbox_session,
    run_ssh_sandbox_command,
    ssh_stub_enabled,
    upload_directory_to_ssh_target,
)


@dataclass
class SandboxBackendCommandResult:
    stdout: bytes
    stderr: bytes
    code: int


@dataclass
class SandboxBackendCommandParams:
    script: str
    args: Optional[list[str]] = None
    stdin: Optional[bytes | str] = None
    allow_failure: bool = False
    signal: Any = None


@dataclass
class SandboxBackendExecSpec:
    argv: list[str]
    env: dict[str, str]
    stdin_mode: str
    finalize_token: Any = None


@dataclass
class CreateSandboxBackendParams:
    session_key: str
    scope_key: str
    workspace_dir: str
    agent_workspace_dir: str
    cfg: Any


class SandboxBackendHandle(Protocol):
    id: str
    runtime_id: str
    runtime_label: str
    workdir: str
    env: Optional[dict[str, str]]
    config_label: Optional[str]
    config_label_kind: Optional[str]
    remote_workspace_dir: str
    remote_agent_workspace_dir: str

    async def build_exec_spec(
        self,
        *,
        command: str,
        workdir: Optional[str],
        env: dict[str, str],
        use_pty: bool,
    ) -> SandboxBackendExecSpec: ...

    async def finalize_exec(self, **kwargs: Any) -> None: ...

    async def run_shell_command(
        self, params: SandboxBackendCommandParams
    ) -> SandboxBackendCommandResult: ...

    def create_fs_bridge(self, *, sandbox: Any) -> Any: ...


@dataclass
class SandboxBackendRuntimeInfo:
    running: bool
    actual_config_label: Optional[str]
    config_label_match: bool


class SandboxBackendManager(Protocol):
    async def describe_runtime(self, **kwargs: Any) -> SandboxBackendRuntimeInfo: ...
    async def remove_runtime(self, **kwargs: Any) -> None: ...


@dataclass
class _ResolvedSshRuntimePaths:
    runtime_id: str
    runtime_root_dir: str
    remote_workspace_dir: str
    remote_agent_workspace_dir: str


@dataclass
class _PendingExec:
    ssh_session: SshSandboxSession


class _SshSandboxBackendImpl(RemoteShellSandboxHandle):
    def __init__(
        self,
        *,
        create_params: CreateSandboxBackendParams,
        target: str,
        runtime_paths: _ResolvedSshRuntimePaths,
    ) -> None:
        self._create_params = create_params
        self._target = target
        self._runtime_paths = runtime_paths
        self._ensure_promise: Optional[Awaitable[None]] = None

    @property
    def id(self) -> str:
        return "ssh"

    @property
    def runtime_id(self) -> str:
        return self._runtime_paths.runtime_id

    @property
    def runtime_label(self) -> str:
        return self._runtime_paths.runtime_id

    @property
    def workdir(self) -> str:
        return self._runtime_paths.remote_workspace_dir

    @property
    def env(self) -> dict[str, str]:
        return getattr(self._create_params.cfg.docker, "env", {}) or {}

    @property
    def config_label(self) -> str:
        return self._target

    @property
    def config_label_kind(self) -> str:
        return "Target"

    @property
    def remote_workspace_dir(self) -> str:
        return self._runtime_paths.remote_workspace_dir

    @property
    def remote_agent_workspace_dir(self) -> str:
        return self._runtime_paths.remote_agent_workspace_dir

    def as_handle(self) -> SandboxBackendHandle:
        return self  # type: ignore[return-value]

    async def build_exec_spec(
        self,
        *,
        command: str,
        workdir: Optional[str],
        env: dict[str, str],
        use_pty: bool,
    ) -> SandboxBackendExecSpec:
        await self._ensure_runtime()
        ssh_session = await self._create_session()
        remote_command = build_exec_remote_command(
            command=command,
            workdir=workdir or self._runtime_paths.remote_workspace_dir,
            env=env,
        )
        return SandboxBackendExecSpec(
            argv=build_ssh_sandbox_argv(
                session=ssh_session,
                remote_command=remote_command,
                tty=use_pty,
            ),
            env=sanitize_env_vars(dict(os.environ)).allowed,
            stdin_mode="pipe-open",
            finalize_token=_PendingExec(ssh_session=ssh_session),
        )

    async def finalize_exec(self, **kwargs: Any) -> None:
        token = kwargs.get("token")
        if isinstance(token, _PendingExec):
            await dispose_ssh_sandbox_session(token.ssh_session)

    async def run_shell_command(
        self, params: SandboxBackendCommandParams
    ) -> SandboxBackendCommandResult:
        return await self.run_remote_shell_script(params)

    def create_fs_bridge(self, *, sandbox: Any) -> Any:
        return create_remote_shell_sandbox_fs_bridge(sandbox=sandbox, runtime=self)

    async def run_remote_shell_script(
        self, params: SandboxBackendCommandParams | dict[str, Any]
    ) -> SandboxBackendCommandResult:
        if isinstance(params, dict):
            params = SandboxBackendCommandParams(
                script=params["script"],
                args=params.get("args"),
                stdin=params.get("stdin"),
                allow_failure=bool(params.get("allow_failure")),
                signal=params.get("signal"),
            )
        await self._ensure_runtime()
        session = await self._create_session()
        try:
            result = await run_ssh_sandbox_command(
                session=session,
                remote_command=build_remote_command(
                    [
                        "/bin/sh",
                        "-c",
                        params.script,
                        "openclaw-sandbox-fs",
                        *(params.args or []),
                    ]
                ),
                stdin=params.stdin,
                allow_failure=params.allow_failure,
                signal=params.signal,
            )
            return SandboxBackendCommandResult(
                stdout=result.stdout,
                stderr=result.stderr,
                code=result.code,
            )
        finally:
            await dispose_ssh_sandbox_session(session)

    async def _create_session(self) -> SshSandboxSession:
        ssh_cfg = self._create_params.cfg.ssh
        return await create_ssh_sandbox_session_from_settings(
            SshSandboxSettings(
                command=getattr(ssh_cfg, "command", "ssh") or "ssh",
                target=self._target,
                strict_host_key_checking=bool(
                    getattr(ssh_cfg, "strict_host_key_checking", True)
                ),
                update_host_keys=bool(getattr(ssh_cfg, "update_host_keys", False)),
                identity_file=getattr(ssh_cfg, "identity_file", None),
                certificate_file=getattr(ssh_cfg, "certificate_file", None),
                known_hosts_file=getattr(ssh_cfg, "known_hosts_file", None),
                identity_data=getattr(ssh_cfg, "identity_data", None),
                certificate_data=getattr(ssh_cfg, "certificate_data", None),
                known_hosts_data=getattr(ssh_cfg, "known_hosts_data", None),
            )
        )

    async def _ensure_runtime(self) -> None:
        if self._ensure_promise is not None:
            await self._ensure_promise
            return
        self._ensure_promise = self._ensure_runtime_inner()
        try:
            await self._ensure_promise
        except Exception:
            self._ensure_promise = None
            raise

    async def _ensure_runtime_inner(self) -> None:
        if ssh_stub_enabled():
            return
        session = await self._create_session()
        try:
            exists = await run_ssh_sandbox_command(
                session=session,
                remote_command=build_remote_command(
                    [
                        "/bin/sh",
                        "-c",
                        'if [ -d "$1" ]; then printf "1\\n"; else printf "0\\n"; fi',
                        "openclaw-sandbox-check",
                        self._runtime_paths.runtime_root_dir,
                    ]
                ),
            )
            if exists.stdout.decode("utf-8", errors="replace").strip() == "1":
                return
            await self._replace_remote_directory_from_local(
                session,
                self._create_params.workspace_dir,
                self._runtime_paths.remote_workspace_dir,
            )
            if (
                getattr(self._create_params.cfg, "workspace_access", "none") != "none"
                and os.path.realpath(self._create_params.agent_workspace_dir)
                != os.path.realpath(self._create_params.workspace_dir)
            ):
                await self._replace_remote_directory_from_local(
                    session,
                    self._create_params.agent_workspace_dir,
                    self._runtime_paths.remote_agent_workspace_dir,
                )
        finally:
            await dispose_ssh_sandbox_session(session)

    async def _replace_remote_directory_from_local(
        self,
        session: SshSandboxSession,
        local_dir: str,
        remote_dir: str,
    ) -> None:
        await run_ssh_sandbox_command(
            session=session,
            remote_command=build_remote_command(
                [
                    "/bin/sh",
                    "-c",
                    'mkdir -p -- "$1" && find "$1" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +',
                    "openclaw-sandbox-clear",
                    remote_dir,
                ]
            ),
        )
        await upload_directory_to_ssh_target(
            session=session,
            local_dir=local_dir,
            remote_dir=remote_dir,
        )


async def _describe_ssh_runtime(**kwargs: Any) -> SandboxBackendRuntimeInfo:
    entry = kwargs["entry"]
    config = kwargs["config"]
    agent_id = kwargs.get("agent_id")
    cfg = resolve_sandbox_config_for_agent(config, agent_id)
    if getattr(cfg, "backend", None) != "ssh" or not getattr(cfg.ssh, "target", None):
        target = getattr(cfg.ssh, "target", None)
        return SandboxBackendRuntimeInfo(
            running=False,
            actual_config_label=target,
            config_label_match=False,
        )
    runtime_paths = _resolve_ssh_runtime_paths(cfg.ssh.workspace_root, entry.session_key)
    session = await create_ssh_sandbox_session_from_settings(
        SshSandboxSettings(
            command=getattr(cfg.ssh, "command", "ssh") or "ssh",
            target=cfg.ssh.target,
            strict_host_key_checking=bool(
                getattr(cfg.ssh, "strict_host_key_checking", True)
            ),
            update_host_keys=bool(getattr(cfg.ssh, "update_host_keys", False)),
            identity_file=getattr(cfg.ssh, "identity_file", None),
            certificate_file=getattr(cfg.ssh, "certificate_file", None),
            known_hosts_file=getattr(cfg.ssh, "known_hosts_file", None),
            identity_data=getattr(cfg.ssh, "identity_data", None),
            certificate_data=getattr(cfg.ssh, "certificate_data", None),
            known_hosts_data=getattr(cfg.ssh, "known_hosts_data", None),
        )
    )
    try:
        if ssh_stub_enabled():
            return SandboxBackendRuntimeInfo(
                running=False,
                actual_config_label=cfg.ssh.target,
                config_label_match=getattr(entry, "image", None) == cfg.ssh.target,
            )
        result = await run_ssh_sandbox_command(
            session=session,
            remote_command=build_remote_command(
                [
                    "/bin/sh",
                    "-c",
                    'if [ -d "$1" ]; then printf "1\\n"; else printf "0\\n"; fi',
                    "openclaw-sandbox-check",
                    runtime_paths.runtime_root_dir,
                ]
            ),
        )
        running = result.stdout.decode("utf-8", errors="replace").strip() == "1"
        return SandboxBackendRuntimeInfo(
            running=running,
            actual_config_label=cfg.ssh.target,
            config_label_match=getattr(entry, "image", None) == cfg.ssh.target,
        )
    finally:
        await dispose_ssh_sandbox_session(session)


async def _remove_ssh_runtime(**kwargs: Any) -> None:
    entry = kwargs["entry"]
    config = kwargs["config"]
    agent_id = kwargs.get("agent_id")
    cfg = resolve_sandbox_config_for_agent(config, agent_id)
    if getattr(cfg, "backend", None) != "ssh" or not getattr(cfg.ssh, "target", None):
        return
    if ssh_stub_enabled():
        return
    runtime_paths = _resolve_ssh_runtime_paths(cfg.ssh.workspace_root, entry.session_key)
    session = await create_ssh_sandbox_session_from_settings(
        SshSandboxSettings(
            command=getattr(cfg.ssh, "command", "ssh") or "ssh",
            target=cfg.ssh.target,
            strict_host_key_checking=bool(
                getattr(cfg.ssh, "strict_host_key_checking", True)
            ),
            update_host_keys=bool(getattr(cfg.ssh, "update_host_keys", False)),
            identity_file=getattr(cfg.ssh, "identity_file", None),
            certificate_file=getattr(cfg.ssh, "certificate_file", None),
            known_hosts_file=getattr(cfg.ssh, "known_hosts_file", None),
            identity_data=getattr(cfg.ssh, "identity_data", None),
            certificate_data=getattr(cfg.ssh, "certificate_data", None),
            known_hosts_data=getattr(cfg.ssh, "known_hosts_data", None),
        )
    )
    try:
        await run_ssh_sandbox_command(
            session=session,
            remote_command=build_remote_command(
                [
                    "/bin/sh",
                    "-c",
                    'rm -rf -- "$1"',
                    "openclaw-sandbox-remove",
                    runtime_paths.runtime_root_dir,
                ]
            ),
            allow_failure=True,
        )
    finally:
        await dispose_ssh_sandbox_session(session)


class _SshSandboxBackendManager:
    describe_runtime = staticmethod(_describe_ssh_runtime)
    remove_runtime = staticmethod(_remove_ssh_runtime)


ssh_sandbox_backend_manager: SandboxBackendManager = _SshSandboxBackendManager()


async def create_ssh_sandbox_backend(
    params: CreateSandboxBackendParams,
) -> SandboxBackendHandle:
    binds = getattr(params.cfg.docker, "binds", None) or []
    if len(binds) > 0:
        raise ValueError("SSH sandbox backend does not support sandbox.docker.binds.")
    target = getattr(params.cfg.ssh, "target", None)
    if not target:
        raise ValueError('Sandbox backend "ssh" requires agents.defaults.sandbox.ssh.target.')
    runtime_paths = _resolve_ssh_runtime_paths(
        getattr(params.cfg.ssh, "workspace_root", "/tmp/openclaw-sandboxes"),
        params.scope_key,
    )
    impl = _SshSandboxBackendImpl(
        create_params=params,
        target=target,
        runtime_paths=runtime_paths,
    )
    return impl.as_handle()


def _resolve_ssh_runtime_paths(workspace_root: str, scope_key: str) -> _ResolvedSshRuntimePaths:
    runtime_id = _build_ssh_sandbox_runtime_id(scope_key)
    runtime_root_dir = posixpath.join(workspace_root, runtime_id)
    return _ResolvedSshRuntimePaths(
        runtime_id=runtime_id,
        runtime_root_dir=runtime_root_dir,
        remote_workspace_dir=posixpath.join(runtime_root_dir, "workspace"),
        remote_agent_workspace_dir=posixpath.join(runtime_root_dir, "agent"),
    )


def _build_ssh_sandbox_runtime_id(scope_key: str) -> str:
    trimmed = (scope_key or "").strip() or "session"
    safe = re.sub(r"[^a-z0-9._-]+", "-", trimmed.lower())
    safe = re.sub(r"^-+|-+$", "", safe)[:32]
    acc = 5381
    for char in trimmed:
        acc = ((acc * 33) ^ ord(char)) & 0xFFFFFFFF
    hash_hex = format(acc, "x")[:8]
    return f"openclaw-ssh-{safe or 'session'}-{hash_hex}"
