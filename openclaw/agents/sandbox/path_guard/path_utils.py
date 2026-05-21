"""Container path normalization helpers.

Matches TypeScript openclaw/src/agents/sandbox/path-utils.ts
"""
from __future__ import annotations

import posixpath


def normalize_container_path(value: str) -> str:
    normalized = posixpath.normpath(value)
    return "/" if normalized == "." else normalized


def is_path_inside_container_root(root: str, target: str) -> bool:
    normalized_root = normalize_container_path(root)
    normalized_target = normalize_container_path(target)
    if normalized_root == "/":
        return True
    return normalized_target == normalized_root or normalized_target.startswith(
        f"{normalized_root}/"
    )
