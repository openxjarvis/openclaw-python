"""Boundary file open runtime (stub).

Matches TypeScript openclaw/src/agents/sandbox/fs-bridge-path-safety.runtime.ts
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional

from .types import PathAliasPolicy


@dataclass
class BoundaryFileOpenResult:
    ok: bool
    fd: Optional[int] = None
    reason: Optional[Literal["path", "type", "permission"]] = None
    error: Optional[BaseException] = None


async def open_boundary_file(
    *,
    absolute_path: str,
    root_path: str,
    boundary_label: str = "sandbox mount root",
    alias_policy: Optional[PathAliasPolicy] = None,
    allowed_type: Optional[str] = None,
) -> BoundaryFileOpenResult:
    """Open a path within a boundary (host-side stub).

    Production Docker sandboxes resolve paths inside the container; this stub
    validates host containment when the target exists locally.
    """
    del boundary_label, alias_policy, allowed_type
    root = os.path.realpath(root_path)
    try:
        resolved = os.path.realpath(absolute_path)
    except OSError as exc:
        return BoundaryFileOpenResult(ok=False, reason="path", error=exc)

    if resolved != root and not resolved.startswith(root + os.sep):
        return BoundaryFileOpenResult(
            ok=False,
            reason="path",
            error=ValueError(f"Path escapes {root_path}"),
        )

    if not os.path.exists(resolved):
        return BoundaryFileOpenResult(
            ok=False,
            reason="path",
            error=FileNotFoundError(resolved),
        )

    try:
        fd = os.open(resolved, os.O_RDONLY)
    except OSError as exc:
        return BoundaryFileOpenResult(ok=False, reason="permission", error=exc)

    return BoundaryFileOpenResult(ok=True, fd=fd)
