"""Memory multimodal support (image and audio memory files).

Mirrors openclaw/src/memory-host-sdk/host/multimodal.ts.

Provides settings, file classification, and label generation for
memory files that contain images or audio rather than text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MemoryMultimodalModality = Literal["image", "audio"]
MemoryMultimodalSelection = Literal["image", "audio", "all"]

# ---------------------------------------------------------------------------
# File extension specs — mirrors MEMORY_MULTIMODAL_SPECS
# ---------------------------------------------------------------------------

_MULTIMODAL_SPECS: dict[str, dict[str, object]] = {
    "image": {
        "label_prefix": "Image file",
        "extensions": [".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"],
    },
    "audio": {
        "label_prefix": "Audio file",
        "extensions": [".mp3", ".wav", ".ogg", ".opus", ".m4a", ".aac", ".flac"],
    },
}

MEMORY_MULTIMODAL_MODALITIES: list[str] = list(_MULTIMODAL_SPECS.keys())

DEFAULT_MEMORY_MULTIMODAL_MAX_FILE_BYTES: int = 10 * 1024 * 1024  # 10 MiB


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@dataclass
class MemoryMultimodalSettings:
    """Resolved multimodal settings.

    Mirrors TS MemoryMultimodalSettings.
    """

    enabled: bool = False
    modalities: list[str] = field(default_factory=list)
    max_file_bytes: int = DEFAULT_MEMORY_MULTIMODAL_MAX_FILE_BYTES


def normalize_memory_multimodal_modalities(
    raw: list[str] | None = None,
) -> list[str]:
    """Resolve the list of enabled modalities.

    Mirrors TS normalizeMemoryMultimodalModalities().
    """
    if raw is None or "all" in raw:
        return list(MEMORY_MULTIMODAL_MODALITIES)
    normalized: list[str] = []
    for value in raw:
        if value in _MULTIMODAL_SPECS and value not in normalized:
            normalized.append(value)
    return normalized


def normalize_memory_multimodal_settings(
    raw: dict | None = None,
) -> MemoryMultimodalSettings:
    """Parse raw config dict into a resolved MemoryMultimodalSettings.

    Mirrors TS normalizeMemoryMultimodalSettings().
    """
    if raw is None:
        raw = {}
    enabled = raw.get("enabled") is True
    raw_max = raw.get("maxFileBytes") or raw.get("max_file_bytes")
    if isinstance(raw_max, (int, float)) and raw_max > 0:
        max_file_bytes = max(1, int(raw_max))
    else:
        max_file_bytes = DEFAULT_MEMORY_MULTIMODAL_MAX_FILE_BYTES
    modalities = (
        normalize_memory_multimodal_modalities(raw.get("modalities"))
        if enabled
        else []
    )
    return MemoryMultimodalSettings(
        enabled=enabled,
        modalities=modalities,
        max_file_bytes=max_file_bytes,
    )


def is_memory_multimodal_enabled(settings: MemoryMultimodalSettings) -> bool:
    """Return True if multimodal is enabled and at least one modality is active.

    Mirrors TS isMemoryMultimodalEnabled().
    """
    return settings.enabled and bool(settings.modalities)


def get_memory_multimodal_extensions(modality: str) -> list[str]:
    """Return the file extensions for a given modality.

    Mirrors TS getMemoryMultimodalExtensions().
    """
    spec = _MULTIMODAL_SPECS.get(modality)
    if not spec:
        return []
    return list(spec["extensions"])  # type: ignore[arg-type]


def build_memory_multimodal_label(modality: str, normalized_path: str) -> str:
    """Build a human-readable label for a multimodal memory entry.

    Mirrors TS buildMemoryMultimodalLabel().
    """
    spec = _MULTIMODAL_SPECS.get(modality, {})
    label_prefix = spec.get("label_prefix", "File")
    return f"{label_prefix}: {normalized_path}"


def classify_memory_multimodal_path(
    file_path: str,
    settings: MemoryMultimodalSettings,
) -> str | None:
    """Return the modality for *file_path* if it matches a known extension, else None.

    Mirrors TS classifyMemoryMultimodalPath().
    """
    if not is_memory_multimodal_enabled(settings):
        return None
    lower = file_path.lower()
    for modality in settings.modalities:
        for ext in get_memory_multimodal_extensions(modality):
            if lower.endswith(ext):
                return modality
    return None


def build_case_insensitive_extension_glob(extension: str) -> str:
    """Build a glob pattern that matches an extension case-insensitively.

    Mirrors TS buildCaseInsensitiveExtensionGlob().

    Example::

        build_case_insensitive_extension_glob(".png") -> "*.[pP][nN][gG]"
    """
    normalized = extension.lstrip(".").lower()
    if not normalized:
        return "*"
    parts = [f"[{c}{c.upper()}]" for c in normalized]
    return "*." + "".join(parts)


__all__ = [
    "MEMORY_MULTIMODAL_MODALITIES",
    "DEFAULT_MEMORY_MULTIMODAL_MAX_FILE_BYTES",
    "MemoryMultimodalSettings",
    "normalize_memory_multimodal_modalities",
    "normalize_memory_multimodal_settings",
    "is_memory_multimodal_enabled",
    "get_memory_multimodal_extensions",
    "build_memory_multimodal_label",
    "classify_memory_multimodal_path",
    "build_case_insensitive_extension_glob",
]
