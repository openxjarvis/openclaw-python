"""Agent Harness V2 lifecycle adapter.

Mirrors TypeScript src/agents/harness/v2.ts

Wraps any AgentHarness into the V2 lifecycle:
  prepare → start → send (runAttempt) → resolveOutcome (classify) → cleanup

Emits diagnostic events:
  harness.run.started / harness.run.completed / harness.run.error
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .result_classification import classify_harness_result
from .types import AgentHarness, AgentHarnessResult, AgentHarnessRunAttemptParams

logger = logging.getLogger(__name__)

HarnessV2State = Literal["idle", "prepared", "started", "completed", "error"]


@dataclass
class AgentHarnessV2:
    """V2 lifecycle wrapper around an AgentHarness.

    Mirrors TS AgentHarnessV2 interface from v2.ts.
    Adds prepare/start/cleanup phases and diagnostic event emission.
    """

    harness: AgentHarness
    state: HarnessV2State = "idle"
    started_at: float | None = None
    completed_at: float | None = None
    _diagnostics_emitter: Callable | None = field(default=None, repr=False)

    def emit_diagnostic(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Emit a diagnostic event if emitter is configured."""
        if self._diagnostics_emitter:
            try:
                self._diagnostics_emitter(event, data or {})
            except Exception:
                logger.exception("Error emitting harness diagnostic '%s'", event)

    async def prepare(self) -> None:
        """Prepare phase — transition from idle to prepared."""
        if self.state != "idle":
            raise RuntimeError(f"Cannot prepare harness in state '{self.state}'")
        self.state = "prepared"

    async def start(self) -> None:
        """Start phase — transition from prepared to started."""
        if self.state != "prepared":
            raise RuntimeError(f"Cannot start harness in state '{self.state}'")
        self.state = "started"
        self.started_at = time.monotonic()

    async def send(self, params: AgentHarnessRunAttemptParams) -> AgentHarnessResult:
        """Execute the run attempt (the actual harness.runAttempt call)."""
        if self.state != "started":
            raise RuntimeError(f"Cannot send in harness state '{self.state}'")
        return await self.harness.run_attempt(params)

    def resolve_outcome(self, result: AgentHarnessResult) -> AgentHarnessResult:
        """Apply classification to the result.

        Mirrors TS resolveOutcome() — calls harness.classify() or falls back
        to the built-in classifier.
        """
        if result.agent_harness_id is None:
            result.agent_harness_id = self.harness.id

        classify = getattr(self.harness, "classify", None)
        if callable(classify):
            try:
                result.classification = classify(result)
            except Exception:
                logger.exception("Error in harness classify()")
                result.classification = classify_harness_result(result)
        else:
            result.classification = classify_harness_result(result)

        return result

    async def cleanup(self) -> None:
        """Cleanup phase."""
        self.state = "completed"
        self.completed_at = time.monotonic()


async def run_harness_v2(
    harness: AgentHarness,
    params: AgentHarnessRunAttemptParams,
    diagnostics_emitter: Callable | None = None,
) -> AgentHarnessResult:
    """Run a harness through the full V2 lifecycle.

    Mirrors TS runAgentHarnessAttemptV2().

    Emits diagnostic events:
      harness.run.started  — when run starts
      harness.run.completed — on success
      harness.run.error    — on exception
    """
    v2 = AgentHarnessV2(
        harness=harness,
        _diagnostics_emitter=diagnostics_emitter,
    )

    run_meta = {
        "harnessId": harness.id,
        "sessionKey": params.session_key,
        "modelId": params.model_id,
        "provider": params.provider,
    }

    await v2.prepare()
    await v2.start()

    v2.emit_diagnostic("harness.run.started", run_meta)

    try:
        result = await v2.send(params)
        result = v2.resolve_outcome(result)
        await v2.cleanup()
        v2.emit_diagnostic("harness.run.completed", {
            **run_meta,
            "classification": result.classification,
            "durationMs": int((v2.completed_at - v2.started_at) * 1000) if v2.started_at and v2.completed_at else None,
        })
        return result
    except Exception as exc:
        v2.state = "error"
        v2.emit_diagnostic("harness.run.error", {
            **run_meta,
            "error": str(exc),
        })
        raise
