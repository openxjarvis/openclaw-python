"""Path guard type definitions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Optional, TypedDict


class SandboxFsMount(TypedDict):
    host_root: str
    container_root: str
    writable: bool
    source: Literal["workspace", "agent", "bind"]


class SandboxResolvedFsPath(TypedDict):
    host_path: str
    relative_path: str
    container_path: str
    writable: bool


class PathAliasPolicy(TypedDict, total=False):
    allow_final_symlink_for_unlink: bool


class PathSafetyOptions(TypedDict, total=False):
    action: str
    alias_policy: PathAliasPolicy
    require_writable: bool
    allowed_type: Literal["file", "directory", "any"]


@dataclass(frozen=True)
class PathSafetyCheck:
    target: SandboxResolvedFsPath
    options: PathSafetyOptions


@dataclass(frozen=True)
class PinnedSandboxEntry:
    mount_root_path: str
    relative_parent_path: str
    basename: str


@dataclass(frozen=True)
class AnchoredSandboxEntry:
    canonical_parent_path: str
    basename: str


@dataclass(frozen=True)
class PinnedSandboxDirectoryEntry:
    mount_root_path: str
    relative_path: str


RunCommand = Callable[
    ...,
    Awaitable[Any],
]
