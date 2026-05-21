"""Secret target registry — mirrors TS src/secrets/target-registry-data.ts (core entries).

Provides ``is_known_secret_target_id`` for gateway ``secrets.resolve`` validation.
"""
from __future__ import annotations

import re
from typing import Any

# Core openclaw.json target IDs from TS CORE_SECRET_TARGET_REGISTRY
CORE_SECRET_TARGET_IDS: frozenset[str] = frozenset({
    "auth-profiles.key.key",
    "auth-profiles.token.token",
    "agents.defaults.memorySearch.remote.apiKey",
    "agents.list[].memorySearch.remote.apiKey",
    "cron.webhookToken",
    "gateway.auth.token",
    "gateway.auth.password",
    "gateway.remote.password",
    "gateway.remote.token",
    "messages.tts.providers.*.apiKey",
    "agents.list[].tts.providers.*.apiKey",
    "models.providers.*.apiKey",
    "models.providers.*.headers.*",
    "models.providers.*.request.headers.*",
    "models.providers.*.request.auth.token",
    "models.providers.*.request.auth.value",
    "models.providers.*.request.proxy.tls.ca",
    "models.providers.*.request.proxy.tls.cert",
    "models.providers.*.request.proxy.tls.key",
    "models.providers.*.request.proxy.tls.passphrase",
    "models.providers.*.request.tls.ca",
    "models.providers.*.request.tls.cert",
    "models.providers.*.request.tls.key",
    "models.providers.*.request.tls.passphrase",
    "skills.entries.*.apiKey",
    "talk.providers.*.apiKey",
    "tools.web.search.apiKey",
})

# Channel secret targets follow channels.<id>.<field> patterns
_CHANNEL_TARGET_RE = re.compile(
    r"^channels\.[a-zA-Z0-9_-]+\.(token|apiKey|api_key|botToken|bot_token|secret|webhookSecret)$"
)


def is_known_secret_target_id(target_id: str) -> bool:
    """Return True if *target_id* is a known secret target.

    Matches TS ``isKnownSecretTargetId()`` for core registry entries and
    standard channel secret paths.
    """
    tid = (target_id or "").strip()
    if not tid:
        return False
    if tid in CORE_SECRET_TARGET_IDS:
        return True
    if _CHANNEL_TARGET_RE.match(tid):
        return True
    return False


def list_known_secret_target_ids() -> list[str]:
    """Return sorted core target IDs (for diagnostics/UI)."""
    return sorted(CORE_SECRET_TARGET_IDS)


def target_id_matches_path(target_id: str, path: str) -> bool:
    """Return True if config *path* matches registry *target_id* pattern."""
    tid = target_id.strip()
    if tid == path:
        return True
    # Wildcard: models.providers.*.apiKey -> models.providers.openai.apiKey
    if ".*" in tid:
        pattern = "^" + re.escape(tid).replace(r"\.\*", r"\.[^.]+") + "$"
        return bool(re.match(pattern, path))
    if "[]" in tid:
        base = tid.replace("[]", "")
        return path.startswith(base.replace("[]", ""))
    return path.startswith(tid + ".") or tid.startswith(path)
