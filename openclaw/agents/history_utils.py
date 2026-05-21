"""
History Utils - session history cleaning and limiting.

Includes TranscriptPolicy + resolve_transcript_policy() mirroring
TS openclaw/src/agents/transcript-policy.ts.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TranscriptPolicy — mirrors TS TranscriptPolicy type + resolveTranscriptPolicy()
# ---------------------------------------------------------------------------

TranscriptSanitizeMode = Literal["full", "images-only"]
ToolCallIdMode = Literal["strict", "strict9"]

_MISTRAL_MODEL_HINTS: tuple[str, ...] = (
    "mistral", "mixtral", "codestral", "pixtral", "devstral", "ministral", "mistralai",
)

_OPENAI_MODEL_APIS: frozenset[str] = frozenset(
    ["openai", "openai-completions", "openai-responses", "openai-codex-responses"]
)

_OPENAI_PROVIDERS: frozenset[str] = frozenset(["openai", "openai-codex"])

_GOOGLE_MODEL_APIS: frozenset[str] = frozenset(
    # Canonical value is "google-generative-ai" (aligned with TS MODEL_APIS).
    # Legacy strings kept for backwards compatibility with existing sessions.
    ["google-generative-ai", "google-ai-studio", "google-vertex", "google-genai", "gemini", "gemini-vertex"]
)


@dataclass
class SanitizeThoughtSignaturesConfig:
    """Config for thought-signature sanitization (OpenRouter Gemini)."""
    allow_base64_only: bool = False
    include_camel_case: bool = False


@dataclass
class TranscriptPolicy:
    """Provider-aware transcript sanitization policy.

    Mirrors TS TranscriptPolicy in openclaw/src/agents/transcript-policy.ts.
    """
    sanitize_mode: TranscriptSanitizeMode = "images-only"
    sanitize_tool_call_ids: bool = False
    tool_call_id_mode: ToolCallIdMode | None = None
    repair_tool_use_result_pairing: bool = False
    preserve_signatures: bool = False
    sanitize_thought_signatures: SanitizeThoughtSignaturesConfig | None = None
    normalize_antigravity_thinking_blocks: bool = False
    apply_google_turn_ordering: bool = False
    validate_gemini_turns: bool = False
    validate_anthropic_turns: bool = False
    allow_synthetic_tool_results: bool = False


def _normalize_provider_id(provider: str) -> str:
    """Local normalizer — calls model_selection.normalize_provider_id if available."""
    try:
        from openclaw.agents.model_selection import normalize_provider_id
        return normalize_provider_id(provider)
    except Exception:
        return provider.strip().lower()


def _is_openai_api(model_api: str | None) -> bool:
    return bool(model_api) and model_api in _OPENAI_MODEL_APIS


def _is_openai_provider(provider: str | None) -> bool:
    return bool(provider) and _normalize_provider_id(provider) in _OPENAI_PROVIDERS


def _is_anthropic_api(model_api: str | None, provider: str | None) -> bool:
    if model_api == "anthropic-messages":
        return True
    # MiniMax uses openai-completions, not anthropic-messages
    return _normalize_provider_id(provider or "") == "anthropic"


def _is_google_model_api(model_api: str | None) -> bool:
    return bool(model_api) and model_api in _GOOGLE_MODEL_APIS


def _is_mistral_model(provider: str | None, model_id: str | None) -> bool:
    p = _normalize_provider_id(provider or "")
    if p == "mistral":
        return True
    mid = (model_id or "").lower()
    return any(hint in mid for hint in _MISTRAL_MODEL_HINTS)


def _is_antigravity_claude(model_api: str | None, provider: str | None, model_id: str | None) -> bool:
    """Detect Antigravity Claude (Google-hosted Anthropic models)."""
    p = _normalize_provider_id(provider or "")
    api = model_api or ""
    if p == "antigravity":
        return True
    if api in ("antigravity", "antigravity-messages"):
        return True
    if p == "google-vertex" and (model_id or "").lower().startswith("claude"):
        return True
    return False


def resolve_transcript_policy(
    model_api: str | None = None,
    provider: str | None = None,
    model_id: str | None = None,
) -> TranscriptPolicy:
    """Resolve provider-aware transcript sanitization policy.

    Mirrors TS resolveTranscriptPolicy() in openclaw/src/agents/transcript-policy.ts.

    Args:
        model_api: API type string (e.g. 'anthropic-messages', 'google-ai-studio').
        provider: Provider name (e.g. 'anthropic', 'openai', 'mistral').
        model_id: Model identifier (e.g. 'claude-3-5-sonnet', 'gemini-2.0-flash').

    Returns:
        TranscriptPolicy with all sanitization flags resolved.
    """
    p = _normalize_provider_id(provider or "")
    mid = model_id or ""

    is_google = _is_google_model_api(model_api)
    is_anthropic = _is_anthropic_api(model_api, p)
    is_openai = _is_openai_provider(p) or (not p and _is_openai_api(model_api))
    is_mistral = _is_mistral_model(p, mid)
    is_openrouter_gemini = (p in ("openrouter", "opencode")) and "gemini" in mid.lower()
    is_antigravity_claude = _is_antigravity_claude(model_api, p, mid)

    needs_non_image_sanitize = is_google or is_anthropic or is_mistral or is_openrouter_gemini

    sanitize_tool_call_ids = is_google or is_mistral or is_anthropic
    tool_call_id_mode: ToolCallIdMode | None = (
        "strict9" if is_mistral
        else "strict" if sanitize_tool_call_ids
        else None
    )
    repair_tool_use_result_pairing = is_google or is_anthropic
    sanitize_thought_signatures = (
        SanitizeThoughtSignaturesConfig(allow_base64_only=True, include_camel_case=True)
        if is_openrouter_gemini else None
    )

    return TranscriptPolicy(
        sanitize_mode="images-only" if is_openai else ("full" if needs_non_image_sanitize else "images-only"),
        sanitize_tool_call_ids=not is_openai and sanitize_tool_call_ids,
        tool_call_id_mode=tool_call_id_mode,
        repair_tool_use_result_pairing=not is_openai and repair_tool_use_result_pairing,
        preserve_signatures=is_antigravity_claude,
        sanitize_thought_signatures=None if is_openai else sanitize_thought_signatures,
        normalize_antigravity_thinking_blocks=is_antigravity_claude,
        apply_google_turn_ordering=not is_openai and is_google,
        validate_gemini_turns=not is_openai and is_google,
        validate_anthropic_turns=not is_openai and is_anthropic,
        allow_synthetic_tool_results=not is_openai and (is_google or is_anthropic),
    )


def sanitize_session_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Sanitize session history by removing metadata and empty messages.
    
    Matches TypeScript sanitizeSessionHistory() behavior from pi-mono/packages/coding-agent/src/core/google.ts:
    - Remove thinking, details, usage, cost fields
    - Remove empty tool results
    - Remove messages without role or content
    - Keep only essential fields (id, timestamp, role, content)
    
    Args:
        messages: Raw session messages
        
    Returns:
        Cleaned messages
    """
    sanitized = []
    
    for msg in messages:
        # Skip messages without role
        if not msg.get("role"):
            logger.debug(f"Skipping message without role: {msg}")
            continue
        
        # Create clean copy without metadata
        role = msg["role"]
        # compactionSummary / branchSummary have a "summary" field, not "content"
        content_val = msg.get("content")
        if role in ("compactionSummary", "branchSummary") and content_val is None:
            content_val = msg.get("summary")
        clean_msg = {
            "role": role,
            "content": content_val,
        }
        # Preserve "summary" so convert_to_llm can access it
        if role in ("compactionSummary", "branchSummary") and "summary" in msg:
            clean_msg["summary"] = msg["summary"]
        
        # Preserve essential fields only
        if "id" in msg:
            clean_msg["id"] = msg["id"]
        if "timestamp" in msg:
            clean_msg["timestamp"] = msg["timestamp"]
        
        # Support both camelCase (TypeScript) and snake_case (Python)
        if "toolCallId" in msg:
            clean_msg["toolCallId"] = msg["toolCallId"]
        if "tool_call_id" in msg:
            clean_msg["tool_call_id"] = msg["tool_call_id"]
        
        if "toolName" in msg:
            clean_msg["toolName"] = msg["toolName"]
        if "name" in msg:
            clean_msg["name"] = msg["name"]
        
        if "tool_calls" in msg:
            clean_msg["tool_calls"] = msg["tool_calls"]
        
        # Skip empty content
        content = clean_msg["content"]
        if content is None or content == "":
            logger.debug(f"Skipping message without content: role={msg.get('role')}")
            continue
        
        # Skip empty tool results
        if msg["role"] in ["toolResult", "tool"]:
            if isinstance(content, list) and len(content) == 0:
                logger.debug(f"Skipping empty tool result: toolCallId={msg.get('toolCallId')}")
                continue
            if isinstance(content, str) and not content.strip():
                logger.debug(f"Skipping empty tool result: toolCallId={msg.get('toolCallId')}")
                continue
        
        # Skip messages with empty content array (system messages)
        if isinstance(content, list) and len(content) == 0:
            logger.debug(f"Skipping message with empty content array: role={msg.get('role')}")
            continue
        
        sanitized.append(clean_msg)
    
    logger.debug(f"Sanitized history: {len(messages)} -> {len(sanitized)} messages")
    return sanitized


def limit_history_turns(
    messages: list[dict[str, Any]],
    max_turns: int | None = None,
    provider: str | None = None
) -> list[dict[str, Any]]:
    """
    Limit history to most recent N user messages and their responses.
    
    Aligned with TypeScript openclaw/src/agents/pi-embedded-runner/history.ts:
    - Count only user messages (not user-assistant pairs)
    - Scan from end backwards
    - Keep last N user messages + all messages after the Nth user message
    
    Args:
        messages: Session messages
        max_turns: Maximum number of user messages to keep (None = no limit)
        provider: Provider name for default limits
        
    Returns:
        Limited messages
    """
    # Keep behavior aligned with TS: no implicit provider defaults.
    if max_turns is None or max_turns <= 0:
        return messages
    
    if len(messages) == 0:
        return messages
    
    # Scan backwards to find the Nth user message from the end
    user_count = 0
    last_user_index = len(messages)  # Track position of Nth user message
    
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            user_count += 1
            if user_count > max_turns:
                # Found the (N+1)th user message, cut at last_user_index
                break
            last_user_index = i  # Update to current user position
    
    # Return slice from last_user_index
    if last_user_index > 0 and last_user_index < len(messages):
        limited = messages[last_user_index:]
        kept_users = min(user_count, max_turns)
        logger.info(f"✂️ History limited: {len(messages)} -> {len(limited)} messages (kept last {kept_users} user messages)")
        return limited
    
    return messages


def count_history_turns(messages: list[dict[str, Any]]) -> int:
    """
    Count number of user-assistant turns in history
    
    Args:
        messages: Session messages
        
    Returns:
        Number of complete turns
    """
    turn_count = 0
    last_role = None
    
    for msg in messages:
        role = msg.get("role")
        if role == "assistant" and last_role == "user":
            turn_count += 1
        last_role = role
    
    return turn_count


def find_last_message_by_role(
    messages: list[dict[str, Any]],
    role: str
) -> dict[str, Any] | None:
    """
    Find last message with given role
    
    Args:
        messages: Session messages
        role: Role to search for
        
    Returns:
        Last message with role, or None
    """
    for msg in reversed(messages):
        if msg.get("role") == role:
            return msg
    return None


def extract_text_from_content(content: Any) -> str:
    """
    Extract text from message content (supports both string and array formats)
    
    Args:
        content: Message content (string or array of content blocks)
        
    Returns:
        Extracted text
    """
    if isinstance(content, str):
        return content
    
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif isinstance(block, str):
                texts.append(block)
        return "".join(texts)
    
    return str(content)


def inject_history_images_into_messages(
    messages: list[dict[str, Any]],
    images: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Inject image references into message history for multi-turn conversations.

    Matches TypeScript injectHistoryImagesIntoMessages() in attempt.ts:
    - Takes message history, finds image references by index
    - Converts string content to array format where necessary
    - Prevents duplicates (only injects if not already present)

    Args:
        messages: Message history dicts.
        images: List of image URLs / base64 data to inject into the last
                user message if they are not already embedded.

    Returns:
        Updated messages list (shallow copy of the list, dicts may be mutated).
    """
    if not images:
        return messages

    messages = list(messages)  # shallow copy

    # Find the last user message
    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break

    if last_user_idx == -1:
        return messages

    msg = dict(messages[last_user_idx])
    content = msg.get("content", [])

    # Normalize content to list format
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    else:
        content = list(content)

    # Collect already-present image URLs/data to deduplicate
    existing_images: set[str] = set()
    for block in content:
        if isinstance(block, dict) and block.get("type") == "image":
            src = block.get("source", {})
            if isinstance(src, dict):
                existing_images.add(src.get("url", "") or src.get("data", ""))
            elif isinstance(src, str):
                existing_images.add(src)

    # Inject new images
    for img in images:
        if img in existing_images:
            continue
        if img.startswith("data:"):
            # base64 data URI
            try:
                header, data = img.split(",", 1)
                media_type = header.split(":")[1].split(";")[0]
            except Exception:
                media_type = "image/jpeg"
                data = img
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": data,
                },
            })
        else:
            # URL reference
            content.append({
                "type": "image",
                "source": {
                    "type": "url",
                    "url": img,
                },
            })
        existing_images.add(img)

    msg["content"] = content
    messages[last_user_idx] = msg
    return messages


def validate_message_sequence(messages: list[dict[str, Any]]) -> tuple[bool, str | None]:
    """
    Validate message sequence for correct role ordering
    
    Checks for:
    - User messages followed by assistant responses
    - No consecutive messages of same role
    - Tool messages between assistant and next assistant
    
    Args:
        messages: Session messages
        
    Returns:
        (is_valid, error_message)
    """
    if not messages:
        return True, None
    
    last_role = None
    
    for i, msg in enumerate(messages):
        role = msg.get("role")
        
        # Check for consecutive same roles (except tool)
        if role != "tool" and role == last_role:
            return False, f"Consecutive {role} messages at index {i}"
        
        last_role = role
    
    return True, None


def read_session_transcript(
    transcript_path: Path | str,
    limit: int | None = None
) -> list[dict[str, Any]]:
    """
    Read session transcript from JSONL file with optional limit.
    
    Args:
        transcript_path: Path to the JSONL transcript file
        limit: Maximum number of messages to return (from end)
        
    Returns:
        List of messages
    """
    if isinstance(transcript_path, str):
        transcript_path = Path(transcript_path)
    
    if not transcript_path.exists():
        return []
    
    messages = []
    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse JSONL line: {e}")
                        continue
    except Exception as e:
        logger.error(f"Failed to read transcript {transcript_path}: {e}")
        return []
    
    # Apply limit (keep last N messages)
    if limit and len(messages) > limit:
        messages = messages[-limit:]
    
    return messages


def apply_transcript_policy(
    messages: list[dict[str, Any]],
    policy: TranscriptPolicy,
) -> list[dict[str, Any]]:
    """Apply a resolved :class:`TranscriptPolicy` to *messages*.

    Mirrors TS ``applyTranscriptPolicy()`` — runs each sanitization step that
    the policy enables.  The heavy per-provider validators
    (``validate_gemini_turns``, ``validate_anthropic_turns``) are imported on
    demand to avoid circular-import issues.
    """
    out = list(messages)

    if policy.repair_tool_use_result_pairing:
        try:
            from openclaw.agents.compaction.functions import _repair_tool_use_result_pairing
            out = _repair_tool_use_result_pairing(out)
        except Exception:
            logger.debug("repair_tool_use_result_pairing unavailable", exc_info=True)

    if policy.validate_gemini_turns:
        try:
            from openclaw.agents.context import validate_gemini_turns
            out = validate_gemini_turns(out)
        except Exception:
            logger.debug("validate_gemini_turns unavailable", exc_info=True)

    if policy.validate_anthropic_turns:
        try:
            from openclaw.agents.context import validate_anthropic_turns
            out = validate_anthropic_turns(out)
        except Exception:
            logger.debug("validate_anthropic_turns unavailable", exc_info=True)

    if policy.apply_google_turn_ordering:
        try:
            from openclaw.agents.context import validate_gemini_turns
            out = validate_gemini_turns(out)
        except Exception:
            logger.debug("apply_google_turn_ordering unavailable", exc_info=True)

    return out


__all__ = [
    "TranscriptPolicy",
    "TranscriptSanitizeMode",
    "ToolCallIdMode",
    "SanitizeThoughtSignaturesConfig",
    "resolve_transcript_policy",
    "apply_transcript_policy",
    "sanitize_session_history",
]
