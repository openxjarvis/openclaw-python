"""Plugin SDK — single stable import surface for OpenClaw plugins.

Mirrors src/plugin-sdk/index.ts (460 lines).

Plugins should import from this module rather than from internal modules:

    from openclaw.plugin_sdk import (
        resolve_allowlist_match_simple,
        should_ack_reaction,
        dispatch_reply_from_config,
        ...
    )
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Channel utilities
# ---------------------------------------------------------------------------

try:
    from openclaw.channels.allowlist_match import (
        AllowlistMatch,
        resolve_allowlist_match_simple,
    )
except ImportError:
    AllowlistMatch = None  # type: ignore[assignment,misc]
    resolve_allowlist_match_simple = None  # type: ignore[assignment]

try:
    from openclaw.channels.command_gating import (
        CommandAuthorizer,
        resolve_command_authorized_from_authorizers,
    )
except ImportError:
    CommandAuthorizer = None  # type: ignore[assignment,misc]
    resolve_command_authorized_from_authorizers = None  # type: ignore[assignment]

try:
    from openclaw.channels.ack_reactions import (
        should_ack_reaction,
        remove_ack_reaction_after_reply,
    )
except ImportError:
    should_ack_reaction = None  # type: ignore[assignment]
    remove_ack_reaction_after_reply = None  # type: ignore[assignment]

try:
    from openclaw.channels.location import (
        NormalizedLocation,
        format_location_text,
        to_location_context,
    )
except ImportError:
    NormalizedLocation = None  # type: ignore[assignment,misc]
    format_location_text = None  # type: ignore[assignment]
    to_location_context = None  # type: ignore[assignment]

try:
    from openclaw.channels.targets import (
        MessagingTarget,
        normalize_target_id,
        parse_messaging_target,
    )
except ImportError:
    MessagingTarget = None  # type: ignore[assignment,misc]
    normalize_target_id = None  # type: ignore[assignment]
    parse_messaging_target = None  # type: ignore[assignment]

try:
    from openclaw.channels.mention_gating import (
        MentionGateResult,
        resolve_mention_gating,
    )
except ImportError:
    MentionGateResult = None  # type: ignore[assignment,misc]
    resolve_mention_gating = None  # type: ignore[assignment]

try:
    from openclaw.channels.sender_label import (
        resolve_sender_label,
        list_sender_label_candidates,
    )
except ImportError:
    resolve_sender_label = None  # type: ignore[assignment]
    list_sender_label_candidates = None  # type: ignore[assignment]

try:
    from openclaw.channels.sender_identity import validate_sender_identity
except ImportError:
    validate_sender_identity = None  # type: ignore[assignment]

try:
    from openclaw.channels.conversation_label import resolve_conversation_label
except ImportError:
    resolve_conversation_label = None  # type: ignore[assignment]

try:
    from openclaw.channels.reply_prefix import create_reply_prefix_context
except ImportError:
    create_reply_prefix_context = None  # type: ignore[assignment]

try:
    from openclaw.channels.draft_stream_loop import (
        DraftStreamLoop,
        create_draft_stream_loop,
    )
except ImportError:
    DraftStreamLoop = None  # type: ignore[assignment,misc]
    create_draft_stream_loop = None  # type: ignore[assignment]

try:
    from openclaw.channels.channel_typing import (
        TypingCallbacks,
        create_typing_callbacks,
    )
except ImportError:
    TypingCallbacks = None  # type: ignore[assignment,misc]
    create_typing_callbacks = None  # type: ignore[assignment]

try:
    from openclaw.channels.channel_config import (
        ChannelEntryMatch,
        normalize_channel_slug,
        resolve_channel_entry_match_with_fallback,
    )
except ImportError:
    ChannelEntryMatch = None  # type: ignore[assignment,misc]
    normalize_channel_slug = None  # type: ignore[assignment]
    resolve_channel_entry_match_with_fallback = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Auto-reply utilities
# ---------------------------------------------------------------------------

try:
    from openclaw.auto_reply.reply.dispatch_from_config import dispatch_reply_from_config
except ImportError:
    dispatch_reply_from_config = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Config utilities
# ---------------------------------------------------------------------------

try:
    from openclaw.config.loader import load_config
except ImportError:
    try:
        from openclaw.config.loader import load_config  # type: ignore[no-redef]
    except ImportError:
        load_config = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Plugin registry & hooks
# ---------------------------------------------------------------------------

from openclaw.plugins.types import (
    OpenClawPluginApi,
    OpenClawPluginCommandDefinition,
    OpenClawPluginDefinition,
    OpenClawPluginHookOptions,
    OpenClawPluginService,
    OpenClawPluginServiceContext,
    OpenClawPluginToolOptions,
    PluginHookName,
    PluginKind,
    PluginLogger,
    PluginOrigin,
    ProviderAuthMethod,
    ProviderPlugin,
)

from openclaw.plugins.hooks import HookRunner, HookRunnerOptions, create_hook_runner
from openclaw.plugins.registry import (
    ConcretePluginApi,
    PluginCommandRegistration,
    PluginProviderRegistration,
    PluginRecord,
    PluginRegistryData,
    create_plugin_registry,
)
from openclaw.plugins.manifest import (
    PluginManifest,
    load_plugin_manifest,
    resolve_plugin_manifest_path,
)
from openclaw.plugins.config_state import (
    NormalizedPluginsConfig,
    normalize_plugins_config,
    resolve_enable_state,
)
from openclaw.plugins.runtime import PluginRuntime, create_plugin_runtime
from openclaw.plugins.runtime.types import (
    PluginRuntimeChannel,
    PluginRuntimeConfig,
    PluginRuntimeMedia,
)

# ---------------------------------------------------------------------------
# Plugin config schema helpers
# Mirrors TS emptyPluginConfigSchema() from src/plugin-sdk/index.ts
# ---------------------------------------------------------------------------

def empty_plugin_config_schema() -> dict:
    """Return an empty (but valid) plugin config JSON Schema.

    Mirrors TypeScript ``emptyPluginConfigSchema()`` from ``openclaw/plugin-sdk``.
    Plugins that accept no configuration can use this as their ``config_schema``.

    Returns:
        A minimal JSON Schema object that accepts an empty config object.

    Example::

        plugin = {
            "id": "my-plugin",
            "name": "My Plugin",
            "config_schema": empty_plugin_config_schema(),
            "register": register,
        }
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }


# ---------------------------------------------------------------------------
# Plugin hooks directory loader
# Mirrors TS registerPluginHooksFromDir() from src/plugin-sdk/index.ts
# ---------------------------------------------------------------------------

def register_plugin_hooks_from_dir(api: "Any", hooks_dir: str) -> None:
    """Register plugin hooks from a directory of hook files.

    Mirrors TypeScript ``registerPluginHooksFromDir(api, dir)`` from
    ``openclaw/plugin-sdk``.

    Scans ``hooks_dir`` for Python files (non-underscore-prefixed). Each file
    must define a ``register(api)`` function or a module-level ``hook`` dict.
    Hook files are loaded in sorted order.

    Args:
        api: The PluginApi instance passed to the plugin's ``register()`` function.
        hooks_dir: Path to the directory containing hook files (relative to the
            plugin root, or absolute).

    Example::

        def register(api) -> None:
            register_plugin_hooks_from_dir(api, "./hooks")
    """
    import importlib.util
    import os
    import sys
    import logging

    _logger = logging.getLogger(__name__)

    # Resolve hooks_dir relative to the plugin source file
    if not os.path.isabs(hooks_dir):
        plugin_source = getattr(api, "source", None) or ""
        if plugin_source and os.path.isfile(plugin_source):
            base = os.path.dirname(plugin_source)
        elif plugin_source and os.path.isdir(plugin_source):
            base = plugin_source
        else:
            base = os.getcwd()
        hooks_dir = os.path.join(base, hooks_dir)

    hooks_path = os.path.abspath(hooks_dir)
    if not os.path.isdir(hooks_path):
        _logger.debug(f"register_plugin_hooks_from_dir: directory not found: {hooks_path}")
        return

    hook_files = sorted(
        p for p in os.listdir(hooks_path)
        if p.endswith(".py") and not p.startswith("_")
    )

    for filename in hook_files:
        filepath = os.path.join(hooks_path, filename)
        module_name = f"openclaw_plugin_{getattr(api, 'id', 'unknown')}_hook_{filename[:-3]}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            if hasattr(module, "register"):
                module.register(api)
            elif hasattr(module, "hook"):
                hook = module.hook
                if isinstance(hook, dict):
                    events = hook.get("events") or hook.get("event")
                    handler = hook.get("handler")
                    if events and handler:
                        api.register_hook(events if isinstance(events, list) else [events], handler)
        except Exception as exc:
            _logger.warning(f"Failed to load hook file '{filepath}': {exc}")


__all__ = [
    # Channel
    "AllowlistMatch",
    "resolve_allowlist_match_simple",
    "CommandAuthorizer",
    "resolve_command_authorized_from_authorizers",
    "should_ack_reaction",
    "remove_ack_reaction_after_reply",
    "NormalizedLocation",
    "format_location_text",
    "to_location_context",
    "MessagingTarget",
    "normalize_target_id",
    "parse_messaging_target",
    "MentionGateResult",
    "resolve_mention_gating",
    "resolve_sender_label",
    "list_sender_label_candidates",
    "validate_sender_identity",
    "resolve_conversation_label",
    "create_reply_prefix_context",
    "DraftStreamLoop",
    "create_draft_stream_loop",
    "TypingCallbacks",
    "create_typing_callbacks",
    "ChannelEntryMatch",
    "normalize_channel_slug",
    "resolve_channel_entry_match_with_fallback",
    # Auto-reply
    "dispatch_reply_from_config",
    # Config
    "load_config",
    # Plugin types
    "OpenClawPluginApi",
    "OpenClawPluginCommandDefinition",
    "OpenClawPluginDefinition",
    "OpenClawPluginHookOptions",
    "OpenClawPluginService",
    "OpenClawPluginServiceContext",
    "OpenClawPluginToolOptions",
    "PluginHookName",
    "PluginKind",
    "PluginLogger",
    "PluginOrigin",
    "ProviderAuthMethod",
    "ProviderPlugin",
    # Hook runner
    "HookRunner",
    "HookRunnerOptions",
    "create_hook_runner",
    # Registry
    "ConcretePluginApi",
    "PluginCommandRegistration",
    "PluginProviderRegistration",
    "PluginRecord",
    "PluginRegistryData",
    "create_plugin_registry",
    # Manifest
    "PluginManifest",
    "load_plugin_manifest",
    "resolve_plugin_manifest_path",
    # Config state
    "NormalizedPluginsConfig",
    "normalize_plugins_config",
    "resolve_enable_state",
    # Runtime
    "PluginRuntime",
    "create_plugin_runtime",
    "PluginRuntimeChannel",
    "PluginRuntimeConfig",
    "PluginRuntimeMedia",
    # Plugin helpers
    "empty_plugin_config_schema",
    "register_plugin_hooks_from_dir",
]
