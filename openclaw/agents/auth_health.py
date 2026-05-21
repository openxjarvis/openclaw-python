"""Auth health summary — mirrors TS models.authStatus extended shape."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CACHE_TTL_S = 60
_cache: dict[str, Any] | None = None
_cache_ts: float = 0.0


def _get_state_dir() -> Path:
    try:
        from openclaw.config.paths import resolve_state_dir
        return Path(resolve_state_dir())
    except Exception:
        return Path.home() / ".openclaw"


def _read_auth_profiles() -> dict[str, Any]:
    state_dir = _get_state_dir()
    candidates = [
        state_dir / "auth-profiles.json",
        state_dir / "auth_profiles.json",
        state_dir / "config" / "auth-profiles.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def _classify_profile_status(profile: dict[str, Any]) -> str:
    """Return one of: ok, expiring, expired, missing, static."""
    if not profile:
        return "missing"
    key_type = profile.get("keyType") or profile.get("type") or ""
    if key_type == "static":
        return "static"
    expiry = profile.get("expiresAt") or profile.get("expiry") or profile.get("expiresAtMs")
    if expiry is None:
        token = profile.get("token") or profile.get("apiKey") or profile.get("key")
        if token:
            return "ok"
        return "missing"
    try:
        if isinstance(expiry, (int, float)):
            exp_ts = int(expiry) / 1000.0 if expiry > 1e12 else float(expiry)
        else:
            from datetime import datetime
            exp_ts = datetime.fromisoformat(str(expiry)).timestamp()
    except Exception:
        return "ok"
    now = time.time()
    if exp_ts < now:
        return "expired"
    if exp_ts - now < 7 * 24 * 3600:
        return "expiring"
    return "ok"


def build_auth_health_summary() -> dict[str, Any]:
    """Build auth health summary with 60-second cache.

    Returns:
        {ts, providers: [{provider, status, profiles[], expiry}]}
    """
    global _cache, _cache_ts
    now = time.time()
    if _cache is not None and now - _cache_ts < _CACHE_TTL_S:
        return _cache

    auth_data = _read_auth_profiles()
    providers: list[dict[str, Any]] = []

    # Check well-known environment variables
    provider_env_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google": "GOOGLE_API_KEY",
        "groq": "GROQ_API_KEY",
        "cerebras": "CEREBRAS_API_KEY",
        "xai": "XAI_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "moonshot": "MOONSHOT_API_KEY",
        "github-copilot": "GITHUB_COPILOT_TOKEN",
        "bedrock": "AWS_ACCESS_KEY_ID",
        "azure-openai": "AZURE_OPENAI_API_KEY",
    }

    seen: set[str] = set()

    # Process profiles from auth-profiles.json
    profiles_by_provider: dict[str, list[dict[str, Any]]] = {}
    if isinstance(auth_data, dict):
        for provider_id, provider_data in auth_data.items():
            if isinstance(provider_data, list):
                profiles_by_provider[provider_id] = provider_data
            elif isinstance(provider_data, dict):
                profiles_by_provider[provider_id] = [provider_data]

    for provider_id, profile_list in profiles_by_provider.items():
        seen.add(provider_id)
        statuses = [_classify_profile_status(p) for p in profile_list]
        # Worst status wins: expired > expiring > missing > ok > static
        rank = {"expired": 0, "expiring": 1, "missing": 2, "ok": 3, "static": 4}
        best_status = min(statuses, key=lambda s: rank.get(s, 99)) if statuses else "missing"
        expiry_vals = [p.get("expiresAt") or p.get("expiresAtMs") for p in profile_list if p.get("expiresAt") or p.get("expiresAtMs")]
        providers.append(
            {
                "provider": provider_id,
                "status": best_status,
                "profiles": profile_list,
                "expiry": expiry_vals[0] if expiry_vals else None,
            }
        )

    # Add env-var based providers not already in profiles
    for provider_id, env_key in provider_env_map.items():
        if provider_id in seen:
            continue
        val = os.environ.get(env_key, "")
        status = "ok" if val else "missing"
        providers.append(
            {
                "provider": provider_id,
                "status": status,
                "profiles": [{"keyType": "env", "env": env_key}] if val else [],
                "expiry": None,
            }
        )

    result = {
        "ts": int(now * 1000),
        "providers": providers,
    }
    _cache = result
    _cache_ts = now
    return result


def invalidate_auth_health_cache() -> None:
    """Invalidate the 60-second cache."""
    global _cache, _cache_ts
    _cache = None
    _cache_ts = 0.0
