"""Web tools — search and fetch

Implements ``web_search`` and ``web_fetch`` aligned with the TypeScript
``src/agents/tools/web-search.ts`` and ``web-fetch.ts``.

Provider support (mirrors TS):
  - ``brave``      — Brave Search REST API (default when BRAVE_API_KEY is set)
  - ``perplexity`` — Perplexity AI chat completions (PERPLEXITY_API_KEY / OPENROUTER_API_KEY)
  - ``grok``       — xAI Grok responses endpoint (XAI_API_KEY)
  - ``duckduckgo`` — DuckDuckGo via ``ddgs`` library (free, no key required)
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from typing import Any

import httpx

from .base import AgentTool, ToolResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — mirrors TS web-search.ts
# ---------------------------------------------------------------------------

SEARCH_PROVIDERS = ("brave", "perplexity", "grok", "duckduckgo")
DEFAULT_SEARCH_COUNT = 5
MAX_SEARCH_COUNT = 10
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_CACHE_TTL_SECONDS = 300  # 5 minutes

BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
DEFAULT_PERPLEXITY_BASE_URL = "https://openrouter.ai/api/v1"
PERPLEXITY_DIRECT_BASE_URL = "https://api.perplexity.ai"
DEFAULT_PERPLEXITY_MODEL = "perplexity/sonar-pro"
PERPLEXITY_KEY_PREFIXES = ("pplx-",)
OPENROUTER_KEY_PREFIXES = ("sk-or-",)

XAI_API_ENDPOINT = "https://api.x.ai/v1/responses"
DEFAULT_GROK_MODEL = "grok-4-1-fast"

BRAVE_FRESHNESS_SHORTCUTS = frozenset({"pd", "pw", "pm", "py"})
_BRAVE_FRESHNESS_RANGE = re.compile(r"^(\d{4}-\d{2}-\d{2})to(\d{4}-\d{2}-\d{2})$")

# Simple in-memory cache: cache_key -> (value, expires_at)
_SEARCH_CACHE: dict[str, tuple[dict, float]] = {}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _normalize_cache_key(raw: str) -> str:
    return hashlib.md5(raw.encode()).hexdigest()


def _read_cache(key: str) -> dict | None:
    entry = _SEARCH_CACHE.get(key)
    if entry is None:
        return None
    value, expires_at = entry
    if time.time() > expires_at:
        del _SEARCH_CACHE[key]
        return None
    return value


def _write_cache(key: str, value: dict, ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS) -> None:
    _SEARCH_CACHE[key] = (value, time.time() + ttl_seconds)


# ---------------------------------------------------------------------------
# Freshness helpers — mirrors TS normalizeFreshness / freshnessToPerplexityRecency
# ---------------------------------------------------------------------------


def _is_valid_iso_date(value: str) -> bool:
    """Validate YYYY-MM-DD."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return False
    try:
        year, month, day = (int(p) for p in value.split("-"))
        # Basic range checks
        return 1 <= month <= 12 and 1 <= day <= 31
    except ValueError:
        return False


def normalize_freshness(value: str | None) -> str | None:
    """Normalise freshness to a safe value; returns ``None`` if invalid."""
    if not value:
        return None
    trimmed = value.strip()
    lower = trimmed.lower()
    if lower in BRAVE_FRESHNESS_SHORTCUTS:
        return lower
    match = _BRAVE_FRESHNESS_RANGE.match(trimmed)
    if not match:
        return None
    start, end = match.group(1), match.group(2)
    if not _is_valid_iso_date(start) or not _is_valid_iso_date(end):
        return None
    if start > end:
        return None
    return f"{start}to{end}"


def freshness_to_perplexity_recency(freshness: str | None) -> str | None:
    """Map ``pd|pw|pm|py`` to Perplexity ``search_recency_filter`` values."""
    mapping = {"pd": "day", "pw": "week", "pm": "month", "py": "year"}
    return mapping.get(freshness or "") if freshness else None


# ---------------------------------------------------------------------------
# Provider resolution helpers — mirrors TS resolveSearchProvider / resolveApiKey
# ---------------------------------------------------------------------------


def resolve_search_provider(config: dict | None = None) -> str:
    """Return the active provider name.

    Priority order:
      1. ``config["tools"]["web"]["search"]["provider"]``
      2. Environment: ``BRAVE_API_KEY`` → ``"brave"``
      3. Environment: ``PERPLEXITY_API_KEY`` or ``OPENROUTER_API_KEY`` → ``"perplexity"``
      4. Environment: ``XAI_API_KEY`` → ``"grok"``
      5. Fallback: ``"duckduckgo"``
    """
    cfg_provider = ""
    if config:
        try:
            cfg_provider = (
                config.get("tools", {})
                .get("web", {})
                .get("search", {})
                .get("provider", "")
                or ""
            ).strip().lower()
        except Exception:
            pass

    if cfg_provider in SEARCH_PROVIDERS:
        return cfg_provider

    # Auto-detect from environment
    if os.environ.get("BRAVE_API_KEY", "").strip():
        return "brave"
    if os.environ.get("PERPLEXITY_API_KEY", "").strip() or os.environ.get(
        "OPENROUTER_API_KEY", ""
    ).strip():
        return "perplexity"
    if os.environ.get("XAI_API_KEY", "").strip():
        return "grok"
    return "duckduckgo"


def _resolve_brave_api_key(config: dict | None) -> str | None:
    """Brave: config key or BRAVE_API_KEY env var."""
    if config:
        try:
            key = (
                config.get("tools", {})
                .get("web", {})
                .get("search", {})
                .get("apiKey", "")
                or ""
            ).strip()
            if key:
                return key
        except Exception:
            pass
    return os.environ.get("BRAVE_API_KEY", "").strip() or None


def _resolve_perplexity_api_key(config: dict | None) -> tuple[str | None, str]:
    """Perplexity: (api_key, source) — source is 'config'|'perplexity_env'|'openrouter_env'|'none'."""
    if config:
        try:
            perplexity_cfg = (
                config.get("tools", {})
                .get("web", {})
                .get("search", {})
                .get("perplexity", {})
                or {}
            )
            key = perplexity_cfg.get("apiKey", "").strip()
            if key:
                return key, "config"
        except Exception:
            pass
    key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if key:
        return key, "perplexity_env"
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key, "openrouter_env"
    return None, "none"


def _infer_perplexity_base_url_from_key(api_key: str | None) -> str | None:
    """Guess the base URL from the API key prefix."""
    if not api_key:
        return None
    lower = api_key.lower()
    if any(lower.startswith(p) for p in PERPLEXITY_KEY_PREFIXES):
        return "direct"
    if any(lower.startswith(p) for p in OPENROUTER_KEY_PREFIXES):
        return "openrouter"
    return None


def resolve_perplexity_base_url(
    perplexity_cfg: dict | None = None,
    api_key_source: str = "none",
    api_key: str | None = None,
) -> str:
    """Resolve the Perplexity base URL from config or key prefix."""
    from_cfg = ""
    if perplexity_cfg:
        from_cfg = perplexity_cfg.get("baseUrl", "").strip()
    if from_cfg:
        return from_cfg
    if api_key_source == "perplexity_env":
        return PERPLEXITY_DIRECT_BASE_URL
    if api_key_source == "openrouter_env":
        return DEFAULT_PERPLEXITY_BASE_URL
    if api_key_source == "config":
        hint = _infer_perplexity_base_url_from_key(api_key)
        if hint == "direct":
            return PERPLEXITY_DIRECT_BASE_URL
        if hint == "openrouter":
            return DEFAULT_PERPLEXITY_BASE_URL
    return DEFAULT_PERPLEXITY_BASE_URL


def resolve_perplexity_model(perplexity_cfg: dict | None = None) -> str:
    if perplexity_cfg:
        m = perplexity_cfg.get("model", "").strip()
        if m:
            return m
    return DEFAULT_PERPLEXITY_MODEL


def _resolve_perplexity_request_model(base_url: str, model: str) -> str:
    """Strip ``perplexity/`` prefix when calling the direct API."""
    try:
        from urllib.parse import urlparse
        if urlparse(base_url).hostname == "api.perplexity.ai":
            return model[len("perplexity/"):] if model.startswith("perplexity/") else model
    except Exception:
        pass
    return model


def _resolve_grok_api_key(config: dict | None) -> str | None:
    if config:
        try:
            grok_cfg = (
                config.get("tools", {})
                .get("web", {})
                .get("search", {})
                .get("grok", {})
                or {}
            )
            key = grok_cfg.get("apiKey", "").strip()
            if key:
                return key
        except Exception:
            pass
    return os.environ.get("XAI_API_KEY", "").strip() or None


def _resolve_grok_model(config: dict | None) -> str:
    if config:
        try:
            grok_cfg = (
                config.get("tools", {})
                .get("web", {})
                .get("search", {})
                .get("grok", {})
                or {}
            )
            m = grok_cfg.get("model", "").strip()
            if m:
                return m
        except Exception:
            pass
    return DEFAULT_GROK_MODEL


def _resolve_search_count(value: Any, fallback: int) -> int:
    try:
        return max(1, min(MAX_SEARCH_COUNT, int(value)))
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------


async def run_brave_search(
    query: str,
    api_key: str,
    count: int = DEFAULT_SEARCH_COUNT,
    country: str | None = None,
    search_lang: str | None = None,
    ui_lang: str | None = None,
    freshness: str | None = None,
    timeout_sec: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Call Brave Search REST API and return structured results."""
    params: dict[str, str] = {"q": query, "count": str(count)}
    if country:
        params["country"] = country
    if search_lang:
        params["search_lang"] = search_lang
    if ui_lang:
        params["ui_lang"] = ui_lang
    if freshness:
        params["freshness"] = freshness

    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        resp = await client.get(
            BRAVE_SEARCH_ENDPOINT,
            params=params,
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        )
        resp.raise_for_status()
        data = resp.json()

    raw_results = (data.get("web") or {}).get("results") or []
    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "description": r.get("description", ""),
            "published": r.get("age") or None,
        }
        for r in raw_results
        if isinstance(r, dict)
    ]
    return {
        "query": query,
        "provider": "brave",
        "count": len(results),
        "results": results,
    }


async def run_perplexity_search(
    query: str,
    api_key: str,
    base_url: str = DEFAULT_PERPLEXITY_BASE_URL,
    model: str = DEFAULT_PERPLEXITY_MODEL,
    freshness: str | None = None,
    timeout_sec: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Call Perplexity chat completions and return content + citations."""
    endpoint = base_url.rstrip("/") + "/chat/completions"
    request_model = _resolve_perplexity_request_model(base_url, model)

    body: dict[str, Any] = {
        "model": request_model,
        "messages": [{"role": "user", "content": query}],
    }
    recency = freshness_to_perplexity_recency(freshness)
    if recency:
        body["search_recency_filter"] = recency

    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        resp = await client.post(
            endpoint,
            json=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://openclaw.ai",
                "X-Title": "OpenClaw Web Search",
            },
        )
        if not resp.is_success:
            raise RuntimeError(
                f"Perplexity API error ({resp.status_code}): {resp.text[:500]}"
            )
        data = resp.json()

    content = (
        (data.get("choices") or [{}])[0]
        .get("message", {})
        .get("content", "No response")
    )
    citations = data.get("citations") or []
    return {
        "query": query,
        "provider": "perplexity",
        "model": model,
        "content": content,
        "citations": citations,
    }


async def run_grok_search(
    query: str,
    api_key: str,
    model: str = DEFAULT_GROK_MODEL,
    timeout_sec: float = DEFAULT_TIMEOUT_SECONDS,
    inline_citations: bool = False,
) -> dict:
    """Call xAI Grok responses endpoint with web_search tool."""
    body: dict[str, Any] = {
        "model": model,
        "input": [{"role": "user", "content": query}],
        "tools": [{"type": "web_search"}],
    }

    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        resp = await client.post(
            XAI_API_ENDPOINT,
            json=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        if not resp.is_success:
            raise RuntimeError(
                f"xAI API error ({resp.status_code}): {resp.text[:500]}"
            )
        data = resp.json()

    # Extract text from output array
    content = ""
    for item in data.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    content += part.get("text", "")
    if not content:
        content = data.get("text", "No response")

    citations = data.get("citations") or []
    return {
        "query": query,
        "provider": "grok",
        "model": model,
        "content": content,
        "citations": citations,
    }


async def run_duckduckgo_search(
    query: str,
    count: int = DEFAULT_SEARCH_COUNT,
) -> dict:
    """Search via DuckDuckGo using the ``ddgs`` library."""
    try:
        from ddgs import DDGS  # type: ignore[import]
    except ImportError:
        raise RuntimeError("ddgs not installed. Install with: pip install ddgs")

    with DDGS() as ddgs:
        raw = list(ddgs.text(query, max_results=count))

    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("href", ""),
            "description": r.get("body", ""),
        }
        for r in raw
    ]
    return {"query": query, "provider": "duckduckgo", "count": len(results), "results": results}


# ---------------------------------------------------------------------------
# Unified web-search runner
# ---------------------------------------------------------------------------


async def run_web_search(
    query: str,
    provider: str = "duckduckgo",
    count: int = DEFAULT_SEARCH_COUNT,
    api_key: str | None = None,
    country: str | None = None,
    search_lang: str | None = None,
    ui_lang: str | None = None,
    freshness: str | None = None,
    timeout_sec: float = DEFAULT_TIMEOUT_SECONDS,
    cache_ttl_sec: float = DEFAULT_CACHE_TTL_SECONDS,
    # Perplexity
    perplexity_base_url: str = DEFAULT_PERPLEXITY_BASE_URL,
    perplexity_model: str = DEFAULT_PERPLEXITY_MODEL,
    # Grok
    grok_model: str = DEFAULT_GROK_MODEL,
    grok_inline_citations: bool = False,
) -> dict:
    """Dispatch a search to the configured provider with result caching."""
    # Build cache key
    if provider == "brave":
        raw_key = f"brave:{query}:{count}:{country or 'default'}:{search_lang or 'default'}:{ui_lang or 'default'}:{freshness or 'default'}"
    elif provider == "perplexity":
        raw_key = f"perplexity:{query}:{perplexity_base_url}:{perplexity_model}:{freshness or 'default'}"
    elif provider == "grok":
        raw_key = f"grok:{query}:{grok_model}:{grok_inline_citations}"
    else:
        raw_key = f"ddg:{query}:{count}"

    cache_key = _normalize_cache_key(raw_key)
    cached = _read_cache(cache_key)
    if cached:
        return {**cached, "cached": True}

    start = time.monotonic()

    if provider == "brave":
        if not api_key:
            return {
                "error": "missing_brave_api_key",
                "message": "web_search (brave) requires BRAVE_API_KEY env var or tools.web.search.apiKey config.",
            }
        result = await run_brave_search(
            query, api_key, count=count,
            country=country, search_lang=search_lang, ui_lang=ui_lang,
            freshness=freshness, timeout_sec=timeout_sec,
        )

    elif provider == "perplexity":
        if not api_key:
            return {
                "error": "missing_perplexity_api_key",
                "message": "web_search (perplexity) requires PERPLEXITY_API_KEY or OPENROUTER_API_KEY.",
            }
        result = await run_perplexity_search(
            query, api_key, base_url=perplexity_base_url, model=perplexity_model,
            freshness=freshness, timeout_sec=timeout_sec,
        )

    elif provider == "grok":
        if not api_key:
            return {
                "error": "missing_xai_api_key",
                "message": "web_search (grok) requires XAI_API_KEY env var or tools.web.search.grok.apiKey config.",
            }
        result = await run_grok_search(
            query, api_key, model=grok_model, timeout_sec=timeout_sec,
            inline_citations=grok_inline_citations,
        )

    else:
        # duckduckgo (no API key needed)
        result = await run_duckduckgo_search(query, count=count)

    result["tookMs"] = int((time.monotonic() - start) * 1000)
    _write_cache(cache_key, result, cache_ttl_sec)
    return result


# ---------------------------------------------------------------------------
# Factory function — mirrors TS createWebSearchTool()
# ---------------------------------------------------------------------------


def create_web_search_tool(config: dict | None = None) -> "WebSearchTool | None":
    """Factory returning a configured :class:`WebSearchTool`, or ``None`` if disabled.

    Mirrors TS ``createWebSearchTool(options)``.
    """
    if config:
        enabled = (
            config.get("tools", {})
            .get("web", {})
            .get("search", {})
            .get("enabled", True)
        )
        if enabled is False:
            return None

    provider = resolve_search_provider(config)
    return WebSearchTool(provider=provider, config=config)


# ---------------------------------------------------------------------------
# AgentTool implementations
# ---------------------------------------------------------------------------


class WebSearchTool(AgentTool):
    """Search the web using the configured provider.

    Supports Brave, Perplexity, Grok, and DuckDuckGo.
    Mirrors TS ``createWebSearchTool()`` / ``runWebSearch()``.
    """

    def __init__(self, provider: str = "duckduckgo", config: dict | None = None) -> None:
        super().__init__()
        self.provider = provider
        self._config = config or {}
        self.name = "web_search"
        self.description = self._build_description(provider)

    @staticmethod
    def _build_description(provider: str) -> str:
        """
        Build dynamic description matching TS web-search.ts:1379-1418.
        
        Provides provider-specific guidance and parameter explanations.
        """
        provider_note = {
            "brave": "Using Brave Search API.",
            "perplexity": "Using Perplexity AI (Sonar model via OpenRouter or direct).",
            "grok": "Using xAI Grok.",
            "duckduckgo": "Using DuckDuckGo (free, no API key required).",
        }.get(provider, f"Using {provider}.")
        
        return (
            f"Search the web for real-time information. {provider_note} "
            "Supports parameters: query (required), count (1-10), country, search_lang, ui_lang, freshness. "
            "freshness filters by discovery time: 'pd' (past day), 'pw' (past week), 'pm' (past month), "
            "'py' (past year), or date range 'YYYY-MM-DDtoYYYY-MM-DD' (Brave only). "
            "search_lang is a 2-letter ISO code (e.g., 'en', 'de', 'fr', 'tr'). "
            "ui_lang is a locale code in language-region format (e.g., 'en-US', 'de-DE', 'fr-FR', 'tr-TR'). "
            "Results are cached for 5 minutes. Returns array of {title, url, description, age}."
        )
        if provider == "grok":
            return (
                "Search the web using xAI Grok. "
                "Returns AI-synthesized answers with citations from real-time web search."
            )
        if provider == "brave":
            return (
                "Search the web using Brave Search API. "
                "Supports region-specific and localised search via country and language "
                "parameters. Returns titles, URLs, and snippets for fast research."
            )
        return (
            "Search the web for information using DuckDuckGo. "
            "Returns titles, URLs, and snippets for fast research. "
            "Use this for finding articles, websites, news, and general information."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query string.",
                },
                "count": {
                    "type": "integer",
                    "description": f"Number of results to return (1-{MAX_SEARCH_COUNT}).",
                    "minimum": 1,
                    "maximum": MAX_SEARCH_COUNT,
                },
                "country": {
                    "type": "string",
                    "description": (
                        "2-letter country code for region-specific results "
                        "(e.g. 'DE', 'US', 'ALL'). Brave only."
                    ),
                },
                "search_lang": {
                    "type": "string",
                    "description": "ISO language code for search results (e.g. 'de', 'en'). Brave only.",
                },
                "ui_lang": {
                    "type": "string",
                    "description": "ISO language code for UI elements. Brave only.",
                },
                "freshness": {
                    "type": "string",
                    "description": (
                        "Filter results by discovery time. "
                        "Brave/Perplexity: 'pd', 'pw', 'pm', 'py', "
                        "or date range 'YYYY-MM-DDtoYYYY-MM-DD'."
                    ),
                },
            },
            "required": ["query"],
        }

    async def _execute_impl(self, params: dict[str, Any]) -> ToolResult:
        """Execute web search via the configured provider."""
        query = (params.get("query") or "").strip()
        if not query:
            return ToolResult(success=False, content="", error="No query provided")

        count = _resolve_search_count(params.get("count"), DEFAULT_SEARCH_COUNT)
        country = params.get("country")
        search_lang = params.get("search_lang")
        ui_lang = params.get("ui_lang")
        freshness_raw = params.get("freshness")
        freshness = normalize_freshness(freshness_raw) if freshness_raw else None

        if freshness_raw and not freshness:
            return ToolResult(
                success=False,
                content="",
                error=(
                    "Invalid freshness value. Must be one of pd, pw, pm, py, "
                    "or YYYY-MM-DDtoYYYY-MM-DD range."
                ),
            )

        # Resolve provider-specific config
        cfg = self._config
        perplexity_cfg = {}
        grok_cfg: dict = {}
        if cfg:
            search_section = cfg.get("tools", {}).get("web", {}).get("search", {}) or {}
            perplexity_cfg = search_section.get("perplexity", {}) or {}
            grok_cfg = search_section.get("grok", {}) or {}

        # Resolve API key
        api_key: str | None = None
        if self.provider == "brave":
            api_key = _resolve_brave_api_key(cfg)
        elif self.provider == "perplexity":
            api_key, source = _resolve_perplexity_api_key(cfg)
        elif self.provider == "grok":
            api_key = _resolve_grok_api_key(cfg)

        try:
            # Build extra perplexity kwargs
            extra_kwargs: dict[str, Any] = {}
            if self.provider == "perplexity":
                _, source = _resolve_perplexity_api_key(cfg)
                extra_kwargs["perplexity_base_url"] = resolve_perplexity_base_url(
                    perplexity_cfg, source, api_key
                )
                extra_kwargs["perplexity_model"] = resolve_perplexity_model(perplexity_cfg)
            elif self.provider == "grok":
                extra_kwargs["grok_model"] = _resolve_grok_model(cfg)
                extra_kwargs["grok_inline_citations"] = grok_cfg.get("inlineCitations", False)

            result = await run_web_search(
                query=query,
                provider=self.provider,
                count=count,
                api_key=api_key,
                country=country,
                search_lang=search_lang,
                ui_lang=ui_lang,
                freshness=freshness,
                **extra_kwargs,
            )

            if "error" in result:
                return ToolResult(
                    success=False, content="", error=result.get("message", result["error"])
                )

            # Format output
            content = _format_search_result(result)
            return ToolResult(
                success=True,
                content=content,
                metadata=result,
            )

        except Exception as exc:
            logger.error(f"Web search error ({self.provider}): {exc}", exc_info=True)
            return ToolResult(success=False, content="", error=str(exc))


def _format_search_result(result: dict) -> str:
    """Format search result as readable text."""
    provider = result.get("provider", "")

    # Perplexity / Grok: content + citations
    if provider in ("perplexity", "grok"):
        content = result.get("content", "")
        citations = result.get("citations") or []
        if citations:
            refs = "\n".join(f"  [{i+1}] {c}" for i, c in enumerate(citations))
            return f"{content}\n\nSources:\n{refs}"
        return content

    # Brave / DuckDuckGo: list of results
    results = result.get("results") or []
    if not results:
        return "No results found for this query."

    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "No title")
        url = r.get("url", "")
        description = r.get("description", "")
        lines.append(f"{i}. **{title}**\n   URL: {url}\n   {description}\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# WebFetchTool — with HTML-to-text extraction
# ---------------------------------------------------------------------------


class WebFetchTool(AgentTool):
    """Fetch a web page and return its content as plain text.

    Uses ``html2text`` or ``BeautifulSoup`` for HTML extraction when available,
    aligning with TS ``web-fetch.ts``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.name = "web_fetch"
        self.description = (
            "Fetch the content of a URL. Returns the page as plain text with "
            "Markdown formatting when possible. Useful for reading documentation, "
            "articles, or any web page content."
        )

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "max_length": {
                    "type": "integer",
                    "description": "Maximum number of characters to return (default: 50000)",
                    "default": 50000,
                },
            },
            "required": ["url"],
        }

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        url = (params.get("url") or "").strip()
        max_length = int(params.get("max_length") or 50000)

        if not url:
            return ToolResult(success=False, content="", error="No URL provided")

        try:
            async with httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; OpenClaw/1.0; +https://openclaw.ai)"
                    )
                },
            ) as client:
                response = await client.get(url)
                response.raise_for_status()

            content_type = response.headers.get("content-type", "").lower()
            final_url = str(response.url)

            if "html" in content_type:
                text = _html_to_text(response.text)
            elif "json" in content_type:
                text = response.text
            elif "text" in content_type:
                text = response.text
            else:
                return ToolResult(
                    success=True,
                    content=f"Fetched {len(response.content)} bytes of {content_type}",
                    metadata={
                        "status_code": response.status_code,
                        "content_type": content_type,
                        "size": len(response.content),
                        "url": final_url,
                    },
                )

            if len(text) > max_length:
                text = text[:max_length] + f"\n\n[Content truncated at {max_length} chars]"

            return ToolResult(
                success=True,
                content=text,
                metadata={
                    "status_code": response.status_code,
                    "content_type": content_type,
                    "url": final_url,
                    "length": len(text),
                },
            )

        except httpx.HTTPStatusError as exc:
            return ToolResult(
                success=False,
                content="",
                error=f"HTTP {exc.response.status_code}: {str(exc)}",
            )
        except Exception as exc:
            logger.error(f"Web fetch error: {exc}", exc_info=True)
            return ToolResult(success=False, content="", error=str(exc))


def _html_to_text(html: str) -> str:
    """Convert HTML to plain text.

    Tries ``html2text`` first (Markdown output), then ``BeautifulSoup``,
    then naive tag stripping as a last resort.
    """
    # Try html2text (best Markdown output, aligns with TS readability approach)
    try:
        import html2text  # type: ignore[import]

        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.body_width = 0  # No line wrapping
        return h.handle(html)
    except ImportError:
        pass

    # Try BeautifulSoup
    try:
        from bs4 import BeautifulSoup  # type: ignore[import]

        soup = BeautifulSoup(html, "html.parser")
        # Remove script/style tags
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except ImportError:
        pass

    # Naive tag stripping
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
