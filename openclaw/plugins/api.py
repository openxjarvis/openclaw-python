"""Plugin API implementation.

Provides the PluginApi class that is passed to each plugin's register() function.
Matches TypeScript src/plugins/registry.ts createPluginApi() implementation.

When a plugin calls api.register_tool(), api.on(), api.register_channel(), etc.,
the PluginApi collects those registrations into the shared PluginRegistry.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .runtime import PluginRuntime, create_plugin_runtime
from .types import (
    PLUGIN_HOOK_NAMES,
    OpenClawPluginCommandDefinition,
    OpenClawPluginService,
    PluginChannelRegistration,
    PluginCliRegistration,
    PluginCommandRegistration,
    PluginDiagnostic,
    PluginHookRegistration,
    PluginHttpRegistration,
    PluginHttpRouteRegistration,
    PluginLogger,
    PluginProviderRegistration,
    PluginRegistry,
    PluginServiceRegistration,
    PluginToolRegistration,
    ProviderPlugin,
    TypedPluginHookRegistration,
)

logger = logging.getLogger(__name__)

# Reserved command names that plugins cannot override — mirrors TS reserved list
_RESERVED_COMMAND_NAMES = frozenset([
    "help", "status", "reset", "new", "compact", "stop", "context",
    "send", "queue", "model", "think", "verbose", "debug",
])


def _validate_plugin_command_name(name: str) -> dict:
    """Validate a plugin command name.

    Mirrors TypeScript registerPluginCommand() validation in src/plugins/commands.ts.
    Returns dict with 'ok' bool and optional 'error' string.
    """
    import re
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", name):
        return {
            "ok": False,
            "error": (
                f"command name '{name}' is invalid; must start with a letter "
                "and contain only letters, numbers, hyphens, and underscores"
            ),
        }
    if name.lower() in _RESERVED_COMMAND_NAMES:
        return {"ok": False, "error": f"command name '{name}' is reserved"}
    return {"ok": True}


class _PluginApiLogger:
    """Logger that prefixes messages with plugin ID."""

    def __init__(self, plugin_id: str) -> None:
        self._prefix = f"[plugin:{plugin_id}]"
        self._logger = logging.getLogger(f"openclaw.plugins.{plugin_id}")

    def debug(self, message: str) -> None:
        self._logger.debug(f"{self._prefix} {message}")

    def info(self, message: str) -> None:
        self._logger.info(f"{self._prefix} {message}")

    def warn(self, message: str) -> None:
        self._logger.warning(f"{self._prefix} {message}")

    def error(self, message: str) -> None:
        self._logger.error(f"{self._prefix} {message}")


class PluginApi:
    """
    Concrete implementation of the plugin API.

    Passed to each plugin's register() function. All calls are collected
    into the shared PluginRegistry.

    Matches TypeScript OpenClawPluginApi interface.
    """

    def __init__(
        self,
        plugin_id: str,
        plugin_name: str,
        registry: PluginRegistry,
        config: dict[str, Any],
        source: str = "",
        version: str | None = None,
        description: str | None = None,
        plugin_config: dict[str, Any] | None = None,
        workspace_dir: str | None = None,
        runtime: PluginRuntime | None = None,
    ) -> None:
        self.id = plugin_id
        self.name = plugin_name
        self.version = version
        self.description = description
        self.source = source
        self.config = config
        self.plugin_config = plugin_config
        self._registry = registry
        self._workspace_dir = workspace_dir
        self.logger: PluginLogger = _PluginApiLogger(plugin_id)  # type: ignore[assignment]
        self.runtime: PluginRuntime = runtime if runtime is not None else create_plugin_runtime()

    # =========================================================================
    # Tool Registration
    # =========================================================================

    def register_tool(
        self,
        tool: Any = None,
        opts: dict[str, Any] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        schema: dict[str, Any] | None = None,
        parameters: dict[str, Any] | None = None,
        handler: Any = None,
        execute: Any = None,
    ) -> None:
        """Register an agent tool from this plugin.

        Accepts two call styles:

        Legacy style (tool object / factory):
            api.register_tool(tool_obj)
            api.register_tool(tool_obj, opts={"name": "..."})

        Named style (used by channel plugins like feishu):
            api.register_tool(name="my_tool", description="...",
                              schema={...}, handler=async_fn)
        """
        opts = opts or {}

        # Named-kwargs style: wrap handler into a ChannelHandlerTool
        if name or handler or execute:
            tool_name = name or (opts.get("name") or "")
            tool_desc = description or ""
            tool_schema = schema or parameters or {}
            tool_handler = handler or execute
            if tool_name and tool_handler:
                from .channel_tool import ChannelHandlerTool
                wrapped = ChannelHandlerTool(
                    tool_name=tool_name,
                    description=tool_desc,
                    schema=tool_schema,
                    handler=tool_handler,
                )
                reg = PluginToolRegistration(
                    plugin_id=self.id,
                    factory=wrapped,
                    names=[tool_name],
                    optional=False,
                    source=self.source,
                )
                self._registry.tools.append(reg)
                logger.debug(f"[{self.id}] registered channel tool: {tool_name}")
            else:
                logger.warning(f"[{self.id}] register_tool(name=...) called without name or handler")
            return

        names: list[str] = []
        if "names" in opts:
            names = list(opts["names"])
        elif "name" in opts:
            names = [opts["name"]]
        elif isinstance(tool, dict) and tool.get("name"):
            names = [str(tool["name"])]
        elif hasattr(tool, "name") and tool.name:
            names = [tool.name]

        optional = bool(opts.get("optional", False))

        reg = PluginToolRegistration(
            plugin_id=self.id,
            factory=tool,
            names=names,
            optional=optional,
            source=self.source,
        )
        self._registry.tools.append(reg)
        logger.debug(f"[{self.id}] registered tool: {names}")

    # =========================================================================
    # Internal Hook Registration
    # =========================================================================

    def register_hook(
        self,
        events: str | list[str],
        handler: Callable,
        opts: dict[str, Any] | None = None,
    ) -> None:
        """Register an internal hook handler (event-based hooks).

        Mirrors TypeScript registerHook() in src/plugins/registry.ts.

        Args:
            events: Single event key or list of event keys (e.g. "command:new")
            handler: Async handler function
            opts: Options dict with optional keys:
                - 'entry': HookEntry metadata dict
                - 'name': hook display name (required if no entry)
                - 'description': hook description
                - 'register': if False, skip registering with the internal hook
                  system (default: True)
        """
        opts = opts or {}
        if isinstance(events, str):
            events = [events]

        event_list = [e.strip() for e in events if isinstance(e, str) and e.strip()]
        entry = opts.get("entry")
        name = (entry or {}).get("hook", {}).get("name") if isinstance(entry, dict) else None
        name = name or opts.get("name", "").strip() or None

        if not name:
            self._push_diagnostic("warn", "hook registration missing name")
            return

        reg = PluginHookRegistration(
            plugin_id=self.id,
            events=event_list,
            handler=handler,
            entry=entry,
            source=self.source,
        )
        self._registry.hooks.append(reg)

        # Respect opts.register flag — mirrors TS registry.ts
        should_register = opts.get("register", True)
        if not should_register:
            logger.debug(f"[{self.id}] hook '{name}' registered but not wired (register=False)")
            return

        try:
            from ..hooks.internal_hooks import register_internal_hook
            for event_key in event_list:
                register_internal_hook(event_key, handler)
        except Exception as exc:
            logger.warning(f"[{self.id}] failed to register internal hook: {exc}")

        logger.debug(f"[{self.id}] registered hook '{name}' for events: {event_list}")

    # =========================================================================
    # HTTP Registration
    # =========================================================================

    def register_http_handler(self, handler: Callable) -> None:
        """Register an HTTP request handler (catch-all style)."""
        reg = PluginHttpRegistration(
            plugin_id=self.id,
            handler=handler,
            source=self.source,
        )
        self._registry.http_handlers.append(reg)

    def register_http_route(self, path: str, handler: Callable) -> None:
        """Register an HTTP route handler for a specific path."""
        reg = PluginHttpRouteRegistration(
            plugin_id=self.id,
            path=path,
            handler=handler,
            source=self.source,
        )
        self._registry.http_routes.append(reg)

    # =========================================================================
    # Channel Registration
    # =========================================================================

    def register_channel(self, registration: Any) -> None:
        """Register a channel plugin.

        Args:
            registration: ChannelPlugin instance or dict with 'plugin' and optional 'dock'
        """
        if isinstance(registration, dict):
            plugin = registration.get("plugin")
            dock = registration.get("dock")
        else:
            plugin = registration
            dock = None

        if plugin is None:
            logger.warning(f"[{self.id}] register_channel called with None plugin")
            return

        reg = PluginChannelRegistration(
            plugin_id=self.id,
            plugin=plugin,
            dock=dock,
            source=self.source,
        )
        self._registry.channels.append(reg)
        channel_id = getattr(plugin, "id", str(plugin))
        logger.debug(f"[{self.id}] registered channel: {channel_id}")

    # =========================================================================
    # Gateway Method Registration
    # =========================================================================

    def register_gateway_method(self, method: str, handler: Callable) -> None:
        """Register a gateway WebSocket RPC method handler."""
        self._registry.gateway_handlers[method] = handler
        logger.debug(f"[{self.id}] registered gateway method: {method}")

    # =========================================================================
    # CLI Registration
    # =========================================================================

    def register_cli(
        self,
        registrar: Callable,
        opts: dict[str, Any] | None = None,
    ) -> None:
        """Register a CLI command registrar."""
        opts = opts or {}
        commands = list(opts.get("commands") or [])
        reg = PluginCliRegistration(
            plugin_id=self.id,
            register=registrar,
            commands=commands,
            source=self.source,
        )
        self._registry.cli_registrars.append(reg)

    # =========================================================================
    # Service Registration
    # =========================================================================

    def register_service(self, service: OpenClawPluginService) -> None:
        """Register a background service."""
        reg = PluginServiceRegistration(
            plugin_id=self.id,
            service=service,
            source=self.source,
        )
        self._registry.services.append(reg)
        logger.debug(f"[{self.id}] registered service: {service.id}")

    # =========================================================================
    # Provider Registration
    # =========================================================================

    def register_provider(self, provider: ProviderPlugin) -> None:
        """Register a model provider."""
        reg = PluginProviderRegistration(
            plugin_id=self.id,
            provider=provider,
            source=self.source,
        )
        self._registry.providers.append(reg)
        logger.debug(f"[{self.id}] registered provider: {provider.id}")

    # =========================================================================
    # Command Registration
    # =========================================================================

    def register_command(self, command: OpenClawPluginCommandDefinition) -> None:
        """Register a custom command that bypasses the LLM agent.

        Mirrors TypeScript registerCommand() in src/plugins/registry.ts.
        Validates name format and rejects duplicate registrations.
        """
        name = command.name.strip() if command.name else ""
        if not name:
            self._push_diagnostic("error", "command registration missing name")
            return

        result = _validate_plugin_command_name(name)
        if not result["ok"]:
            self._push_diagnostic("error", f"command registration failed: {result['error']}")
            return

        # Check for duplicates across all registered commands
        existing = next(
            (r for r in self._registry.commands if r.command.name == name),
            None,
        )
        if existing is not None:
            self._push_diagnostic(
                "error",
                f"command '{name}' already registered by plugin '{existing.plugin_id}'",
            )
            return

        reg = PluginCommandRegistration(
            plugin_id=self.id,
            command=command,
            source=self.source,
        )
        self._registry.commands.append(reg)
        logger.debug(f"[{self.id}] registered command: {name}")

    # =========================================================================
    # Typed Lifecycle Hook Registration
    # =========================================================================

    def on(
        self,
        hook_name: str,
        handler: Callable,
        opts: dict[str, Any] | None = None,
    ) -> None:
        """Register a typed lifecycle hook handler.

        Args:
            hook_name: One of the 20 PluginHookName values
            handler: Handler function (async or sync depending on hook type)
            opts: Options with optional 'priority' (higher = runs first)
        """
        if hook_name not in PLUGIN_HOOK_NAMES:
            logger.warning(
                f"[{self.id}] unknown hook name '{hook_name}'; "
                f"valid names: {sorted(PLUGIN_HOOK_NAMES)}"
            )
            return

        opts = opts or {}
        priority = int(opts.get("priority", 0))

        reg = TypedPluginHookRegistration(
            plugin_id=self.id,
            hook_name=hook_name,
            handler=handler,
            priority=priority,
            source=self.source,
        )
        self._registry.typed_hooks.append(reg)
        logger.debug(f"[{self.id}] registered typed hook: {hook_name} (priority={priority})")

    # =========================================================================
    # Agent Harness Registration (mirrors TS registerAgentHarness)
    # =========================================================================

    def register_agent_harness(self, harness: Any) -> None:
        """Register an agent harness from this plugin.

        Mirrors TS OpenClawPluginApi.registerAgentHarness().
        The harness must have an 'id' attribute.
        """
        harness_id = getattr(harness, "id", None)
        if not harness_id:
            self._push_diagnostic("error", "register_agent_harness: harness missing 'id'")
            return
        # Set plugin_id if not set
        if not getattr(harness, "plugin_id", None):
            try:
                harness.plugin_id = self.id
            except AttributeError:
                pass
        try:
            from openclaw.agents.harness.registry import register_global_agent_harness
            register_global_agent_harness(harness)
            logger.debug(f"[{self.id}] registered agent harness: {harness_id}")
        except ValueError as e:
            self._push_diagnostic("error", f"register_agent_harness: {e}")

    # =========================================================================
    # Thinking Provider Profile Registration (mirrors TS provider-thinking)
    # =========================================================================

    def register_provider_thinking_profile(self, provider: str, profile: Any) -> None:
        """Register a provider thinking profile from this plugin.

        Mirrors TS registerProviderThinkingProfile().
        profile: ThinkingProviderProfile { binary, xhigh_capable, available_levels, default_level }
        """
        try:
            from openclaw.auto_reply.reply.thinking import register_provider_thinking_profile as _reg
            _reg(provider, profile)
            logger.debug(f"[{self.id}] registered thinking profile for provider: {provider}")
        except Exception as exc:
            self._push_diagnostic("error", f"register_provider_thinking_profile: {exc}")

    # =========================================================================
    # Media / Content Generation Provider Registration
    # =========================================================================

    def register_speech_provider(self, provider: Any) -> None:
        """Register a TTS/speech provider."""
        self._register_media_provider("speech", provider)

    def register_realtime_transcription_provider(self, provider: Any) -> None:
        """Register a real-time transcription provider."""
        self._register_media_provider("realtime_transcription", provider)

    def register_realtime_voice_provider(self, provider: Any) -> None:
        """Register a real-time voice provider."""
        self._register_media_provider("realtime_voice", provider)

    def register_image_generation_provider(self, provider: Any) -> None:
        """Register an image generation provider."""
        self._register_media_provider("image_generation", provider)

    def register_music_generation_provider(self, provider: Any) -> None:
        """Register a music generation provider."""
        self._register_media_provider("music_generation", provider)

    def register_video_generation_provider(self, provider: Any) -> None:
        """Register a video generation provider."""
        self._register_media_provider("video_generation", provider)

    def register_web_search_provider(self, provider: Any) -> None:
        """Register a web search provider."""
        self._register_media_provider("web_search", provider)

    def register_web_fetch_provider(self, provider: Any) -> None:
        """Register a web fetch provider."""
        self._register_media_provider("web_fetch", provider)

    def _register_media_provider(self, kind: str, provider: Any) -> None:
        """Internal: store a typed provider registration in the registry."""
        if not hasattr(self._registry, "media_providers"):
            self._registry.media_providers = {}  # type: ignore[attr-defined]
        providers = self._registry.media_providers  # type: ignore[attr-defined]
        if kind not in providers:
            providers[kind] = []
        providers[kind].append({"plugin_id": self.id, "provider": provider})
        logger.debug(f"[{self.id}] registered {kind} provider")

    # =========================================================================
    # Context / Compaction / Harness Extension Registration
    # =========================================================================

    def register_context_engine(self, engine: Any) -> None:
        """Register a context engine (for RAG/search augmentation)."""
        if not hasattr(self._registry, "context_engines"):
            self._registry.context_engines = []  # type: ignore[attr-defined]
        self._registry.context_engines.append({"plugin_id": self.id, "engine": engine})  # type: ignore[attr-defined]
        logger.debug(f"[{self.id}] registered context engine")

    def register_compaction_provider(self, provider: Any) -> None:
        """Register a custom compaction provider."""
        if not hasattr(self._registry, "compaction_providers"):
            self._registry.compaction_providers = []  # type: ignore[attr-defined]
        self._registry.compaction_providers.append({"plugin_id": self.id, "provider": provider})  # type: ignore[attr-defined]
        logger.debug(f"[{self.id}] registered compaction provider")

    def register_detached_task_runtime(self, runtime: Any) -> None:
        """Register a detached task runtime (mirrors TS registerDetachedTaskRuntime)."""
        if not hasattr(self._registry, "detached_task_runtimes"):
            self._registry.detached_task_runtimes = []  # type: ignore[attr-defined]
        self._registry.detached_task_runtimes.append({"plugin_id": self.id, "runtime": runtime})  # type: ignore[attr-defined]
        logger.debug(f"[{self.id}] registered detached task runtime")

    # =========================================================================
    # Memory Registration (mirrors TS memory capability slots)
    # =========================================================================

    def register_memory_capability(self, capability: Any) -> None:
        """Register a memory capability (backend slot)."""
        if not hasattr(self._registry, "memory_capabilities"):
            self._registry.memory_capabilities = []  # type: ignore[attr-defined]
        self._registry.memory_capabilities.append({"plugin_id": self.id, "capability": capability})  # type: ignore[attr-defined]
        logger.debug(f"[{self.id}] registered memory capability")

    def register_memory_embedding_provider(self, provider: Any) -> None:
        """Register a memory embedding provider."""
        if not hasattr(self._registry, "memory_embedding_providers"):
            self._registry.memory_embedding_providers = []  # type: ignore[attr-defined]
        self._registry.memory_embedding_providers.append({"plugin_id": self.id, "provider": provider})  # type: ignore[attr-defined]
        logger.debug(f"[{self.id}] registered memory embedding provider")

    # =========================================================================
    # Config & Text Transforms (mirrors TS registerConfigMigration / registerTextTransforms)
    # =========================================================================

    def register_config_migration(self, migration: Any) -> None:
        """Register a config migration (version upgrade handler)."""
        if not hasattr(self._registry, "config_migrations"):
            self._registry.config_migrations = []  # type: ignore[attr-defined]
        self._registry.config_migrations.append({"plugin_id": self.id, "migration": migration})  # type: ignore[attr-defined]
        logger.debug(f"[{self.id}] registered config migration")

    def register_text_transforms(self, transforms: Any) -> None:
        """Register text transform functions (mirrors TS registerTextTransforms)."""
        if not hasattr(self._registry, "text_transforms"):
            self._registry.text_transforms = []  # type: ignore[attr-defined]
        self._registry.text_transforms.append({"plugin_id": self.id, "transforms": transforms})  # type: ignore[attr-defined]
        logger.debug(f"[{self.id}] registered text transforms")

    # =========================================================================
    # Utility
    # =========================================================================

    def resolve_path(self, input_path: str) -> str:
        """Resolve a path relative to the plugin's workspace directory."""
        if os.path.isabs(input_path):
            return input_path
        base = self._workspace_dir or str(Path.home())
        return str(Path(base) / input_path)

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _push_diagnostic(self, level: str, message: str) -> None:
        """Push a diagnostic message into the registry. Mirrors TS pushDiagnostic()."""
        self._registry.diagnostics.append(PluginDiagnostic(
            level=level,
            message=message,
            plugin_id=self.id,
            source=self.source,
        ))


def create_plugin_api(
    plugin_id: str,
    plugin_name: str,
    registry: PluginRegistry,
    config: dict[str, Any],
    source: str = "",
    version: str | None = None,
    description: str | None = None,
    plugin_config: dict[str, Any] | None = None,
    workspace_dir: str | None = None,
    runtime: PluginRuntime | None = None,
) -> PluginApi:
    """Factory function to create a PluginApi instance."""
    return PluginApi(
        plugin_id=plugin_id,
        plugin_name=plugin_name,
        registry=registry,
        config=config,
        source=source,
        version=version,
        description=description,
        plugin_config=plugin_config,
        workspace_dir=workspace_dir,
        runtime=runtime,
    )


__all__ = [
    "PluginApi",
    "create_plugin_api",
]
