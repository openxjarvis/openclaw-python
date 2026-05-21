"""Agent Harness selection logic.

Mirrors TypeScript src/agents/harness/selection.ts

Implements the 5-level priority selection algorithm:
  1. Session pin (agentHarnessId) → strict lock
  2. Env OPENCLAW_AGENT_RUNTIME
  3. Per-agent agentRuntime.id + defaults
  4. Specific plugin id → use it or fallback
  5. "auto" → supports() sorted by priority → best match or fallback to PI

Also implements runAgentHarnessAttemptWithFallback() which wraps selection
with V2 lifecycle execution.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .builtin_pi import PI_HARNESS_ID, create_pi_agent_harness
from .registry import (
    get_agent_harness,
    get_pinned_session_harness,
    list_registered_agent_harnesses,
    pin_session_harness,
    register_global_agent_harness,
)
from .types import (
    AgentHarness,
    AgentHarnessRunAttemptParams,
    AgentHarnessResult,
    AgentHarnessSupportContext,
    AgentRuntimeConfig,
    HarnessFallback,
    ResolvedAgentHarnessPolicy,
)
from .v2 import run_harness_v2

logger = logging.getLogger(__name__)

# Environment variables (mirrors TS)
_ENV_AGENT_RUNTIME = "OPENCLAW_AGENT_RUNTIME"
_ENV_AGENT_HARNESS_FALLBACK = "OPENCLAW_AGENT_HARNESS_FALLBACK"


def _ensure_pi_registered() -> AgentHarness:
    """Ensure the built-in PI harness is registered and return it."""
    pi = get_agent_harness(PI_HARNESS_ID)
    if pi is None:
        pi = create_pi_agent_harness()
        try:
            register_global_agent_harness(pi)
        except ValueError:
            pi = get_agent_harness(PI_HARNESS_ID)  # race — already registered
    return pi


def resolve_agent_runtime_config(
    config: Any | None,
    agent_id: str | None,
) -> AgentRuntimeConfig:
    """Resolve the effective AgentRuntimeConfig for a given agent.

    Priority:
      1. Env OPENCLAW_AGENT_RUNTIME / OPENCLAW_AGENT_HARNESS_FALLBACK
      2. Per-agent config (agents.list[agent_id].agentRuntime)
      3. agents.defaults.agentRuntime
      4. Defaults: { id: "auto", fallback: "pi" }
    """
    # Env overrides everything
    env_runtime = os.environ.get(_ENV_AGENT_RUNTIME, "").strip()
    env_fallback = os.environ.get(_ENV_AGENT_HARNESS_FALLBACK, "").strip()

    base_id = "auto"
    base_fallback: HarnessFallback = "pi"

    if config is not None:
        # Try per-agent config first
        agents_list = getattr(config, "agents", None)
        if agents_list and agent_id:
            agent_configs = getattr(agents_list, "list", None) or []
            for ac in agent_configs:
                ac_id = getattr(ac, "id", None) or getattr(ac, "agentId", None)
                if ac_id == agent_id:
                    ar = getattr(ac, "agent_runtime", None) or getattr(ac, "agentRuntime", None)
                    if ar:
                        base_id = getattr(ar, "id", base_id)
                        base_fallback = getattr(ar, "fallback", base_fallback)
                    break

        # Try defaults
        defaults = getattr(getattr(config, "agents", None), "defaults", None)
        if defaults:
            ar = getattr(defaults, "agent_runtime", None) or getattr(defaults, "agentRuntime", None)
            if ar and base_id == "auto":
                base_id = getattr(ar, "id", base_id)
                base_fallback = getattr(ar, "fallback", base_fallback)

    # Env wins
    if env_runtime:
        base_id = env_runtime
    if env_fallback in ("pi", "none"):
        base_fallback = env_fallback  # type: ignore[assignment]

    return AgentRuntimeConfig(id=base_id, fallback=base_fallback)


def resolve_pinned_agent_harness_policy(
    agent_harness_id: str,
) -> ResolvedAgentHarnessPolicy:
    """Resolve policy from a session pin.

    Mirrors TS resolvePinnedAgentHarnessPolicy().
    A non-empty, non-"auto" pin locks strictly to that harness with no fallback.
    """
    return ResolvedAgentHarnessPolicy(
        runtime=agent_harness_id,
        fallback="none",
        is_pinned=True,
    )


def resolve_agent_harness_policy(
    config: Any | None,
    agent_id: str | None,
) -> ResolvedAgentHarnessPolicy:
    """Resolve harness policy from config/env (no session pin).

    Mirrors TS resolveAgentHarnessPolicy().
    """
    rc = resolve_agent_runtime_config(config, agent_id)
    return ResolvedAgentHarnessPolicy(
        runtime=rc.id,
        fallback=rc.fallback,
        is_pinned=False,
    )


def select_agent_harness(
    provider: str,
    model_id: str,
    config: Any | None = None,
    agent_id: str | None = None,
    session_key: str | None = None,
    agent_harness_id: str | None = None,
) -> AgentHarness:
    """Select the appropriate harness for a run.

    Mirrors TS selectAgentHarness().

    Selection algorithm (5 levels):
      1. agent_harness_id (session pin) non-empty, non-"auto" → pin strictly
      2. Env OPENCLAW_AGENT_RUNTIME
      3. Per-agent agentRuntime.id + defaults
      4. "pi" → built-in PI harness
      5. Specific plugin id → use it; not found + fallback!="none" → warn + PI
      6. "auto" → evaluate supports() sorted by priority desc; fallback if none
    """
    # Ensure PI is always available
    pi_harness = _ensure_pi_registered()

    # Step 1: Session pin
    pinned = None
    if agent_harness_id and agent_harness_id != "auto":
        policy = resolve_pinned_agent_harness_policy(agent_harness_id)
    elif session_key:
        pinned_id = get_pinned_session_harness(session_key)
        if pinned_id and pinned_id != "auto":
            policy = resolve_pinned_agent_harness_policy(pinned_id)
        else:
            policy = resolve_agent_harness_policy(config, agent_id)
    else:
        policy = resolve_agent_harness_policy(config, agent_id)

    runtime = policy.runtime
    fallback = policy.fallback

    # Step 2-3: Resolve by runtime id
    if runtime == PI_HARNESS_ID:
        return pi_harness

    if runtime != "auto":
        # Specific harness id requested
        harness = get_agent_harness(runtime)
        if harness is not None:
            return harness
        # Not found
        if fallback == "none":
            raise ValueError(
                f"Agent harness '{runtime}' is not registered and fallback is 'none'."
            )
        logger.warning(
            "Agent harness '%s' not found; falling back to built-in 'pi' harness",
            runtime,
        )
        return pi_harness

    # Step 4: Auto mode — evaluate supports() for all non-PI harnesses
    ctx = AgentHarnessSupportContext(
        provider=provider,
        model_id=model_id,
        requested_runtime="auto",
        agent_id=agent_id,
    )

    candidates: list[tuple[int, str, AgentHarness]] = []
    for h in list_registered_agent_harnesses():
        if h.id == PI_HARNESS_ID:
            continue  # PI is the fallback, not a candidate in auto
        try:
            if h.supports(ctx):
                priority = getattr(h, "priority", 0)
                candidates.append((priority, h.id, h))
        except Exception:
            logger.exception("Error calling supports() on harness '%s'", h.id)

    if candidates:
        # Sort by priority desc, then id asc for determinism
        candidates.sort(key=lambda x: (-x[0], x[1]))
        selected = candidates[0][2]
        logger.debug(
            "Auto-selected harness '%s' for provider='%s' model='%s'",
            selected.id,
            provider,
            model_id,
        )
        return selected

    # No candidate found
    if fallback == "none":
        raise ValueError(
            f"No registered harness supports provider='{provider}' model='{model_id}' "
            "and fallback is 'none'."
        )

    logger.debug(
        "No harness supports provider='%s' model='%s'; using built-in 'pi'",
        provider,
        model_id,
    )
    return pi_harness


async def run_agent_harness_attempt_with_fallback(
    params: AgentHarnessRunAttemptParams,
    config: Any | None = None,
    diagnostics_emitter=None,
) -> AgentHarnessResult:
    """Select harness and run with V2 lifecycle.

    Mirrors TS runAgentHarnessAttemptWithFallback().

    After running, pins the session to the chosen harness so subsequent
    turns use the same one.
    """
    harness = select_agent_harness(
        provider=params.provider,
        model_id=params.model_id,
        config=config,
        agent_id=params.agent_id,
        session_key=params.session_key,
        agent_harness_id=params.agent_harness_id,
    )

    # Pin this session to the selected harness
    if params.session_key:
        pin_session_harness(params.session_key, harness.id)

    # Set harness id on params so result can carry it
    params.agent_harness_id = harness.id

    result = await run_harness_v2(
        harness=harness,
        params=params,
        diagnostics_emitter=diagnostics_emitter,
    )

    return result
