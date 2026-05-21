"""Docker bind mount spec parsing.

Matches TypeScript openclaw/src/agents/sandbox/bind-spec.ts
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SplitBindSpec:
    host: str
    container: str
    options: str


def split_sandbox_bind_spec(spec: str) -> Optional[SplitBindSpec]:
    separator = _host_container_separator_index(spec)
    if separator == -1:
        return None

    host = spec[:separator]
    rest = spec[separator + 1 :]
    options_start = rest.find(":")
    if options_start == -1:
        return SplitBindSpec(host=host, container=rest, options="")
    return SplitBindSpec(
        host=host,
        container=rest[:options_start],
        options=rest[options_start + 1 :],
    )


def _host_container_separator_index(spec: str) -> int:
    has_drive_letter_prefix = bool(re.match(r"^[A-Za-z]:[\\/]", spec))
    start = 2 if has_drive_letter_prefix else 0
    for i in range(start, len(spec)):
        if spec[i] == ":":
            return i
    return -1
