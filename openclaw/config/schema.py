"""Configuration schema using Pydantic (aligned with TypeScript OpenClawConfig)"""
from __future__ import annotations

import builtins

from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Literal, Optional


class ModelConfig(BaseModel):
    """Model configuration"""
    primary: str = Field(default="anthropic/claude-opus-4-5-20250514")
    fallbacks: list[str] = Field(default_factory=list)


class AgentModelEntryConfig(BaseModel):
    """Agent model entry configuration - mirrors TS AgentModelEntryConfig
    
    Represents a model configuration entry in agents.defaults.models dictionary.
    Used for model aliases and provider-specific parameters.
    """
    alias: str | None = Field(default=None)
    """Model alias/identifier"""
    
    params: dict[str, Any] | None = Field(default=None)
    """Provider-specific parameters (e.g., cacheRetention for Anthropic)"""
    
    streaming: bool | None = Field(default=None)
    """Whether this model supports streaming"""
    
    model_config = ConfigDict(populate_by_name=True)


class AgentConfig(BaseModel):
    """Agent configuration"""
    model: str | ModelConfig = Field(default="anthropic/claude-opus-4-5-20250514")
    thinking: str | None = Field(default=None)
    verbose: bool = Field(default=False)
    compaction: CompactionConfig | None = Field(default=None)
    contextPruning: ContextPruningConfig | None = Field(default=None)
    maxHistoryTurns: int = Field(default=50, ge=1)
    maxHistoryShare: float = Field(default=0.5, ge=0.1, le=0.9)


class AuthConfig(BaseModel):
    """Gateway authentication configuration"""
    mode: str = Field(default="token")
    token: str | None = Field(default=None)
    password: str | None = Field(default=None)
    allow_tailscale: bool | None = Field(default=None, alias="allowTailscale")
    trusted_proxy: "GatewayTrustedProxyConfig | dict[str, Any] | None" = Field(default=None, alias="trustedProxy")


class GatewayNodesConfig(BaseModel):
    """Gateway nodes configuration"""
    browser: "BrowserConfig | dict[str, Any] | None" = Field(default=None)
    allow_commands: list[str] | None = Field(default=None, alias="allowCommands")
    deny_commands: list[str] | None = Field(default=None, alias="denyCommands")


class GatewayTailscaleConfig(BaseModel):
    """Gateway Tailscale configuration"""
    mode: str = Field(default="off")  # "off" | "serve" | "funnel"
    reset_on_exit: bool = Field(default=False, alias="resetOnExit")


class GatewayControlUiConfig(BaseModel):
    """Gateway Control UI configuration.
    
    Mirrors TS GatewayControlUiConfig in openclaw/src/config/types.gateway.ts.
    All fields optional to match TS optional properties.
    """
    
    enabled: bool | None = Field(default=None)
    """If false, the Gateway will not serve the Control UI (default /)."""
    
    base_path: str | None = Field(default=None, alias="basePath")
    """Optional base path prefix for the Control UI (e.g. "/openclaw")."""
    
    root: str | None = Field(default=None)
    """Optional filesystem root for Control UI assets (defaults to dist/control-ui)."""
    
    allowed_origins: list[str] | None = Field(default=None, alias="allowedOrigins")
    """Allowed browser origins for Control UI/WebChat websocket connections."""
    
    dangerously_allow_host_header_origin_fallback: bool | None = Field(
        default=None, 
        alias="dangerouslyAllowHostHeaderOriginFallback"
    )
    """DANGEROUS: Keep Host-header origin fallback behavior.
    Supported long-term for deployments that intentionally rely on this policy."""
    
    allow_insecure_auth: bool | None = Field(default=None, alias="allowInsecureAuth")
    """Insecure-auth toggle.
    Control UI still requires secure context + device identity unless
    dangerouslyDisableDeviceAuth is enabled."""
    
    dangerously_disable_device_auth: bool | None = Field(
        default=None, 
        alias="dangerouslyDisableDeviceAuth"
    )
    """DANGEROUS: Disable device identity checks for the Control UI (default: false)."""
    
    model_config = {"populate_by_name": True}


class GatewayTlsConfig(BaseModel):
    """TLS configuration for gateway - mirrors TS TlsConfig"""
    model_config = ConfigDict(populate_by_name=True)
    
    enabled: bool | None = Field(default=None)
    """Whether TLS/SSL is enabled"""
    
    cert: str | None = Field(default=None)
    """Path to TLS certificate file"""
    
    key: str | None = Field(default=None)
    """Path to TLS private key file"""
    
    ca: str | None = Field(default=None)
    """Path to CA certificate for client verification"""


class GatewayReloadConfig(BaseModel):
    """Config reload settings - mirrors TS ReloadConfig"""
    model_config = ConfigDict(populate_by_name=True)
    
    enabled: bool | None = Field(default=None)
    """Whether config hot-reloading is enabled"""
    
    watch_paths: list[str] | None = Field(default=None, alias="watchPaths")
    """Additional paths to watch for changes"""
    
    debounce_ms: int | None = Field(default=None, alias="debounceMs")
    """Debounce delay in milliseconds before reloading"""


class GatewayHttpConfig(BaseModel):
    """HTTP service configuration - mirrors TS HttpConfig"""
    model_config = ConfigDict(populate_by_name=True)
    
    enabled: bool | None = Field(default=None)
    """Whether HTTP endpoints are enabled"""
    
    timeout_ms: int | None = Field(default=None, alias="timeoutMs")
    """HTTP request timeout in milliseconds"""
    
    max_body_size: str | int | None = Field(default=None, alias="maxBodySize")
    """Maximum request body size. Supports "10mb" format or integer bytes"""


class GatewayRemoteConfig(BaseModel):
    """Remote access configuration - mirrors TS RemoteConfig"""
    model_config = ConfigDict(populate_by_name=True)
    
    enabled: bool | None = Field(default=None)
    """Whether remote access is enabled"""
    
    allow_origins: list[str] | None = Field(default=None, alias="allowOrigins")
    """CORS allowed origins for remote access"""
    
    api_keys: list[str] | None = Field(default=None, alias="apiKeys")
    """API keys for remote authentication"""


class GatewayConfig(BaseModel):
    """Gateway server configuration (aligned with TypeScript)"""
    port: int = Field(default=18789)
    bind: str = Field(default="loopback")
    mode: str = Field(default="local")
    auth: AuthConfig | None = Field(default=None)
    control_ui: GatewayControlUiConfig | None = Field(default=None, alias="controlUi")
    trusted_proxies: list[str] = Field(default_factory=list, alias="trustedProxies")
    nodes: GatewayNodesConfig | None = Field(default=None)
    tailscale: GatewayTailscaleConfig | None = Field(default=None)
    
    # New fields (aligned with TS)
    tls: GatewayTlsConfig | None = Field(default=None)
    """TLS/SSL configuration"""
    
    reload: GatewayReloadConfig | None = Field(default=None)
    """Config hot-reload settings"""
    
    http: GatewayHttpConfig | None = Field(default=None)
    """HTTP service configuration"""
    
    remote: GatewayRemoteConfig | None = Field(default=None)
    """Remote access configuration"""
    
    channel_health_check_minutes: int | None = Field(
        default=None,
        alias="channelHealthCheckMinutes"
    )
    """Channel health check interval in minutes"""
    
    # Legacy fields for backward compatibility
    enable_web_ui: bool | None = Field(default=None, alias="enableWebUI", exclude=True)
    web_ui_port: int | None = Field(default=None, alias="webUIPort", exclude=True)
    web_ui_base_path: str | None = Field(default=None, alias="webUIBasePath", exclude=True)
    
    model_config = {"populate_by_name": True}


class ApplyPatchConfig(BaseModel):
    """apply_patch tool sub-configuration (aligned with TS ExecToolConfig.applyPatch)"""
    enabled: bool = Field(default=False)  # Aligns with TS default (security-first)
    workspace_only: bool = Field(default=True, alias="workspaceOnly")
    allow_models: list[str] | None = Field(default=None, alias="allowModels")

    model_config = {"populate_by_name": True}


class ExecToolConfig(BaseModel):
    """Exec tool configuration (fully aligned with TS ExecToolConfig)"""
    host: str = Field(default="sandbox")          # "sandbox" | "gateway" | "node" - Aligns with TS default
    security: str = Field(default="deny")         # TS default: "deny"
    ask: str = Field(default="on-miss")           # TS default: "on-miss"
    ask_fallback: str = Field(default="deny", alias="askFallback")
    node: str | None = Field(default=None)        # bound node id for host=node
    safe_bins: list[str] = Field(
        default_factory=lambda: ["python", "pip", "git", "node", "npm"],
        alias="safeBins",
    )
    safe_bin_trusted_dirs: list[str] = Field(default_factory=list, alias="safeBinTrustedDirs")
    safe_bin_profiles: dict[str, Any] = Field(default_factory=dict, alias="safeBinProfiles")
    path_prepend: list[str] = Field(default_factory=list, alias="pathPrepend")
    timeout_sec: int = Field(default=120, alias="timeoutSec")
    background_ms: int = Field(default=10_000, alias="backgroundMs")       # auto-background after N ms
    approval_running_notice_ms: int = Field(default=10_000, alias="approvalRunningNoticeMs")
    cleanup_ms: int = Field(default=1_800_000, alias="cleanupMs")           # 30 min TTL for finished sessions
    notify_on_exit: bool = Field(default=False, alias="notifyOnExit")
    notify_on_exit_empty_success: bool = Field(default=False, alias="notifyOnExitEmptySuccess")
    apply_patch: ApplyPatchConfig = Field(default_factory=ApplyPatchConfig, alias="applyPatch")

    model_config = {"populate_by_name": True}


class AgentToAgentConfig(BaseModel):
    """Agent-to-agent routing configuration (aligned with TS)"""
    enabled: bool = Field(default=False)
    allow: list[str] = Field(default_factory=list)
    maxPingPongTurns: int = Field(default=5, ge=0, le=5)


class SessionsToolsConfig(BaseModel):
    """Sessions tools visibility configuration (aligned with TS)"""
    visibility: str = Field(default="tree")  # "self" | "tree" | "agent" | "all"


class ElevatedAllowFromEntry(BaseModel):
    """Per-provider sender allowlist for /elevated mode"""
    provider: str
    senders: list[str] = Field(default_factory=list)


class ElevatedConfig(BaseModel):
    """Elevated mode configuration (aligned with TS elevated config)"""
    enabled: bool = Field(default=False)
    allow_from: list[ElevatedAllowFromEntry] | None = Field(default=None, alias="allowFrom")

    model_config = {"populate_by_name": True}


class ToolPolicyConfig(BaseModel):
    """Per-provider or per-sender tool policy (aligned with TS ToolPolicyConfig)"""
    allow: list[str] | None = Field(default=None)
    also_allow: list[str] | None = Field(default=None, alias="alsoAllow")
    deny: list[str] | None = Field(default=None)
    profile: str | None = Field(default=None)

    model_config = {"populate_by_name": True}


class LoopDetectionDetectorsConfig(BaseModel):
    """Loop detection detectors configuration - mirrors TS ToolLoopDetectorsSchema"""
    genericRepeat: bool | None = Field(default=None)
    knownPollNoProgress: bool | None = Field(default=None)
    pingPong: bool | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


class LoopDetectionConfig(BaseModel):
    """Tool loop detection configuration - mirrors TS ToolLoopDetectionSchema"""
    enabled: bool | None = Field(default=None)
    historySize: int | None = Field(default=None)           # TS field name (was "window" - wrong)
    warningThreshold: int | None = Field(default=None)      # missing in Python before
    criticalThreshold: int | None = Field(default=None)     # TS field name (was "threshold" - wrong)
    globalCircuitBreakerThreshold: int | None = Field(default=None)
    detectors: LoopDetectionDetectorsConfig | None = Field(default=None)

    # Legacy aliases for backward compatibility (old Python-specific names)
    window: int | None = Field(default=None, exclude=True)
    threshold: int | None = Field(default=None, exclude=True)

    model_config = ConfigDict(populate_by_name=True)

    def model_post_init(self, __context: object) -> None:
        """Migrate legacy field names"""
        if self.window is not None and self.historySize is None:
            self.historySize = self.window
        if self.threshold is not None and self.criticalThreshold is None:
            self.criticalThreshold = self.threshold


class MessageCrossContextMarkerConfig(BaseModel):
    """Cross-context marker config (aligned with TS tools.message.crossContext.marker)"""
    enabled: bool | None = Field(default=None)
    prefix: str | None = Field(default=None)
    suffix: str | None = Field(default=None)

    model_config = {"populate_by_name": True}


class MessageCrossContextConfig(BaseModel):
    """Cross-context send permissions (aligned with TS tools.message.crossContext)"""
    # default=None so we can distinguish "not set" (→ TS-equivalent true/false defaults)
    allow_within_provider: bool | None = Field(default=None, alias="allowWithinProvider")
    allow_across_providers: bool | None = Field(default=None, alias="allowAcrossProviders")
    marker: MessageCrossContextMarkerConfig | None = Field(default=None)

    model_config = {"populate_by_name": True}


class MessageBroadcastConfig(BaseModel):
    """Broadcast action config (aligned with TS tools.message.broadcast)"""
    enabled: bool | None = Field(default=None)

    model_config = {"populate_by_name": True}


class MessageToolConfig(BaseModel):
    """Message tool configuration (aligned with TS tools.message)"""
    # @deprecated — kept for backward compat; use crossContext instead
    allow_cross_context_send: bool | None = Field(default=None, alias="allowCrossContextSend")
    cross_context: MessageCrossContextConfig | None = Field(default=None, alias="crossContext")
    broadcast: MessageBroadcastConfig | None = Field(default=None)

    model_config = {"populate_by_name": True}


class SubagentsToolsConfig(BaseModel):
    """Subagent tool policy defaults (aligned with TS subagents.tools)"""
    allow: list[str] | None = Field(default=None)
    also_allow: list[str] | None = Field(default=None, alias="alsoAllow")
    deny: list[str] | None = Field(default=None)

    model_config = {"populate_by_name": True}


class SandboxToolsConfig(BaseModel):
    """Sandbox tool policy at ToolsConfig level (aligned with TS sandbox.tools)"""
    allow: list[str] | None = Field(default=None)
    deny: list[str] | None = Field(default=None)


class FsToolsConfig(BaseModel):
    """Filesystem tool restrictions (aligned with TS FsToolsConfig)"""
    workspace_only: bool = Field(default=False, alias="workspaceOnly")

    model_config = {"populate_by_name": True}


class ToolsWebSearchConfig(BaseModel):
    """Web search tool configuration - mirrors TS ToolsWebSearchSchema"""
    enabled: bool | None = Field(default=None)
    provider: Literal["brave", "perplexity", "grok", "gemini", "kimi"] | None = Field(default=None)
    apiKey: str | None = Field(default=None)
    maxResults: int | None = Field(default=None)
    timeoutSeconds: int | None = Field(default=None)
    cacheTtlMinutes: float | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class ToolsWebFetchConfig(BaseModel):
    """Web fetch tool configuration - mirrors TS ToolsWebFetchSchema"""
    enabled: bool | None = Field(default=None)
    maxChars: int | None = Field(default=None)
    maxCharsCap: int | None = Field(default=None)
    timeoutSeconds: int | None = Field(default=None)
    cacheTtlMinutes: float | None = Field(default=None)
    maxRedirects: int | None = Field(default=None)
    userAgent: str | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


class ToolsWebConfig(BaseModel):
    """Web tools configuration (search + fetch) - mirrors TS tools.web schema"""
    search: ToolsWebSearchConfig | None = Field(default=None)
    fetch: ToolsWebFetchConfig | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


class ToolsSessionsSpawnAttachmentsConfig(BaseModel):
    """Spawned session attachments configuration - mirrors TS"""
    enabled: bool | None = Field(default=None)
    maxTotalBytes: float | None = Field(default=None)
    maxFiles: float | None = Field(default=None)
    maxFileBytes: float | None = Field(default=None)
    retainOnSessionKeep: bool | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


class ToolsSessionsSpawnConfig(BaseModel):
    """Sessions spawn tool configuration - mirrors TS tools.sessions_spawn schema"""
    attachments: ToolsSessionsSpawnAttachmentsConfig | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


class ToolsConfig(BaseModel):
    """Tools configuration - mirrors TypeScript ToolsSchema"""
    profile: str = Field(default="full")
    allow: list[str] | None = Field(default=None)
    also_allow: list[str] | None = Field(default=None, alias="alsoAllow")
    deny: list[str] | None = Field(default=None)
    by_provider: dict[str, ToolPolicyConfig] | None = Field(default=None, alias="byProvider")
    elevated: ElevatedConfig | None = Field(default=None)
    exec: ExecToolConfig | None = Field(default_factory=ExecToolConfig)
    fs: FsToolsConfig | None = Field(default=None)
    subagents: SubagentsToolsConfig | None = Field(default=None)
    sandbox: SandboxToolsConfig | None = Field(default=None)
    loop_detection: LoopDetectionConfig | None = Field(default=None, alias="loopDetection")
    sessions: SessionsToolsConfig | None = Field(default=None)
    sessions_spawn: ToolsSessionsSpawnConfig | None = Field(default=None, alias="sessions_spawn")
    message: MessageToolConfig | None = Field(default=None)
    agentToAgent: AgentToAgentConfig | None = Field(default=None)
    # Fields added in P2 alignment - mirrors TS ToolsSchema
    web: ToolsWebConfig | None = Field(default=None)
    media: dict[str, Any] | None = Field(default=None)
    links: dict[str, Any] | None = Field(default=None)

    model_config = {"populate_by_name": True}


class SoftTrimConfig(BaseModel):
    """Soft trim configuration for context pruning"""
    maxChars: int = Field(default=4000)
    headChars: int = Field(default=1500)
    tailChars: int = Field(default=1500)


class HardClearConfig(BaseModel):
    """Hard clear configuration for context pruning"""
    enabled: bool = Field(default=True)
    placeholder: str = Field(default="[Old tool result content cleared]")


class ContextPruningToolsConfig(BaseModel):
    """Tools configuration for context pruning - mirrors TS contextPruning.tools schema"""
    allow: list[str] | None = Field(default=None)   # TS field: allow specific tools for pruning
    deny: list[str] | None = Field(default=None)    # TS field: deny specific tools from pruning
    # Legacy Python-only field kept for backward compat (not in TS)
    prunable: list[str] | None = Field(default=None, exclude=True)

    model_config = ConfigDict(populate_by_name=True)


class ContextPruningConfig(BaseModel):
    """Context pruning configuration - mirrors TypeScript AgentContextPruningConfig"""
    mode: str = Field(default="off")  # "off" | "cache-ttl"
    ttl: str | None = Field(default="5m")
    softTrimRatio: float = Field(default=0.3, ge=0.0, le=1.0)
    hardClearRatio: float = Field(default=0.5, ge=0.0, le=1.0)
    keepLastAssistants: int = Field(default=3, ge=0)
    softTrim: SoftTrimConfig = Field(default_factory=SoftTrimConfig)
    hardClear: HardClearConfig = Field(default_factory=HardClearConfig)
    tools: ContextPruningToolsConfig = Field(default_factory=ContextPruningToolsConfig)
    minPrunableToolChars: int = Field(default=50000, ge=0)


class CompactionConfig(BaseModel):
    """Compaction configuration - mirrors TypeScript AgentCompactionConfig"""
    enabled: bool = Field(default=True)
    mode: str = Field(default="safeguard")  # "default" | "safeguard"
    reserveTokens: int = Field(default=16384, ge=0)
    keepRecentTokens: int = Field(default=20000, ge=0)
    maxHistoryShare: float = Field(default=0.5, ge=0.1, le=0.9)
    reserveTokensFloor: int | None = Field(default=None, ge=0)
    # Fields from TS compaction schema (added in P2 alignment)
    identifierPolicy: Literal["strict", "off", "custom"] | None = Field(default=None)
    identifierInstructions: str | None = Field(default=None)
    memoryFlush: "MemoryFlushConfig | None" = Field(default=None)  # TS location is compaction.memoryFlush

    model_config = ConfigDict(populate_by_name=True)


class HeartbeatActiveHoursConfig(BaseModel):
    """Heartbeat active hours - mirrors TS HeartbeatActiveHoursSchema"""
    start: str | None = Field(default=None)
    end: str | None = Field(default=None)
    timezone: str | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


class HeartbeatConfig(BaseModel):
    """Heartbeat configuration - mirrors TS HeartbeatSchema"""
    every: str | None = Field(default=None)
    activeHours: HeartbeatActiveHoursConfig | None = Field(default=None)
    model: str | None = Field(default=None)
    session: str | None = Field(default=None)
    includeReasoning: bool | None = Field(default=None)
    target: str | None = Field(default=None)
    directPolicy: Literal["allow", "block"] | None = Field(default=None)
    to: str | None = Field(default=None)
    accountId: str | None = Field(default=None)
    prompt: str | None = Field(default=None)
    ackMaxChars: int | None = Field(default=None)
    suppressToolErrorWarnings: bool | None = Field(default=None)
    lightContext: bool | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


class CliWatchdogConfig(BaseModel):
    """CLI watchdog configuration - mirrors TS CLI watchdog config"""
    model_config = ConfigDict(populate_by_name=True)
    
    no_output_timeout_ms: Optional[int] = Field(default=None, alias="noOutputTimeoutMs")
    """Fixed watchdog timeout in ms (overrides ratio when set)"""
    
    no_output_timeout_ratio: Optional[float] = Field(default=None, alias="noOutputTimeoutRatio")
    """Fraction of overall timeout used when fixed timeout is not set"""
    
    min_ms: Optional[int] = Field(default=None, alias="minMs")
    """Lower bound for computed watchdog timeout"""
    
    max_ms: Optional[int] = Field(default=None, alias="maxMs")
    """Upper bound for computed watchdog timeout"""


class CliReliabilityConfig(BaseModel):
    """CLI reliability configuration - mirrors TS reliability config"""
    model_config = ConfigDict(populate_by_name=True)
    
    watchdog: Optional[dict[str, CliWatchdogConfig]] = Field(default=None)
    """No-output watchdog tuning (fresh vs resumed runs)"""


class CliBackendConfig(BaseModel):
    """CLI backend configuration - mirrors TS CliBackendConfig"""
    model_config = ConfigDict(populate_by_name=True)
    
    command: str
    """CLI command to execute (absolute path or on PATH)"""
    
    args: Optional[list[str]] = Field(default=None)
    """Base args applied to every invocation"""
    
    output: Optional[Literal["json", "text", "jsonl"]] = Field(default=None)
    """Output parsing mode (default: json)"""
    
    resume_output: Optional[Literal["json", "text", "jsonl"]] = Field(default=None, alias="resumeOutput")
    """Output parsing mode when resuming a CLI session"""
    
    input: Optional[Literal["arg", "stdin"]] = Field(default=None)
    """Prompt input mode (default: arg)"""
    
    max_prompt_arg_chars: Optional[int] = Field(default=None, alias="maxPromptArgChars")
    """Max prompt length for arg mode (if exceeded, stdin is used)"""
    
    env: Optional[dict[str, str]] = Field(default=None)
    """Extra env vars injected for this CLI"""
    
    clear_env: Optional[list[str]] = Field(default=None, alias="clearEnv")
    """Env vars to remove before launching this CLI"""
    
    model_arg: Optional[str] = Field(default=None, alias="modelArg")
    """Flag used to pass model id (e.g. --model)"""
    
    model_aliases: Optional[dict[str, str]] = Field(default=None, alias="modelAliases")
    """Model aliases mapping (config model id → CLI model id)"""
    
    session_arg: Optional[str] = Field(default=None, alias="sessionArg")
    """Flag used to pass session id (e.g. --session-id)"""
    
    session_args: Optional[list[str]] = Field(default=None, alias="sessionArgs")
    """Extra args used when resuming a session (use {sessionId} placeholder)"""
    
    resume_args: Optional[list[str]] = Field(default=None, alias="resumeArgs")
    """Alternate args to use when resuming a session (use {sessionId} placeholder)"""
    
    session_mode: Optional[Literal["always", "existing", "none"]] = Field(default=None, alias="sessionMode")
    """When to pass session ids"""
    
    session_id_fields: Optional[list[str]] = Field(default=None, alias="sessionIdFields")
    """JSON fields to read session id from (in order)"""
    
    system_prompt_arg: Optional[str] = Field(default=None, alias="systemPromptArg")
    """Flag used to pass system prompt"""
    
    system_prompt_mode: Optional[Literal["append", "replace"]] = Field(default=None, alias="systemPromptMode")
    """System prompt behavior (append vs replace)"""
    
    system_prompt_when: Optional[Literal["first", "always", "never"]] = Field(default=None, alias="systemPromptWhen")
    """When to send system prompt"""
    
    image_arg: Optional[str] = Field(default=None, alias="imageArg")
    """Flag used to pass image paths"""
    
    image_mode: Optional[Literal["repeat", "list"]] = Field(default=None, alias="imageMode")
    """How to pass multiple images"""
    
    serialize: Optional[bool] = Field(default=None)
    """Serialize runs for this CLI"""
    
    reliability: Optional[CliReliabilityConfig] = Field(default=None)
    """Runtime reliability tuning for this backend's process lifecycle"""


class AgentDefaults(BaseModel):
    """Default agent settings - mirrors TS AgentDefaultsSchema"""

    workspace: str | None = Field(default=None)
    agentDir: str | None = Field(default=None)
    model: str | ModelConfig = Field(default="google/gemini-3-pro-preview")
    models: dict[str, AgentModelEntryConfig] | None = Field(default=None)
    """Model directory mapping model IDs to their configuration entries"""
    tools: ToolsConfig | None = Field(default=None)
    compaction: CompactionConfig | None = Field(default=None)
    contextPruning: ContextPruningConfig | None = Field(default=None)
    maxHistoryTurns: int = Field(default=50, ge=1)
    maxHistoryShare: float = Field(default=0.5, ge=0.1, le=0.9)
    maxConcurrent: int | None = Field(default=None)
    subagents: "SubagentsConfig | None" = Field(default=None)
    sandbox: "SandboxConfig | None" = Field(default=None)
    # Memory configuration - mirrors TS agents.defaults
    memorySearch: "MemorySearchConfig | None" = Field(default=None)
    memoryFlush: "MemoryFlushConfig | None" = Field(default=None, exclude=True)  # deprecated: use compaction.memoryFlush
    # Block streaming configuration - mirrors TS agents.defaults
    blockStreamingDefault: Literal["on", "off"] | None = Field(default=None)
    blockStreamingBreak: Literal["text_end", "message_end"] | None = Field(default=None)
    blockStreamingChunk: "BlockStreamingChunkConfig | dict[str, Any] | None" = Field(default=None)
    blockStreamingCoalesce: "BlockStreamingCoalesceConfig | dict[str, Any] | None" = Field(default=None)
    # Fields added in P2 alignment - mirrors TS AgentDefaultsSchema
    imageModel: str | ModelConfig | None = Field(default=None)
    pdfModel: str | ModelConfig | None = Field(default=None)
    pdfMaxBytesMb: float | None = Field(default=None)
    pdfMaxPages: int | None = Field(default=None)
    repoRoot: str | None = Field(default=None)
    skipBootstrap: bool | None = Field(default=None)
    bootstrapMaxChars: int | None = Field(default=None)
    bootstrapTotalMaxChars: int | None = Field(default=None)
    userTimezone: str | None = Field(default=None)
    timeFormat: Literal["auto", "12", "24"] | None = Field(default=None)
    envelopeTimezone: str | None = Field(default=None)
    envelopeTimestamp: Literal["on", "off"] | None = Field(default=None)
    envelopeElapsed: Literal["on", "off"] | None = Field(default=None)
    contextTokens: int | None = Field(default=None)
    cliBackends: Optional[dict[str, CliBackendConfig]] = Field(default=None)
    embeddedPi: dict[str, Any] | None = Field(default=None)
    thinkingDefault: Literal["off", "minimal", "low", "medium", "high", "xhigh", "adaptive"] | None = Field(default=None)
    verboseDefault: Literal["off", "on", "full"] | None = Field(default=None)
    elevatedDefault: Literal["off", "on", "ask", "full"] | None = Field(default=None)
    humanDelay: "HumanDelayConfig | dict[str, Any] | None" = Field(default=None)
    timeoutSeconds: int | None = Field(default=None)
    mediaMaxMb: float | None = Field(default=None)
    imageMaxDimensionPx: int | None = Field(default=None)
    typingIntervalSeconds: int | None = Field(default=None)
    typingMode: Literal["never", "instant", "thinking", "message"] | None = Field(default=None)
    heartbeat: HeartbeatConfig | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


class IdentityConfig(BaseModel):
    """Agent identity configuration (aligned with TS)"""
    name: str | None = Field(default=None)
    theme: str | None = Field(default=None)
    emoji: str | None = Field(default=None)
    avatar: str | None = Field(default=None)
    creature: str | None = Field(default=None)
    vibe: str | None = Field(default=None)


class SandboxDockerConfig(BaseModel):
    """Docker-level sandbox configuration (aligned with TS SandboxDockerSettings)."""
    image: str | None = Field(default=None)
    container_prefix: str | None = Field(default=None, alias="containerPrefix")
    workdir: str | None = Field(default=None)
    read_only_root: bool | None = Field(default=None, alias="readOnlyRoot")
    tmpfs: list[str] | None = Field(default=None)
    network: str | None = Field(default=None)
    user: str | None = Field(default=None)
    cap_drop: list[str] | None = Field(default=None, alias="capDrop")
    env: dict[str, str] | None = Field(default=None)
    setup_command: str | None = Field(default=None, alias="setupCommand")
    pids_limit: int | None = Field(default=None, alias="pidsLimit")
    memory: str | int | None = Field(default=None)
    memory_swap: str | int | None = Field(default=None, alias="memorySwap")
    cpus: float | None = Field(default=None)
    ulimits: "dict[str, DockerUlimitConfig] | dict[str, Any] | None" = Field(default=None)
    seccomp_profile: str | None = Field(default=None, alias="seccompProfile")
    apparmor_profile: str | None = Field(default=None, alias="apparmorProfile")
    dns: list[str] | None = Field(default=None)
    extra_hosts: list[str] | None = Field(default=None, alias="extraHosts")
    binds: list[str] = Field(default_factory=list)
    dangerously_allow_reserved_container_targets: bool = Field(
        default=False, alias="dangerouslyAllowReservedContainerTargets"
    )
    dangerously_allow_external_bind_sources: bool = Field(
        default=False, alias="dangerouslyAllowExternalBindSources"
    )
    dangerously_allow_container_namespace_join: bool = Field(
        default=False, alias="dangerouslyAllowContainerNamespaceJoin"
    )

    model_config = {"populate_by_name": True}


class SandboxBrowserConfig(BaseModel):
    """Sandbox browser configuration."""
    autoStart: bool = Field(default=True)
    autoStartTimeoutMs: int = Field(default=10_000)
    allowHostControl: bool = Field(default=False)
    allowedControlUrls: list[str] | None = Field(default=None)
    allowedControlHosts: list[str] | None = Field(default=None)
    allowedControlPorts: list[int] | None = Field(default=None)
    binds: list[str] | None = Field(default=None)


class SandboxConfig(BaseModel):
    """Sandbox configuration.

    Mirrors TS agents.defaults.sandbox / agents.list[].sandbox schema.
    See: openclaw/docs/gateway/sandboxing.md
    
    Default: mode="all" to ensure workspace isolation and match TS behavior.
    Requires Docker. If Docker is unavailable, Gateway will warn and auto-disable.
    Users can explicitly set mode="off" in openclaw.json to disable sandbox.
    """

    # When to sandbox: "off" | "non-main" | "all"
    mode: str = Field(
        default="all",
        description="Sandbox mode: 'all' (recommended, requires Docker), 'non-main', or 'off'"
    )

    # Container scope: "session" | "agent" | "shared"
    scope: str = Field(
        default="session",
        description="Container scope: 'session' (isolated per-session, recommended), 'agent', or 'shared'"
    )

    # Workspace access inside sandbox: "none" | "ro" | "rw"
    # TS default is "none" — matches resolveSandboxConfigForAgent in sandbox/config.ts
    workspaceAccess: str = Field(
        default="none",
        description="Workspace access inside container: 'none' (default), 'ro', or 'rw'"
    )

    # Sandbox workspace root — where per-session/agent sandbox subdirs are created.
    # TS: DEFAULT_SANDBOX_WORKSPACE_ROOT = path.join(STATE_DIR, "sandboxes")
    # Keeping this None means "use ~/.openclaw/sandboxes/" (resolved at runtime).
    workspaceRoot: Optional[str] = Field(
        default=None,
        description="Sandbox workspace root. Defaults to ~/.openclaw/sandboxes/"
    )

    # Docker-level settings (bind mounts etc.)
    docker: SandboxDockerConfig = Field(default_factory=SandboxDockerConfig)

    # Optional browser sandbox
    browser: SandboxBrowserConfig | None = Field(default=None)

    # Legacy field kept for backward compat
    sessionToolsVisibility: str = Field(default="spawned")  # "spawned" | "all"


class SubagentsConfig(BaseModel):
    """Subagents configuration - mirrors TS subagents schema"""
    maxConcurrent: int | None = Field(default=None)
    maxSpawnDepth: int = Field(default=1, ge=1, le=5)
    maxChildrenPerAgent: int = Field(default=5, ge=1, le=20)
    archiveAfterMinutes: int = Field(default=60, ge=1)
    model: str | ModelConfig | None = Field(default=None)
    thinking: str | None = Field(default=None)
    # Fields added in P2 alignment - mirrors TS subagents schema
    runTimeoutSeconds: int | None = Field(default=None)
    announceTimeoutMs: int | None = Field(default=None)
    allowAgents: list[str] | None = Field(default=None)
    
    # Legacy fields for backward compatibility
    enabled: bool = Field(default=True, exclude=True)
    maxDepth: int | None = Field(default=None, exclude=True)
    maxActive: int | None = Field(default=None, exclude=True)

    model_config = ConfigDict(populate_by_name=True)
    
    def model_post_init(self, __context: object) -> None:
        """Migrate legacy fields"""
        if self.maxDepth is not None:
            self.maxSpawnDepth = self.maxDepth
        if self.maxActive is not None:
            self.maxChildrenPerAgent = self.maxActive


class GroupChatConfig(BaseModel):
    """Group chat configuration (aligned with TS GroupChatSchema)"""
    mentionPatterns: list[str] | None = Field(default=None)
    historyLimit: int | None = Field(default=None, ge=0)


class AgentEntry(BaseModel):
    """Individual agent configuration - mirrors TS AgentEntrySchema"""

    id: str
    default: bool = Field(default=False)
    name: str | None = Field(default=None)
    workspace: str | None = Field(default=None)
    agentDir: str | None = Field(default=None)
    model: str | ModelConfig | None = Field(default=None)
    tools: ToolsConfig | None = Field(default=None)
    identity: IdentityConfig | None = Field(default=None)
    sandbox: SandboxConfig | None = Field(default=None)
    subagents: SubagentsConfig | None = Field(default=None)
    groupChat: GroupChatConfig | None = Field(default=None)
    # Fields added in P2 alignment - mirrors TS AgentEntrySchema
    skills: list[str] | None = Field(default=None)
    memorySearch: "MemorySearchConfig | None" = Field(default=None)
    humanDelay: "HumanDelayConfig | dict[str, Any] | None" = Field(default=None)
    heartbeat: HeartbeatConfig | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


class AgentsConfig(BaseModel):
    """Agents configuration (aligned with TS)"""

    model_config = ConfigDict(populate_by_name=True)

    defaults: AgentDefaults = Field(default_factory=AgentDefaults)
    # Use alias so JSON key "list" maps to agents_list (avoids shadowing builtin `list`)
    agents_list: builtins.list["AgentEntry"] | None = Field(default_factory=builtins.list, alias="list")

    # Support legacy "agents" field for backward compatibility
    agents: builtins.list["AgentEntry"] | None = Field(default=None, exclude=True)

    @property
    def list(self) -> builtins.list["AgentEntry"]:  # type: ignore[override]
        """Access agent list (TS compatibility: agents.list)"""
        return self.agents_list or []

    def model_post_init(self, __context: object) -> None:
        """Migrate legacy 'agents' field to agents_list"""
        if self.agents and not self.agents_list:
            self.agents_list = self.agents


class TelegramRetryConfig(BaseModel):
    """Telegram retry configuration"""
    attempts: int = Field(default=3, ge=1)
    minDelayMs: int = Field(default=1000, ge=0, alias="min_delay_ms")
    maxDelayMs: int = Field(default=30000, ge=0, alias="max_delay_ms")
    jitter: bool = Field(default=True)
    
    model_config = {"populate_by_name": True}


class TelegramNetworkConfig(BaseModel):
    """Telegram network configuration"""
    autoSelectFamily: bool | None = Field(default=None, alias="auto_select_family")
    dnsResultOrder: str | None = Field(default=None, alias="dns_result_order")
    
    model_config = {"populate_by_name": True}


class TelegramActionConfig(BaseModel):
    """Telegram action configuration"""
    reactions: bool = Field(default=True)
    sendMessage: bool = Field(default=True, alias="send_message")
    deleteMessage: bool = Field(default=True, alias="delete_message")
    editMessage: bool = Field(default=True, alias="edit_message")
    sticker: bool = Field(default=False)
    createForumTopic: bool = Field(default=False, alias="create_forum_topic")
    
    model_config = {"populate_by_name": True}


class TelegramDraftChunkConfig(BaseModel):
    """Draft chunk configuration for block streaming"""
    minChars: int = Field(default=100, ge=0, alias="min_chars")
    maxChars: int = Field(default=2000, ge=0, alias="max_chars")
    
    model_config = {"populate_by_name": True}


class TelegramTopicConfig(BaseModel):
    """Telegram forum topic configuration"""
    requireMention: bool | None = Field(default=None, alias="require_mention")
    groupPolicy: str | None = Field(default=None, alias="group_policy")
    skills: list[str] | None = Field(default=None)
    enabled: bool | None = Field(default=None)
    allowFrom: list[str | int] | None = Field(default=None, alias="allow_from")
    systemPrompt: str | None = Field(default=None, alias="system_prompt")
    
    model_config = {"populate_by_name": True}


class TelegramGroupConfig(BaseModel):
    """Telegram group configuration"""
    requireMention: bool | None = Field(default=None, alias="require_mention")
    groupPolicy: str | None = Field(default=None, alias="group_policy")
    skills: list[str] | None = Field(default=None)
    topics: dict[str, TelegramTopicConfig] | None = Field(default=None)
    enabled: bool | None = Field(default=None)
    allowFrom: list[str | int] | None = Field(default=None, alias="allow_from")
    systemPrompt: str | None = Field(default=None, alias="system_prompt")
    
    model_config = {"populate_by_name": True}


class TelegramDmConfig(BaseModel):
    """Per-DM configuration"""
    historyLimit: int | None = Field(default=None, alias="history_limit")
    
    model_config = {"populate_by_name": True}


class TelegramCapabilitiesConfig(BaseModel):
    """
    Telegram capabilities configuration (aligned with TS TelegramCapabilitiesConfig).
    Controls which features the bot exposes per chat type.
    """
    inline_buttons: str | None = Field(
        default=None,
        alias="inlineButtons",
        description=(
            "Inline button scope: off | dm | group | all | allowlist (default). "
            "Mirrors TS TelegramInlineButtonsScope."
        ),
    )

    model_config = {"populate_by_name": True}


class TelegramChannelConfig(BaseModel):
    """Telegram channel configuration"""

    model_config = {"populate_by_name": True}

    enabled: bool = Field(default=True)
    botToken: str | None = Field(default=None, alias="bot_token")
    tokenFile: str | None = Field(default=None, alias="token_file")
    allowFrom: list[str | int] | None = Field(default=None, alias="allow_from")
    groupAllowFrom: list[str | int] | None = Field(default=None, alias="group_allow_from")
    dmPolicy: str | None = Field(default=None, alias="dm_policy")
    groupPolicy: str | None = Field(default=None, alias="group_policy")
    replyToMode: str | None = Field(default=None, alias="reply_to_mode")
    
    # Streaming and chunking - mirrors TS streaming: "off" | "partial" | "block" | "progress"
    streaming: Literal["off", "partial", "block", "progress"] | None = Field(default=None)
    streamMode: Literal["off", "partial", "block", "progress"] | None = Field(
        default=None, 
        alias="stream_mode",
        description="Legacy alias for 'streaming' field"
    )
    draftChunk: TelegramDraftChunkConfig | None = Field(default=None, alias="draft_chunk")
    textChunkLimit: int | None = Field(default=None, alias="text_chunk_limit")
    chunkMode: str | None = Field(default=None, alias="chunk_mode")
    blockStreaming: bool | None = Field(default=None, alias="block_streaming")
    blockStreamingCoalesce: "BlockStreamingCoalesceConfig | dict[str, Any] | None" = Field(default=None, alias="block_streaming_coalesce")
    
    # Reactions
    reactionNotifications: str | None = Field(default=None, alias="reaction_notifications")
    reactionLevel: str | None = Field(default=None, alias="reaction_level")
    ackReaction: str | None = Field(default=None, alias="ack_reaction")
    
    # History
    historyLimit: int | None = Field(default=None, alias="history_limit")
    dmHistoryLimit: int | None = Field(default=None, alias="dm_history_limit")
    dms: dict[str, TelegramDmConfig] | None = Field(default=None)
    
    # Groups and topics
    groups: dict[str, TelegramGroupConfig] | None = Field(default=None)
    
    # Webhook
    webhookUrl: str | None = Field(default=None, alias="webhook_url")
    webhookSecret: str | None = Field(default=None, alias="webhook_secret")
    webhookPath: str | None = Field(default=None, alias="webhook_path")
    webhookHost: str | None = Field(default=None, alias="webhook_host")
    
    # Network and retry
    retry: TelegramRetryConfig | None = Field(default=None)
    network: TelegramNetworkConfig | None = Field(default=None)
    proxy: str | None = Field(default=None)
    timeoutSeconds: int | None = Field(default=None, alias="timeout_seconds")
    
    # Actions
    actions: TelegramActionConfig | None = Field(default=None)
    
    # Media
    mediaMaxMb: int | None = Field(default=None, alias="media_max_mb")
    
    # Misc
    capabilities: TelegramCapabilitiesConfig | Any | None = Field(default=None)
    linkPreview: bool | None = Field(default=None, alias="link_preview")
    responsePrefix: str | None = Field(default=None, alias="response_prefix")
    configWrites: bool | None = Field(default=None, alias="config_writes")
    
    # Multi-account
    accounts: dict[str, Any] | None = Field(default=None)


class ChannelConfig(BaseModel):
    """Individual channel configuration (generic)"""

    enabled: bool = Field(default=True)
    botToken: str | None = Field(default=None, alias="bot_token")
    allowFrom: list[str] | None = Field(default=None, alias="allow_from")
    dmPolicy: str | None = Field(default=None, alias="dm_policy")

    # Additional platform-specific fields
    token: str | None = Field(default=None)
    signingSecret: str | None = Field(default=None, alias="signing_secret")
    appId: str | None = Field(default=None, alias="app_id")
    appSecret: str | None = Field(default=None, alias="app_secret")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class FeishuChannelConfig(BaseModel):
    """Feishu / Lark channel configuration (mirrors TS FeishuConfigSchema)"""

    enabled: bool = Field(default=True)
    appId: str | None = Field(default=None)
    appSecret: str | None = Field(default=None)
    useWebSocket: bool = Field(default=True)
    dmPolicy: str = Field(default="pairing")
    webhookPath: str | None = Field(default=None)
    allowFrom: list[str] | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class SlackAccountConfig(BaseModel):
    """Slack account configuration - mirrors TS SlackAccountConfig"""
    
    # Basic
    name: str | None = Field(default=None)
    enabled: bool | None = Field(default=None)
    
    # Connection mode (P1-4: HTTP mode support)
    mode: Literal["socket", "http"] | None = Field(default=None)
    signing_secret: str | None = Field(default=None, alias="signingSecret")
    webhook_path: str | None = Field(default=None, alias="webhookPath")
    
    # Tokens
    bot_token: str | None = Field(default=None, alias="botToken")
    app_token: str | None = Field(default=None, alias="appToken")
    user_token: str | None = Field(default=None, alias="userToken")
    user_token_read_only: bool | None = Field(default=None, alias="userTokenReadOnly")
    
    # Streaming (P1-5: block_streaming support)
    block_streaming: bool | None = Field(default=None, alias="blockStreaming")
    block_streaming_coalesce: "BlockStreamingCoalesceConfig | None" = Field(
        default=None, 
        alias="blockStreamingCoalesce"
    )
    streaming: Literal["off", "partial", "block", "progress"] | bool | None = Field(default=None)
    native_streaming: bool | None = Field(default=None, alias="nativeStreaming")
    
    # Text/media
    text_chunk_limit: int | None = Field(default=None, alias="textChunkLimit")
    chunk_mode: Literal["length", "newline"] | None = Field(default=None, alias="chunkMode")
    media_max_mb: int | None = Field(default=None, alias="mediaMaxMb")
    
    # Behavior
    allow_bots: bool | None = Field(default=None, alias="allowBots")
    require_mention: bool | None = Field(default=None, alias="requireMention")
    history_limit: int | None = Field(default=None, alias="historyLimit")
    dm_history_limit: int | None = Field(default=None, alias="dmHistoryLimit")
    
    # Other fields
    capabilities: list[str] | None = Field(default=None)
    config_writes: bool | None = Field(default=None, alias="configWrites")
    reply_to_mode: str | None = Field(default=None, alias="replyToMode")
    reaction_notifications: str | None = Field(default=None, alias="reactionNotifications")
    
    model_config = ConfigDict(populate_by_name=True)


class SlackConfig(BaseModel):
    """Slack configuration - mirrors TS SlackConfig"""
    
    # Base config (inherited by accounts)
    mode: Literal["socket", "http"] | None = Field(default=None)
    signing_secret: str | None = Field(default=None, alias="signingSecret")
    webhook_path: str | None = Field(default="/slack/events", alias="webhookPath")
    bot_token: str | None = Field(default=None, alias="botToken")
    app_token: str | None = Field(default=None, alias="appToken")
    
    # Block streaming defaults
    block_streaming: bool | None = Field(default=None, alias="blockStreaming")
    block_streaming_coalesce: "BlockStreamingCoalesceConfig | None" = Field(
        default=None,
        alias="blockStreamingCoalesce"
    )
    
    # Accounts
    accounts: dict[str, SlackAccountConfig] | None = Field(default=None)
    default_account: str | None = Field(default=None, alias="defaultAccount")
    
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class ChannelsConfig(BaseModel):
    """Channels configuration (mirrors TS ChannelsSchema with .passthrough())"""

    telegram: TelegramChannelConfig | None = Field(default=None)
    whatsapp: ChannelConfig | None = Field(default=None)
    discord: ChannelConfig | None = Field(default=None)
    slack: SlackConfig | None = Field(default=None)
    feishu: FeishuChannelConfig | None = Field(default=None)

    # Allow extension channels (matrix, zalo, msteams, etc.) — matches TS .passthrough()
    model_config = ConfigDict(extra="allow")


class SkillsConfig(BaseModel):
    """Skills configuration (aligned with TS skills block)"""

    allowBundled: list[str] | None = Field(default=None)
    enable: list[str] | None = Field(default=None)
    disable: list[str] | None = Field(default=None)
    entries: dict[str, Any] | None = Field(default=None)  # skills.entries.<skillKey>.apiKey, etc.
    install: dict[str, Any] | None = Field(default=None)  # nodeManager, etc.

    model_config = ConfigDict(extra="allow")


class PluginEntryConfig(BaseModel):
    """Individual plugin entry configuration (mirrors TS plugins.entries[name])"""
    enabled: bool = Field(default=True)


class PluginsConfig(BaseModel):
    """Plugins configuration - mirrors TS plugins schema"""

    enabled: bool | None = Field(default=None)  # top-level toggle (added P2 alignment)
    entries: dict[str, PluginEntryConfig] | None = Field(default=None)
    # TS uses allow/deny; Python previously used enable/disable (P2 alignment fix)
    allow: list[str] | None = Field(default=None)     # TS field name
    deny: list[str] | None = Field(default=None)      # TS field name
    load: dict[str, Any] | None = Field(default=None)
    slots: dict[str, Any] | None = Field(default=None)
    installs: list[Any] | None = Field(default=None)
    # Legacy Python-specific fields kept for backward compat
    enable: list[str] | None = Field(default=None, exclude=True)
    disable: list[str] | None = Field(default=None, exclude=True)

    model_config = ConfigDict(populate_by_name=True)

    def model_post_init(self, __context: object) -> None:
        """Migrate legacy enable/disable to allow/deny"""
        if self.enable is not None and self.allow is None:
            self.allow = self.enable
        if self.disable is not None and self.deny is None:
            self.deny = self.disable


class MetaConfig(BaseModel):
    """Metadata configuration"""
    last_touched_version: str | None = Field(default=None, alias="lastTouchedVersion")
    last_touched_at: str | None = Field(default=None, alias="lastTouchedAt")


class MessagesConfig(BaseModel):
    """Messages configuration - mirrors TS MessagesConfig
    
    Controls message handling, acknowledgments, queueing, and text-to-speech.
    """
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    
    message_prefix: str | None = Field(default=None, alias="messagePrefix")
    """Prefix to prepend to user messages"""
    
    response_prefix: str | Literal["auto"] | None = Field(default=None, alias="responsePrefix")
    """Prefix for assistant responses. 'auto' uses agent identity."""
    
    group_chat: dict[str, Any] | None = Field(default=None, alias="groupChat")
    """Group chat behavior configuration"""
    
    queue: dict[str, Any] | None = Field(default=None)
    """Message queue configuration per channel"""
    
    inbound: dict[str, Any] | None = Field(default=None)
    """Inbound message debouncing configuration"""
    
    ack_reaction: str | None = Field(default=None, alias="ackReaction")
    """Emoji/reaction for message acknowledgment. Default: '👀'"""
    
    ack_reaction_scope: str | None = Field(default="group-mentions", alias="ackReactionScope")
    """When to add ack reaction: 'group-mentions', 'group-all', 'direct', 'all', 'off', 'none'"""
    
    remove_ack_after_reply: bool | None = Field(default=None, alias="removeAckAfterReply")
    """Whether to remove ack reaction after sending reply"""
    
    status_reactions: dict[str, Any] | None = Field(default=None, alias="statusReactions")
    """Status reactions configuration (e.g., for thinking, errors)"""
    
    suppress_tool_errors: bool | None = Field(default=None, alias="suppressToolErrors")
    """Whether to suppress tool error messages"""
    
    tts: dict[str, Any] | None = Field(default=None)
    """Text-to-speech configuration"""


class CommandsConfig(BaseModel):
    """Commands configuration (TS alignment)"""
    native: str | None = Field(default="auto")
    native_skills: str | None = Field(default="auto", alias="nativeSkills")


class WizardConfig(BaseModel):
    """Wizard run tracking"""
    last_run_at: str | None = Field(default=None, alias="lastRunAt")
    last_run_version: str | None = Field(default=None, alias="lastRunVersion")
    last_run_commit: str | None = Field(default=None, alias="lastRunCommit")
    last_run_command: str | None = Field(default=None, alias="lastRunCommand")
    last_run_mode: str | None = Field(default=None, alias="lastRunMode")


class LoggingConfig(BaseModel):
    """Logging configuration - mirrors TS LoggingConfig from src/config/types.base.ts"""
    model_config = ConfigDict(populate_by_name=True)
    
    level: str | None = Field(default="INFO")
    """Root logger level. Default: INFO"""
    
    format: str | None = Field(default="colored")
    """Python-specific format field (maps to consoleStyle in TS)"""
    
    # New fields (aligned with TS)
    file: str | None = Field(default=None)
    """Path to log file. If set, logs will be written to this file."""
    
    max_file_bytes: int | str | None = Field(default=None, alias="maxFileBytes")
    """Maximum log file size before rotation. Supports "10mb", "1gb" format or integer bytes."""
    
    console_level: str | None = Field(default=None, alias="consoleLevel")
    """Console-specific log level (can differ from file level)."""
    
    console_style: str | None = Field(default=None, alias="consoleStyle")
    """Console output style: 'pretty' | 'compact' | 'json'. Default: pretty"""
    
    redact_sensitive: bool | None = Field(default=None, alias="redactSensitive")
    """Whether to automatically redact sensitive information (API keys, tokens, etc.). Default: true"""
    
    redact_patterns: list[str] | None = Field(default=None, alias="redactPatterns")
    """Custom regex patterns for redacting sensitive data."""


class UpdateAutoConfig(BaseModel):
    """Auto update configuration - mirrors TS update.auto
    
    Configures automatic update behavior for different release channels.
    """
    enabled: bool | None = Field(default=None)
    """Whether auto-update is enabled. Default: false"""
    
    stable_delay_hours: int | None = Field(default=None, alias="stableDelayHours")
    """Hours to wait before auto-updating to stable releases. Default: 6"""
    
    stable_jitter_hours: int | None = Field(default=None, alias="stableJitterHours")
    """Random jitter hours to add to stable delay. Default: 12"""
    
    beta_check_interval_hours: int | None = Field(default=None, alias="betaCheckIntervalHours")
    """Hours between beta update checks. Default: 1"""
    
    model_config = ConfigDict(populate_by_name=True)


class UpdateConfig(BaseModel):
    """Update configuration - mirrors TS UpdateConfig
    
    Controls update checking and automatic update behavior.
    """
    channel: str = Field(default="stable")
    """Update channel: 'stable', 'beta', or 'dev'"""
    
    check_on_start: bool = Field(default=False, alias="checkOnStart")
    """Whether to check for updates on startup. Default: false"""
    
    auto: UpdateAutoConfig | None = Field(default=None)
    """Automatic update configuration"""
    
    model_config = ConfigDict(populate_by_name=True)


class UIAssistantConfig(BaseModel):
    """UI assistant identity configuration - mirrors TS ui.assistant
    
    Configures the display name and avatar for the assistant in the Control UI.
    """
    name: str | None = Field(default=None)
    """Assistant display name. Default: 'Assistant'"""
    
    avatar: str | None = Field(default=None)
    """Assistant avatar (emoji, short text, or image URL/data URI). Default: 'A'"""


class UIConfig(BaseModel):
    """UI configuration - mirrors TS ui schema
    
    Controls visual and identity aspects of the Control UI.
    """
    seam_color: str | None = Field(default=None, alias="seamColor")
    """Hex color for UI seam/accent elements"""
    
    assistant: UIAssistantConfig | None = Field(default=None)
    """Assistant identity configuration"""
    
    model_config = ConfigDict(populate_by_name=True)


class ModelsConfig(BaseModel):
    """Models configuration"""
    providers: dict[str, Any] | None = Field(default=None)


class MemorySearchSyncSessionsConfig(BaseModel):
    """Memory search sync sessions configuration - mirrors TS memorySearch.sync.sessions"""
    deltaBytes: int = Field(default=100_000)
    deltaMessages: int = Field(default=50)


class MemorySearchSyncConfig(BaseModel):
    """Memory search sync configuration - mirrors TS memorySearch.sync"""
    onSessionStart: bool = Field(default=True)
    onSearch: bool = Field(default=True)
    watch: bool = Field(default=True)
    watchDebounceMs: int = Field(default=1500)
    intervalMinutes: int = Field(default=0)
    sessions: MemorySearchSyncSessionsConfig = Field(default_factory=MemorySearchSyncSessionsConfig)


class MemorySearchQueryHybridMMRConfig(BaseModel):
    """Memory search query hybrid MMR configuration - mirrors TS memorySearch.query.hybrid.mmr"""
    enabled: bool = Field(default=False)
    lambda_: float = Field(default=0.7, alias="lambda")
    
    model_config = ConfigDict(populate_by_name=True)


class MemorySearchQueryHybridTemporalDecayConfig(BaseModel):
    """Memory search query hybrid temporal decay configuration - mirrors TS memorySearch.query.hybrid.temporalDecay"""
    enabled: bool = Field(default=False)
    halfLifeDays: int = Field(default=30)


class MemorySearchQueryHybridConfig(BaseModel):
    """Memory search query hybrid configuration - mirrors TS memorySearch.query.hybrid"""
    enabled: bool = Field(default=True)
    vectorWeight: float = Field(default=0.7)
    textWeight: float = Field(default=0.3)
    candidateMultiplier: int = Field(default=4)
    mmr: MemorySearchQueryHybridMMRConfig = Field(default_factory=MemorySearchQueryHybridMMRConfig)
    temporalDecay: MemorySearchQueryHybridTemporalDecayConfig = Field(default_factory=MemorySearchQueryHybridTemporalDecayConfig)


class MemorySearchQueryConfig(BaseModel):
    """Memory search query configuration - mirrors TS memorySearch.query"""
    maxResults: int = Field(default=6)
    minScore: float = Field(default=0.35)
    hybrid: MemorySearchQueryHybridConfig = Field(default_factory=MemorySearchQueryHybridConfig)


class MemorySearchStoreVectorConfig(BaseModel):
    """Memory search store vector configuration - mirrors TS memorySearch.store.vector"""
    enabled: bool = Field(default=True)
    extensionPath: str | None = Field(default=None)


class MemorySearchStoreConfig(BaseModel):
    """Memory search store configuration - mirrors TS memorySearch.store"""
    driver: Literal["sqlite"] = Field(default="sqlite")
    path: str | None = Field(default=None)
    vector: MemorySearchStoreVectorConfig = Field(default_factory=MemorySearchStoreVectorConfig)


class MemorySearchChunkingConfig(BaseModel):
    """Memory search chunking configuration - mirrors TS memorySearch.chunking"""
    tokens: int = Field(default=400)
    overlap: int = Field(default=80)


class MemorySearchCacheConfig(BaseModel):
    """Memory search cache configuration - mirrors TS memorySearch.cache"""
    enabled: bool = Field(default=True)
    maxEntries: int | None = Field(default=None)


class MemorySearchRemoteBatchConfig(BaseModel):
    """Memory search remote batch configuration - mirrors TS memorySearch.remote.batch"""
    enabled: bool = Field(default=False)
    wait: bool = Field(default=True)
    concurrency: int = Field(default=2)
    pollIntervalMs: int = Field(default=2000)
    timeoutMinutes: int = Field(default=60)


class MemorySearchRemoteConfig(BaseModel):
    """Memory search remote configuration - mirrors TS memorySearch.remote"""
    baseUrl: str | None = Field(default=None)
    apiKey: str | None = Field(default=None)
    headers: dict[str, str] | None = Field(default=None)
    batch: MemorySearchRemoteBatchConfig | None = Field(default=None)


class MemorySearchExperimentalConfig(BaseModel):
    """Memory search experimental configuration - mirrors TS memorySearch.experimental"""
    sessionMemory: bool = Field(default=False)


class MemorySearchConfig(BaseModel):
    """Memory search configuration - mirrors TS memorySearch (src/agents/memory-search.ts)"""
    enabled: bool = Field(default=True)
    sources: list[Literal["memory", "sessions"]] = Field(default=["memory"])
    extraPaths: list[str] = Field(default_factory=list)
    provider: Literal["openai", "local", "gemini", "voyage", "mistral", "ollama", "auto"] = Field(default="auto")
    remote: MemorySearchRemoteConfig | None = Field(default=None)
    experimental: MemorySearchExperimentalConfig = Field(default_factory=MemorySearchExperimentalConfig)
    fallback: Literal["openai", "gemini", "local", "voyage", "mistral", "ollama", "none"] = Field(default="none")
    model: str = Field(default="")
    local: dict[str, Any] | None = Field(default=None)
    store: MemorySearchStoreConfig = Field(default_factory=MemorySearchStoreConfig)
    sync: MemorySearchSyncConfig = Field(default_factory=MemorySearchSyncConfig)
    query: MemorySearchQueryConfig = Field(default_factory=MemorySearchQueryConfig)
    chunking: MemorySearchChunkingConfig = Field(default_factory=MemorySearchChunkingConfig)
    cache: MemorySearchCacheConfig = Field(default_factory=MemorySearchCacheConfig)


class MemoryFlushConfig(BaseModel):
    """Memory flush configuration - mirrors TS memoryFlush (src/auto-reply/reply/memory-flush.ts)"""
    enabled: bool = Field(default=True)
    softThresholdTokens: int = Field(default=4000)
    forceFlushTranscriptBytes: int | None = Field(default=2 * 1024 * 1024)  # 2MB
    prompt: str | None = Field(default=None)
    systemPrompt: str | None = Field(default=None)


class MemoryConfig(BaseModel):
    """Memory configuration"""
    enabled: bool = Field(default=True)
    provider: str = Field(default="simple")
    # Legacy fields for backward compatibility
    # Full configuration should use agents.defaults.memorySearch and agents.defaults.memoryFlush


class CronRetryConfig(BaseModel):
    """Cron retry configuration - mirrors TS CronRetryConfig
    
    Controls retry behavior for one-time (at-scheduled) cron jobs that fail with transient errors.
    """
    max_attempts: int | None = Field(default=None, alias="maxAttempts")
    """Maximum number of retry attempts for transient errors. Default: 3"""
    
    backoff_ms: list[int] | None = Field(default=None, alias="backoffMs")
    """Backoff schedule in milliseconds for each retry. Default: [30000, 60000, 300000]"""
    
    retry_on: list[Literal["rate_limit", "network", "timeout", "server_error"]] | None = Field(
        default=None, alias="retryOn"
    )
    """Error types to retry. If not specified, retries all transient errors."""
    
    model_config = ConfigDict(populate_by_name=True)


class CronRunLogConfig(BaseModel):
    """Cron run log configuration - mirrors TS runLog
    
    Controls pruning behavior for cron job execution logs.
    """
    max_bytes: int | str | None = Field(default=None, alias="maxBytes")
    """Maximum log file size. Supports numeric (bytes) or string ('2mb'). Default: 2000000"""
    
    keep_lines: int | None = Field(default=None, alias="keepLines")
    """Number of lines to keep when pruning. Default: 2000"""
    
    model_config = ConfigDict(populate_by_name=True)


class CronFailureAlertConfig(BaseModel):
    """Cron failure alert configuration - mirrors TS CronFailureAlertConfig
    
    Configures when and how to send failure alerts for cron jobs.
    """
    enabled: bool | None = Field(default=None)
    """Whether to enable failure alerts"""
    
    after: int | None = Field(default=None)
    """Number of consecutive failures before alerting. Default: 2"""
    
    cooldown_ms: int | None = Field(default=None, alias="cooldownMs")
    """Cooldown period between alerts in milliseconds. Default: 3600000 (1 hour)"""
    
    mode: Literal["announce", "webhook"] | None = Field(default=None)
    """Alert delivery mode: 'announce' sends to agent session, 'webhook' posts to URL"""
    
    account_id: str | None = Field(default=None, alias="accountId")
    """Account ID for multi-account scenarios"""
    
    model_config = ConfigDict(populate_by_name=True)


class CronFailureDestinationConfig(BaseModel):
    """Cron failure destination configuration - mirrors TS CronFailureDestinationConfig
    
    Default destination for failure notifications across all cron jobs.
    Can be overridden per-job via delivery.failureDestination.
    """
    channel: str | None = Field(default=None)
    """Channel for failure notifications (e.g., 'telegram')"""
    
    to: str | None = Field(default=None)
    """Destination identifier (webhook URL for webhook mode, peer ID for announce mode)"""
    
    account_id: str | None = Field(default=None, alias="accountId")
    """Account ID for the destination channel"""
    
    mode: Literal["announce", "webhook"] | None = Field(default=None)
    """Delivery mode for failure notifications"""
    
    model_config = ConfigDict(populate_by_name=True)


class CronConfig(BaseModel):
    """Cron configuration - mirrors TS CronConfig
    
    Top-level configuration for the cron service, controlling job storage,
    execution behavior, retry policies, and failure handling.
    """
    enabled: bool = Field(default=True)
    """Whether the cron service is enabled. Default: True"""
    
    store: str | None = Field(default=None)
    """Path to cron jobs storage file. Default: ~/.openclaw/cron/jobs.json"""
    
    max_concurrent_runs: int | None = Field(default=None, alias="maxConcurrentRuns")
    """Maximum number of concurrent cron job executions. Default: 1"""
    
    retry: CronRetryConfig | None = Field(default=None)
    """Retry configuration for one-time jobs with transient failures"""
    
    webhook: str | None = Field(default=None)
    """(Deprecated) Legacy webhook URL for jobs with notify=true"""
    
    webhook_token: str | None = Field(default=None, alias="webhookToken")
    """Bearer token for webhook POST requests"""
    
    session_retention: str | Literal[False] | None = Field(default=None, alias="sessionRetention")
    """How long to retain completed cron sessions. Default: '24h'. Set to false to disable cleanup."""
    
    run_log: CronRunLogConfig | None = Field(default=None, alias="runLog")
    """Configuration for cron job execution log pruning"""
    
    failure_alert: CronFailureAlertConfig | None = Field(default=None, alias="failureAlert")
    """Global failure alert configuration"""
    
    failure_destination: CronFailureDestinationConfig | None = Field(
        default=None, alias="failureDestination"
    )
    """Default destination for failure notifications"""
    
    model_config = ConfigDict(populate_by_name=True)


class InternalHooksConfig(BaseModel):
    """Internal hooks configuration"""
    enabled: bool = Field(default=True)
    handlers: dict[str, Any] | None = Field(default=None)
    entries: dict[str, Any] | None = Field(default=None)
    load: dict[str, Any] | None = Field(default=None)
    installs: dict[str, Any] | None = Field(default=None)


class HooksConfig(BaseModel):
    """Hooks configuration — TS does not write top-level enabled; use None to omit it"""
    enabled: bool | None = Field(default=None)
    internal: InternalHooksConfig | None = Field(default=None)


class ShellEnvConfig(BaseModel):
    """Shell env import configuration - mirrors TS env.shellEnv
    
    Controls whether and how to import environment variables from the user's shell.
    """
    enabled: bool = Field(default=False)
    """Whether to import shell environment variables. Default: false"""
    
    timeout_ms: int | None = Field(default=None, alias="timeoutMs")
    """Timeout in milliseconds for shell environment loading. Default: 15000"""
    
    model_config = ConfigDict(populate_by_name=True)


class EnvConfig(BaseModel):
    """Environment variable configuration block in openclaw.json.

    Mirrors TS zod-schema.ts env block:
      env:
        GOOGLE_API_KEY: "AIza..."
        ANTHROPIC_API_KEY: "sk-ant-..."
        vars: { FOO: "bar" }
        shellEnv: { enabled: true }

    Any top-level key that is not a known sub-field is treated as a raw env var,
    applied to the process environment at startup (override: false — already-set
    vars from .env files or the shell take precedence).
    """
    vars: dict[str, str] | None = Field(default=None)
    shell_env: ShellEnvConfig | None = Field(default=None, alias="shellEnv")

    model_config = {"populate_by_name": True, "extra": "allow"}

    def get_all_vars(self) -> dict[str, str]:
        """Return all env var overrides as a flat dict.
        Merges ``vars`` dict and any extra top-level string fields.
        """
        result: dict[str, str] = {}
        if self.vars:
            result.update({k: v for k, v in self.vars.items() if v})
        for key, value in (self.model_extra or {}).items():
            if isinstance(value, str) and value.strip():
                result[key] = value
        return result


class SessionMaintenanceConfig(BaseModel):
    """Session maintenance configuration — mirrors TS session.maintenance schema."""
    model_config = ConfigDict(populate_by_name=True)

    mode: Literal["warn", "enforce"] | None = Field(
        default=None,
        description="Whether to enforce maintenance or warn only. Default: 'warn'"
    )
    pruneAfter: str | int | None = Field(
        default=None,
        description="Remove session entries older than this duration (e.g. '30d', '12h'). Default: '30d'"
    )
    pruneDays: int | None = Field(
        default=None,
        description="Deprecated. Use pruneAfter instead."
    )
    maxEntries: int | None = Field(
        default=None,
        description="Maximum number of session entries to keep. Default: 500"
    )
    rotateBytes: str | int | None = Field(
        default=None,
        description="Rotate sessions.json when it exceeds this size (e.g. '10mb'). Default: 10mb"
    )
    maxDiskBytes: str | int | None = Field(
        default=None,
        description="Optional per-agent sessions-directory disk budget (e.g. '500mb')"
    )
    highWaterBytes: str | int | None = Field(
        default=None,
        description="Target size after disk-budget cleanup (high-water mark). Default: 80% of maxDiskBytes"
    )
    resetArchiveRetention: str | int | Literal[False] | None = Field(
        default=None,
        description="Retention for archived reset transcripts. Set false to disable. Default: same as pruneAfter"
    )


class SessionConfig(BaseModel):
    """Session configuration — mirrors TS zod-schema.session.ts.

    Replaces the previous ``dict[str, Any]`` field so that ``dmScope``,
    ``identityLinks``, and ``maintenance`` are accessible as typed attributes
    in ``resolve_agent_route()`` and elsewhere.
    """
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    dmScope: Literal["main", "per-peer", "per-channel-peer", "per-account-channel-peer"] = Field(
        default="per-channel-peer"
    )
    identityLinks: dict[str, list[str]] | None = Field(default=None)
    maintenance: SessionMaintenanceConfig = Field(default_factory=SessionMaintenanceConfig)
    reset: str | None = Field(default=None)
    idleMinutes: int | None = Field(default=None)
    resetByType: dict[str, Any] | None = Field(default=None)
    resetByChannel: dict[str, Any] | None = Field(default=None)
    store: str | None = Field(default=None)
    # Fields added in P2 alignment - mirrors TS SessionSchema
    scope: Literal["per-sender", "global"] | None = Field(default=None)
    resetTriggers: list[str] | None = Field(default=None)
    parentForkMaxTokens: int | None = Field(default=None)
    mainKey: str | None = Field(default=None)
    sendPolicy: dict[str, Any] | None = Field(default=None)
    threadBindings: dict[str, Any] | None = Field(default=None)


class AcpStreamConfig(BaseModel):
    """ACP stream configuration - mirrors TS AcpStreamSchema"""
    coalesceIdleMs: int | None = Field(default=None)
    maxChunkChars: int | None = Field(default=None)
    repeatSuppression: bool | None = Field(default=None)
    deliveryMode: Literal["live", "final_only"] | None = Field(default=None)
    hiddenBoundarySeparator: Literal["none", "space", "newline", "paragraph"] | None = Field(default=None)
    maxOutputChars: int | None = Field(default=None)
    maxSessionUpdateChars: int | None = Field(default=None)
    tagVisibility: dict[str, bool] | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


class AcpRuntimeConfig(BaseModel):
    """ACP runtime configuration - mirrors TS AcpRuntimeSchema"""
    ttlMinutes: int | None = Field(default=None)
    installCommand: str | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


class AcpConfig(BaseModel):
    """ACP (Agent Communication Protocol) configuration - mirrors TS AcpSchema"""
    enabled: bool | None = Field(default=None)
    dispatch: dict[str, Any] | None = Field(default=None)
    backend: str | None = Field(default=None)
    defaultAgent: str | None = Field(default=None)
    allowedAgents: list[str] | None = Field(default=None)
    maxConcurrentSessions: int | None = Field(default=None)
    stream: AcpStreamConfig | None = Field(default=None)
    runtime: AcpRuntimeConfig | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class MediaTopLevelConfig(BaseModel):
    """Top-level media configuration - mirrors TS media schema"""
    preserveFilenames: bool | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


class CliBannerConfig(BaseModel):
    """CLI banner configuration"""
    taglineMode: Literal["random", "default", "off"] | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


class CliConfig(BaseModel):
    """CLI configuration - mirrors TS cli schema"""
    banner: CliBannerConfig | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


# ─── Browser config ──────────────────────────────────────────────────────────

class BrowserProfileConfig(BaseModel):
    """Browser profile configuration - mirrors TS BrowserProfileConfig"""
    cdp_port: int | None = Field(default=None, alias="cdpPort")
    cdp_url: str | None = Field(default=None, alias="cdpUrl")
    driver: Literal["openclaw", "extension"] | None = Field(default=None)
    attach_only: bool | None = Field(default=None, alias="attachOnly")
    color: str | None = Field(default=None)
    model_config = ConfigDict(populate_by_name=True)


class BrowserSnapshotDefaults(BaseModel):
    mode: Literal["efficient"] | None = Field(default=None)


class BrowserSsrfPolicyConfig(BaseModel):
    allow_private_network: bool | None = Field(default=None, alias="allowPrivateNetwork")
    dangerously_allow_private_network: bool | None = Field(default=None, alias="dangerouslyAllowPrivateNetwork")
    allowed_hostnames: list[str] | None = Field(default=None, alias="allowedHostnames")
    hostname_allowlist: list[str] | None = Field(default=None, alias="hostnameAllowlist")
    model_config = ConfigDict(populate_by_name=True)


class BrowserConfig(BaseModel):
    """Top-level browser configuration - mirrors TS BrowserConfig"""
    enabled: bool | None = Field(default=None)
    evaluate_enabled: bool | None = Field(default=None, alias="evaluateEnabled")
    cdp_url: str | None = Field(default=None, alias="cdpUrl")
    remote_cdp_timeout_ms: int | None = Field(default=None, alias="remoteCdpTimeoutMs")
    remote_cdp_handshake_timeout_ms: int | None = Field(default=None, alias="remoteCdpHandshakeTimeoutMs")
    color: str | None = Field(default=None)
    executable_path: str | None = Field(default=None, alias="executablePath")
    headless: bool | None = Field(default=None)
    no_sandbox: bool | None = Field(default=None, alias="noSandbox")
    attach_only: bool | None = Field(default=None, alias="attachOnly")
    cdp_port_range_start: int | None = Field(default=None, alias="cdpPortRangeStart")
    default_profile: str | None = Field(default=None, alias="defaultProfile")
    profiles: dict[str, BrowserProfileConfig] | None = Field(default=None)
    snapshot_defaults: BrowserSnapshotDefaults | None = Field(default=None, alias="snapshotDefaults")
    ssrf_policy: BrowserSsrfPolicyConfig | None = Field(default=None, alias="ssrfPolicy")
    extra_args: list[str] | None = Field(default=None, alias="extraArgs")
    model_config = ConfigDict(populate_by_name=True)


# ─── Auth config ─────────────────────────────────────────────────────────────

class AuthProfileConfig(BaseModel):
    """Auth profile entry - mirrors TS AuthProfileConfig"""
    provider: str = ""
    mode: Literal["api_key", "oauth", "token"] | None = Field(default=None)
    email: str | None = Field(default=None)
    model_config = ConfigDict(populate_by_name=True)


class AuthCooldownsConfig(BaseModel):
    """Auth cooldowns - mirrors TS auth.cooldowns"""
    billing_backoff_hours: float | None = Field(default=None, alias="billingBackoffHours")
    billing_backoff_hours_by_provider: dict[str, float] | None = Field(default=None, alias="billingBackoffHoursByProvider")
    billing_max_hours: float | None = Field(default=None, alias="billingMaxHours")
    failure_window_hours: float | None = Field(default=None, alias="failureWindowHours")
    model_config = ConfigDict(populate_by_name=True)


class TopLevelAuthConfig(BaseModel):
    """Top-level auth configuration - mirrors TS AuthConfig"""
    profiles: dict[str, AuthProfileConfig] | None = Field(default=None)
    order: dict[str, list[str]] | None = Field(default=None)
    cooldowns: AuthCooldownsConfig | None = Field(default=None)
    model_config = ConfigDict(populate_by_name=True)


# ─── Diagnostics config ─────────────────────────────────────────────────────

class DiagnosticsOtelConfig(BaseModel):
    """OpenTelemetry diagnostics - mirrors TS DiagnosticsOtelConfig"""
    enabled: bool | None = Field(default=None)
    endpoint: str | None = Field(default=None)
    protocol: Literal["http/protobuf", "grpc"] | None = Field(default=None)
    headers: dict[str, str] | None = Field(default=None)
    service_name: str | None = Field(default=None, alias="serviceName")
    traces: bool | None = Field(default=None)
    metrics: bool | None = Field(default=None)
    logs: bool | None = Field(default=None)
    sample_rate: float | None = Field(default=None, alias="sampleRate")
    flush_interval_ms: int | None = Field(default=None, alias="flushIntervalMs")
    model_config = ConfigDict(populate_by_name=True)


class DiagnosticsCacheTraceConfig(BaseModel):
    """Cache trace diagnostics - mirrors TS DiagnosticsCacheTraceConfig"""
    enabled: bool | None = Field(default=None)
    file_path: str | None = Field(default=None, alias="filePath")
    include_messages: bool | None = Field(default=None, alias="includeMessages")
    include_prompt: bool | None = Field(default=None, alias="includePrompt")
    include_system: bool | None = Field(default=None, alias="includeSystem")
    model_config = ConfigDict(populate_by_name=True)


class DiagnosticsConfig(BaseModel):
    """Diagnostics configuration - mirrors TS DiagnosticsConfig"""
    enabled: bool | None = Field(default=None)
    flags: list[str] | None = Field(default=None)
    stuck_session_warn_ms: int | None = Field(default=None, alias="stuckSessionWarnMs")
    otel: DiagnosticsOtelConfig | None = Field(default=None)
    cache_trace: DiagnosticsCacheTraceConfig | None = Field(default=None, alias="cacheTrace")
    model_config = ConfigDict(populate_by_name=True)


# ─── HumanDelay config ───────────────────────────────────────────────────────

class HumanDelayConfig(BaseModel):
    """Human delay configuration - mirrors TS HumanDelayConfig"""
    mode: Literal["off", "natural", "custom"] | None = Field(default=None)
    min_ms: int | None = Field(default=None, alias="minMs")
    max_ms: int | None = Field(default=None, alias="maxMs")
    model_config = ConfigDict(populate_by_name=True)


# ─── Block streaming config ──────────────────────────────────────────────────

class BlockStreamingChunkConfig(BaseModel):
    """Block streaming chunk config - mirrors TS BlockStreamingChunkConfig"""
    min_chars: int | None = Field(default=None, alias="minChars")
    max_chars: int | None = Field(default=None, alias="maxChars")
    break_preference: Literal["paragraph", "newline", "sentence"] | None = Field(default=None, alias="breakPreference")
    model_config = ConfigDict(populate_by_name=True)


class BlockStreamingCoalesceConfig(BaseModel):
    """Block streaming coalesce config - mirrors TS BlockStreamingCoalesceConfig"""
    min_chars: int | None = Field(default=None, alias="minChars")
    max_chars: int | None = Field(default=None, alias="maxChars")
    idle_ms: int | None = Field(default=None, alias="idleMs")
    model_config = ConfigDict(populate_by_name=True)


# ─── Gateway trusted proxy config ────────────────────────────────────────────

class GatewayTrustedProxyConfig(BaseModel):
    """Gateway trusted proxy config - mirrors TS GatewayTrustedProxyConfig"""
    user_header: str = Field(alias="userHeader")
    required_headers: list[str] | None = Field(default=None, alias="requiredHeaders")
    allow_users: list[str] | None = Field(default=None, alias="allowUsers")
    model_config = ConfigDict(populate_by_name=True)


# ─── Docker ulimits ──────────────────────────────────────────────────────────

class DockerUlimitConfig(BaseModel):
    """Single ulimit entry for Docker - mirrors TS ulimits structure"""
    soft: int | None = Field(default=None)
    hard: int | None = Field(default=None)
    model_config = ConfigDict(populate_by_name=True)


# ─── Top-level typed config models (replacing dict[str, Any]) ─────────────────

class SecretsDefaultsConfig(BaseModel):
    """Default resolution strategies for secret refs."""
    env: str | None = Field(default=None)
    file: str | None = Field(default=None)
    exec: str | None = Field(default=None)
    model_config = ConfigDict(populate_by_name=True, extra="allow")

class SecretResolutionConfig(BaseModel):
    max_provider_concurrency: int | None = Field(default=None, alias="maxProviderConcurrency")
    max_refs_per_provider: int | None = Field(default=None, alias="maxRefsPerProvider")
    max_batch_bytes: int | None = Field(default=None, alias="maxBatchBytes")
    model_config = ConfigDict(populate_by_name=True)

class SecretsConfig(BaseModel):
    """Secrets config -- mirrors TS SecretsConfig."""
    providers: dict[str, Any] | None = Field(default=None)
    defaults: SecretsDefaultsConfig | None = Field(default=None)
    resolution: SecretResolutionConfig | None = Field(default=None)
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class TalkProviderConfig(BaseModel):
    """Per-provider TTS/talk config -- mirrors TS TalkProviderConfig."""
    voice_id: str | None = Field(default=None, alias="voiceId")
    voice_aliases: dict[str, str] | None = Field(default=None, alias="voiceAliases")
    model_id: str | None = Field(default=None, alias="modelId")
    output_format: str | None = Field(default=None, alias="outputFormat")
    api_key: str | dict[str, Any] | None = Field(default=None, alias="apiKey")
    model_config = ConfigDict(populate_by_name=True, extra="allow")

class TalkConfig(BaseModel):
    """Talk/TTS config -- mirrors TS TalkConfig."""
    provider: str | None = Field(default=None)
    providers: dict[str, TalkProviderConfig] | None = Field(default=None)
    interrupt_on_speech: bool = Field(default=True, alias="interruptOnSpeech")
    voice_id: str | None = Field(default=None, alias="voiceId")
    voice_aliases: dict[str, str] | None = Field(default=None, alias="voiceAliases")
    model_id: str | None = Field(default=None, alias="modelId")
    output_format: str | None = Field(default=None, alias="outputFormat")
    api_key: str | dict[str, Any] | None = Field(default=None, alias="apiKey")
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class ExecApprovalForwardTarget(BaseModel):
    channel: str
    to: str
    account_id: str | None = Field(default=None, alias="accountId")
    thread_id: str | int | None = Field(default=None, alias="threadId")
    model_config = ConfigDict(populate_by_name=True)

class ExecApprovalForwardingConfig(BaseModel):
    enabled: bool = Field(default=False)
    mode: Literal["session", "targets", "both"] = Field(default="session")
    agent_filter: list[str] | None = Field(default=None, alias="agentFilter")
    session_filter: list[str] | None = Field(default=None, alias="sessionFilter")
    targets: list[ExecApprovalForwardTarget] | None = Field(default=None)
    model_config = ConfigDict(populate_by_name=True)

class ApprovalsConfig(BaseModel):
    """Approvals config -- mirrors TS ApprovalsConfig."""
    exec: ExecApprovalForwardingConfig | None = Field(default=None)
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class WideAreaDiscoveryConfig(BaseModel):
    enabled: bool | None = Field(default=None)
    domain: str | None = Field(default=None)
    model_config = ConfigDict(populate_by_name=True)

class MdnsDiscoveryConfig(BaseModel):
    mode: Literal["off", "minimal", "full"] | None = Field(default="minimal")
    model_config = ConfigDict(populate_by_name=True)

class DiscoveryConfig(BaseModel):
    """Discovery config -- mirrors TS DiscoveryConfig."""
    wide_area: WideAreaDiscoveryConfig | None = Field(default=None, alias="wideArea")
    mdns: MdnsDiscoveryConfig | None = Field(default=None)
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class BroadcastConfig(BaseModel):
    """Broadcast config -- mirrors TS BroadcastConfig."""
    strategy: Literal["parallel", "sequential"] | None = Field(default=None)
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class AudioTranscriptionConfig(BaseModel):
    command: list[str] = Field(default_factory=list)
    timeout_seconds: int | None = Field(default=None, alias="timeoutSeconds")
    model_config = ConfigDict(populate_by_name=True)

class AudioConfig(BaseModel):
    """Audio config -- mirrors TS AudioConfig (deprecated)."""
    transcription: AudioTranscriptionConfig | None = Field(default=None)
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class NodeHostBrowserProxyConfig(BaseModel):
    enabled: bool = Field(default=True)
    allow_profiles: list[str] | None = Field(default=None, alias="allowProfiles")
    model_config = ConfigDict(populate_by_name=True)

class NodeHostConfig(BaseModel):
    """Node host config -- mirrors TS NodeHostConfig."""
    browser_proxy: NodeHostBrowserProxyConfig | None = Field(default=None, alias="browserProxy")
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class CanvasHostConfig(BaseModel):
    """Canvas host config -- mirrors TS CanvasHostConfig."""
    enabled: bool | None = Field(default=None)
    root: str | None = Field(default=None)
    port: int = Field(default=18793)
    live_reload: bool = Field(default=True, alias="liveReload")
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class WebReconnectConfig(BaseModel):
    initial_ms: int | None = Field(default=None, alias="initialMs")
    max_ms: int | None = Field(default=None, alias="maxMs")
    factor: float | None = Field(default=None)
    jitter: float | None = Field(default=None)
    max_attempts: int | None = Field(default=None, alias="maxAttempts")
    model_config = ConfigDict(populate_by_name=True)

class WebConfig(BaseModel):
    """Web config -- mirrors TS WebConfig."""
    enabled: bool = Field(default=True)
    heartbeat_seconds: int | None = Field(default=None, alias="heartbeatSeconds")
    reconnect: WebReconnectConfig | None = Field(default=None)
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class OpenClawConfig(BaseModel):
    """Root configuration schema - mirrors TypeScript OpenClawConfig"""
    
    # Core configs (original 7)
    # Note: 'agent' field is deprecated - use 'agents.defaults' instead
    # This field is kept for backward compatibility with legacy configs
    agent: AgentConfig | None = Field(
        default_factory=AgentConfig,
        deprecated="Use 'agents.defaults' instead. This field will be auto-migrated on load."
    )
    gateway: GatewayConfig | None = Field(default_factory=GatewayConfig)
    agents: AgentsConfig | None = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig | None = Field(default_factory=ChannelsConfig)
    tools: ToolsConfig | None = Field(default_factory=ToolsConfig)
    skills: SkillsConfig | None = Field(default_factory=SkillsConfig)
    plugins: PluginsConfig | None = Field(default_factory=PluginsConfig)
    
    # Additional configs (matching TypeScript with typed models)
    meta: MetaConfig | None = Field(default=None)
    auth: TopLevelAuthConfig | dict[str, Any] | None = Field(default=None)
    env: EnvConfig | dict[str, Any] | None = Field(default=None)
    wizard: WizardConfig | None = Field(default=None)
    diagnostics: DiagnosticsConfig | dict[str, Any] | None = Field(default=None)
    logging: LoggingConfig | None = Field(default=None)
    update: UpdateConfig | None = Field(default=None)
    browser: BrowserConfig | dict[str, Any] | None = Field(default=None)
    ui: UIConfig | None = Field(default=None)
    models: ModelsConfig | None = Field(default=None)
    node_host: NodeHostConfig | None = Field(default=None, alias="nodeHost")
    bindings: list[dict[str, Any]] | None = Field(default=None)
    broadcast: BroadcastConfig | None = Field(default=None)
    audio: AudioConfig | None = Field(default=None)
    messages: MessagesConfig | None = Field(default=None)
    commands: CommandsConfig | None = Field(default=None)
    approvals: ApprovalsConfig | None = Field(default=None)
    session: SessionConfig = Field(default_factory=SessionConfig)
    web: WebConfig | None = Field(default=None)
    cron: CronConfig | None = Field(default=None)
    hooks: HooksConfig | None = Field(default=None)
    discovery: DiscoveryConfig | None = Field(default=None)
    canvas_host: CanvasHostConfig | None = Field(default=None, alias="canvasHost")
    talk: TalkConfig | None = Field(default=None)
    memory: MemoryConfig | None = Field(default=None)
    # P2 alignment: new top-level config sections from TS
    acp: AcpConfig | None = Field(default=None)
    media: MediaTopLevelConfig | None = Field(default=None)
    cli: CliConfig | None = Field(default=None)
    secrets: SecretsConfig | None = Field(default=None)
    # $schema field for JSON schema editor tooling
    schema_ref: str | None = Field(default=None, alias="$schema")

    class Config:
        extra = "allow"  # Allow extra fields for extensibility
        populate_by_name = True  # Support camelCase aliases


# Rebuild models to resolve forward references
# This is necessary because some classes reference other classes defined later in the file
AgentConfig.model_rebuild()
AuthConfig.model_rebuild()
GatewayNodesConfig.model_rebuild()
AgentDefaults.model_rebuild()
AgentsConfig.model_rebuild()
SandboxDockerConfig.model_rebuild()
SlackAccountConfig.model_rebuild()
OpenClawConfig.model_rebuild()
