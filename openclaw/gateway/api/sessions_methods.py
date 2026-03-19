"""
Gateway API methods for session management

This module implements all sessions.* Gateway API methods matching the TypeScript implementation.
"""

import logging
import time
import uuid
from typing import Any, Dict, List, Optional
from dataclasses import asdict

from openclaw.agents.session_entry import SessionEntry
from openclaw.config.sessions.store_utils import (
    load_session_store,
    update_session_store,
)
from openclaw.config.sessions.paths import get_default_store_path
from openclaw.config.sessions.transcripts import (
    read_session_preview_items,
    compact_transcript,
    delete_transcript,
    get_session_transcript_path,
    load_session_transcript,
    save_session_transcript,
)
from openclaw.gateway.session_utils import (
    SessionsListOptions,
    list_sessions_from_store,
    resolve_gateway_session_store_target,
    resolve_main_session_key,
)
from openclaw.gateway.sessions_resolve import resolve_session_key_from_resolve_params
from openclaw.gateway.sessions_patch import apply_sessions_patch_to_store

logger = logging.getLogger(__name__)


# =============================================================================
# Runtime cleanup helpers — mirrors TS ensureSessionRuntimeCleanup()
# =============================================================================

async def _ensure_session_runtime_cleanup(
    connection: Any,
    session_key: str,
    session_id: str | None,
    timeout_ms: int = 15_000,
) -> str | None:
    """Abort active pi run, clear queues, and stop subagents for a session.

    Mirrors TS ensureSessionRuntimeCleanup() in sessions.ts.
    Returns an error message if the session could not be cleanly stopped,
    or None on success.
    """
    gateway = getattr(connection, "gateway", None)

    # 1. Clear steering/follow-up queues
    try:
        from openclaw.auto_reply.reply.queue import clear_session_queues as _clear_session_queues
        _clear_session_queues([session_key])
    except Exception as exc:
        logger.warning(f"Failed to clear session queues for {session_key}: {exc}")

    # 1b. Stop subagents spawned by this session
    try:
        from openclaw.agents.subagent_registry import get_global_registry
        from openclaw.agents.pi_embedded import abort_embedded_pi_run
        
        registry = get_global_registry()
        child_runs = registry.list_runs_for_requester(session_key)
        
        for run in child_runs:
            if not run.ended_at:
                child_session_key = run.child_session_key
                # Get session ID and abort
                try:
                    entry = gateway.session_manager.get_session_entry(child_session_key) if gateway and hasattr(gateway, "session_manager") else None
                    if isinstance(entry, dict):
                        session_id = entry.get("sessionId")
                        if session_id:
                            abort_embedded_pi_run(session_id)
                except Exception:
                    pass
                # Mark as terminated
                registry.mark_subagent_run_terminated(run.run_id, reason="killed")
    except Exception as exc:
        logger.debug(f"Failed to stop subagents for requester {session_key}: {exc}")
    # Also attempt legacy channel_manager path
    try:
        if gateway is not None:
            channel_manager = getattr(gateway, "channel_manager", None)
            if channel_manager is not None:
                session_manager = getattr(channel_manager, "session_manager", None)
                if session_manager is not None and hasattr(session_manager, "clear_queues"):
                    session_manager.clear_queues(session_key)
    except Exception as exc:
        logger.debug(f"Legacy session queue clear failed for {session_key}: {exc}")

    # 2. Abort any active agent turn task
    try:
        if gateway is not None and hasattr(gateway, "active_runs"):
            # Find tasks associated with this session_key
            tasks_to_cancel = [
                task for task in gateway.active_runs.values()
                if (
                    isinstance(task, __import__("asyncio").Task)
                    and hasattr(task, "_openclaw_meta")
                    and task._openclaw_meta.get("session_key") == session_key  # type: ignore[attr-defined]
                )
            ]
            for task in tasks_to_cancel:
                task.cancel()
    except Exception as exc:
        logger.warning(f"Failed to abort active runs for {session_key}: {exc}")

    # 3. Abort embedded pi run and wait for it to end
    if session_id:
        try:
            if gateway is not None:
                pi_runtime = getattr(gateway, "pi_runtime", None)
                if pi_runtime is not None and hasattr(pi_runtime, "abort_session"):
                    await pi_runtime.abort_session(session_id)
                elif pi_runtime is not None and hasattr(pi_runtime, "_sessions"):
                    pi_session = pi_runtime._sessions.get(session_id)
                    if pi_session is not None and hasattr(pi_session, "abort"):
                        pi_session.abort()
        except Exception as exc:
            logger.warning(f"Failed to abort pi run for session {session_id}: {exc}")

    return None


# =============================================================================
# sessions.list
# =============================================================================

class SessionsListMethod:
    """List sessions with filtering and sorting"""
    
    name = "sessions.list"
    description = "List all sessions with optional filtering and sorting"
    category = "sessions"
    
    async def execute(self, connection: Any, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute sessions.list
        
        Params:
        - agentId: Filter by agent ID
        - spawnedBy: Filter by parent session
        - label: Filter by label
        - search: Search query
        - includeGlobal: Include global session (default: true)
        - includeUnknown: Include unknown session (default: true)
        - activeMinutes: Filter by recent activity
        - addDerivedTitles: Add derived titles (default: false)
        - addLastMessagePreview: Add last message preview (default: false)
        - limit: Maximum results
        - offset: Skip first N results
        
        Returns:
        - SessionsListResult with sessions array
        """
        agent_id = params.get("agentId", "main")
        store_path = get_default_store_path(agent_id)

        try:
            store = load_session_store(str(store_path))

            # Multi-agent store merge: when no explicit agentId is specified,
            # merge sessions from peer agent stores into the result set so the
            # client sees all agents' sessions (mirrors TS sessions.list behaviour).
            if not params.get("agentId"):
                try:
                    import os as _os
                    from openclaw.agents.session_entry import get_sessions_base_dir
                    base_dir = get_sessions_base_dir()
                    if base_dir and base_dir.exists():
                        for peer_dir in base_dir.iterdir():
                            if not peer_dir.is_dir() or peer_dir.name == agent_id:
                                continue
                            peer_store_path = peer_dir / "sessions.json"
                            if peer_store_path.exists():
                                peer_store = load_session_store(str(peer_store_path))
                                for k, v in peer_store.items():
                                    if k not in store:
                                        store[k] = v
                except Exception as merge_exc:
                    logger.debug("Multi-agent store merge skipped: %s", merge_exc)

            opts = SessionsListOptions(
                agent_id=params.get("agentId"),
                spawned_by=params.get("spawnedBy"),
                label=params.get("label"),
                search=params.get("search"),
                include_global=params.get("includeGlobal", True),
                include_unknown=params.get("includeUnknown", True),
                active_minutes=params.get("activeMinutes"),
                add_derived_titles=params.get("addDerivedTitles", False),
                add_last_message_preview=params.get("addLastMessagePreview", False),
                limit=params.get("limit"),
                offset=params.get("offset", 0),
            )
            
            result = list_sessions_from_store(str(store_path), store, opts)
            
            # Convert to dict
            return {
                "ts": result.ts,
                "path": result.path,
                "count": result.count,
                "defaults": {
                    "modelProvider": result.defaults.model_provider,
                    "model": result.defaults.model,
                    "contextTokens": result.defaults.context_tokens,
                },
                "sessions": [
                    {
                        "key": row.key,
                        "kind": row.kind,
                        "label": row.label,
                        "displayName": row.display_name,
                        "derivedTitle": row.derived_title,
                        "lastMessagePreview": row.last_message_preview,
                        "channel": row.channel,
                        "subject": row.subject,
                        "groupChannel": row.group_channel,
                        "space": row.space,
                        "chatType": row.chat_type,
                        "origin": row.origin.model_dump() if hasattr(row.origin, 'model_dump') else row.origin,
                        "updatedAt": row.updated_at,
                        "sessionId": row.session_id,
                        "systemSent": row.system_sent,
                        "abortedLastRun": row.aborted_last_run,
                        "thinkingLevel": row.thinking_level,
                        "verboseLevel": row.verbose_level,
                        "reasoningLevel": row.reasoning_level,
                        "elevatedLevel": row.elevated_level,
                        "sendPolicy": row.send_policy,
                        "inputTokens": row.input_tokens,
                        "outputTokens": row.output_tokens,
                        "totalTokens": row.total_tokens,
                        "responseUsage": row.response_usage,
                        "modelProvider": row.model_provider,
                        "model": row.model,
                        "contextTokens": row.context_tokens,
                        "deliveryContext": row.delivery_context.model_dump() if hasattr(row.delivery_context, 'model_dump') else row.delivery_context,
                        "lastChannel": row.last_channel,
                        "lastTo": row.last_to,
                        "lastAccountId": row.last_account_id,
                    }
                    for row in result.sessions
                ]
            }
            
        except Exception as e:
            logger.error(f"sessions.list failed: {e}")
            raise
    
    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agentId": {"type": "string"},
                "spawnedBy": {"type": "string"},
                "label": {"type": "string"},
                "search": {"type": "string"},
                "includeGlobal": {"type": "boolean"},
                "includeUnknown": {"type": "boolean"},
                "activeMinutes": {"type": "integer"},
                "addDerivedTitles": {"type": "boolean"},
                "addLastMessagePreview": {"type": "boolean"},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
            },
        }


# =============================================================================
# sessions.preview
# =============================================================================

class SessionsPreviewMethod:
    """Get transcript preview for sessions"""
    
    name = "sessions.preview"
    description = "Get transcript preview for multiple sessions"
    category = "sessions"
    
    async def execute(self, connection: Any, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute sessions.preview
        
        Params:
        - keys: List of session keys (max 64)
        - limit: Messages per session (default: 12)
        - maxChars: Max characters per message (default: 240)
        
        Returns:
        - SessionsPreviewResult with previews array
        """
        keys_raw = params.get("keys", [])
        keys = [str(k).strip() for k in keys_raw if k][:64]
        
        limit = params.get("limit", 12)
        max_chars = params.get("maxChars", 240)
        
        if not keys:
            return {
                "ts": int(time.time() * 1000),
                "previews": []
            }
        
        previews = []
        
        for key in keys:
            try:
                target = resolve_gateway_session_store_target(key)
                store = load_session_store(target.store_path)
                
                # Find entry
                entry = None
                for store_key in target.store_keys:
                    if store_key in store:
                        entry = store[store_key]
                        break
                
                if not entry or not entry.sessionId:
                    previews.append({"key": key, "status": "missing", "items": []})
                    continue
                
                # Read transcript preview
                items_raw = read_session_preview_items(target.canonical_key, limit=limit)
                items = []
                for item in items_raw:
                    if isinstance(item, dict):
                        text = str(item.get("content", ""))[:max_chars]
                        role = str(item.get("type", "text"))
                    else:
                        text = str(item)[:max_chars]
                        role = "text"
                    items.append({"role": role, "text": text})
                
                previews.append({
                    "key": key,
                    "status": "ok" if items else "empty",
                    "items": items
                })
                
            except Exception as e:
                logger.error(f"Failed to preview session {key}: {e}")
                previews.append({"key": key, "status": "error", "items": []})
        
        return {
            "ts": int(time.time() * 1000),
            "previews": previews
        }
    
    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "keys": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
                "maxChars": {"type": "integer"},
            },
            "required": ["keys"],
        }


# =============================================================================
# sessions.resolve
# =============================================================================

class SessionsResolveMethod:
    """Resolve session key from identifier"""
    
    name = "sessions.resolve"
    description = "Resolve session key from key/sessionId/label"
    category = "sessions"
    
    async def execute(self, connection: Any, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute sessions.resolve
        
        Params (exactly one required):
        - key: Direct session key
        - sessionId: UUID to search
        - label: Label to search
        
        Optional filters:
        - includeGlobal, includeUnknown
        - agentId, spawnedBy
        
        Returns:
        - { ok: true, key: str } or { ok: false, error: str }
        """
        resolve_params = {
            "key": params.get("key"),
            "sessionId": params.get("sessionId"),
            "label": params.get("label"),
            "includeGlobal": params.get("includeGlobal", True),
            "includeUnknown": params.get("includeUnknown", True),
            "agentId": params.get("agentId"),
            "spawnedBy": params.get("spawnedBy"),
        }
        cfg = getattr(getattr(connection, "gateway", None), "cfg", None)
        result = resolve_session_key_from_resolve_params(resolve_params, cfg=cfg)

        if result.get("ok"):
            return {"ok": True, "key": result["key"]}
        else:
            return {"ok": False, "error": result.get("error", {}).get("message", "resolve failed")}
    
    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "sessionId": {"type": "string"},
                "label": {"type": "string"},
                "includeGlobal": {"type": "boolean"},
                "includeUnknown": {"type": "boolean"},
                "agentId": {"type": "string"},
                "spawnedBy": {"type": "string"},
            },
        }


# =============================================================================
# sessions.patch
# =============================================================================

class SessionsPatchMethod:
    """Update session entry fields"""
    
    name = "sessions.patch"
    description = "Update session entry fields with validation"
    category = "sessions"
    
    async def execute(self, connection: Any, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute sessions.patch
        
        Params:
        - key: Session key (required)
        - patch: Partial SessionEntry to merge
        
        Returns:
        - SessionsPatchResult with updated entry
        """
        key = params.get("key")
        if not key:
            raise ValueError("key is required")
        
        patch = params.get("patch", {})
        
        target = resolve_gateway_session_store_target(key)
        
        def mutator(store: Dict[str, SessionEntry]) -> None:
            logger.debug(f"Mutator called, store has {len(store)} entries before patch")
            result = apply_sessions_patch_to_store(
                store,
                target.canonical_key,
                patch,
                model_catalog=None
            )
            if not result.get("ok"):
                logger.error(f"Patch FAILED! Error: {result.get('error')}")
            logger.debug(f"Mutator done, patch result ok={result.get('ok')}, store has {len(store)} entries, target_key={target.canonical_key}")
        
        update_session_store(target.store_path, mutator)
        
        # Reload to get updated entry
        store = load_session_store(target.store_path)
        
        # Check if entry exists after mutation
        if target.canonical_key not in store:
            # This shouldn't happen if apply_sessions_patch_to_store worked correctly
            logger.error(
                f"Entry not found after mutation: key={target.canonical_key}, "
                f"available_keys={list(store.keys())[:10]}"
            )
            raise KeyError(
                f"Session entry not created: {target.canonical_key}. "
                f"This may indicate a problem with session store persistence."
            )
        
        entry = store[target.canonical_key]
        
        return {
            "ok": True,
            "path": target.store_path,
            "key": target.canonical_key,
            "entry": entry.model_dump(exclude_none=False),
        }
    
    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "patch": {"type": "object"},
            },
            "required": ["key", "patch"],
        }


# =============================================================================
# sessions.reset
# =============================================================================

class SessionsResetMethod:
    """Reset session with new UUID, preserve config"""
    
    name = "sessions.reset"
    description = "Reset session with new UUID while preserving configuration"
    category = "sessions"
    
    async def execute(self, connection: Any, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute sessions.reset
        
        Params:
        - key: Session key (required)
        - archiveTranscript: Archive before deleting (default: true)
        
        Returns:
        - { ok: true, key: str, sessionId: str }
        """
        key = params.get("key")
        if not key:
            raise ValueError("key is required")
        
        archive_transcript = params.get("archiveTranscript", True)
        
        target = resolve_gateway_session_store_target(key)
        new_session_id = str(uuid.uuid4())
        now_ms = int(time.time() * 1000)
        
        # Get old session ID before reset
        old_session_id = None
        store_data = load_session_store(target.store_path)
        entry = store_data.get(target.canonical_key)
        if entry:
            old_session_id = entry.sessionId
        
        def mutator(store: Dict[str, SessionEntry]) -> None:
            entry = store.get(target.canonical_key)
            
            # If session doesn't exist yet, create a new one (graceful handling)
            if not entry:
                logger.info(f"Session not found for reset, creating new session: {key}")
                reset_entry = SessionEntry(
                    sessionId=new_session_id,
                    updatedAt=now_ms,
                    systemSent=False,
                    abortedLastRun=False,
                    inputTokens=0,
                    outputTokens=0,
                    totalTokens=0,
                    totalTokensFresh=True,
                )
                store[target.canonical_key] = reset_entry
                return
            
            # Mirrors TS sessions.reset nextEntry construction exactly.
            # Preserved: thinking/verbose/reasoning levels, responseUsage, sendPolicy,
            #   label, origin, lastChannel, lastTo, model/modelProvider, contextTokens,
            #   skillsSnapshot (re-evaluated on first turn).
            # Cleared: elevatedLevel, execSecurity, execAsk, execNode, groupActivation,
            #   modelOverride, providerOverride, ttsAuto, displayName, deliveryContext,
            #   spawnedBy, groupId, groupChannel, compactionCount, memoryFlushAt,
            #   inputTokens/outputTokens/totalTokens (reset to 0).
            reset_entry = SessionEntry(
                sessionId=new_session_id,
                updatedAt=now_ms,
                systemSent=False,
                abortedLastRun=False,
                thinkingLevel=entry.thinkingLevel,
                verboseLevel=entry.verboseLevel,
                reasoningLevel=entry.reasoningLevel,
                responseUsage=entry.responseUsage,
                sendPolicy=entry.sendPolicy,
                label=entry.label,
                origin=entry.origin,
                lastChannel=entry.lastChannel,
                lastTo=entry.lastTo,
                model=entry.model,
                modelProvider=entry.modelProvider,
                contextTokens=entry.contextTokens,
                skillsSnapshot=entry.skillsSnapshot,
                inputTokens=0,
                outputTokens=0,
                totalTokens=0,
                totalTokensFresh=True,
            )
            
            store[target.canonical_key] = reset_entry
        
        # Runtime cleanup: abort pi run, clear queues, stop subagents
        cleanup_error = await _ensure_session_runtime_cleanup(
            connection, key, old_session_id
        )
        if cleanup_error:
            logger.warning(f"sessions.reset cleanup warning for {key}: {cleanup_error}")

        update_session_store(target.store_path, mutator)

        # Mark session key as needing bootstrap re-read on next turn (mirrors TS clearBootstrapSnapshot)
        from openclaw.agents.bootstrap_cache import mark_bootstrap_stale
        mark_bootstrap_stale(target.canonical_key)

        # Invalidate cached Session object to force fresh load
        try:
            if hasattr(connection, 'gateway') and connection.gateway:
                channel_manager = connection.gateway.channel_manager
                if channel_manager and channel_manager.session_manager:
                    session_manager = channel_manager.session_manager
                    if old_session_id and old_session_id in getattr(session_manager, "_sessions", {}):
                        del session_manager._sessions[old_session_id]
                    session_manager._session_store_loaded_at = 0.0
        except Exception as exc:
            logger.warning(f"Failed to invalidate session cache: {exc}")
        
        # Delete old transcript
        if old_session_id:
            try:
                # Archive transcript if requested (by renaming with timestamp)
                if archive_transcript:
                    transcript_path = get_session_transcript_path(target.canonical_key)
                    if transcript_path.exists():
                        archive_name = f"{transcript_path.stem}_reset_{now_ms}{transcript_path.suffix}"
                        archive_path = transcript_path.parent / archive_name
                        transcript_path.rename(archive_path)
                        logger.info(f"Archived transcript to {archive_path}")
                else:
                    # Delete transcript directly
                    deleted = delete_transcript(target.canonical_key)
                    if deleted:
                        logger.info(f"Deleted transcript for {target.canonical_key}")
            except Exception as e:
                logger.warning(f"Failed to delete transcript for {target.canonical_key}: {e}")
        
        # Fire lifecycle hooks — mirrors TS triggerInternalHook("session:reset") chain
        try:
            from openclaw.hooks.internal_hooks import trigger_internal_hook, InternalHookEvent
            await trigger_internal_hook(InternalHookEvent(
                type="session",
                action="reset",
                session_key=target.canonical_key,
                context={
                    "oldSessionId": old_session_id,
                    "newSessionId": new_session_id,
                    "key": target.canonical_key,
                },
            ))
        except Exception as hook_exc:
            logger.debug("session:reset hook error: %s", hook_exc)

        # Unbind thread bindings for the old session key
        try:
            from openclaw.channels.thread_bindings import unbind_thread_bindings_by_session_key
            await unbind_thread_bindings_by_session_key(target.canonical_key)
        except Exception:
            pass

        # Return updated entry shape (TS-compatible sessions.reset payload)
        updated_store = load_session_store(target.store_path)
        updated_entry = updated_store.get(target.canonical_key)
        return {
            "ok": True,
            "key": target.canonical_key,
            "entry": updated_entry.model_dump(exclude_none=False) if updated_entry else {"sessionId": new_session_id},
        }
    
    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
            },
            "required": ["key"],
        }


# =============================================================================
# sessions.delete
# =============================================================================

class SessionsDeleteMethod:
    """Delete session with protection"""
    
    name = "sessions.delete"
    description = "Delete session with main session protection"
    category = "sessions"
    
    async def execute(self, connection: Any, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute sessions.delete

        Params:
        - key: Session key (required)
        - deleteTranscript: Delete transcript permanently (default: false) — TS canonical name.
          Also accepts legacy ``archiveTranscript`` (inverted semantics: true=archive/keep).

        Returns:
        - { ok: true, deleted: bool }
        """
        key = params.get("key")
        if not key:
            raise ValueError("key is required")

        # TS uses deleteTranscript: true → permanently delete the JSONL file.
        # Legacy Python used archiveTranscript: true → rename (archive) before deleting.
        # Support both: prefer the TS canonical param.
        if "deleteTranscript" in params:
            # TS: deleteTranscript=true means actually delete; false means keep
            archive_transcript = not bool(params.get("deleteTranscript", False))
        else:
            # Legacy Python param
            archive_transcript = params.get("archiveTranscript", True)
        
        target = resolve_gateway_session_store_target(key)
        
        # Check if this is the main session
        main_key = resolve_main_session_key(target.agent_id)
        if target.canonical_key == main_key:
            raise ValueError("Cannot delete main session")

        # Runtime cleanup before deleting
        store = load_session_store(target.store_path)
        entry = store.get(target.canonical_key)
        old_session_id = entry.sessionId if entry else None
        await _ensure_session_runtime_cleanup(connection, key, old_session_id)
        archived: list[str] = []
        
        if entry and entry.sessionId:
            transcript_path = get_session_transcript_path(target.canonical_key)
            if archive_transcript and transcript_path.exists():
                archive_name = f"{transcript_path.stem}_delete_{int(time.time() * 1000)}{transcript_path.suffix}"
                archive_path = transcript_path.parent / archive_name
                transcript_path.rename(archive_path)
                archived.append(str(archive_path))
            else:
                delete_transcript(target.canonical_key)
        
        # Delete from store
        def mutator(store_dict: Dict[str, SessionEntry]) -> None:
            if target.canonical_key in store_dict:
                del store_dict[target.canonical_key]

        update_session_store(target.store_path, mutator)

        # Fire lifecycle hooks — mirrors TS triggerInternalHook("session:delete") chain
        try:
            from openclaw.hooks.internal_hooks import trigger_internal_hook, InternalHookEvent
            await trigger_internal_hook(InternalHookEvent(
                type="session",
                action="delete",
                session_key=target.canonical_key,
                context={"sessionId": old_session_id, "key": target.canonical_key},
            ))
        except Exception as hook_exc:
            logger.debug("session:delete hook error: %s", hook_exc)

        # Unbind thread bindings for the deleted session key
        try:
            from openclaw.channels.thread_bindings import unbind_thread_bindings_by_session_key
            await unbind_thread_bindings_by_session_key(target.canonical_key)
        except Exception:
            pass

        return {"ok": True, "key": target.canonical_key, "deleted": True, "archived": archived}
    
    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "archiveTranscript": {"type": "boolean"},
            },
            "required": ["key"],
        }


# =============================================================================
# sessions.compact
# =============================================================================

class SessionsCompactMethod:
    """Compact session transcript"""
    
    name = "sessions.compact"
    description = "Compact session transcript by keeping last N lines"
    category = "sessions"
    
    async def execute(self, connection: Any, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute sessions.compact
        
        Params:
        - key: Session key (required)
        - maxLines: Keep last N lines (required)
        
        Returns:
        - { ok: true, removedLines: int, keptLines: int, archivedPath: str }
        """
        key = params.get("key")
        if not key:
            raise ValueError("key is required")
        
        max_lines = params.get("maxLines")
        if max_lines is None:
            raise ValueError("maxLines is required")
        
        target = resolve_gateway_session_store_target(key)
        store = load_session_store(target.store_path)
        entry = store.get(target.canonical_key)
        
        if not entry or not entry.sessionId:
            raise ValueError(f"Session not found: {key}")
        
        transcript = load_session_transcript(target.canonical_key)
        if not transcript:
            return {
                "ok": True,
                "key": target.canonical_key,
                "compacted": False,
                "reason": "no transcript",
            }

        lines = [l for l in transcript.splitlines() if l.strip()]
        if len(lines) <= max_lines:
            return {
                "ok": True,
                "key": target.canonical_key,
                "compacted": False,
                "kept": len(lines),
            }

        transcript_path = get_session_transcript_path(target.canonical_key)

        kept_lines = lines[-max_lines:]

        from openclaw.agents.session_lock import acquire_session_write_lock_cached
        async with acquire_session_write_lock_cached(transcript_path):
            archived_path = ""
            if transcript_path.exists():
                archive_name = f"{transcript_path.stem}_compaction_{int(time.time() * 1000)}{transcript_path.suffix}"
                archive_path = transcript_path.parent / archive_name
                transcript_path.rename(archive_path)
                archived_path = str(archive_path)

            save_session_transcript(target.canonical_key, "\n".join(kept_lines) + "\n")
        
        # Update store: clear token counts, increment compaction count
        def mutator(store_dict: Dict[str, SessionEntry]) -> None:
            if target.canonical_key in store_dict:
                e = store_dict[target.canonical_key]
                e.input_tokens = None
                e.output_tokens = None
                e.total_tokens = None
                e.compaction_count = (e.compaction_count or 0) + 1
                e.updatedAt = int(time.time() * 1000)
        
        update_session_store(target.store_path, mutator)
        
        return {
            "ok": True,
            "key": target.canonical_key,
            "compacted": True,
            "archived": [archived_path] if archived_path else [],
            "kept": len(kept_lines),
        }
    
    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "maxLines": {"type": "integer"},
            },
            "required": ["key", "maxLines"],
        }
