"""Batch embedding operations.

Mirrors openclaw/src/memory-host-sdk/host/batch-runner.ts and integrates
all batch sub-modules: upload, status polling, output parsing.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

from .batch_utils import BatchHttpClientConfig, split_batch_requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Simple local batch embed (no remote batch API)
# ---------------------------------------------------------------------------

async def batch_embed(
    texts: list[str],
    embedder: Any,
    batch_size: int = 100,
) -> list[list[float] | None]:
    """Embed texts locally in batches.

    Args:
        texts: Texts to embed.
        embedder: Embedding provider with an ``embed_batch`` coroutine.
        batch_size: Max texts per call.

    Returns:
        List of embeddings (``None`` for failed items).
    """
    results: list[list[float] | None] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        try:
            batch_result = await embedder.embed_batch(batch)
            if hasattr(batch_result, "embeddings"):
                results.extend(batch_result.embeddings)
            else:
                results.extend(batch_result)
        except Exception as exc:
            logger.warning("Batch embedding failed for batch %d: %s", i // batch_size, exc)
            results.extend([None] * len(batch))

    return results


# ---------------------------------------------------------------------------
# Remote batch runner (OpenAI-compatible batch API)
# Mirrors TS runEmbeddingBatchGroups() in batch-runner.ts
# ---------------------------------------------------------------------------

EmbeddingBatchExecutionParams = dict[str, Any]


async def run_embedding_batch_groups(
    requests: list[Any],
    max_requests: int,
    run_group: Callable[
        [dict[str, Any]],
        Awaitable[None],
    ],
    wait: bool = True,
    poll_interval_ms: int = 2_000,
    timeout_ms: int = 300_000,
    concurrency: int = 3,
    debug_label: str = "embedding-batch",
    debug: Callable[[str, dict[str, Any] | None], None] | None = None,
) -> dict[str, list[int]]:
    """Split *requests* into groups and run them with concurrency control.

    Mirrors TS runEmbeddingBatchGroups().

    Args:
        requests: Full list of embedding requests.
        max_requests: Max requests per batch group.
        run_group: Async callable ``(args) -> None`` where *args* contains
            ``group``, ``group_index``, ``groups``, ``by_custom_id``.
        wait: Whether to wait for completion (unused for local runs but
            kept for API parity).
        poll_interval_ms: Poll interval when *wait=True*.
        timeout_ms: Total timeout when *wait=True*.
        concurrency: Max parallel batch group runs.
        debug_label: Label for debug log messages.
        debug: Optional debug callback.

    Returns:
        ``by_custom_id`` mapping of custom_id -> list[int] indices.
    """
    if not requests:
        return {}

    groups = split_batch_requests(requests, max_requests)
    by_custom_id: dict[str, list[int]] = {}

    semaphore = asyncio.Semaphore(concurrency)

    async def _run_one(group: list[Any], group_index: int) -> None:
        async with semaphore:
            if debug:
                debug(
                    f"{debug_label}: running group {group_index + 1}/{len(groups)}",
                    {"groupSize": len(group)},
                )
            await run_group(
                {
                    "group": group,
                    "group_index": group_index,
                    "groups": len(groups),
                    "by_custom_id": by_custom_id,
                }
            )

    tasks = [_run_one(group, idx) for idx, group in enumerate(groups)]
    await asyncio.gather(*tasks)
    return by_custom_id


# ---------------------------------------------------------------------------
# Full remote batch pipeline (upload → create job → poll → download → parse)
# Mirrors the complete flow used in engine-embeddings.ts
# ---------------------------------------------------------------------------

async def run_remote_batch_embeddings(
    client: BatchHttpClientConfig,
    requests: list[dict[str, Any]],
    max_requests: int = 50_000,
    poll_interval_ms: int = 5_000,
    timeout_ms: int = 600_000,
    endpoint: str = "/v1/embeddings",
) -> dict[str, list[float]]:
    """Upload a batch of embedding requests, wait for completion, and return results.

    This wraps the full upload → create → poll → download → parse pipeline.
    Mirrors the orchestration in TS engine-embeddings.ts.

    Args:
        client: HTTP client config (base_url + headers with API key).
        requests: List of ``{"custom_id": str, "body": {"input": str, "model": str, ...}}``.
        max_requests: Max requests per JSONL file.
        poll_interval_ms: Polling interval for batch status.
        timeout_ms: Total timeout.
        endpoint: Batch endpoint (default: ``/v1/embeddings``).

    Returns:
        Dict mapping custom_id → embedding vector.
    """
    from .batch_upload import upload_batch_jsonl_file
    from .batch_http import create_batch_job, fetch_batch_status, download_batch_output_file
    from .batch_status import poll_batch_until_complete
    from .batch_output import parse_batch_output_jsonl

    results: dict[str, list[float]] = {}

    for group in split_batch_requests(requests, max_requests):
        # 1. Upload JSONL
        logger.debug("Uploading batch of %d requests", len(group))
        file_id = await upload_batch_jsonl_file(client, group, error_prefix="remote-batch")

        # 2. Create batch job
        batch_resp = await create_batch_job(client, file_id, endpoint=endpoint)
        batch_id = batch_resp.get("id") or batch_resp.get("batch_id")
        if not batch_id:
            logger.warning("Batch creation response missing id: %s", batch_resp)
            continue

        # 3. Poll until complete
        async def _get_status() -> dict[str, Any]:
            return await fetch_batch_status(client, batch_id)

        final_status = await poll_batch_until_complete(
            _get_status,
            batch_id=batch_id,
            provider="remote",
            poll_interval_ms=poll_interval_ms,
            timeout_ms=timeout_ms,
        )

        # 4. Download output
        output_file_id = final_status.get("output_file_id")
        if not output_file_id:
            logger.warning("Batch %s completed without output file", batch_id)
            continue
        raw_output = await download_batch_output_file(client, output_file_id)

        # 5. Parse output lines
        lines = parse_batch_output_jsonl(raw_output)
        for line in lines:
            if not line.custom_id:
                continue
            body = (line.response or {}).get("body") or {}
            if isinstance(body, dict):
                data = body.get("data") or []
                if data:
                    embedding = data[0].get("embedding")
                    if embedding:
                        results[line.custom_id] = embedding

    return results


__all__ = [
    "batch_embed",
    "run_embedding_batch_groups",
    "run_remote_batch_embeddings",
]
