"""Delivery target resolution for cron isolated agent results.

Mirrors TypeScript: openclaw/src/cron/isolated-agent/delivery-target.ts

Key improvements over previous version:
- DeliveryTarget now carries account_id, thread_id, mode and error (aligned with TS)
- resolve_delivery_target now accepts cfg + agent_id for session-store lookup
- Channel account binding resolution (multi-account setups)
- resolve_outbound_target() validation / docking
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...channels.base import BaseChannel
    from ..types import CronJob

logger = logging.getLogger(__name__)

DEFAULT_CHAT_CHANNEL = "telegram"


# ---------------------------------------------------------------------------
# DeliveryTarget – mirrors TS resolve return type (delivery-target.ts)
# ---------------------------------------------------------------------------

@dataclass
class DeliveryTarget:
    """Resolved delivery target.

    Mirrors TS:
        { channel, to?, accountId?, threadId?, mode: "explicit"|"implicit", error? }
    """
    channel: str
    to: str | None = None
    account_id: str | None = None       # TS: accountId
    thread_id: str | int | None = None  # TS: threadId
    mode: str = "implicit"              # TS: "explicit" | "implicit"
    error: Exception | None = None

    # Legacy alias kept for backward compat with callers using .target_id
    @property
    def target_id(self) -> str | None:
        return self.to

    def __repr__(self) -> str:
        parts = [f"channel={self.channel!r}", f"to={self.to!r}", f"mode={self.mode!r}"]
        if self.account_id:
            parts.append(f"account_id={self.account_id!r}")
        if self.thread_id is not None:
            parts.append(f"thread_id={self.thread_id!r}")
        if self.error:
            parts.append(f"error={self.error!r}")
        return f"DeliveryTarget({', '.join(parts)})"


# ---------------------------------------------------------------------------
# resolve_delivery_target
# Mirrors TS resolveDeliveryTarget (delivery-target.ts)
# ---------------------------------------------------------------------------

async def resolve_delivery_target(
    job: "CronJob",
    session_history: list[dict[str, Any]] | None = None,
    *,
    cfg: Any = None,
    agent_id: str | None = None,
) -> DeliveryTarget:
    """Resolve delivery target for a cron job.

    Resolution order (mirrors TS resolveDeliveryTarget):
    1. Explicit delivery.to with a non-"last" channel → explicit mode.
    2. Session store lookup for the job's session_key or agent main session.
    3. Fallback via message channel selection (config-driven).
    4. DEFAULT_CHAT_CHANNEL final fallback.

    Args:
        job: Cron job whose delivery configuration to resolve.
        session_history: Optional legacy session history for "last" channel lookup.
        cfg: OpenClaw config (enables session-store and account-binding lookups).
        agent_id: Agent identifier (used for session key and account bindings).

    Returns:
        DeliveryTarget with channel, to, account_id, thread_id, mode, error.
    """
    if not job.delivery:
        logger.debug("No delivery configuration")
        return DeliveryTarget(channel=DEFAULT_CHAT_CHANNEL, mode="implicit")

    delivery = job.delivery
    requested_channel: str = (delivery.channel or "last").strip().lower()
    explicit_to: str | None = (
        delivery.to.strip() if isinstance(delivery.to, str) and delivery.to.strip()
        else None
    )

    # ------------------------------------------------------------------
    # 1. Explicit channel + explicit to → skip session store
    # ------------------------------------------------------------------
    if requested_channel != "last" and explicit_to:
        target = DeliveryTarget(
            channel=requested_channel,
            to=explicit_to,
            mode="explicit",
            # Honor explicit account_id from delivery config (Gap 1 fix)
            account_id=getattr(delivery, "account_id", None) or None,
        )
        if not target.account_id:
            _try_resolve_account_id(target, cfg=cfg, agent_id=agent_id)
        logger.info("cron delivery: explicit target %s", target)
        return target

    # ------------------------------------------------------------------
    # 2. Session store lookup (mirrors TS loadSessionStore + store[sessionKey])
    # ------------------------------------------------------------------
    thread_session_key = (getattr(job, "session_key", None) or "").strip() or None
    store_entry: dict[str, Any] | None = None

    if cfg is not None:
        try:
            store_entry = _load_session_entry(cfg, agent_id, thread_session_key)
        except Exception as exc:
            logger.debug("cron delivery: session store lookup failed: %s", exc)

    if store_entry:
        resolved = _resolve_from_session_entry(
            entry=store_entry,
            requested_channel=requested_channel,
            explicit_to=explicit_to,
            allow_mismatched_last_to=(requested_channel == "last"),
        )
        if resolved.channel and (resolved.to or requested_channel == "last"):
            # Honor explicit account_id from delivery config first (Gap 1 fix)
            explicit_account_id = getattr(delivery, "account_id", None) or None
            if explicit_account_id:
                resolved.account_id = explicit_account_id
            else:
                _try_resolve_account_id(resolved, cfg=cfg, agent_id=agent_id)
            _try_validate_outbound(resolved, cfg=cfg)
            logger.info("cron delivery: session-store target %s", resolved)
            return resolved

    # ------------------------------------------------------------------
    # 3. Fallback via session_history (legacy "last" channel support)
    # ------------------------------------------------------------------
    if requested_channel == "last" and session_history:
        for msg in reversed(session_history):
            if not isinstance(msg, dict):
                continue
            metadata = msg.get("metadata", {}) or {}
            ch = metadata.get("channel")
            tid = metadata.get("chat_id") or metadata.get("user_id")
            if ch and tid:
                target = DeliveryTarget(
                    channel=ch,
                    to=str(tid),
                    mode="implicit",
                )
                _try_resolve_account_id(target, cfg=cfg, agent_id=agent_id)
                logger.info("cron delivery: history-resolved target %s", target)
                return target
        logger.warning("cron delivery: cannot resolve 'last' channel from session history")

    # ------------------------------------------------------------------
    # 4. Config-driven channel selection fallback
    # ------------------------------------------------------------------
    fallback_channel: str = DEFAULT_CHAT_CHANNEL
    if cfg is not None:
        try:
            from openclaw.infra.outbound.channel_selection import resolve_message_channel_selection
            sel = await resolve_message_channel_selection(cfg=cfg)
            if sel and sel.get("channel"):
                fallback_channel = sel["channel"]
        except Exception:
            pass

    if requested_channel == "last" and not explicit_to:
        # No explicit target resolved — return channel-only target (missing to)
        target = DeliveryTarget(channel=fallback_channel, mode="implicit")
        _try_resolve_account_id(target, cfg=cfg, agent_id=agent_id)
        logger.warning("cron delivery: could not determine target recipient for channel %r", fallback_channel)
        return target

    # Channel specified but no target could be resolved
    target = DeliveryTarget(
        channel=requested_channel or fallback_channel,
        to=explicit_to,
        mode="explicit" if explicit_to else "implicit",
    )
    _try_resolve_account_id(target, cfg=cfg, agent_id=agent_id)
    if not explicit_to:
        logger.warning("cron delivery: channel %r specified but no target", requested_channel)
    return target


# ---------------------------------------------------------------------------
# Session entry helpers
# ---------------------------------------------------------------------------

def _load_session_entry(
    cfg: Any,
    agent_id: str | None,
    thread_session_key: str | None,
) -> dict[str, Any] | None:
    """Load a session entry from the store, preferring thread-specific over main."""
    try:
        from openclaw.config.sessions import load_session_store, resolve_store_path, resolve_agent_main_session_key
        aid = (agent_id or "").strip() or "default"
        store_path = resolve_store_path(
            getattr(cfg, "session", {}).get("store") if isinstance(cfg, dict) else None,
            agent_id=aid,
        )
        store = load_session_store(store_path)
        if not store:
            return None

        # Prefer thread-specific session key, fallback to agent main
        main_key = resolve_agent_main_session_key(cfg=cfg, agent_id=aid)
        if thread_session_key and thread_session_key in store:
            return store[thread_session_key]
        if main_key in store:
            return store[main_key]
    except Exception:
        pass
    return None


def _resolve_from_session_entry(
    entry: dict[str, Any],
    requested_channel: str,
    explicit_to: str | None,
    allow_mismatched_last_to: bool,
) -> DeliveryTarget:
    """Extract channel/to/threadId from a session store entry."""
    last_channel = (entry.get("lastChannel") or "").strip().lower() or None
    last_to = (entry.get("lastTo") or "").strip() or None
    thread_id_raw = entry.get("lastThreadId")

    if requested_channel == "last":
        channel = last_channel or DEFAULT_CHAT_CHANNEL
        to = explicit_to or last_to
        mode = "explicit" if explicit_to else "implicit"
    else:
        channel = requested_channel
        to = explicit_to or (last_to if allow_mismatched_last_to else None)
        mode = "explicit" if explicit_to else "implicit"

    thread_id: str | int | None = None
    if thread_id_raw is not None:
        try:
            thread_id = int(thread_id_raw) if str(thread_id_raw).lstrip("-").isdigit() else str(thread_id_raw)
        except Exception:
            thread_id = str(thread_id_raw)

    # Only carry thread_id when explicitly set or delivering to same recipient
    carry_thread = thread_id is not None and (
        entry.get("lastThreadIdExplicit")
        or (to is not None and to == last_to)
    )

    return DeliveryTarget(
        channel=channel or DEFAULT_CHAT_CHANNEL,
        to=to,
        thread_id=thread_id if carry_thread else None,
        mode=mode,
    )


def _try_resolve_account_id(
    target: DeliveryTarget,
    cfg: Any,
    agent_id: str | None,
) -> None:
    """Resolve account_id from agent channel bindings if not already set.

    Mirrors TS buildChannelAccountBindings fallback in delivery-target.ts.
    """
    if target.account_id or not cfg or not agent_id or not target.channel:
        return
    try:
        from openclaw.routing.bindings import build_channel_account_bindings
        from openclaw.routing.session_key import normalize_agent_id
        bindings = build_channel_account_bindings(cfg)
        by_agent = bindings.get(target.channel)
        if by_agent:
            bound = by_agent.get(normalize_agent_id(agent_id))
            if bound and len(bound) > 0:
                target.account_id = bound[0]
    except Exception:
        pass


def _try_validate_outbound(target: DeliveryTarget, cfg: Any) -> None:
    """Validate/dock the outbound target via resolveOutboundTarget.

    Mirrors TS resolveOutboundTarget call in delivery-target.ts (lines 115-128).
    Sets target.error if docking fails.
    """
    if not target.to or not cfg:
        return
    try:
        from openclaw.infra.outbound.targets import resolve_outbound_target
        result = resolve_outbound_target(
            channel=target.channel,
            to=target.to,
            cfg=cfg,
            account_id=target.account_id,
            mode=target.mode,
        )
        if not result.get("ok"):
            err_msg = result.get("error") or "outbound target validation failed"
            target.error = ValueError(err_msg)
            target.to = None
        else:
            target.to = result.get("to") or target.to
    except Exception:
        pass


# ---------------------------------------------------------------------------
# deliver_result – higher-level delivery function (unchanged interface)
# ---------------------------------------------------------------------------

async def deliver_result(
    job: "CronJob",
    result: dict[str, Any],
    get_channel_manager: Any = None,
    channel_registry: dict[str, "BaseChannel"] | None = None,
    session_history: list[dict[str, Any]] | None = None,
    *,
    cfg: Any = None,
    agent_id: str | None = None,
    is_failure_alert: bool = False,
) -> bool:
    """Deliver isolated agent result to channel.

    Args:
        job: Cron job.
        result: Execution result dict.
        get_channel_manager: Optional callable returning channel manager.
        channel_registry: Optional registry of active channels (legacy).
        session_history: Optional session history for "last" channel resolution.
        cfg: OpenClaw config (for session-store lookup).
        agent_id: Agent identifier.
        is_failure_alert: If True, route to failure_destination if configured.

    Returns:
        True if delivery succeeded or was not needed.
    """
    import time as _time

    # Resolve channel registry from channel manager if provided
    if get_channel_manager is not None and channel_registry is None:
        try:
            cm = get_channel_manager()
            if cm and hasattr(cm, "_channels"):
                channel_registry = cm._channels
        except Exception as e:
            logger.warning("cron delivery: failed to get channel registry: %s", e)

    if not job.delivery:
        logger.debug("cron delivery: no delivery configuration")
        return True

    # Check if agent already sent via messaging tool
    if result.get("self_sent", False):
        logger.info("cron delivery: agent already sent via messaging tool")
        return True

    # Webhook delivery mode (Gap 5: implement HTTP POST)
    if job.delivery.mode == "webhook" and job.delivery.to:
        return await _deliver_via_webhook(job, result)

    # Failure alert routing: use failure_destination if set (Gap 4)
    if is_failure_alert and job.delivery.failure_destination:
        fd = job.delivery.failure_destination
        target = DeliveryTarget(
            channel=fd.channel or DEFAULT_CHAT_CHANNEL,
            to=fd.to,
            mode="explicit",
            account_id=getattr(job.delivery, "account_id", None) or None,
        )
        if not target.account_id:
            _try_resolve_account_id(target, cfg=cfg, agent_id=agent_id)
    else:
        # Resolve target via normal resolution
        target = await resolve_delivery_target(
            job, session_history, cfg=cfg, agent_id=agent_id
        )

    if not target.to:
        if target.error:
            msg = str(target.error)
        else:
            msg = "could not resolve delivery target"
        delivery_obj = job.delivery
        if getattr(delivery_obj, "best_effort", False):
            logger.warning("cron delivery: %s (best effort)", msg)
            _update_delivery_state(job, status="skipped", now_ms=int(_time.time() * 1000))
            return True
        logger.error("cron delivery: %s", msg)
        _update_delivery_state(job, status="error", error=msg, now_ms=int(_time.time() * 1000))
        return False

    if not channel_registry:
        logger.error("cron delivery: no channel registry available")
        _update_delivery_state(
            job, status="error", error="no channel registry", now_ms=int(_time.time() * 1000)
        )
        return False

    channel = channel_registry.get(target.channel)

    if not channel:
        error_msg = f"channel '{target.channel}' not found"
        if getattr(job.delivery, "best_effort", False):
            logger.warning("cron delivery: %s (best effort)", error_msg)
            _update_delivery_state(job, status="skipped", now_ms=int(_time.time() * 1000))
            return True
        logger.error("cron delivery: %s", error_msg)
        _update_delivery_state(
            job, status="error", error=error_msg, now_ms=int(_time.time() * 1000)
        )
        return False

    if not channel.is_running():
        error_msg = f"channel '{target.channel}' is not running"
        if getattr(job.delivery, "best_effort", False):
            logger.warning("cron delivery: %s (best effort)", error_msg)
            _update_delivery_state(job, status="skipped", now_ms=int(_time.time() * 1000))
            return True
        logger.error("cron delivery: %s", error_msg)
        _update_delivery_state(
            job, status="error", error=error_msg, now_ms=int(_time.time() * 1000)
        )
        return False

    # Prepare message
    message = format_delivery_message(job, result)

    now_ms = int(_time.time() * 1000)
    try:
        logger.info("cron delivery: sending to %s:%s", target.channel, target.to)
        await channel.send_text(target.to, message)
        logger.info("cron delivery: succeeded")
        _update_delivery_state(job, status="ok", now_ms=now_ms)
        return True
    except Exception as e:
        error_msg = f"delivery failed: {e}"
        if getattr(job.delivery, "best_effort", False):
            logger.warning("cron delivery: %s (best effort)", error_msg, exc_info=True)
            _update_delivery_state(job, status="skipped", now_ms=now_ms)
            return True
        logger.error("cron delivery: %s", error_msg, exc_info=True)
        _update_delivery_state(job, status="error", error=error_msg, now_ms=now_ms)
        return False


async def _deliver_via_webhook(job: "CronJob", result: dict[str, Any]) -> bool:
    """Deliver cron job result via HTTP POST webhook (Gap 5).

    Mirrors TS deliverOutboundPayloads webhook branch.
    POSTs a JSON payload to delivery.to URL.
    """
    import json
    try:
        import aiohttp
    except ImportError:
        logger.error("cron delivery: aiohttp not installed, cannot use webhook delivery")
        return False

    url = job.delivery.to if job.delivery else None  # type: ignore[union-attr]
    if not url:
        logger.error("cron delivery: webhook mode requires delivery.to URL")
        return False

    payload = {
        "jobId": job.id,
        "jobName": job.name,
        "status": result.get("status", "ok"),
        "summary": result.get("summary"),
        "error": result.get("error"),
        "sessionKey": result.get("session_key") or result.get("sessionKey"),
        "model": result.get("model"),
        "provider": result.get("provider"),
    }

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status < 400:
                    logger.info("cron delivery: webhook POST %s → %d", url, resp.status)
                    return True
                body = await resp.text()
                logger.error(
                    "cron delivery: webhook POST %s failed: %d %s", url, resp.status, body[:200]
                )
                return False
    except Exception as e:
        logger.error("cron delivery: webhook POST %s error: %s", url, e)
        return False


def _update_delivery_state(
    job: "CronJob",
    status: str,
    error: str | None = None,
    now_ms: int | None = None,
) -> None:
    """Update job state delivery tracking fields after a delivery attempt."""
    import time as _time
    ts = now_ms if now_ms is not None else int(_time.time() * 1000)
    job.state.last_delivery_status = status  # type: ignore[assignment]
    job.state.last_delivery_error = error
    if status == "ok":
        job.state.last_delivered = ts


def format_delivery_message(job: "CronJob", result: dict[str, Any] | None) -> str:
    """Format a delivery message string for a cron job result.
    
    Args:
        job: The cron job
        result: The result dict (can be None for failure alerts)
        
    Returns:
        Formatted message string
    """
    # Handle None result (shouldn't happen but be defensive)
    if result is None:
        return f"\u26a0\ufe0f Cron job '{job.name}' - no result available"
    
    if not result.get("success") and result.get("error"):
        error = result.get("error", "Unknown error")
        return f"\u26a0\ufe0f Cron job '{job.name}' failed:\n\n{error}"

    summary = result.get("summary") or result.get("full_response") or "No response"

    emoji = "\U0001f916"
    name_lower = (job.name or "").lower()
    if "reminder" in name_lower:
        emoji = "\u23f0\ufe0f"
    elif "alert" in name_lower:
        emoji = "\U0001f514"
    elif "report" in name_lower:
        emoji = "\U0001f4ca"

    return f"{emoji} **{job.name}**\n\n{summary}"
