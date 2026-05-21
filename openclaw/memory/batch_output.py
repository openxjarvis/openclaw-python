"""Parse batch embedding output lines.

Mirrors openclaw/src/memory-host-sdk/host/batch-output.ts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EmbeddingBatchOutputLine:
    """One line from the batch output JSONL file.

    Mirrors TS EmbeddingBatchOutputLine.
    """

    custom_id: str | None = None
    error: dict[str, Any] | None = None
    response: dict[str, Any] | None = None


def apply_embedding_batch_output_line(
    line: EmbeddingBatchOutputLine,
    remaining: set[str],
    errors: list[str],
    by_custom_id: dict[str, list[int]],
) -> None:
    """Consume one output line: update *remaining* and *by_custom_id*.

    Mirrors TS applyEmbeddingBatchOutputLine().
    """
    custom_id = line.custom_id
    if not custom_id:
        return
    remaining.discard(custom_id)

    error_message = (line.error or {}).get("message")
    if error_message:
        errors.append(str(error_message))
        return

    response = line.response or {}
    status = response.get("status_code")
    body = response.get("body") or {}
    if isinstance(body, str):
        return
    if status and status >= 400:
        err_msg = (body.get("error") or {}).get("message", f"HTTP {status}")
        errors.append(str(err_msg))
        return

    data: list[dict[str, Any]] = body.get("data") or []
    embedding: list[float] | None = None
    if data:
        embedding = data[0].get("embedding")
    if embedding is None:
        return

    indices = by_custom_id.get(custom_id)
    if indices is not None:
        for idx in indices:
            by_custom_id[f"__result__{idx}"] = embedding  # type: ignore[assignment]


def parse_batch_output_jsonl(raw: str) -> list[EmbeddingBatchOutputLine]:
    """Parse raw JSONL string into a list of output line objects."""
    import json

    lines: list[EmbeddingBatchOutputLine] = []
    for text in raw.splitlines():
        text = text.strip()
        if not text:
            continue
        try:
            d = json.loads(text)
            lines.append(
                EmbeddingBatchOutputLine(
                    custom_id=d.get("custom_id"),
                    error=d.get("error"),
                    response=d.get("response"),
                )
            )
        except Exception:
            pass
    return lines


__all__ = [
    "EmbeddingBatchOutputLine",
    "apply_embedding_batch_output_line",
    "parse_batch_output_jsonl",
]
