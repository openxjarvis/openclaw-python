"""
Session Entry - Complete session metadata structure (aligned with openclaw SessionEntry)

This module defines the SessionEntry structure that stores all session metadata
in sessions.json, matching the TypeScript implementation.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, RootModel


class SessionOrigin(BaseModel):
    """Session origin information - tracks where the session started"""
    label: Optional[str] = None
    provider: Optional[str] = None  # Legacy field (now 'channel')
    channel: Optional[str] = None
    surface: Optional[str] = None
    chatType: Optional[str] = None
    from_: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    accountId: Optional[str] = None
    threadId: Optional[str | int] = None
    nativeChannelId: Optional[str] = None
    nativeDirectUserId: Optional[str] = None


class DeliveryContext(BaseModel):
    """Delivery context for outbound messages"""
    channel: Optional[str] = None
    to: Optional[str] = None
    accountId: Optional[str] = None
    threadId: Optional[str | int] = None
    replyToMessageId: Optional[str] = None


class SessionSkillEntry(BaseModel):
    """Skill entry in session snapshot — mirrors TS skills[] item."""
    name: str
    primaryEnv: Optional[str] = None
    requiredEnv: Optional[list[str]] = None


class SessionSkillSnapshot(BaseModel):
    """Snapshot of skills enabled for this session — mirrors TS SessionSkillSnapshot."""
    prompt: str = ""
    skills: list[SessionSkillEntry] = Field(default_factory=list)
    skillFilter: Optional[list[str]] = None
    resolvedSkills: Optional[list[dict[str, Any]]] = None
    version: Optional[int] = None
    # Legacy Python shape (kept for backward compatibility when loading old stores)
    enabled: Optional[dict[str, bool]] = None
    apiKeys: Optional[dict[str, str]] = None


class SessionAcpIdentity(BaseModel):
    state: Literal["pending", "resolved"]
    acpxRecordId: Optional[str] = None
    acpxSessionId: Optional[str] = None
    agentSessionId: Optional[str] = None
    source: Literal["ensure", "status", "event"]
    lastUpdatedAt: int


class SessionAcpMeta(BaseModel):
    """ACP session metadata — mirrors TS SessionAcpMeta."""
    backend: str
    agent: str
    runtimeSessionName: str
    identity: Optional[SessionAcpIdentity] = None
    mode: Literal["persistent", "oneshot"]
    runtimeOptions: Optional[dict[str, Any]] = None
    cwd: Optional[str] = None
    state: Literal["idle", "running", "error"]
    lastActivityAt: int
    lastError: Optional[str] = None


class SessionCompactionTranscriptReference(BaseModel):
    sessionId: str
    sessionFile: Optional[str] = None
    leafId: Optional[str] = None
    entryId: Optional[str] = None


class SessionCompactionCheckpoint(BaseModel):
    """Compaction checkpoint DAG node — mirrors TS SessionCompactionCheckpoint."""
    checkpointId: str
    sessionKey: str
    sessionId: str
    createdAt: int
    reason: Literal["manual", "auto-threshold", "overflow-retry", "timeout-retry"]
    tokensBefore: Optional[int] = None
    tokensAfter: Optional[int] = None
    summary: Optional[str] = None
    firstKeptEntryId: Optional[str] = None
    preCompaction: SessionCompactionTranscriptReference
    postCompaction: SessionCompactionTranscriptReference


class CliSessionBinding(BaseModel):
    sessionId: str
    authProfileId: Optional[str] = None
    authEpoch: Optional[str] = None
    authEpochVersion: Optional[int] = None
    extraSystemPromptHash: Optional[str] = None
    mcpConfigHash: Optional[str] = None
    mcpResumeHash: Optional[str] = None


class SessionPluginDebugEntry(BaseModel):
    pluginId: str
    lines: list[str] = Field(default_factory=list)


class SessionSystemPromptReportWorkspaceFile(BaseModel):
    """Injected workspace file entry in the system prompt report"""
    name: str
    path: str
    missing: bool = False
    rawChars: int = 0
    injectedChars: int = 0
    truncated: bool = False


class SessionSystemPromptReportSkillEntry(BaseModel):
    """Skill entry in the system prompt report"""
    name: str
    blockChars: int = 0


class SessionSystemPromptReportToolEntry(BaseModel):
    """Tool entry in the system prompt report"""
    name: str
    summaryChars: int = 0
    schemaChars: int = 0
    propertiesCount: Optional[int] = None


class SessionSystemPromptReport(BaseModel):
    """System prompt build report — aligned with TS SessionSystemPromptReport"""
    source: Literal["run", "estimate"] = "estimate"
    generatedAt: int = Field(default_factory=lambda: int(__import__("time").time() * 1000))
    sessionId: Optional[str] = None
    sessionKey: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    workspaceDir: Optional[str] = None
    bootstrapMaxChars: Optional[int] = None
    bootstrapTotalMaxChars: Optional[int] = None
    sandbox: Optional[dict[str, Any]] = None
    systemPrompt: dict[str, int] = Field(
        default_factory=lambda: {"chars": 0, "projectContextChars": 0, "nonProjectContextChars": 0}
    )
    injectedWorkspaceFiles: list[SessionSystemPromptReportWorkspaceFile] = Field(default_factory=list)
    skills: dict[str, Any] = Field(
        default_factory=lambda: {"promptChars": 0, "entries": []}
    )
    tools: dict[str, Any] = Field(
        default_factory=lambda: {"listChars": 0, "schemaChars": 0, "entries": []}
    )
    # Nested object — mirrors TS SessionSystemPromptReport.bootstrapTruncation
    bootstrapTruncation: Optional[dict[str, Any]] = Field(
        default=None,
        description="Bootstrap truncation details: {warningMode, truncatedFiles, chars}"
    )
    # Legacy flat fields preserved for back-compat (populate from bootstrapTruncation when read)
    bootstrapTruncationWarning: Optional[bool] = None
    bootstrapTruncationFiles: Optional[list[str]] = None
    bootstrapTruncationChars: Optional[int] = None


class SessionEntry(BaseModel):
    """
    Complete session metadata (aligned with openclaw SessionEntry)
    
    Stored in sessions.json as: {sessionKey: SessionEntry}
    """
    # Core identity  
    sessionId: str = Field(..., description="UUID v4 session identifier")
    updatedAt: int = Field(default_factory=lambda: int(__import__("time").time() * 1000), description="Last update timestamp (Unix ms)")
    sessionFile: Optional[str] = Field(None, description="Custom transcript path override")
    
    # Parent relationship
    spawnedBy: Optional[str] = Field(None, description="Parent session key that spawned this session")
    spawnedWorkspaceDir: Optional[str] = None
    parentSessionKey: Optional[str] = None
    forkedFromParent: Optional[bool] = None

    # Subagent lifecycle (persisted after completion)
    startedAt: Optional[int] = None
    endedAt: Optional[int] = None
    runtimeMs: Optional[int] = None
    status: Optional[Literal["running", "done", "failed", "killed", "timeout"]] = None
    subagentRole: Optional[Literal["orchestrator", "leaf"]] = None
    subagentControlScope: Optional[Literal["children", "none"]] = None

    # Abort cutoff (/stop boundary)
    abortCutoffMessageSid: Optional[str] = None
    abortCutoffTimestamp: Optional[int] = None

    # Session timing
    sessionStartedAt: Optional[int] = None
    lastInteractionAt: Optional[int] = None

    # Cost / cache tracking
    estimatedCostUsd: Optional[float] = None
    cacheRead: Optional[int] = None
    cacheWrite: Optional[int] = None

    # Model flags
    fastMode: Optional[bool] = None
    traceLevel: Optional[str] = None
    liveModelSwitchPending: Optional[bool] = None
    agentRuntimeOverride: Optional[str] = None
    modelOverrideSource: Optional[Literal["auto", "user"]] = None
    agentHarnessId: Optional[str] = None

    # TTS read-latest state
    lastTtsReadLatestHash: Optional[str] = None
    lastTtsReadLatestAt: Optional[int] = None

    # Heartbeat isolation
    heartbeatIsolatedBaseSessionKey: Optional[str] = None
    heartbeatTaskState: Optional[dict[str, int]] = None

    # Compaction checkpoints (full DAG)
    compactionCheckpoints: Optional[list[SessionCompactionCheckpoint]] = None
    memoryFlushContextHash: Optional[str] = None

    # CLI bindings
    cliSessionBindings: Optional[dict[str, CliSessionBinding]] = None

    # Plugin debug + ACP
    pluginDebugEntries: Optional[list[SessionPluginDebugEntry]] = None
    acp: Optional[SessionAcpMeta] = None
    
    # Token statistics
    inputTokens: Optional[int] = Field(None, description="Total input tokens used")
    outputTokens: Optional[int] = Field(None, description="Total output tokens generated")
    totalTokens: Optional[int] = Field(None, description="Total tokens (input + output)")
    contextTokens: Optional[int] = Field(None, description="Context window size in tokens")
    compactionCount: Optional[int] = Field(0, description="Number of compactions performed")
    
    # Model information
    modelProvider: Optional[str] = Field(None, description="Model provider (anthropic, google, openai, etc)")
    model: Optional[str] = Field(None, description="Model identifier")
    providerOverride: Optional[str] = Field(None, description="User-set provider override")
    modelOverride: Optional[str] = Field(None, description="User-set model override")
    authProfileOverride: Optional[str] = Field(None, description="Auth profile override")
    authProfileOverrideSource: Optional[Literal["auto", "user"]] = None
    authProfileOverrideCompactionCount: Optional[int] = None
    
    # Session behavior settings
    thinkingLevel: Optional[str] = Field(None, description="Thinking verbosity level")
    verboseLevel: Optional[str] = Field(None, description="Verbose output level")
    reasoningLevel: Optional[str] = Field(None, description="Reasoning detail level")
    elevatedLevel: Optional[str] = Field(None, description="Elevated privileges level")
    chatType: Optional[str] = Field(None, description="Chat type: direct, group, channel")
    
    # TTS and exec settings
    ttsAuto: Optional[str] = Field(None, description="TTS auto mode")
    execHost: Optional[str] = Field(None, description="Exec host preference")
    execSecurity: Optional[str] = Field(None, description="Exec security level")
    execAsk: Optional[str] = Field(None, description="Exec approval mode")
    execNode: Optional[str] = Field(None, description="Exec node target")
    
    # Response settings
    responseUsage: Optional[Literal["on", "off", "tokens", "full"]] = Field(None, description="Usage reporting mode")
    
    # Group settings
    groupActivation: Optional[Literal["mention", "always"]] = Field(None, description="Group activation mode")
    groupActivationNeedsSystemIntro: Optional[bool] = None
    
    # Send policy
    sendPolicy: Optional[Literal["allow", "deny"]] = Field(None, description="Send policy")
    
    # Queue settings
    queueMode: Optional[Literal["steer", "followup", "collect", "steer-backlog", "steer+backlog", "queue", "interrupt"]] = None
    queueDebounceMs: Optional[int] = None
    queueCap: Optional[int] = None
    queueDrop: Optional[Literal["old", "new", "summarize"]] = None
    
    # Channel information
    channel: Optional[str] = Field(None, description="Primary channel")
    lastChannel: Optional[str] = Field(None, description="Last used channel")
    lastTo: Optional[str] = Field(None, description="Last recipient")
    lastAccountId: Optional[str] = Field(None, description="Last account ID")
    lastThreadId: Optional[str | int] = Field(None, description="Last thread ID")
    
    # Display information
    displayName: Optional[str] = Field(None, description="Display name")
    label: Optional[str] = Field(None, description="User-set label")
    
    # Group information
    groupId: Optional[str] = Field(None, description="Group ID")
    subject: Optional[str] = Field(None, description="Group subject/name")
    groupChannel: Optional[str] = Field(None, description="Group channel")
    space: Optional[str] = Field(None, description="Workspace/space")
    
    # Origin and delivery
    origin: Optional[SessionOrigin] = Field(None, description="Session origin info")
    deliveryContext: Optional[DeliveryContext] = Field(None, description="Default delivery context")
    
    # Memory management
    memoryFlushAt: Optional[int] = Field(None, description="Timestamp when memory was flushed")
    memoryFlushCompactionCount: Optional[int] = Field(None, description="Compaction count at memory flush")
    lastCacheTouchAt: Optional[int] = Field(None, description="Last cache touch timestamp for TTL mode")
    
    # Skills and system prompt
    skillsSnapshot: Optional[SessionSkillSnapshot] = Field(None, description="Skills snapshot")
    systemPromptReport: Optional[SessionSystemPromptReport] = Field(None, description="System prompt report")
    
    # CLI session IDs
    cliSessionIds: Optional[dict[str, str]] = Field(None, description="CLI tool session IDs")
    claudeCliSessionId: Optional[str] = Field(None, description="Claude CLI session ID")
    
    # Sub-agent depth
    spawnDepth: Optional[int] = Field(None, description="0=main, 1=sub-agent, 2=sub-sub-agent")
    
    # Token freshness
    totalTokensFresh: Optional[bool] = Field(None, description="Whether totalTokens is from a fresh snapshot")
    
    # Flags
    systemSent: Optional[bool] = Field(None, description="System message sent flag")
    abortedLastRun: Optional[bool] = Field(None, description="Last run aborted flag")
    
    # Heartbeat deduplication
    lastHeartbeatText: Optional[str] = Field(None, description="Last heartbeat text for deduplication")
    lastHeartbeatSentAt: Optional[int] = Field(None, description="Last heartbeat timestamp (ms)")
    
    # Fallback notice (model fallback metadata)
    fallback_notice_selected_model: Optional[str] = Field(None, alias="fallbackNoticeSelectedModel")
    fallback_notice_active_model: Optional[str] = Field(None, alias="fallbackNoticeActiveModel")
    fallback_notice_reason: Optional[str] = Field(None, alias="fallbackNoticeReason")
    
    class Config:
        populate_by_name = True
        # Allow both camelCase and snake_case field names
        alias_generator = None
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionEntry:
        """Create SessionEntry from dict, handling both naming conventions"""
        # Convert snake_case keys to camelCase if needed
        normalized_data = {}
        for key, value in data.items():
            # Handle session_id -> sessionId
            if key == "session_id" and "sessionId" not in data:
                normalized_data["sessionId"] = value
            # Handle updated_at -> updatedAt
            elif key == "updated_at" and "updatedAt" not in data:
                normalized_data["updatedAt"] = value
            else:
                normalized_data[key] = value
        
        return cls(**normalized_data)
        
    def update_tokens(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """Update token statistics"""
        self.inputTokens = (self.inputTokens or 0) + input_tokens
        self.outputTokens = (self.outputTokens or 0) + output_tokens
        self.totalTokens = (self.inputTokens or 0) + (self.outputTokens or 0)
        self.updatedAt = int(__import__("time").time() * 1000)
    
    def update_model(self, provider: str | None = None, model: str | None = None) -> None:
        """Update model information"""
        if provider:
            self.modelProvider = provider
        if model:
            self.model = model
        self.updatedAt = int(__import__("time").time() * 1000)


class SessionStore(RootModel[dict[str, SessionEntry]]):
    """
    Session store structure - the complete sessions.json file
    
    Format: {sessionKey: SessionEntry}
    
    Uses Pydantic v2 RootModel for dict-based structure
    """
    root: dict[str, SessionEntry] = Field(default_factory=dict)
    
    def get(self, session_key: str) -> SessionEntry | None:
        """Get session entry by key"""
        return self.root.get(session_key)
    
    def set(self, session_key: str, entry: SessionEntry) -> None:
        """Set session entry"""
        self.root[session_key] = entry
    
    def delete(self, session_key: str) -> bool:
        """Delete session entry"""
        if session_key in self.root:
            del self.root[session_key]
            return True
        return False
    
    def keys(self) -> list[str]:
        """Get all session keys"""
        return list(self.root.keys())
    
    def values(self) -> list[SessionEntry]:
        """Get all session entries"""
        return list(self.root.values())
    
    def items(self) -> list[tuple[str, SessionEntry]]:
        """Get all key-entry pairs"""
        return list(self.root.items())
    
    def to_dict(self) -> dict[str, dict[str, Any]]:
        """Export to dict for JSON serialization"""
        return {k: v.model_dump(exclude_none=True, by_alias=True) for k, v in self.root.items()}
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionStore:
        """Load from dict"""
        store = cls(root={})
        for key, entry_data in data.items():
            if isinstance(entry_data, dict):
                # Use SessionEntry.from_dict to handle naming conventions
                store.root[key] = SessionEntry.from_dict(entry_data)
        return store


def merge_session_entry(existing: SessionEntry | None, patch: dict[str, Any]) -> SessionEntry:
    """
    Merge a patch dict into an existing SessionEntry (or create a new one if existing is None).

    The patch can use either camelCase (sessionId) or snake_case (session_id) keys —
    both are normalised before merging.

    Args:
        existing: The existing SessionEntry or None to create from scratch.
        patch: Partial update data (camelCase or snake_case keys).

    Returns:
        Updated SessionEntry with merged data.
    """
    # Normalise snake_case patch keys → camelCase so we can pass them to SessionEntry
    _snake_to_camel = {
        "session_id": "sessionId",
        "updated_at": "updatedAt",
        "session_file": "sessionFile",
        "spawned_by": "spawnedBy",
        "thinking_level": "thinkingLevel",
        "verbose_level": "verboseLevel",
        "reasoning_level": "reasoningLevel",
        "input_tokens": "inputTokens",
        "output_tokens": "outputTokens",
        "total_tokens": "totalTokens",
        "context_tokens": "contextTokens",
        "compaction_count": "compactionCount",
        "delivery_context": "deliveryContext",
        "model_provider": "modelProvider",
        "model_override": "modelOverride",
        "provider_override": "providerOverride",
        "fallback_notice_selected_model": "fallbackNoticeSelectedModel",
        "fallback_notice_active_model": "fallbackNoticeActiveModel",
        "fallback_notice_reason": "fallbackNoticeReason",
    }
    normalised_patch: dict[str, Any] = {}
    for key, value in patch.items():
        camel_key = _snake_to_camel.get(key, key)
        normalised_patch[camel_key] = value

    if existing is None:
        # Create new entry — ensure we have a sessionId
        if "sessionId" not in normalised_patch:
            import uuid as _uuid
            normalised_patch["sessionId"] = str(_uuid.uuid4())
        normalised_patch.setdefault("updatedAt", int(__import__("time").time() * 1000))
        return SessionEntry(**normalised_patch)

    # Merge into existing
    existing_dict = existing.model_dump(exclude_none=True)
    for key, value in normalised_patch.items():
        if value is not None:
            existing_dict[key] = value

    # Update timestamp
    existing_dict["updatedAt"] = int(__import__("time").time() * 1000)
    return SessionEntry(**existing_dict)


async def update_session_entry_tokens(
    session_manager,
    session_id: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    context_tokens: int | None = None,
) -> None:
    """
    Update token statistics in SessionEntry.
    
    Args:
        session_manager: SessionManager instance
        session_id: Session UUID
        input_tokens: Input tokens from API response
        output_tokens: Output tokens from API response
        context_tokens: Estimated context tokens
    """
    import time
    
    entry = session_manager.get_entry_by_id(session_id)
    if not entry:
        return
    
    # Update token statistics
    entry.inputTokens = (entry.inputTokens or 0) + input_tokens
    entry.outputTokens = (entry.outputTokens or 0) + output_tokens
    entry.totalTokens = (entry.totalTokens or 0) + input_tokens + output_tokens
    
    if context_tokens is not None:
        entry.contextTokens = context_tokens
    
    entry.totalTokensFresh = True
    entry.updatedAt = int(time.time() * 1000)
    
    # Save updated entry
    await session_manager.save_entry(entry)


# ---------------------------------------------------------------------------
# P2-16 Session helper functions — mirrors TS src/config/sessions/types.ts L314-437
# ---------------------------------------------------------------------------

def resolve_session_total_tokens(entry: "SessionEntry") -> int | None:
    """Return totalTokens only if set and finite — mirrors TS resolveSessionTotalTokens.

    TS: returns totalTokens if finite and >= 0; does NOT sum input+output.
    """
    val = getattr(entry, "totalTokens", None)
    if isinstance(val, int) and val >= 0:
        return val
    return None


def resolve_fresh_session_total_tokens(entry: "SessionEntry") -> int | None:
    """Return totalTokens only if it was set this session (not inherited) — mirrors TS resolveFreshSessionTotalTokens."""
    val = getattr(entry, "totalTokens", None)
    if isinstance(val, int) and val >= 0:
        return val
    # Fall back to computed sum if fresh input/output available
    inp = getattr(entry, "inputTokens", None)
    out = getattr(entry, "outputTokens", None)
    if isinstance(inp, int) and isinstance(out, int):
        return inp + out
    return None


def is_session_total_tokens_fresh(entry: "SessionEntry") -> bool:
    """Return True if totalTokens is present and non-negative — mirrors TS isSessionTotalTokensFresh."""
    val = getattr(entry, "totalTokens", None)
    return isinstance(val, int) and val >= 0


def normalize_session_runtime_model_fields(entry: "SessionEntry") -> "SessionEntry":
    """Normalize model-related fields on a session entry — mirrors TS normalizeSessionRuntimeModelFields.

    Strips whitespace; removes empty strings (→ None); clears stale provider when model changes.
    """
    model_raw = entry.model
    if model_raw is not None:
        stripped = str(model_raw).strip()
        entry.model = stripped if stripped else None  # type: ignore[assignment]

    provider_raw = entry.modelProvider
    if provider_raw is not None:
        stripped = str(provider_raw).strip()
        entry.modelProvider = stripped if stripped else None  # type: ignore[assignment]

    if entry.modelOverride is not None:
        stripped = str(entry.modelOverride).strip()
        entry.modelOverride = stripped if stripped else None  # type: ignore[assignment]

    if entry.providerOverride is not None:
        stripped = str(entry.providerOverride).strip()
        entry.providerOverride = stripped if stripped else None  # type: ignore[assignment]

    # If model was cleared, clear provider too (stale provider guard)
    if entry.model is None and entry.modelProvider is not None:
        entry.modelProvider = None
    return entry


def set_session_runtime_model(
    entry: "SessionEntry",
    provider: str,
    model: str,
) -> "SessionEntry":
    """Set the runtime model and provider — mirrors TS setSessionRuntimeModel(entry, {provider, model}).

    TS signature requires both provider and model.
    """
    import time as _time

    entry.model = str(model).strip() if model else entry.model
    entry.modelProvider = str(provider).strip() if provider else entry.modelProvider
    entry.updatedAt = int(_time.time() * 1000)
    return entry


def merge_session_entry_preserve_activity(
    base: "SessionEntry",
    updates: dict,
) -> "SessionEntry":
    """Merge updates dict into base entry, preserving activity timestamps.

    Activity fields (lastInteractionAt, sessionStartedAt, updatedAt) are kept
    if the incoming updates would regress them.

    Args:
        base: The base session entry.
        updates: Dict of fields to merge.

    Returns:
        Updated session entry.
    """
    activity_fields = {"lastInteractionAt", "sessionStartedAt", "updatedAt"}
    existing_activity = {f: getattr(base, f, None) for f in activity_fields}

    merged_data = base.model_dump()
    for key, value in updates.items():
        if key in merged_data or not key.startswith("_"):
            merged_data[key] = value

    for f in activity_fields:
        old_val = existing_activity.get(f)
        new_val = merged_data.get(f)
        if old_val is not None and (new_val is None or (isinstance(new_val, int) and new_val < old_val)):
            merged_data[f] = old_val

    return SessionEntry.model_validate(merged_data)
