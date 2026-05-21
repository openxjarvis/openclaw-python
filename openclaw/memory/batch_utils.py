"""Batch HTTP client configuration utilities.

Mirrors openclaw/src/memory-host-sdk/host/batch-utils.ts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BatchHttpClientConfig:
    """Configuration for batch HTTP API calls.

    Mirrors TS BatchHttpClientConfig.
    """

    base_url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


def normalize_batch_base_url(client: BatchHttpClientConfig) -> str:
    """Strip trailing slash from base URL.  Mirrors TS normalizeBatchBaseUrl()."""
    url = client.base_url or ""
    return url.rstrip("/")


def build_batch_headers(
    client: BatchHttpClientConfig,
    *,
    json: bool = True,
) -> dict[str, str]:
    """Build request headers, optionally including Content-Type: application/json.

    Mirrors TS buildBatchHeaders().
    """
    headers = {k: v for k, v in client.headers.items()}
    if json:
        if "Content-Type" not in headers and "content-type" not in headers:
            headers["Content-Type"] = "application/json"
    else:
        headers.pop("Content-Type", None)
        headers.pop("content-type", None)
    return headers


def split_batch_requests(requests: list[Any], max_requests: int) -> list[list[Any]]:
    """Split a flat list of requests into groups of at most *max_requests*.

    Mirrors TS splitBatchRequests().
    """
    if max_requests <= 0:
        return [requests]
    return [requests[i : i + max_requests] for i in range(0, len(requests), max_requests)]


__all__ = [
    "BatchHttpClientConfig",
    "normalize_batch_base_url",
    "build_batch_headers",
    "split_batch_requests",
]
