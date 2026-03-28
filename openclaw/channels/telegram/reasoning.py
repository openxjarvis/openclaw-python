"""Telegram two-lane reasoning stream support.

Extracts content inside <think>/<thinking>/<thought>/<antthinking> tags from the
agent's accumulated response text, routing it to a separate "reasoning" draft stream
while the cleaned answer goes to the main "answer" draft stream.

When `reasoningLevel` is:
  "off"    — reasoning blocks are stripped silently (default)
  "on"     — reasoning is stripped from the visible reply; not streamed live
  "stream" — reasoning gets its own live-updating draft bubble in the chat

Mirrors TypeScript:
  src/telegram/reasoning-lane-coordinator.ts  (splitTelegramReasoningText,
                                               TelegramReasoningStepState)
  src/telegram/lane-delivery.ts               (lane lifecycle)
  src/telegram/bot-message-dispatch.ts        (reasoningLevel resolution, wiring)
"""
from __future__ import annotations

import re
from typing import Any

_THINK_TAG_RE = re.compile(
    r"<\s*(?P<closing>/\s*)?(?:think(?:ing)?|thought|antthinking)\b[^<>]*>",
    re.IGNORECASE,
)

REASONING_MESSAGE_PREFIX = "Reasoning:\n"

REASONING_TAG_PREFIXES = [
    "<think",
    "<thinking",
    "<thought",
    "<antthinking",
    "</think",
    "</thinking",
    "</thought",
    "</antthinking",
]

# <final> / </final> tags (TS reasoning-tags.ts)
_FINAL_TAG_RE = re.compile(
    r"<\s*/?\s*final\s*/?\s*>",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Code-region detection (mirrors TS shared/text/code-regions.ts)
# ---------------------------------------------------------------------------

_FENCED_CODE_RE = re.compile(r"(?:^|\n)(```|~~~)[^\n]*\n[\s\S]*?(?:\n\1(?:\n|$)|$)")
_INLINE_CODE_RE = re.compile(r"`+[^`]+`+")


def _find_code_regions(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans of fenced and inline code blocks."""
    regions: list[tuple[int, int]] = []
    for m in _FENCED_CODE_RE.finditer(text):
        start = m.start()
        if text[start] == "\n":
            start += 1
        regions.append((start, start + len(m.group(0)) - (m.start() - start + m.start())))
        regions[-1] = (start, m.end())
    for m in _INLINE_CODE_RE.finditer(text):
        s, e = m.start(), m.end()
        inside_fenced = any(s >= rs and e <= re_ for rs, re_ in regions)
        if not inside_fenced:
            regions.append((s, e))
    return regions


def _is_inside_code(pos: int, regions: list[tuple[int, int]]) -> bool:
    return any(pos >= s and pos < e for s, e in regions)


# ---------------------------------------------------------------------------
# Partial tag detection (mirrors TS isPartialReasoningTagPrefix)
# ---------------------------------------------------------------------------

def _is_partial_reasoning_tag_prefix(text: str) -> bool:
    """True when text looks like an incomplete opening/closing think tag."""
    trimmed = text.lstrip().lower()
    if not trimmed.startswith("<"):
        return False
    if ">" in trimmed:
        return False
    return any(prefix.startswith(trimmed) for prefix in REASONING_TAG_PREFIXES)


# ---------------------------------------------------------------------------
# formatReasoningMessage (mirrors TS pi-embedded-utils.ts)
# ---------------------------------------------------------------------------

def format_reasoning_message(text: str) -> str:
    """Wrap reasoning text in italic markdown with a Reasoning: prefix.

    Each non-empty line is wrapped in _..._ (Telegram italic).
    """
    trimmed = text.strip()
    if not trimmed:
        return ""
    italic_lines = "\n".join(
        f"_{line}_" if line else line
        for line in trimmed.split("\n")
    )
    return f"{REASONING_MESSAGE_PREFIX}{italic_lines}"


# ---------------------------------------------------------------------------
# Tag stripping (mirrors TS stripReasoningTagsFromText with mode="strict")
# ---------------------------------------------------------------------------

def _strip_reasoning_tags_from_text(text: str) -> str:
    """Remove think-family tags and <final> tags outside code regions."""
    if not text:
        return text
    code_regions = _find_code_regions(text)

    # Strip <final>...</final> outside code
    result_chars: list[str] = list(text)
    for m in _FINAL_TAG_RE.finditer(text):
        if not _is_inside_code(m.start(), code_regions):
            for i in range(m.start(), m.end()):
                result_chars[i] = ""
    text_no_final = "".join(result_chars)

    # Strip think tags outside code
    code_regions2 = _find_code_regions(text_no_final)
    parts: list[str] = []
    pos = 0
    depth = 0
    for m in _THINK_TAG_RE.finditer(text_no_final):
        if _is_inside_code(m.start(), code_regions2):
            continue
        is_closing = bool(m.group("closing"))
        if not is_closing:
            if depth == 0:
                parts.append(text_no_final[pos:m.start()])
            depth += 1
        else:
            if depth > 0:
                depth -= 1
            if depth == 0:
                pos = m.end()
                continue
        pos = m.end()
    if depth == 0:
        parts.append(text_no_final[pos:])

    return "".join(parts).strip()


# ---------------------------------------------------------------------------
# Text splitting
# ---------------------------------------------------------------------------

def split_telegram_reasoning_text(text: str) -> tuple[str | None, str | None]:
    """Split accumulated text into (reasoning_text, answer_text).

    Returns:
        (None, None)     — text is a partial tag prefix; no split yet
        (reasoning, None) — entire text is reasoning (Reasoning:\\n prefix)
        (reasoning, answer) — tagged reasoning extracted
        (None, answer)   — no reasoning found

    Mirrors TS splitTelegramReasoningText() in reasoning-lane-coordinator.ts.
    """
    if not text:
        return None, None

    trimmed = text.strip()

    # Partial tag prefix → return nothing (wait for more data)
    if _is_partial_reasoning_tag_prefix(trimmed):
        return None, None

    # "Reasoning:\n..." prefix → entire text is already-formatted reasoning
    if (
        trimmed.startswith(REASONING_MESSAGE_PREFIX)
        and len(trimmed) > len(REASONING_MESSAGE_PREFIX)
    ):
        return trimmed, None

    # Extract thinking from tags, skipping tags inside code regions
    code_regions = _find_code_regions(text)
    reasoning_parts: list[str] = []
    in_thinking = False
    last_index = 0
    for m in _THINK_TAG_RE.finditer(text):
        idx = m.start()
        if _is_inside_code(idx, code_regions):
            continue
        if in_thinking:
            reasoning_parts.append(text[last_index:idx])
        is_close = bool(m.group("closing"))
        in_thinking = not is_close
        last_index = m.end()
    if in_thinking:
        reasoning_parts.append(text[last_index:])
    tagged_reasoning = "".join(reasoning_parts).strip()

    stripped_answer = _strip_reasoning_tags_from_text(text)

    if not tagged_reasoning and stripped_answer == text:
        return None, text

    reasoning_text = format_reasoning_message(tagged_reasoning) if tagged_reasoning else None
    answer_text = stripped_answer or None
    return reasoning_text, answer_text


def strip_reasoning_from_text(text: str) -> str:
    """Return only the answer portion (strips all reasoning blocks and tags).

    Convenience wrapper used for final delivery when reasoningLevel != "stream".
    """
    _, answer = split_telegram_reasoning_text(text)
    return answer or ""


# ---------------------------------------------------------------------------
# Reasoning level config resolution
# ---------------------------------------------------------------------------

def resolve_reasoning_level(channel_config: dict) -> str:
    """Read reasoningLevel from the channel config dict.

    Returns "off" | "on" | "stream".
    Mirrors TS resolveTelegramReasoningLevel().
    """
    messages_cfg = channel_config.get("messages", {}) or {}
    level = (
        messages_cfg.get("reasoningLevel")
        or messages_cfg.get("reasoning_level")
        or channel_config.get("reasoningLevel")
        or channel_config.get("reasoning_level")
        or "off"
    )
    if level in ("on", "stream"):
        return level
    return "off"


# ---------------------------------------------------------------------------
# ReasoningStepState — tracks delivery state across a multi-turn agent run
# ---------------------------------------------------------------------------

class TelegramReasoningStepState:
    """Tracks whether a reasoning block has been seen and delivered.

    States: none -> hinted -> delivered

    Used to buffer the final answer text until the reasoning message is
    committed, preventing the answer from arriving before the reasoning bubble.

    Mirrors TS createTelegramReasoningStepState().
    """

    def __init__(self) -> None:
        self._state: str = "none"  # "none" | "hinted" | "delivered"
        self._buffered_answer: str | None = None

    @property
    def state(self) -> str:
        return self._state

    def on_reasoning_seen(self) -> None:
        """Called when reasoning content is first detected in the stream."""
        if self._state == "none":
            self._state = "hinted"

    def on_reasoning_delivered(self) -> None:
        """Called after the reasoning draft is sent/updated."""
        self._state = "delivered"

    def should_buffer_final_answer(self) -> bool:
        """True when reasoning was hinted but not yet delivered AND no buffer exists."""
        return self._state == "hinted" and self._buffered_answer is None

    def buffer_final_answer(self, text: str) -> None:
        self._buffered_answer = text

    def take_buffered_answer(self) -> str | None:
        ans = self._buffered_answer
        self._buffered_answer = None
        return ans

    def reset_for_next_step(self) -> None:
        """Clear state between agent tool-call turns."""
        self._state = "none"
        self._buffered_answer = None


__all__ = [
    "split_telegram_reasoning_text",
    "strip_reasoning_from_text",
    "format_reasoning_message",
    "resolve_reasoning_level",
    "TelegramReasoningStepState",
    "REASONING_MESSAGE_PREFIX",
]
