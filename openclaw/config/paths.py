"""
Configuration and state directory path resolution.

Matches openclaw/src/config/paths.ts
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants (matches TS lines 21-24)
# ---------------------------------------------------------------------------

LEGACY_STATE_DIRNAMES: List[str] = [".clawdbot", ".moldbot", ".moltbot"]
NEW_STATE_DIRNAME = ".openclaw"
CONFIG_FILENAME = "openclaw.json"
LEGACY_CONFIG_FILENAMES: List[str] = ["clawdbot.json", "moldbot.json", "moltbot.json"]
OAUTH_FILENAME = "oauth.json"
DEFAULT_GATEWAY_PORT = 18789


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_homedir() -> str:
    return str(Path.home())


def _resolve_user_path(input_str: str, env: Optional[Dict[str, str]] = None, homedir: Optional[Callable[[], str]] = None) -> str:
    """Expand ~ prefix and resolve to absolute path."""
    trimmed = input_str.strip()
    if not trimmed:
        return trimmed
    if trimmed.startswith("~"):
        home = (homedir or _default_homedir)()
        trimmed = home + trimmed[1:]
    return str(Path(trimmed).resolve())


def resolve_user_path(input_str: str) -> str:
    """Public API: Expand ~ prefix and resolve to absolute path.
    
    Mirrors TS resolveUserPath() from utils.ts.
    """
    return _resolve_user_path(input_str)


# ---------------------------------------------------------------------------
# Nix mode detection (matches TS resolveIsNixMode)
# ---------------------------------------------------------------------------

def resolve_is_nix_mode(env: Optional[Dict[str, str]] = None) -> bool:
    """Return True when running under Nix (OPENCLAW_NIX_MODE=1)."""
    e = env if env is not None else dict(os.environ)
    return e.get("OPENCLAW_NIX_MODE") == "1"


is_nix_mode = resolve_is_nix_mode()


# ---------------------------------------------------------------------------
# State directory resolution (matches TS resolveStateDir and helpers)
# ---------------------------------------------------------------------------

def _legacy_state_dirs(homedir: Callable[[], str] = _default_homedir) -> List[str]:
    home = homedir()
    return [str(Path(home) / dirname) for dirname in LEGACY_STATE_DIRNAMES]


def _new_state_dir(homedir: Callable[[], str] = _default_homedir) -> str:
    return str(Path(homedir()) / NEW_STATE_DIRNAME)


def resolve_legacy_state_dir(homedir: Callable[[], str] = _default_homedir) -> str:
    """Return the first legacy state directory path (or new dir as fallback)."""
    dirs = _legacy_state_dirs(homedir)
    return dirs[0] if dirs else _new_state_dir(homedir)


def resolve_legacy_state_dirs(homedir: Callable[[], str] = _default_homedir) -> List[str]:
    """Return all legacy state directory paths."""
    return _legacy_state_dirs(homedir)


def resolve_new_state_dir(homedir: Callable[[], str] = _default_homedir) -> str:
    """Return the new (~/.openclaw) state directory path."""
    return _new_state_dir(homedir)


def resolve_state_dir(
    env: Optional[Dict[str, str]] = None,
    homedir: Optional[Callable[[], str]] = None,
) -> Path:
    """
    Resolve the active state directory.

    Priority:
    1. OPENCLAW_STATE_DIR / CLAWDBOT_STATE_DIR env var override
    2. ~/.openclaw (if it exists)
    3. First existing legacy dir (.clawdbot, .moldbot, .moltbot)
    4. ~/.openclaw (default fallback)

    Matches TS resolveStateDir().
    """
    e = env if env is not None else dict(os.environ)
    hd = homedir or _default_homedir

    override = (e.get("OPENCLAW_STATE_DIR") or "").strip() or (e.get("CLAWDBOT_STATE_DIR") or "").strip()
    if override:
        return Path(_resolve_user_path(override, e, hd))

    new_dir = _new_state_dir(hd)
    if Path(new_dir).exists():
        return Path(new_dir)

    for legacy_dir in _legacy_state_dirs(hd):
        try:
            if Path(legacy_dir).exists():
                return Path(legacy_dir)
        except Exception:
            pass

    return Path(new_dir)


# ---------------------------------------------------------------------------
# Config path resolution (matches TS resolveConfigPath and helpers)
# ---------------------------------------------------------------------------

def resolve_canonical_config_path(
    env: Optional[Dict[str, str]] = None,
    state_dir: Optional[str] = None,
) -> str:
    """
    Resolve the canonical config path (may not exist yet).

    Checks OPENCLAW_CONFIG_PATH / CLAWDBOT_CONFIG_PATH overrides first.
    Defaults to <state_dir>/openclaw.json.

    Matches TS resolveCanonicalConfigPath().
    """
    e = env if env is not None else dict(os.environ)
    override = (e.get("OPENCLAW_CONFIG_PATH") or "").strip() or (e.get("CLAWDBOT_CONFIG_PATH") or "").strip()
    if override:
        return _resolve_user_path(override, e)
    sd = state_dir if state_dir is not None else resolve_state_dir(e)
    return str(Path(sd) / CONFIG_FILENAME)


def resolve_default_config_candidates(
    env: Optional[Dict[str, str]] = None,
    homedir: Optional[Callable[[], str]] = None,
) -> List[str]:
    """
    Return ordered list of candidate config file paths to check for existence.

    Order: explicit config path → state-dir-derived paths → new default.

    Matches TS resolveDefaultConfigCandidates().
    """
    e = env if env is not None else dict(os.environ)
    hd = homedir or _default_homedir

    explicit = (e.get("OPENCLAW_CONFIG_PATH") or "").strip() or (e.get("CLAWDBOT_CONFIG_PATH") or "").strip()
    if explicit:
        return [_resolve_user_path(explicit, e, hd)]

    candidates: List[str] = []

    state_override = (e.get("OPENCLAW_STATE_DIR") or "").strip() or (e.get("CLAWDBOT_STATE_DIR") or "").strip()
    if state_override:
        resolved = _resolve_user_path(state_override, e, hd)
        candidates.append(str(Path(resolved) / CONFIG_FILENAME))
        candidates.extend(str(Path(resolved) / name) for name in LEGACY_CONFIG_FILENAMES)

    default_dirs = [_new_state_dir(hd)] + _legacy_state_dirs(hd)
    for d in default_dirs:
        candidates.append(str(Path(d) / CONFIG_FILENAME))
        candidates.extend(str(Path(d) / name) for name in LEGACY_CONFIG_FILENAMES)

    return candidates


def resolve_config_path_candidate(
    env: Optional[Dict[str, str]] = None,
    homedir: Optional[Callable[[], str]] = None,
) -> str:
    """
    Resolve the active config path — prefers existing files.

    Matches TS resolveConfigPathCandidate().
    """
    e = env if env is not None else dict(os.environ)
    hd = homedir or _default_homedir
    for candidate in resolve_default_config_candidates(e, hd):
        try:
            if Path(candidate).exists():
                return candidate
        except Exception:
            pass
    return resolve_canonical_config_path(e, resolve_state_dir(e, hd))


def resolve_config_path(
    env: Optional[Dict[str, str]] = None,
    state_dir: Optional[str] = None,
    homedir: Optional[Callable[[], str]] = None,
) -> str:
    """
    Resolve the config path with full fallback logic.

    Matches TS resolveConfigPath().
    """
    e = env if env is not None else dict(os.environ)
    hd = homedir or _default_homedir

    override = (e.get("OPENCLAW_CONFIG_PATH") or "").strip()
    if override:
        return _resolve_user_path(override, e, hd)

    sd = state_dir if state_dir is not None else resolve_state_dir(e, hd)
    candidates = [str(Path(sd) / CONFIG_FILENAME)] + [str(Path(sd) / name) for name in LEGACY_CONFIG_FILENAMES]

    for candidate in candidates:
        try:
            if Path(candidate).exists():
                return candidate
        except Exception:
            pass

    state_override = (e.get("OPENCLAW_STATE_DIR") or "").strip()
    if state_override:
        return str(Path(sd) / CONFIG_FILENAME)

    default_state_dir = resolve_state_dir(e, hd)
    if str(Path(sd).resolve()) == str(Path(default_state_dir).resolve()):
        return resolve_config_path_candidate(e, hd)

    return str(Path(sd) / CONFIG_FILENAME)


# Pre-resolved defaults (module-level, matches TS CONFIG_PATH / STATE_DIR)
STATE_DIR = resolve_state_dir()
CONFIG_PATH = resolve_config_path_candidate()


# ---------------------------------------------------------------------------
# OpenClaw temp directory (matches TS resolvePreferredOpenClawTmpDir)
# ---------------------------------------------------------------------------

def resolve_preferred_openclaw_tmp_dir() -> str:
    """
    Resolve the preferred OpenClaw temporary directory.
    
    Mirrors TS resolvePreferredOpenClawTmpDir() from src/infra/tmp-openclaw-dir.ts.
    
    Returns {tmpdir}/openclaw-{uid} or {tmpdir}/openclaw on Windows.
    """
    base = tempfile.gettempdir()
    try:
        uid = os.getuid()
        suffix = f"openclaw-{uid}"
    except AttributeError:
        suffix = "openclaw"
    return str(Path(base) / suffix)


# ---------------------------------------------------------------------------
# Sandbox workspace root (matches TS DEFAULT_SANDBOX_WORKSPACE_ROOT)
# ---------------------------------------------------------------------------

def resolve_default_sandbox_workspace_root(
    env: Optional[Dict[str, str]] = None,
    homedir: Optional[Callable[[], str]] = None,
) -> Path:
    """
    Resolve the default sandbox workspace root directory.

    Mirrors TS DEFAULT_SANDBOX_WORKSPACE_ROOT = path.join(STATE_DIR, "sandboxes")
    from openclaw/src/agents/sandbox/constants.ts.

    Per-session/agent sandbox subdirs are placed here (not inside workspace/).
    """
    state_dir = resolve_state_dir(env=env, homedir=homedir)
    return Path(state_dir) / "sandboxes"


# ---------------------------------------------------------------------------
# Gateway lock directory (matches TS resolveGatewayLockDir)
# ---------------------------------------------------------------------------

def resolve_gateway_lock_dir(tmpdir: Optional[Callable[[], str]] = None) -> str:
    """
    Resolve the gateway lock directory (ephemeral, in tmpdir).

    Matches TS resolveGatewayLockDir().
    """
    base = (tmpdir or tempfile.gettempdir)()
    try:
        uid = os.getuid()
        suffix = f"openclaw-{uid}"
    except AttributeError:
        suffix = "openclaw"
    return str(Path(base) / suffix)


# ---------------------------------------------------------------------------
# OAuth paths (matches TS resolveOAuthDir / resolveOAuthPath)
# ---------------------------------------------------------------------------

def resolve_oauth_dir(
    env: Optional[Dict[str, str]] = None,
    state_dir: Optional[str] = None,
) -> str:
    """
    Resolve the OAuth credentials directory.

    Priority: OPENCLAW_OAUTH_DIR → <state_dir>/credentials

    Matches TS resolveOAuthDir().
    """
    e = env if env is not None else dict(os.environ)
    override = (e.get("OPENCLAW_OAUTH_DIR") or "").strip()
    if override:
        return _resolve_user_path(override, e)
    sd = state_dir if state_dir is not None else resolve_state_dir(e)
    return str(Path(sd) / "credentials")


def resolve_oauth_path(
    env: Optional[Dict[str, str]] = None,
    state_dir: Optional[str] = None,
) -> str:
    """Return full path to oauth.json. Matches TS resolveOAuthPath()."""
    return str(Path(resolve_oauth_dir(env, state_dir)) / OAUTH_FILENAME)


# ---------------------------------------------------------------------------
# Gateway port resolution (matches TS resolveGatewayPort)
# ---------------------------------------------------------------------------

def resolve_gateway_port(
    cfg: Optional[object] = None,
    env: Optional[Dict[str, str]] = None,
) -> int:
    """
    Resolve the gateway port number.

    Priority: env var → config → DEFAULT_GATEWAY_PORT (18789)

    Matches TS resolveGatewayPort().
    """
    e = env if env is not None else dict(os.environ)
    env_raw = (e.get("OPENCLAW_GATEWAY_PORT") or "").strip() or (e.get("CLAWDBOT_GATEWAY_PORT") or "").strip()
    if env_raw:
        try:
            parsed = int(env_raw)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    if cfg is not None:
        config_port = None
        if hasattr(cfg, "gateway") and cfg.gateway:  # type: ignore[union-attr]
            config_port = getattr(cfg.gateway, "port", None)  # type: ignore[union-attr]
        elif isinstance(cfg, dict):
            gw = cfg.get("gateway") or {}
            config_port = gw.get("port") if isinstance(gw, dict) else None
        if isinstance(config_port, (int, float)) and config_port > 0:
            return int(config_port)
    return DEFAULT_GATEWAY_PORT


# ---------------------------------------------------------------------------
# Legacy helpers (kept for backward compat with other modules)
# ---------------------------------------------------------------------------

def get_openclaw_data_dir() -> Path:
    """Get OpenClaw data directory (e.g. ~/.openclaw/data)."""
    override = os.environ.get("OPENCLAW_DATA_DIR")
    if override:
        return Path(override)
    return resolve_state_dir() / "data"


def get_openclaw_config_dir() -> Path:
    """Get OpenClaw config directory (e.g. ~/.openclaw)."""
    override = os.environ.get("OPENCLAW_CONFIG_DIR")
    if override:
        return Path(override)
    return resolve_state_dir()
