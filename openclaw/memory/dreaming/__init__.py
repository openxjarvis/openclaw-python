"""Memory dreaming subsystem.

Mirrors TypeScript memory-host-sdk/dreaming.ts

Provides periodic memory consolidation in three phases:
  - Light: daily digest from recent sessions/recall
  - Deep: long-term synthesis with recovery support
  - REM: pattern and relationship discovery

Usage:
    from openclaw.memory.dreaming import DreamingConfig, DreamingScheduler

    config = DreamingConfig(enabled=True)
    scheduler = DreamingScheduler(config, agent_id="main", memory_manager=mm)
    await scheduler.start()
"""
from pathlib import Path

from .config import (
    DeepPhaseConfig,
    DeepRecoveryConfig,
    DreamBudget,
    DreamExecutionConfig,
    DreamingConfig,
    DreamingPhasesConfig,
    DreamSpeed,
    DreamStorageConfig,
    DreamThinking,
    LightPhaseConfig,
    RemPhaseConfig,
    resolve_dreaming_config,
)
from .phases import DreamPhase, run_deep_phase, run_light_phase, run_rem_phase
from .recovery import check_and_run_recovery
from .scheduler import DreamingScheduler
from .workspaces import resolve_dreaming_workspaces
from .doctor import (
    build_dreaming_status_payload,
    dedupe_dream_diary_entries,
    extract_iso_day_from_path,
    grounded_markdown_to_diary_lines,
    list_workspace_daily_files,
    load_dreaming_store_stats,
    preview_grounded_rem_markdown,
    read_dream_diary,
    remove_backfill_diary_entries,
    remove_grounded_short_term_candidates,
    repair_dreaming_artifacts,
    write_backfill_diary_entries,
)

__all__ = [
    # Config
    "DreamingConfig",
    "DreamingPhasesConfig",
    "DreamExecutionConfig",
    "DreamStorageConfig",
    "LightPhaseConfig",
    "DeepPhaseConfig",
    "DeepRecoveryConfig",
    "RemPhaseConfig",
    "DreamSpeed",
    "DreamThinking",
    "DreamBudget",
    "resolve_dreaming_config",
    # Phases
    "DreamPhase",
    "run_light_phase",
    "run_deep_phase",
    "run_rem_phase",
    # Scheduler
    "DreamingScheduler",
    # Recovery
    "check_and_run_recovery",
    # Workspaces
    "resolve_dreaming_workspaces",
    # Doctor RPC helpers
    "read_dream_diary",
    "write_backfill_diary_entries",
    "remove_backfill_diary_entries",
    "dedupe_dream_diary_entries",
    "remove_grounded_short_term_candidates",
    "repair_dreaming_artifacts",
    "preview_grounded_rem_markdown",
    "list_workspace_daily_files",
    "extract_iso_day_from_path",
    "grounded_markdown_to_diary_lines",
    "load_dreaming_store_stats",
    "build_dreaming_status_payload",
]


# Backward-compatible aliases for gateway handlers
async def get_dream_diary(agent_id: str = "main") -> dict:
    """Return dream diary payload for agent (resolves workspace from config)."""
    from openclaw.agents.agent_scope import resolve_agent_workspace_dir, resolve_default_agent_id
    from openclaw.config.loader import load_config

    cfg = load_config()
    workspace_dir = resolve_agent_workspace_dir(cfg, agent_id or resolve_default_agent_id(cfg))
    return await read_dream_diary(str(workspace_dir))


async def backfill_dream_diary(agent_id: str, params: dict) -> dict:
    """Backfill dream diary from workspace daily memory files."""
    from openclaw.agents.agent_scope import resolve_agent_workspace_dir, resolve_default_agent_id
    from openclaw.config.loader import load_config
    from .config import resolve_dreaming_config

    cfg = load_config()
    aid = agent_id or resolve_default_agent_id(cfg)
    workspace_dir = str(resolve_agent_workspace_dir(cfg, aid))
    memory_dir = Path(workspace_dir) / "memory"
    source_files = list_workspace_daily_files(memory_dir)
    if not source_files:
        diary = await read_dream_diary(workspace_dir)
        return {
            "path": diary.get("path"),
            "action": "backfill",
            "found": diary.get("found"),
            "scannedFiles": 0,
            "written": 0,
            "replaced": 0,
        }
    grounded = preview_grounded_rem_markdown(workspace_dir, source_files)
    dreaming_cfg = resolve_dreaming_config(cfg)
    entries = []
    for file_info in grounded.get("files") or []:
        iso_day = extract_iso_day_from_path(str(file_info.get("path") or ""))
        if not iso_day:
            continue
        entries.append(
            {
                "isoDay": iso_day,
                "sourcePath": file_info.get("path"),
                "bodyLines": grounded_markdown_to_diary_lines(
                    str(file_info.get("renderedMarkdown") or "")
                ),
            }
        )
    written = await write_backfill_diary_entries(
        workspace_dir,
        entries,
        timezone=dreaming_cfg.timezone,
    )
    diary = await read_dream_diary(workspace_dir)
    return {
        "path": diary.get("path"),
        "action": "backfill",
        "found": diary.get("found"),
        "scannedFiles": grounded.get("scannedFiles", 0),
        "written": written.get("written", 0),
        "replaced": written.get("replaced", 0),
    }


async def reset_dream_diary(agent_id: str) -> dict:
    """Reset backfill dream diary entries."""
    from openclaw.agents.agent_scope import resolve_agent_workspace_dir, resolve_default_agent_id
    from openclaw.config.loader import load_config

    cfg = load_config()
    aid = agent_id or resolve_default_agent_id(cfg)
    workspace_dir = str(resolve_agent_workspace_dir(cfg, aid))
    removed = await remove_backfill_diary_entries(workspace_dir)
    diary = await read_dream_diary(workspace_dir)
    return {
        "path": diary.get("path"),
        "action": "reset",
        "found": diary.get("found"),
        "removedEntries": removed.get("removed", 0),
    }

