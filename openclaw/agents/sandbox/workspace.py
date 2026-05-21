"""Sandbox workspace setup

Creates and seeds per-sandbox workspace directories with standard agent
bootstrap files (AGENTS.md, SOUL.md, etc.), then calls
``ensure_agent_workspace()`` to set up boundary files.

Mirrors TypeScript openclaw/src/agents/sandbox/workspace.ts
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Standard agent bootstrap files to copy when seeding a new workspace
# (mirrors TS DEFAULT_* filenames in workspace.ts)
_SEED_FILES = [
    "AGENTS.md",
    "SOUL.md",
    "TOOLS.md",
    "IDENTITY.md",
    "USER.md",
    "BOOTSTRAP.md",
    "HEARTBEAT.md",
]


async def ensure_sandbox_workspace(
    workspace_dir: str | Path,
    seed_from: str | Path | None = None,
    skip_bootstrap: bool = False,
) -> None:
    """Create and optionally seed a sandbox workspace directory.

    1. Creates *workspace_dir* (and parents) if it does not exist.
    2. If *seed_from* is given **and** the target directory is empty, copies
       the standard agent files (AGENTS.md, SOUL.md, …) from *seed_from*
       into *workspace_dir* using write-if-missing semantics.
    3. Calls :func:`ensure_agent_workspace` to create/update boundary files
       (workspace-state.json, template bootstrapping, etc.) unless
       *skip_bootstrap* is True.

    Mirrors TS ``ensureSandboxWorkspace()``.
    """
    workspace = Path(workspace_dir)
    workspace.mkdir(parents=True, exist_ok=True)

    if seed_from:
        seed = Path(seed_from).expanduser().resolve()
        if seed.is_dir():
            for filename in _SEED_FILES:
                dest = workspace / filename
                if dest.exists():
                    continue  # write-if-missing: never overwrite
                src = seed / filename
                if not src.exists():
                    continue
                # M11: Lightweight boundary check — mirrors TS openBoundaryFile.
                # Resolve symlinks and ensure the source is truly under the seed dir.
                try:
                    resolved_src = src.resolve()
                    resolved_src.relative_to(seed)  # raises ValueError if outside seed
                except (ValueError, OSError):
                    logger.warning("Seed file %s resolves outside seed dir — skipping (symlink escape?)", filename)
                    continue
                try:
                    shutil.copy2(str(src), str(dest))
                    logger.debug("Seeded sandbox workspace file: %s", filename)
                except OSError as exc:
                    logger.warning("Could not seed %s into sandbox workspace: %s", filename, exc)
        else:
            logger.warning("Sandbox seed_from path does not exist or is not a directory: %s", seed)

    # M12: Always call ensure_agent_workspace — mirrors TS which always invokes
    # ensureAgentWorkspace and only sets ensureBootstrapFiles=false when skip_bootstrap.
    # Python previously skipped the call entirely; now we always run mkdir/state setup.
    try:
        from openclaw.agents.ensure_workspace import ensure_agent_workspace  # type: ignore[import]
        ensure_agent_workspace(
            workspace_dir=str(workspace),
            ensure_bootstrap_files=not skip_bootstrap,
        )
    except (ImportError, Exception) as exc:
        logger.warning("ensure_agent_workspace skipped for sandbox workspace: %s", exc)
