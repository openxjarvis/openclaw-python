"""
.env file loading — mirrors TypeScript infra/dotenv.ts.

Priority (highest → lowest):
  1. Process environment (already set — never overridden)
  2. CWD .env   (project-level, developer convenience)
  3. ~/.openclaw/.env  (global user-level, override=False)

TS reference: src/infra/dotenv.ts
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from ..config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


def load_dot_env(quiet: bool = True) -> None:
    """Load .env files in the same priority order as TS loadDotEnv().

    Mirrors:
      dotenv.config({ quiet })                              # CWD .env
      dotenv.config({ path: globalEnvPath, override: false })  # ~/.openclaw/.env

    This function is idempotent — already-set env vars are never overridden.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        if not quiet:
            logger.warning("python-dotenv not installed; .env files will not be loaded")
        return

    loaded: list[str] = []

    # 1. CWD .env (dotenv default behaviour)
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        load_dotenv(cwd_env, override=False)
        loaded.append(str(cwd_env))

    # 2. Global ~/.openclaw/.env (never overrides already-set vars)
    from openclaw.config.auth_profiles import resolve_state_dir
    global_env = resolve_state_dir() / ".env"
    if global_env.exists():
        load_dotenv(global_env, override=False)
        loaded.append(str(global_env))

    if loaded and not quiet:
        logger.debug("Loaded .env files: %s", ", ".join(loaded))
    elif loaded:
        logger.debug("Loaded .env: %s", ", ".join(loaded))


def write_global_dot_env(key: str, value: str) -> None:
    """Write or update a single key=value in ~/.openclaw/.env.

    Used when the user prefers env-var style storage over auth-profiles.json.
    """
    from openclaw.config.auth_profiles import resolve_state_dir
    env_path = resolve_state_dir() / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)

    existing_lines: list[str] = []
    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)

    prefix = f"{key}="
    updated = False
    for i, line in enumerate(existing_lines):
        if line.lstrip().startswith(prefix):
            existing_lines[i] = f"{key}={value}\n"
            updated = True
            break

    if not updated:
        existing_lines.append(f"{key}={value}\n")

    env_path.write_text("".join(existing_lines), encoding="utf-8")
    logger.debug("Wrote %s to %s", key, env_path)
