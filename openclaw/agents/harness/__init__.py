"""Agent Harness subsystem.

Mirrors TypeScript src/agents/harness/

The harness system is a pluggable replacement for the embedded PI agent run.
Plugins can register their own harness implementations to handle specific
providers/models (e.g. codex, custom inference backends).

The built-in "pi" harness delegates to PiAgentRuntime and is always available
as the universal fallback.

Usage:
    from openclaw.agents.harness import select_agent_harness, register_agent_harness

    # In a plugin:
    class MyHarness:
        id = "my-harness"
        label = "My Custom Harness"
        ...
    api.register_agent_harness(MyHarness())

    # At run time:
    harness = select_agent_harness(provider, model_id, config, agent_id, session_key)
    result = await run_agent_harness_attempt_with_fallback(params)
"""
from .builtin_pi import PI_HARNESS_ID, PiAgentHarness, create_pi_agent_harness
from .registry import (
    clear_harness_registry,
    dispose_registered_agent_harnesses,
    get_agent_harness,
    get_pinned_session_harness,
    list_registered_agent_harnesses,
    pin_session_harness,
    register_global_agent_harness,
    reset_registered_agent_harness_sessions,
)
from .result_classification import classify_harness_result
from .selection import (
    resolve_agent_harness_policy,
    resolve_agent_runtime_config,
    resolve_pinned_agent_harness_policy,
    run_agent_harness_attempt_with_fallback,
    select_agent_harness,
)
from .types import (
    AgentHarness,
    AgentHarnessResult,
    AgentHarnessRunAttemptParams,
    AgentHarnessSupportContext,
    AgentRuntimeConfig,
    HarnessFallback,
    HarnessResultClassification,
    ResolvedAgentHarnessPolicy,
)
from .v2 import AgentHarnessV2, run_harness_v2

__all__ = [
    # Types
    "AgentHarness",
    "AgentHarnessResult",
    "AgentHarnessRunAttemptParams",
    "AgentHarnessSupportContext",
    "AgentRuntimeConfig",
    "AgentHarnessV2",
    "HarnessFallback",
    "HarnessResultClassification",
    "ResolvedAgentHarnessPolicy",
    # Registry
    "register_global_agent_harness",
    "get_agent_harness",
    "list_registered_agent_harnesses",
    "pin_session_harness",
    "get_pinned_session_harness",
    "reset_registered_agent_harness_sessions",
    "dispose_registered_agent_harnesses",
    "clear_harness_registry",
    # Selection
    "select_agent_harness",
    "run_agent_harness_attempt_with_fallback",
    "resolve_agent_harness_policy",
    "resolve_agent_runtime_config",
    "resolve_pinned_agent_harness_policy",
    # V2 lifecycle
    "run_harness_v2",
    # Built-in PI
    "PI_HARNESS_ID",
    "PiAgentHarness",
    "create_pi_agent_harness",
    # Classification
    "classify_harness_result",
]


def initialize_harness_registry() -> None:
    """Initialize the registry with the built-in PI harness.

    Call this once at gateway startup (from bootstrap.py).
    Safe to call multiple times — PI harness is only registered once.
    """
    pi = get_agent_harness(PI_HARNESS_ID)
    if pi is None:
        pi_harness = create_pi_agent_harness()
        register_global_agent_harness(pi_harness)
