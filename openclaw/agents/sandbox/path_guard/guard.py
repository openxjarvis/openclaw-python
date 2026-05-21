"""SandboxFsPathGuard implementation."""
from __future__ import annotations

import os
import posixpath
from typing import Awaitable, Callable, Optional

from .path_utils import is_path_inside_container_root, normalize_container_path
from .runtime import BoundaryFileOpenResult, open_boundary_file
from .types import (
    AnchoredSandboxEntry,
    PathSafetyCheck,
    PathSafetyOptions,
    PinnedSandboxDirectoryEntry,
    PinnedSandboxEntry,
    SandboxFsMount,
    SandboxResolvedFsPath,
)


RunCommandResult = dict[str, bytes]
RunCommand = Callable[..., Awaitable[RunCommandResult]]


class SandboxFsPathGuard:
    """Path safety checks for sandbox FS bridges.

    Matches TypeScript ``SandboxFsPathGuard`` in fs-bridge-path-safety.ts.
    """

    def __init__(
        self,
        *,
        mounts_by_container: list[SandboxFsMount],
        run_command: RunCommand,
    ) -> None:
        self._mounts_by_container = mounts_by_container
        self._run_command = run_command

    async def assert_path_checks(self, checks: list[PathSafetyCheck]) -> None:
        for check in checks:
            await self.assert_path_safety(check.target, check.options)

    async def assert_path_safety(
        self,
        target: SandboxResolvedFsPath,
        options: PathSafetyOptions,
    ) -> None:
        action = options.get("action", "access files")
        guarded = await self._open_boundary_within_required_mount(
            target,
            action,
            alias_policy=options.get("alias_policy"),
            allowed_type=options.get("allowed_type"),
        )
        await self._assert_guarded_path_safety(target, options, guarded)

    async def open_readable_file(
        self,
        target: SandboxResolvedFsPath,
    ) -> BoundaryFileOpenResult:
        opened = await self._open_boundary_within_required_mount(target, "read files")
        if not opened.ok:
            message = (
                str(opened.error)
                if opened.error
                else f"Sandbox boundary checks failed; cannot read files: {target['container_path']}"
            )
            raise RuntimeError(message)
        return opened

    def resolve_pinned_entry(
        self,
        target: SandboxResolvedFsPath,
        action: str,
    ) -> PinnedSandboxEntry:
        basename = posixpath.basename(target["container_path"])
        if not basename or basename in (".", "/"):
            raise ValueError(f"Invalid sandbox entry target: {target['container_path']}")
        parent_path = normalize_container_path(posixpath.dirname(target["container_path"]))
        mount = self._resolve_required_mount(parent_path, action)
        return self._finalize_pinned_entry(
            mount=mount,
            parent_path=parent_path,
            basename=basename,
            target_path=target["container_path"],
            action=action,
        )

    async def resolve_anchored_sandbox_entry(
        self,
        target: SandboxResolvedFsPath,
        action: str,
    ) -> AnchoredSandboxEntry:
        basename = posixpath.basename(target["container_path"])
        if not basename or basename in (".", "/"):
            raise ValueError(f"Invalid sandbox entry target: {target['container_path']}")
        parent_path = normalize_container_path(posixpath.dirname(target["container_path"]))
        canonical_parent_path = await self._resolve_canonical_container_path(
            container_path=parent_path,
            allow_final_symlink_for_unlink=False,
        )
        self._resolve_required_mount(canonical_parent_path, action)
        return AnchoredSandboxEntry(
            canonical_parent_path=canonical_parent_path,
            basename=basename,
        )

    async def resolve_anchored_pinned_entry(
        self,
        target: SandboxResolvedFsPath,
        action: str,
    ) -> PinnedSandboxEntry:
        anchored = await self.resolve_anchored_sandbox_entry(target, action)
        mount = self._resolve_required_mount(anchored.canonical_parent_path, action)
        return self._finalize_pinned_entry(
            mount=mount,
            parent_path=anchored.canonical_parent_path,
            basename=anchored.basename,
            target_path=target["container_path"],
            action=action,
        )

    def resolve_pinned_directory_entry(
        self,
        target: SandboxResolvedFsPath,
        action: str,
    ) -> PinnedSandboxDirectoryEntry:
        mount = self._resolve_required_mount(target["container_path"], action)
        relative_path = posixpath.relpath(target["container_path"], mount["container_root"])
        if relative_path.startswith("..") or posixpath.isabs(relative_path):
            raise ValueError(
                f"Sandbox path escapes allowed mounts; cannot {action}: {target['container_path']}"
            )
        return PinnedSandboxDirectoryEntry(
            mount_root_path=mount["container_root"],
            relative_path="" if relative_path == "." else relative_path,
        )

    def _resolve_required_mount(self, container_path: str, action: str) -> SandboxFsMount:
        lexical_mount = self._resolve_mount_by_container_path(container_path)
        if not lexical_mount:
            raise ValueError(
                f"Sandbox path escapes allowed mounts; cannot {action}: {container_path}"
            )
        return lexical_mount

    def _finalize_pinned_entry(
        self,
        *,
        mount: SandboxFsMount,
        parent_path: str,
        basename: str,
        target_path: str,
        action: str,
    ) -> PinnedSandboxEntry:
        relative_parent_path = posixpath.relpath(parent_path, mount["container_root"])
        if relative_parent_path.startswith("..") or posixpath.isabs(relative_parent_path):
            raise ValueError(
                f"Sandbox path escapes allowed mounts; cannot {action}: {target_path}"
            )
        return PinnedSandboxEntry(
            mount_root_path=mount["container_root"],
            relative_parent_path="" if relative_parent_path == "." else relative_parent_path,
            basename=basename,
        )

    async def _assert_guarded_path_safety(
        self,
        target: SandboxResolvedFsPath,
        options: PathSafetyOptions,
        guarded: BoundaryFileOpenResult,
    ) -> None:
        action = options.get("action", "access files")
        if not guarded.ok:
            if guarded.reason != "path":
                can_fallback = (
                    options.get("allowed_type") == "directory"
                    and self._path_is_existing_directory(target["host_path"])
                )
                if not can_fallback:
                    message = (
                        str(guarded.error)
                        if guarded.error
                        else f"Sandbox boundary checks failed; cannot {action}: {target['container_path']}"
                    )
                    raise RuntimeError(message)
        elif guarded.fd is not None:
            os.close(guarded.fd)

        canonical_container_path = await self._resolve_canonical_container_path(
            container_path=target["container_path"],
            allow_final_symlink_for_unlink=bool(
                (options.get("alias_policy") or {}).get("allow_final_symlink_for_unlink")
            ),
        )
        canonical_mount = self._resolve_required_mount(canonical_container_path, action)
        if options.get("require_writable") and not canonical_mount["writable"]:
            raise RuntimeError(
                f"Sandbox path is read-only; cannot {action}: {target['container_path']}"
            )

    async def _open_boundary_within_required_mount(
        self,
        target: SandboxResolvedFsPath,
        action: str,
        *,
        alias_policy: Optional[dict] = None,
        allowed_type: Optional[str] = None,
    ) -> BoundaryFileOpenResult:
        lexical_mount = self._resolve_required_mount(target["container_path"], action)
        return await open_boundary_file(
            absolute_path=target["host_path"],
            root_path=lexical_mount["host_root"],
            boundary_label="sandbox mount root",
            alias_policy=alias_policy,
            allowed_type=allowed_type,
        )

    def _path_is_existing_directory(self, host_path: str) -> bool:
        try:
            return os.path.isdir(host_path)
        except OSError:
            return False

    def _resolve_mount_by_container_path(self, container_path: str) -> Optional[SandboxFsMount]:
        normalized = normalize_container_path(container_path)
        for mount in self._mounts_by_container:
            if is_path_inside_container_root(
                normalize_container_path(mount["container_root"]),
                normalized,
            ):
                return mount
        return None

    async def _resolve_canonical_container_path(
        self,
        *,
        container_path: str,
        allow_final_symlink_for_unlink: bool,
    ) -> str:
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
        result = await self._run_command(
            script,
            args=[container_path, "1" if allow_final_symlink_for_unlink else "0"],
        )
        stdout = result.get("stdout", b"")
        canonical = stdout.decode("utf-8", errors="replace").strip()
        if not canonical.startswith("/"):
            raise RuntimeError(f"Failed to resolve canonical sandbox path: {container_path}")
        return normalize_container_path(canonical)
