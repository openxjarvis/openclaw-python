"""Batch file upload to the embeddings API.

Mirrors openclaw/src/memory-host-sdk/host/batch-upload.ts.
"""
from __future__ import annotations

import json
import logging

from .batch_utils import BatchHttpClientConfig, build_batch_headers, normalize_batch_base_url

logger = logging.getLogger(__name__)


async def upload_batch_jsonl_file(
    client: BatchHttpClientConfig,
    requests: list[object],
    error_prefix: str = "batch",
) -> str:
    """Upload a JSONL batch file and return the file ID.

    Mirrors TS uploadBatchJsonlFile().

    Args:
        client: HTTP client configuration (base_url + headers).
        requests: List of request objects to serialize as JSONL.
        error_prefix: Prefix for error messages.

    Returns:
        The file ID returned by the API (used for creating the batch job).
    """
    try:
        import httpx  # type: ignore[import-untyped]
    except ImportError:
        raise RuntimeError(
            "httpx is required for batch file upload. Install with: pip install httpx"
        )

    base_url = normalize_batch_base_url(client)
    url = f"{base_url}/files"
    jsonl = "\n".join(json.dumps(r) for r in requests)

    # Build multipart form data
    files = {
        "file": ("memory-embeddings.jsonl", jsonl.encode("utf-8"), "application/jsonl"),
    }
    form_data = {"purpose": "batch"}

    # Headers without Content-Type so httpx sets multipart boundary automatically
    headers = build_batch_headers(client, json=False)

    async with httpx.AsyncClient() as http:
        resp = await http.post(url, data=form_data, files=files, headers=headers)
        if not resp.is_success:
            raise RuntimeError(
                f"{error_prefix}: file upload failed with HTTP {resp.status_code}: {resp.text}"
            )
        data = resp.json()
        file_id = data.get("id")
        if not file_id:
            raise RuntimeError(
                f"{error_prefix}: file upload response missing 'id': {data}"
            )
        return str(file_id)


__all__ = ["upload_batch_jsonl_file"]
