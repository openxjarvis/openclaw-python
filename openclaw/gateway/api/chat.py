"""
Gateway Chat Methods - Aligned with TypeScript openclaw/src/gateway/server-methods/chat.ts

Implements WebSocket RPC methods for chat interactions:
- chat.send: Send message and execute agent
- chat.history: Get chat history from session
- chat.abort: Abort running agent execution
- chat.inject: Inject message into session transcript
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING
from ...config.paths import resolve_state_dir

if TYPE_CHECKING:
    from ..server import GatewayConnection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — mirrors TS chat-abort.ts
# ---------------------------------------------------------------------------
# Max timeout when no gateway timeout is in effect (≈ 25 days in ms)
NO_GATEWAY_TIMEOUT_MS = 2_147_000_000


# =============================================================================
# TS-aligned utility functions
# =============================================================================

def is_chat_stop_command_text(text: str) -> bool:
    """
    Return True if the text is exactly the ``/stop`` stop command.

    TS ``isChatStopCommandText()`` only matches the literal string ``/stop``
    (case-insensitive, trimmed).  Broader "abort"/"cancel" aliases were a
    Python-only divergence that could accidentally stop legitimate messages.
    """
    return text.strip().lower() == "/stop"


def resolve_chat_run_expires_at_ms(
    now: int,
    timeout_ms: int,
    grace_ms: int = 60_000,
    min_ms: int = 2 * 60_000,
    max_ms: int = 24 * 60 * 60_000,
) -> int:
    """
    Compute the absolute expiry timestamp for a chat run.
    Mirrors TS resolveChatRunExpiresAtMs().
    """
    bounded_timeout_ms = max(0, timeout_ms)
    target = now + bounded_timeout_ms + grace_ms
    min_ts = now + min_ms
    max_ts = now + max_ms
    return min(max_ts, max(min_ts, target))


def strip_envelope_from_message(message: Any) -> Any:
    """
    Strip message-ID envelope markers from user message text/content blocks.
    Mirrors TS stripEnvelopeFromMessage().
    """
    if not isinstance(message, dict):
        return message
    role = str(message.get("role", "")).lower()
    if role != "user":
        return message

    import re

    def _strip_envelope(text: str) -> str:
        # Remove common envelope patterns: [msg:uuid], [envelope:...], [id:...]
        text = re.sub(r'\[msg:[a-f0-9\-]{8,}\]', '', text)
        text = re.sub(r'\[envelope:[^\]]+\]', '', text)
        text = re.sub(r'\[id:[^\]]+\]', '', text)
        return text

    changed = False
    next_msg = dict(message)

    content = message.get("content")
    if isinstance(content, str):
        stripped = _strip_envelope(content)
        if stripped != content:
            next_msg["content"] = stripped
            changed = True
    elif isinstance(content, list):
        new_content = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                stripped = _strip_envelope(block["text"])
                if stripped != block["text"]:
                    new_content.append({**block, "text": stripped})
                    changed = True
                    continue
            new_content.append(block)
        if changed:
            next_msg["content"] = new_content
    elif isinstance(message.get("text"), str):
        stripped = _strip_envelope(message["text"])
        if stripped != message["text"]:
            next_msg["text"] = stripped
            changed = True

    return next_msg if changed else message


def strip_envelope_from_messages(messages: list[Any]) -> list[Any]:
    """
    Strip envelope markers from all user messages in a list.
    Mirrors TS stripEnvelopeFromMessages().
    """
    if not messages:
        return messages
    changed = False
    result = []
    for msg in messages:
        stripped = strip_envelope_from_message(msg)
        if stripped is not msg:
            changed = True
        result.append(stripped)
    return result if changed else messages


def normalize_rpc_attachments_to_chat_attachments(
    attachments: list[Any] | None,
) -> list[dict[str, Any]]:
    """
    Normalise raw RPC attachment dicts to internal ChatAttachment format.
    Mirrors TS normalizeRpcAttachmentsToChatAttachments().
    """
    if not attachments:
        return []
    result = []
    for a in attachments:
        if not isinstance(a, dict):
            continue
        content = a.get("content")
        if isinstance(content, bytes):
            import base64
            content = base64.b64encode(content).decode("ascii")
        elif not isinstance(content, str):
            continue  # skip if no usable content
        item: dict[str, Any] = {"content": content}
        if isinstance(a.get("type"), str):
            item["type"] = a["type"]
        if isinstance(a.get("mimeType"), str):
            item["mimeType"] = a["mimeType"]
        if isinstance(a.get("fileName"), str):
            item["fileName"] = a["fileName"]
        result.append(item)
    return result


# =============================================================================
# Helper Functions
# =============================================================================

def resolve_transcript_path(
    session_id: str,
    sessions_dir: Path,
    session_file: str | None = None,
) -> Path:
    """Resolve transcript file path for session"""
    if session_file:
        return Path(session_file)
    return sessions_dir / f"{session_id}.jsonl"


CURRENT_SESSION_VERSION = 3


def ensure_transcript_file(
    transcript_path: Path,
    session_id: str,
    cwd: str | None = None,
) -> tuple[bool, str | None]:
    """Ensure transcript file exists with a v3 session header."""
    if transcript_path.exists():
        return True, None

    try:
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        header: dict[str, Any] = {
            "type": "session",
            "version": CURRENT_SESSION_VERSION,
            "id": session_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if cwd:
            header["cwd"] = cwd
        with open(transcript_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(header) + "\n")
        return True, None
    except Exception as e:
        return False, str(e)


def read_session_messages(transcript_path: Path, limit: int = 200) -> list[dict[str, Any]]:
    """Read messages from session transcript"""
    if not transcript_path.exists():
        return []
    
    messages = []
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "message" and "message" in entry:
                        messages.append(entry["message"])
                except json.JSONDecodeError:
                    continue
        
        # Return last N messages
        return messages[-limit:] if len(messages) > limit else messages
    
    except Exception as e:
        logger.error(f"Failed to read session messages: {e}")
        return []


def _read_last_entry_id(transcript_path: Path) -> str | None:
    """Return the ``id`` of the last JSONL entry in the transcript (for parentId chain)."""
    try:
        last_line: str | None = None
        with open(transcript_path, "rb") as f:
            # Efficient backwards scan — read last non-empty line
            f.seek(0, 2)
            file_size = f.tell()
            buf = b""
            pos = file_size
            while pos > 0:
                read_size = min(512, pos)
                pos -= read_size
                f.seek(pos)
                chunk = f.read(read_size)
                buf = chunk + buf
                lines = buf.split(b"\n")
                for line in reversed(lines):
                    stripped = line.strip()
                    if stripped:
                        last_line = stripped.decode("utf-8", errors="replace")
                        break
                if last_line:
                    break
        if last_line:
            entry = json.loads(last_line)
            return entry.get("id")
    except Exception:
        pass
    return None


def append_message_to_transcript(
    transcript_path: Path,
    message: dict[str, Any],
    create_if_missing: bool = True,
    cwd: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """
    Append a message entry to the JSONL transcript.

    Each entry gets a full UUID and a ``parentId`` pointing to the previous
    entry, forming a linked-list DAG that the compaction algorithm traverses.

    Returns:
        (success, message_id, error)
    """
    message_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    # Ensure file exists
    if not transcript_path.exists() and create_if_missing:
        session_id = transcript_path.stem
        success, error = ensure_transcript_file(transcript_path, session_id, cwd=cwd)
        if not success:
            return False, None, error

    # Resolve parentId from the current last entry
    parent_id = _read_last_entry_id(transcript_path)

    entry: dict[str, Any] = {
        "type": "message",
        "id": message_id,
        "timestamp": now.isoformat(),
        "message": message,
    }
    if parent_id:
        entry["parentId"] = parent_id

    try:
        with open(transcript_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return True, message_id, None
    except Exception as e:
        return False, None, str(e)


CHAT_HISTORY_TEXT_MAX_CHARS = 12_000
CHAT_HISTORY_MAX_SINGLE_MESSAGE_BYTES = 128 * 1024
CHAT_HISTORY_OVERSIZED_PLACEHOLDER = "[chat.history omitted: message too large]"


def _strip_disallowed_control_chars(text: str) -> str:
    """Remove disallowed control characters, keeping tab/LF/CR and printable chars."""
    out = []
    for ch in text:
        code = ord(ch)
        if code in (9, 10, 13) or (32 <= code != 127):
            out.append(ch)
    return "".join(out)


def sanitize_chat_send_message_input(
    message: str,
) -> tuple[bool, str, str | None]:
    """Validate and sanitize a chat.send message.

    Mirrors TS sanitizeChatSendMessageInput().
    Returns (ok, message, error).
    """
    normalized = message.normalize("NFC") if hasattr(message, "normalize") else message
    if "\x00" in normalized:
        return False, message, "message must not contain null bytes"
    return True, _strip_disallowed_control_chars(normalized), None


def _truncate_history_text(text: str) -> tuple[str, bool]:
    """Truncate to CHAT_HISTORY_TEXT_MAX_CHARS chars."""
    if len(text) <= CHAT_HISTORY_TEXT_MAX_CHARS:
        return text, False
    return f"{text[:CHAT_HISTORY_TEXT_MAX_CHARS]}\n...(truncated)...", True


def _sanitize_history_content_block(block: Any) -> tuple[Any, bool]:
    """Sanitize a single content block.

    Mirrors TS sanitizeChatHistoryContentBlock().
    """
    if not isinstance(block, dict):
        return block, False
    entry = dict(block)
    changed = False

    for field in ("text", "partialJson", "arguments", "thinking"):
        if isinstance(entry.get(field), str):
            new_val, truncated = _truncate_history_text(entry[field])
            if truncated:
                entry[field] = new_val
                changed = True

    if "thinkingSignature" in entry:
        del entry["thinkingSignature"]
        changed = True

    if entry.get("type") == "image" and isinstance(entry.get("data"), str):
        byte_size = len(entry["data"].encode("utf-8"))
        del entry["data"]
        entry["omitted"] = True
        entry["bytes"] = byte_size
        changed = True

    return (entry if changed else block), changed


def _sanitize_history_message(message: Any) -> tuple[Any, bool]:
    """Sanitize a single history message.

    Mirrors TS sanitizeChatHistoryMessage().
    """
    if not isinstance(message, dict):
        return message, False
    entry = dict(message)
    changed = False

    for field in ("details", "usage", "cost"):
        if field in entry:
            del entry[field]
            changed = True

    if isinstance(entry.get("content"), str):
        new_val, truncated = _truncate_history_text(entry["content"])
        if truncated:
            entry["content"] = new_val
            changed = True
    elif isinstance(entry.get("content"), list):
        new_blocks = []
        any_block_changed = False
        for blk in entry["content"]:
            new_blk, blk_changed = _sanitize_history_content_block(blk)
            new_blocks.append(new_blk)
            any_block_changed = any_block_changed or blk_changed
        if any_block_changed:
            entry["content"] = new_blocks
            changed = True

    if isinstance(entry.get("text"), str):
        new_val, truncated = _truncate_history_text(entry["text"])
        if truncated:
            entry["text"] = new_val
            changed = True

    return (entry if changed else message), changed


def _json_utf8_bytes(value: Any) -> int:
    """Return UTF-8 byte length of JSON serialization."""
    try:
        return len(json.dumps(value).encode("utf-8"))
    except Exception:
        return len(str(value).encode("utf-8"))


def _build_oversized_placeholder(message: Any) -> dict[str, Any]:
    """Build a placeholder for an oversized message."""
    role = "assistant"
    timestamp = int(datetime.now(UTC).timestamp() * 1000)
    if isinstance(message, dict):
        if isinstance(message.get("role"), str):
            role = message["role"]
        if isinstance(message.get("timestamp"), (int, float)):
            timestamp = int(message["timestamp"])
    return {
        "role": role,
        "timestamp": timestamp,
        "content": [{"type": "text", "text": CHAT_HISTORY_OVERSIZED_PLACEHOLDER}],
        "__openclaw": {"truncated": True, "reason": "oversized"},
    }


def _replace_oversized_messages(
    messages: list[Any],
    max_single_bytes: int = CHAT_HISTORY_MAX_SINGLE_MESSAGE_BYTES,
) -> tuple[list[Any], int]:
    """Replace messages exceeding max_single_bytes with placeholder.

    Mirrors TS replaceOversizedChatHistoryMessages().
    Returns (messages, replaced_count).
    """
    if not messages:
        return messages, 0
    replaced = 0
    result = []
    for msg in messages:
        if _json_utf8_bytes(msg) > max_single_bytes:
            result.append(_build_oversized_placeholder(msg))
            replaced += 1
        else:
            result.append(msg)
    return (result if replaced > 0 else messages), replaced


def _enforce_chat_history_budget(
    messages: list[Any],
    max_bytes: int,
) -> tuple[list[Any], int]:
    """Drop oldest messages until total JSON byte size fits within max_bytes.

    Mirrors TS enforceChatHistoryFinalBudget().
    Returns (messages, placeholder_count).
    """
    if not messages:
        return messages, 0
    if _json_utf8_bytes(messages) <= max_bytes:
        return messages, 0
    # Drop from the front until we fit
    while messages and _json_utf8_bytes(messages) > max_bytes:
        messages = messages[1:]
    return messages, 0


def _sanitize_history_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Full history sanitization pipeline.

    Mirrors TS chat.history sanitization in order:
    1. sanitizeChatHistoryMessages (strip details/usage/cost + truncate text)
    2. replaceOversizedChatHistoryMessages (128KB per-message limit)
    3. enforceChatHistoryFinalBudget (total byte budget from server constants)
    """
    if not messages:
        return messages

    # Step 1: field-level sanitization
    any_changed = False
    sanitized: list[Any] = []
    for msg in messages:
        new_msg, changed = _sanitize_history_message(msg)
        sanitized.append(new_msg)
        any_changed = any_changed or changed
    if any_changed:
        messages = sanitized

    # Step 2: replace oversized single messages
    messages, _ = _replace_oversized_messages(messages)

    # Step 3: enforce total budget (~8 MB default matching TS getMaxChatHistoryMessagesBytes)
    from openclaw.gateway.server_constants import get_max_chat_history_messages_bytes
    max_total_bytes = get_max_chat_history_messages_bytes()
    messages, _ = _enforce_chat_history_budget(messages, max_total_bytes)

    return messages


def broadcast_chat_event(
    connection: GatewayConnection,
    event_type: str,  # "delta" | "final" | "error" | "aborted" | "start"
    run_id: str,
    session_key: str,
    message: dict[str, Any] | None = None,
    error_message: str | None = None,
    text: str | None = None,  # For delta events
) -> None:
    """Broadcast chat event to WebSocket clients"""
    payload: dict[str, Any] = {
        "runId": run_id,
        "sessionKey": session_key,
        "state": event_type,
    }
    
    # For delta events with text, create a proper message structure
    if text is not None and event_type == "delta":
        payload["message"] = {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "timestamp": int(datetime.now(UTC).timestamp() * 1000),
        }
    elif message is not None:
        payload["message"] = message
    
    if error_message is not None:
        payload["errorMessage"] = error_message
    
    # Broadcast to all connected clients
    if connection.gateway:
        asyncio.create_task(connection.gateway.broadcast_event("chat", payload))


# =============================================================================
# Chat Method Implementations
# =============================================================================

class ChatHistoryMethod:
    """Get chat history from session"""
    
    name = "chat.history"
    description = "Get chat history for a session"
    category = "chat"
    
    async def execute(self, connection: GatewayConnection, params: dict[str, Any]) -> dict[str, Any]:
        """
        Get chat history
        
        Args:
            params: {
                "sessionKey": str,
                "limit": int (optional, default 200)
            }
        
        Returns:
            {
                "sessionKey": str,
                "sessionId": str,
                "messages": list,
                "thinkingLevel": str | None,
                "verboseLevel": str | None
            }
        """
        session_key = params.get("sessionKey")
        limit = params.get("limit", 200)
        
        if not session_key:
            raise ValueError("sessionKey is required")
        
        # Get session manager from gateway
        if not connection.gateway or not connection.gateway.channel_manager:
            return {
                "sessionKey": session_key,
                "sessionId": session_key,
                "messages": [],
            }
        
        session_manager = connection.gateway.channel_manager.session_manager
        if not session_manager:
            return {
                "sessionKey": session_key,
                "sessionId": session_key,
                "messages": [],
            }
        
        # Get or create session to get the real session_id (use session_key parameter properly)
        session = session_manager.get_or_create_session(session_key=session_key)
        session_id = session.session_id
        logger.info(f"chat.history using session: key={session_key}, id={session_id}")
        
        # Read messages from transcript
        # Check if sessions_dir attribute exists
        if not hasattr(session_manager, 'sessions_dir'):
            logger.error(f"SessionManager has no sessions_dir! Type: {type(session_manager)}, attrs: {dir(session_manager)}")
            # Fallback to _sessions_dir
            sessions_dir = Path(getattr(session_manager, '_sessions_dir', resolve_state_dir() / ".sessions"))
        else:
            sessions_dir = Path(session_manager.sessions_dir)
        transcript_path = resolve_transcript_path(session_id, sessions_dir)
        logger.info(f"chat.history: sessionKey={session_key}, sessionId={session_id}, transcript={transcript_path}")
        messages = read_session_messages(transcript_path, limit=min(limit, 1000))

        # Strip envelope markers from user messages — mirrors TS stripEnvelopeFromMessages()
        messages = strip_envelope_from_messages(messages)

        # Sanitize messages — mirrors TypeScript chat.history sanitization
        messages = _sanitize_history_messages(messages)

        # Resolve thinkingLevel and verboseLevel from SessionEntry
        thinking_level: str | None = None
        verbose_level: str | None = None
        try:
            entry = session_manager.get_session_entry(session_key)
            if entry:
                thinking_level = getattr(entry, "thinkingLevel", None)
                verbose_level = getattr(entry, "verboseLevel", None)
        except Exception:
            pass

        return {
            "sessionKey": session_key,
            "sessionId": session_id,
            "messages": messages,
            "thinkingLevel": thinking_level,
            "verboseLevel": verbose_level,
        }


class ChatSendMethod:
    """Send chat message and execute agent"""
    
    name = "chat.send"
    description = "Send message and execute agent"
    category = "chat"
    
    async def execute(self, connection: GatewayConnection, params: dict[str, Any]) -> dict[str, Any]:
        """
        Send message and execute agent
        
        Args:
            params: {
                "sessionKey": str,
                "message": str,
                "deliver": bool (optional),
                "idempotencyKey": str,
                "attachments": list (optional)
            }
        
        Returns:
            {
                "runId": str,
                "status": "started" | "error"
            }
        """
        logger.info(f"chat.send params: {list(params.keys())}")
        logger.debug(f"chat.send full params: {params}")
        
        session_key = params.get("sessionKey")
        raw_message = params.get("message", "")
        if not isinstance(raw_message, str):
            raw_message = ""
        idempotency_key = params.get("idempotencyKey")
        attachments = params.get("attachments", [])

        if not session_key:
            raise ValueError("sessionKey is required")

        if not idempotency_key:
            raise ValueError("idempotencyKey is required")

        if not raw_message.strip() and not attachments:
            raise ValueError("message or attachments required")

        # Sanitize message input — mirrors TS sanitizeChatSendMessageInput()
        if raw_message:
            ok, raw_message, err = sanitize_chat_send_message_input(raw_message)
            if not ok:
                raise ValueError(err or "invalid message")

        message = raw_message.strip()

        # Normalise attachments — mirrors TS normalizeRpcAttachmentsToChatAttachments()
        normalized_attachments = normalize_rpc_attachments_to_chat_attachments(
            attachments if isinstance(attachments, list) else []
        )

        # Empty message + no attachments → reject early
        if not message and not normalized_attachments:
            raise ValueError("message or attachment required")

        # Check for stop command — mirrors TS isChatStopCommandText()
        if is_chat_stop_command_text(message):
            # Abort any active runs for this session and return
            gateway = connection.gateway
            if gateway and hasattr(gateway, "active_runs"):
                import asyncio as _asyncio
                for task in list(getattr(gateway, "active_runs", {}).values()):
                    if isinstance(task, _asyncio.Task):
                        meta = getattr(task, "_openclaw_meta", {})
                        if meta.get("session_key") == session_key:
                            task.cancel()
            return {"ok": True, "aborted": True, "runIds": []}

        # Get gateway and channel manager
        if not connection.gateway or not connection.gateway.channel_manager:
            raise RuntimeError("Gateway not initialized")
        
        channel_manager = connection.gateway.channel_manager
        run_id = idempotency_key
        
        # Get session manager
        session_manager = channel_manager.session_manager
        if not session_manager:
            raise RuntimeError("Session manager not available")
        
        # Get or create session (use session_key parameter properly)
        session = session_manager.get_or_create_session(session_key=session_key)
        session_id = session.session_id
        logger.info(f"chat.send using session: key={session_key}, id={session_id}")
        
        # Append user message to transcript
        # Check if sessions_dir attribute exists (fallback to _sessions_dir)
        if hasattr(session_manager, 'sessions_dir'):
            sessions_dir = Path(session_manager.sessions_dir)
        else:
            sessions_dir = Path(getattr(session_manager, '_sessions_dir', resolve_state_dir() / ".sessions"))
        transcript_path = resolve_transcript_path(session_id, sessions_dir)
        
        # Build user message
        now = datetime.now(UTC)
        now_ms = int(now.timestamp() * 1000)
        user_message_content = []
        
        if message:
            user_message_content.append({"type": "text", "text": message})

        # Resolve run expiry — mirrors TS resolveChatRunExpiresAtMs()
        timeout_ms = params.get("timeoutMs") or NO_GATEWAY_TIMEOUT_MS
        expires_at_ms = resolve_chat_run_expires_at_ms(now_ms, int(timeout_ms))

        # Handle attachments (images) using normalized attachments
        if normalized_attachments:
            for att in normalized_attachments:
                if att.get("type") == "image" or (att.get("mimeType") or "").startswith("image/"):
                    user_message_content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": att.get("mimeType", "image/jpeg"),
                            "data": att.get("content", ""),
                        },
                    })
        elif attachments:
            for att in (attachments if isinstance(attachments, list) else []):
                if isinstance(att, dict) and att.get("type") == "image":
                    user_message_content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": att.get("mimeType", "image/jpeg"),
                            "data": att.get("content", ""),
                        },
                    })
        
        user_message = {
            "role": "user",
            "content": user_message_content,
            "timestamp": now_ms,
        }
        
        # Append user message to transcript IMMEDIATELY
        logger.info(f"Appending user message to transcript: {transcript_path}")
        success, msg_id, error = append_message_to_transcript(
            transcript_path, user_message, create_if_missing=True
        )
        
        if not success:
            logger.error(f"Failed to append user message to transcript: {error}")
            # Still continue with agent execution, but record the error
        else:
            logger.info(f"User message saved successfully: {msg_id}")
        
        # Send "started" response immediately
        asyncio.create_task(connection.send_response(
            params.get("__request_id", run_id),
            payload={"runId": run_id, "status": "started"}
        ))
        
        # Build image data URLs from normalized attachments to pass to the agent runtime.
        # Encoded as "data:<mimeType>;base64,<content>" so providers can handle them inline.
        image_data_urls: list[str] = []
        for att in normalized_attachments:
            mime = att.get("mimeType", "")
            content = att.get("content", "")
            if content and (att.get("type") == "image" or mime.startswith("image/")):
                image_data_urls.append(f"data:{mime or 'image/jpeg'};base64,{content}")

        # Create agent task and register for abort support
        task = asyncio.create_task(self._execute_agent_turn(
            connection=connection,
            channel_manager=channel_manager,
            session=session,
            session_key=session_key,
            session_id=session_id,
            message=message,
            run_id=run_id,
            transcript_path=transcript_path,
            images=image_data_urls or None,
        ))

        # Tag task with metadata so abort can find it by session_key
        task._openclaw_meta = {"session_key": session_key, "run_id": run_id}  # type: ignore[attr-defined]

        # Register in gateway.active_runs for abort support
        if connection.gateway:
            if not hasattr(connection.gateway, "active_runs"):
                connection.gateway.active_runs = {}
            connection.gateway.active_runs[run_id] = task

            # Register with chat_registry if available
            chat_registry = getattr(connection.gateway, "chat_registry", None)
            if chat_registry is not None:
                chat_registry.add_run(
                    run_id=run_id,
                    client_run_id=idempotency_key,
                    session_key=session_key,
                    conn_id=getattr(connection, "id", ""),
                )

            # Clean up registration when task completes
            def _cleanup(t: asyncio.Task) -> None:
                if connection.gateway:
                    connection.gateway.active_runs.pop(run_id, None)
            task.add_done_callback(_cleanup)

        # Return acknowledgment (already sent via send_response above)
        return {"runId": run_id, "status": "started"}
    
    async def _execute_agent_turn(
        self,
        connection: GatewayConnection,
        channel_manager: Any,
        session: Any,
        session_key: str,
        session_id: str,
        message: str,
        run_id: str,
        transcript_path: Path,
        images: list[str] | None = None,
    ) -> None:
        """Execute agent turn, streaming events via runtime.run_turn() async generator."""
        from openclaw.events import EventType

        try:
            runtime = channel_manager.default_runtime
            if not runtime:
                raise RuntimeError("Agent runtime not available")

            tools = []
            if hasattr(connection.gateway, "tool_registry") and connection.gateway.tool_registry:
                tools = connection.gateway.tool_registry.list_tools()
            elif hasattr(connection.gateway, "tools") and connection.gateway.tools:
                tools = connection.gateway.tools

            logger.info(f"🔧 Loaded {len(tools)} tools for agent turn")
            logger.info(
                f"Executing agent turn: session={session_key}, run_id={run_id}, msg={message[:60]!r}..."
            )

            broadcast_chat_event(connection, "start", run_id, session_key)

            assistant_response = ""

            # Get the fully-built system prompt from ChannelManager (contains
            # USER.md, SOUL.md, IDENTITY.md inlined) and forward it to run_turn
            # so the pi_coding_agent session receives openclaw's prompt instead
            # of its own internal one.  Mirrors the TS runtime which always
            # passes the pre-built system prompt to the underlying agent.
            system_prompt = getattr(channel_manager, "system_prompt", None)

            # Stream events from run_turn() async generator — this is the correct
            # pattern: events arrive in real time via an internal asyncio.Queue,
            # so they are never missed regardless of await scheduling.
            # Pass session_key and run_id so the active-run registry uses the
            # meaningful key (e.g. "agent:xxx:...") instead of the raw UUID,
            # enabling steer/abort lookups and tool-policy spawnedBy resolution.
            # Mirrors TS where runEmbeddedPiAgent always receives sessionKey.
            async for event in runtime.run_turn(
                session,
                message,
                tools,
                images=images,
                system_prompt=system_prompt,
                session_key=session_key,
                run_id=run_id,
                streaming_behavior="followUp",  # Queue if agent is already processing
            ):
                evt_type = getattr(event, "type", "")
                event_data: dict[str, Any] = {}
                if hasattr(event, "data") and isinstance(event.data, dict):
                    event_data = event.data

                if evt_type in (EventType.TEXT, EventType.TEXT_DELTA, "text", "text_delta"):
                    # Accept both {"text": "..."} and {"delta": "..."}
                    text_chunk = (
                        event_data.get("text")
                        or event_data.get("delta")
                        or ""
                    )
                    if isinstance(text_chunk, dict):
                        text_chunk = text_chunk.get("text", "")
                    text_chunk = str(text_chunk) if text_chunk else ""
                    if text_chunk:
                        assistant_response += text_chunk
                        broadcast_chat_event(
                            connection, "delta", run_id, session_key, text=text_chunk
                        )
                        logger.debug(f"delta: {text_chunk[:80]!r}")

                elif evt_type in (EventType.ERROR, EventType.AGENT_ERROR, "error", "agent.error"):
                    error_msg = event_data.get("message", "Unknown error")
                    logger.error(f"Agent error during turn: {error_msg}")
                    broadcast_chat_event(
                        connection, "error", run_id, session_key, error_message=error_msg
                    )

                elif evt_type in (EventType.TURN_END, EventType.AGENT_TURN_COMPLETE, "turn_end", "agent.turn_complete"):
                    logger.info(
                        f"Turn complete for {session_key}: {len(assistant_response)} chars accumulated"
                    )

            logger.info(f"run_turn complete: {len(assistant_response)} chars")

            now = datetime.now(UTC)
            assistant_message = {
                "role": "assistant",
                "content": [{"type": "text", "text": assistant_response}],
                "timestamp": int(now.timestamp() * 1000),
                "stopReason": "end_turn",
            }

            success, msg_id, error = append_message_to_transcript(
                transcript_path, assistant_message, create_if_missing=False
            )
            if not success:
                logger.warning(f"Failed to append assistant message: {error}")
            else:
                logger.info(f"Assistant message saved: {msg_id}")

            logger.info(f"Broadcasting final event for run_id={run_id}")
            broadcast_chat_event(
                connection, "final", run_id, session_key, message=assistant_message
            )
            logger.info("Final event broadcast complete")

        except Exception as e:
            logger.error(f"Agent execution failed: {e}", exc_info=True)
            broadcast_chat_event(
                connection, "error", run_id, session_key, error_message=str(e)
            )


class ChatAbortMethod:
    """Abort running agent execution.

    Mirrors TypeScript chat.abort:
    1. Look up run by sessionKey or runId in gateway.active_runs
    2. Cancel the asyncio Task (or set abort Event)
    3. Collect partial snapshot from run buffer
    4. Persist aborted-partial to transcript
    5. Broadcast abort-completion event
    """

    name = "chat.abort"
    description = "Abort running agent execution"
    category = "chat"

    async def execute(self, connection: GatewayConnection, params: dict[str, Any]) -> dict[str, Any]:
        """
        Abort agent execution.

        Args:
            params: {
                "sessionKey": str,
                "runId": str (optional)
            }

        Returns:
            {"ok": True, "aborted": bool, "runIds": list[str]}
        """
        session_key = params.get("sessionKey")
        run_id = params.get("runId")

        if not session_key:
            raise ValueError("sessionKey is required")

        if not connection.gateway:
            return {"ok": True, "aborted": False, "runIds": []}

        aborted_run_ids: list[str] = []

        # -------------------------------------------------------------------
        # 1. Find matching active runs
        # -------------------------------------------------------------------
        active_runs: dict[str, asyncio.Task] = getattr(connection.gateway, "active_runs", {})
        chat_registry = getattr(connection.gateway, "chat_registry", None)

        # Build list of run_ids to abort
        target_run_ids: list[str] = []
        if run_id and run_id in active_runs:
            target_run_ids.append(run_id)
        else:
            # Abort all runs matching session_key
            for rid, task in list(active_runs.items()):
                task_meta = getattr(task, "_openclaw_meta", {})
                if task_meta.get("session_key") == session_key:
                    target_run_ids.append(rid)

        # -------------------------------------------------------------------
        # 2. Abort each run
        # -------------------------------------------------------------------
        for rid in target_run_ids:
            task = active_runs.get(rid)
            partial_text = ""

            # Signal abort via registry abort event (preferred)
            if chat_registry is not None:
                chat_registry.abort_run(rid)
                partial_text = "".join(chat_registry.get_buffer(rid))
                chat_registry.clear_buffer(rid)

            # Cancel the asyncio Task
            if task and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

            # Remove from active_runs
            active_runs.pop(rid, None)
            aborted_run_ids.append(rid)

            # -------------------------------------------------------------------
            # 3. Persist partial snapshot to transcript
            # -------------------------------------------------------------------
            if partial_text:
                try:
                    gw = connection.gateway
                    session_manager = getattr(
                        getattr(gw, "channel_manager", None), "session_manager", None
                    )
                    if session_manager:
                        session = session_manager.get_or_create_session(session_key=session_key)
                        sessions_dir = Path(
                            getattr(session_manager, "sessions_dir",
                                    getattr(session_manager, "_sessions_dir",
                                            resolve_state_dir() / ".sessions"))
                        )
                        transcript_path = resolve_transcript_path(session.session_id, sessions_dir)
                        now = datetime.now(UTC)
                        aborted_msg = {
                            "role": "assistant",
                            "content": [{"type": "text", "text": partial_text}],
                            "timestamp": int(now.timestamp() * 1000),
                            "stopReason": "aborted",
                        }
                        append_message_to_transcript(transcript_path, aborted_msg, create_if_missing=False)
                except Exception as exc:
                    logger.warning(f"Failed to persist partial abort snapshot: {exc}")

            # -------------------------------------------------------------------
            # 4. Broadcast abort event
            # -------------------------------------------------------------------
            if connection.gateway:
                abort_payload: dict[str, Any] = {
                    "runId": rid,
                    "sessionKey": session_key,
                    "state": "aborted",
                }
                if partial_text:
                    abort_payload["message"] = {
                        "role": "assistant",
                        "content": [{"type": "text", "text": partial_text}],
                        "stopReason": "aborted",
                    }
                asyncio.create_task(
                    connection.gateway.broadcast_event("chat", abort_payload)
                )

        return {
            "ok": True,
            "aborted": len(aborted_run_ids) > 0,
            "runIds": aborted_run_ids,
        }


class ChatInjectMethod:
    """Inject message into session transcript"""
    
    name = "chat.inject"
    description = "Inject message into session transcript"
    category = "chat"
    
    async def execute(self, connection: GatewayConnection, params: dict[str, Any]) -> dict[str, Any]:
        """
        Inject message into transcript
        
        Args:
            params: {
                "sessionKey": str,
                "message": str,
                "label": str (optional)
            }
        
        Returns:
            {
                "ok": true,
                "messageId": str
            }
        """
        session_key = params.get("sessionKey")
        message_text = params.get("message", "").strip()
        label = params.get("label")
        
        if not session_key:
            raise ValueError("sessionKey is required")
        
        if not message_text:
            raise ValueError("message is required")
        
        # Get session manager
        if not connection.gateway or not connection.gateway.channel_manager:
            raise RuntimeError("Gateway not initialized")
        
        session_manager = connection.gateway.channel_manager.session_manager
        if not session_manager:
            raise RuntimeError("Session manager not available")
        
        # Get session
        session = session_manager.get_or_create_session(session_key)
        session_id = session.session_id
        
        # Build message
        now = datetime.now(UTC)
        label_prefix = f"[{label}]\n\n" if label else ""
        message_body = {
            "role": "assistant",
            "content": [{"type": "text", "text": f"{label_prefix}{message_text}"}],
            "timestamp": int(now.timestamp() * 1000),
            "stopReason": "injected",
            "usage": {"input": 0, "output": 0, "totalTokens": 0},
        }
        
        # Append to transcript
        sessions_dir = Path(session_manager.sessions_dir)
        transcript_path = resolve_transcript_path(session_id, sessions_dir)
        
        success, message_id, error = append_message_to_transcript(
            transcript_path, message_body, create_if_missing=True
        )
        
        if not success:
            raise RuntimeError(f"Failed to write transcript: {error}")
        
        # Broadcast to WebSocket clients
        broadcast_chat_event(
            connection, "final", f"inject-{message_id}", session_key, message=message_body
        )
        
        return {
            "ok": True,
            "messageId": message_id,
        }


# =============================================================================
# Export all chat methods
# =============================================================================

CHAT_METHODS = [
    ChatHistoryMethod(),
    ChatSendMethod(),
    ChatAbortMethod(),
    ChatInjectMethod(),
]
