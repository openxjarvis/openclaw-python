"""Sandbox file-system bridge

Provides read/write/stat/mkdir/remove/rename operations inside a running Docker
sandbox container via `docker exec`. Mirrors TypeScript
openclaw/src/agents/sandbox/fs-bridge.ts exactly.
"""
from __future__ import annotations

import asyncio
import logging
import os
import posixpath
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .docker import exec_docker

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class SandboxResolvedPath:
    """A path resolved to both host and container representations."""

    host_path: str
    relative_path: str
    container_path: str
    writable: bool = True


@dataclass
class SandboxFsStat:
    """File-system stat result from the sandbox container."""

    type: str  # "file" | "directory" | "other"
    size: int
    mtime_ms: float


class SandboxFsBridge:
    """File-system bridge for sandbox containers.

    Executes all I/O via ``docker exec`` so the host never needs direct
    filesystem access to the container's workspace.  Matches the interface in
    TS ``SandboxFsBridgeImpl``.
    """

    def __init__(
        self,
        container_name: str,
        workspace_dir: str,
        container_workdir: str,
        workspace_access: str = "rw",
    ) -> None:
        """
        Args:
            container_name: Running container to exec into.
            workspace_dir: Host-side workspace directory path.
            container_workdir: Default working directory inside the container
                (e.g. ``/workspace``).
            workspace_access: ``"rw"`` | ``"ro"`` | ``"none"``.
        """
        self.container_name = container_name
        self.workspace_dir = workspace_dir
        self.container_workdir = container_workdir
        self.workspace_access = workspace_access

    # ------------------------------------------------------------------
    # Path resolution helpers
    # ------------------------------------------------------------------

    def resolve_path(self, file_path: str, cwd: str | None = None) -> SandboxResolvedPath:
        """Resolve *file_path* to an absolute container path.

        Relative paths are resolved against *cwd* (defaults to
        ``container_workdir``).
        """
        effective_cwd = cwd or self.container_workdir
        if posixpath.isabs(file_path):
            abs_path = posixpath.normpath(file_path)
        else:
            abs_path = posixpath.normpath(posixpath.join(effective_cwd, file_path))

        # Determine relative path
        try:
            rel = posixpath.relpath(abs_path, self.container_workdir)
        except ValueError:
            rel = abs_path

        # Determine corresponding host path (best-effort; used for metadata only)
        if abs_path.startswith(self.container_workdir):
            suffix = abs_path[len(self.container_workdir) :].lstrip("/")
            host_path = os.path.join(self.workspace_dir, suffix)
        else:
            host_path = abs_path

        writable = self.workspace_access == "rw"
        return SandboxResolvedPath(
            host_path=host_path,
            relative_path=rel,
            container_path=abs_path,
            writable=writable,
        )

    async def assert_path_safety(self, file_path: str, cwd: str | None = None) -> SandboxResolvedPath:
        """Resolve and verify a path is safe to access.

        Uses local Python path normalization to detect escape attempts.
        This avoids an extra Docker exec round-trip for every operation,
        matching the TS behavior where assertPathSafety is a synchronous check.

        Raises ``PermissionError`` on unsafe paths.
        """
        resolved = self.resolve_path(file_path, cwd)
        abs_path = resolved.container_path

        mount_boundary = self.container_workdir.rstrip("/")
        canonical = posixpath.normpath(abs_path)

        # Check mount boundary escape
        if not canonical.startswith(mount_boundary + "/") and canonical != mount_boundary:
            raise PermissionError(
                f"Path escapes mount boundary: {abs_path} resolves to {canonical} "
                f"(outside {mount_boundary})"
            )

        resolved.container_path = canonical
        return resolved

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def read_file(
        self, file_path: str, cwd: str | None = None
    ) -> bytes:
        """Read a file from the container, returning raw bytes."""
        target = await self.assert_path_safety(file_path, cwd)
        result = await self._run_command(
            'set -eu; cat -- "$1"',
            args=[target.container_path],
            allow_failure=False,
        )
        return result["stdout_bytes"]

    async def write_file(
        self,
        file_path: str,
        data: bytes | str,
        cwd: str | None = None,
        encoding: str = "utf-8",
        mkdir: bool = True,
    ) -> None:
        """Write *data* to *file_path* inside the container.

        Uses atomic write (mktemp + mv) to prevent partial writes.
        Mirrors TS fs-bridge.ts atomic write pattern.
        """
        target = await self.assert_path_safety(file_path, cwd)
        self._ensure_write_access(target, "write files")

        if isinstance(data, str):
            data = data.encode(encoding)

        # Atomic: write to temp file, then move into place
        script = (
            'set -eu; dir=$(dirname -- "$1"); '
            'if [ "$dir" != "." ]; then mkdir -p -- "$dir"; fi; '
            'tmp=$(mktemp -- "$1.XXXXXX"); '
            'cat >"$tmp"; '
            'mv -- "$tmp" "$1"'
        )

        await self._run_command(script, args=[target.container_path], stdin=data)

    async def mkdirp(self, file_path: str, cwd: str | None = None) -> None:
        """Create directory *file_path* (and parents) inside the container."""
        target = await self.assert_path_safety(file_path, cwd)
        self._ensure_write_access(target, "create directories")
        await self._run_command('set -eu; mkdir -p -- "$1"', args=[target.container_path])

    async def remove(
        self,
        file_path: str,
        cwd: str | None = None,
        recursive: bool = False,
        force: bool = True,
    ) -> None:
        """Remove *file_path* inside the container."""
        target = await self.assert_path_safety(file_path, cwd)
        self._ensure_write_access(target, "remove files")
        # Recheck safety via canonical path inside container before mutating
        await self._recheck_path_safety(target.container_path)

        flags: list[str] = []
        if force:
            flags.append("-f")
        if recursive:
            flags.append("-r")
        rm_cmd = "rm " + " ".join(flags) if flags else "rm"
        await self._run_command(
            f'set -eu; {rm_cmd} -- "$1"', args=[target.container_path]
        )

    async def rename(
        self, from_path: str, to_path: str, cwd: str | None = None
    ) -> None:
        """Move *from_path* to *to_path* inside the container."""
        src = await self.assert_path_safety(from_path, cwd)
        dst = await self.assert_path_safety(to_path, cwd)
        self._ensure_write_access(src, "rename files")
        self._ensure_write_access(dst, "rename files")
        await self._recheck_path_safety(src.container_path)
        await self._recheck_path_safety(dst.container_path)
        script = (
            'set -eu; dir=$(dirname -- "$2"); '
            'if [ "$dir" != "." ]; then mkdir -p -- "$dir"; fi; '
            'mv -- "$1" "$2"'
        )
        await self._run_command(script, args=[src.container_path, dst.container_path])

    async def stat(
        self, file_path: str, cwd: str | None = None
    ) -> SandboxFsStat | None:
        """Stat *file_path* inside the container.

        Returns ``None`` if the path does not exist.
        """
        target = self.resolve_path(file_path, cwd)
        result = await self._run_command(
            'set -eu; stat -c "%F|%s|%Y" -- "$1"',
            args=[target.container_path],
            allow_failure=True,
        )
        if result["code"] != 0:
            stderr = result.get("stderr", "")
            if "No such file or directory" in stderr:
                return None
            msg = stderr.strip() or f"stat failed with code {result['code']}"
            raise RuntimeError(f"stat failed for {target.container_path}: {msg}")

        text = result["stdout"].strip()
        parts = text.split("|")
        type_raw = parts[0] if len(parts) > 0 else ""
        size_raw = parts[1] if len(parts) > 1 else "0"
        mtime_raw = parts[2] if len(parts) > 2 else "0"

        try:
            size = int(size_raw)
        except ValueError:
            size = 0
        try:
            mtime_ms = int(mtime_raw) * 1000
        except ValueError:
            mtime_ms = 0

        return SandboxFsStat(
            type=_coerce_stat_type(type_raw),
            size=size,
            mtime_ms=float(mtime_ms),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_command(
        self,
        script: str,
        args: list[str] | None = None,
        stdin: bytes | None = None,
        allow_failure: bool = False,
    ) -> dict:
        """Execute *script* via ``docker exec -i`` in the sandbox container."""
        docker_args = [
            "exec",
            "-i",
            self.container_name,
            "sh",
            "-c",
            script,
            "openclaw-sandbox-fs",  # $0 — used in error messages inside the script
        ]
        if args:
            docker_args.extend(args)

        proc = await asyncio.create_subprocess_exec(
            "docker",
            *docker_args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await proc.communicate(input=stdin)
        code = proc.returncode or 0
        stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        if code != 0 and not allow_failure:
            msg = stderr_text.strip() or f"docker exec failed with code {code}"
            raise RuntimeError(msg)

        return {
            "stdout": stdout_text,
            "stdout_bytes": stdout_bytes or b"",
            "stderr": stderr_text,
            "code": code,
        }

    async def _recheck_path_safety(self, container_path: str) -> None:
        """Resolve canonical path inside the container and verify mount boundary.

        Mirrors TS recheckBeforeCommand which runs `readlink -f` inside
        the container to detect symlink-based escape attempts.
        """
        result = await self._run_command(
            'set -eu; readlink -f -- "$1" 2>/dev/null || echo "$1"',
            args=[container_path],
            allow_failure=True,
        )
        canonical = (result.get("stdout") or container_path).strip()
        mount_boundary = self.container_workdir.rstrip("/")
        if not canonical.startswith(mount_boundary + "/") and canonical != mount_boundary:
            raise PermissionError(
                f"Canonical path escapes mount boundary: {container_path} -> {canonical}"
            )

    def _ensure_write_access(self, target: SandboxResolvedPath, action: str) -> None:
        if self.workspace_access != "rw" or not target.writable:
            raise PermissionError(
                f"Sandbox path is read-only; cannot {action}: {target.container_path}"
            )


def _coerce_stat_type(type_raw: str) -> str:
    """Convert ``stat -c %F`` output to ``"file" | "directory" | "other"``."""
    normalized = type_raw.strip().lower()
    if "directory" in normalized:
        return "directory"
    if "file" in normalized:
        return "file"
    return "other"


def create_sandbox_fs_bridge(
    container_name: str,
    workspace_dir: str,
    container_workdir: str,
    workspace_access: str = "rw",
) -> SandboxFsBridge:
    """Factory function — mirrors TS ``createSandboxFsBridge()``."""
    return SandboxFsBridge(
        container_name=container_name,
        workspace_dir=workspace_dir,
        container_workdir=container_workdir,
        workspace_access=workspace_access,
    )
