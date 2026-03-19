"""Plugin runtime factory — mirrors src/plugins/runtime/index.ts"""
from __future__ import annotations

from typing import Any

from .types import (
    PluginRuntime,
    PluginRuntimeChannel,
    PluginRuntimeChannelActivity,
    PluginRuntimeChannelCommands,
    PluginRuntimeChannelDebounce,
    PluginRuntimeChannelGroups,
    PluginRuntimeChannelMentions,
    PluginRuntimeChannelPairing,
    PluginRuntimeChannelReactions,
    PluginRuntimeChannelReply,
    PluginRuntimeChannelRouting,
    PluginRuntimeChannelSession,
    PluginRuntimeChannelText,
    PluginRuntimeConfig,
    PluginRuntimeLogging,
    PluginRuntimeMedia,
    PluginRuntimeState,
    PluginRuntimeSystem,
    PluginRuntimeTools,
    PluginRuntimeTts,
)


def _try_import(module_path: str, attr: str) -> Any:
    """Lazily import a function/class — returns None if unavailable."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, attr, None)
    except (ImportError, ModuleNotFoundError):
        return None


def create_plugin_runtime(
    registry: Any = None,
    cfg: Any = None,
    workspace_dir: str | None = None,
) -> PluginRuntime:
    """Build a PluginRuntime by lazily resolving available functions.

    Mirrors createPluginRuntime() / the runtime factory in plugins/runtime/index.ts.
    """
    version = "0.0.0"
    try:
        import importlib.metadata
        version = importlib.metadata.version("openclaw") or version
    except Exception:
        pass

    # --- config ---
    config = PluginRuntimeConfig(
        load_config=_try_import("openclaw.config.loader", "load_config"),
        write_config_file=_try_import("openclaw.config.loader", "write_config_file"),
    )

    # --- system ---
    system = PluginRuntimeSystem(
        enqueue_system_event=_try_import("openclaw.infra.system_events", "enqueue_system_event"),
        request_heartbeat_now=_try_import("openclaw.infra.heartbeat_wake", "request_heartbeat_now"),
        run_command_with_timeout=_try_import("openclaw.process.exec", "run_command_with_timeout"),
        format_native_dependency_hint=None,
    )

    # --- media ---
    media = PluginRuntimeMedia(
        load_web_media=_try_import("openclaw.web.media", "load_web_media"),
        detect_mime=_try_import("openclaw.media.mime", "detect_mime"),
        media_kind_from_mime=_try_import("openclaw.media.constants", "media_kind_from_mime"),
        is_voice_compatible_audio=_try_import("openclaw.media.audio", "is_voice_compatible_audio"),
        get_image_metadata=_try_import("openclaw.media.image_ops", "get_image_metadata"),
        resize_to_jpeg=_try_import("openclaw.media.image_ops", "resize_to_jpeg"),
        fetch_remote_media=_try_import("openclaw.media.fetch", "fetch_remote_media"),
        save_media_buffer=_try_import("openclaw.media.store", "save_media_buffer"),
    )

    # --- tts ---
    tts = PluginRuntimeTts(
        text_to_speech_telephony=_try_import("openclaw.tts.tts", "text_to_speech_telephony"),
    )

    # --- stt ---
    from .runtime_stt import create_runtime_stt
    stt = create_runtime_stt()

    # --- tools ---
    tools = PluginRuntimeTools(
        create_memory_get_tool=_try_import("openclaw.agents.tools.memory_tool", "create_memory_get_tool"),
        create_memory_search_tool=_try_import("openclaw.agents.tools.memory_tool", "create_memory_search_tool"),
        register_memory_cli=_try_import("openclaw.cli.memory_cli", "register_memory_cli"),
    )

    # --- channel.text ---
    channel_text = PluginRuntimeChannelText(
        chunk_by_newline=_try_import("openclaw.auto_reply.chunk", "chunk_by_newline"),
        chunk_markdown_text=_try_import("openclaw.auto_reply.chunk", "chunk_markdown_text"),
        chunk_markdown_text_with_mode=_try_import("openclaw.auto_reply.chunk", "chunk_markdown_text_with_mode"),
        chunk_text=_try_import("openclaw.auto_reply.chunk", "chunk_text"),
        chunk_text_with_mode=_try_import("openclaw.auto_reply.chunk", "chunk_text_with_mode"),
        resolve_chunk_mode=_try_import("openclaw.auto_reply.chunk", "resolve_chunk_mode"),
        resolve_text_chunk_limit=_try_import("openclaw.auto_reply.chunk", "resolve_text_chunk_limit"),
        has_control_command=_try_import("openclaw.auto_reply.command_detection", "has_control_command"),
        resolve_markdown_table_mode=_try_import("openclaw.config.markdown_tables", "resolve_markdown_table_mode"),
        convert_markdown_tables=_try_import("openclaw.markdown.tables", "convert_markdown_tables"),
    )

    # --- channel.reply ---
    channel_reply = PluginRuntimeChannelReply(
        dispatch_reply_with_buffered_block_dispatcher=_try_import(
            "openclaw.auto_reply.reply.provider_dispatcher", "dispatch_reply_with_buffered_block_dispatcher"
        ),
        create_reply_dispatcher_with_typing=_try_import(
            "openclaw.auto_reply.reply.reply_dispatcher", "create_reply_dispatcher_with_typing"
        ),
        resolve_effective_messages_config=_try_import("openclaw.agents.identity", "resolve_effective_messages_config"),
        resolve_human_delay_config=_try_import("openclaw.agents.identity", "resolve_human_delay_config"),
        dispatch_reply_from_config=_try_import(
            "openclaw.auto_reply.reply.dispatch_from_config", "dispatch_reply_from_config"
        ),
        finalize_inbound_context=_try_import("openclaw.auto_reply.reply.inbound_context", "finalize_inbound_context"),
        format_agent_envelope=_try_import("openclaw.auto_reply.envelope", "format_agent_envelope"),
        format_inbound_envelope=_try_import("openclaw.auto_reply.envelope", "format_inbound_envelope"),
        resolve_envelope_format_options=_try_import("openclaw.auto_reply.envelope", "resolve_envelope_format_options"),
    )

    # --- channel.routing ---
    channel_routing = PluginRuntimeChannelRouting(
        resolve_agent_route=_try_import("openclaw.routing.resolve_route", "resolve_agent_route"),
    )

    # --- channel.pairing ---
    channel_pairing = PluginRuntimeChannelPairing(
        build_pairing_reply=_try_import("openclaw.pairing.pairing_messages", "build_pairing_reply"),
        read_allow_from_store=_try_import("openclaw.pairing.pairing_store", "read_channel_allow_from_store"),
        upsert_pairing_request=_try_import("openclaw.pairing.pairing_store", "upsert_channel_pairing_request"),
    )

    # --- channel.activity ---
    channel_activity = PluginRuntimeChannelActivity(
        record=_try_import("openclaw.infra.channel_activity", "record_channel_activity"),
        get=_try_import("openclaw.infra.channel_activity", "get_channel_activity"),
    )

    # --- channel.session ---
    channel_session = PluginRuntimeChannelSession(
        resolve_store_path=_try_import("openclaw.config.sessions", "resolve_store_path"),
        read_session_updated_at=_try_import("openclaw.config.sessions", "read_session_updated_at"),
        record_session_meta_from_inbound=_try_import("openclaw.config.sessions", "record_session_meta_from_inbound"),
        record_inbound_session=_try_import("openclaw.channels.session", "record_inbound_session"),
        update_last_route=_try_import("openclaw.config.sessions", "update_last_route"),
    )

    # --- channel.mentions ---
    channel_mentions = PluginRuntimeChannelMentions(
        build_mention_regexes=_try_import("openclaw.auto_reply.reply.mentions", "build_mention_regexes"),
        matches_mention_patterns=_try_import("openclaw.auto_reply.reply.mentions", "matches_mention_patterns"),
        matches_mention_with_explicit=_try_import("openclaw.auto_reply.reply.mentions", "matches_mention_with_explicit"),
    )

    # --- channel.reactions ---
    channel_reactions = PluginRuntimeChannelReactions(
        should_ack_reaction=_try_import("openclaw.channels.ack_reactions", "should_ack_reaction"),
        remove_ack_reaction_after_reply=_try_import("openclaw.channels.ack_reactions", "remove_ack_reaction_after_reply"),
    )

    # --- channel.groups ---
    channel_groups = PluginRuntimeChannelGroups(
        resolve_group_policy=_try_import("openclaw.config.group_policy", "resolve_channel_group_policy"),
        resolve_require_mention=_try_import("openclaw.config.group_policy", "resolve_channel_group_require_mention"),
    )

    # --- channel.debounce ---
    channel_debounce = PluginRuntimeChannelDebounce(
        create_inbound_debouncer=_try_import("openclaw.auto_reply.inbound_debounce", "create_inbound_debouncer"),
        resolve_inbound_debounce_ms=_try_import("openclaw.auto_reply.inbound_debounce", "resolve_inbound_debounce_ms"),
    )

    # --- channel.commands ---
    channel_commands = PluginRuntimeChannelCommands(
        resolve_command_authorized_from_authorizers=_try_import(
            "openclaw.channels.command_gating", "resolve_command_authorized_from_authorizers"
        ),
        is_control_command_message=_try_import("openclaw.auto_reply.command_detection", "is_control_command_message"),
        should_compute_command_authorized=_try_import(
            "openclaw.auto_reply.command_detection", "should_compute_command_authorized"
        ),
        should_handle_text_commands=_try_import("openclaw.auto_reply.commands_registry", "should_handle_text_commands"),
    )

    channel = PluginRuntimeChannel(
        text=channel_text,
        reply=channel_reply,
        routing=channel_routing,
        pairing=channel_pairing,
        media=media,
        activity=channel_activity,
        session=channel_session,
        mentions=channel_mentions,
        reactions=channel_reactions,
        groups=channel_groups,
        debounce=channel_debounce,
        commands=channel_commands,
    )

    # --- events ---
    from .runtime_events import create_runtime_events
    events = create_runtime_events()

    # --- logging ---
    logging_rt = PluginRuntimeLogging(
        should_log_verbose=_try_import("openclaw.globals", "should_log_verbose"),
        get_child_logger=_try_import("openclaw.logging.subsystem", "get_child_logger"),
    )

    # --- state ---
    state = PluginRuntimeState(
        resolve_state_dir=_try_import("openclaw.config.paths", "resolve_state_dir"),
    )

    return PluginRuntime(
        version=version,
        config=config,
        system=system,
        media=media,
        tts=tts,
        stt=stt,
        tools=tools,
        events=events,
        channel=channel,
        logging=logging_rt,
        state=state,
    )


__all__ = ["PluginRuntime", "create_plugin_runtime"]
