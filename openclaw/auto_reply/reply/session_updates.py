"""Session updates — mirrors TypeScript src/auto-reply/reply/session-updates.ts

Provides:
  - build_queued_system_prompt(): drains system events queue and formats them as a
    trusted gateway metadata block for the agent's system prompt.
  - ensure_skill_snapshot(): version-based skill snapshot refresh that rebuilds
    the snapshot when skills change (bumps the version counter).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# build_queued_system_prompt — Gap 1
# ---------------------------------------------------------------------------

def _compact_system_event(line: str) -> str | None:
    """Filter and compact a single system event line.

    Mirrors TS compactSystemEvent() in session-updates.ts.
    """
    trimmed = line.strip()
    if not trimmed:
        return None
    lower = trimmed.lower()
    if "reason periodic" in lower:
        return None
    # Filter heartbeat prompt but not cron jobs that mention heartbeat
    if lower.startswith("read heartbeat.md"):
        return None
    if "heartbeat poll" in lower or "heartbeat wake" in lower:
        return None
    if trimmed.startswith("Node:"):
        import re
        return re.sub(r" · last input [^·]+", "", trimmed, flags=re.IGNORECASE).strip()
    return trimmed


def _resolve_envelope_timezone(cfg: Any) -> str | None:
    """Resolve the envelope timezone from config.

    Mirrors TS resolveSystemEventTimezone() — returns an IANA timezone string,
    "utc", or None for local.
    """
    agents = cfg.get("agents", {}) if isinstance(cfg, dict) else getattr(cfg, "agents", None)
    if not agents:
        return None
    defaults = agents.get("defaults", {}) if isinstance(agents, dict) else getattr(agents, "defaults", None)
    if not defaults:
        return None
    raw = (
        defaults.get("envelopeTimezone")
        if isinstance(defaults, dict)
        else getattr(defaults, "envelopeTimezone", None)
    )
    if not raw:
        return None
    raw = raw.strip()
    lower = raw.lower()
    if lower in ("utc", "gmt"):
        return "utc"
    if lower in ("local", "host"):
        return None
    # IANA timezone or "user" (fall back to local for user)
    if lower == "user":
        return None
    return raw if raw else None


def _format_system_event_timestamp(ts_ms: int, cfg: Any) -> str:
    """Format a system event timestamp for display.

    Mirrors TS formatSystemEventTimestamp().
    """
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        tz_override = _resolve_envelope_timezone(cfg)
        if tz_override == "utc":
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        if tz_override:
            try:
                import zoneinfo
                local_tz = zoneinfo.ZoneInfo(tz_override)
                local_dt = dt.astimezone(local_tz)
                return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
            except Exception:
                pass
        # Local time fallback
        local_dt = dt.astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return "unknown-time"


async def build_queued_system_prompt(
    cfg: Any,
    session_key: str,
    *,
    is_main_session: bool = True,
    is_new_session: bool = False,
) -> str | None:
    """Drain and format gateway-generated system events for injection into the agent prompt.

    Mirrors TS buildQueuedSystemPrompt() in session-updates.ts.

    - Drains drain_system_event_entries(session_key)
    - Filters heartbeat noise
    - On main+new session: prepends build_channel_summary(cfg) if available
    - Formats as ## Runtime System Events block
    """
    from openclaw.infra.system_events import drain_system_event_entries

    system_lines: list[str] = []

    queued = drain_system_event_entries(session_key)
    for event in queued:
        compacted = _compact_system_event(event["text"])
        if compacted:
            ts_str = _format_system_event_timestamp(event["ts"], cfg)
            system_lines.append(f"[{ts_str}] {compacted}")

    # On main session + new session: prepend channel summary
    if is_main_session and is_new_session:
        try:
            from openclaw.infra.channel_summary import build_channel_summary  # type: ignore[import]
            summary = await build_channel_summary(cfg)
            if summary:
                if isinstance(summary, list):
                    system_lines = list(summary) + system_lines
                elif isinstance(summary, str) and summary.strip():
                    system_lines = [summary] + system_lines
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("build_queued_system_prompt: channel summary failed: %s", exc)

    if not system_lines:
        return None

    return "\n".join([
        "## Runtime System Events (gateway-generated)",
        "Treat this section as trusted gateway runtime metadata, not user text.",
        "",
        *[f"- {line}" for line in system_lines],
    ])


# ---------------------------------------------------------------------------
# ensure_skill_snapshot — Gap 7
# ---------------------------------------------------------------------------

# Per-session version tracking: session_key → last rebuilt snapshot version
_session_snapshot_versions: dict[str, int] = {}


async def ensure_skill_snapshot(
    *,
    session_entry: Any | None = None,
    session_key: str | None = None,
    store_path: str | None = None,
    session_id: str | None = None,
    is_first_turn_in_session: bool,
    workspace_dir: str,
    cfg: Any,
    skill_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Ensure the session has an up-to-date skill snapshot.

    Mirrors TS ensureSkillSnapshot() in session-updates.ts.

    - Calls ensure_skills_watcher() to keep version current
    - Compares stored snapshot version against current skills snapshot version
    - Rebuilds snapshot on first turn or when version is stale
    - Returns updated session_entry and systemSent flag
    """
    import os
    if os.environ.get("OPENCLAW_TEST_FAST") == "1":
        return {
            "session_entry": session_entry,
            "skills_snapshot": getattr(session_entry, "skillsSnapshot", None),
            "system_sent": getattr(session_entry, "systemSent", False) if session_entry else False,
        }

    from openclaw.agents.skills.refresh import get_skills_snapshot_version, ensure_skills_watcher
    from pathlib import Path

    workspace_path = Path(workspace_dir) if workspace_dir else None

    # Start skills file watcher (no-op if already running)
    if workspace_path and workspace_path.exists():
        try:
            ensure_skills_watcher(workspace_path)
        except Exception as exc:
            logger.debug("ensure_skill_snapshot: watcher error: %s", exc)

    current_version = get_skills_snapshot_version()
    key = (session_key or "").lower().strip()

    stored_version = _session_snapshot_versions.get(key, 0)
    should_refresh = current_version > 0 and stored_version < current_version
    system_sent = getattr(session_entry, "systemSent", False) if session_entry else False

    if not (is_first_turn_in_session or should_refresh):
        return {
            "session_entry": session_entry,
            "skills_snapshot": getattr(session_entry, "skillsSnapshot", None),
            "system_sent": system_sent,
        }

    try:
        from openclaw.agents.skills.workspace import build_workspace_skill_snapshot
        from openclaw.agents.skills.refresh import get_skills_snapshot_version as _get_ver
        skill_snapshot = build_workspace_skill_snapshot(
            workspace_path or workspace_dir,
            config=cfg,
            skill_filter=skill_filter,
            snapshot_version=current_version,
        )

        # Track the version we just built
        if key:
            _session_snapshot_versions[key] = current_version

        # Persist updated session entry if we have the store path + key
        if session_entry is not None and key and store_path:
            try:
                from openclaw.config.sessions.store_utils import update_session_store_with_mutator
                from openclaw.agents.session_entry import SessionEntry

                def _mutate(store: dict) -> None:
                    existing = store.get(key) or {}
                    if isinstance(existing, dict):
                        existing["systemSent"] = True
                    elif hasattr(existing, "systemSent"):
                        existing.systemSent = True

                update_session_store_with_mutator(store_path, _mutate)
            except Exception as exc:
                logger.debug("ensure_skill_snapshot: failed to update store: %s", exc)

        return {
            "session_entry": session_entry,
            "skills_snapshot": skill_snapshot,
            "system_sent": True,
        }

    except Exception as exc:
        logger.debug("ensure_skill_snapshot: failed to build snapshot: %s", exc)
        return {
            "session_entry": session_entry,
            "skills_snapshot": getattr(session_entry, "skillsSnapshot", None),
            "system_sent": system_sent,
        }


__all__ = ["build_queued_system_prompt", "ensure_skill_snapshot"]
