"""Cron service bootstrap for Gateway

Aligned with TypeScript openclaw/src/gateway/server-cron.ts (buildGatewayCronService).

Key responsibilities:
1. Resolve store path from config
2. Create CronService with properly wired callbacks:
   - enqueue_system_event  (in-memory queue — mirrors TS system-events.ts)
   - request_heartbeat_now (wake signal)
   - run_heartbeat_once    (drain queue + run agent turn + broadcast)
   - run_isolated_agent_job
   - on_event (broadcast + run log)
3. Load existing jobs from store
4. Return GatewayCronState (start is deferred)
"""
from __future__ import annotations

from openclaw.config.paths import resolve_state_dir

import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..cron import CronService
    from ..cron.types import CronJob
    from .types import GatewayDeps, GatewayCronState, BroadcastFn

logger = logging.getLogger(__name__)


async def build_gateway_cron_service(
    config: dict[str, Any] | Any,
    deps: "GatewayDeps",
    broadcast: "BroadcastFn",
) -> "GatewayCronState":
    """
    Build and initialize cron service for Gateway.

    Matches TypeScript buildGatewayCronService():
    - Wires enqueueSystemEvent, requestHeartbeatNow, runHeartbeatOnce,
      runIsolatedAgentJob, and onEvent callbacks.
    - Loads jobs from disk.
    - Returns GatewayCronState (service.start() is deferred to after
      channel_manager is ready).
    """
    from ..cron import CronService
    from ..cron.store import CronStore, CronRunLog
    from ..cron.isolated_agent.run import run_cron_isolated_agent_turn
    from .types import GatewayCronState

    # ------------------------------------------------------------------
    # Resolve config dict
    # ------------------------------------------------------------------
    config_dict = _resolve_config_dict(config)
    cron_config: dict[str, Any] = (config_dict or {}).get("cron") or {}
    store_path = _resolve_store_path(cron_config)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    log_dir = store_path.parent / "runs"  # Unified to use "runs/" consistently with TS

    logger.info(f"Cron store path: {store_path}")

    cron_enabled = (
        os.getenv("OPENCLAW_SKIP_CRON") != "1"
        and cron_config.get("enabled", True)
    )

    if not cron_enabled:
        logger.info("Cron service is disabled")
        service = CronService(cron_enabled=False)
        return GatewayCronState(cron=service, store_path=store_path, enabled=False)

    # ------------------------------------------------------------------
    # Migrate store if needed
    # ------------------------------------------------------------------
    store = CronStore(store_path)
    store.migrate_if_needed()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_session_key(agent_id: str | None, session_key: str | None) -> str:
        """Resolve the canonical session key for the given agent/session target."""
        if session_key and session_key.strip():
            return session_key.strip()
        # Default: the main WebUI session
        return "main"

    # ------------------------------------------------------------------
    # Callback: enqueue_system_event
    # Mirrors TS: enqueueSystemEvent(text, {sessionKey, contextKey})
    # ------------------------------------------------------------------
    def enqueue_system_event(
        text: str,
        agent_id: str | None = None,
        session_key: str | None = None,
        context_key: str | None = None,
    ) -> None:
        """Enqueue a system event into the in-memory queue for the given session."""
        from openclaw.infra.system_events import enqueue_system_event as _enqueue
        key = _resolve_session_key(agent_id, session_key)
        logger.info(f"cron: enqueue system event to session={key!r}: {text[:80]!r}")
        _enqueue(text, session_key=key, context_key=context_key)

    # ------------------------------------------------------------------
    # Callback: request_heartbeat_now
    # Mirrors TS: requestHeartbeatNow(opts)
    # ------------------------------------------------------------------
    def request_heartbeat_now(
        reason: str | None = None,
        agent_id: str | None = None,
        session_key: str | None = None,
    ) -> None:
        """Schedule an immediate heartbeat run as a fire-and-forget async task."""
        key = _resolve_session_key(agent_id, session_key)
        logger.info(f"cron: request_heartbeat_now reason={reason!r} session={key!r}")
        asyncio.ensure_future(
            _run_heartbeat_async(key, reason=reason, deps=deps, broadcast=broadcast)
        )

    # ------------------------------------------------------------------
    # Callback: run_heartbeat_once
    # Mirrors TS: runHeartbeatOnce(opts) -> HeartbeatRunResult
    # ------------------------------------------------------------------
    async def run_heartbeat_once(
        reason: str | None = None,
        agent_id: str | None = None,
        session_key: str | None = None,
    ) -> dict[str, Any]:
        """Drain the system event queue and run an agent turn on the session."""
        key = _resolve_session_key(agent_id, session_key)
        return await _run_heartbeat_async(key, reason=reason, deps=deps, broadcast=broadcast)

    # ------------------------------------------------------------------
    # Callback: run_isolated_agent_job
    # Matches TS: state.deps.runIsolatedAgentJob({job, message})
    # ------------------------------------------------------------------
    async def run_isolated_agent(job: "CronJob", message: str) -> dict[str, Any]:
        """Run isolated agent for cron job."""
        from openclaw.cron.isolated_agent.session_key import resolve_cron_agent_session_key

        # Resolve job-level identifiers once so _agent_run can use them
        job_agent_id = getattr(job, "agent_id", None) or "default"
        
        # Resolve session key - mirrors TS server-cron.ts:294
        # Always use `cron:${job.id}` for isolated session, NOT job.session_key
        # (job.session_key stores the *delivery* target, not the execution session)
        base_session_key = f"cron:{job.id}"
        job_session_key = resolve_cron_agent_session_key(
            session_key=base_session_key,
            agent_id=job_agent_id,
        )

        async def _agent_run(job: "CronJob", message: str) -> dict[str, Any]:
            try:
                cm = deps.get_channel_manager()
                runtime = cm.default_runtime if cm else None
                if runtime is None:
                    logger.warning("cron: no runtime for isolated agent job")
                    return {
                        "status": "error",
                        "error": "isolated agent runtime not configured",
                        "delivered": False,
                    }

                session = deps.session_manager.get_or_create_session_by_key(job_session_key)
                tools = (cm.tools if cm else None) or []
                system_prompt = cm.system_prompt if cm else None

                # ----------------------------------------------------------
                # Model selection chain: mirrors TS runCronIsolatedAgentTurn
                # Priority: hooks.gmail.model > job.payload.model > session override > default
                # ----------------------------------------------------------
                job_payload = getattr(job, "payload", None)
                model_override: str | None = None

                # 1. hooks.gmail.model override for Gmail hook sessions
                if base_session_key.startswith("hook:gmail:"):
                    try:
                        from openclaw.agents.model_selection import resolve_hooks_gmail_model
                        gmail_model = resolve_hooks_gmail_model(config_dict)
                        if gmail_model:
                            model_override = gmail_model
                    except Exception:
                        pass

                # 2. Job payload model override (agentTurn.model)
                if model_override is None:
                    payload_model = getattr(job_payload, "model", None)
                    if payload_model and isinstance(payload_model, str) and payload_model.strip():
                        model_override = payload_model.strip()

                # 3. Session model override (user-set via /model command)
                if model_override is None:
                    try:
                        session_entry = getattr(session, "entry", None) or {}
                        if isinstance(session_entry, dict):
                            model_override = session_entry.get("modelOverride") or None
                        elif hasattr(session_entry, "modelOverride"):
                            model_override = session_entry.modelOverride or None
                    except Exception:
                        pass

                # payload.fallbacks — model fallback chain (Gap 6)
                payload_fallbacks: list[str] | None = getattr(job_payload, "fallbacks", None)

                # payload.timeout_seconds — per-job agent turn timeout (Gap 6)
                payload_timeout: int | None = getattr(job_payload, "timeout_seconds", None)

                response_text = ""
                
                # Check if the model uses a CLI provider (mirrors TS run.ts line 464-487)
                from openclaw.agents.cli_backends import resolve_cli_backend_ids
                from openclaw.agents.model_selection import get_provider_from_model
                
                provider = get_provider_from_model(model_override) if model_override else None
                cli_backend_ids = resolve_cli_backend_ids(config_dict)
                
                # CLI provider routing
                if provider and provider in cli_backend_ids:
                    from openclaw.agents.cli_runner import run_cli_agent
                    from openclaw.agents.cli_session import get_cli_session_id, set_cli_session_id
                    from openclaw.agents.agent_scope import resolve_agent_workspace_dir
                    
                    # Get session entry
                    session_entry = getattr(session, "entry", None)
                    
                    # Determine if this is a new session
                    is_new_session = session_entry is None or not hasattr(session_entry, "sessionId")
                    
                    # Get CLI session ID (None for new sessions)
                    cli_session_id = None if is_new_session else get_cli_session_id(session_entry, provider)
                    
                    # Resolve workspace directory
                    workspace_dir = resolve_agent_workspace_dir(config_dict, job_agent_id)
                    
                    # Run CLI agent
                    timeout_ms = payload_timeout * 1000 if payload_timeout else None
                    result = await run_cli_agent(
                        session_id=getattr(session, "id", None) or job_session_key,
                        session_key=job_session_key,
                        agent_id=job_agent_id,
                        workspace_dir=str(workspace_dir),
                        config=config_dict,
                        prompt=message,
                        provider=provider,
                        model=model_override,
                        timeout_ms=timeout_ms,
                        run_id=f"cron-{job.id}",
                        extra_system_prompt=system_prompt,
                        cli_session_id=cli_session_id,
                    )
                    
                    # Save new CLI session ID if available
                    if result.get("meta", {}).get("agentMeta", {}).get("sessionId") and session_entry:
                        set_cli_session_id(session_entry, provider, result["meta"]["agentMeta"]["sessionId"])
                    
                    # Extract response text
                    response_text = ""
                    for payload in result.get("payloads", []):
                        if "text" in payload:
                            response_text += payload["text"]
                    
                    # Extract telemetry
                    used_model = model_override
                    used_provider = provider
                    usage = result.get("meta", {}).get("usage")
                else:
                    # Default embedded runtime path
                    run_kwargs: dict[str, Any] = {
                        "tools": tools,
                        "system_prompt": system_prompt,
                    }
                    if model_override:
                        # TS uses 'model' parameter (not 'model_override')
                        # See: openclaw/src/agents/pi-embedded-runner/run.ts line 243
                        run_kwargs["model"] = model_override
                    if payload_fallbacks:
                        run_kwargs["model_fallbacks"] = payload_fallbacks

                    async def _collect_response() -> str:
                        text = ""
                        async for event in runtime.run_turn(session, message, **run_kwargs):
                            evt_type = getattr(event, "type", "")
                            if evt_type in ("text", "text_delta"):
                                data = getattr(event, "data", {}) or {}
                                chunk = data.get("text") or data.get("delta") or ""
                                text += str(chunk) if chunk else ""
                        return text

                    if payload_timeout and payload_timeout > 0:
                        response_text = await asyncio.wait_for(
                            _collect_response(), timeout=float(payload_timeout)
                        )
                    else:
                        response_text = await _collect_response()
                    
                    # Collect basic telemetry if runtime exposes it
                    used_model: str | None = None
                    used_provider: str | None = None
                    usage: dict[str, Any] | None = None
                    try:
                        last_meta = getattr(runtime, "last_run_meta", None)
                        if isinstance(last_meta, dict):
                            used_model = last_meta.get("model") or model_override
                            used_provider = last_meta.get("provider")
                            usage = last_meta.get("usage")
                    except Exception:
                        pass

                # Build payloads array (mirrors TS structure for delivery)
                payloads: list[dict[str, Any]] = []
                if response_text.strip():
                    payloads.append({
                        "text": response_text,
                        "role": "assistant",
                    })

                # Resolve delivery target using complete resolver (mirrors TS)
                # This replaces the simplified _extract_delivery_targets with the full
                # resolve_delivery_target which includes proper fallback chains
                resolved_delivery: dict[str, Any] = {}
                try:
                    from openclaw.cron.isolated_agent.delivery import (
                        resolve_delivery_target,
                        DEFAULT_CHAT_CHANNEL,
                    )
                    from openclaw.routing.session_key import parse_agent_session_key
                    
                    # CRITICAL FIX: Use job.session_key (original creator session) for delivery resolution,
                    # not the constructed cron session key. This mirrors TS version behavior.
                    # The original session key is where we'll find the real delivery target in session store.
                    original_session_key = getattr(job, "session_key", None)
                    lookup_agent_id = job_agent_id
                    
                    # Extract agent_id using standardized parser (mirrors TS normalizeAgentId)
                    if original_session_key:
                        parsed = parse_agent_session_key(original_session_key)
                        if parsed:
                            # Successfully parsed agent: format → use extracted agent_id
                            lookup_agent_id = parsed.agent_id
                        else:
                            # Non-agent: format (e.g., "cron:job-1", "main") → keep job_agent_id
                            logger.debug(
                                f"cron: job.session_key={original_session_key!r} is not an agent session key, "
                                f"using job_agent_id={job_agent_id!r}"
                            )
                    
                    logger.info(f"cron: resolving delivery with original_session_key={original_session_key}, lookup_agent_id={lookup_agent_id}")
                    
                    # Use full delivery resolver with fallback chain:
                    # 1. Session store (thread + main session) - using ORIGINAL agent/session
                    # 2. Session history
                    # 3. Config-driven channel selection
                    # 4. DEFAULT_CHAT_CHANNEL fallback
                    resolved_delivery_target = await resolve_delivery_target(
                        job=job,
                        session_history=None,
                        cfg=config_dict,
                        agent_id=lookup_agent_id,  # Use extracted agent_id from original session
                    )
                    
                    # Build resolved_delivery dict for subagent_announce
                    resolved_delivery = {
                        "channel": resolved_delivery_target.channel,
                        "to": resolved_delivery_target.to,
                    }
                    
                    if resolved_delivery_target.account_id:
                        resolved_delivery["accountId"] = resolved_delivery_target.account_id
                    if resolved_delivery_target.thread_id is not None:
                        resolved_delivery["threadId"] = resolved_delivery_target.thread_id
                    
                    logger.info(f"cron: resolved delivery target: {resolved_delivery}")
                    
                except ImportError:
                    # Fallback to old implementation if imports fail
                    logger.warning("cron: resolve_delivery_target import failed, using fallback")
                    delivery_config = getattr(job, "delivery", None)
                    if delivery_config and getattr(delivery_config, "mode", "none") != "none":
                        channel_mode = getattr(delivery_config, "channel", "last")
                        if channel_mode == "last":
                            running_channels = cm.list_running() if hasattr(cm, "list_running") else []
                            all_keys = _list_all_session_keys(cm)
                            agent_part = _extract_agent_part(job_session_key)
                            targets = _extract_delivery_targets(all_keys, agent_part, running_channels)
                            
                            if targets:
                                channel_id, chat_id, thread_id = targets[0]
                                resolved_delivery = {
                                    "channel": channel_id,
                                    "to": chat_id,
                                }
                                if thread_id is not None:
                                    resolved_delivery["threadId"] = thread_id
                except Exception as e:
                    logger.warning(f"cron: delivery resolution error: {e}", exc_info=True)

                return {
                    "status": "ok",
                    "summary": response_text.strip(),
                    "output_text": response_text,
                    "payloads": payloads,
                    "resolved_delivery": resolved_delivery,
                    "delivered": False,  # Let run_cron_isolated_agent_turn decide
                    "session_id": str(session.id) if hasattr(session, "id") else None,
                    "session_key": job_session_key,
                    "model": used_model or model_override,
                    "provider": used_provider,
                    "usage": usage,
                    "error": None,
                }
            except Exception as e:
                logger.error(f"cron: isolated agent error: {e}", exc_info=True)
                return {"status": "error", "error": str(e), "delivered": False}

        return await run_cron_isolated_agent_turn(
            job=job,
            run_agent_fn=_agent_run,
            message=message,
            session_key=job_session_key,
            config=config,
            agent_id=job_agent_id,
        )

    # ------------------------------------------------------------------
    # Callback: on_event (broadcast + run log on "finished")
    # ------------------------------------------------------------------
    def on_event(event: dict[str, Any]) -> None:
        if not event:
            return
        try:
            broadcast("cron", event, {"dropIfSlow": True})

            action = event.get("action")
            job_id = event.get("jobId")

            if action == "started":
                logger.info(f"Cron job started: {job_id}")
            elif action == "finished":
                status = event.get("status")
                duration_ms = event.get("durationMs", 0)
                logger.info(
                    f"Cron job finished: {job_id}, status={status}, duration={duration_ms}ms"
                )
                if status == "error":
                    logger.error(f"Cron job error {job_id}: {event.get('error')}")

                # Append run log
                try:
                    from openclaw.cron.run_log import resolve_cron_run_log_prune_options
                    
                    runs_dir = log_dir.parent / "runs"
                    
                    # Resolve prune options from config
                    run_log_config = cron_config.get("runLog") if isinstance(cron_config, dict) else None
                    prune_options = resolve_cron_run_log_prune_options(run_log_config)
                    
                    run_log = CronRunLog(runs_dir, job_id, prune_options=prune_options)
                    import time as _time
                    run_log.append({
                        "ts": event.get("ts") or int(_time.time() * 1000),
                        "jobId": job_id,
                        "action": "finished",
                        "status": status,
                        "error": event.get("error"),
                        "summary": event.get("summary"),
                        "runAtMs": event.get("runAtMs"),
                        "durationMs": duration_ms,
                        "nextRunAtMs": event.get("nextRunAtMs"),
                        "sessionId": event.get("sessionId"),
                        "sessionKey": event.get("sessionKey"),
                        "model": event.get("model"),
                        "provider": event.get("provider"),
                        "usage": event.get("usage"),
                    })
                except Exception as e:
                    logger.warning(f"Failed to append run log: {e}")

        except Exception as e:
            logger.error(f"Error handling cron event: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Callback: send_failure_alert
    # Wires CronService failure-alert subsystem to the channel manager.
    # Mirrors TS cron/service.ts failure alert delivery.
    # ------------------------------------------------------------------
    async def send_failure_alert(
        job: "CronJob",
        consecutive_errors: int,
        last_error: str | None,
    ) -> bool:
        """Send failure alert after N consecutive errors.

        Delivers via failure_destination (if set) or normal delivery channel.
        """
        from ..cron.isolated_agent.delivery import deliver_result
        fa = job.failure_alert
        if not fa:
            return False

        # Build alert text
        alert_text = fa.message or (
            f"⚠️ Cron job **{job.name}** has failed {consecutive_errors} times in a row.\n\n"
            f"Last error: {last_error or 'unknown'}"
        )

        # Create synthetic result for delivery
        alert_result = {
            "status": "error",
            "error": last_error,
            "summary": alert_text,
        }

        try:
            cm = deps.get_channel_manager()
            channel_registry = cm._channels if cm and hasattr(cm, "_channels") else None
            ok = await deliver_result(
                job=job,
                result=alert_result,
                channel_registry=channel_registry,
                cfg=config,
                agent_id=job.agent_id,
                is_failure_alert=True,
            )
            if ok:
                logger.info(
                    "cron: failure alert sent for job %r (consecutiveErrors=%d)",
                    job.id, consecutive_errors,
                )
            return ok
        except Exception as e:
            logger.error("cron: send_failure_alert error for job %r: %s", job.id, e)
            return False

    # ------------------------------------------------------------------
    # Create service
    # ------------------------------------------------------------------
    service = CronService(
        store_path=store_path,
        log_dir=log_dir,
        cron_enabled=cron_enabled,
        enqueue_system_event=enqueue_system_event,
        request_heartbeat_now=request_heartbeat_now,
        run_heartbeat_once=run_heartbeat_once,
        run_isolated_agent_job=run_isolated_agent,
        on_event=on_event,
        send_failure_alert=send_failure_alert,
    )

    # Load jobs
    jobs = store.load()
    for job in jobs:
        service.jobs[job.id] = job

    logger.info(f"Cron service initialized with {len(jobs)} jobs (start deferred)")

    return GatewayCronState(
        cron=service,
        store_path=store_path,
        enabled=cron_enabled,
    )


# ---------------------------------------------------------------------------
# Heartbeat execution helper
# ---------------------------------------------------------------------------

async def _run_heartbeat_async(
    session_key: str,
    *,
    reason: str | None,
    deps: Any,
    broadcast: Any,
) -> dict[str, Any]:
    """
    Core heartbeat runner.

    Drains the system event queue for the session, runs an agent turn with the
    queued text(s), broadcasts results to WebSocket clients, and optionally
    delivers via active channels (e.g., Telegram).

    Returns a dict matching TS HeartbeatRunResult:
      {"status": "ran" | "skipped" | "error", "reason": str | None}
    """
    from openclaw.infra.system_events import drain_system_events

    events = drain_system_events(session_key)
    if not events:
        logger.debug(f"cron: heartbeat skipped (no events) for session={session_key!r}")
        return {"status": "skipped", "reason": "no-events"}

    message = "\n".join(events)
    logger.info(
        f"cron: running heartbeat for session={session_key!r}, "
        f"events={len(events)}, reason={reason!r}"
    )

    # -- Get dependencies lazily --
    cm = deps.get_channel_manager()
    if not cm:
        logger.warning("cron: channel_manager not ready, re-queuing system events")
        # Re-enqueue so they can be delivered later
        from openclaw.infra.system_events import enqueue_system_event as _enqueue
        for e in events:
            _enqueue(e, session_key=session_key)
        return {"status": "skipped", "reason": "channel-manager-not-ready"}

    runtime = cm.default_runtime
    if not runtime:
        logger.warning("cron: no runtime available for heartbeat")
        return {"status": "error", "reason": "runtime not available"}

    # -- Get/create session --
    try:
        session = deps.session_manager.get_or_create_session_by_key(session_key)
    except Exception as e:
        logger.error(f"cron: failed to get session {session_key!r}: {e}")
        return {"status": "error", "reason": f"session error: {e}"}

    tools = (cm.tools if cm else None) or []
    system_prompt = cm.system_prompt if cm else None
    run_id = str(uuid.uuid4())

    # -- Broadcast start --
    _broadcast_chat(broadcast, session_key, run_id, "start")

    # -- Save user message to transcript (it's a system event, not a real user msg) --
    # We skip saving the "user" side for system events — just save the agent response.

    # -- Run agent turn --
    response_text = ""
    try:
        from openclaw.events import EventType as _ET
        async for event in runtime.run_turn(
            session,
            message,
            tools=tools,
            system_prompt=system_prompt,
            streaming_behavior="followUp",  # Queue if agent is busy
        ):
            evt_type = getattr(event, "type", "")
            if evt_type in (_ET.TEXT, _ET.TEXT_DELTA, "text", "text_delta"):
                data = getattr(event, "data", {}) or {}
                chunk = data.get("text") or data.get("delta") or ""
                chunk = str(chunk) if chunk else ""
                if chunk:
                    response_text += chunk
                    _broadcast_chat(broadcast, session_key, run_id, "delta", text=chunk)

            elif evt_type in (_ET.ERROR, "error", "agent.error"):
                data = getattr(event, "data", {}) or {}
                err_msg = data.get("message", "Unknown error")
                logger.error(f"cron: heartbeat agent error for session={session_key!r}: {err_msg}")

    except Exception as e:
        logger.error(f"cron: heartbeat run_turn failed: {e}", exc_info=True)
        _broadcast_chat(broadcast, session_key, run_id, "error", error=str(e))
        return {"status": "error", "reason": str(e)}

    # -- Broadcast final --
    _broadcast_chat(broadcast, session_key, run_id, "final")

    # -- Deliver via active channels (e.g., Telegram) --
    if response_text:
        asyncio.create_task(
            _deliver_via_channels(session_key, response_text, cm)
        )

    logger.info(
        f"cron: heartbeat complete for session={session_key!r}, "
        f"response={len(response_text)} chars"
    )
    return {"status": "ran", "reason": reason}


def _broadcast_chat(
    broadcast: Any,
    session_key: str,
    run_id: str,
    state: str,
    *,
    text: str | None = None,
    error: str | None = None,
) -> None:
    """Broadcast a chat event to WebSocket clients."""
    payload: dict[str, Any] = {
        "runId": run_id,
        "sessionKey": session_key,
        "state": state,
    }
    if text is not None:
        payload["message"] = {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "timestamp": int(datetime.now(UTC).timestamp() * 1000),
        }
    if error is not None:
        payload["errorMessage"] = error
    try:
        broadcast("chat", payload, {})
    except Exception as e:
        logger.debug(f"cron: broadcast error: {e}")


async def _deliver_via_channels(
    session_key: str,
    response_text: str,
    cm: Any,
) -> None:
    """
    Deliver the heartbeat response to active channel sessions (e.g. Telegram).

    Uses split_media_from_output to extract MEDIA: tokens from the agent response
    (mirrors TS splitMediaFromOutput / deliverReplies). The cleaned text is sent
    first, then any media files are sent as attachments.
    """
    from pathlib import Path
    from openclaw.auto_reply.media_parse import split_media_from_output
    from openclaw.media.mime import detect_mime, media_kind_from_mime, MediaKind

    try:
        running = cm.list_running() if hasattr(cm, "list_running") else []
        all_session_keys = _list_all_session_keys(cm)
        agent_part = _extract_agent_part(session_key)

        # Extract agent_id from session key for agent-scoped media resolution
        # Mirrors TS cron delivery logic which uses job agent_id
        cron_agent_id = "main"  # Default to main
        try:
            from openclaw.routing.session_key import parse_agent_session_key
            parsed = parse_agent_session_key(session_key)
            if parsed and parsed.agent_id:
                cron_agent_id = parsed.agent_id
        except Exception:
            pass

        # Parse MEDIA: tokens — strip them from display text, collect URLs
        media_result = split_media_from_output(response_text)
        display_text = media_result.text if media_result.text is not None else response_text
        all_media: list[str] = []
        if media_result.media_url:
            all_media.append(media_result.media_url)
        if media_result.media_urls:
            all_media.extend(media_result.media_urls)

        # Mirrors TS resolveSessionDeliveryTarget: use last-used channel from session.
        # Find sessions belonging to this agent and extract their channel+chat_id.
        delivery_targets = _extract_delivery_targets(all_session_keys, agent_part, running)

        for ch_id, chat_id, thread_id in delivery_targets:
            channel = cm.get_channel(ch_id)
            if not channel:
                continue

            # Send text (without MEDIA: lines)
            if display_text:
                try:
                    _send_kwargs: dict = {}
                    if thread_id is not None:
                        _send_kwargs["message_thread_id"] = thread_id
                    await channel.send_text(target=chat_id, text=display_text, **_send_kwargs)
                    logger.info(f"cron: delivered text to {ch_id} chat_id={chat_id}")
                except Exception as e:
                    logger.warning(f"cron: send_text failed {ch_id} chat_id={chat_id}: {e}")

            # Send media files extracted from MEDIA: tokens
            for media_url in all_media:
                try:
                    resolved_url = _resolve_media_url(media_url, None, cron_agent_id)
                    if resolved_url is None:
                        continue
                    mime = detect_mime(resolved_url)
                    kind = media_kind_from_mime(mime)
                    media_type = kind.value if kind != MediaKind.UNKNOWN else "document"
                    await channel.send_media(
                        target=chat_id,
                        media_url=resolved_url,
                        media_type=media_type,
                    )
                    logger.info(
                        f"cron: delivered media {Path(resolved_url).name} "
                        f"(type={media_type}) to {ch_id} chat_id={chat_id}"
                    )
                except Exception as e:
                    logger.warning(
                        f"cron: failed to send media {media_url} to {ch_id} chat_id={chat_id}: {e}"
                    )
    except Exception as e:
        logger.debug(f"cron: _deliver_via_channels error: {e}")


def _extract_delivery_targets(
    all_session_keys: list[str],
    agent_part: str | None,
    running_channel_ids: list[str],
) -> list[tuple[str, str, int | None]]:
    """
    Extract (channel_id, chat_id, thread_id) delivery triples from session store.

    Mirrors TypeScript resolveSessionDeliveryTarget("last"):
    - Loads sessions.json via loadSessionStore
    - Extracts lastChannel/lastTo/lastThreadId from each SessionEntry via deliveryContextFromSession
    - Returns matching delivery targets for running channels

    TypeScript reference:
    - openclaw/src/cron/isolated-agent/delivery-target.ts: resolveDeliveryTarget()
    - openclaw/src/config/sessions/store.ts: loadSessionStore()
    - openclaw/src/utils/delivery-context.ts: deliveryContextFromSession()
    """
    _KNOWN_CHANNELS = {
        "telegram", "feishu", "discord", "whatsapp", "slack",
        "line", "imessage", "lark",
    }
    targets: list[tuple[str, str, int | None]] = []
    seen: set[tuple[str, str]] = set()

    # Normalize agent_part for matching (mirrors TS agent matching logic)
    # "default" should match "main" sessions for backward compatibility
    target_agent = agent_part
    if target_agent == "default":
        target_agent = "main"
    
    logger.info(f"cron: _extract_delivery_targets looking for agent='{target_agent}' (original='{agent_part}')")

    # Load session store (mirrors TS loadSessionStore)
    try:
        from pathlib import Path
        import json

        sessions_file = resolve_state_dir() / "agents" / "main" / "sessions" / "sessions.json"
        if not sessions_file.exists():
            logger.warning(f"cron: session store not found at {sessions_file}")
            return targets

        with open(sessions_file) as f:
            session_store = json.load(f)

        logger.info(f"cron: loaded {len(session_store)} sessions from store")

        # Extract delivery context from each session entry
        # (mirrors TS deliveryContextFromSession)
        for session_key, entry in session_store.items():
            if not isinstance(entry, dict):
                continue

            # Filter by agent (match cron's agent with session's agent)
            # Session keys can be: "main", "agent:main:...", "agent:main:telegram:...", etc.
            session_agent = "main"  # Default for simple keys like "main"
            if isinstance(session_key, str) and session_key.startswith("agent:"):
                parts = session_key.split(":")
                if len(parts) >= 2:
                    session_agent = parts[1]
            
            # Skip sessions from other agents
            if target_agent and session_agent != target_agent:
                logger.debug(f"cron: skipping session {session_key} (agent={session_agent}, want={target_agent})")
                continue

            # Extract last* fields from session entry
            last_channel = entry.get("lastChannel")
            last_to = entry.get("lastTo")
            last_thread_id = entry.get("lastThreadId")

            # Validate channel
            if not last_channel or last_channel not in _KNOWN_CHANNELS:
                logger.debug(f"cron: skipping session {session_key} (no valid lastChannel)")
                continue
            if last_channel not in running_channel_ids:
                logger.debug(f"cron: skipping session {session_key} (channel {last_channel} not running)")
                continue

            # Validate recipient
            if not last_to or not isinstance(last_to, str):
                logger.debug(f"cron: skipping session {session_key} (no valid lastTo)")
                continue

            # Skip test chat IDs for Telegram
            if last_channel == "telegram":
                try:
                    chat_id_num = int(last_to)
                    if 0 < abs(chat_id_num) < 1000:
                        logger.debug(f"cron: skipping test chat_id={last_to} from session {session_key}")
                        continue
                except ValueError:
                    pass

            # Normalize threadId (mirrors TS: can be string | number)
            thread_id: int | None = None
            if last_thread_id is not None:
                if isinstance(last_thread_id, int):
                    thread_id = last_thread_id
                elif isinstance(last_thread_id, str) and last_thread_id.strip():
                    try:
                        thread_id = int(last_thread_id.strip())
                    except ValueError:
                        pass

            # Add unique target
            key = (last_channel, last_to)
            if key not in seen:
                seen.add(key)
                targets.append((last_channel, last_to, thread_id))
                logger.info(f"cron: ✅ delivery target {last_channel} -> {last_to} (session={session_key})")

    except Exception as e:
        logger.error(f"cron: failed to load session store: {e}", exc_info=True)

    if not targets:
        logger.warning(f"cron: no delivery targets found for agent='{target_agent}' (running_channels={running_channel_ids})")
    else:
        logger.info(f"cron: found {len(targets)} delivery target(s)")
    
    return targets


def _resolve_media_url(media_url: str, workspace_dir: str | None, agent_id: str = "main") -> str | None:
    """
    Resolve media URL with agent-scoped local roots.
    
    Mirrors TS resolveSandboxedMediaSource + getAgentScopedMediaLocalRoots.
    
    Args:
        media_url: The media URL/path to resolve
        workspace_dir: Optional workspace directory (deprecated, kept for compatibility)
        agent_id: Agent identifier for scoped root resolution
    
    Returns:
        Resolved absolute path string, or None if not found
    """
    from pathlib import Path
    from urllib.parse import urlparse
    from urllib.request import url2pathname
    
    # Strip MEDIA: prefix if present
    if media_url.upper().startswith("MEDIA:"):
        media_url = media_url[6:].strip()
    
    # HTTP(S) URLs: return as-is
    if media_url.startswith(("http://", "https://")):
        return media_url
    
    # file:// URLs: convert to local path
    if media_url.startswith("file://"):
        try:
            parsed = urlparse(media_url)
            return url2pathname(parsed.path)
        except Exception:
            logger.warning("cron: invalid file:// URL: %s", media_url)
            return None
    
    # Expand ~ to home directory
    p = Path(media_url).expanduser()
    
    # Absolute paths: validate against allowed roots
    if p.is_absolute():
        if p.exists():
            try:
                from openclaw.media.local_roots import get_agent_scoped_media_local_roots, is_path_in_allowed_roots
                from openclaw.config.loader import load_config
                
                cfg = load_config()
                allowed_roots = get_agent_scoped_media_local_roots(cfg, agent_id)
                
                if is_path_in_allowed_roots(p, allowed_roots):
                    logger.info("cron: resolved absolute path '%s' (validated)", media_url)
                    return str(p)
                else:
                    logger.warning("cron: absolute path '%s' outside allowed roots, blocked", media_url)
                    return None
            except Exception as e:
                logger.warning("cron: failed to validate absolute path '%s': %s", media_url, e)
                return None
        else:
            logger.warning("cron: absolute path '%s' does not exist", media_url)
            return None
    
    # Relative paths: search in agent-scoped roots
    try:
        from openclaw.media.local_roots import get_agent_scoped_media_local_roots
        from openclaw.config.loader import load_config
        
        cfg = load_config()
        local_roots = get_agent_scoped_media_local_roots(cfg, agent_id)
        
        for root in local_roots:
            # Try full relative path first (e.g. "presentations/file.pptx")
            candidate = root / p
            if candidate.exists():
                logger.info("cron: resolved relative path '%s' -> %s", media_url, candidate)
                return str(candidate)
            
            # Fall back to filename-only search (mirrors TS local-roots fallback)
            candidate = root / p.name
            if candidate.exists():
                logger.info("cron: resolved filename '%s' -> %s (filename-only fallback)", media_url, candidate)
                return str(candidate)
        
        logger.warning("cron: media file '%s' not found in any agent-scoped root", media_url)
        return None
    except Exception as e:
        logger.error("cron: failed to resolve media URL '%s': %s", media_url, e, exc_info=True)
        return None


def _extract_agent_part(session_key: str) -> str | None:
    """Extract agent identifier from session key like 'main' or 'agent:main:...'."""
    parts = session_key.split(":")
    if len(parts) >= 2 and parts[0] == "agent":
        return parts[1]
    return session_key  # Use as-is for simple keys like "main"



def _list_all_session_keys(cm: Any) -> list[str]:
    """List all known session keys (best-effort)."""
    try:
        sm = getattr(cm, "session_manager", None)
        if sm:
            # Use the session store (most complete) or fallback to _sessions dict
            if hasattr(sm, "_get_session_store"):
                store = sm._get_session_store()
                return list(store.keys()) if store else []
            elif hasattr(sm, "_sessions"):
                return list(sm._sessions.keys())
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _resolve_config_dict(config: Any) -> dict[str, Any]:
    if config is None:
        return {}
    if hasattr(config, "model_dump"):
        return config.model_dump()
    if hasattr(config, "__dict__") and not isinstance(config, dict):
        return config.__dict__
    if isinstance(config, dict):
        return config
    return {}


def _resolve_store_path(cron_config: dict[str, Any] | None) -> Path:
    if not cron_config:
        cron_config = {}
    store_path_str = cron_config.get("store", "~/.openclaw/cron/jobs.json")
    if store_path_str.startswith("~"):
        return Path.home() / store_path_str[2:]
    return Path(store_path_str).expanduser()


def resolve_cron_store_path(config: dict[str, Any] | None) -> Path:
    if not config:
        config = {}
    cron_config = config.get("cron") or {}
    return _resolve_store_path(cron_config)


def is_cron_enabled(config: dict[str, Any] | None) -> bool:
    if os.getenv("OPENCLAW_SKIP_CRON") == "1":
        return False
    if not config:
        return True
    cron_config = config.get("cron") or {}
    return cron_config.get("enabled", True)
