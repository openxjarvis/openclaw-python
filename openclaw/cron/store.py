"""Job store matching TypeScript openclaw/src/cron/service/store.ts

Key features:
- JSON file with atomic writes
- mtime-based change detection for fast ensureLoaded
- Legacy field migration
- Backup before overwrite
- Run log (JSONL) per job
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from .types import CronJob

logger = logging.getLogger(__name__)


class CronStore:
    """
    File-based persistent storage for cron jobs.

    Provides:
    - Atomic writes (temp file + rename)
    - Automatic backups
    - mtime tracking for cache invalidation
    """

    def __init__(self, store_path: Path):
        self.store_path = store_path
        self.backup_path = store_path.with_suffix(".json.bak")
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # mtime helpers
    # ------------------------------------------------------------------
    def get_file_mtime_ms(self) -> float | None:
        """Get file modification time in ms (None if missing)."""
        try:
            return self.store_path.stat().st_mtime_ns / 1e6
        except FileNotFoundError:
            return None

    # ------------------------------------------------------------------
    # load / save
    # ------------------------------------------------------------------
    def load(self) -> list[CronJob]:
        """Load jobs from store, applying migrations."""
        if not self.store_path.exists():
            logger.info(f"Store file not found: {self.store_path}")
            return []

        try:
            with open(self.store_path) as f:
                data = json.load(f)

            # Handle v0 format (bare list)
            if isinstance(data, list):
                jobs_data = data
                mutated = True
            else:
                jobs_data = data.get("jobs", [])
                mutated = False

            jobs: list[CronJob] = []

            for raw in jobs_data:
                try:
                    # --- Legacy migrations (matches TypeScript ensureLoaded) ---
                    if self._migrate_job_fields(raw):
                        mutated = True

                    job = CronJob.from_dict(raw)
                    jobs.append(job)
                except Exception as e:
                    logger.error(f"Error parsing job: {e}", exc_info=True)
                    continue

            if mutated:
                # Re-save with migrations applied
                self.save(jobs)

            logger.info(f"Loaded {len(jobs)} jobs from {self.store_path}")
            return jobs

        except json.JSONDecodeError as e:
            logger.error(f"Error parsing store file: {e}")
            return []
        except Exception as e:
            logger.error(f"Error loading store: {e}", exc_info=True)
            return []

    def save(self, jobs: list[CronJob]) -> None:
        """Save jobs to store (atomic write with backup)."""
        try:
            if self.store_path.exists():
                shutil.copy2(self.store_path, self.backup_path)

            jobs_data = [job.to_dict() for job in jobs]
            data = {"version": 1, "jobs": jobs_data}

            temp_path = self.store_path.with_suffix(f".tmp.{uuid.uuid4().hex[:8]}")
            with open(temp_path, "w") as f:
                json.dump(data, f, indent=2)

            temp_path.replace(self.store_path)
            logger.debug(f"Saved {len(jobs)} jobs to {self.store_path}")

        except Exception as e:
            logger.error(f"Error saving store: {e}", exc_info=True)
            if "temp_path" in locals() and temp_path.exists():
                temp_path.unlink()
            raise

    def migrate_if_needed(self) -> None:
        """Run migration on existing store file."""
        if not self.store_path.exists():
            return
        # Just loading triggers migrations
        self.load()

    # ------------------------------------------------------------------
    # Legacy migration (matches TypeScript ensureLoaded migration block)
    # ------------------------------------------------------------------
    @staticmethod
    def _migrate_job_fields(raw: dict[str, Any]) -> bool:
        """Migrate legacy fields on a single raw job dict. Returns True if mutated."""
        mutated = False

        # name: ensure non-empty trimmed string
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raw["name"] = _infer_legacy_name(raw)
            mutated = True
        else:
            trimmed = name.strip()
            if trimmed != name:
                raw["name"] = trimmed
                mutated = True

        # description: normalize optional text
        desc = raw.get("description")
        norm_desc = desc.strip() if isinstance(desc, str) and desc.strip() else None
        if raw.get("description") != norm_desc:
            raw["description"] = norm_desc
            mutated = True

        # enabled: must be bool
        if not isinstance(raw.get("enabled"), bool):
            raw["enabled"] = True
            mutated = True

        # payload migration (kind field)
        # NOTE: TS wire format uses "message" for agentTurn (not "prompt"),
        # so we do NOT rename message→prompt here. Keep as-is.
        payload = raw.get("payload")
        if isinstance(payload, dict):
            kind = payload.get("kind", "")
            # Normalise kind casing
            if kind == "agentturn":
                payload["kind"] = "agentTurn"
                mutated = True
            elif kind == "systemevent":
                payload["kind"] = "systemEvent"
                mutated = True

        # schedule migration
        schedule = raw.get("schedule")
        if isinstance(schedule, dict):
            kind = schedule.get("type", schedule.get("kind", ""))
            if not kind:
                if "at" in schedule or "atMs" in schedule or "timestamp" in schedule:
                    schedule["type"] = "at"
                    mutated = True
                elif "interval_ms" in schedule or "intervalMs" in schedule:
                    schedule["type"] = "every"
                    mutated = True
                elif "expression" in schedule:
                    schedule["type"] = "cron"
                    mutated = True
            # Normalize kind -> type
            if "kind" in schedule and "type" not in schedule:
                schedule["type"] = schedule.pop("kind")
                mutated = True

        # delivery mode: "deliver" -> "announce"
        delivery = raw.get("delivery")
        if isinstance(delivery, dict):
            mode = delivery.get("mode", "")
            if isinstance(mode, str) and mode.strip().lower() == "deliver":
                delivery["mode"] = "announce"
                mutated = True

        # Legacy isolation field: remove
        if "isolation" in raw:
            del raw["isolation"]
            mutated = True

        # Legacy delivery hints in payload
        if isinstance(payload, dict) and _has_legacy_delivery_hints(payload):
            if not isinstance(delivery, dict):
                raw["delivery"] = _build_delivery_from_legacy_payload(payload)
                mutated = True
            _strip_legacy_delivery_fields(payload)
            mutated = True

        return mutated


# ---------------------------------------------------------------------------
# Legacy helpers
# ---------------------------------------------------------------------------

def _infer_legacy_name(raw: dict[str, Any]) -> str:
    """Infer a name for a legacy job without one."""
    payload = raw.get("payload", {})
    if isinstance(payload, dict):
        kind = payload.get("kind", "")
        if kind == "systemEvent":
            text = payload.get("text", "")
            if text:
                return text[:40].strip()
        elif kind == "agentTurn":
            prompt = payload.get("prompt", payload.get("message", ""))
            if prompt:
                return prompt[:40].strip()
    schedule = raw.get("schedule", {})
    stype = schedule.get("type", "") if isinstance(schedule, dict) else ""
    return f"Cron job ({stype})" if stype else "Unnamed job"


def _has_legacy_delivery_hints(payload: dict[str, Any]) -> bool:
    if isinstance(payload.get("deliver"), bool):
        return True
    if isinstance(payload.get("bestEffortDeliver"), bool):
        return True
    to = payload.get("to")
    if isinstance(to, str) and to.strip():
        return True
    return False


def _build_delivery_from_legacy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    deliver = payload.get("deliver")
    mode = "none" if deliver is False else "announce"
    channel = payload.get("channel", "")
    to = payload.get("to", "")
    result: dict[str, Any] = {"mode": mode}
    if isinstance(channel, str) and channel.strip():
        result["channel"] = channel.strip().lower()
    if isinstance(to, str) and to.strip():
        result["to"] = to.strip()
    if isinstance(payload.get("bestEffortDeliver"), bool):
        result["bestEffort"] = payload["bestEffortDeliver"]
    return result


def _strip_legacy_delivery_fields(payload: dict[str, Any]) -> None:
    for key in ("deliver", "channel", "to", "bestEffortDeliver"):
        payload.pop(key, None)


# ---------------------------------------------------------------------------
# CronRunLog  (matches TS appendCronRunLog / readCronRunLogEntries)
# ---------------------------------------------------------------------------

class CronRunLog:
    """JSONL run log for cron job execution history.

    Mirrors TS run-log.ts:
    - Serialized writes via asyncio lock (prevents corruption)
    - Max 2 MB / 2000 lines (prune oldest when exceeded)
    - Read reverse-chronological (newest first up to limit)
    - Validates action=="finished", ts, jobId on read
    - Includes telemetry fields: sessionId, sessionKey, model, provider, usage
    """

    MAX_BYTES = 2_000_000   # 2 MB
    MAX_LINES = 2_000

    def __init__(
        self,
        log_dir: Path,
        job_id: str,
        prune_options: dict[str, int] | None = None
    ):
        """Initialize CronRunLog
        
        Args:
            log_dir: Directory for log files
            job_id: Job identifier
            prune_options: Optional pruning config with 'maxBytes' and 'keepLines'
                          Mirrors TS resolveCronRunLogPruneOptions
        """
        self.log_path = log_dir / f"{job_id}.jsonl"
        self.job_id = job_id
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Apply prune options if provided (mirrors TS)
        if prune_options:
            self.max_bytes = prune_options.get("maxBytes", self.MAX_BYTES)
            self.max_lines = prune_options.get("keepLines", self.MAX_LINES)
        else:
            self.max_bytes = self.MAX_BYTES
            self.max_lines = self.MAX_LINES
        
        # Serialized write chain (asyncio lock per path)
        self._write_lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        return self._write_lock

    def append(self, entry: dict[str, Any]) -> None:
        """Synchronous append (fire-and-forget in async context, serialized via lock)."""
        try:
            import asyncio as _asyncio
            loop: _asyncio.AbstractEventLoop | None = None
            try:
                loop = _asyncio.get_running_loop()
            except RuntimeError:
                pass
            if loop and loop.is_running():
                loop.create_task(self._async_append(entry))
            else:
                self._sync_append(entry)
        except Exception as e:
            logger.error(f"cron: run log append error: {e}", exc_info=True)

    async def async_append(self, entry: dict[str, Any]) -> None:
        """Async append with serialized writes."""
        await self._async_append(entry)

    async def _async_append(self, entry: dict[str, Any]) -> None:
        async with self._get_lock():
            try:
                self._sync_append(entry)
            except Exception as e:
                logger.error(f"cron: run log async append error: {e}", exc_info=True)

    def _sync_append(self, entry: dict[str, Any]) -> None:
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._prune_if_needed()
        except Exception as e:
            logger.error(f"cron: run log write error: {e}", exc_info=True)

    def _prune_if_needed(self) -> None:
        """Prune log if it exceeds max_bytes."""
        try:
            if not self.log_path.exists():
                return
            size = self.log_path.stat().st_size
            if size <= self.max_bytes:
                return
            with open(self.log_path, encoding="utf-8") as f:
                raw = f.read()
            lines = [ln for ln in raw.split("\n") if ln.strip()]
            kept = lines[-self.max_lines:]  # keep newest
            tmp = self.log_path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(kept) + "\n")
            tmp.replace(self.log_path)
        except Exception as e:
            logger.error(f"cron: run log prune error: {e}", exc_info=True)

    def read(self, limit: int | None = None, job_id: str | None = None) -> list[dict[str, Any]]:
        """Read entries reverse-chronological (newest first). Matches TS readCronRunLogEntries.

        Validates and normalizes fields to match TS schema exactly:
        - Required: action=="finished", jobId (non-empty string), ts (finite number)
        - Telemetry: model, provider (non-empty strings only)
        - Usage: input_tokens, output_tokens, total_tokens, cache_read_tokens,
          cache_write_tokens (numbers only, others dropped)
        """
        if not self.log_path.exists():
            return []
        try:
            with open(self.log_path, encoding="utf-8") as f:
                raw = f.read()
        except Exception as e:
            logger.error(f"cron: run log read error: {e}", exc_info=True)
            return []

        if not raw.strip():
            return []

        effective_limit = max(1, min(5000, int(limit or 200)))
        filter_job_id = (job_id or self.job_id or "").strip() or None

        parsed: list[dict[str, Any]] = []
        lines = raw.split("\n")
        for i in range(len(lines) - 1, -1, -1):
            if len(parsed) >= effective_limit:
                break
            line = lines[i].strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(obj, dict):
                continue
            # Validate required fields (mirrors TS readCronRunLogEntries)
            if obj.get("action") != "finished":
                continue
            job_id_val = obj.get("jobId", "")
            if not isinstance(job_id_val, str) or not job_id_val.strip():
                continue
            ts_val = obj.get("ts")
            # Reject non-finite values (mirrors TS Number.isFinite check)
            if not isinstance(ts_val, (int, float)) or ts_val != ts_val:  # nan check
                continue
            import math as _math
            if not _math.isfinite(float(ts_val)):
                continue
            if filter_job_id and job_id_val != filter_job_id:
                continue

            # Normalize telemetry fields (mirrors TS readCronRunLogEntries normalization)
            entry: dict[str, Any] = {
                "ts": ts_val,
                "jobId": job_id_val,
                "action": "finished",
                "status": obj.get("status"),
                "error": obj.get("error"),
                "summary": obj.get("summary"),
                "runAtMs": obj.get("runAtMs"),
                "durationMs": obj.get("durationMs"),
                "nextRunAtMs": obj.get("nextRunAtMs"),
            }

            # model / provider: only include non-empty strings
            model_val = obj.get("model")
            if isinstance(model_val, str) and model_val.strip():
                entry["model"] = model_val

            provider_val = obj.get("provider")
            if isinstance(provider_val, str) and provider_val.strip():
                entry["provider"] = provider_val

            # sessionId / sessionKey
            session_id_val = obj.get("sessionId")
            if isinstance(session_id_val, str) and session_id_val.strip():
                entry["sessionId"] = session_id_val

            session_key_val = obj.get("sessionKey")
            if isinstance(session_key_val, str) and session_key_val.strip():
                entry["sessionKey"] = session_key_val

            # usage: normalize sub-fields (mirrors TS usage normalization block)
            raw_usage = obj.get("usage")
            if raw_usage and isinstance(raw_usage, dict):
                normalized_usage: dict[str, Any] = {}
                for token_key in (
                    "input_tokens", "output_tokens", "total_tokens",
                    "cache_read_tokens", "cache_write_tokens",
                ):
                    v = raw_usage.get(token_key)
                    if isinstance(v, (int, float)) and v == v:  # not nan
                        normalized_usage[token_key] = v
                if normalized_usage:
                    entry["usage"] = normalized_usage

            parsed.append(entry)

        # parsed is newest-first; return as-is (reverse-chronological)
        return parsed

    def clear(self) -> None:
        try:
            if self.log_path.exists():
                self.log_path.unlink()
        except Exception as e:
            logger.error(f"Error clearing run log: {e}", exc_info=True)


def resolve_cron_store_path(config: dict[str, Any] | None) -> Path:
    """Resolve cron store path from config.
    
    Mirrors TS resolveCronStorePath() from src/cron/store.ts:13-22
    
    Args:
        config: Full config dict with 'cron' key
        
    Returns:
        Path to cron jobs.json file
        
    Example:
        >>> resolve_cron_store_path({"cron": {"store": "~/my-cron.json"}})
        PosixPath('/home/user/my-cron.json')
        
        >>> resolve_cron_store_path(None)
        PosixPath('/home/user/.openclaw/cron/jobs.json')
    """
    if not config:
        config = {}
    
    cron_config = config.get("cron") or {}
    store_path_str = cron_config.get("store", "~/.openclaw/cron/jobs.json")
    
    # Handle ~ prefix
    if store_path_str.startswith("~"):
        rest = store_path_str[2:] if len(store_path_str) > 1 else ""
        if rest.startswith("/"):
            rest = rest[1:]
        return Path.home() / rest if rest else Path.home()
    
    # Handle regular paths
    return Path(store_path_str).expanduser().resolve()
