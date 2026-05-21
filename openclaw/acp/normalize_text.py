"""ACP text normalization.

Mirrors TypeScript src/acp/normalize-text.ts

Normalizes text for use in ACP messages: standardizes whitespace,
Unicode, and line endings so messages are consistent across channels.
"""
from __future__ import annotations

import re
import unicodedata


# Patterns for normalization
_MULTIPLE_SPACES_RE = re.compile(r"[ \t]+")
_MULTIPLE_NEWLINES_RE = re.compile(r"\n{3,}")
_TRAILING_WHITESPACE_RE = re.compile(r"[ \t]+$", re.MULTILINE)
_CRLF_RE = re.compile(r"\r\n|\r")
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")  # zero-width chars


def normalize_acp_text(text: str) -> str:
    """Normalize text for ACP message formatting.

    Mirrors TS normalizeAcpText() from normalize-text.ts.

    Operations:
    1. Normalize Unicode to NFC form
    2. Remove zero-width characters
    3. Normalize CRLF to LF
    4. Collapse multiple spaces/tabs within lines
    5. Remove trailing whitespace from each line
    6. Collapse 3+ consecutive newlines to 2
    7. Strip leading/trailing whitespace from the whole text
    """
    if not text:
        return ""

    # Step 1: Unicode NFC normalization
    text = unicodedata.normalize("NFC", text)

    # Step 2: Remove zero-width characters
    text = _ZERO_WIDTH_RE.sub("", text)

    # Step 3: Normalize line endings
    text = _CRLF_RE.sub("\n", text)

    # Step 4: Collapse multiple spaces/tabs within lines
    lines = text.split("\n")
    lines = [_MULTIPLE_SPACES_RE.sub(" ", line) for line in lines]
    text = "\n".join(lines)

    # Step 5: Remove trailing whitespace from each line
    text = _TRAILING_WHITESPACE_RE.sub("", text)

    # Step 6: Collapse 3+ newlines to 2
    text = _MULTIPLE_NEWLINES_RE.sub("\n\n", text)

    # Step 7: Strip
    return text.strip()


def normalize_acp_command_text(text: str) -> str:
    """Normalize text that may be an ACP command.

    Additional normalization for command parsing:
    - Strip leading slash variants
    - Lowercase the command prefix
    """
    normalized = normalize_acp_text(text)
    if normalized.startswith("/"):
        # Normalize the command name (lowercase) but preserve args
        parts = normalized.split(None, 1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        return f"{cmd} {rest}".strip()
    return normalized
