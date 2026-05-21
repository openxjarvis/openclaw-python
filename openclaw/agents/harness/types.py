"""Agent Harness type definitions.

Mirrors TypeScript src/agents/harness/types.ts

An AgentHarness is a pluggable replacement for the embedded PI agent run.
Plugins can register their own harness implementations via api.register_agent_harness().
The built-in "pi" harness delegates to PiAgentRuntime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

# Result classification — mirrors TS result-classification.ts
HarnessResultClassification = Literal["empty", "reasoning-only", "planning-only"]


@dataclass
class AgentHarnessRunAttemptParams:
    """Parameters for a single harness run attempt.

    Mirrors TS EmbeddedRunAttemptParams (the subset passed to harness.runAttempt).
    """

    session_key: str
    session_id: str
    agent_id: str
    provider: str
    model_id: str
    run_id: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    system_prompt: str | None = None
    abort_signal: Any | None = None
    # Draft streaming callbacks (mirrors TS onPartialReply / onBlockReply)
    on_partial_reply: Any | None = None    # Callable[[str, str], None]
    on_block_reply: Any | None = None      # Callable[[dict, str], None]
    on_reasoning_stream: Any | None = None  # Callable[[str], None]
    # Extra provider params
    extra_params: dict[str, Any] = field(default_factory=dict)
    # Harness-level metadata
    agent_harness_id: str | None = None


@dataclass
class AgentHarnessResult:
    """Result from a harness run attempt.

    Mirrors TS AgentHarnessRunResult.
    """

    success: bool
    content: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    stop_reason: str | None = None
    # Classification set by resolveOutcome / classify()
    classification: HarnessResultClassification | None = None
    # The harness that produced this result
    agent_harness_id: str | None = None
    error: Exception | None = None
    raw: Any | None = None


@dataclass
class AgentHarnessSupportContext:
    """Context passed to harness.supports() for auto-selection.

    Mirrors TS AgentHarnessSupportContext.
    """

    provider: str
    model_id: str
    requested_runtime: str = "auto"
    agent_id: str | None = None


@runtime_checkable
class AgentHarness(Protocol):
    """Protocol for agent harness implementations.

    Mirrors TS AgentHarness interface:
      id, label, pluginId?, priority?, supports(), runAttempt(),
      classify?(), compact?(), reset?(), dispose?()

    Plugins implement this protocol and register via api.register_agent_harness().
    The built-in "pi" harness is created by create_pi_agent_harness().
    """

    @property
    def id(self) -> str:
        """Unique harness id (e.g. "pi", "codex", "my-plugin-harness")."""
        ...

    @property
    def label(self) -> str:
        """Human-readable label for UI display."""
        ...

    @property
    def plugin_id(self) -> str | None:
        """Plugin that registered this harness, or None for built-ins."""
        ...

    @property
    def priority(self) -> int:
        """Selection priority in auto mode (higher = preferred). Default 0."""
        ...

    def supports(self, ctx: AgentHarnessSupportContext) -> bool:
        """Return True if this harness can run the given provider/model."""
        ...

    async def run_attempt(self, params: AgentHarnessRunAttemptParams) -> AgentHarnessResult:
        """Execute a single agent turn attempt."""
        ...

    def classify(self, result: AgentHarnessResult) -> HarnessResultClassification | None:
        """Optional: classify the result (empty/reasoning-only/planning-only)."""
        ...

    async def compact(self, session_key: str, messages: list[dict]) -> list[dict]:
        """Optional: compaction hook."""
        ...

    async def reset(self, session_key: str) -> None:
        """Optional: reset session state."""
        ...

    async def dispose(self) -> None:
        """Optional: cleanup when gateway shuts down."""
        ...


# Harness runtime policy
HarnessRuntimeId = str  # "pi" | "auto" | plugin-registered id
HarnessFallback = Literal["pi", "none"]


@dataclass
class AgentRuntimeConfig:
    """Per-agent or defaults-level runtime configuration.

    Mirrors TS agentRuntime: { id, fallback } in types.agents.ts.
    Config keys: agents.list[].agentRuntime / agents.defaults.agentRuntime
    Env: OPENCLAW_AGENT_RUNTIME, OPENCLAW_AGENT_HARNESS_FALLBACK
    """

    id: str = "auto"          # "pi" | "auto" | plugin-harness-id
    fallback: HarnessFallback = "pi"


@dataclass
class ResolvedAgentHarnessPolicy:
    """Resolved harness selection policy for a single run.

    Mirrors TS resolveAgentHarnessPolicy result shape.
    """

    runtime: str          # the resolved runtime id
    fallback: HarnessFallback
    is_pinned: bool = False  # True when agentHarnessId session-pin was used
