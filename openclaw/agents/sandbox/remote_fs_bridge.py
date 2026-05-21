"""Remote shell (SSH) sandbox filesystem bridge.

Matches TypeScript openclaw/src/agents/sandbox/remote-fs-bridge.ts
"""
from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass
from typing import Any, Literal, Optional, Protocol

from .path_guard.path_utils import is_path_inside_container_root, normalize_container_path
from .ssh import SandboxBackendCommandResult, ssh_stub_enabled

SANDBOX_PINNED_MUTATION_PYTHON = "# openclaw sandbox pinned mutation helper (stub)"


@dataclass
class SandboxResolvedPath:
    relative_path: str
    container_path: str


@dataclass
class SandboxFsStat:
    type: Literal["file", "directory", "other"]
    size: int
    mtime_ms: float


class SandboxFsBridgeContext(Protocol):
    workspace_dir: str
    agent_workspace_dir: str
    workspace_access: Literal["none", "ro", "rw"]


class RemoteShellSandboxHandle(Protocol):
    remote_workspace_dir: str
    remote_agent_workspace_dir: str

    async def run_remote_shell_script(self, params: Any) -> SandboxBackendCommandResult: ...


@dataclass
class _MountInfo:
    container_root: str
    writable: bool
    source: Literal["workspace", "agent"]


@dataclass
class _ResolvedRemotePath(SandboxResolvedPath):
    writable: bool
    mount_root_path: str
    source: Literal["workspace", "agent"]


class RemoteShellSandboxFsBridge:
    """FS bridge that executes mutations on a remote host via shell scripts."""

    def __init__(
        self,
        sandbox: SandboxFsBridgeContext,
        runtime: RemoteShellSandboxHandle,
    ) -> None:
        self._sandbox = sandbox
        self._runtime = runtime

    def resolve_path(self, *, file_path: str, cwd: Optional[str] = None) -> SandboxResolvedPath:
        target = self._resolve_target(file_path=file_path, cwd=cwd)
        return SandboxResolvedPath(
            relative_path=target.relative_path,
            container_path=target.container_path,
        )

    async def read_file(
        self,
        *,
        file_path: str,
        cwd: Optional[str] = None,
        signal: Any = None,
    ) -> bytes:
        target = self._resolve_target(file_path=file_path, cwd=cwd)
        relative_path = posixpath.relpath(target.container_path, target.mount_root_path)
        if (
            relative_path in ("", ".")
            or relative_path.startswith("..")
            or posixpath.isabs(relative_path)
        ):
            raise ValueError(f"Invalid sandbox entry target: {target.container_path}")
        parent = posixpath.dirname(relative_path)
        result = await self._run_mutation(
            args=[
                "read",
                target.mount_root_path,
                "" if parent == "." else parent,
                posixpath.basename(relative_path),
            ],
            signal=signal,
        )
        return result.stdout

    async def write_file(
        self,
        *,
        file_path: str,
        cwd: Optional[str] = None,
        data: bytes | str,
        encoding: str = "utf-8",
        mkdir: bool = True,
        signal: Any = None,
    ) -> None:
        target = self._resolve_target(file_path=file_path, cwd=cwd)
        self._ensure_writable(target, "write files")
        if ssh_stub_enabled():
            return
        buffer = data if isinstance(data, bytes) else data.encode(encoding)
        pinned = await self._resolve_pinned_parent(
            container_path=target.container_path,
            action="write files",
            require_writable=True,
        )
        await self._run_mutation(
            args=[
                "write",
                pinned["mount_root_path"],
                pinned["relative_parent_path"],
                pinned["basename"],
                "1" if mkdir else "0",
            ],
            stdin=buffer,
            signal=signal,
        )

    async def mkdirp(
        self,
        *,
        file_path: str,
        cwd: Optional[str] = None,
        signal: Any = None,
    ) -> None:
        target = self._resolve_target(file_path=file_path, cwd=cwd)
        self._ensure_writable(target, "create directories")
        if ssh_stub_enabled():
            return
        relative_path = posixpath.relpath(target.container_path, target.mount_root_path)
        if relative_path.startswith("..") or posixpath.isabs(relative_path):
            raise ValueError(
                f"Sandbox path escapes allowed mounts; cannot create directories: {target.container_path}"
            )
        await self._run_mutation(
            args=["mkdirp", target.mount_root_path, "" if relative_path == "." else relative_path],
            signal=signal,
        )

    async def remove(
        self,
        *,
        file_path: str,
        cwd: Optional[str] = None,
        recursive: bool = False,
        force: bool = True,
        signal: Any = None,
    ) -> None:
        target = self._resolve_target(file_path=file_path, cwd=cwd)
        self._ensure_writable(target, "remove files")
        if ssh_stub_enabled():
            return
        exists = await self._remote_path_exists(target.container_path, signal)
        if not exists:
            if force is False:
                raise FileNotFoundError(
                    f"Sandbox path not found; cannot remove files: {target.container_path}"
                )
            return
        pinned = await self._resolve_pinned_parent(
            container_path=target.container_path,
            action="remove files",
            require_writable=True,
            allow_final_symlink_for_unlink=True,
        )
        await self._run_mutation(
            args=[
                "remove",
                pinned["mount_root_path"],
                pinned["relative_parent_path"],
                pinned["basename"],
                "1" if recursive else "0",
                "0" if force is False else "1",
            ],
            signal=signal,
            allow_failure=force is not False,
        )

    async def rename(
        self,
        *,
        from_path: str,
        to_path: str,
        cwd: Optional[str] = None,
        signal: Any = None,
    ) -> None:
        from_target = self._resolve_target(file_path=from_path, cwd=cwd)
        to_target = self._resolve_target(file_path=to_path, cwd=cwd)
        self._ensure_writable(from_target, "rename files")
        self._ensure_writable(to_target, "rename files")
        if ssh_stub_enabled():
            return
        from_pinned = await self._resolve_pinned_parent(
            container_path=from_target.container_path,
            action="rename files",
            require_writable=True,
            allow_final_symlink_for_unlink=True,
        )
        to_pinned = await self._resolve_pinned_parent(
            container_path=to_target.container_path,
            action="rename files",
            require_writable=True,
        )
        await self._run_mutation(
            args=[
                "rename",
                from_pinned["mount_root_path"],
                from_pinned["relative_parent_path"],
                from_pinned["basename"],
                to_pinned["mount_root_path"],
                to_pinned["relative_parent_path"],
                to_pinned["basename"],
                "1",
            ],
            signal=signal,
        )

    async def stat(
        self,
        *,
        file_path: str,
        cwd: Optional[str] = None,
        signal: Any = None,
    ) -> Optional[SandboxFsStat]:
        target = self._resolve_target(file_path=file_path, cwd=cwd)
        if ssh_stub_enabled():
            return None
        exists = await self._remote_path_exists(target.container_path, signal)
        if not exists:
            return None
        result = await self._run_remote_script(
            script='set -eu\nstat -c "%F|%s|%Y" -- "$1"',
            args=[target.container_path],
            signal=signal,
        )
        output = result.stdout.decode("utf-8", errors="replace").strip()
        parts = output.split("|")
        kind_raw = parts[0] if parts else ""
        size_raw = parts[1] if len(parts) > 1 else "0"
        mtime_raw = parts[2] if len(parts) > 2 else "0"
        kind: Literal["file", "directory", "other"]
        if kind_raw == "directory":
            kind = "directory"
        elif kind_raw == "regular file":
            kind = "file"
        else:
            kind = "other"
        return SandboxFsStat(
            type=kind,
            size=int(size_raw or 0),
            mtime_ms=int(mtime_raw or 0) * 1000,
        )

    def _get_mounts(self) -> list[_MountInfo]:
        mounts = [
            _MountInfo(
                container_root=_normalize_container_path(self._runtime.remote_workspace_dir),
                writable=self._sandbox.workspace_access == "rw",
                source="workspace",
            )
        ]
        if (
            self._sandbox.workspace_access != "none"
            and os.path.realpath(self._sandbox.agent_workspace_dir)
            != os.path.realpath(self._sandbox.workspace_dir)
        ):
            mounts.append(
                _MountInfo(
                    container_root=_normalize_container_path(
                        self._runtime.remote_agent_workspace_dir
                    ),
                    writable=self._sandbox.workspace_access == "rw",
                    source="agent",
                )
            )
        return mounts

    def _resolve_target(self, *, file_path: str, cwd: Optional[str]) -> _ResolvedRemotePath:
        workspace_root = os.path.realpath(self._sandbox.workspace_dir)
        agent_root = os.path.realpath(self._sandbox.agent_workspace_dir)
        workspace_container_root = _normalize_container_path(self._runtime.remote_workspace_dir)
        agent_container_root = _normalize_container_path(self._runtime.remote_agent_workspace_dir)
        mounts = self._get_mounts()
        input_path = file_path.strip()
        input_posix = input_path.replace("\\", "/")
        if posixpath.isabs(input_posix):
            maybe = self._resolve_mount_by_container_path(
                mounts, _normalize_container_path(input_posix)
            )
            if maybe:
                return self._to_resolved_path(
                    mount=maybe,
                    container_path=_normalize_container_path(input_posix),
                )

        host_cwd = os.path.realpath(cwd) if cwd else workspace_root
        host_candidate = (
            os.path.realpath(input_path)
            if os.path.isabs(input_path)
            else os.path.realpath(os.path.join(host_cwd, input_path))
        )
        if _is_path_inside(workspace_root, host_candidate):
            relative = _to_posix_relative(workspace_root, host_candidate)
            container = (
                posixpath.join(workspace_container_root, relative)
                if relative
                else workspace_container_root
            )
            return self._to_resolved_path(mount=mounts[0], container_path=container)
        if len(mounts) > 1 and _is_path_inside(agent_root, host_candidate):
            relative = _to_posix_relative(agent_root, host_candidate)
            container = (
                posixpath.join(agent_container_root, relative)
                if relative
                else agent_container_root
            )
            return self._to_resolved_path(mount=mounts[1], container_path=container)

        if cwd:
            cwd_posix = cwd.replace("\\", "/")
            if posixpath.isabs(cwd_posix):
                cwd_container = _normalize_container_path(cwd_posix)
                cwd_mount = self._resolve_mount_by_container_path(mounts, cwd_container)
                if cwd_mount:
                    return self._to_resolved_path(
                        mount=cwd_mount,
                        container_path=_normalize_container_path(
                            posixpath.join(cwd_container, input_posix)
                        ),
                    )

        raise ValueError(f"Sandbox path escapes allowed mounts; cannot access: {file_path}")

    def _to_resolved_path(
        self,
        *,
        mount: _MountInfo,
        container_path: str,
    ) -> _ResolvedRemotePath:
        relative = posixpath.relpath(container_path, mount.container_root)
        if relative.startswith("..") or posixpath.isabs(relative):
            raise ValueError(
                f"Sandbox path escapes allowed mounts; cannot access: {container_path}"
            )
        if mount.source == "workspace":
            rel_path = "" if relative == "." else relative
        else:
            rel_path = mount.container_root if relative == "." else f"{mount.container_root}/{relative}"
        return _ResolvedRemotePath(
            relative_path=rel_path,
            container_path=container_path,
            writable=mount.writable,
            mount_root_path=mount.container_root,
            source=mount.source,
        )

    def _resolve_mount_by_container_path(
        self,
        mounts: list[_MountInfo],
        container_path: str,
    ) -> Optional[_MountInfo]:
        ordered = sorted(mounts, key=lambda m: len(m.container_root), reverse=True)
        for mount in ordered:
            if is_path_inside_container_root(mount.container_root, container_path):
                return mount
        return None

    def _ensure_writable(self, target: _ResolvedRemotePath, action: str) -> None:
        if self._sandbox.workspace_access != "rw" or not target.writable:
            raise PermissionError(
                f"Sandbox path is read-only; cannot {action}: {target.container_path}"
            )

    async def _remote_path_exists(self, container_path: str, signal: Any = None) -> bool:
        if ssh_stub_enabled():
            return False
        result = await self._run_remote_script(
            script='if [ -e "$1" ] || [ -L "$1" ]; then printf "1\\n"; else printf "0\\n"; fi',
            args=[container_path],
            signal=signal,
        )
        return result.stdout.decode("utf-8", errors="replace").strip() == "1"

    async def _resolve_pinned_parent(
        self,
        *,
        container_path: str,
        action: str,
        require_writable: bool = False,
        allow_final_symlink_for_unlink: bool = False,
    ) -> dict[str, str]:
        basename = posixpath.basename(container_path)
        if not basename or basename in (".", "/"):
            raise ValueError(f"Invalid sandbox entry target: {container_path}")
        canonical_parent = await self._resolve_canonical_path(
            container_path=_normalize_container_path(posixpath.dirname(container_path)),
            action=action,
            allow_final_symlink_for_unlink=allow_final_symlink_for_unlink,
        )
        mount = self._resolve_mount_by_container_path(self._get_mounts(), canonical_parent)
        if not mount:
            raise ValueError(
                f"Sandbox path escapes allowed mounts; cannot {action}: {container_path}"
            )
        if require_writable and not mount.writable:
            raise PermissionError(
                f"Sandbox path is read-only; cannot {action}: {container_path}"
            )
        relative_parent_path = posixpath.relpath(canonical_parent, mount.container_root)
        if relative_parent_path.startswith("..") or posixpath.isabs(relative_parent_path):
            raise ValueError(
                f"Sandbox path escapes allowed mounts; cannot {action}: {container_path}"
            )
        return {
            "mount_root_path": mount.container_root,
            "relative_parent_path": "" if relative_parent_path == "." else relative_parent_path,
            "basename": basename,
        }

    async def _resolve_canonical_path(
        self,
        *,
        container_path: str,
        action: str,
        allow_final_symlink_for_unlink: bool = False,
        signal: Any = None,
    ) -> str:
        if ssh_stub_enabled():
            return container_path
        script = "\n".join(
            [
                "set -eu",
                'target="$1"',
                'allow_final="$2"',
                'suffix=""',
                'probe="$target"',
                'if [ "$allow_final" = "1" ] && [ -L "$target" ]; then probe=$(dirname -- "$target"); fi',
                'cursor="$probe"',
                'while [ ! -e "$cursor" ] && [ ! -L "$cursor" ]; do',
                '  parent=$(dirname -- "$cursor")',
                '  if [ "$parent" = "$cursor" ]; then break; fi',
                '  base=$(basename -- "$cursor")',
                '  suffix="/$base$suffix"',
                '  cursor="$parent"',
                "done",
                'canonical=$(readlink -f -- "$cursor")',
                'printf "%s%s\\n" "$canonical" "$suffix"',
            ]
        )
        result = await self._run_remote_script(
            script=script,
            args=[container_path, "1" if allow_final_symlink_for_unlink else "0"],
            signal=signal,
        )
        canonical = _normalize_container_path(
            result.stdout.decode("utf-8", errors="replace").strip()
        )
        if not self._resolve_mount_by_container_path(self._get_mounts(), canonical):
            raise ValueError(
                f"Sandbox path escapes allowed mounts; cannot {action}: {container_path}"
            )
        return canonical

    async def _run_mutation(
        self,
        *,
        args: list[str],
        stdin: Optional[bytes | str] = None,
        signal: Any = None,
        allow_failure: bool = False,
    ) -> SandboxBackendCommandResult:
        script = "\n".join(
            [
                "set -eu",
                "python3 /dev/fd/3 \"$@\" 3<<'PY'",
                SANDBOX_PINNED_MUTATION_PYTHON,
                "PY",
            ]
        )
        return await self._run_remote_script(
            script=script,
            args=args,
            stdin=stdin,
            signal=signal,
            allow_failure=allow_failure,
        )

    async def _run_remote_script(
        self,
        *,
        script: str,
        args: Optional[list[str]] = None,
        stdin: Optional[bytes | str] = None,
        signal: Any = None,
        allow_failure: bool = False,
    ) -> SandboxBackendCommandResult:
        return await self._runtime.run_remote_shell_script(
            {
                "script": script,
                "args": args,
                "stdin": stdin,
                "signal": signal,
                "allow_failure": allow_failure,
            }
        )


def create_remote_shell_sandbox_fs_bridge(
    *,
    sandbox: SandboxFsBridgeContext,
    runtime: RemoteShellSandboxHandle,
) -> RemoteShellSandboxFsBridge:
    return RemoteShellSandboxFsBridge(sandbox, runtime)


def _normalize_container_path(value: str) -> str:
    normalized = normalize_container_path((value or "/").strip() or "/")
    return normalized if normalized.startswith("/") else f"/{normalized}"


def _is_path_inside(root: str, candidate: str) -> bool:
    return candidate == root or candidate.startswith(root + os.sep)


def _to_posix_relative(root: str, candidate: str) -> str:
    parts = [
        part
        for part in os.path.relpath(candidate, root).split(os.sep)
        if part and part != "."
    ]
    return posixpath.join(*parts) if parts else ""
