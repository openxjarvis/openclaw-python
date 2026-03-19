"""Plugin runtime type definitions — mirrors src/plugins/runtime/types.ts"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class RuntimeLogger(Protocol):
    def info(self, message: str, meta: dict | None = None) -> None: ...
    def warn(self, message: str, meta: dict | None = None) -> None: ...
    def error(self, message: str, meta: dict | None = None) -> None: ...
    def debug(self, message: str, meta: dict | None = None) -> None: ...


@dataclass
class PluginRuntimeConfig:
    """runtime.config — config file load/write utilities."""
    load_config: Callable[..., Any] | None = None
    write_config_file: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeSystem:
    """runtime.system — system-level utilities."""
    enqueue_system_event: Callable[..., Any] | None = None
    request_heartbeat_now: Callable[..., Any] | None = None
    run_command_with_timeout: Callable[..., Any] | None = None
    format_native_dependency_hint: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeMedia:
    """runtime.media — media/file utilities."""
    load_web_media: Callable[..., Any] | None = None
    detect_mime: Callable[..., Any] | None = None
    media_kind_from_mime: Callable[..., Any] | None = None
    is_voice_compatible_audio: Callable[..., Any] | None = None
    get_image_metadata: Callable[..., Any] | None = None
    resize_to_jpeg: Callable[..., Any] | None = None
    fetch_remote_media: Callable[..., Any] | None = None
    save_media_buffer: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeTts:
    """runtime.tts — text-to-speech."""
    text_to_speech_telephony: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeStt:
    """runtime.stt — speech-to-text."""
    transcribe_audio_file: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeTools:
    """runtime.tools — agent tool factories."""
    create_memory_get_tool: Callable[..., Any] | None = None
    create_memory_search_tool: Callable[..., Any] | None = None
    register_memory_cli: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeChannelText:
    chunk_by_newline: Callable[..., Any] | None = None
    chunk_markdown_text: Callable[..., Any] | None = None
    chunk_markdown_text_with_mode: Callable[..., Any] | None = None
    chunk_text: Callable[..., Any] | None = None
    chunk_text_with_mode: Callable[..., Any] | None = None
    resolve_chunk_mode: Callable[..., Any] | None = None
    resolve_text_chunk_limit: Callable[..., Any] | None = None
    has_control_command: Callable[..., Any] | None = None
    resolve_markdown_table_mode: Callable[..., Any] | None = None
    convert_markdown_tables: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeChannelReply:
    dispatch_reply_with_buffered_block_dispatcher: Callable[..., Any] | None = None
    create_reply_dispatcher_with_typing: Callable[..., Any] | None = None
    resolve_effective_messages_config: Callable[..., Any] | None = None
    resolve_human_delay_config: Callable[..., Any] | None = None
    dispatch_reply_from_config: Callable[..., Any] | None = None
    finalize_inbound_context: Callable[..., Any] | None = None
    format_agent_envelope: Callable[..., Any] | None = None
    format_inbound_envelope: Callable[..., Any] | None = None
    resolve_envelope_format_options: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeChannelRouting:
    resolve_agent_route: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeChannelPairing:
    build_pairing_reply: Callable[..., Any] | None = None
    read_allow_from_store: Callable[..., Any] | None = None
    upsert_pairing_request: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeChannelActivity:
    record: Callable[..., Any] | None = None
    get: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeChannelSession:
    resolve_store_path: Callable[..., Any] | None = None
    read_session_updated_at: Callable[..., Any] | None = None
    record_session_meta_from_inbound: Callable[..., Any] | None = None
    record_inbound_session: Callable[..., Any] | None = None
    update_last_route: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeChannelMentions:
    build_mention_regexes: Callable[..., Any] | None = None
    matches_mention_patterns: Callable[..., Any] | None = None
    matches_mention_with_explicit: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeChannelReactions:
    should_ack_reaction: Callable[..., Any] | None = None
    remove_ack_reaction_after_reply: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeChannelGroups:
    resolve_group_policy: Callable[..., Any] | None = None
    resolve_require_mention: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeChannelDebounce:
    create_inbound_debouncer: Callable[..., Any] | None = None
    resolve_inbound_debounce_ms: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeChannelCommands:
    resolve_command_authorized_from_authorizers: Callable[..., Any] | None = None
    is_control_command_message: Callable[..., Any] | None = None
    should_compute_command_authorized: Callable[..., Any] | None = None
    should_handle_text_commands: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeChannel:
    """runtime.channel — channel utilities namespace."""
    text: PluginRuntimeChannelText = field(default_factory=PluginRuntimeChannelText)
    reply: PluginRuntimeChannelReply = field(default_factory=PluginRuntimeChannelReply)
    routing: PluginRuntimeChannelRouting = field(default_factory=PluginRuntimeChannelRouting)
    pairing: PluginRuntimeChannelPairing = field(default_factory=PluginRuntimeChannelPairing)
    media: PluginRuntimeMedia = field(default_factory=PluginRuntimeMedia)
    activity: PluginRuntimeChannelActivity = field(default_factory=PluginRuntimeChannelActivity)
    session: PluginRuntimeChannelSession = field(default_factory=PluginRuntimeChannelSession)
    mentions: PluginRuntimeChannelMentions = field(default_factory=PluginRuntimeChannelMentions)
    reactions: PluginRuntimeChannelReactions = field(default_factory=PluginRuntimeChannelReactions)
    groups: PluginRuntimeChannelGroups = field(default_factory=PluginRuntimeChannelGroups)
    debounce: PluginRuntimeChannelDebounce = field(default_factory=PluginRuntimeChannelDebounce)
    commands: PluginRuntimeChannelCommands = field(default_factory=PluginRuntimeChannelCommands)


@dataclass
class PluginRuntimeLogging:
    """runtime.logging — logging utilities."""
    should_log_verbose: Callable[..., Any] | None = None
    get_child_logger: Callable[..., RuntimeLogger] | None = None


@dataclass
class PluginRuntimeEvents:
    """runtime.events — event listeners."""
    on_agent_event: Callable[..., Any] | None = None
    on_session_transcript_update: Callable[..., Any] | None = None


@dataclass
class PluginRuntimeState:
    """runtime.state — state directory resolution."""
    resolve_state_dir: Callable[..., Any] | None = None


@dataclass
class PluginRuntime:
    """Full plugin runtime object — mirrors PluginRuntime from runtime/types.ts.

    Plugins access this via `api.runtime`.
    """
    version: str = "0.0.0"
    config: PluginRuntimeConfig = field(default_factory=PluginRuntimeConfig)
    system: PluginRuntimeSystem = field(default_factory=PluginRuntimeSystem)
    media: PluginRuntimeMedia = field(default_factory=PluginRuntimeMedia)
    tts: PluginRuntimeTts = field(default_factory=PluginRuntimeTts)
    stt: PluginRuntimeStt = field(default_factory=PluginRuntimeStt)
    tools: PluginRuntimeTools = field(default_factory=PluginRuntimeTools)
    events: PluginRuntimeEvents = field(default_factory=PluginRuntimeEvents)
    channel: PluginRuntimeChannel = field(default_factory=PluginRuntimeChannel)
    logging: PluginRuntimeLogging = field(default_factory=PluginRuntimeLogging)
    state: PluginRuntimeState = field(default_factory=PluginRuntimeState)
