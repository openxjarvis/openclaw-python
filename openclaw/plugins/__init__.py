"""Plugin system — type exports and module surface."""
from .plugin_manager import load_gateway_plugins
from .types import PluginRegistry
from .bundled_dir import resolve_bundled_plugins_dir
from .bundled_sources import (
    BundledPluginLookup,
    BundledPluginSource,
    find_bundled_plugin_source,
    resolve_bundled_plugin_sources,
)
from .cli import register_plugin_cli_commands
from .config_schema import (
    EmptyPluginConfigSchema,
    OpenClawPluginConfigSchema,
    SafeParseResult,
    empty_plugin_config_schema,
)
from .config_state import NormalizedPluginsConfig, normalize_plugins_config, resolve_enable_state
from .hooks import HookRunner, HookRunnerOptions, create_hook_runner
from .http_path import normalize_plugin_http_path
from .http_registry import PluginHttpRouteHandler, register_plugin_http_route
from .logger import PluginLogger, create_plugin_loader_logger
from .manifest import PluginManifest, load_plugin_manifest, resolve_plugin_manifest_path
from .manifest_registry import PluginManifestRecord, PluginManifestRegistry, load_plugin_manifest_registry
from .providers import resolve_plugin_providers
from .registry import (
    ConcretePluginApi,
    PluginRecord,
    PluginRegistryApi,
    PluginRegistryData,
    create_empty_plugin_registry,
    create_plugin_registry,
)
from .slots import (
    PluginSlotKey,
    SlotSelectionResult,
    apply_exclusive_slot_selection,
    default_slot_id_for_key,
    slot_key_for_plugin_kind,
)
from .source_display import (
    PluginSourceRoots,
    format_plugin_source_for_table,
    resolve_plugin_source_roots,
    try_relative,
)
from .status import PluginStatusReport, build_plugin_status_report
from .toggle_config import normalize_chat_channel_id, set_plugin_enabled_in_config
from .types import (
    OpenClawPluginApi,
    OpenClawPluginCommandDefinition,
    OpenClawPluginDefinition,
    OpenClawPluginHookOptions,
    OpenClawPluginService,
    OpenClawPluginToolOptions,
    Plugin,
    PluginAPI,
    PluginDiagnostic,
    PluginHookName,
    PluginHookRegistration,
    PluginKind,
    PluginLogger as PluginLoggerType,
    PluginManifest as PluginManifestLegacy,
    PluginOrigin,
    ProviderPlugin,
)
from .uninstall import (
    UninstallActions,
    UninstallPluginResult,
    remove_plugin_from_config,
    resolve_uninstall_directory_target,
    uninstall_plugin,
)
from .update import (
    PluginChannelSyncResult,
    PluginChannelSyncSummary,
    PluginUpdateIntegrityDriftParams,
    PluginUpdateLogger,
    PluginUpdateOutcome,
    PluginUpdateStatus,
    PluginUpdateSummary,
    sync_plugins_for_update_channel,
    update_npm_installed_plugins,
)

__all__ = [
    # Plugin Manager (core loading function)
    "load_gateway_plugins",
    "PluginRegistry",
    # Core types
    "NormalizedPluginsConfig",
    "normalize_plugins_config",
    "resolve_enable_state",
    "HookRunner",
    "HookRunnerOptions",
    "create_hook_runner",
    "PluginManifest",
    "load_plugin_manifest",
    "resolve_plugin_manifest_path",
    "PluginManifestRecord",
    "PluginManifestRegistry",
    "load_plugin_manifest_registry",
    "ConcretePluginApi",
    "PluginRecord",
    "PluginRegistryApi",
    "PluginRegistryData",
    "create_empty_plugin_registry",
    "create_plugin_registry",
    "OpenClawPluginApi",
    "OpenClawPluginCommandDefinition",
    "OpenClawPluginDefinition",
    "OpenClawPluginHookOptions",
    "OpenClawPluginService",
    "OpenClawPluginToolOptions",
    "Plugin",
    "PluginAPI",
    "PluginDiagnostic",
    "PluginHookName",
    "PluginHookRegistration",
    "PluginKind",
    "PluginLogger",
    "PluginLoggerType",
    "PluginManifestLegacy",
    "PluginOrigin",
    "ProviderPlugin",
    # New modules (100% alignment)
    "resolve_bundled_plugins_dir",
    "BundledPluginLookup",
    "BundledPluginSource",
    "find_bundled_plugin_source",
    "resolve_bundled_plugin_sources",
    "register_plugin_cli_commands",
    "EmptyPluginConfigSchema",
    "OpenClawPluginConfigSchema",
    "SafeParseResult",
    "empty_plugin_config_schema",
    "normalize_plugin_http_path",
    "PluginHttpRouteHandler",
    "register_plugin_http_route",
    "create_plugin_loader_logger",
    "resolve_plugin_providers",
    "PluginSlotKey",
    "SlotSelectionResult",
    "apply_exclusive_slot_selection",
    "default_slot_id_for_key",
    "slot_key_for_plugin_kind",
    "PluginSourceRoots",
    "format_plugin_source_for_table",
    "resolve_plugin_source_roots",
    "try_relative",
    "PluginStatusReport",
    "build_plugin_status_report",
    "normalize_chat_channel_id",
    "set_plugin_enabled_in_config",
    "UninstallActions",
    "UninstallPluginResult",
    "remove_plugin_from_config",
    "resolve_uninstall_directory_target",
    "uninstall_plugin",
    "PluginChannelSyncResult",
    "PluginChannelSyncSummary",
    "PluginUpdateIntegrityDriftParams",
    "PluginUpdateLogger",
    "PluginUpdateOutcome",
    "PluginUpdateStatus",
    "PluginUpdateSummary",
    "sync_plugins_for_update_channel",
    "update_npm_installed_plugins",
]
