"""Batch status polling helpers.

Mirrors openclaw/src/memory-host-sdk/host/batch-status.ts.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)

TERMINAL_FAILURE_STATES = frozenset({"failed", "expired", "cancelled", "canceled"})


class BatchStatusLike(Protocol):
    """Minimal interface for a batch status dict/object."""

    def get(self, key: str, default: Any = None) -> Any: ...


def resolve_batch_completion_from_status(
    provider: str,
    batch_id: str,
    status: dict[str, Any],
) -> dict[str, str]:
    """Extract output/error file IDs from a completed batch status response.

    Mirrors TS resolveBatchCompletionFromStatus().

    Returns:
        ``{"outputFileId": str, "errorFileId": str | None}``

    Raises:
        ValueError: if the batch completed without an output file.
    """
    output_file_id = status.get("output_file_id")
    if not output_file_id:
        raise ValueError(
            f"{provider} batch {batch_id} completed without output file"
        )
    result: dict[str, str] = {"outputFileId": str(output_file_id)}
    error_file_id = status.get("error_file_id")
    if error_file_id:
        result["errorFileId"] = str(error_file_id)
    return result


def is_terminal_failure_state(state: str | None) -> bool:
    """Return True if *state* is a known terminal failure state."""
    return (state or "").lower() in TERMINAL_FAILURE_STATES


async def poll_batch_until_complete(
    get_status: Any,  # async callable () -> dict
    batch_id: str,
    provider: str = "unknown",
    poll_interval_ms: int = 2_000,
    timeout_ms: int = 300_000,
) -> dict[str, Any]:
    """Poll *get_status* until the batch is complete or fails.

    Mirrors TS throwIfBatchTerminalFailure / polling loop in batch-runner.ts.

    Args:
        get_status: Async callable that returns the latest status dict.
        batch_id: Batch ID (for logging).
        provider: Provider name (for error messages).
        poll_interval_ms: How often to poll.
        timeout_ms: Max total wait time.

    Returns:
        The final status dict when the batch is complete.

    Raises:
        TimeoutError: If the batch does not complete within *timeout_ms*.
        RuntimeError: If the batch ends in a terminal failure state.
    """
    elapsed_ms = 0
    while elapsed_ms < timeout_ms:
        status = await get_status()
        state = str(status.get("status") or "").lower()

        if is_terminal_failure_state(state):
            raise RuntimeError(
                f"{provider} batch {batch_id} ended with terminal failure: {state}"
            )
        if state in {"completed", "done", "finished"}:
            return status
        if status.get("output_file_id"):
            return status

        sleep_ms = min(poll_interval_ms, timeout_ms - elapsed_ms)
        await asyncio.sleep(sleep_ms / 1000.0)
        elapsed_ms += sleep_ms

    raise TimeoutError(
        f"{provider} batch {batch_id} did not complete within {timeout_ms} ms"
    )


__all__ = [
    "TERMINAL_FAILURE_STATES",
    "resolve_batch_completion_from_status",
    "is_terminal_failure_state",
    "poll_batch_until_complete",
]
