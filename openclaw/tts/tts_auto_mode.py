"""TTS auto mode normalization (mirrors TS tts-auto-mode.ts)."""
from __future__ import annotations

from typing import Literal

TtsAutoMode = Literal["off", "always", "inbound", "tagged"]

TTS_AUTO_MODES: set[str] = {"off", "always", "inbound", "tagged"}


def normalize_tts_auto_mode(value: object) -> TtsAutoMode | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in TTS_AUTO_MODES:
        return normalized  # type: ignore[return-value]
    return None
