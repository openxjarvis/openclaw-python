"""Models config writer — ensures models.json is up-to-date in the agent dir.

Aligned with TypeScript openclaw/src/agents/models-config.ts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_OLLAMA_SHOW_CONCURRENCY = 8
_OLLAMA_SHOW_MAX_MODELS = 200
_OLLAMA_DEFAULT_CONTEXT_WINDOW = 128000
_OLLAMA_DEFAULT_MAX_TOKENS = 8192

_DEFAULT_MODE = "merge"


# ---------------------------------------------------------------------------
# Provider model merging helpers — mirrors TS mergeProviderModels / mergeProviders
# ---------------------------------------------------------------------------

def merge_provider_models(
    implicit: dict[str, Any],
    explicit: dict[str, Any],
) -> dict[str, Any]:
    """Merge two ProviderConfig dicts, deduplicating models by id.

    Explicit entries take precedence; implicit entries fill in missing models.
    Mirrors TS mergeProviderModels().
    """
    implicit_models: list[Any] = implicit.get("models") or []
    explicit_models: list[Any] = explicit.get("models") or []

    if not implicit_models:
        return {**implicit, **explicit}

    def get_id(model: Any) -> str:
        if not isinstance(model, dict):
            return ""
        return str(model.get("id") or "").strip()

    seen: set[str] = {get_id(m) for m in explicit_models if get_id(m)}
    extra = [
        m for m in implicit_models
        if get_id(m) and get_id(m) not in seen
    ]
    merged_models = list(explicit_models) + extra
    return {**implicit, **explicit, "models": merged_models}


def merge_providers(
    implicit: dict[str, Any] | None,
    explicit: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge implicit and explicit provider dicts.

    Mirrors TS mergeProviders().
    """
    out: dict[str, Any] = dict(implicit or {})
    for key, explicit_entry in (explicit or {}).items():
        provider_key = key.strip()
        if not provider_key:
            continue
        existing = out.get(provider_key)
        out[provider_key] = (
            merge_provider_models(existing, explicit_entry)
            if existing is not None
            else explicit_entry
        )
    return out


# ---------------------------------------------------------------------------
# Implicit provider discovery (Python equivalent of models-config.providers.ts)
# ---------------------------------------------------------------------------

def _resolve_ollama_api_base(configured_base_url: str | None) -> str:
    """Derive the native Ollama API base URL by stripping /v1 if present.

    Mirrors resolveOllamaApiBase() in models-config.providers.ts.
    """
    base = configured_base_url or "http://localhost:11434"
    base = base.rstrip("/")
    if base.lower().endswith("/v1"):
        base = base[:-3]
    return base


async def _query_ollama_context_window(
    api_base: str,
    model_name: str,
    client: httpx.AsyncClient,
) -> int | None:
    """Query /api/show for a model's context window size.

    Mirrors queryOllamaContextWindow() in models-config.providers.ts.
    """
    try:
        resp = await client.post(
            f"{api_base}/api/show",
            json={"name": model_name},
            timeout=3.0,
        )
        if not resp.is_success:
            return None
        data: dict[str, Any] = resp.json()
        model_info: dict[str, Any] = data.get("model_info") or {}
        for key, value in model_info.items():
            if key.endswith(".context_length") and isinstance(value, (int, float)):
                ctx = int(value)
                if ctx > 0:
                    return ctx
        return None
    except Exception:
        return None


async def _discover_ollama_models(
    base_url: str | None = None,
    quiet: bool = False,
) -> list[dict[str, Any]]:
    """Discover locally installed Ollama models via /api/tags + /api/show.

    Mirrors discoverOllamaModels() in models-config.providers.ts:
      - GET /api/tags to list installed models (5s timeout)
      - POST /api/show for up to OLLAMA_SHOW_MAX_MODELS models,
        8 at a time concurrently, to discover context window size
      - Sets reasoning=True for models whose name contains 'r1' or 'reasoning'

    Returns list of model dicts suitable for the models.json format.
    """
    api_base = _resolve_ollama_api_base(base_url)
    discovered: list[dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        # GET /api/tags
        try:
            resp = await client.get(f"{api_base}/api/tags", timeout=5.0)
            if not resp.is_success:
                if not quiet:
                    logger.warning("Failed to discover Ollama models: HTTP %s", resp.status_code)
                return []
            data: dict[str, Any] = resp.json()
        except Exception as exc:
            if not quiet:
                logger.warning("Failed to discover Ollama models: %s", exc)
            return []

        raw_models: list[dict[str, Any]] = data.get("models") or []
        if not raw_models:
            logger.debug("No Ollama models found on local instance")
            return []

        models_to_inspect = raw_models[:_OLLAMA_SHOW_MAX_MODELS]
        if len(models_to_inspect) < len(raw_models) and not quiet:
            logger.warning(
                "Capping Ollama /api/show inspection to %d models (received %d)",
                _OLLAMA_SHOW_MAX_MODELS,
                len(raw_models),
            )

        # Batch POST /api/show — OLLAMA_SHOW_CONCURRENCY at a time
        for i in range(0, len(models_to_inspect), _OLLAMA_SHOW_CONCURRENCY):
            batch = models_to_inspect[i : i + _OLLAMA_SHOW_CONCURRENCY]

            async def _show_one(model_info: dict[str, Any]) -> dict[str, Any]:
                model_id: str = model_info.get("name") or ""
                if not model_id:
                    return {}
                ctx_window = await _query_ollama_context_window(api_base, model_id, client)
                is_reasoning = (
                    "r1" in model_id.lower() or "reasoning" in model_id.lower()
                )
                return {
                    "id": model_id,
                    "name": model_id,
                    "reasoning": is_reasoning,
                    "input": ["text"],
                    "contextWindow": ctx_window if ctx_window else _OLLAMA_DEFAULT_CONTEXT_WINDOW,
                    "maxTokens": _OLLAMA_DEFAULT_MAX_TOKENS,
                }

            batch_results = await asyncio.gather(*[_show_one(m) for m in batch])
            for entry in batch_results:
                if entry.get("id"):
                    discovered.append(entry)

    return discovered


def _resolve_implicit_providers(
    agent_dir: str,
    explicit_providers: dict[str, Any],
) -> dict[str, Any]:
    """Discover implicit provider configs that are present in the environment.

    Returns a dict of provider_id -> ProviderConfig. Checks environment for
    API keys and injects well-known default model lists.
    """
    implicit: dict[str, Any] = {}

    # Anthropic
    if not explicit_providers.get("anthropic"):
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if anthropic_key:
            implicit["anthropic"] = {
                "apiKey": anthropic_key,
                "models": [
                    {"id": "claude-opus-4-5", "name": "Claude Opus 4.5",
                     "input": ["text", "image"], "reasoning": True},
                    {"id": "claude-sonnet-4-5", "name": "Claude Sonnet 4.5",
                     "input": ["text", "image"], "reasoning": True},
                ],
            }

    # OpenAI
    if not explicit_providers.get("openai"):
        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if openai_key:
            implicit["openai"] = {
                "apiKey": openai_key,
                "models": [
                    {"id": "gpt-4o", "name": "GPT-4o", "input": ["text", "image"]},
                    {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "input": ["text", "image"]},
                    {"id": "o1", "name": "o1", "input": ["text"], "reasoning": True},
                ],
            }

    # Google Gemini
    if not explicit_providers.get("google"):
        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if gemini_key:
            implicit["google"] = {
                "apiKey": gemini_key,
                "models": [
                    {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro",
                     "input": ["text", "image"], "reasoning": True},
                    {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash",
                     "input": ["text", "image"]},
                ],
            }

    # Ollama placeholder — baseUrl only; models are discovered async in
    # ensure_openclaw_models_json() and the entry is REMOVED if nothing is found.
    # Mirrors TS: only include ollama when models found OR OLLAMA_API_KEY set.
    if not explicit_providers.get("ollama"):
        ollama_base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        implicit["_ollama_pending"] = {
            "baseUrl": _resolve_ollama_api_base(ollama_base),
            "api": "ollama",
            "models": [],
        }

    # ── OpenAI-compatible providers — mirrors TS register-builtins.ts ──

    # xAI Grok
    if not explicit_providers.get("xai"):
        xai_key = os.environ.get("XAI_API_KEY", "").strip()
        if xai_key:
            implicit["xai"] = {
                "apiKey": xai_key,
                "baseUrl": "https://api.x.ai/v1",
                "models": [
                    {"id": "grok-3", "name": "Grok 3", "input": ["text"]},
                    {"id": "grok-3-mini", "name": "Grok 3 Mini", "input": ["text"]},
                    {"id": "grok-2-vision-1212", "name": "Grok 2 Vision", "input": ["text", "image"]},
                ],
            }

    # DeepSeek
    if not explicit_providers.get("deepseek"):
        deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if deepseek_key:
            implicit["deepseek"] = {
                "apiKey": deepseek_key,
                "baseUrl": "https://api.deepseek.com",
                "models": [
                    {"id": "deepseek-chat", "name": "DeepSeek Chat", "input": ["text"]},
                    {"id": "deepseek-reasoner", "name": "DeepSeek R1", "input": ["text"], "reasoning": True},
                ],
            }

    # Zhipu AI (ZAI / GLM)
    if not explicit_providers.get("zai"):
        zai_key = (
            os.environ.get("ZAI_API_KEY", "").strip()
            or os.environ.get("ZHIPU_API_KEY", "").strip()
        )
        if zai_key:
            implicit["zai"] = {
                "apiKey": zai_key,
                "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
                "models": [
                    {"id": "glm-4-plus", "name": "GLM-4 Plus", "input": ["text", "image"]},
                    {"id": "glm-4-flash", "name": "GLM-4 Flash", "input": ["text"]},
                ],
            }

    # Groq
    if not explicit_providers.get("groq"):
        groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        if groq_key:
            implicit["groq"] = {
                "apiKey": groq_key,
                "baseUrl": "https://api.groq.com/openai/v1",
                "models": [
                    {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B", "input": ["text"]},
                    {"id": "mixtral-8x7b-32768", "name": "Mixtral 8x7B", "input": ["text"]},
                ],
            }

    # Mistral
    if not explicit_providers.get("mistral"):
        mistral_key = os.environ.get("MISTRAL_API_KEY", "").strip()
        if mistral_key:
            implicit["mistral"] = {
                "apiKey": mistral_key,
                "baseUrl": "https://api.mistral.ai/v1",
                "models": [
                    {"id": "mistral-large-latest", "name": "Mistral Large", "input": ["text"]},
                    {"id": "mistral-small-latest", "name": "Mistral Small", "input": ["text"]},
                ],
            }

    # Moonshot (Kimi) — TS CHANGELOG #68907, #68816, #68746
    # URL: MOONSHOT_API_KEY → .cn endpoint; KIMI_API_KEY → .ai endpoint
    # Default model updated to kimi-k2.6 (kimi-latest is deprecated)
    if not explicit_providers.get("moonshot"):
        moonshot_key = os.environ.get("MOONSHOT_API_KEY", "").strip()
        kimi_key = os.environ.get("KIMI_API_KEY", "").strip()
        effective_key = moonshot_key or kimi_key
        if effective_key:
            # .ai endpoint for KIMI_API_KEY, .cn for MOONSHOT_API_KEY (legacy)
            base_url = (
                "https://api.moonshot.ai/v1" if (kimi_key and not moonshot_key)
                else "https://api.moonshot.cn/v1"
            )
            implicit["moonshot"] = {
                "apiKey": effective_key,
                "baseUrl": base_url,
                "defaultModel": "kimi-k2.6",
                "models": [
                    {"id": "moonshot-v1-8k", "name": "Moonshot 8K", "input": ["text"]},
                    {"id": "moonshot-v1-32k", "name": "Moonshot 32K", "input": ["text"]},
                    {"id": "kimi-k2.6", "name": "Kimi K2.6", "input": ["text"], "reasoning": True},
                    {"id": "kimi-latest", "name": "Kimi Latest (deprecated)", "input": ["text"]},
                ],
            }

    # Together AI
    if not explicit_providers.get("together"):
        together_key = os.environ.get("TOGETHER_API_KEY", "").strip()
        if together_key:
            implicit["together"] = {
                "apiKey": together_key,
                "baseUrl": "https://api.together.xyz/v1",
                "models": [],
            }

    # OpenRouter
    if not explicit_providers.get("openrouter"):
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if openrouter_key:
            implicit["openrouter"] = {
                "apiKey": openrouter_key,
                "baseUrl": "https://openrouter.ai/api/v1",
                "models": [],
            }

    # Azure OpenAI — api type "azure-openai-responses" (mirrors TS MODEL_APIS)
    if not explicit_providers.get("azure-openai"):
        azure_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
        azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
        if azure_key and azure_endpoint:
            implicit["azure-openai"] = {
                "apiKey": azure_key,
                "baseUrl": azure_endpoint,
                "api": "azure-openai-responses",
                "models": [],
            }

    # HuggingFace
    if not explicit_providers.get("huggingface"):
        hf_key = (
            os.environ.get("HUGGINGFACE_API_KEY", "").strip()
            or os.environ.get("HF_API_KEY", "").strip()
        )
        if hf_key:
            implicit["huggingface"] = {
                "apiKey": hf_key,
                "baseUrl": "https://api-inference.huggingface.co/v1",
                "models": [],
            }

    # Cerebras
    if not explicit_providers.get("cerebras"):
        cerebras_key = os.environ.get("CEREBRAS_API_KEY", "").strip()
        if cerebras_key:
            implicit["cerebras"] = {
                "apiKey": cerebras_key,
                "baseUrl": "https://api.cerebras.ai/v1",
                "models": [
                    {"id": "llama3.1-70b", "name": "Llama 3.1 70B (Cerebras)", "input": ["text"]},
                ],
            }

    # Alibaba Qwen via DashScope
    if not explicit_providers.get("qwen-portal"):
        qwen_key = (
            os.environ.get("DASHSCOPE_API_KEY", "").strip()
            or os.environ.get("QWEN_API_KEY", "").strip()
        )
        if qwen_key:
            implicit["qwen-portal"] = {
                "apiKey": qwen_key,
                "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "models": [
                    {"id": "qwen-max", "name": "Qwen Max", "input": ["text", "image"]},
                    {"id": "qwen-turbo", "name": "Qwen Turbo", "input": ["text"]},
                ],
            }

    return implicit


def _normalize_providers(
    providers: dict[str, Any],
    agent_dir: str,
) -> dict[str, Any]:
    """Post-process providers dict before writing to models.json.

    Strips empty model lists if provider has no models.
    """
    out: dict[str, Any] = {}
    for provider_id, config in providers.items():
        if not isinstance(config, dict):
            continue
        models = config.get("models")
        if isinstance(models, list) and len(models) == 0:
            # If we have no models but have a baseUrl / apiKey, keep provider
            if config.get("baseUrl") or config.get("apiKey"):
                out[provider_id] = config
        else:
            out[provider_id] = config
    return out


# ---------------------------------------------------------------------------
# Main entry point — mirrors TS ensureOpenClawModelsJson
# ---------------------------------------------------------------------------

def _merge_with_existing_provider_secrets(
    next_providers: dict[str, Any],
    existing_providers: dict[str, Any],
) -> dict[str, Any]:
    """Merge new provider configs with existing, preserving apiKey/baseUrl from existing.

    Mirrors TS mergeWithExistingProviderSecrets() — when regenerating models.json in
    'merge' mode, user-set API keys and baseUrls stored in the file are not overwritten.
    """
    merged: dict[str, Any] = {}
    # Start with all existing providers
    for key, entry in existing_providers.items():
        merged[key] = entry
    # For each new provider, merge preserving secrets from existing
    for key, new_entry in next_providers.items():
        existing = existing_providers.get(key)
        if existing is None:
            merged[key] = new_entry
            continue
        preserved: dict[str, Any] = {}
        if isinstance(existing.get("apiKey"), str) and existing["apiKey"]:
            preserved["apiKey"] = existing["apiKey"]
        if isinstance(existing.get("baseUrl"), str) and existing["baseUrl"]:
            preserved["baseUrl"] = existing["baseUrl"]
        merged[key] = {**new_entry, **preserved}
    return merged


async def _resolve_providers_for_mode(
    mode: str,
    target_path: Path,
    providers: dict[str, Any],
) -> dict[str, Any]:
    """Apply 'merge' mode logic — preserve secrets from existing models.json.

    Mirrors TS resolveProvidersForMode().
    """
    if mode != "merge":
        return providers
    try:
        with open(target_path, encoding="utf-8") as fh:
            existing = json.load(fh)
        if not isinstance(existing, dict) or not isinstance(existing.get("providers"), dict):
            return providers
        existing_providers: dict[str, Any] = existing["providers"]
        return _merge_with_existing_provider_secrets(
            next_providers=providers,
            existing_providers=existing_providers,
        )
    except Exception:
        return providers


async def _read_raw_file(path: Path) -> str:
    """Read a file as raw text, returning '' on any error. Mirrors TS readRawFile()."""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


async def ensure_openclaw_models_json(
    config: Any = None,
    agent_dir_override: str | None = None,
) -> dict[str, Any]:
    """Write (or update) {agentDir}/models.json based on config + implicit discovery.

    Mirrors TS ensureOpenClawModelsJson().
    Returns {"agent_dir": str, "wrote": bool}.
    """
    from .agent_paths import resolve_openclaw_agent_dir

    if config is None:
        try:
            from openclaw.config.loader import load_config
            config = load_config(as_dict=True)
        except Exception:
            config = {}

    agent_dir = (
        agent_dir_override.strip()
        if agent_dir_override and agent_dir_override.strip()
        else resolve_openclaw_agent_dir()
    )

    cfg = config if isinstance(config, dict) else {}
    explicit_providers: dict[str, Any] = {}
    models_section = cfg.get("models") or {}
    if isinstance(models_section, dict):
        explicit_providers = models_section.get("providers") or {}

    implicit_providers = _resolve_implicit_providers(agent_dir, explicit_providers)

    # ── Ollama model discovery (async) — mirrors TS discoverOllamaModels gate ─
    # TS only adds ollama provider when: models found OR OLLAMA_API_KEY set OR
    # explicit config has apiKey. This prevents an empty entry from cluttering the
    # catalog when Ollama is not running.
    ollama_api_key = os.environ.get("OLLAMA_API_KEY", "").strip()
    explicit_ollama = explicit_providers.get("ollama") or {}
    explicit_ollama_api_key = explicit_ollama.get("apiKey", "")
    has_explicit_ollama_models = (
        isinstance(explicit_ollama.get("models"), list)
        and len(explicit_ollama["models"]) > 0
    )

    # Remove the placeholder added above so we can decide whether to keep it
    implicit_providers.pop("_ollama_pending", None)

    ollama_base = (
        _resolve_ollama_api_base(explicit_ollama.get("baseUrl"))
        if explicit_ollama.get("baseUrl")
        else _resolve_ollama_api_base(os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    )

    # Normalise the explicit_providers["ollama"] baseUrl right away so that
    # merge_providers never overwrites the resolved URL with a raw /v1-suffixed one.
    if explicit_providers.get("ollama") and isinstance(explicit_providers["ollama"], dict):
        explicit_providers["ollama"] = {
            **explicit_providers["ollama"],
            "baseUrl": ollama_base,
        }

    if has_explicit_ollama_models:
        # Explicit models configured — skip discovery; set api field.
        implicit_providers["ollama"] = {
            **explicit_ollama,
            "baseUrl": ollama_base,
            "api": explicit_ollama.get("api") or "ollama",
        }
        if ollama_api_key or explicit_ollama_api_key:
            implicit_providers["ollama"]["apiKey"] = ollama_api_key or explicit_ollama_api_key
    else:
        # Auto-discovery (quiet=True when user has not explicitly configured Ollama)
        quiet = not (ollama_api_key or explicit_ollama)
        ollama_models = await _discover_ollama_models(ollama_base, quiet=quiet)

        # Gate: only include if models found OR API key present OR explicit apiKey
        if ollama_models or ollama_api_key or explicit_ollama_api_key:
            implicit_providers["ollama"] = {
                "baseUrl": ollama_base,
                "api": "ollama",
                "models": ollama_models,
            }
            api_key = ollama_api_key or explicit_ollama_api_key
            if api_key:
                implicit_providers["ollama"]["apiKey"] = api_key

    providers = merge_providers(implicit_providers, explicit_providers)

    # Bedrock: check for AWS credentials (simplified — TS does full SDK discovery)
    if not providers.get("amazon-bedrock"):
        if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"):
            aws_region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
            providers["amazon-bedrock"] = {
                "region": aws_region,
                "models": [],
            }

    if not providers:
        return {"agent_dir": agent_dir, "wrote": False}

    mode = "merge"
    if isinstance(models_section, dict):
        mode = models_section.get("mode") or _DEFAULT_MODE

    target_path = Path(agent_dir) / "models.json"
    merged_providers = await _resolve_providers_for_mode(
        mode=mode,
        target_path=target_path,
        providers=providers,
    )

    normalized = _normalize_providers(merged_providers, agent_dir)
    next_content = json.dumps({"providers": normalized}, indent=2) + "\n"

    existing_raw = await _read_raw_file(target_path)

    if existing_raw == next_content:
        return {"agent_dir": agent_dir, "wrote": False}

    target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target_path.write_text(next_content, encoding="utf-8")
    # Restrict file permissions (best-effort on non-POSIX)
    try:
        os.chmod(target_path, 0o600)
    except Exception:
        pass

    return {"agent_dir": agent_dir, "wrote": True}


# ---------------------------------------------------------------------------
# Additional provider builders — mirrors TS models-config.providers.ts
# ---------------------------------------------------------------------------

MINIMAX_BASE_URL = "https://api.minimax.io/anthropic"
QIANFAN_BASE_URL = "https://qianfan.baidubce.com/v2"
SYNTHETIC_BASE_URL = "https://api.synthetic.new/anthropic"
HUGGINGFACE_BASE_URL = "https://router.huggingface.co/v1"


def build_minimax_provider() -> dict:
    """Build MiniMax provider config.

    Mirrors TS buildMinimaxProvider() in models-config.providers.ts.
    Uses anthropic-messages API against the MiniMax Anthropic-compatible portal.
    Auto-detected via MINIMAX_API_KEY env var.
    """
    return {
        "baseUrl": MINIMAX_BASE_URL,
        "api": "anthropic-messages",
        "apiKeyEnv": "MINIMAX_API_KEY",
        "models": [
            {
                "id": "MiniMax-M2.1",
                "name": "MiniMax M2.1",
                "reasoning": False,
                "input": ["text"],
                "contextWindow": 200000,
                "maxTokens": 8192,
            },
            {
                "id": "MiniMax-M2.1-lightning",
                "name": "MiniMax M2.1 Lightning",
                "reasoning": False,
                "input": ["text"],
                "contextWindow": 200000,
                "maxTokens": 8192,
            },
            {
                "id": "MiniMax-VL-01",
                "name": "MiniMax VL 01",
                "reasoning": False,
                "input": ["text", "image"],
                "contextWindow": 200000,
                "maxTokens": 8192,
            },
            {
                "id": "MiniMax-M2.5",
                "name": "MiniMax M2.5",
                "reasoning": True,
                "input": ["text"],
                "contextWindow": 200000,
                "maxTokens": 8192,
            },
            {
                "id": "MiniMax-M2.5-Lightning",
                "name": "MiniMax M2.5 Lightning",
                "reasoning": True,
                "input": ["text"],
                "contextWindow": 200000,
                "maxTokens": 8192,
            },
        ],
    }


def build_qianfan_provider() -> dict:
    """Build Baidu Qianfan provider config.

    Mirrors TS buildQianfanProvider() in models-config.providers.ts.
    Uses openai-completions API.  Auto-detected via QIANFAN_API_KEY env var.
    """
    return {
        "baseUrl": QIANFAN_BASE_URL,
        "api": "openai-completions",
        "apiKeyEnv": "QIANFAN_API_KEY",
        "models": [
            {
                "id": "deepseek-v3.2",
                "name": "DEEPSEEK V3.2",
                "reasoning": True,
                "input": ["text"],
                "contextWindow": 98304,
                "maxTokens": 32768,
            },
            {
                "id": "ernie-5.0-thinking-preview",
                "name": "ERNIE-5.0-Thinking-Preview",
                "reasoning": True,
                "input": ["text", "image"],
                "contextWindow": 119000,
                "maxTokens": 64000,
            },
        ],
    }


def build_synthetic_provider() -> dict:
    """Build Synthetic provider config.

    Mirrors TS buildSyntheticProvider() in models-config.providers.ts.
    Uses anthropic-messages API at https://api.synthetic.new/anthropic.
    Auto-detected via SYNTHETIC_API_KEY env var.
    """
    return {
        "baseUrl": SYNTHETIC_BASE_URL,
        "api": "anthropic-messages",
        "apiKeyEnv": "SYNTHETIC_API_KEY",
        "models": [
            {
                "id": "claude-3-7-sonnet-20250219",
                "name": "Claude 3.7 Sonnet (Synthetic)",
                "reasoning": True,
                "input": ["text", "image"],
                "contextWindow": 200000,
                "maxTokens": 64000,
            },
            {
                "id": "claude-opus-4-5",
                "name": "Claude Opus 4.5 (Synthetic)",
                "reasoning": True,
                "input": ["text", "image"],
                "contextWindow": 200000,
                "maxTokens": 32000,
            },
        ],
    }


async def build_huggingface_provider(api_key: str | None = None) -> dict:
    """Build HuggingFace provider config, optionally discovering models from the API.

    Mirrors TS buildHuggingfaceProvider() in models-config.providers.ts.
    Uses openai-completions API at https://router.huggingface.co/v1.
    Auto-detected via HUGGINGFACE_HUB_TOKEN or HF_TOKEN env var.

    If api_key is provided and non-empty, attempts to discover models from
    GET https://router.huggingface.co/v1/models.  Falls back to a static catalog.
    """
    import os
    resolved_key = (api_key or "").strip()
    if not resolved_key:
        resolved_key = (
            os.environ.get("HUGGINGFACE_HUB_TOKEN", "")
            or os.environ.get("HF_TOKEN", "")
        ).strip()

    discovered: list[dict] = []
    if resolved_key:
        try:
            import urllib.request
            import json as _json
            req = urllib.request.Request(
                f"{HUGGINGFACE_BASE_URL}/models",
                headers={"Authorization": f"Bearer {resolved_key}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read())
            raw_models = data.get("data", []) if isinstance(data, dict) else data
            for m in raw_models:
                if not isinstance(m, dict):
                    continue
                mid = m.get("id", "")
                if not mid:
                    continue
                inputs = ["text"]
                arch = m.get("architecture", {}) or {}
                modalities = arch.get("input_modalities") or arch.get("modalities", [])
                if "image" in modalities:
                    inputs.append("image")
                discovered.append({
                    "id": mid,
                    "name": mid.replace("/", " / "),
                    "reasoning": False,
                    "input": inputs,
                    "contextWindow": 32768,
                    "maxTokens": 4096,
                })
        except Exception:
            pass

    if not discovered:
        discovered = [
            {
                "id": "meta-llama/Llama-3.1-8B-Instruct",
                "name": "Meta Llama 3.1 8B Instruct",
                "reasoning": False,
                "input": ["text"],
                "contextWindow": 131072,
                "maxTokens": 4096,
            },
            {
                "id": "Qwen/Qwen2.5-72B-Instruct",
                "name": "Qwen 2.5 72B Instruct",
                "reasoning": False,
                "input": ["text"],
                "contextWindow": 32768,
                "maxTokens": 4096,
            },
        ]

    return {
        "baseUrl": HUGGINGFACE_BASE_URL,
        "api": "openai-completions",
        "apiKeyEnv": "HUGGINGFACE_HUB_TOKEN",
        "models": discovered,
    }


def _detect_implicit_new_providers(implicit: dict) -> None:
    """Detect MiniMax, Qianfan, Synthetic and HuggingFace from env vars.

    Called during provider resolution to add implicit provider configs when
    the corresponding API key env vars are set.
    """
    import os

    if os.environ.get("MINIMAX_API_KEY", "").strip() and "minimax" not in implicit:
        implicit["minimax"] = {**build_minimax_provider(), "apiKey": os.environ["MINIMAX_API_KEY"].strip()}

    if os.environ.get("QIANFAN_API_KEY", "").strip() and "qianfan" not in implicit:
        implicit["qianfan"] = {**build_qianfan_provider(), "apiKey": os.environ["QIANFAN_API_KEY"].strip()}

    if os.environ.get("SYNTHETIC_API_KEY", "").strip() and "synthetic" not in implicit:
        implicit["synthetic"] = {**build_synthetic_provider(), "apiKey": os.environ["SYNTHETIC_API_KEY"].strip()}


__all__ = [
    "merge_provider_models",
    "merge_providers",
    "ensure_openclaw_models_json",
    "build_minimax_provider",
    "build_qianfan_provider",
    "build_synthetic_provider",
    "build_huggingface_provider",
    "_discover_ollama_models",
    "_resolve_ollama_api_base",
    "MINIMAX_BASE_URL",
    "QIANFAN_BASE_URL",
    "SYNTHETIC_BASE_URL",
    "HUGGINGFACE_BASE_URL",
]
