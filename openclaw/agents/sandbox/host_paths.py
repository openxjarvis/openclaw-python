"""POSIX host path normalization for sandbox mounts.

Matches TypeScript openclaw/src/agents/sandbox/host-paths.ts
"""
from __future__ import annotations

import os
import posixpath
import re


def strip_windows_namespace_prefix(input_path: str) -> str:
    if input_path.startswith("\\\\?\\"):
        without_prefix = input_path[4:]
        if without_prefix.upper().startswith("UNC\\"):
            return f"\\\\{without_prefix[4:]}"
        return without_prefix
    if input_path.startswith("//?/"):
        without_prefix = input_path[4:]
        if without_prefix.upper().startswith("UNC/"):
            return f"//{without_prefix[4:]}"
        return without_prefix
    return input_path


def normalize_sandbox_host_path(raw: str) -> str:
    """Normalize a POSIX host path: resolve `.`, `..`, collapse `//`, strip trailing `/`."""
    trimmed = strip_windows_namespace_prefix(raw.strip())
    if not trimmed:
        return "/"
    normalized = posixpath.normpath(trimmed.replace("\\", "/"))
    return re.sub(r"/+$", "", normalized) or "/"


def resolve_sandbox_host_path_via_existing_ancestor(source_path: str) -> str:
    """Resolve through the deepest existing ancestor (symlink-safe stub).

    Full boundary-path resolution requires host FS access; this stub walks
    existing path components only.
    """
    if not source_path.startswith("/"):
        return source_path
    current = normalize_sandbox_host_path(source_path)
    while current not in ("/", "") and not os.path.exists(current):
        parent = posixpath.dirname(current)
        if parent == current:
            break
        current = parent
    if os.path.exists(current):
        try:
            return normalize_sandbox_host_path(os.path.realpath(current))
        except OSError:
            pass
    return normalize_sandbox_host_path(source_path)
