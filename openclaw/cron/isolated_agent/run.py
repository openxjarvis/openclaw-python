"""Isolated agent execution for cron jobs.

Mirrors TypeScript: openclaw/src/cron/isolated-agent/run.ts

The main entry point `run_cron_isolated_agent_turn` wraps the gateway-provided
`run_agent_fn` callback with orchestration logic:

- Security wrapping for external hook sessions (Gmail, webhook) before the agent turn
- Payload extraction using helpers (pick_summary, pick_last_deliverable, etc.)
- Heartbeat-only response detection to skip no-op delivery
- Messaging tool dedup: skip delivery if agent already sent via tool
- Best-effort delivery flag resolution
- Subagent announce session key resolution
- Subagent followup wiring (wait + fallback read)
- Telemetry normalization from run_agent_fn result
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Messaging tool delivery target matching
# Mirrors TS matchesMessagingToolDeliveryTarget
# ---------------------------------------------------------------------------

def matches_messaging_tool_delivery_target(
    target: dict[str, Any],
    delivery: dict[str, Any],
) -> bool:
    """Return True when a messaging-tool send matches the configured delivery target.

    Prevents duplicate delivery when the agent already sent a message via a
    messaging tool to the exact same recipient.

    Mirrors TS matchesMessagingToolDeliveryTarget.

    Args:
        target: Messaging tool send dict with keys: to, provider, accountId.
        delivery: Resolved delivery dict with keys: channel, to, accountId.
    """
    if not delivery.get("to") or not target.get("to"):
        return False

    channel = (delivery.get("channel") or "").strip().lower()
    provider = (target.get("provider") or "").strip().lower()
    if provider and provider not in ("message", channel):
        return False

    t_account = target.get("accountId") or target.get("account_id")
    d_account = delivery.get("accountId") or delivery.get("account_id")
    if t_account and d_account and t_account != d_account:
        return False

    return target["to"] == delivery["to"]


# ---------------------------------------------------------------------------
# Best-effort delivery flag resolution
# Mirrors TS resolveCronDeliveryBestEffort
# ---------------------------------------------------------------------------

def resolve_cron_delivery_best_effort(job: Any) -> bool:
    """Resolve whether delivery failures should be treated as best-effort.

    Mirrors TS resolveCronDeliveryBestEffort.

    Checks job.delivery.best_effort first, then
    job.payload.best_effort_deliver for legacy agentTurn payloads.
    """
    delivery = getattr(job, "delivery", None)
    if delivery is not None:
        best_effort = getattr(delivery, "best_effort", None)
        if isinstance(best_effort, bool):
            return best_effort

    payload = getattr(job, "payload", None)
    if payload is not None and getattr(payload, "kind", None) == "agentTurn":
        bef = getattr(payload, "best_effort_deliver", None)
        if isinstance(bef, bool):
            return bef

    return False


# ---------------------------------------------------------------------------
# Announce session key resolution
# Mirrors TS resolveCronAnnounceSessionKey
# ---------------------------------------------------------------------------

async def resolve_cron_announce_session_key(
    config: Any,
    agent_id: str,
    fallback_session_key: str,
    delivery: dict[str, Any],
) -> str:
    """Resolve the session key to use when announcing a cron result.

    Falls back to fallback_session_key if no better routing is available.

    Mirrors TS resolveCronAnnounceSessionKey.

    Args:
        config: OpenClaw config object.
        agent_id: Agent identifier.
        fallback_session_key: Default session key to return on error.
        delivery: Dict with channel, to, accountId, threadId.
    """
    to = (delivery.get("to") or "").strip()
    if not to:
        return fallback_session_key

    try:
        from openclaw.agents.outbound_session import resolve_outbound_session_route
        route = await resolve_outbound_session_route(
            config=config,
            channel=delivery.get("channel"),
            agent_id=agent_id,
            account_id=delivery.get("accountId") or delivery.get("account_id"),
            target=to,
            thread_id=delivery.get("threadId") or delivery.get("thread_id"),
        )
        resolved = (route or {}).get("sessionKey", "").strip()
        if resolved:
            return resolved
    except Exception:
        pass

    return fallback_session_key


# ---------------------------------------------------------------------------
# Main entry point
# Mirrors TS runCronIsolatedAgentTurn
# ---------------------------------------------------------------------------

async def run_cron_isolated_agent_turn(
    job: Any,
    run_agent_fn: Callable[..., Awaitable[dict[str, Any]]],
    message: str,
    *,
    session_key: str | None = None,
    config: Any = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Run an isolated agent turn for a cron job with full orchestration.

    Delegates to run_agent_fn (gateway callback) which executes the agent via
    pi_runtime or equivalent, then applies:

    1. Security wrapping: wraps content from external hooks (hook:gmail:, hook:webhook:)
       with injection-detection boundaries before passing to the agent.
    2. Payload extraction: uses helpers to pick the best deliverable text.
    3. Heartbeat detection: skips delivery for HEARTBEAT_OK-only responses.
    4. Messaging tool dedup: skips delivery if agent already sent via tool.
    5. Subagent announce flow: waits for descendant subagents, then announces.
    6. Telemetry: normalizes model/provider/usage from run_agent_fn result.

    Returns dict with:
        status: "ok" | "error" | "skipped"
        summary: str | None
        output_text: str | None
        delivered: bool
        session_id: str | None
        session_key: str | None
        model: str | None
        provider: str | None
        usage: dict | None
        error: str | None
    """
    effective_session_key = session_key or f"cron:{getattr(job, 'id', '?')}"

    # ------------------------------------------------------------------
    # Model override parsing — mirrors TS cron/isolated-agent/run.ts
    # Allows per-job model override via job.model or job.payload.model
    # ------------------------------------------------------------------
    job_model_override: str | None = None
    try:
        _raw_model = getattr(job, "model", None)
        if isinstance(_raw_model, str) and _raw_model:
            job_model_override = _raw_model
        else:
            _payload = getattr(job, "payload", None)
            _payload_model = getattr(_payload, "model", None) if _payload is not None else None
            if isinstance(_payload_model, str) and _payload_model:
                job_model_override = _payload_model
        if job_model_override:
            logger.debug(
                "cron: model override '%s' for job %r",
                job_model_override,
                getattr(job, "id", "?"),
            )
    except Exception:
        pass

    # ------------------------------------------------------------------
    # SECURITY: Wrap external hook content before agent turn
    # Mirrors TS security block in runCronIsolatedAgentTurn (lines 372-407)
    # ------------------------------------------------------------------
    try:
        from .external_content_guard import (
            build_safe_external_prompt,
            detect_suspicious_patterns,
            get_hook_type,
            is_external_hook_session,
        )
        is_external_hook = is_external_hook_session(effective_session_key)
    except ImportError:
        is_external_hook = False

    payload = getattr(job, "payload", None)
    allow_unsafe = (
        getattr(payload, "allow_unsafe_external_content", False)
        if payload is not None
        else False
    )
    should_wrap_external = is_external_hook and not allow_unsafe

    if is_external_hook:
        try:
            suspicious = detect_suspicious_patterns(message)
            if suspicious:
                logger.warning(
                    "[security] Suspicious patterns detected in external hook content "
                    "(session=%s, patterns=%d): %s",
                    effective_session_key,
                    len(suspicious),
                    ", ".join(suspicious[:3]),
                )
        except Exception:
            pass

    if should_wrap_external:
        try:
            hook_type = get_hook_type(effective_session_key)
            message = build_safe_external_prompt(
                content=message,
                source=hook_type,
                job_name=getattr(job, "name", None),
                job_id=getattr(job, "id", None),
            )
        except Exception as exc:
            logger.warning("cron: external content wrapping failed: %s", exc)

    # ------------------------------------------------------------------
    # Execute agent turn via gateway callback
    # ------------------------------------------------------------------
    try:
        result = await run_agent_fn(
            job=job,
            message=message,
            **({"model": job_model_override} if job_model_override else {}),
        )
    except Exception as err:
        logger.error(
            "cron: isolated agent run failed for job %r: %s",
            getattr(job, "id", "?"),
            err,
        )
        return {
            "status": "error",
            "error": str(err),
            "summary": None,
            "output_text": None,
            "delivered": False,
            "session_id": None,
            "session_key": effective_session_key,
            "model": None,
            "provider": None,
            "usage": None,
        }

    # ------------------------------------------------------------------
    # Validate result (defensive check for None or non-dict)
    # ------------------------------------------------------------------
    if not isinstance(result, dict):
        error_msg = f"run_agent_fn returned invalid result type: {type(result)}"
        logger.error("cron: %s", error_msg)
        return {
            "status": "error",
            "error": error_msg,
            "summary": None,
            "output_text": None,
            "delivered": False,
            "session_id": None,
            "session_key": effective_session_key,
            "model": None,
            "provider": None,
            "usage": None,
        }

    # ------------------------------------------------------------------
    # Normalize result keys (support both snake_case and camelCase)
    # ------------------------------------------------------------------
    status = result.get("status") or ("ok" if result.get("success") else "error")
    session_id = result.get("session_id") or result.get("sessionId")
    model = result.get("model")
    provider = result.get("provider")
    usage = result.get("usage")
    error = result.get("error")
    delivered = bool(result.get("delivered"))

    # ------------------------------------------------------------------
    # Extract text/summary from payloads if provided by run_agent_fn
    # Mirrors TS payload helpers block (lines 559-574 in run.ts)
    # ------------------------------------------------------------------
    payloads: list[dict[str, Any]] = result.get("payloads") or []

    try:
        from .helpers import (
            is_heartbeat_only_response,
            pick_last_deliverable_payload,
            pick_last_non_empty_text_from_payloads,
            pick_summary_from_output,
            pick_summary_from_payloads,
            resolve_heartbeat_ack_max_chars,
        )
        has_helpers = True
    except ImportError:
        has_helpers = False

    if payloads and has_helpers:
        summary = pick_summary_from_payloads(payloads) or pick_summary_from_output(
            result.get("output_text") or result.get("outputText")
        )
        output_text = pick_last_non_empty_text_from_payloads(payloads)
        synthesized_text = (output_text or "").strip() or (summary or "").strip() or None

        # Resolve delivery configuration
        delivery = getattr(job, "delivery", None)
        delivery_requested = (
            delivery is not None and getattr(delivery, "mode", "none") != "none"
        )
        best_effort = resolve_cron_delivery_best_effort(job)
        
        # Get agent config defaults (handle both dict and Pydantic model)
        agent_cfg: dict[str, Any] | None = None
        if config:
            try:
                agents_obj = getattr(config, "agents", None)
                if agents_obj is not None:
                    # Handle Pydantic model
                    if hasattr(agents_obj, "defaults"):
                        defaults_obj = getattr(agents_obj, "defaults", None)
                        if isinstance(defaults_obj, dict):
                            agent_cfg = defaults_obj
                        elif defaults_obj is not None:
                            # Convert Pydantic model to dict
                            agent_cfg = getattr(defaults_obj, "__dict__", None) or {}
                    # Handle dict
                    elif isinstance(agents_obj, dict):
                        agent_cfg = agents_obj.get("defaults")
            except Exception:
                pass
        
        if isinstance(agent_cfg, dict):
            ack_max_chars = resolve_heartbeat_ack_max_chars(agent_cfg)
        else:
            ack_max_chars = resolve_heartbeat_ack_max_chars(None)

        # Heartbeat-only skip (mirrors TS lines 576-578)
        skip_heartbeat = delivery_requested and is_heartbeat_only_response(
            payloads, ack_max_chars
        )

        # Messaging tool dedup (mirrors TS lines 579-588)
        skip_messaging_tool = False
        if (
            delivery_requested
            and result.get("did_send_via_messaging_tool")
            and synthesized_text
        ):
            resolved_delivery = result.get("resolved_delivery") or {}
            tool_targets: list[dict[str, Any]] = (
                result.get("messaging_tool_sent_targets") or []
            )
            skip_messaging_tool = any(
                matches_messaging_tool_delivery_target(t, resolved_delivery)
                for t in tool_targets
            )

        if skip_messaging_tool:
            delivered = True

        # Subagent announce flow (mirrors TS lines 661-776)
        if (
            delivery_requested
            and not skip_heartbeat
            and not skip_messaging_tool
            and synthesized_text
            and not delivered
        ):
            try:
                from .subagent_followup import (
                    SILENT_REPLY_TOKEN,
                    expects_subagent_followup,
                    is_likely_interim_cron_message,
                    read_descendant_subagent_fallback_reply,
                    wait_for_descendant_subagent_summary,
                )
                from openclaw.agents.subagent_registry import get_global_registry

                registry = get_global_registry()
                run_started_at_ms = result.get("run_started_at") or 0
                timeout_ms = result.get("timeout_ms") or 60_000
                active_count = registry.count_active_runs_for_session(effective_session_key)
                expected_followup = expects_subagent_followup(synthesized_text)
                initial_text = synthesized_text.strip()
                had_active = active_count > 0

                if active_count > 0 or expected_followup:
                    final_reply = await wait_for_descendant_subagent_summary(
                        session_key=effective_session_key,
                        initial_reply=initial_text,
                        timeout_ms=timeout_ms,
                        observed_active_descendants=(active_count > 0 or expected_followup),
                    )
                    active_count = registry.count_active_runs_for_session(effective_session_key)
                    if (
                        not final_reply
                        and active_count == 0
                        and (had_active or expected_followup)
                    ):
                        final_reply = await read_descendant_subagent_fallback_reply(
                            session_key=effective_session_key,
                            run_started_at=run_started_at_ms,
                        )
                    if final_reply and active_count == 0:
                        output_text = final_reply
                        summary = pick_summary_from_output(final_reply) or summary
                        synthesized_text = final_reply

                # Suppress if subagents still active
                if active_count > 0:
                    return _build_result(
                        status="ok", summary=summary, output_text=output_text,
                        delivered=False, session_id=session_id,
                        session_key=effective_session_key,
                        model=model, provider=provider, usage=usage,
                    )

                # Suppress stale interim messages
                if (
                    (had_active or expected_followup)
                    and synthesized_text.strip() == initial_text
                    and is_likely_interim_cron_message(initial_text)
                    and initial_text.upper() != SILENT_REPLY_TOKEN.upper()
                ):
                    return _build_result(
                        status="ok", summary=summary, output_text=output_text,
                        delivered=False, session_id=session_id,
                        session_key=effective_session_key,
                        model=model, provider=provider, usage=usage,
                    )

                # Suppress silent reply token
                if synthesized_text.upper() == SILENT_REPLY_TOKEN.upper():
                    return _build_result(
                        status="ok", summary=summary, output_text=output_text,
                        delivered=False, session_id=session_id,
                        session_key=effective_session_key,
                        model=model, provider=provider, usage=usage,
                    )

                # Run subagent announce flow (text-only delivery path)
                try:
                    from openclaw.agents.subagent_announce import run_subagent_announce_flow

                    job_id = getattr(job, "id", "?")
                    job_name_str = (
                        getattr(job, "name", "").strip() or f"cron:{job_id}"
                    )
                    run_session_id = session_id or job_id

                    ann_session_key = fallback = effective_session_key
                    if config is not None and agent_id:
                        resolved_delivery_for_ann = result.get("resolved_delivery") or {}
                        ann_session_key = await resolve_cron_announce_session_key(
                            config=config,
                            agent_id=agent_id,
                            fallback_session_key=fallback,
                            delivery=resolved_delivery_for_ann,
                        )

                    did_announce = await run_subagent_announce_flow(
                        child_session_key=effective_session_key,
                        child_run_id=f"{getattr(job, 'id', '?')}:{run_session_id}",
                        requester_session_key=ann_session_key,
                        requester_origin=result.get("resolved_delivery") or {},
                        task=job_name_str,
                        timeout_ms=timeout_ms,
                        cleanup="delete" if getattr(job, "delete_after_run", False) else "keep",
                        round_one_reply=synthesized_text,
                        wait_for_completion=False,
                        announce_type="cron job",
                    )
                    if did_announce:
                        delivered = True
                    elif not best_effort:
                        return _build_result(
                            status="error",
                            error="cron announce delivery failed",
                            summary=summary,
                            output_text=output_text,
                            delivered=False,
                            session_id=session_id,
                            session_key=effective_session_key,
                            model=model,
                            provider=provider,
                            usage=usage,
                        )
                except Exception as ann_err:
                    if not best_effort:
                        return _build_result(
                            status="error",
                            error=str(ann_err),
                            summary=summary,
                            output_text=output_text,
                            delivered=False,
                            session_id=session_id,
                            session_key=effective_session_key,
                            model=model,
                            provider=provider,
                            usage=usage,
                        )
                    logger.warning("cron: announce flow error: %s", ann_err)

            except ImportError:
                pass

    else:
        # Fallback: no payloads returned – use direct text fields from result
        summary = result.get("summary")
        output_text = result.get("output_text") or result.get("outputText")

    final_result = _build_result(
        status=status,
        summary=summary if payloads else result.get("summary"),
        output_text=output_text if payloads else (result.get("output_text") or result.get("outputText")),
        delivered=delivered,
        session_id=session_id,
        session_key=effective_session_key,
        model=model,
        provider=provider,
        usage=usage,
        error=error,
    )

    # Last delivery sentinel — write a marker so the next cron run can detect
    # the previous delivery outcome (mirrors TS lastDeliverySentinel in run.ts).
    if delivered and effective_session_key:
        try:
            import time as _time
            from openclaw.config.paths import resolve_state_dir
            import json as _json
            sentinel_path = resolve_state_dir() / "cron" / "last_delivery" / f"{effective_session_key}.json"
            sentinel_path.parent.mkdir(parents=True, exist_ok=True)
            sentinel_path.write_text(_json.dumps({
                "delivered_at": int(_time.time()),
                "session_key": effective_session_key,
                "job_id": getattr(job, "id", None),
                "status": status,
                "model": model,
            }))
        except Exception:
            pass

    return final_result


def _build_result(
    *,
    status: str,
    summary: str | None = None,
    output_text: str | None = None,
    delivered: bool = False,
    session_id: str | None = None,
    session_key: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    usage: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "summary": summary,
        "output_text": output_text,
        "delivered": delivered,
        "session_id": session_id,
        "session_key": session_key,
        "model": model,
        "provider": provider,
        "usage": usage,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Legacy helpers (kept for backward compatibility / tests)
# ---------------------------------------------------------------------------

def extract_summary(text: str, max_length: int = 200) -> str:
    """Extract a short summary from agent output text."""
    if not text:
        return ""
    paragraphs = text.split("\n\n")
    first = paragraphs[0].strip() if paragraphs else text.strip()
    if len(first) <= max_length:
        return first
    cut = first[:max_length]
    last_space = cut.rfind(" ")
    return (cut[:last_space] + "\u2026") if last_space > 0 else (cut + "\u2026")


def detect_self_sent_via_messaging(messages: list[Any]) -> bool:
    """Check if agent already sent message via a messaging tool call."""
    messaging_tools = {
        "send_telegram_message",
        "send_discord_message",
        "send_slack_message",
        "send_message",
        "channel_send",
    }
    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                name = (tc.get("name") or "").lower()
                if any(mt in name for mt in messaging_tools):
                    return True
    return False


async def post_summary_to_main_session(
    job: Any,
    result: dict[str, Any],
    main_session_callback: Any,
) -> None:
    """Post execution summary back to main session."""
    if result.get("status") not in ("ok", None) or result.get("error"):
        return
    summary = (result.get("summary") or "").strip()
    if not summary:
        return
    message = f"Cron job '{getattr(job, 'name', job)}' completed:\n\n{summary}"
    try:
        if main_session_callback:
            await main_session_callback(message)
    except Exception as e:
        logger.error("cron: error posting summary to main session: %s", e)
