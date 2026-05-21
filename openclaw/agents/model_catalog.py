"""Model catalog — loads the curated model list from models.json + SDK discovery.

Aligned with TypeScript openclaw/src/agents/model-catalog.ts.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Standardised set of API type strings — mirrors TS types.models.ts MODEL_APIS
MODEL_APIS: list[str] = [
    "openai-completions",
    "openai-responses",
    "openai-codex-responses",
    "anthropic-messages",
    "google-generative-ai",
    "github-copilot",
    "bedrock-converse-stream",
    "ollama",
    "azure-openai-responses",
]

_CODEX_PROVIDER = "openai-codex"
_OPENAI_CODEX_GPT53_MODEL_ID = "gpt-5.3-codex"
_OPENAI_CODEX_GPT53_SPARK_MODEL_ID = "gpt-5.3-codex-spark"


@dataclass
class ModelCatalogEntry:
    """A single entry in the model catalog — mirrors TS ModelCatalogEntry."""

    id: str
    name: str
    provider: str
    context_window: int | None = None
    reasoning: bool | None = None
    input: list[str] | None = None  # e.g. ["text", "image"]
    api: str | None = None  # Provider API type: "ollama", "openai-completions", etc.


# ---------------------------------------------------------------------------
# Module-level promise-style cache (mirrors TS `modelCatalogPromise`)
# ---------------------------------------------------------------------------

_model_catalog_future: asyncio.Future[list[ModelCatalogEntry]] | None = None
_has_logged_error: bool = False


def _apply_openai_codex_spark_fallback(models: list[ModelCatalogEntry]) -> None:
    """Ensure the spark variant exists if the base codex model is present."""
    has_spark = any(
        e.provider == _CODEX_PROVIDER and e.id.lower() == _OPENAI_CODEX_GPT53_SPARK_MODEL_ID
        for e in models
    )
    if has_spark:
        return
    base = next(
        (e for e in models
         if e.provider == _CODEX_PROVIDER and e.id.lower() == _OPENAI_CODEX_GPT53_MODEL_ID),
        None,
    )
    if not base:
        return
    models.append(ModelCatalogEntry(
        id=_OPENAI_CODEX_GPT53_SPARK_MODEL_ID,
        name=_OPENAI_CODEX_GPT53_SPARK_MODEL_ID,
        provider=base.provider,
        context_window=base.context_window,
        reasoning=base.reasoning,
        input=base.input,
    ))


def _sort_models(entries: list[ModelCatalogEntry]) -> list[ModelCatalogEntry]:
    return sorted(entries, key=lambda e: (e.provider, e.name))


def _read_models_json_directly(agent_dir: str) -> list[ModelCatalogEntry]:
    """Fallback: parse models.json without the Pi SDK."""
    import json
    import os

    models_path = os.path.join(agent_dir, "models.json")
    if not os.path.exists(models_path):
        return []
    try:
        with open(models_path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        return []

    # models.json may be a list or {"providers": {provider: {api: ..., models: [...]}}}
    entries: list[dict[str, Any]] = []
    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        providers = raw.get("providers") or {}
        for provider_id, provider_config in (providers.items() if isinstance(providers, dict) else []):
            # provider_config can be a list (legacy) or a dict with "models" key
            if isinstance(provider_config, list):
                provider_models = provider_config
                provider_api = None
            elif isinstance(provider_config, dict):
                provider_models = provider_config.get("models") or []
                provider_api = provider_config.get("api")
            else:
                continue
            for model_entry in provider_models:
                if isinstance(model_entry, dict):
                    entries.append({
                        **model_entry,
                        "provider": provider_id,
                        "_provider_api": provider_api,
                    })

    results: list[ModelCatalogEntry] = []
    for entry in entries:
        mid = str(entry.get("id") or "").strip()
        if not mid:
            continue
        provider = str(entry.get("provider") or "").strip()
        if not provider:
            continue
        name = str(entry.get("name") or mid).strip() or mid
        ctx_win = entry.get("contextWindow") or entry.get("context_window")
        context_window = int(ctx_win) if isinstance(ctx_win, (int, float)) and ctx_win > 0 else None
        reasoning = entry.get("reasoning")
        reasoning = bool(reasoning) if isinstance(reasoning, bool) else None
        inp = entry.get("input")
        input_types = list(inp) if isinstance(inp, list) else None
        # Preserve provider-level api type — model-level "api" takes precedence
        api_type = entry.get("api") or entry.get("_provider_api") or None
        results.append(ModelCatalogEntry(
            id=mid,
            name=name,
            provider=provider,
            context_window=context_window,
            reasoning=reasoning,
            input=input_types,
            api=api_type,
        ))
    return results


async def load_model_catalog(
    config: Any = None,
    use_cache: bool = True,
) -> list[ModelCatalogEntry]:
    """Load the model catalog, using a module-level async cache.

    Mirrors TS loadModelCatalog().
    Falls back to reading models.json directly if SDK discovery fails.
    """
    global _model_catalog_future, _has_logged_error

    if not use_cache:
        _model_catalog_future = None

    if _model_catalog_future is not None:
        return await _model_catalog_future

    loop = asyncio.get_running_loop()
    fut: asyncio.Future[list[ModelCatalogEntry]] = loop.create_future()
    _model_catalog_future = fut

    models: list[ModelCatalogEntry] = []
    try:
        # Ensure models.json is written/merged first
        from .models_config import ensure_openclaw_models_json
        from .agent_paths import resolve_openclaw_agent_dir

        agent_dir = resolve_openclaw_agent_dir()
        await ensure_openclaw_models_json(config, agent_dir_override=agent_dir)

        # Try loading via models.json directly (Python has no Pi SDK equivalent)
        models = _read_models_json_directly(agent_dir)
        _apply_openai_codex_spark_fallback(models)

        if not models:
            # Don't cache an empty result
            _model_catalog_future = None
            fut.set_result([])
            return []

        result = _sort_models(models)
        fut.set_result(result)
        return result

    except Exception as exc:
        if not _has_logged_error:
            _has_logged_error = True
            logger.warning("[model-catalog] Failed to load model catalog: %s", exc)
        # Don't poison the cache on transient errors
        _model_catalog_future = None
        fut.cancel()
        if models:
            return _sort_models(models)
        return []


def model_supports_vision(entry: ModelCatalogEntry | None) -> bool:
    """Return True if the model's catalog entry lists image input support.

    Mirrors TS modelSupportsVision().
    """
    if entry is None:
        return False
    return bool(entry.input and "image" in entry.input)


def find_model_in_catalog(
    catalog: list[ModelCatalogEntry],
    provider: str,
    model_id: str,
) -> ModelCatalogEntry | None:
    """Find a model in the catalog by provider and id (case-insensitive).

    Mirrors TS findModelInCatalog().
    """
    norm_provider = provider.lower().strip()
    norm_id = model_id.lower().strip()
    return next(
        (e for e in catalog
         if e.provider.lower() == norm_provider and e.id.lower() == norm_id),
        None,
    )


def reset_model_catalog_cache_for_test() -> None:
    """Clear the module-level catalog cache. For use in tests only.

    Mirrors TS resetModelCatalogCacheForTest().
    """
    global _model_catalog_future, _has_logged_error
    _model_catalog_future = None
    _has_logged_error = False


__all__ = [
    "ModelCatalogEntry",
    "load_model_catalog",
    "model_supports_vision",
    "find_model_in_catalog",
    "reset_model_catalog_cache_for_test",
]
