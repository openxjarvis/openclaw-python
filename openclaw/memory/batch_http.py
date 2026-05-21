"""Batch HTTP client — fetch and download batch outputs.

Mirrors openclaw/src/memory-host-sdk/host/batch-http.ts.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .batch_utils import BatchHttpClientConfig, build_batch_headers, normalize_batch_base_url

logger = logging.getLogger(__name__)


async def fetch_batch_status(
    client: BatchHttpClientConfig,
    batch_id: str,
) -> dict[str, Any]:
    """GET /v1/batches/{batch_id} and return the JSON response.

    Mirrors TS fetchBatchStatus().
    """
    try:
        import httpx  # type: ignore[import-untyped]
    except ImportError:
        raise RuntimeError("httpx is required for batch HTTP operations. Install with: pip install httpx")

    base_url = normalize_batch_base_url(client)
    url = f"{base_url}/v1/batches/{batch_id}"
    headers = build_batch_headers(client, json=True)
    async with httpx.AsyncClient() as http:
        resp = await http.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def download_batch_output_file(
    client: BatchHttpClientConfig,
    file_id: str,
) -> str:
    """Download a batch output file by *file_id* and return its raw text.

    Mirrors TS downloadBatchOutputFile().
    """
    try:
        import httpx  # type: ignore[import-untyped]
    except ImportError:
        raise RuntimeError("httpx is required for batch HTTP operations. Install with: pip install httpx")

    base_url = normalize_batch_base_url(client)
    url = f"{base_url}/v1/files/{file_id}/content"
    headers = build_batch_headers(client, json=False)
    async with httpx.AsyncClient() as http:
        resp = await http.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text


async def create_batch_job(
    client: BatchHttpClientConfig,
    input_file_id: str,
    endpoint: str = "/v1/embeddings",
) -> dict[str, Any]:
    """Submit a batch job and return the response.

    Mirrors TS createBatchJob() / the OpenAI /v1/batches POST endpoint.
    """
    try:
        import httpx  # type: ignore[import-untyped]
    except ImportError:
        raise RuntimeError("httpx is required for batch HTTP operations. Install with: pip install httpx")

    base_url = normalize_batch_base_url(client)
    url = f"{base_url}/v1/batches"
    headers = build_batch_headers(client, json=True)
    payload = {
        "input_file_id": input_file_id,
        "endpoint": endpoint,
        "completion_window": "24h",
    }
    async with httpx.AsyncClient() as http:
        resp = await http.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


__all__ = [
    "fetch_batch_status",
    "download_batch_output_file",
    "create_batch_job",
]
