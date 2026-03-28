"""pi_ai stream_simple adapter for openclaw-python.

Thin adapter that maps openclaw session/tool representation to
pi_ai.stream_simple, replacing the legacy MultiProviderRuntime + providers/.

Architecture::

    openclaw AgentSession
         ↓  calls
    PiStreamAdapter.stream_turn(messages, tools, opts)
         ↓  calls
    pi_ai.stream_simple(model, context, opts)
         ↓  yields
    pi_ai AssistantMessageEvent hierarchy
         ↓  converted by
    PiStreamAdapter → openclaw Events

Mirrors how attempt.ts calls streamSimple() from @pi-ai.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, AsyncIterator

from pi_ai import get_model, stream_simple
from pi_ai.types import (
    AssistantMessage,
    Context,
    EventDone,
    EventError,
    EventTextDelta,
    EventToolCallEnd,
    EventThinkingDelta,
    Model,
    SimpleStreamOptions,
    TextContent,
    Tool,
    ToolCall,
    UserMessage,
)

from openclaw.events import Event, EventType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider configuration for forward-compatibility
# ---------------------------------------------------------------------------

# OpenAI-compatible providers that can be dynamically created
# Mirrors TypeScript resolveForwardCompatModel logic
OPENAI_COMPATIBLE_PROVIDERS = {
    "moonshot", "kimi-coding", "kimi",
    "deepseek", "groq", "mistral", "xai",
    "together", "openrouter", "huggingface",
    "cerebras", "zai", "zhipu",
    "minimax", "minimax-cn", "qwen",
    "xiaomi", "volcengine", "byteplus", "synthetic",
}

# Provider base URLs (aligned with runtime.py and TypeScript)
PROVIDER_BASE_URLS = {
    "moonshot": "https://api.moonshot.ai/v1",
    "kimi-coding": "https://api.kimi.com/coding/",  # ✅ With trailing slash (per TS)
    "kimi": "https://api.kimi.com/coding/",           # ✅ With trailing slash
    "deepseek": "https://api.deepseek.com",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "xai": "https://api.x.ai/v1",
    "together": "https://api.together.xyz/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "huggingface": "https://api-inference.huggingface.co/models",
    "cerebras": "https://api.cerebras.ai/v1",
    "zai": "https://api.z.ai/api/coding/paas/v4",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "minimax": "https://api.minimax.io/anthropic",
    "minimax-cn": "https://api.minimaxi.com/anthropic",
    "qwen": "https://portal.qwen.ai/v1",
    "xiaomi": "https://api.xiaomimimo.com/anthropic",
    "volcengine": "https://ark.cn-beijing.volces.com/api/v3",
    "byteplus": "https://ark-us-east-1.bytepluses.com/api/v3",
    "synthetic": "https://api.synthetic.ai/v1",
}

# Provider API key environment variables
PROVIDER_API_KEY_ENV_VARS = {
    "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "moonshot": ["MOONSHOT_API_KEY", "KIMI_CODE_API_KEY"],
    "kimi-coding": ["KIMI_API_KEY", "KIMI_CODE_API_KEY"],
    "kimi": ["KIMI_API_KEY", "KIMI_CODE_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "mistral": ["MISTRAL_API_KEY"],
    "xai": ["XAI_API_KEY"],
    "together": ["TOGETHER_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "huggingface": ["HUGGINGFACE_API_KEY", "HF_API_KEY", "HF_TOKEN"],
    "cerebras": ["CEREBRAS_API_KEY"],
    "zai": ["ZAI_API_KEY", "ZHIPU_API_KEY"],
    "zhipu": ["ZHIPU_API_KEY", "ZAI_API_KEY"],
    "minimax": ["MINIMAX_API_KEY"],
    "minimax-cn": ["MINIMAX_CN_API_KEY"],
    "qwen": ["DASHSCOPE_API_KEY", "QWEN_API_KEY"],
    "xiaomi": ["XIAOMI_API_KEY"],
    "volcengine": ["VOLCANO_ENGINE_API_KEY", "VOLCENGINE_API_KEY"],
    "byteplus": ["BYTEPLUS_API_KEY"],
    "synthetic": ["SYNTHETIC_API_KEY"],
    "ollama": ["OLLAMA_API_KEY"],
}


def _has_api_key(provider: str) -> bool:
    """Check if an API key exists for the given provider.
    
    Checks in order (mirrors TS behavior):
    1. Environment variables
    2. openclaw.json models.providers[provider].apiKey
    
    This ensures subagents can use the same providers as parent agents.
    """
    # 1. Check environment variables
    env_vars = PROVIDER_API_KEY_ENV_VARS.get(provider, [f"{provider.upper()}_API_KEY"])
    if any(os.getenv(var) for var in env_vars):
        return True
    
    # 2. Check openclaw.json config (mirrors TS config.models?.providers?.[provider]?.apiKey)
    try:
        from openclaw.config.loader import load_config
        cfg = load_config()
        
        # Access models.providers (support both dict and Pydantic object)
        if hasattr(cfg, 'models') and cfg.models:
            models_cfg = cfg.models
            if hasattr(models_cfg, 'providers') and models_cfg.providers:
                providers = models_cfg.providers
                # Handle both dict and object access
                if isinstance(providers, dict):
                    provider_config = providers.get(provider)
                    if isinstance(provider_config, dict):
                        api_key = provider_config.get('apiKey')
                        if api_key and isinstance(api_key, str) and api_key.strip():
                            return True
                elif hasattr(providers, provider):
                    provider_config = getattr(providers, provider)
                    if hasattr(provider_config, 'apiKey'):
                        api_key = provider_config.apiKey
                        if api_key and isinstance(api_key, str) and api_key.strip():
                            return True
    except Exception as e:
        # Don't fail if config loading fails; just fallback to env vars only
        logger.debug(f"Failed to check API key in config for {provider}: {e}")
    
    return False


def _get_api_key(provider: str) -> str | None:
    """Get API key for the given provider.
    
    Checks in order (mirrors TS getApiKey behavior):
    1. Environment variables
    2. openclaw.json models.providers[provider].apiKey
    
    Returns:
        API key string or None if not found
    """
    # 1. Check environment variables
    env_vars = PROVIDER_API_KEY_ENV_VARS.get(provider, [f"{provider.upper()}_API_KEY"])
    for var in env_vars:
        key = os.getenv(var)
        if key:
            return key
    
    # 2. Check openclaw.json config
    try:
        from openclaw.config.loader import load_config
        cfg = load_config()
        
        if hasattr(cfg, 'models') and cfg.models:
            models_cfg = cfg.models
            if hasattr(models_cfg, 'providers') and models_cfg.providers:
                providers = models_cfg.providers
                # Handle both dict and object access
                if isinstance(providers, dict):
                    provider_config = providers.get(provider)
                    if isinstance(provider_config, dict):
                        api_key = provider_config.get('apiKey')
                        if api_key and isinstance(api_key, str):
                            return api_key.strip() or None
                elif hasattr(providers, provider):
                    provider_config = getattr(providers, provider)
                    if hasattr(provider_config, 'apiKey'):
                        api_key = provider_config.apiKey
                        if api_key and isinstance(api_key, str):
                            return api_key.strip() or None
    except Exception as e:
        logger.debug(f"Failed to get API key from config for {provider}: {e}")
    
    return None


def _create_forward_compat_model(provider: str, model_id: str) -> Model:
    """Create a forward-compatible Model for OpenAI-compatible providers.
    
    Mirrors TypeScript resolveForwardCompatModel in pi-embedded-runner/model.ts.
    This allows using providers/models not in the static registry.
    """
    base_url = PROVIDER_BASE_URLS.get(provider)
    if not base_url:
        raise ValueError(f"Unknown provider {provider!r} for forward-compat model creation")
    
    # Create a minimal Model object that pi_ai can use
    # The actual API call will use the base_url and provider's API key
    return Model(
        provider=provider,
        id=model_id,
        name=f"{provider}/{model_id}",
        input=["text"],  # Assume text input; models can override
        api="openai-completions",  # OpenAI-compatible
        base_url=base_url,  # Required by pi_ai.Model
    )


# Fallback models ordered by preference (only if they have API keys)
_FALLBACK_MODEL_PAIRS = [
    ("google", "gemini-2.0-flash"),
    ("anthropic", "claude-3-5-sonnet-20241022"),
    ("openai", "gpt-4o"),
]


def _resolve_model(model_str: str) -> Model:
    """Resolve a 'provider/model-id' string to a pi_ai Model object.

    Resolution order:
    1. Try to get from pi_ai registry (built-in models)
    2. For OpenAI-compatible providers, create forward-compat model
    3. Fall back to available models with API keys
    
    Mirrors TypeScript model resolution in openclaw/src/agents/pi-embedded-runner/model.ts
    """
    if "/" in model_str:
        provider, model_id = model_str.split("/", 1)
    else:
        # Bare model name — guess provider from prefix
        lower = model_str.lower()
        if "gemini" in lower or "google" in lower:
            provider, model_id = "google", model_str
        elif "claude" in lower or "haiku" in lower or "sonnet" in lower or "opus" in lower:
            provider, model_id = "anthropic", model_str
        elif "kimi" in lower or "moonshot" in lower:
            provider, model_id = "moonshot", model_str
        else:
            provider, model_id = "openai", model_str

    # Normalize provider ID (mirrors model_selection.normalize_provider_id)
    provider_normalized = provider.lower().replace("_", "-")
    if provider_normalized == "google-gemini":
        provider_normalized = "google"
    elif provider_normalized in ("z.ai", "zhipu-ai"):
        provider_normalized = "zai"

    # 1. Try to get from pi_ai registry
    try:
        model = get_model(provider_normalized, model_id)
        if model is not None:
            return model
    except KeyError:
        pass

    # 2. For OpenAI-compatible providers, create forward-compat model
    if provider_normalized in OPENAI_COMPATIBLE_PROVIDERS:
        try:
            logger.info(
                f"Creating forward-compat model for {provider_normalized}/{model_id}"
            )
            model = _create_forward_compat_model(provider_normalized, model_id)
            # Warn if no API key found, but still return the model
            # (key might be provided later via auth-profiles.json)
            if not _has_api_key(provider_normalized):
                logger.warning(
                    f"Model {model_str!r} created but no API key found for provider {provider_normalized!r}. "
                    f"Set {', '.join(PROVIDER_API_KEY_ENV_VARS.get(provider_normalized, []))} in environment."
                )
            return model
        except Exception as e:
            logger.warning(f"Failed to create forward-compat model: {e}")

    # 3. Try fallbacks (only if they have API keys)
    for fp, fid in _FALLBACK_MODEL_PAIRS:
        if not _has_api_key(fp):
            continue
        try:
            logger.warning(
                f"Model {model_str!r} not available, falling back to {fp}/{fid}"
            )
            return get_model(fp, fid)
        except KeyError:
            continue

    # No model found
    available_providers = [p for p in ["google", "anthropic", "openai"] if _has_api_key(p)]
    if available_providers:
        error_msg = (
            f"Could not resolve model {model_str!r}. "
            f"Available providers with API keys: {', '.join(available_providers)}"
        )
    else:
        error_msg = (
            f"Could not resolve model {model_str!r} and no API keys found for fallback providers. "
            f"Please set API keys in environment (e.g., GOOGLE_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY)"
        )
    raise ValueError(error_msg)


# ---------------------------------------------------------------------------
# Message conversion: openclaw → pi_ai
# ---------------------------------------------------------------------------

def _now_ms() -> int:
    return int(time.time() * 1000)


def _to_pi_user_message(text: str) -> UserMessage:
    return UserMessage(role="user", content=text, timestamp=_now_ms())


def _to_pi_messages(history: list[dict[str, Any]]) -> list[Any]:
    """Convert openclaw history dicts to pi_ai message objects."""
    result: list[Any] = []
    ts = _now_ms()
    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            # Extract text from list of content blocks
            text_parts = []
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    text_parts.append(blk.get("text", ""))
                elif isinstance(blk, str):
                    text_parts.append(blk)
            content_str = " ".join(text_parts)
        else:
            content_str = str(content)

        if role == "user":
            result.append(UserMessage(role="user", content=content_str, timestamp=ts))
        # tool/assistant roles are handled by pi_coding_agent internally;
        # for the pure stream adapter we only pass user messages.
    return result


def _to_pi_tools(tools: list[Any]) -> list[Tool]:
    """Convert openclaw tool objects to pi_ai Tool objects."""
    result: list[Tool] = []
    for tool in tools:
        if isinstance(tool, Tool):
            result.append(tool)
            continue

        name = ""
        description = ""
        parameters: dict[str, Any] = {"type": "object", "properties": {}}

        if hasattr(tool, "to_dict"):
            spec = tool.to_dict()
            name = spec.get("name", "")
            description = spec.get("description", "")
            parameters = spec.get("parameters", spec.get("input_schema", parameters))
        elif hasattr(tool, "schema"):
            name = getattr(tool, "name", str(tool))
            description = getattr(tool, "description", "")
            parameters = tool.schema()
        elif isinstance(tool, dict):
            name = tool.get("name", "")
            description = tool.get("description", "")
            parameters = tool.get("parameters", tool.get("input_schema", parameters))
        else:
            name = getattr(tool, "name", str(tool))
            description = getattr(tool, "description", "")

        if name:
            result.append(Tool(name=name, description=description, parameters=parameters))
    return result


# ---------------------------------------------------------------------------
# Main adapter
# ---------------------------------------------------------------------------

class PiStreamAdapter:
    """Thin adapter that calls pi_ai.stream_simple and converts events.

    Replaces providers/ + MultiProviderRuntime for openclaw's LLM calls.
    Mirrors how attempt.ts calls streamSimple() from @pi-ai.

    Usage::

        adapter = PiStreamAdapter(model="google/gemini-2.0-flash")
        async for event in adapter.stream_turn(messages, tools, system_prompt):
            ...
    """

    def __init__(
        self,
        model: str = "google/gemini-2.0-flash",
        max_tokens: int = 8192,
        temperature: float | None = None,
        reasoning: str | None = None,
    ) -> None:
        self.model_str = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.reasoning = reasoning

    async def stream_turn(
        self,
        history: list[dict[str, Any]],
        tools: list[Any],
        system_prompt: str | None = None,
        *,
        session_id: str | None = None,
    ) -> AsyncIterator[Event]:
        """Stream a single LLM turn using pi_ai.stream_simple.

        Args:
            history: List of openclaw message dicts (role/content).
            tools: List of openclaw tool objects.
            system_prompt: Optional system prompt string.
            session_id: Used for pi_ai session tracking.

        Yields:
            openclaw Event objects.
        """
        try:
            model = _resolve_model(self.model_str)
        except ValueError as exc:
            yield Event(
                type=EventType.ERROR,
                source="pi-stream-adapter",
                session_id=session_id or "",
                data={"message": str(exc)},
            )
            return

        pi_messages = _to_pi_messages(history)
        pi_tools = _to_pi_tools(tools) or None

        context = Context(
            system_prompt=system_prompt,
            messages=pi_messages,
            tools=pi_tools,
        )

        opts = SimpleStreamOptions(
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            session_id=session_id,
        )

        try:
            async for event in stream_simple(model, context, opts):
                converted = _convert_event(event, session_id or "")
                if converted is not None:
                    yield converted
        except Exception as exc:
            logger.error("PiStreamAdapter error: %s", exc, exc_info=True)
            yield Event(
                type=EventType.ERROR,
                source="pi-stream-adapter",
                session_id=session_id or "",
                data={"message": str(exc)},
            )


# ---------------------------------------------------------------------------
# Event conversion: pi_ai → openclaw
# ---------------------------------------------------------------------------

def _convert_event(event: Any, session_id: str) -> Event | None:
    """Convert a pi_ai AssistantMessageEvent to an openclaw Event."""
    etype = getattr(event, "type", None)

    if etype == "text_delta":
        ev: EventTextDelta = event
        return Event(
            type=EventType.TEXT,
            source="pi-stream-adapter",
            session_id=session_id,
            data={"delta": {"text": ev.delta}},
        )

    if etype == "thinking_delta":
        ev_think: EventThinkingDelta = event
        return Event(
            type=EventType.THINKING_UPDATE,
            source="pi-stream-adapter",
            session_id=session_id,
            data={"delta": {"text": ev_think.delta}},
        )

    if etype == "toolcall_end":
        ev_tc: EventToolCallEnd = event
        tc: ToolCall = ev_tc.tool_call
        return Event(
            type=EventType.TOOL_EXECUTION_START,
            source="pi-stream-adapter",
            session_id=session_id,
            data={
                "tool_name": tc.name,
                "tool_call_id": tc.id,
                "arguments": tc.arguments,
            },
        )

    if etype == "done":
        ev_done: EventDone = event
        stop_reason = ev_done.reason
        return Event(
            type=EventType.AGENT_TURN_COMPLETE,
            source="pi-stream-adapter",
            session_id=session_id,
            data={
                "stop_reason": stop_reason,
                "usage": {
                    "input_tokens": ev_done.message.usage.input,
                    "output_tokens": ev_done.message.usage.output,
                },
            },
        )

    if etype == "error":
        ev_err: EventError = event
        err_msg = ""
        if ev_err.error and ev_err.error.error_message:
            err_msg = ev_err.error.error_message
        return Event(
            type=EventType.ERROR,
            source="pi-stream-adapter",
            session_id=session_id,
            data={"message": err_msg, "reason": ev_err.reason},
        )

    # text_start / text_end / toolcall_start / toolcall_delta / start — skip
    return None


__all__ = ["PiStreamAdapter", "_to_pi_messages", "_to_pi_tools", "_resolve_model"]
