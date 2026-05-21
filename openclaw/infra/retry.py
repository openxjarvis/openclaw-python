"""Unified async retry module with exponential backoff and jitter."""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class RetryConfig:
    """Retry configuration."""

    max_attempts: int = 3
    initial_delay_ms: int = 300
    max_delay_ms: int = 30_000
    backoff_factor: float = 2.0
    jitter: bool = True


def resolve_retry_config(**overrides: Any) -> RetryConfig:
    """Build a RetryConfig with default values overridden by kwargs.

    Defaults:
        max_attempts=3, initial_delay_ms=300, max_delay_ms=30000,
        backoff_factor=2.0, jitter=True.
    """
    cfg = RetryConfig()
    for key, value in overrides.items():
        if hasattr(cfg, key) and value is not None:
            setattr(cfg, key, value)
    return cfg


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    config: RetryConfig | None = None,
    should_retry: Callable[[Exception, int], bool] | None = None,
    retry_after_ms_fn: Callable[[Exception, int], int | None] | None = None,
) -> T:
    """Retry an async function with exponential backoff and optional jitter.

    Args:
        fn: Async function to call (no arguments).
        config: RetryConfig (uses defaults if None).
        should_retry: Optional predicate(exc, attempt) → bool.
                      If None, retries on any Exception.
        retry_after_ms_fn: Optional callback(exc, attempt) → int | None.
                           If provided, its return value overrides the
                           computed backoff delay (None = use computed).

    Returns:
        The return value of fn on success.

    Raises:
        The last exception if all attempts are exhausted.
    """
    cfg = config or RetryConfig()
    last_exc: Exception | None = None

    for attempt in range(1, cfg.max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= cfg.max_attempts:
                break
            if should_retry is not None and not should_retry(exc, attempt):
                break

            # Compute delay
            if retry_after_ms_fn is not None:
                override_ms = retry_after_ms_fn(exc, attempt)
            else:
                override_ms = None

            if override_ms is not None:
                delay_ms = int(override_ms)
            else:
                raw_delay = cfg.initial_delay_ms * (cfg.backoff_factor ** (attempt - 1))
                delay_ms = int(min(raw_delay, cfg.max_delay_ms))
                if cfg.jitter:
                    delay_ms = random.randint(delay_ms // 2, delay_ms)

            logger.debug(
                "retry_async: attempt %d/%d failed (%s), retrying in %dms",
                attempt,
                cfg.max_attempts,
                exc,
                delay_ms,
            )
            await asyncio.sleep(delay_ms / 1000.0)

    raise last_exc  # type: ignore[misc]
