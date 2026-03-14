"""CLI runner helpers for OpenClaw Python.

Mirrors TypeScript src/agents/cli-runner/helpers.ts implementation.
"""

import asyncio
import base64
import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, TypeVar

from openclaw.config.schema import CliBackendConfig

logger = logging.getLogger("openclaw.agents.cli_runner_helpers")

# ============================================================================
# Keyed Async Queue (for serialization)
# ============================================================================

T = TypeVar("T")


class KeyedAsyncQueue:
    """Simple keyed async queue for serializing CLI runs."""

    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._lock_for_locks = asyncio.Lock()

    async def enqueue(self, key: str, task: Callable[[], Awaitable[T]]) -> T:
        """Enqueue a task for a given key."""
        async with self._lock_for_locks:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            lock = self._locks[key]

        async with lock:
            return await task()


_CLI_RUN_QUEUE = KeyedAsyncQueue()


async def enqueue_cli_run(key: str, task: Callable[[], Awaitable[T]]) -> T:
    """Enqueue a CLI run task (mirrors TS enqueueCliRun)."""
    return await _CLI_RUN_QUEUE.enqueue(key, task)


# ============================================================================
# Model Normalization (mirrors helpers.ts normalizeCliModel)
# ============================================================================


def normalize_cli_model(model_id: str, backend: CliBackendConfig) -> str:
    """Normalize model ID using backend aliases (mirrors TS normalizeCliModel)."""
    trimmed = model_id.strip()
    if not trimmed:
        return trimmed

    # Check direct match
    if backend.model_aliases:
        direct = backend.model_aliases.get(trimmed)
        if direct:
            return direct

        # Check lowercase match
        lower = trimmed.lower()
        mapped = backend.model_aliases.get(lower)
        if mapped:
            return mapped

    return trimmed


# ============================================================================
# Output Parsing (mirrors helpers.ts parseCliJson, parseCliJsonl)
# ============================================================================


@dataclass
class CliUsage:
    """CLI usage information (mirrors TS CliUsage)."""

    input: Optional[int] = None
    output: Optional[int] = None
    cache_read: Optional[int] = None
    cache_write: Optional[int] = None
    total: Optional[int] = None


@dataclass
class CliOutput:
    """CLI output (mirrors TS CliOutput)."""

    text: str
    session_id: Optional[str] = None
    usage: Optional[CliUsage] = None


def _to_usage(raw: Dict[str, Any]) -> Optional[CliUsage]:
    """Convert raw dict to CliUsage."""

    def pick(key: str) -> Optional[int]:
        value = raw.get(key)
        return value if isinstance(value, int) and value > 0 else None

    input_tokens = pick("input_tokens") or pick("inputTokens")
    output_tokens = pick("output_tokens") or pick("outputTokens")
    cache_read = pick("cache_read_input_tokens") or pick("cached_input_tokens") or pick("cacheRead")
    cache_write = pick("cache_write_input_tokens") or pick("cacheWrite")
    total = pick("total_tokens") or pick("total")

    if not any([input_tokens, output_tokens, cache_read, cache_write, total]):
        return None

    return CliUsage(
        input=input_tokens,
        output=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
        total=total,
    )


def _collect_text(value: Any) -> str:
    """Recursively collect text from nested structures."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_collect_text(entry) for entry in value)
    if not isinstance(value, dict):
        return ""
    if isinstance(value.get("text"), str):
        return value["text"]
    if isinstance(value.get("content"), str):
        return value["content"]
    if isinstance(value.get("content"), list):
        return "".join(_collect_text(entry) for entry in value["content"])
    if isinstance(value.get("message"), dict):
        return _collect_text(value["message"])
    return ""


def _pick_session_id(parsed: Dict[str, Any], backend: CliBackendConfig) -> Optional[str]:
    """Pick session ID from parsed JSON."""
    fields = backend.session_id_fields or [
        "session_id",
        "sessionId",
        "conversation_id",
        "conversationId",
    ]
    for field in fields:
        value = parsed.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_cli_json(raw: str, backend: CliBackendConfig) -> Optional[CliOutput]:
    """Parse CLI JSON output (mirrors TS parseCliJson)."""
    trimmed = raw.strip()
    if not trimmed:
        return None

    try:
        parsed = json.loads(trimmed)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    session_id = _pick_session_id(parsed, backend)
    usage = _to_usage(parsed["usage"]) if isinstance(parsed.get("usage"), dict) else None

    text = (
        _collect_text(parsed.get("message"))
        or _collect_text(parsed.get("content"))
        or _collect_text(parsed.get("result"))
        or _collect_text(parsed)
    )

    return CliOutput(text=text.strip(), session_id=session_id, usage=usage)


def parse_cli_jsonl(raw: str, backend: CliBackendConfig) -> Optional[CliOutput]:
    """Parse CLI JSONL output (mirrors TS parseCliJsonl)."""
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return None

    session_id: Optional[str] = None
    usage: Optional[CliUsage] = None
    texts: List[str] = []

    for line in lines:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(parsed, dict):
            continue

        if not session_id:
            session_id = _pick_session_id(parsed, backend)

        if not session_id and isinstance(parsed.get("thread_id"), str):
            session_id = parsed["thread_id"].strip()

        if isinstance(parsed.get("usage"), dict):
            parsed_usage = _to_usage(parsed["usage"])
            if parsed_usage:
                usage = parsed_usage

        item = parsed.get("item")
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            item_type = item.get("type", "").lower() if isinstance(item.get("type"), str) else ""
            if not item_type or "message" in item_type:
                texts.append(item["text"])

    text = "\n".join(texts).strip()
    if not text:
        return None

    return CliOutput(text=text, session_id=session_id, usage=usage)


# ============================================================================
# Session Management (mirrors helpers.ts)
# ============================================================================


def resolve_system_prompt_usage(
    backend: CliBackendConfig,
    is_new_session: bool,
    system_prompt: Optional[str] = None,
) -> Optional[str]:
    """Resolve whether to use system prompt (mirrors TS resolveSystemPromptUsage)."""
    sp = system_prompt.strip() if system_prompt else ""
    if not sp:
        return None

    when = backend.system_prompt_when or "first"
    if when == "never":
        return None
    if when == "first" and not is_new_session:
        return None
    if not backend.system_prompt_arg or not backend.system_prompt_arg.strip():
        return None

    return sp


@dataclass
class ResolvedSessionId:
    """Resolved session ID (mirrors TS return type)."""

    session_id: Optional[str]
    is_new: bool


def resolve_session_id_to_send(
    backend: CliBackendConfig,
    cli_session_id: Optional[str] = None,
) -> ResolvedSessionId:
    """Resolve session ID to send (mirrors TS resolveSessionIdToSend)."""
    mode = backend.session_mode or "always"
    existing = cli_session_id.strip() if cli_session_id else None

    if mode == "none":
        return ResolvedSessionId(session_id=None, is_new=not existing)

    if mode == "existing":
        return ResolvedSessionId(session_id=existing, is_new=not existing)

    if existing:
        return ResolvedSessionId(session_id=existing, is_new=False)

    return ResolvedSessionId(session_id=str(uuid.uuid4()), is_new=True)


# ============================================================================
# Prompt Input Resolution (mirrors helpers.ts)
# ============================================================================


@dataclass
class ResolvedPromptInput:
    """Resolved prompt input (mirrors TS return type)."""

    args_prompt: Optional[str] = None
    stdin: Optional[str] = None


def resolve_prompt_input(backend: CliBackendConfig, prompt: str) -> ResolvedPromptInput:
    """Resolve prompt input mode (mirrors TS resolvePromptInput)."""
    input_mode = backend.input or "arg"

    if input_mode == "stdin":
        return ResolvedPromptInput(stdin=prompt)

    if backend.max_prompt_arg_chars and len(prompt) > backend.max_prompt_arg_chars:
        return ResolvedPromptInput(stdin=prompt)

    return ResolvedPromptInput(args_prompt=prompt)


# ============================================================================
# Image Handling (mirrors helpers.ts)
# ============================================================================


def _resolve_image_extension(mime_type: str) -> str:
    """Resolve image file extension from MIME type."""
    normalized = mime_type.lower()
    if "png" in normalized:
        return "png"
    if "jpeg" in normalized or "jpg" in normalized:
        return "jpg"
    if "gif" in normalized:
        return "gif"
    if "webp" in normalized:
        return "webp"
    return "bin"


def append_image_paths_to_prompt(prompt: str, paths: List[str]) -> str:
    """Append image paths to prompt (mirrors TS appendImagePathsToPrompt)."""
    if not paths:
        return prompt

    trimmed = prompt.rstrip()
    separator = "\n\n" if trimmed else ""
    return f"{trimmed}{separator}{chr(10).join(paths)}"


@dataclass
class ImageContent:
    """Image content structure."""

    data: str
    mime_type: str


async def write_cli_images(images: List[ImageContent]) -> Dict[str, Any]:
    """Write images to temp files (mirrors TS writeCliImages).

    Returns:
        dict with 'paths' (list of file paths) and 'cleanup' (async cleanup function)
    """
    temp_dir = tempfile.mkdtemp(prefix="openclaw-cli-images-")
    paths: List[str] = []

    for i, image in enumerate(images):
        ext = _resolve_image_extension(image.mime_type)
        file_path = os.path.join(temp_dir, f"image-{i + 1}.{ext}")
        buffer = base64.b64decode(image.data)

        with open(file_path, "wb") as f:
            f.write(buffer)
        os.chmod(file_path, 0o600)
        paths.append(file_path)

    async def cleanup() -> None:
        try:
            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as e:
            logger.debug(f"Failed to cleanup temp images: {e}")

    return {"paths": paths, "cleanup": cleanup}


# ============================================================================
# CLI Args Building (mirrors helpers.ts buildCliArgs)
# ============================================================================


def build_cli_args(
    backend: CliBackendConfig,
    base_args: List[str],
    model_id: str,
    session_id: Optional[str] = None,
    system_prompt: Optional[str] = None,
    image_paths: Optional[List[str]] = None,
    prompt_arg: Optional[str] = None,
    use_resume: bool = False,
) -> List[str]:
    """Build CLI arguments (mirrors TS buildCliArgs)."""
    args = list(base_args)

    # Model arg (not for resume)
    if not use_resume and backend.model_arg and model_id:
        args.extend([backend.model_arg, model_id])

    # System prompt arg (not for resume)
    if not use_resume and system_prompt and backend.system_prompt_arg:
        args.extend([backend.system_prompt_arg, system_prompt])

    # Session args (not for resume)
    if not use_resume and session_id:
        if backend.session_args:
            for entry in backend.session_args:
                args.append(entry.replace("{sessionId}", session_id))
        elif backend.session_arg:
            args.extend([backend.session_arg, session_id])

    # Image args
    if image_paths:
        mode = backend.image_mode or "repeat"
        image_arg = backend.image_arg
        if image_arg:
            if mode == "list":
                args.extend([image_arg, ",".join(image_paths)])
            else:
                for image_path in image_paths:
                    args.extend([image_arg, image_path])

    # Prompt arg
    if prompt_arg is not None:
        args.append(prompt_arg)

    return args


# ============================================================================
# Reliability Functions (mirrors cli-runner/reliability.ts)
# ============================================================================

CLI_WATCHDOG_MIN_TIMEOUT_MS = 1000


def _pick_watchdog_profile(backend: CliBackendConfig, use_resume: bool) -> Dict[str, Any]:
    """Pick watchdog profile (fresh vs resume)."""
    # Import defaults from cli_backends
    from openclaw.agents.cli_backends import (
        CLI_FRESH_WATCHDOG_DEFAULTS,
        CLI_RESUME_WATCHDOG_DEFAULTS,
    )

    defaults = CLI_RESUME_WATCHDOG_DEFAULTS if use_resume else CLI_FRESH_WATCHDOG_DEFAULTS

    configured = None
    if backend.reliability and backend.reliability.watchdog:
        profile_key = "resume" if use_resume else "fresh"
        configured = backend.reliability.watchdog.get(profile_key)

    # Ratio
    ratio = defaults.no_output_timeout_ratio
    if configured and configured.no_output_timeout_ratio is not None:
        value = configured.no_output_timeout_ratio
        if isinstance(value, (int, float)):
            ratio = max(0.05, min(0.95, value))

    # Min MS
    min_ms = defaults.min_ms
    if configured and configured.min_ms is not None:
        value = configured.min_ms
        if isinstance(value, int):
            min_ms = max(CLI_WATCHDOG_MIN_TIMEOUT_MS, value)

    # Max MS
    max_ms = defaults.max_ms
    if configured and configured.max_ms is not None:
        value = configured.max_ms
        if isinstance(value, int):
            max_ms = max(CLI_WATCHDOG_MIN_TIMEOUT_MS, value)

    # Fixed timeout
    no_output_timeout_ms = None
    if configured and configured.no_output_timeout_ms is not None:
        value = configured.no_output_timeout_ms
        if isinstance(value, int):
            no_output_timeout_ms = max(CLI_WATCHDOG_MIN_TIMEOUT_MS, value)

    return {
        "no_output_timeout_ms": no_output_timeout_ms,
        "no_output_timeout_ratio": ratio,
        "min_ms": min(min_ms, max_ms),
        "max_ms": max(min_ms, max_ms),
    }


def resolve_cli_no_output_timeout_ms(backend: CliBackendConfig, timeout_ms: int, use_resume: bool) -> int:
    """Resolve no-output timeout (mirrors TS resolveCliNoOutputTimeoutMs)."""
    profile = _pick_watchdog_profile(backend, use_resume)

    # Keep watchdog below global timeout
    cap = max(CLI_WATCHDOG_MIN_TIMEOUT_MS, timeout_ms - 1000)

    if profile["no_output_timeout_ms"] is not None:
        return min(profile["no_output_timeout_ms"], cap)

    computed = int(timeout_ms * profile["no_output_timeout_ratio"])
    bounded = min(profile["max_ms"], max(profile["min_ms"], computed))
    return min(bounded, cap)


def build_cli_supervisor_scope_key(
    backend: CliBackendConfig,
    backend_id: str,
    cli_session_id: Optional[str] = None,
) -> Optional[str]:
    """Build supervisor scope key (mirrors TS buildCliSupervisorScopeKey)."""
    command_token = Path(backend.command or "").name.strip().lower()
    backend_token = backend_id.strip().lower()
    session_token = cli_session_id.strip() if cli_session_id else None

    if not session_token:
        return None

    return f"cli:{backend_token}:{command_token}:{session_token}"
