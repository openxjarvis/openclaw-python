"""
API key and OAuth credential storage — mirrors TypeScript auth-profiles/store.ts.

Storage location: ~/.openclaw/agents/<agentId>/agent/auth-profiles.json
Default agent:   ~/.openclaw/agents/main/agent/auth-profiles.json

JSON structure (compatible with TS auth-profiles.json):
{
  "version": 1,
  "profiles": {
    "google:default":    {"type": "api_key", "provider": "google",    "key": "AIza..."},
    "anthropic:default": {"type": "api_key", "provider": "anthropic", "key": "sk-ant-..."},
    "openai:default":    {"type": "api_key", "provider": "openai",    "key": "sk-..."}
  },
  "order": {"google": ["google:default"]},
  "lastGood": {},
  "usageStats": {}
}

TS references:
  src/agents/auth-profiles/store.ts
  src/agents/auth-profiles/paths.ts
  src/agents/auth-profiles/constants.ts
  src/agents/agent-paths.ts  (resolveOpenClawAgentDir)
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
from openclaw.config.paths import resolve_state_dir

logger = logging.getLogger(__name__)

AUTH_PROFILE_FILENAME = "auth-profiles.json"
LEGACY_AUTH_FILENAME = "auth.json"          # pi_coding_agent legacy format
AUTH_STORE_VERSION = 1
DEFAULT_AGENT_ID = "main"


# ---------------------------------------------------------------------------
# Path resolution  (mirrors agent-paths.ts / paths.ts)
# ---------------------------------------------------------------------------

def resolve_state_dir() -> Path:
    """Return ~/.openclaw (or $OPENCLAW_STATE_DIR override).  Mirrors resolveStateDir()."""
    override = (
        os.environ.get("OPENCLAW_STATE_DIR", "").strip()
        or os.environ.get("CLAWDBOT_STATE_DIR", "").strip()
    )
    if override:
        return Path(override).expanduser()
    # Use the imported resolve_state_dir from paths module
    from openclaw.config.paths import resolve_state_dir as _resolve
    return _resolve()


def resolve_agent_dir(agent_id: str | None = None) -> Path:
    """Return ~/.openclaw/agents/<agent_id>/agent.  Mirrors resolveOpenClawAgentDir()."""
    override = (
        os.environ.get("OPENCLAW_AGENT_DIR", "").strip()
        or os.environ.get("PI_CODING_AGENT_DIR", "").strip()
    )
    if override:
        return Path(override).expanduser()
    state_dir = resolve_state_dir()
    aid = agent_id or DEFAULT_AGENT_ID
    return state_dir / "agents" / aid / "agent"


def resolve_auth_store_path(agent_id: str | None = None) -> Path:
    """Return path to auth-profiles.json.  Mirrors resolveAuthStorePath()."""
    return resolve_agent_dir(agent_id) / AUTH_PROFILE_FILENAME


def resolve_legacy_auth_store_path(agent_id: str | None = None) -> Path:
    """Return path to legacy auth.json (pi_coding_agent format)."""
    return resolve_agent_dir(agent_id) / LEGACY_AUTH_FILENAME


# ---------------------------------------------------------------------------
# Store I/O  (mirrors ensureAuthProfileStore / loadAuthProfileStore / saveAuthProfileStore)
# ---------------------------------------------------------------------------

def _empty_store() -> dict[str, Any]:
    return {
        "version": AUTH_STORE_VERSION,
        "profiles": {},
        "order": {},
        "lastGood": {},
        "usageStats": {},
    }


def load_auth_profile_store(agent_id: str | None = None) -> dict[str, Any]:
    """Load auth-profiles.json, creating it if absent.  Mirrors loadAuthProfileStore()."""
    path = resolve_auth_store_path(agent_id)
    if not path.exists():
        return _empty_store()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "profiles" not in raw:
            return _empty_store()
        return raw
    except Exception as exc:
        logger.warning("Could not load auth-profiles.json: %s", exc)
        return _empty_store()


def save_auth_profile_store(store: dict[str, Any], agent_id: str | None = None) -> None:
    """Save auth-profiles.json with restricted permissions.  Mirrors saveAuthProfileStore()."""
    path = resolve_auth_store_path(agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(path), flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
    except Exception:
        os.close(fd)
        raise


# ---------------------------------------------------------------------------
# Profile helpers  (mirrors upsertAuthProfile / listProfilesForProvider)
# ---------------------------------------------------------------------------

def upsert_auth_profile(
    profile_id: str,
    credential: dict[str, Any],
    agent_id: str | None = None,
) -> None:
    """Write a single profile to auth-profiles.json.  Mirrors upsertAuthProfile()."""
    store = load_auth_profile_store(agent_id)
    store.setdefault("profiles", {})[profile_id] = credential

    provider = credential.get("provider", profile_id.split(":")[0])
    order: dict[str, list[str]] = store.setdefault("order", {})
    order.setdefault(provider, [])
    if profile_id not in order[provider]:
        order[provider].insert(0, profile_id)

    save_auth_profile_store(store, agent_id)


def get_api_key(provider: str, agent_id: str | None = None) -> str | None:
    """Return stored API key for *provider*, or None.  Mirrors resolveApiKeyForProfile()."""
    store = load_auth_profile_store(agent_id)
    profiles: dict[str, Any] = store.get("profiles", {})
    order: list[str] = store.get("order", {}).get(provider, [])

    # Try profiles in preferred order, then scan all profiles for the provider
    candidates = list(dict.fromkeys(order + [k for k in profiles if k.startswith(f"{provider}:")]))
    for pid in candidates:
        cred = profiles.get(pid)
        if not cred:
            continue
        if cred.get("type") == "api_key" and cred.get("key"):
            return cred["key"]
    return None


def set_api_key(
    provider: str,
    key: str,
    profile_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    """Persist an API key.  Mirrors setGeminiApiKey / setAnthropicApiKey pattern."""
    pid = profile_id or f"{provider}:default"
    upsert_auth_profile(
        pid,
        {"type": "api_key", "provider": provider, "key": key},
        agent_id=agent_id,
    )


# ---------------------------------------------------------------------------
# Env-var resolution  (mirrors live-auth-keys.ts collectProviderApiKeys)
# ---------------------------------------------------------------------------

_PROVIDER_ENV_VARS: dict[str, list[str]] = {
    "google":    ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai":    ["OPENAI_API_KEY"],
    "moonshot":  ["MOONSHOT_API_KEY", "KIMI_CODE_API_KEY"],
    "kimi-coding": ["KIMI_API_KEY", "KIMI_CODE_API_KEY"],
    "kimi":      ["KIMI_API_KEY", "KIMI_CODE_API_KEY"],
    "deepseek":  ["DEEPSEEK_API_KEY"],
    "groq":      ["GROQ_API_KEY"],
    "mistral":   ["MISTRAL_API_KEY"],
    "xai":       ["XAI_API_KEY"],
    "together":  ["TOGETHER_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "huggingface": ["HUGGINGFACE_API_KEY", "HF_API_KEY", "HF_TOKEN"],
    "cerebras":  ["CEREBRAS_API_KEY"],
    "zai":       ["ZAI_API_KEY", "ZHIPU_API_KEY"],
    "zhipu":     ["ZHIPU_API_KEY", "ZAI_API_KEY"],
    "minimax":   ["MINIMAX_API_KEY"],
    "minimax-cn": ["MINIMAX_CN_API_KEY"],
    "qwen":      ["DASHSCOPE_API_KEY", "QWEN_API_KEY"],
    "ollama":    ["OLLAMA_API_KEY"],
}


def resolve_api_key(provider: str, agent_id: str | None = None) -> str | None:
    """Resolve API key: auth-profiles.json → env vars.  Mirrors resolveApiKeyForProvider()."""
    stored = get_api_key(provider, agent_id)
    if stored:
        return stored
    for env_name in _PROVIDER_ENV_VARS.get(provider, []):
        val = os.environ.get(env_name, "").strip()
        if val:
            return val
    return None


# ---------------------------------------------------------------------------
# Ensure OPENCLAW_AGENT_DIR / PI_CODING_AGENT_DIR are set
# (mirrors ensureOpenClawAgentEnv in agent-paths.ts)
# ---------------------------------------------------------------------------

def ensure_agent_env(agent_id: str | None = None) -> str:
    """Set OPENCLAW_AGENT_DIR and PI_CODING_AGENT_DIR env vars if not already set.
    This ensures pi_coding_agent finds auth-profiles.json at the right location.
    Mirrors ensureOpenClawAgentEnv().
    """
    agent_dir = str(resolve_agent_dir(agent_id))
    if not os.environ.get("OPENCLAW_AGENT_DIR"):
        os.environ["OPENCLAW_AGENT_DIR"] = agent_dir
    if not os.environ.get("PI_CODING_AGENT_DIR"):
        os.environ["PI_CODING_AGENT_DIR"] = agent_dir
    return agent_dir


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------

def migrate_pi_auth_to_openclaw(agent_id: str | None = None) -> bool:
    """
    One-time migration: copy API keys from ~/.pi/agent/auth.json (old pi_coding_agent
    format) to ~/.openclaw/agents/main/agent/auth-profiles.json (TS-aligned format).
    Returns True if any keys were migrated.
    """
    pi_auth = Path.home() / ".pi" / "agent" / "auth.json"
    if not pi_auth.exists():
        return False
    try:
        data = json.loads(pi_auth.read_text(encoding="utf-8"))
        api_keys: dict[str, str] = data.get("api_keys", {})
        migrated = False
        for provider, key in api_keys.items():
            if key and not get_api_key(provider, agent_id):
                set_api_key(provider, key, agent_id=agent_id)
                migrated = True
                logger.info("Migrated %s API key from ~/.pi/agent/auth.json", provider)
        return migrated
    except Exception as exc:
        logger.debug("Migration from ~/.pi/agent/auth.json failed: %s", exc)
        return False
