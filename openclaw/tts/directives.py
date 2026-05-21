"""TTS directive parsing — mirrors TypeScript openclaw/src/tts/directives.ts.

Parses [[tts:...]] and [[tts:text]]...[[/tts:text]] directives embedded in
assistant message text, extracting TTS overrides (provider, voice, speed, etc.)
and producing cleaned text for speech synthesis.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class TtsDirectiveOverrides:
    """Extracted overrides from TTS directives."""
    tts_text: str | None = None            # Explicit TTS-only text
    provider: str | None = None            # Provider override
    provider_overrides: dict[str, Any] = field(default_factory=dict)  # Per-provider overrides


@dataclass
class TtsDirectiveParseResult:
    """Result of parsing TTS directives from text."""
    cleaned_text: str
    tts_text: str | None
    has_directive: bool
    overrides: TtsDirectiveOverrides
    warnings: list[str]


@dataclass
class SpeechModelOverridePolicy:
    """Policy controlling which directive overrides are allowed."""
    enabled: bool = True
    allow_text: bool = True
    allow_provider: bool = True


# ---------------------------------------------------------------------------
# Internal helpers (mirrors TS)
# ---------------------------------------------------------------------------

_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~|`+[^`\n]*`+|^(?: {4}|\t).*(?:\n|$)", re.MULTILINE)


def _collect_code_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for m in _CODE_BLOCK_RE.finditer(text):
        ranges.append((m.start(), m.end()))
    return sorted(ranges, key=lambda r: r[0])


def _is_inside_range(index: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= index < end for start, end in ranges)


def _replace_outside_code(
    text: str,
    pattern: re.Pattern,
    replacer,
) -> str:
    code_ranges = _collect_code_ranges(text)
    result = []
    last_end = 0
    for m in pattern.finditer(text):
        start = m.start()
        if _is_inside_range(start, code_ranges):
            result.append(text[last_end:m.end()])
            last_end = m.end()
            continue
        result.append(text[last_end:start])
        replacement = replacer(m)
        result.append(replacement)
        last_end = m.end()
    result.append(text[last_end:])
    return "".join(result)


def _normalize(s: str) -> str:
    return s.strip().replace(" ", "").lower()


def _classify_tts_tag(body: str) -> str:
    normalized = _normalize(body)
    if normalized == "tts:text":
        return "hidden-open"
    if normalized == "/tts:text":
        return "hidden-close"
    if normalized in ("tts", "/tts") or normalized.startswith("tts:") or normalized.startswith("/tts:"):
        return "tts"
    return "other"


# ---------------------------------------------------------------------------
# Stream cleaner (mirrors TS createTtsDirectiveTextStreamCleaner)
# ---------------------------------------------------------------------------

class TtsDirectiveTextStreamCleaner:
    """Incrementally strips [[tts:...]] directives from a text stream."""

    def __init__(self) -> None:
        self._pending = ""
        self._inside_hidden = False

    def push(self, text: str) -> str:
        inp = self._pending + text
        self._pending = ""
        output = []
        i = 0
        while i < len(inp):
            tag_start = inp.find("[[", i)
            if tag_start == -1:
                if not self._inside_hidden:
                    output.append(inp[i:])
                break
            if not self._inside_hidden:
                output.append(inp[i:tag_start])
            tag_end = inp.find("]]", tag_start + 2)
            if tag_end == -1:
                self._pending = inp[tag_start:]
                break
            body = inp[tag_start + 2:tag_end]
            tag_kind = _classify_tts_tag(body)
            if tag_kind == "hidden-open":
                self._inside_hidden = True
            elif tag_kind == "hidden-close":
                self._inside_hidden = False
            elif tag_kind == "other" and not self._inside_hidden:
                output.append(inp[tag_start:tag_end + 2])
            i = tag_end + 2
        return "".join(output)

    def flush(self) -> str:
        tail = self._pending
        self._pending = ""
        return "" if self._inside_hidden else tail

    def has_buffered_directive_text(self) -> bool:
        return bool(self._pending) or self._inside_hidden


def create_tts_directive_text_stream_cleaner() -> TtsDirectiveTextStreamCleaner:
    return TtsDirectiveTextStreamCleaner()


# ---------------------------------------------------------------------------
# Main parse function (mirrors TS parseTtsDirectives)
# ---------------------------------------------------------------------------

# Compiled patterns
_TEXT_BLOCK_RE = re.compile(
    r"\[\[\s*tts\s*:\s*text\s*\]\]([\s\S]*?)\[\[\s*/\s*tts\s*:\s*text\s*\]\]",
    re.IGNORECASE,
)
_PLAIN_BLOCK_RE = re.compile(
    r"\[\[\s*tts\s*\]\]([\s\S]*?)\[\[\s*/\s*tts\s*\]\]",
    re.IGNORECASE,
)
_DIRECTIVE_RE = re.compile(
    r"\[\[\s*tts\s*:\s*([^\]]+)\]\]",
    re.IGNORECASE,
)
_BARE_TAG_RE = re.compile(r"\[\[\s*tts\s*\]\]", re.IGNORECASE)
_CLOSING_TAG_RE = re.compile(r"\[\[\s*/\s*tts(?:\s*:\s*[^\]]*)?\]\]", re.IGNORECASE)

_QUICK_CHECK_RE = re.compile(r"\[\[\s*/?\s*tts(?:\s*:|\s*\]\])", re.IGNORECASE)


def parse_tts_directives(
    text: str,
    policy: SpeechModelOverridePolicy | None = None,
    *,
    cfg: Any = None,
    preferred_provider_id: str | None = None,
) -> TtsDirectiveParseResult:
    """Parse [[tts:...]] directives from text — mirrors TS parseTtsDirectives.

    Extracts TTS overrides and returns cleaned text (directives stripped).
    """
    if policy is None:
        policy = SpeechModelOverridePolicy()

    if not policy.enabled:
        return TtsDirectiveParseResult(
            cleaned_text=text, tts_text=None,
            has_directive=False, overrides=TtsDirectiveOverrides(), warnings=[],
        )

    if not _QUICK_CHECK_RE.search(text):
        return TtsDirectiveParseResult(
            cleaned_text=text, tts_text=None,
            has_directive=False, overrides=TtsDirectiveOverrides(), warnings=[],
        )

    overrides = TtsDirectiveOverrides()
    warnings: list[str] = []
    has_directive = False
    cleaned = text

    # [[tts:text]]...[[/tts:text]] — hidden TTS override block
    def _replace_text_block(m: re.Match) -> str:
        nonlocal has_directive
        has_directive = True
        inner = (m.group(1) or "").strip()
        if policy.allow_text and overrides.tts_text is None:
            overrides.tts_text = inner
        return ""
    cleaned = _replace_outside_code(cleaned, _TEXT_BLOCK_RE, _replace_text_block)

    # [[tts]]...[[/tts]] — visible TTS block (stays in cleaned text)
    def _replace_plain_block(m: re.Match) -> str:
        nonlocal has_directive
        has_directive = True
        visible = (m.group(1) or "").strip()
        if policy.allow_text and overrides.tts_text is None:
            overrides.tts_text = visible
        return visible
    cleaned = _replace_outside_code(cleaned, _PLAIN_BLOCK_RE, _replace_plain_block)

    # [[tts:key=value ...]] — key/value directive tags
    def _replace_directive(m: re.Match) -> str:
        nonlocal has_directive
        has_directive = True
        body = m.group(1) or ""
        tokens = body.split()
        declared_provider_id: str | None = None

        if policy.allow_provider:
            for token in tokens:
                eq = token.find("=")
                if eq == -1:
                    continue
                k = token[:eq].strip().lower()
                v = token[eq + 1:].strip()
                if k == "provider" and v:
                    declared_provider_id = v.lower()
                    overrides.provider = declared_provider_id

        for token in tokens:
            eq = token.find("=")
            if eq == -1:
                continue
            k = token[:eq].strip().lower()
            v = token[eq + 1:].strip()
            if not k or not v or k == "provider":
                continue
            target = declared_provider_id or overrides.provider or "default"
            if target not in overrides.provider_overrides:
                overrides.provider_overrides[target] = {}
            overrides.provider_overrides[target][k] = v

        return ""
    cleaned = _replace_outside_code(cleaned, _DIRECTIVE_RE, _replace_directive)

    # [[tts]] bare open tag
    def _replace_bare(m: re.Match) -> str:
        nonlocal has_directive
        has_directive = True
        return ""
    cleaned = _replace_outside_code(cleaned, _BARE_TAG_RE, _replace_bare)

    # [[/tts...]] closing tags
    cleaned = _replace_outside_code(cleaned, _CLOSING_TAG_RE, _replace_bare)

    return TtsDirectiveParseResult(
        cleaned_text=cleaned,
        tts_text=overrides.tts_text,
        has_directive=has_directive,
        overrides=overrides,
        warnings=warnings,
    )
