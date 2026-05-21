"""OpenAI reasoning effort resolution — mirrors src/agents/openai-reasoning-effort.ts."""
from __future__ import annotations

import re
from typing import Any, Literal, TypedDict

OpenAIReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
OpenAIApiReasoningEffort = OpenAIReasoningEffort

ALL_OPENAI_REASONING_EFFORTS: tuple[OpenAIApiReasoningEffort, ...] = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
)

GPT_5_REASONING_EFFORTS: tuple[OpenAIApiReasoningEffort, ...] = ("minimal", "low", "medium", "high")
GPT_51_REASONING_EFFORTS: tuple[OpenAIApiReasoningEffort, ...] = ("none", "low", "medium", "high")
GPT_52_REASONING_EFFORTS: tuple[OpenAIApiReasoningEffort, ...] = (
    "none",
    "low",
    "medium",
    "high",
    "xhigh",
)
GPT_CODEX_REASONING_EFFORTS: tuple[OpenAIApiReasoningEffort, ...] = ("low", "medium", "high", "xhigh")
GPT_PRO_REASONING_EFFORTS: tuple[OpenAIApiReasoningEffort, ...] = ("medium", "high", "xhigh")
GPT_5_PRO_REASONING_EFFORTS: tuple[OpenAIApiReasoningEffort, ...] = ("high",)
GPT_51_CODEX_MAX_REASONING_EFFORTS: tuple[OpenAIApiReasoningEffort, ...] = (
    "none",
    "medium",
    "high",
    "xhigh",
)
GPT_51_CODEX_MINI_REASONING_EFFORTS: tuple[OpenAIApiReasoningEffort, ...] = ("medium",)
GENERIC_REASONING_EFFORTS: tuple[OpenAIApiReasoningEffort, ...] = ("low", "medium", "high")

_DATE_SUFFIX_RE = re.compile(r"-\d{4}-\d{2}-\d{2}$")


class OpenAIReasoningModel(TypedDict, total=False):
    provider: Any
    id: Any
    api: Any
    baseUrl: Any
    compat: Any


def _normalize_lowercase_string_or_empty(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    trimmed = value.strip()
    return trimmed.lower() if trimmed else ""


def normalize_model_id(model_id: str | None) -> str:
    normalized = _normalize_lowercase_string_or_empty(model_id or "")
    return _DATE_SUFFIX_RE.sub("", normalized)


def normalize_openai_reasoning_effort(effort: str) -> str:
    return "minimal" if effort == "minimal" else effort


def _read_compat_reasoning_efforts(compat: Any) -> list[OpenAIApiReasoningEffort] | None:
    if not isinstance(compat, dict):
        return None
    raw = compat.get("supportedReasoningEfforts")
    if not isinstance(raw, list):
        return None
    supported = [
        value
        for value in raw
        if isinstance(value, str) and value in ALL_OPENAI_REASONING_EFFORTS
    ]
    return supported or None


def resolve_openai_supported_reasoning_efforts(
    model: OpenAIReasoningModel,
) -> tuple[OpenAIApiReasoningEffort, ...]:
    compat_efforts = _read_compat_reasoning_efforts(model.get("compat"))
    if compat_efforts:
        return tuple(compat_efforts)

    provider = _normalize_lowercase_string_or_empty(
        model.get("provider") if isinstance(model.get("provider"), str) else ""
    )
    model_id = normalize_model_id(model.get("id") if isinstance(model.get("id"), str) else None)

    if model_id == "gpt-5.1-codex-mini":
        return GPT_51_CODEX_MINI_REASONING_EFFORTS
    if model_id == "gpt-5.1-codex-max":
        return GPT_51_CODEX_MAX_REASONING_EFFORTS
    if re.match(r"^gpt-5(?:\.\d+)?-codex(?:-|$)", model_id) or provider == "openai-codex":
        return GPT_CODEX_REASONING_EFFORTS
    if model_id == "gpt-5-pro":
        return GPT_5_PRO_REASONING_EFFORTS
    if re.match(r"^gpt-5\.[2-9](?:\.\d+)?-pro(?:-|$)", model_id):
        return GPT_PRO_REASONING_EFFORTS
    if re.match(r"^gpt-5\.[2-9](?:\.\d+)?(?:-|$)", model_id):
        return GPT_52_REASONING_EFFORTS
    if re.match(r"^gpt-5\.1(?:-|$)", model_id):
        return GPT_51_REASONING_EFFORTS
    if re.match(r"^gpt-5(?:-|$)", model_id):
        return GPT_5_REASONING_EFFORTS
    return GENERIC_REASONING_EFFORTS


def supports_openai_reasoning_effort(model: OpenAIReasoningModel, effort: str) -> bool:
    normalized = normalize_openai_reasoning_effort(effort)
    return normalized in resolve_openai_supported_reasoning_efforts(model)


def resolve_openai_reasoning_effort_for_model(
    *,
    model: OpenAIReasoningModel,
    effort: str,
    fallback_map: dict[str, str] | None = None,
) -> OpenAIApiReasoningEffort | None:
    requested = normalize_openai_reasoning_effort(effort)
    mapped = (fallback_map or {}).get(requested, requested)
    normalized = normalize_openai_reasoning_effort(mapped)
    supported = resolve_openai_supported_reasoning_efforts(model)
    if normalized in supported:
        return normalized  # type: ignore[return-value]
    if requested == "none":
        return None
    if requested == "minimal" and "low" in supported:
        return "low"
    if requested in ("minimal", "low") and "medium" in supported:
        return "medium"
    if requested == "xhigh" and "high" in supported:
        return "high"
    for value in supported:
        if value != "none":
            return value
    return None


__all__ = [
    "ALL_OPENAI_REASONING_EFFORTS",
    "GENERIC_REASONING_EFFORTS",
    "GPT_51_CODEX_MAX_REASONING_EFFORTS",
    "GPT_51_CODEX_MINI_REASONING_EFFORTS",
    "GPT_51_REASONING_EFFORTS",
    "GPT_52_REASONING_EFFORTS",
    "GPT_5_PRO_REASONING_EFFORTS",
    "GPT_5_REASONING_EFFORTS",
    "GPT_CODEX_REASONING_EFFORTS",
    "GPT_PRO_REASONING_EFFORTS",
    "OpenAIApiReasoningEffort",
    "OpenAIReasoningEffort",
    "OpenAIReasoningModel",
    "normalize_model_id",
    "normalize_openai_reasoning_effort",
    "resolve_openai_reasoning_effort_for_model",
    "resolve_openai_supported_reasoning_efforts",
    "supports_openai_reasoning_effort",
]
