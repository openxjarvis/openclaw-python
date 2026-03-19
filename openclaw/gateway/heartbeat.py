"""
Periodic heartbeat system for agents.

Executes periodic agent turns in the main session to:
- Keep sessions alive
- Monitor system health
- Provide status updates

Reference: openclaw/docs/gateway/heartbeat.md
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import InitVar, dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Default heartbeat target (matches TS DEFAULT_HEARTBEAT_TARGET = "none")
DEFAULT_HEARTBEAT_TARGET = "none"

# Default heartbeat prompt (matches TS)
DEFAULT_HEARTBEAT_PROMPT = (
    "Read HEARTBEAT.md if it exists (workspace context). "
    "Follow it strictly. Do not infer or repeat old tasks from prior chats. "
    "If nothing needs attention, reply HEARTBEAT_OK."
)

# Default ack cutoff (TS: ackMaxChars = 300)
DEFAULT_ACK_MAX_CHARS = 300


@dataclass
class ActiveHoursConfig:
    """Active hours restriction — mirrors TS HeartbeatActiveHours."""

    start: str = "00:00"      # HH:MM inclusive
    end: str = "24:00"        # HH:MM exclusive ("24:00" = end of day)
    timezone: str | None = None  # IANA tz or "user"/"local"; None = host tz

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ActiveHoursConfig):
            return self.start == other.start and self.end == other.end and self.timezone == other.timezone
        if isinstance(other, (tuple, list)) and len(other) == 2:
            # Compare as (start_hour, end_hour) integers
            try:
                s = int(self.start.split(":")[0])
                e = int(self.end.split(":")[0])
                return (s, e) == (int(other[0]), int(other[1]))
            except (ValueError, IndexError):
                pass
        return NotImplemented


@dataclass
class HeartbeatVisibilityConfig:
    """Per-channel heartbeat visibility — mirrors TS HeartbeatChannelConfig."""

    show_ok: bool = False       # Send HEARTBEAT_OK ack messages
    show_alerts: bool = True    # Send alert (non-OK) messages
    use_indicator: bool = True  # Emit indicator events


class HeartbeatConfig:
    """Heartbeat configuration — mirrors TS HeartbeatConfig.

    Fields intentionally mirror the TS config schema to allow 1-to-1 mapping.
    Accepts convenience kwargs ``enabled`` and ``interval_minutes`` in addition
    to the canonical ``every`` interval string.
    """

    def __init__(
        self,
        every: str = "30m",
        interval_minutes: int | None = None,
        enabled: bool | None = None,
        model: str | None = None,
        include_reasoning: bool = False,
        light_context: bool = False,
        target: str = "none",  # Changed from "last" to align with TS DEFAULT_HEARTBEAT_TARGET
        to: str | None = None,
        account_id: str | None = None,
        prompt: str = DEFAULT_HEARTBEAT_PROMPT,
        ack_max_chars: int = DEFAULT_ACK_MAX_CHARS,
        session: str = "main",
        suppress_tool_error_warnings: bool = False,
        active_hours: "ActiveHoursConfig | tuple[int, int] | None" = None,
        visibility: "HeartbeatVisibilityConfig | None" = None,
        response_prefix: str | None = None,  # Prepended to non-HEARTBEAT_OK messages
        workspace_dir: str | None = None,  # For HEARTBEAT.md empty check
    ) -> None:
        # Coerce interval_minutes → every
        if interval_minutes is not None:
            self.every = f"{interval_minutes}m"
        else:
            self.every = every
        self.model = model
        self.include_reasoning = include_reasoning
        self.light_context = light_context
        self.target = target
        self.to = to
        self.account_id = account_id
        self.prompt = prompt
        self.ack_max_chars = ack_max_chars
        self.session = session
        self.suppress_tool_error_warnings = suppress_tool_error_warnings
        self.visibility = visibility if visibility is not None else HeartbeatVisibilityConfig()
        # Coerce tuple active_hours → ActiveHoursConfig
        if isinstance(active_hours, tuple):
            s_val, e_val = active_hours
            def _int_to_hhmm(v: "int | str") -> str:
                return f"{v:02d}:00" if isinstance(v, int) else str(v)
            self.active_hours: "ActiveHoursConfig | None" = ActiveHoursConfig(
                start=_int_to_hhmm(s_val), end=_int_to_hhmm(e_val)
            )
        else:
            self.active_hours = active_hours
        self.response_prefix = response_prefix
        self.workspace_dir = workspace_dir
        # Explicit enabled overrides computed value
        self._enabled_explicit: bool | None = enabled

    @property
    def enabled(self) -> bool:
        """True unless interval is "0m"/"0" (disabled)."""
        if self._enabled_explicit is not None:
            return self._enabled_explicit
        return not self._is_zero_interval()

    @enabled.setter
    def enabled(self, value: bool | None) -> None:
        self._enabled_explicit = value

    @property
    def interval_minutes(self) -> int:
        """Parsed interval in minutes."""
        return _parse_interval_minutes(self.every)

    def _is_zero_interval(self) -> bool:
        return _parse_interval_minutes(self.every) == 0

    def get_interval_minutes(self) -> int:
        """Parsed interval in minutes (alias for interval_minutes property)."""
        return self.interval_minutes

    def __repr__(self) -> str:
        return (
            f"HeartbeatConfig(enabled={self.enabled!r}, every={self.every!r}, "
            f"model={self.model!r}, include_reasoning={self.include_reasoning!r}, "
            f"light_context={self.light_context!r}, "
            f"target={self.target!r}, to={self.to!r}, account_id={self.account_id!r}, "
            f"prompt={self.prompt!r}, ack_max_chars={self.ack_max_chars!r}, "
            f"session={self.session!r}, suppress_tool_error_warnings={self.suppress_tool_error_warnings!r}, "
            f"active_hours={self.active_hours!r}, visibility={self.visibility!r})"
        )


def _parse_interval_minutes(every: str | int) -> int:
    """Parse a duration string like "30m", "1h", "90s" to minutes."""
    if isinstance(every, int):
        return max(0, every)
    s = str(every).strip().lower()
    if not s or s in ("0", "0m", "never"):
        return 0
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd]?)", s)
    if not m:
        return 30
    val, unit = float(m.group(1)), m.group(2) or "m"
    if unit == "s":
        return max(0, int(val / 60))
    if unit == "m":
        return max(0, int(val))
    if unit == "h":
        return max(0, int(val * 60))
    if unit == "d":
        return max(0, int(val * 1440))
    return max(0, int(val))


def _is_heartbeat_ok(text: str, ack_max_chars: int = DEFAULT_ACK_MAX_CHARS) -> bool:
    """Return True when text is a HEARTBEAT_OK ack with no significant payload.

    Mirrors TS isHeartbeatOk() logic:
    - Strip leading/trailing HEARTBEAT_OK tokens and whitespace.
    - If the remainder is <= ack_max_chars, treat as pure ack.
    - If HEARTBEAT_OK appears in the middle (not at start/end), NOT treated as ack.
    """
    stripped = text.strip()
    upper = stripped.upper()

    starts_with_ok = upper.startswith("HEARTBEAT_OK")
    ends_with_ok = upper.endswith("HEARTBEAT_OK")

    # If HEARTBEAT_OK is neither at start nor end, it's in the middle — not an ack
    if not starts_with_ok and not ends_with_ok:
        # Check if it contains HEARTBEAT_OK at all (in middle)
        if "HEARTBEAT_OK" in upper:
            return False

    # Remove leading HEARTBEAT_OK
    while stripped.upper().startswith("HEARTBEAT_OK"):
        stripped = stripped[len("HEARTBEAT_OK"):].strip()
    # Remove trailing HEARTBEAT_OK
    while stripped.upper().endswith("HEARTBEAT_OK"):
        stripped = stripped[: -len("HEARTBEAT_OK")].strip()

    # If remaining text still has HEARTBEAT_OK in middle, not an ack
    if "HEARTBEAT_OK" in stripped.upper():
        return False

    return len(stripped) <= ack_max_chars


def strip_heartbeat_ok(text: str) -> str:
    """Strip leading/trailing HEARTBEAT_OK from a message body.

    Mirrors TS stripHeartbeatOk().
    """
    stripped = text.strip()
    while stripped.upper().startswith("HEARTBEAT_OK"):
        stripped = stripped[len("HEARTBEAT_OK"):].strip()
    while stripped.upper().endswith("HEARTBEAT_OK"):
        stripped = stripped[: -len("HEARTBEAT_OK")].strip()
    return stripped


def is_heartbeat_content_effectively_empty(content: str | None) -> bool:
    """Return True when a HEARTBEAT.md file has no actionable content.

    Strips frontmatter-style lines, blank lines, markdown headers, and
    empty list items.  Mirrors TS isHeartbeatContentEffectivelyEmpty().
    """
    if content is None:
        return False
    if not isinstance(content, str):
        return False
    import re as _re
    _HEADER_RE = _re.compile(r"^#+(\s|$)")
    _EMPTY_LIST_RE = _re.compile(r"^[-*+]\s*(\[[\sXx]?\]\s*)?$")
    for line in content.split("\n"):
        trimmed = line.strip()
        if not trimmed:
            continue
        if _HEADER_RE.match(trimmed):
            continue
        if _EMPTY_LIST_RE.match(trimmed):
            continue
        return False  # Found actionable content
    return True


from ..infra.heartbeat_events_filter import (  # noqa: E402 — placed here for locality
    build_cron_event_prompt,
    build_exec_event_prompt,
    is_cron_system_event,
    is_exec_completion_event,
)


async def prune_heartbeat_transcript(
    transcript_path: str | None,
    pre_heartbeat_size: int | None,
) -> None:
    """Truncate transcript file back to its pre-heartbeat size on HEARTBEAT_OK.

    Mirrors TS pruneHeartbeatTranscript().
    """
    import os
    if not transcript_path or pre_heartbeat_size is None or pre_heartbeat_size < 0:
        return
    try:
        current_size = os.path.getsize(transcript_path)
        if current_size > pre_heartbeat_size:
            with open(transcript_path, "r+b") as fh:
                fh.truncate(pre_heartbeat_size)
    except OSError:
        pass


def _parse_hhmm(hhmm: str) -> tuple[int, int]:
    """Parse "HH:MM" or "24:00" → (hours, minutes)."""
    parts = hhmm.split(":")
    if len(parts) == 2:
        return int(parts[0]), int(parts[1])
    return int(parts[0]), 0


def _resolve_active_hours_timezone(raw: str | None) -> str | None:
    """Resolve active-hours timezone string to a valid IANA tz or None (local).

    Mirrors TS resolveActiveHoursTimezone().
    """
    trimmed = (raw or "").strip()
    if not trimmed or trimmed == "local":
        return None  # Use local time
    if trimmed == "user":
        # TODO: resolve from config.agents.defaults.userTimezone; fall back to local
        return None
    # Validate as IANA timezone
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(trimmed)
        return trimmed
    except Exception:
        return None  # Fall back to local time on invalid IANA string


def _in_active_hours(cfg: ActiveHoursConfig) -> bool:
    """Return True if now is within the configured active hours window.

    Supports IANA timezone strings via the ``timezone`` field on
    ``ActiveHoursConfig``.  Mirrors TS isWithinActiveHours().
    """
    tz_name = _resolve_active_hours_timezone(cfg.timezone)
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            import datetime as _dt
            now_local = _dt.datetime.now(ZoneInfo(tz_name))
        except Exception:
            now_local = datetime.now()
    else:
        now_local = datetime.now()

    current_mins = now_local.hour * 60 + now_local.minute
    start_h, start_m = _parse_hhmm(cfg.start)
    end_h, end_m = _parse_hhmm(cfg.end)
    start_mins = start_h * 60 + start_m
    end_mins = end_h * 60 + end_m  # "24:00" → 1440

    if start_mins == end_mins:
        return False  # Zero-width window — always outside

    if start_mins < end_mins:
        return start_mins <= current_mins < end_mins
    # Wraps midnight
    return current_mins >= start_mins or current_mins < end_mins


class HeartbeatManager:
    """Manage periodic heartbeat agent turns.

    The heartbeat system executes periodic agent turns in the main session
    to provide health monitoring and keep connections alive.

    Features:
    - Configurable interval (default 30m)
    - Active hours restriction with HH:MM granularity
    - HEARTBEAT_OK special handling with ack_max_chars cutoff
    - Per-channel visibility settings (show_ok, show_alerts, use_indicator)
    - Custom prompt, model override, session override
    - suppressToolErrorWarnings flag

    Mirrors TS HeartbeatManager in openclaw/src/gateway/heartbeat.ts.
    """

    def __init__(
        self,
        config: HeartbeatConfig,
        agent_runtime: Any,
        session_key: str = "agent:main:main",
    ) -> None:
        self._config = config
        self._agent_runtime = agent_runtime
        self._session_key = session_key
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start heartbeat loop."""
        if not self._config.enabled:
            logger.info("Heartbeat disabled (interval=0)")
            return
        if self._running:
            logger.warning("Heartbeat already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info(
            "Heartbeat started: every=%s (%dm), target=%s",
            self._config.every,
            self._config.interval_minutes,
            self._config.target,
        )

    async def stop(self) -> None:
        """Stop heartbeat loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Heartbeat stopped")

    async def _heartbeat_loop(self) -> None:
        """Periodic heartbeat execution loop."""
        while self._running:
            try:
                await asyncio.sleep(self._config.interval_minutes * 60)
                if not self._should_run():
                    logger.debug("Skipping heartbeat: outside active hours or visibility all-off")
                    continue
                await self._execute_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Heartbeat execution error: %s", exc, exc_info=True)

    def _should_run(self) -> bool:
        """Return True if the heartbeat should fire now."""
        # Active hours check
        if self._config.active_hours and not _in_active_hours(self._config.active_hours):
            return False
        # If all visibility flags are off, skip (mirrors TS)
        vis = self._config.visibility
        if not vis.show_ok and not vis.show_alerts and not vis.use_indicator:
            return False
        return True

    async def _check_heartbeat_md(self) -> bool:
        """Return True if execution should proceed (HEARTBEAT.md exists and is not empty).

        Missing HEARTBEAT.md is treated as non-empty (proceed).
        Effectively-empty HEARTBEAT.md causes skip.
        Mirrors TS preflight HEARTBEAT.md gate.
        """
        import os
        workspace = self._config.workspace_dir
        if not workspace:
            return True  # No workspace configured — skip file gate
        heartbeat_path = os.path.join(workspace, "HEARTBEAT.md")
        try:
            with open(heartbeat_path, "r", encoding="utf-8") as fh:
                content = fh.read()
            if is_heartbeat_content_effectively_empty(content):
                logger.info("HEARTBEAT.md is effectively empty — skipping heartbeat")
                return False
        except FileNotFoundError:
            pass  # Missing file is intentional in some setups; proceed
        except Exception as exc:
            logger.debug("HEARTBEAT.md read error: %s", exc)
        return True

    async def _execute_heartbeat(self) -> None:
        """Execute heartbeat agent turn and handle HEARTBEAT_OK contract."""
        logger.info("Executing heartbeat turn (session=%s)", self._session_key)
        
        # Queue check: skip heartbeat if agent is busy (mirrors TS getQueueSize check)
        if hasattr(self._agent_runtime, 'is_busy') and callable(self._agent_runtime.is_busy):
            try:
                if self._agent_runtime.is_busy():
                    logger.info("Heartbeat skipped: agent is busy (requests in flight)")
                    from openclaw.infra.heartbeat_events import emit_heartbeat_event
                    emit_heartbeat_event({
                        "agentId": getattr(self._agent_runtime, 'agent_id', 'main'),
                        "status": "skipped",
                        "reason": "requests-in-flight",
                        "indicator": "ok",
                    })
                    return
            except Exception as e:
                logger.debug(f"Failed to check agent busy state: {e}")
                # Continue with heartbeat if check fails

        # HEARTBEAT.md empty check (mirrors TS preflight gate)
        if not await self._check_heartbeat_md():
            return

        # Capture transcript state for pruning on HEARTBEAT_OK
        transcript_path: str | None = None
        pre_heartbeat_size: int | None = None
        try:
            import os
            workspace = self._config.workspace_dir
            if workspace:
                # Best-effort: look for a JSONL transcript for this session
                # (simple heuristic; full TS implementation queries session store)
                candidate = os.path.join(workspace, ".openclaw", "sessions", "main.jsonl")
                if os.path.isfile(candidate):
                    transcript_path = candidate
                    pre_heartbeat_size = os.path.getsize(candidate)
        except Exception:
            pass

        # Append current time to prompt (mirrors TS appendCronStyleCurrentTimeLine)
        from openclaw.agents.current_time import append_cron_style_current_time_line
        import time
        
        prompt_with_time = append_cron_style_current_time_line(
            self._config.prompt,
            self._agent_runtime.config if hasattr(self._agent_runtime, 'config') else {},
            int(time.time() * 1000)
        )

        # Build run_turn kwargs — light_context skips full history (mirrors TS lightContext).
        run_kwargs: dict[str, Any] = {
            "session_key": self._session_key,
            "messages": [{"role": "user", "content": prompt_with_time}],  # Use prompt with time
            "stream": True,
        }
        if self._config.light_context:
            run_kwargs["light_context"] = True
        if self._config.model:
            run_kwargs["model"] = self._config.model
        if self._config.include_reasoning:
            run_kwargs["include_reasoning"] = True

        try:
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            async for event in self._agent_runtime.run_turn(**run_kwargs):
                etype = getattr(event, "type", None) or (event.get("type") if isinstance(event, dict) else "")
                data = getattr(event, "data", None) or (event.get("data", {}) if isinstance(event, dict) else {})
                if etype == "agent_text":
                    content_parts.append(data.get("text", "") if isinstance(data, dict) else "")
                elif etype == "agent_reasoning":
                    # Collect reasoning tokens when include_reasoning=True (mirrors TS)
                    reasoning_parts.append(data.get("text", "") if isinstance(data, dict) else "")
                elif etype == "agent_error":
                    logger.error("Heartbeat agent error: %s", data)
                elif etype == "agent_complete":
                    logger.debug("Heartbeat agent turn complete")

            full_text = "".join(content_parts).strip()

            # Strip leading responsePrefix before HEARTBEAT_OK check (mirrors TS)
            response_prefix = (self._config.response_prefix or "").strip()
            if response_prefix and full_text.startswith(response_prefix):
                after_prefix = full_text[len(response_prefix):].lstrip()
                check_text = after_prefix
            else:
                check_text = full_text

            is_ok = _is_heartbeat_ok(check_text, self._config.ack_max_chars)
            vis = self._config.visibility

            if is_ok:
                # Prune transcript on HEARTBEAT_OK (mirrors TS pruneHeartbeatTranscript)
                await prune_heartbeat_transcript(transcript_path, pre_heartbeat_size)

                heartbeat_ok_text = (
                    f"{response_prefix} HEARTBEAT_OK" if response_prefix else "HEARTBEAT_OK"
                )
                if vis.show_ok:
                    payload = strip_heartbeat_ok(check_text)
                    logger.info("Heartbeat OK (delivering ack)")
                    await self._deliver(payload or heartbeat_ok_text)
                else:
                    logger.debug("Heartbeat OK — suppressed (show_ok=False)")
            else:
                # Prepend responsePrefix to alert messages (mirrors TS)
                deliver_text = full_text
                if response_prefix and deliver_text and not deliver_text.startswith(response_prefix):
                    deliver_text = f"{response_prefix} {deliver_text}"
                if vis.show_alerts:
                    logger.info("Heartbeat alert — delivering")
                    await self._deliver(deliver_text)
                else:
                    logger.debug("Heartbeat alert suppressed (show_alerts=False)")

            if vis.use_indicator:
                await self._emit_indicator(is_ok)

            # Deliver reasoning as a separate message when include_reasoning=True (mirrors TS)
            if self._config.include_reasoning and reasoning_parts and vis.show_alerts:
                reasoning_text = "".join(reasoning_parts).strip()
                if reasoning_text:
                    logger.debug("Heartbeat delivering reasoning message")
                    await self._deliver(f"Reasoning:\n{reasoning_text}")

        except Exception as exc:
            logger.error("Heartbeat execution failed: %s", exc, exc_info=True)

    async def _deliver(self, text: str) -> None:
        """Send heartbeat message via the configured target/to/accountId.

        Implements 24-hour deduplication (mirrors TS lastHeartbeatText/lastHeartbeatSentAt).
        Currently delegates to agent_runtime if it exposes a send() method.
        If not, logs the message only.
        """
        if not text:
            return
        
        # Deduplication check (mirrors TS isDuplicateMain logic)
        import time
        current_ms = int(time.time() * 1000)
        
        try:
            # Load session store to check last heartbeat
            from openclaw.config.sessions.store_utils import load_session_store_from_path, save_session_store_to_path
            from openclaw.config.sessions.paths import resolve_default_session_store_path
            
            store_path = resolve_default_session_store_path(agent_id=None)  # Use default agent
            
            if store_path.exists():
                store = load_session_store_from_path(store_path)
                entry = store.get(self._session_key.lower())  # Case-insensitive lookup
                
                if entry:
                    prev_text = getattr(entry, 'lastHeartbeatText', None) or getattr(entry, 'last_heartbeat_text', None)
                    prev_at = getattr(entry, 'lastHeartbeatSentAt', None) or getattr(entry, 'last_heartbeat_sent_at', None)
                    
                    # Check if duplicate (same text within 24 hours)
                    if prev_text and prev_at:
                        time_diff_ms = current_ms - prev_at
                        TWENTY_FOUR_HOURS_MS = 24 * 60 * 60 * 1000
                        
                        if time_diff_ms < TWENTY_FOUR_HOURS_MS and prev_text.strip() == text.strip():
                            logger.info(
                                "Heartbeat suppressed: duplicate message within 24h (age=%d min)",
                                int(time_diff_ms / 60000)
                            )
                            return
        except Exception as e:
            logger.debug(f"Failed to check heartbeat deduplication: {e}")
            # Continue to send even if dedup check fails
        
        # Send the message
        if hasattr(self._agent_runtime, "send_heartbeat_message"):
            await self._agent_runtime.send_heartbeat_message(
                text=text,
                target=self._config.target,
                to=self._config.to,
                account_id=self._config.account_id,
            )
        else:
            logger.info("Heartbeat message (target=%s): %s", self._config.target, text[:200])
        
        # Update session store with last heartbeat info (mirrors TS)
        try:
            from openclaw.config.sessions.store_utils import load_session_store_from_path, save_session_store_to_path
            from openclaw.config.sessions.paths import resolve_default_session_store_path
            
            store_path = resolve_default_session_store_path(agent_id=None)
            
            if store_path.exists():
                store = load_session_store_from_path(store_path)
                entry = store.get(self._session_key.lower())
                
                if entry:
                    # Update lastHeartbeatText and lastHeartbeatSentAt
                    if hasattr(entry, 'lastHeartbeatText'):
                        entry.lastHeartbeatText = text
                    else:
                        entry.last_heartbeat_text = text
                    
                    if hasattr(entry, 'lastHeartbeatSentAt'):
                        entry.lastHeartbeatSentAt = current_ms
                    else:
                        entry.last_heartbeat_sent_at = current_ms
                    
                    # Save updated store
                    save_session_store_to_path(store_path, store)
                    logger.debug("Updated session store with heartbeat metadata")
        except Exception as e:
            logger.debug(f"Failed to update heartbeat metadata: {e}")
            # Non-critical failure, already sent the message

    async def _emit_indicator(self, is_ok: bool) -> None:
        """Emit a heartbeat indicator event."""
        if hasattr(self._agent_runtime, "emit_heartbeat_indicator"):
            await self._agent_runtime.emit_heartbeat_indicator(ok=is_ok)

    async def trigger_now(self) -> None:
        """Trigger an immediate heartbeat (manual wake).

        Mirrors TS triggerHeartbeatNow() / system event --mode now.
        """
        logger.info("Manual heartbeat triggered")
        await self._execute_heartbeat()

    @property
    def is_running(self) -> bool:
        return self._running

    def get_config(self) -> HeartbeatConfig:
        return self._config

    def update_config(self, config: HeartbeatConfig) -> None:
        """Update configuration (requires restart to take effect)."""
        self._config = config


def resolve_heartbeat_config(
    agent_cfg: dict[str, Any],
    defaults_cfg: dict[str, Any] | None = None,
    workspace_dir: str | Path | None = None,
) -> HeartbeatConfig | None:
    """Build a HeartbeatConfig from agent config dict.

    Merges agents.defaults.heartbeat on top and then agent-specific heartbeat
    on top of that, mirroring TS config merging.

    Args:
        agent_cfg: Agent-specific configuration dict
        defaults_cfg: Defaults configuration dict (agents.defaults)
        workspace_dir: Workspace directory for HEARTBEAT.md empty check

    Returns None if heartbeat is not configured or interval is 0.
    """
    base: dict[str, Any] = {}
    if defaults_cfg:
        base.update(defaults_cfg.get("heartbeat") or {})
    base.update(agent_cfg.get("heartbeat") or {})

    if not base:
        return None

    every = base.get("every", "30m")
    if _parse_interval_minutes(str(every)) == 0:
        return None

    active_hours_raw = base.get("activeHours") or base.get("active_hours")
    active_hours: ActiveHoursConfig | None = None
    if active_hours_raw and isinstance(active_hours_raw, dict):
        active_hours = ActiveHoursConfig(
            start=active_hours_raw.get("start", "00:00"),
            end=active_hours_raw.get("end", "24:00"),
            timezone=active_hours_raw.get("timezone"),
        )

    vis_raw = base.get("visibility") or {}
    visibility = HeartbeatVisibilityConfig(
        show_ok=vis_raw.get("showOk", vis_raw.get("show_ok", False)),
        show_alerts=vis_raw.get("showAlerts", vis_raw.get("show_alerts", True)),
        use_indicator=vis_raw.get("useIndicator", vis_raw.get("use_indicator", True)),
    )

    return HeartbeatConfig(
        every=str(every),
        model=base.get("model"),
        include_reasoning=base.get("includeReasoning", base.get("include_reasoning", False)),
        light_context=base.get("lightContext", base.get("light_context", False)),
        target=base.get("target", DEFAULT_HEARTBEAT_TARGET),  # Use constant for consistency
        to=base.get("to"),
        account_id=base.get("accountId") or base.get("account_id"),
        prompt=base.get("prompt", DEFAULT_HEARTBEAT_PROMPT),
        ack_max_chars=int(base.get("ackMaxChars", base.get("ack_max_chars", DEFAULT_ACK_MAX_CHARS))),
        session=base.get("session", "main"),
        suppress_tool_error_warnings=bool(
            base.get("suppressToolErrorWarnings", base.get("suppress_tool_error_warnings", False))
        ),
        active_hours=active_hours,
        visibility=visibility,
        workspace_dir=str(workspace_dir) if workspace_dir else None,
    )


__all__ = [
    "ActiveHoursConfig",
    "HeartbeatConfig",
    "HeartbeatManager",
    "HeartbeatVisibilityConfig",
    "DEFAULT_HEARTBEAT_PROMPT",
    "DEFAULT_HEARTBEAT_TARGET",
    "DEFAULT_ACK_MAX_CHARS",
    "resolve_heartbeat_config",
    "strip_heartbeat_ok",
    "_is_heartbeat_ok",
    "_parse_interval_minutes",
    "is_heartbeat_content_effectively_empty",
    # Re-exported from infra/heartbeat_events_filter.py
    "build_exec_event_prompt",
    "build_cron_event_prompt",
    "is_cron_system_event",
    "is_exec_completion_event",
    "prune_heartbeat_transcript",
]
