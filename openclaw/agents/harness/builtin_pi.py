"""Built-in 'pi' Agent Harness.

Mirrors TypeScript src/agents/harness/builtin-pi.ts

The "pi" harness is the default embedded agent execution path.
It delegates to PiAgentRuntime.run_turn() in gateway/pi_runtime.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .types import (
    AgentHarness,
    AgentHarnessResult,
    AgentHarnessRunAttemptParams,
    AgentHarnessSupportContext,
)

logger = logging.getLogger(__name__)

PI_HARNESS_ID = "pi"


@dataclass
class PiAgentHarness:
    """Built-in PI embedded harness.

    Mirrors TS createPiAgentHarness() result.
    All models/providers are supported by the PI path (fallback harness).
    """

    @property
    def id(self) -> str:
        return PI_HARNESS_ID

    @property
    def label(self) -> str:
        return "Pi Embedded"

    @property
    def plugin_id(self) -> str | None:
        return None  # built-in

    @property
    def priority(self) -> int:
        return 0  # lowest priority — only chosen as fallback

    def supports(self, ctx: AgentHarnessSupportContext) -> bool:
        """PI harness supports everything (it's the universal fallback)."""
        return True

    async def run_attempt(self, params: AgentHarnessRunAttemptParams) -> AgentHarnessResult:
        """Delegate to PiAgentRuntime.run_turn().

        We import lazily to avoid circular imports (pi_runtime imports from agents/).
        """
        try:
            from openclaw.gateway.pi_runtime import PiAgentRuntime
        except ImportError:
            logger.warning("PiAgentRuntime not available, using stub response")
            return AgentHarnessResult(
                success=False,
                error=RuntimeError("PiAgentRuntime not available"),
                agent_harness_id=PI_HARNESS_ID,
            )

        runtime = PiAgentRuntime.get_instance()
        if runtime is None:
            return AgentHarnessResult(
                success=False,
                error=RuntimeError("PiAgentRuntime instance not initialized"),
                agent_harness_id=PI_HARNESS_ID,
            )

        try:
            result = await runtime.run_turn(
                session_key=params.session_key,
                session_id=params.session_id,
                messages=params.messages,
                tools=params.tools,
                system_prompt=params.system_prompt,
                provider=params.provider,
                model_id=params.model_id,
                run_id=params.run_id,
                abort_signal=params.abort_signal,
                on_partial_reply=params.on_partial_reply,
                on_block_reply=params.on_block_reply,
                on_reasoning_stream=params.on_reasoning_stream,
                extra_params=params.extra_params,
            )
            return AgentHarnessResult(
                success=True,
                content=result.get("content"),
                tool_calls=result.get("tool_calls", []),
                usage=result.get("usage"),
                stop_reason=result.get("stop_reason"),
                raw=result,
                agent_harness_id=PI_HARNESS_ID,
            )
        except Exception as exc:
            return AgentHarnessResult(
                success=False,
                error=exc,
                agent_harness_id=PI_HARNESS_ID,
            )

    def classify(self, result: AgentHarnessResult):
        return None  # built-in classifier handles this

    async def compact(self, session_key: str, messages: list[dict]) -> list[dict]:
        return messages  # delegate to compaction module

    async def reset(self, session_key: str) -> None:
        pass

    async def dispose(self) -> None:
        pass


def create_pi_agent_harness() -> PiAgentHarness:
    """Create and return the built-in PI harness instance.

    Mirrors TS createPiAgentHarness().
    """
    return PiAgentHarness()
