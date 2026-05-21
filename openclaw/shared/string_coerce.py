"""String coercion helpers — mirrors TypeScript src/shared/string-coerce.ts."""
from __future__ import annotations


def normalize_optional_string(value: object | None) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if trimmed else None


def normalize_optional_lowercase_string(value: object | None) -> str | None:
    normalized = normalize_optional_string(value)
    return normalized.lower() if normalized else None


def normalize_lowercase_string_or_empty(value: object | None) -> str:
    return normalize_optional_lowercase_string(value) or ""
