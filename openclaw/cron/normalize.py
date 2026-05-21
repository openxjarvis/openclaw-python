"""Cron job normalization utilities.

Mirrors TypeScript:
  openclaw/src/cron/normalize.ts
  openclaw/src/cron/stagger.ts
  openclaw/src/cron/parse.ts
  openclaw/src/cron/delivery.ts
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TOP_OF_HOUR_STAGGER_MS = 5 * 60 * 1000  # 5 minutes

# ---------------------------------------------------------------------------
# parse.ts — parseAbsoluteTimeMs
# ---------------------------------------------------------------------------

_ISO_TZ_RE = re.compile(r"(Z|[+-]\d{2}:?\d{2})$", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATE_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")


def _normalize_utc_iso(raw: str) -> str:
    if _ISO_TZ_RE.search(raw):
        return raw
    if _ISO_DATE_RE.match(raw):
        return f"{raw}T00:00:00Z"
    if _ISO_DATE_TIME_RE.match(raw):
        return f"{raw}Z"
    return raw


def parse_absolute_time_ms(input_str: str) -> int | None:
    """Parse an absolute time string to epoch milliseconds.

    Accepts: pure numeric ms, ISO date, ISO datetime (with or without TZ).
    """
    raw = input_str.strip()
    if not raw:
        return None
    if re.fullmatch(r"\d+", raw):
        n = int(raw)
        if n > 0:
            return n
    normalized = _normalize_utc_iso(raw)
    try:
        dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# stagger.ts — isRecurringTopOfHourCronExpr, normalize/resolveDefaultCronStaggerMs
# ---------------------------------------------------------------------------

def is_recurring_top_of_hour_cron_expr(expr: str) -> bool:
    """Return True if expr fires at exactly :00 of every (or some) hour."""
    fields = [f for f in expr.strip().split() if f]
    if len(fields) == 5:
        minute_field, hour_field = fields[0], fields[1]
        return minute_field == "0" and "*" in hour_field
    if len(fields) == 6:
        second_field, minute_field, hour_field = fields[0], fields[1], fields[2]
        return second_field == "0" and minute_field == "0" and "*" in hour_field
    return False


def normalize_cron_stagger_ms(raw: Any) -> int | None:
    """Coerce raw staggerMs input to a non-negative integer or None."""
    if isinstance(raw, (int, float)):
        numeric = float(raw)
    elif isinstance(raw, str) and raw.strip():
        try:
            numeric = float(raw.strip())
        except ValueError:
            return None
    else:
        return None
    import math
    if not math.isfinite(numeric):
        return None
    return max(0, int(numeric))


def resolve_default_cron_stagger_ms(expr: str) -> int | None:
    """Return default stagger for top-of-hour cron expressions."""
    return DEFAULT_TOP_OF_HOUR_STAGGER_MS if is_recurring_top_of_hour_cron_expr(expr) else None


def resolve_cron_stagger_ms(schedule: Any) -> int:
    """Resolve stagger for a CronSchedule object (kind="cron")."""
    from .types import CronSchedule as CronSchedType
    if isinstance(schedule, CronSchedType):
        explicit = normalize_cron_stagger_ms(schedule.stagger_ms)
        if explicit is not None:
            return explicit
        return resolve_default_cron_stagger_ms(schedule.expr) or 0
    return 0


def compute_staggered_next_run_ms(base_next_run_ms: int, job_id: str, stagger_ms: int) -> int:
    """Add a deterministic per-job SHA256-based offset within [0, stagger_ms).

    Uses the first **4 bytes** (uint32) of SHA256 — matches TS ``resolveStableCronOffset``
    and ``_resolve_stable_cron_offset_ms`` in service.py.
    """
    if stagger_ms <= 0:
        return base_next_run_ms
    digest = hashlib.sha256(job_id.encode()).digest()
    value = int.from_bytes(digest[:4], "big")
    offset = value % stagger_ms
    return base_next_run_ms + offset


# ---------------------------------------------------------------------------
# delivery.ts — resolveCronDeliveryPlan
# ---------------------------------------------------------------------------

def _normalize_channel(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip().lower()
    return trimmed if trimmed else None


def _normalize_to(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if trimmed else None


def resolve_cron_delivery_plan(job: Any) -> dict[str, Any]:
    """Resolve delivery plan for a cron job.

    Returns a dict with: mode, channel, to, source, requested.
    Mirrors TS resolveCronDeliveryPlan.
    """
    from .types import AgentTurnPayload, CronDelivery

    payload = job.payload if isinstance(job.payload, AgentTurnPayload) else None
    delivery: CronDelivery | None = job.delivery if isinstance(job.delivery, CronDelivery) else None

    raw_mode = delivery.mode if delivery else None
    if isinstance(raw_mode, str):
        m = raw_mode.strip().lower()
        mode: str | None = (
            "announce" if m in ("announce", "deliver")
            else "webhook" if m == "webhook"
            else "none" if m == "none"
            else None
        )
    else:
        mode = None

    payload_channel = _normalize_channel(payload.channel if payload else None)
    payload_to = _normalize_to(payload.to if payload else None)
    delivery_channel = _normalize_channel(delivery.channel if delivery else None)
    delivery_to = _normalize_to(delivery.to if delivery else None)

    channel = delivery_channel or payload_channel or "last"
    to = delivery_to or payload_to

    if delivery is not None:
        resolved_mode = mode or "announce"
        return {
            "mode": resolved_mode,
            "channel": channel if resolved_mode == "announce" else None,
            "to": to,
            "source": "delivery",
            "requested": resolved_mode == "announce",
        }

    # Legacy payload-level delivery hints
    legacy_deliver = payload.deliver if payload else None
    if legacy_deliver is True:
        legacy_mode = "explicit"
    elif legacy_deliver is False:
        legacy_mode = "off"
    else:
        legacy_mode = "auto"

    has_explicit_target = bool(to)
    requested = legacy_mode == "explicit" or (legacy_mode == "auto" and has_explicit_target)

    return {
        "mode": "announce" if requested else "none",
        "channel": channel,
        "to": to,
        "source": "payload",
        "requested": requested,
    }


# ---------------------------------------------------------------------------
# validate-timestamp helpers
# ---------------------------------------------------------------------------

SCHEDULE_TIMESTAMP_PAST_GRACE_MS = 60_000        # allow up to 60s in the past
SCHEDULE_TIMESTAMP_FUTURE_CAP_YEARS = 100        # reject if > 100 years from now (prevents absurd values)


def validate_schedule_timestamp(schedule: Any, now_ms: int | None = None) -> str | None:
    """Validate an 'at' schedule's timestamp.

    Returns an error message string, or None if valid.
    Changes vs original: 1-minute past grace, 10-year future cap.
    """
    from .types import AtSchedule
    if isinstance(schedule, dict):
        kind = schedule.get("kind") or schedule.get("type")
        if kind != "at":
            return None
        at_val = schedule.get("at") or schedule.get("timestamp") or ""
        schedule = AtSchedule(at=str(at_val), type="at")
    if not isinstance(schedule, AtSchedule):
        return None
    if not schedule.at or not schedule.at.strip():
        return "at schedule requires a non-empty timestamp"
    parsed = parse_absolute_time_ms(schedule.at)
    if parsed is None:
        return f"invalid timestamp: {schedule.at!r}"
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    # Allow up to 60s in the past (grace period)
    if parsed < now_ms - SCHEDULE_TIMESTAMP_PAST_GRACE_MS:
        return f"at schedule timestamp is in the past: {schedule.at!r}"
    # Reject if more than SCHEDULE_TIMESTAMP_FUTURE_CAP_YEARS years in the future
    future_cap_ms = SCHEDULE_TIMESTAMP_FUTURE_CAP_YEARS * 365 * 24 * 3600 * 1000
    if parsed > now_ms + future_cap_ms:
        return f"at schedule timestamp is more than {SCHEDULE_TIMESTAMP_FUTURE_CAP_YEARS} years in the future: {schedule.at!r}"
    return None


def assert_valid_cron_announce_delivery(
    delivery_config: Any,
    configured_channel_ids: list[str] | None = None,
) -> None:
    """Validate announce channel IDs in a cron delivery config.

    Args:
        delivery_config: Delivery config dict or object.
        configured_channel_ids: List of known/configured channel IDs (optional).

    Raises:
        ValueError: If announce references unknown channels.
    """
    if not delivery_config:
        return
    if isinstance(delivery_config, dict):
        announce = delivery_config.get("announce")
    else:
        announce = getattr(delivery_config, "announce", None)
    if not announce:
        return
    if isinstance(announce, str):
        announce_ids = [announce]
    elif isinstance(announce, list):
        announce_ids = [str(a) for a in announce if a]
    else:
        announce_ids = []

    if configured_channel_ids is None:
        return  # Can't validate without a channel list

    known = set(configured_channel_ids)
    for ch_id in announce_ids:
        if ch_id and ch_id not in known:
            raise ValueError(f"announce references unknown channel: {ch_id!r}")


# ---------------------------------------------------------------------------
# normalize.ts — normalizeCronJobCreate / normalizeCronJobPatch
# ---------------------------------------------------------------------------

def _sanitize_agent_id(agent_id: str) -> str:
    """Lower-case, strip, replace spaces with underscores — mirrors sanitizeAgentId."""
    return re.sub(r"[^a-z0-9_\-]", "", agent_id.strip().lower())


def _infer_name(raw: dict[str, Any]) -> str:
    """Infer a human-readable name from schedule and payload (mirrors inferLegacyName)."""
    schedule = raw.get("schedule", {})
    payload = raw.get("payload", {})
    s_kind = schedule.get("kind", schedule.get("type", ""))
    p_kind = payload.get("kind", "")
    if s_kind == "at":
        at_val = schedule.get("at", schedule.get("timestamp", "?"))
        return f"One-time at {at_val}"
    if s_kind == "every":
        every_ms = schedule.get("every_ms") or schedule.get("everyMs") or 0
        secs = int(every_ms) // 1000
        return f"Every {secs}s"
    if s_kind == "cron":
        expr = schedule.get("expr") or schedule.get("expression", "?")
        return f"Cron {expr}"
    if p_kind == "systemEvent":
        text = payload.get("text", "")
        return (text[:50] + "…") if len(text) > 50 else text or "System event"
    if p_kind == "agentTurn":
        msg = payload.get("message") or payload.get("prompt", "")
        return (msg[:50] + "…") if len(msg) > 50 else msg or "Agent turn"
    return "Unnamed job"


def _coerce_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    """Normalize a schedule dict — mirrors coerceSchedule in TS normalize.ts."""
    nxt = dict(schedule)
    raw_kind = str(schedule.get("kind", schedule.get("type", ""))).strip().lower()
    kind: str | None = raw_kind if raw_kind in ("at", "every", "cron") else None

    # Auto-detect kind
    if not kind:
        if "atMs" in schedule or "at" in schedule:
            kind = "at"
        elif "everyMs" in schedule or "every_ms" in schedule or "interval_ms" in schedule:
            kind = "every"
        elif "expr" in schedule or "expression" in schedule:
            kind = "cron"
    if kind:
        nxt["kind"] = kind

    # Normalize "at" field
    at_raw = schedule.get("at") or schedule.get("timestamp")
    at_ms_raw = schedule.get("atMs")
    parsed_at_ms: int | None = None
    if isinstance(at_ms_raw, (int, float)):
        parsed_at_ms = int(at_ms_raw)
    elif isinstance(at_ms_raw, str):
        parsed_at_ms = parse_absolute_time_ms(at_ms_raw)
    elif isinstance(at_raw, str) and at_raw.strip():
        parsed_at_ms = parse_absolute_time_ms(at_raw)

    if isinstance(at_raw, str) and at_raw.strip():
        nxt["at"] = (
            datetime.fromtimestamp(parsed_at_ms / 1000, tz=timezone.utc).isoformat()
            if parsed_at_ms is not None
            else at_raw
        )
    elif parsed_at_ms is not None:
        nxt["at"] = datetime.fromtimestamp(parsed_at_ms / 1000, tz=timezone.utc).isoformat()
    nxt.pop("atMs", None)
    nxt.pop("timestamp", None)
    nxt.pop("type", None)  # prefer "kind"

    # Normalize everyMs/interval_ms → every_ms
    for alias in ("everyMs", "interval_ms", "intervalMs"):
        if alias in nxt and "every_ms" not in nxt:
            nxt["every_ms"] = nxt.pop(alias)
        else:
            nxt.pop(alias, None)

    # Normalize expr/expression
    if "expression" in nxt and "expr" not in nxt:
        nxt["expr"] = nxt.pop("expression")
    else:
        nxt.pop("expression", None)

    # Normalize staggerMs
    stagger = normalize_cron_stagger_ms(nxt.get("staggerMs") or nxt.get("stagger_ms"))
    nxt.pop("staggerMs", None)
    nxt.pop("stagger_ms", None)
    if stagger is not None:
        nxt["stagger_ms"] = stagger

    # Normalize tz/timezone
    if "timezone" in nxt and "tz" not in nxt:
        nxt["tz"] = nxt.pop("timezone")
    else:
        nxt.pop("timezone", None)

    # Normalize anchorMs/anchor
    anchor_ms = nxt.pop("anchorMs", None) or nxt.pop("anchor", None)
    if anchor_ms is not None:
        nxt["anchor_ms"] = int(anchor_ms)

    return nxt


def _coerce_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a payload dict — mirrors coercePayload in TS normalize.ts."""
    nxt = dict(payload)

    # Normalize kind casing
    kind_raw = str(nxt.get("kind", "")).strip().lower()
    if kind_raw == "agentturn":
        nxt["kind"] = "agentTurn"
    elif kind_raw == "systemevent":
        nxt["kind"] = "systemEvent"

    # Auto-detect kind when missing
    if not nxt.get("kind"):
        has_message = isinstance(nxt.get("message"), str) and nxt.get("message", "").strip()
        has_text = isinstance(nxt.get("text"), str) and nxt.get("text", "").strip()
        has_agent_hint = (
            isinstance(nxt.get("model"), str)
            or isinstance(nxt.get("thinking"), str)
            or isinstance(nxt.get("timeoutSeconds"), (int, float))
            or isinstance(nxt.get("timeout_seconds"), (int, float))
            or isinstance(nxt.get("allowUnsafeExternalContent"), bool)
        )
        if has_message:
            nxt["kind"] = "agentTurn"
        elif has_text:
            nxt["kind"] = "systemEvent"
        elif has_agent_hint:
            nxt["kind"] = "agentTurn"

    # Trim message/text
    for field in ("message", "text"):
        if isinstance(nxt.get(field), str):
            trimmed = nxt[field].strip()
            if trimmed:
                nxt[field] = trimmed

    # Handle prompt → message alias
    if "prompt" in nxt and "message" not in nxt:
        nxt["message"] = nxt.pop("prompt")
    else:
        nxt.pop("prompt", None)

    # Normalize model
    if "model" in nxt:
        if isinstance(nxt["model"], str) and nxt["model"].strip():
            nxt["model"] = nxt["model"].strip()
        else:
            del nxt["model"]

    # Normalize thinking
    if "thinking" in nxt:
        if isinstance(nxt["thinking"], str) and nxt["thinking"].strip():
            nxt["thinking"] = nxt["thinking"].strip()
        else:
            del nxt["thinking"]

    # Normalize timeoutSeconds
    raw_ts = nxt.pop("timeout_seconds", None) or nxt.pop("timeoutSeconds", None)
    if raw_ts is not None:
        import math
        if isinstance(raw_ts, (int, float)) and math.isfinite(raw_ts):
            nxt["timeout_seconds"] = max(0, int(raw_ts))

    # Normalize allowUnsafeExternalContent
    raw_unsafe = nxt.pop("allowUnsafeExternalContent", None)
    if raw_unsafe is None:
        raw_unsafe = nxt.pop("allow_unsafe_external_content", None)
    if raw_unsafe is not None:
        if isinstance(raw_unsafe, bool):
            nxt["allow_unsafe_external_content"] = raw_unsafe
        # else drop it

    return nxt


def _coerce_delivery(delivery: dict[str, Any]) -> dict[str, Any]:
    """Normalize a delivery dict — mirrors coerceDelivery in TS normalize.ts."""
    nxt = dict(delivery)
    if isinstance(delivery.get("mode"), str):
        m = delivery["mode"].strip().lower()
        if m == "deliver":
            nxt["mode"] = "announce"
        elif m in ("announce", "none", "webhook"):
            nxt["mode"] = m
        else:
            nxt.pop("mode", None)
    else:
        nxt.pop("mode", None)

    if isinstance(delivery.get("channel"), str):
        trimmed = delivery["channel"].strip().lower()
        if trimmed:
            nxt["channel"] = trimmed
        else:
            nxt.pop("channel", None)

    if isinstance(delivery.get("to"), str):
        trimmed = delivery["to"].strip()
        if trimmed:
            nxt["to"] = trimmed
        else:
            nxt.pop("to", None)

    # target → to alias
    if "target" in nxt and "to" not in nxt:
        nxt["to"] = nxt.pop("target")
    else:
        nxt.pop("target", None)

    # best_effort/bestEffort
    be = nxt.pop("bestEffort", None) or nxt.pop("best_effort", None)
    if isinstance(be, bool):
        nxt["best_effort"] = be

    return nxt


def _normalize_session_target(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    t = raw.strip().lower()
    return t if t in ("main", "isolated") else None


def _normalize_wake_mode(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    t = raw.strip().lower()
    return t if t in ("now", "next-heartbeat") else None


def _copy_top_level_agent_fields(nxt: dict[str, Any], payload: dict[str, Any]) -> None:
    """Copy top-level agent-turn fields into payload if payload lacks them."""
    for field in ("model", "thinking"):
        if not (isinstance(payload.get(field), str) and payload[field].strip()):
            val = nxt.get(field)
            if isinstance(val, str) and val.strip():
                payload[field] = val.strip()
    if not isinstance(payload.get("timeout_seconds"), (int, float)):
        for alias in ("timeout_seconds", "timeoutSeconds"):
            if isinstance(nxt.get(alias), (int, float)):
                payload["timeout_seconds"] = nxt[alias]
                break
    if not isinstance(payload.get("allow_unsafe_external_content"), bool):
        for alias in ("allow_unsafe_external_content", "allowUnsafeExternalContent"):
            if isinstance(nxt.get(alias), bool):
                payload["allow_unsafe_external_content"] = nxt[alias]
                break


def _copy_top_level_legacy_delivery_fields(nxt: dict[str, Any], payload: dict[str, Any]) -> None:
    if not isinstance(payload.get("deliver"), bool) and isinstance(nxt.get("deliver"), bool):
        payload["deliver"] = nxt["deliver"]
    if (
        not isinstance(payload.get("channel"), str)
        and isinstance(nxt.get("channel"), str)
        and nxt["channel"].strip()
    ):
        payload["channel"] = nxt["channel"].strip()
    if (
        not isinstance(payload.get("to"), str)
        and isinstance(nxt.get("to"), str)
        and nxt["to"].strip()
    ):
        payload["to"] = nxt["to"].strip()
    if (
        not isinstance(payload.get("best_effort_deliver"), bool)
        and isinstance(nxt.get("bestEffortDeliver"), bool)
    ):
        payload["best_effort_deliver"] = nxt["bestEffortDeliver"]


def _strip_legacy_top_level_fields(nxt: dict[str, Any]) -> None:
    for key in (
        "model", "thinking", "timeoutSeconds", "timeout_seconds",
        "allowUnsafeExternalContent", "allow_unsafe_external_content",
        "message", "text", "deliver", "channel", "to",
        "bestEffortDeliver", "best_effort_deliver", "provider",
    ):
        nxt.pop(key, None)


def _has_legacy_delivery_hints(payload: dict[str, Any]) -> bool:
    if isinstance(payload.get("deliver"), bool):
        return True
    if isinstance(payload.get("bestEffortDeliver"), bool):
        return True
    if isinstance(payload.get("to"), str) and payload["to"].strip():
        return True
    return False


def _build_delivery_from_legacy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    deliver = payload.get("deliver")
    mode = "none" if deliver is False else "announce"
    result: dict[str, Any] = {"mode": mode}
    ch = payload.get("channel", "")
    if isinstance(ch, str) and ch.strip():
        result["channel"] = ch.strip().lower()
    to = payload.get("to", "")
    if isinstance(to, str) and to.strip():
        result["to"] = to.strip()
    if isinstance(payload.get("bestEffortDeliver"), bool):
        result["best_effort"] = payload["bestEffortDeliver"]
    return result


def _strip_legacy_delivery_fields(payload: dict[str, Any]) -> None:
    for key in ("deliver", "channel", "to", "bestEffortDeliver"):
        payload.pop(key, None)


def normalize_cron_job_input(
    raw: Any,
    apply_defaults: bool = False,
) -> dict[str, Any] | None:
    """Validate and normalize raw cron job input dict.

    When apply_defaults=True (for create), fills in: wakeMode=now, enabled=True,
    sessionTarget inferred from payload, deleteAfterRun for "at" jobs,
    staggerMs for top-of-hour cron, delivery auto-set for isolated agentTurn.

    When apply_defaults=False (for patch), only coerces existing fields.
    """
    if not isinstance(raw, dict):
        return None

    # Unwrap wrapper if any
    base: dict[str, Any]
    if isinstance(raw.get("data"), dict):
        base = raw["data"]
    elif isinstance(raw.get("job"), dict):
        base = raw["job"]
    else:
        base = raw

    nxt: dict[str, Any] = dict(base)

    # agentId
    if "agentId" in base or "agent_id" in base:
        agent_id = base.get("agentId") or base.get("agent_id")
        if agent_id is None:
            nxt["agent_id"] = None
        elif isinstance(agent_id, str):
            trimmed = agent_id.strip()
            if trimmed:
                nxt["agent_id"] = _sanitize_agent_id(trimmed)
            else:
                nxt.pop("agent_id", None)
        nxt.pop("agentId", None)

    # sessionKey
    if "sessionKey" in base or "session_key" in base:
        sk = base.get("sessionKey") or base.get("session_key")
        if sk is None:
            nxt["session_key"] = None
        elif isinstance(sk, str):
            trimmed = sk.strip()
            if trimmed:
                nxt["session_key"] = trimmed
            else:
                nxt.pop("session_key", None)
        nxt.pop("sessionKey", None)

    # enabled
    if "enabled" in base:
        enabled = base["enabled"]
        if isinstance(enabled, bool):
            nxt["enabled"] = enabled
        elif isinstance(enabled, str):
            t = enabled.strip().lower()
            if t == "true":
                nxt["enabled"] = True
            elif t == "false":
                nxt["enabled"] = False

    # sessionTarget
    if "sessionTarget" in base or "session_target" in base:
        raw_st = base.get("sessionTarget") or base.get("session_target")
        normalized_st = _normalize_session_target(raw_st)
        if normalized_st:
            nxt["session_target"] = normalized_st
        else:
            nxt.pop("session_target", None)
        nxt.pop("sessionTarget", None)

    # wakeMode
    if "wakeMode" in base or "wake_mode" in base:
        raw_wm = base.get("wakeMode") or base.get("wake_mode")
        normalized_wm = _normalize_wake_mode(raw_wm)
        if normalized_wm:
            nxt["wake_mode"] = normalized_wm
        else:
            nxt.pop("wake_mode", None)
        nxt.pop("wakeMode", None)

    # name trimming
    if isinstance(nxt.get("name"), str):
        nxt["name"] = nxt["name"].strip()

    # schedule
    raw_schedule = base.get("schedule")
    if isinstance(raw_schedule, dict):
        nxt["schedule"] = _coerce_schedule(raw_schedule)

    # payload — auto-create from top-level message/text
    if "payload" not in nxt or not isinstance(nxt.get("payload"), dict):
        msg = str(nxt.get("message", "")).strip()
        text = str(nxt.get("text", "")).strip()
        if msg:
            nxt["payload"] = {"kind": "agentTurn", "message": msg}
        elif text:
            nxt["payload"] = {"kind": "systemEvent", "text": text}

    if isinstance(base.get("payload"), dict):
        nxt["payload"] = _coerce_payload(base["payload"])

    # delivery
    if isinstance(base.get("delivery"), dict):
        nxt["delivery"] = _coerce_delivery(base["delivery"])

    # Remove legacy isolation field
    nxt.pop("isolation", None)
    nxt.pop("deleteAfterRun", None)

    # Copy top-level agent-turn + legacy delivery fields into payload
    payload = nxt.get("payload")
    if isinstance(payload, dict) and payload.get("kind") == "agentTurn":
        _copy_top_level_agent_fields(nxt, payload)
        _copy_top_level_legacy_delivery_fields(nxt, payload)
    _strip_legacy_top_level_fields(nxt)

    # ------------------------------------------------------------------
    # Defaults (create only)
    # ------------------------------------------------------------------
    if apply_defaults:
        # wakeMode default = "now"
        if not nxt.get("wake_mode"):
            nxt["wake_mode"] = "now"

        # enabled default = True
        if not isinstance(nxt.get("enabled"), bool):
            nxt["enabled"] = True

        # name inference
        if not (isinstance(nxt.get("name"), str) and nxt["name"].strip()):
            nxt["name"] = _infer_name(nxt)

        # sessionTarget auto-infer from payload kind
        if not nxt.get("session_target") and isinstance(payload, dict):
            kind = payload.get("kind", "")
            if kind == "systemEvent":
                nxt["session_target"] = "main"
            elif kind == "agentTurn":
                nxt["session_target"] = "isolated"

        # deleteAfterRun for "at" jobs
        sched = nxt.get("schedule")
        if isinstance(sched, dict) and sched.get("kind") == "at" and "delete_after_run" not in nxt:
            nxt["delete_after_run"] = True

        # anchor_ms default for "every" jobs — mirrors TS anchorMs = nowMs at creation time
        # Ensures the job fires ~everyMs after creation, not from the Unix epoch.
        if isinstance(sched, dict) and sched.get("kind") == "every" and "anchor_ms" not in sched:
            import time as _time
            sched["anchor_ms"] = int(_time.time() * 1000)

        # stagger for top-of-hour cron
        if isinstance(sched, dict) and sched.get("kind") == "cron":
            explicit_stagger = normalize_cron_stagger_ms(sched.get("stagger_ms"))
            if explicit_stagger is not None:
                sched["stagger_ms"] = explicit_stagger
            else:
                expr = str(sched.get("expr", ""))
                default_stagger = resolve_default_cron_stagger_ms(expr)
                if default_stagger is not None:
                    sched["stagger_ms"] = default_stagger

        # auto-delivery for isolated agentTurn
        session_target = nxt.get("session_target", "")
        payload_kind = payload.get("kind", "") if isinstance(payload, dict) else ""
        is_isolated_agent_turn = (
            session_target == "isolated"
            or (session_target == "" and payload_kind == "agentTurn")
        )
        has_delivery = "delivery" in nxt and nxt["delivery"] is not None
        has_legacy_delivery = _has_legacy_delivery_hints(payload) if isinstance(payload, dict) else False
        
        # Debug logging
        logger.info(f"[normalize] auto-delivery check: session_target={session_target}, payload_kind={payload_kind}")
        logger.info(f"[normalize] is_isolated_agent_turn={is_isolated_agent_turn}, has_delivery={has_delivery}, has_legacy={has_legacy_delivery}")
        
        if not has_delivery and is_isolated_agent_turn and payload_kind == "agentTurn":
            if isinstance(payload, dict) and has_legacy_delivery:
                nxt["delivery"] = _build_delivery_from_legacy_payload(payload)
                _strip_legacy_delivery_fields(payload)
                logger.info(f"[normalize] Set delivery from legacy: {nxt['delivery']}")
            else:
                nxt["delivery"] = {"mode": "announce"}
                logger.info(f"[normalize] Set auto-delivery: announce")

    return nxt


def normalize_cron_job_create(raw: Any) -> dict[str, Any] | None:
    """Normalize raw input for a new cron job (applies defaults)."""
    return normalize_cron_job_input(raw, apply_defaults=True)


def normalize_cron_job_patch(raw: Any) -> dict[str, Any] | None:
    """Normalize raw input for a cron job patch (no defaults)."""
    return normalize_cron_job_input(raw, apply_defaults=False)
