"""
Channel Manager - Manages channel plugins within Gateway

This implements the TypeScript OpenClaw architecture where:
- Gateway contains ChannelManager
- ChannelManager manages all channel plugins
- Each channel can have its own RuntimeEnv (Agent configuration)
- Channels connect to Agent Runtime via function calls (not HTTP/WebSocket)

Architecture:
    Gateway Server
        └── ChannelManager
                ├── Telegram Channel (plugin)
                ├── Discord Channel (plugin)
                └── ... other channels
"""
from __future__ import annotations

from openclaw.config.paths import resolve_state_dir

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..agents.runtime import AgentRuntime
from ..channels.base import ChannelPlugin, InboundMessage, MessageHandler
from ..events import Event, EventType

# =============================================================================
# Channel Restart Policy — mirrors TS CHANNEL_RESTART_POLICY
# =============================================================================

MAX_RESTART_ATTEMPTS = 10

CHANNEL_RESTART_POLICY = {
    "initial_delay_ms": 5_000,    # 5 s initial backoff
    "max_delay_ms": 300_000,       # 5 min max backoff
    "backoff_factor": 2.0,
}


def _compute_backoff_ms(attempt: int) -> float:
    """Compute backoff delay in ms for a given restart attempt (1-indexed)."""
    initial = CHANNEL_RESTART_POLICY["initial_delay_ms"]
    factor = CHANNEL_RESTART_POLICY["backoff_factor"]
    max_delay = CHANNEL_RESTART_POLICY["max_delay_ms"]
    delay = initial * (factor ** (attempt - 1))
    return min(delay, max_delay)


# Channel event type constants
class ChannelEventType:
    """Channel-specific event types"""
    REGISTERED = "registered"
    UNREGISTERED = "unregistered"
    STARTING = "starting"
    STARTED = "started"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"

logger = logging.getLogger(__name__)


async def _transcribe_audio_attachment(
    content: str,
    mime: str,
    filename: str,
    config: dict,
) -> str | None:
    """Decode a base64 audio attachment and transcribe it using the configured STT provider.

    Mirrors TS media-understanding/audio-preflight.ts transcribeFirstAudio().
    Returns the transcript text or None if unavailable/disabled.

    Provider priority:
    1. config.tools.media.audio (explicit configuration)
    2. OpenAI Whisper (if OPENAI_API_KEY or config.providers.openai.apiKey present)
    3. Groq Whisper (if GROQ_API_KEY present)
    """
    import base64
    import os
    import tempfile

    # Check explicit audio config
    audio_cfg = ((config.get("tools") or {}).get("media") or {}).get("audio") or {}
    if audio_cfg.get("enabled") is False:
        return None

    explicit_provider = (audio_cfg.get("provider") or "").lower()
    explicit_api_key = audio_cfg.get("apiKey") or ""

    # Resolve provider + api key
    api_key: str | None = None
    provider_name: str | None = None

    if explicit_provider and explicit_api_key:
        provider_name = explicit_provider
        api_key = explicit_api_key
    else:
        # Auto-detect from env / global provider config
        openai_key = (
            explicit_api_key
            or ((config.get("providers") or {}).get("openai") or {}).get("apiKey")
            or os.environ.get("OPENAI_API_KEY")
        )
        if openai_key:
            provider_name = "openai"
            api_key = openai_key
        else:
            groq_key = (
                ((config.get("providers") or {}).get("groq") or {}).get("apiKey")
                or os.environ.get("GROQ_API_KEY")
            )
            if groq_key:
                provider_name = "groq"
                api_key = groq_key

    if not provider_name or not api_key:
        # Last resort: check env vars and model provider config
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if not openai_key:
            # Try to get from model providers config (e.g. models.openai.apiKey)
            for section in ("models", "providers"):
                openai_key = (
                    (config.get(section) or {}).get("openai", {}).get("apiKey")
                    or (config.get(section) or {}).get("openai", {}).get("api_key")
                    or ""
                )
                if openai_key:
                    break
        if openai_key:
            provider_name = "openai"
            api_key = openai_key
        else:
            logger.debug(
                "_transcribe_audio_attachment: no STT provider configured "
                "(set OPENAI_API_KEY or add tools.media.audio to openclaw.json)"
            )
            return None

    # Decode base64 content
    try:
        audio_bytes = base64.b64decode(content)
    except Exception as exc:
        logger.warning(f"_transcribe_audio_attachment: base64 decode failed: {exc}")
        return None

    # Determine file extension from MIME or filename
    if filename and "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1]
    else:
        _mime_to_ext: dict[str, str] = {
            "audio/ogg": ".ogg",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "audio/wav": ".wav",
            "audio/webm": ".webm",
            "audio/flac": ".flac",
        }
        ext = _mime_to_ext.get((mime or "").split(";")[0].strip(), ".ogg")

    # Write to temp file and transcribe
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        if provider_name == "openai":
            from openclaw.media_understanding.providers.openai_provider import OpenAIAudioProvider
            stt = OpenAIAudioProvider(api_key=api_key)
        elif provider_name == "groq":
            from openclaw.media_understanding.providers.groq_provider import (
                GroqAudioProvider,  # type: ignore[import]
            )
            stt = GroqAudioProvider(api_key=api_key)
        elif provider_name == "deepgram":
            from openclaw.media_understanding.providers.deepgram_provider import (
                DeepgramProvider,  # type: ignore[import]
            )
            stt = DeepgramProvider(api_key=api_key)
        else:
            return None

        result = await stt.transcribe(tmp_path)
        transcript = (result.get("text") or "").strip()
        return transcript or None

    except Exception as exc:
        logger.warning(f"_transcribe_audio_attachment: transcription error: {exc}")
        return None
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class ChannelState(str, Enum):
    """Channel lifecycle state"""

    REGISTERED = "registered"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class ChannelRuntimeEnv:
    """
    Runtime environment for a channel

    Each channel can have its own:
    - Agent Runtime (or share a default)
    - Configuration
    - Message handler

    This mirrors TypeScript's channelRuntimeEnvs concept.
    """

    channel_id: str
    agent_runtime: AgentRuntime | None = None
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    # Optional custom message handler
    custom_message_handler: MessageHandler | None = None

    # Runtime state
    state: ChannelState = ChannelState.REGISTERED
    error: str | None = None
    started_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return {
            "channel_id": self.channel_id,
            "enabled": self.enabled,
            "state": self.state.value,
            "error": self.error,
            "started_at": self.started_at,
            "has_custom_runtime": self.agent_runtime is not None,
            "has_custom_handler": self.custom_message_handler is not None,
        }


# Type for event listener callbacks
ChannelEventListener = Callable[[str, str, dict[str, Any]], Awaitable[None]]


class ChannelManager:
    """
    Manages channel plugins within Gateway

    This is the central component for channel lifecycle management,
    matching the TypeScript OpenClaw architecture.

    Features:
    - Register channel plugin classes or instances
    - Start/stop channels individually or all at once
    - Each channel can have independent configuration (RuntimeEnv)
    - Automatic message routing to Agent Runtime
    - Event emission for channel state changes

    Example:
        # Create manager with default agent runtime
        manager = ChannelManager(default_runtime=agent_runtime)

        # Register channel classes
        manager.register("telegram", EnhancedTelegramChannel)
        manager.register("discord", DiscordChannel)

        # Configure channels
        manager.configure("telegram", {
            "bot_token": "...",
            "enabled": True
        })

        # Start specific channel
        await manager.start_channel("telegram")

        # Or start all enabled channels
        await manager.start_all()

        # Stop all
        await manager.stop_all()
    """

    def __init__(
        self,
        default_runtime: AgentRuntime | None = None,
        session_manager: Any = None,
        tools: list | None = None,
        system_prompt: str | None = None,
        workspace_dir: Path | None = None,
    ):
        """
        Initialize ChannelManager

        Args:
            default_runtime: Default AgentRuntime for channels that don't have their own
            session_manager: Session manager for creating/retrieving sessions
            tools: List of tools available to the agent
            system_prompt: Optional system prompt (skills, capabilities, etc.)
            workspace_dir: Workspace directory for bootstrap files
        """
        self.default_runtime = default_runtime
        self.session_manager = session_manager
        # Normalise: accept either a ToolRegistry object or a plain list of tools
        # (TS ChannelManager receives a plain array — we mirror that internally)
        if tools is None:
            self.tools = []
        elif hasattr(tools, "list_tools"):
            self.tools = tools.list_tools()
        else:
            self.tools = list(tools)
        self.workspace_dir = workspace_dir or resolve_state_dir() / "workspace"

        # Load bootstrap files and build complete system prompt
        self.system_prompt = self._build_system_prompt_with_bootstrap(system_prompt)

        # Channel plugin classes (for lazy instantiation)
        self._channel_classes: dict[str, type[ChannelPlugin]] = {}

        # Channel plugin instances
        self._channels: dict[str, ChannelPlugin] = {}

        # Runtime environments per channel
        self._runtime_envs: dict[str, ChannelRuntimeEnv] = {}

        # Event listeners
        self._event_listeners: list[ChannelEventListener] = []

        # Restart tracking — mirrors TS restartAttempts Map
        # Key: channel_id (or "channel_id:account_id" for multi-account)
        self._restart_attempts: dict[str, int] = {}

        # Channels that were manually stopped — suppress auto-restart
        # Mirrors TS manuallyStopped Set
        self._manually_stopped: set[str] = set()

        # Per-channel background tasks (for abort on stop)
        self._channel_tasks: dict[str, asyncio.Task] = {}

        # Running state
        self._running = False

        # Per-agent SessionManager registry — mirrors TS per-agent session stores.
        # Key: agent_id (normalised). Created lazily via get_session_manager().
        self._session_managers: dict[str, Any] = {}

        logger.info("ChannelManager initialized")

    def _build_system_prompt_with_bootstrap(
        self,
        base_prompt: str | None = None,
        channel_id: str | None = None,
    ) -> str:
        """
        Build complete system prompt with bootstrap files injected.
        
        Matches TypeScript behavior: loads SOUL.md, IDENTITY.md, BOOTSTRAP.md, etc.
        and injects them into the system prompt.
        
        Args:
            base_prompt: Base system prompt (if any)
            channel_id: The real inbound channel (e.g. "telegram", "feishu").
                        Passed to build_system_prompt_params so runtime_channel
                        is set correctly (mirrors TS attempt.ts runtimeChannel).
            
        Returns:
            Complete system prompt with bootstrap files
        """
        try:
            from ..agents.system_prompt import build_agent_system_prompt
            from ..agents.system_prompt_bootstrap import (
                format_bootstrap_context,
                format_bootstrap_context_string,
                load_bootstrap_files,
            )
            from ..agents.system_prompt_params import build_system_prompt_params

            # Load bootstrap files from workspace
            bootstrap_files = load_bootstrap_files(self.workspace_dir)
            logger.info(f"Loaded {len(bootstrap_files)} bootstrap files from {self.workspace_dir}")

            # Format as string for injection
            bootstrap_context = format_bootstrap_context_string(bootstrap_files)

            # If there's a base prompt, append bootstrap context
            if base_prompt:
                if bootstrap_context:
                    return f"{base_prompt}\n\n{bootstrap_context}"
                return base_prompt

            # Otherwise, build complete system prompt with bootstrap files
            tool_names = [tool.name for tool in self.tools] if self.tools else []

            # Get model name from default_runtime if available
            model_name = "unknown"
            if self.default_runtime and hasattr(self.default_runtime, 'model_str'):
                model_name = self.default_runtime.model_str

            # Get complete runtime parameters (includes timezone, runtime_info, etc.)
            # Load config so the configured timezone (agents.defaults.timezone) is used.
            try:
                from openclaw.config.loader import load_config
                _cfg = load_config()
            except Exception:
                _cfg = None
            prompt_params = build_system_prompt_params(
                config=_cfg,
                workspace_dir=self.workspace_dir,
                runtime={
                    "agent_id": "main",
                    # Use the real channel when known (e.g. "telegram", "feishu"),
                    # otherwise fall back to "gateway" for startup/preview builds.
                    # Mirrors TS attempt.ts: runtimeChannel = normalizeMessageChannel(params.messageChannel).
                    "channel": channel_id or "gateway",
                    "model": model_name,
                    "default_model": model_name,
                }
            )

            # Resolve elevated config for system prompt injection
            _elevated_cfg: dict | None = None
            try:
                _tools_cfg = (_cfg.tools if _cfg and _cfg.tools else None)
                _elevated_obj = _tools_cfg.elevated if _tools_cfg else None
                if _elevated_obj:
                    _elevated_cfg = {
                        "enabled": _elevated_obj.enabled,
                        "allowFrom": (
                            [{"provider": e.provider, "senders": list(e.senders)}
                             for e in (_elevated_obj.allow_from or [])]
                        ),
                    }
            except Exception:
                pass

            # Build complete system prompt with resolved parameters
            system_prompt = build_agent_system_prompt(
                workspace_dir=self.workspace_dir,
                tool_names=tool_names,
                prompt_mode="full",
                runtime_info=prompt_params["runtime_info"],  # Complete runtime info
                user_timezone=prompt_params["user_timezone"],  # Resolved timezone
                context_files=format_bootstrap_context([
                    bf for bf in bootstrap_files
                    if "(File" not in bf.content
                ]) if bootstrap_files else None,
                elevated_config=_elevated_cfg,
                # current_elevated_level is per-session; inject at turn time below
            )

            logger.info(f"Built system prompt with bootstrap files ({len(system_prompt)} chars)")
            logger.info(f"Using timezone: {prompt_params['user_timezone']}")
            return system_prompt

        except Exception as e:
            logger.error(f"Failed to build system prompt with bootstrap: {e}", exc_info=True)
            # Fallback to base prompt or default
            return base_prompt or "You are a personal assistant running inside OpenClaw."

    # =========================================================================
    # Registration
    # =========================================================================

    def register(
        self,
        channel_id: str,
        channel_class: type[ChannelPlugin],
        config: dict[str, Any] | None = None,
        runtime: AgentRuntime | None = None,
    ) -> None:
        """
        Register a channel plugin class

        The channel will be instantiated lazily when needed.

        Args:
            channel_id: Unique identifier for the channel
            channel_class: Channel plugin class
            config: Optional initial configuration
            runtime: Optional custom AgentRuntime for this channel
        """
        self._channel_classes[channel_id] = channel_class

        # Create runtime environment
        self._runtime_envs[channel_id] = ChannelRuntimeEnv(
            channel_id=channel_id,
            agent_runtime=runtime,
            config=config or {},
            enabled=True,
        )

        logger.info(f"Registered channel class: {channel_id}")
        asyncio.create_task(self._emit_event(ChannelEventType.REGISTERED, channel_id, {}))

    def register_instance(
        self,
        channel: ChannelPlugin,
        config: dict[str, Any] | None = None,
        runtime: AgentRuntime | None = None,
    ) -> None:
        """
        Register a channel plugin instance directly

        Args:
            channel: Channel plugin instance
            config: Optional initial configuration
            runtime: Optional custom AgentRuntime for this channel
        """
        if not channel.id:
            raise ValueError("Channel must have an ID")

        self._channels[channel.id] = channel

        # Create runtime environment
        self._runtime_envs[channel.id] = ChannelRuntimeEnv(
            channel_id=channel.id,
            agent_runtime=runtime,
            config=config or {},
            enabled=True,
        )

        logger.info(f"Registered channel instance: {channel.id}")
        asyncio.create_task(self._emit_event("registered", channel.id, {}))

    def unregister(self, channel_id: str) -> bool:
        """
        Unregister a channel

        The channel will be stopped if running.

        Args:
            channel_id: Channel to unregister

        Returns:
            True if unregistered, False if not found
        """
        if channel_id not in self._channel_classes and channel_id not in self._channels:
            return False

        # Remove from all registries
        self._channel_classes.pop(channel_id, None)
        self._channels.pop(channel_id, None)
        self._runtime_envs.pop(channel_id, None)

        logger.info(f"Unregistered channel: {channel_id}")
        asyncio.create_task(self._emit_event(ChannelEventType.UNREGISTERED, channel_id, {}))
        return True

    # =========================================================================
    # Configuration
    # =========================================================================

    def configure(
        self,
        channel_id: str,
        config: dict[str, Any],
        merge: bool = True,
    ) -> None:
        """
        Configure a channel

        Args:
            channel_id: Channel to configure
            config: Configuration dictionary
            merge: If True, merge with existing config; otherwise replace
        """
        if channel_id not in self._runtime_envs:
            # Auto-create runtime env if channel class exists
            if channel_id in self._channel_classes:
                self._runtime_envs[channel_id] = ChannelRuntimeEnv(channel_id=channel_id)
            else:
                raise ValueError(f"Channel not registered: {channel_id}")

        env = self._runtime_envs[channel_id]

        if merge:
            env.config.update(config)
        else:
            env.config = config

        # Handle special config keys
        if "enabled" in config:
            env.enabled = config["enabled"]

        logger.debug(f"Configured channel {channel_id}: {config}")

    def set_default_runtime(self, runtime: AgentRuntime) -> None:
        """
        Replace the default AgentRuntime used for all channels without an explicit
        per-channel runtime.  Mirrors TS ChannelManager.setDefaultRuntime().

        Args:
            runtime: New default AgentRuntime instance.
        """
        self.default_runtime = runtime
        logger.info("Default channel runtime updated")

    def set_runtime(self, channel_id: str, runtime: AgentRuntime) -> None:
        """
        Set custom AgentRuntime for a channel

        Args:
            channel_id: Channel ID
            runtime: Custom AgentRuntime
        """
        if channel_id not in self._runtime_envs:
            raise ValueError(f"Channel not registered: {channel_id}")

        self._runtime_envs[channel_id].agent_runtime = runtime
        logger.info(f"Set custom runtime for channel: {channel_id}")

    def set_message_handler(self, channel_id: str, handler: MessageHandler) -> None:
        """
        Set custom message handler for a channel

        This overrides the default Agent Runtime handler.

        Args:
            channel_id: Channel ID
            handler: Custom message handler
        """
        if channel_id not in self._runtime_envs:
            raise ValueError(f"Channel not registered: {channel_id}")

        self._runtime_envs[channel_id].custom_message_handler = handler
        logger.info(f"Set custom message handler for channel: {channel_id}")

    # =========================================================================
    # Lifecycle Management
    # =========================================================================

    async def start_channel(self, channel_id: str, account_id: str | None = None) -> bool:
        """
        Start a specific channel (supports multi-account)
        
        If channel has a gateway adapter, will start individual account(s).
        Otherwise, falls back to legacy single-instance start().

        Args:
            channel_id: Channel to start
            account_id: Optional specific account ID to start (multi-account mode)

        Returns:
            True if started successfully
        """
        env = self._runtime_envs.get(channel_id)
        if not env:
            logger.error(f"Channel not found: {channel_id}")
            return False

        if not env.enabled:
            logger.info(f"Channel disabled, skipping: {channel_id}")
            return False

        # Get or create channel instance
        channel = self._get_or_create_channel(channel_id)
        if not channel:
            return False

        try:
            env.state = ChannelState.STARTING
            await self._emit_event(ChannelEventType.STARTING, channel_id, {})

            # Check if channel has gateway adapter (multi-account mode)
            if hasattr(channel, 'gateway') and channel.gateway:
                # Multi-account mode
                adapter = channel.gateway
                
                # Get account IDs to start
                account_ids = []
                if account_id:
                    account_ids = [account_id]
                elif hasattr(channel, 'config') and hasattr(channel.config, 'list_account_ids'):
                    account_ids = channel.config.list_account_ids(env.config)
                else:
                    account_ids = ["default"]
                
                # Start each account
                for acc_id in account_ids:
                    # Resolve account config
                    account = None
                    if hasattr(channel, 'config') and hasattr(channel.config, 'resolve_account'):
                        account = channel.config.resolve_account(env.config, acc_id)
                    
                    # Create status snapshot
                    from openclaw.channels.base import ChannelAccountSnapshot
                    
                    status = ChannelAccountSnapshot(
                        account_id=acc_id,
                        enabled=True,
                        configured=True,
                        running=False,
                    )
                    
                    def get_status():
                        return status
                    
                    def set_status(new_status: ChannelAccountSnapshot):
                        nonlocal status
                        status = new_status
                    
                    # Create abort signal (simple version)
                    class AbortSignal:
                        def __init__(self):
                            self.aborted = False
                        
                        def abort(self):
                            self.aborted = True
                    
                    abort_signal = AbortSignal()
                    
                    # Get channel runtime if available
                    channel_runtime = None
                    try:
                        from openclaw.plugins.runtime import create_plugin_runtime
                        plugin_runtime = create_plugin_runtime()
                        channel_runtime = plugin_runtime.channel
                    except Exception:
                        pass
                    
                    # Start account
                    await adapter.start_account(
                        cfg=env.config,
                        account_id=acc_id,
                        account=account,
                        runtime=env,
                        abort_signal=abort_signal,
                        get_status=get_status,
                        set_status=set_status,
                        log=logger,
                        channel_runtime=channel_runtime,
                    )
                
                env.state = ChannelState.RUNNING
                env.error = None
                
                from datetime import datetime
                env.started_at = datetime.now().isoformat()
                
                logger.info(f"✅ Channel started (multi-account): {channel_id}")
                await self._emit_event(ChannelEventType.STARTED, channel_id, {})
                return True
            
            else:
                # Legacy mode: single-instance start()
                handler = self._create_message_handler(channel_id)
                channel.set_message_handler(handler)
                
                # Start channel with config
                await channel.start(env.config)
                
                env.state = ChannelState.RUNNING
                env.error = None
                
                from datetime import datetime
                env.started_at = datetime.now().isoformat()
                
                logger.info(f"✅ Channel started: {channel_id}")
                await self._emit_event(ChannelEventType.STARTED, channel_id, {})
                return True

        except Exception as e:
            env.state = ChannelState.ERROR
            env.error = str(e)
            logger.error(f"❌ Failed to start channel {channel_id}: {e}")
            await self._emit_event(ChannelEventType.ERROR, channel_id, {"error": str(e)})
            return False

    async def _start_channel_with_backoff(self, channel_id: str) -> None:
        """Start a channel and automatically restart it with backoff on failure.

        Mirrors TS startChannelInternal() restart logic in server-channels.ts.
        Runs as a background task; exits when manually stopped or max retries reached.
        """
        rkey = channel_id
        self._manually_stopped.discard(rkey)
        self._restart_attempts.pop(rkey, None)

        while True:
            success = await self.start_channel(channel_id)

            # Wait for channel to finish (it runs until stopped or errors)
            channel = self._channels.get(channel_id)
            if channel and hasattr(channel, "_running"):
                # Poll until channel stops running or is manually stopped
                try:
                    while channel._running and rkey not in self._manually_stopped:
                        await asyncio.sleep(1.0)
                except asyncio.CancelledError:
                    break

            if rkey in self._manually_stopped:
                logger.debug(f"Channel {channel_id} manually stopped — not restarting")
                break

            attempt = self._restart_attempts.get(rkey, 0) + 1
            self._restart_attempts[rkey] = attempt

            if attempt > MAX_RESTART_ATTEMPTS:
                logger.error(
                    f"Channel {channel_id} giving up after {MAX_RESTART_ATTEMPTS} restart attempts"
                )
                break

            delay_ms = _compute_backoff_ms(attempt)
            delay_s = delay_ms / 1000.0
            logger.info(
                f"Channel {channel_id} auto-restart attempt {attempt}/{MAX_RESTART_ATTEMPTS} "
                f"in {delay_s:.1f}s"
            )

            try:
                await asyncio.sleep(delay_s)
            except asyncio.CancelledError:
                break

            if rkey in self._manually_stopped:
                break

    async def stop_channel(self, channel_id: str) -> bool:
        """
        Stop a specific channel

        Args:
            channel_id: Channel to stop

        Returns:
            True if stopped successfully
        """
        # Mark as manually stopped to suppress auto-restart
        self._manually_stopped.add(channel_id)
        self._restart_attempts.pop(channel_id, None)

        # Cancel the background task if running
        task = self._channel_tasks.pop(channel_id, None)
        if task and not task.done():
            task.cancel()

        channel = self._channels.get(channel_id)
        if not channel:
            logger.warning(f"Channel not found or not started: {channel_id}")
            return False

        env = self._runtime_envs.get(channel_id)
        if env:
            env.state = ChannelState.STOPPING

        await self._emit_event(ChannelEventType.STOPPING, channel_id, {})

        try:
            await channel.stop()

            if env:
                env.state = ChannelState.STOPPED

            logger.info(f"Channel stopped: {channel_id}")
            await self._emit_event(ChannelEventType.STOPPED, channel_id, {})
            return True

        except Exception as e:
            if env:
                env.state = ChannelState.ERROR
                env.error = str(e)
            logger.error(f"Failed to stop channel {channel_id}: {e}")
            await self._emit_event(ChannelEventType.ERROR, channel_id, {"error": str(e)})
            return False

    async def restart_channel(self, channel_id: str) -> bool:
        """
        Restart a channel

        Args:
            channel_id: Channel to restart

        Returns:
            True if restarted successfully
        """
        await self.stop_channel(channel_id)
        return await self.start_channel(channel_id)

    async def start_all(self) -> dict[str, bool]:
        """Start all enabled channels, with auto-restart backoff in background.

        Mirrors TS startChannels() — launches each enabled channel in a background
        task that automatically restarts with exponential backoff on failure.

        Returns:
            Dict mapping channel_id to initial start success status
        """
        self._running = True
        results: dict[str, bool] = {}

        for channel_id, env in self._runtime_envs.items():
            if not env.enabled:
                continue

            # Start the channel once to get immediate feedback
            ok = await self.start_channel(channel_id)
            results[channel_id] = ok

            # Launch background task for auto-restart management
            # The task monitors the channel and restarts it with backoff
            if ok:
                self._manually_stopped.discard(channel_id)
                task = asyncio.create_task(
                    self._monitor_channel_for_restart(channel_id),
                    name=f"channel-monitor-{channel_id}",
                )
                self._channel_tasks[channel_id] = task

        return results

    async def _monitor_channel_for_restart(self, channel_id: str) -> None:
        """Background task: monitor a running channel and restart it with backoff.

        Runs continuously until the channel is manually stopped or max retries exceeded.
        Mirrors TS trackedPromise restart loop in server-channels.ts.
        """
        rkey = channel_id
        while True:
            channel = self._channels.get(channel_id)

            # Wait until channel stops running
            try:
                while (
                    channel is not None
                    and getattr(channel, "_running", False)
                    and rkey not in self._manually_stopped
                ):
                    await asyncio.sleep(2.0)
                    channel = self._channels.get(channel_id)
            except asyncio.CancelledError:
                return

            if rkey in self._manually_stopped:
                return

            # Channel stopped unexpectedly — attempt restart with backoff
            attempt = self._restart_attempts.get(rkey, 0) + 1
            self._restart_attempts[rkey] = attempt

            if attempt > MAX_RESTART_ATTEMPTS:
                logger.error(
                    f"Channel {channel_id}: giving up after {MAX_RESTART_ATTEMPTS} restart attempts"
                )
                return

            delay_ms = _compute_backoff_ms(attempt)
            delay_s = delay_ms / 1000.0
            logger.info(
                f"Channel {channel_id}: auto-restart attempt {attempt}/{MAX_RESTART_ATTEMPTS} "
                f"in {delay_s:.1f}s"
            )

            env = self._runtime_envs.get(channel_id)
            if env:
                env.state = ChannelState.STOPPED

            try:
                await asyncio.sleep(delay_s)
            except asyncio.CancelledError:
                return

            if rkey in self._manually_stopped:
                return

            await self.start_channel(channel_id)

    async def stop_all(self) -> None:
        """Stop all running channels, cancelling auto-restart tasks."""
        self._running = False

        # Cancel all background monitor tasks
        for channel_id, task in list(self._channel_tasks.items()):
            if not task.done():
                task.cancel()
        self._channel_tasks.clear()

        for channel_id, channel in list(self._channels.items()):
            if channel.is_running():
                await self.stop_channel(channel_id)

    # =========================================================================
    # Query Methods
    # =========================================================================

    def get_channel(self, channel_id: str) -> ChannelPlugin | None:
        """Get channel instance by ID"""
        return self._channels.get(channel_id)

    def get_runtime_env(self, channel_id: str) -> ChannelRuntimeEnv | None:
        """Get runtime environment for a channel"""
        return self._runtime_envs.get(channel_id)

    def get_runtime(self, channel_id: str) -> AgentRuntime | None:
        """
        Get AgentRuntime for a channel

        Returns channel-specific runtime if set, otherwise default runtime.
        """
        env = self._runtime_envs.get(channel_id)
        if env and env.agent_runtime:
            return env.agent_runtime
        return self.default_runtime

    def get_runtime_for_agent(self, agent_id: str) -> AgentRuntime | None:
        """Return the AgentRuntime for a specific agent_id.

        Checks ``_runtime_envs`` for a channel-env keyed by agent_id first,
        then falls back to the default runtime.  This mirrors TS
        ``getChannelRuntime(agentId)`` used after ``resolveAgentRoute``.
        """
        env = self._runtime_envs.get(agent_id)
        if env and env.agent_runtime:
            return env.agent_runtime
        return self.default_runtime

    def get_session_manager(self, agent_id: str) -> Any:
        """Return (and lazily create) the per-agent SessionManager.

        Mirrors TS per-agent session store isolation: each agentId has its own
        sessions.json at ``~/.openclaw/agents/<agentId>/sessions/``.

        Falls back to the default ``self.session_manager`` if it was provided
        for the same agent_id; creates a fresh one otherwise.
        """
        from openclaw.routing.session_key import normalize_agent_id as _norm_agent

        norm = _norm_agent(agent_id)

        # Return cached instance
        if norm in self._session_managers:
            return self._session_managers[norm]

        # Re-use the injected default session_manager if it matches
        if (
            self.session_manager is not None
            and getattr(self.session_manager, "agent_id", None) == norm
        ):
            self._session_managers[norm] = self.session_manager
            return self.session_manager

        # Create a new one with workspace resolved per-agent (mirrors resolveAgentWorkspaceDir)
        try:
            from openclaw.agents.session import SessionManager
            from openclaw.agents.agent_scope import resolve_agent_workspace_dir
            from openclaw.config.loader import load_config as _lc
            cfg = _lc()
            workspace = resolve_agent_workspace_dir(cfg, norm)
            sm = SessionManager(workspace_dir=workspace, agent_id=norm)
            self._session_managers[norm] = sm
            logger.info("Created SessionManager for agent_id=%s workspace=%s", norm, workspace)
            return sm
        except Exception as exc:
            logger.warning(
                "Failed to create SessionManager for agent_id=%s, falling back to default: %s",
                norm,
                exc,
            )
            return self.session_manager

    def list_channels(self) -> list[str]:
        """List all registered channel IDs"""
        all_ids = set(self._channel_classes.keys())
        all_ids.update(self._channels.keys())
        return sorted(all_ids)

    def list_running(self) -> list[str]:
        """List running channel IDs"""
        return [ch_id for ch_id, ch in self._channels.items() if ch.is_running()]

    def list_enabled(self) -> list[str]:
        """List enabled channel IDs"""
        return [ch_id for ch_id, env in self._runtime_envs.items() if env.enabled]

    def get_runtime_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return runtime status snapshot for all channels.

        Mirrors TS getRuntimeSnapshot() in server-channels.ts.
        """
        snapshot: dict[str, dict[str, Any]] = {}
        for channel_id in self.list_channels():
            env = self._runtime_envs.get(channel_id)
            channel = self._channels.get(channel_id)
            snap: dict[str, Any] = {
                "channel_id": channel_id,
                "enabled": env.enabled if env else False,
                "configured": True,
                "running": channel.is_running() if channel else False,
                "state": env.state.value if env else "registered",
                "last_error": env.error if env else None,
                "started_at": env.started_at if env else None,
                "reconnect_attempts": self._restart_attempts.get(channel_id, 0),
                "manually_stopped": channel_id in self._manually_stopped,
            }
            snapshot[channel_id] = snap
        return snapshot

    def get_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return channel status snapshot for all channels.
        
        This method directly accesses _runtime_envs to ensure we capture
        all running channels even if registration state is inconsistent.
        
        Returns dict with channel_id -> snapshot dict containing:
        - label: channel label
        - enabled: whether enabled
        - running: whether running
        - connected: whether connected (from channel if available)
        - healthy: whether healthy
        - state: current state
        """
        snapshot: dict[str, dict[str, Any]] = {}
        for channel_id, env in self._runtime_envs.items():
            channel = self._channels.get(channel_id)
            
            # Get connection status from channel if available
            connected = False
            healthy = False
            if channel:
                if hasattr(channel, "is_connected"):
                    connected = channel.is_connected()
                elif hasattr(channel, "_connected"):
                    connected = getattr(channel, "_connected", False)
                
                # Healthy if running and connected (or just running if no connection status)
                healthy = channel.is_running() and (connected if hasattr(channel, "is_connected") else True)
            
            snap: dict[str, Any] = {
                "label": channel_id.capitalize() if channel_id else "Unknown",
                "enabled": env.enabled,
                "running": channel.is_running() if channel else False,
                "connected": connected,
                "healthy": healthy,
                "state": env.state.value,
            }
            snapshot[channel_id] = snap
        return snapshot

    def get_all_channels(self) -> list[dict[str, Any]]:
        """
        Get all channels with full details
        
        Returns list of channel info dicts with:
        - id: channel ID
        - label: channel label
        - running: whether channel is running
        - connected: whether channel is connected
        - healthy: whether channel is healthy
        - state: channel state
        - enabled: whether channel is enabled
        - capabilities: channel capabilities
        """
        channels = []
        for channel_id in self.list_channels():
            channel = self._channels.get(channel_id)
            env = self._runtime_envs.get(channel_id)

            channel_info = {
                "id": channel_id,
                "enabled": env.enabled if env else False,
                "state": env.state.value if env else "unknown",
            }

            if channel:
                channel_info.update({
                    "label": channel.label,
                    "running": channel.is_running(),
                    "connected": channel.is_connected(),
                    "healthy": channel.is_healthy(),
                    "capabilities": channel.capabilities.model_dump(),
                })
            else:
                # Channel class registered but not instantiated
                channel_info.update({
                    "label": channel_id,
                    "running": False,
                    "connected": False,
                    "healthy": False,
                    "capabilities": {},
                })

            channels.append(channel_info)

        return channels

    def get_status(self, channel_id: str) -> dict[str, Any] | None:
        """Get channel status"""
        channel = self._channels.get(channel_id)
        env = self._runtime_envs.get(channel_id)

        if not env:
            return None

        result = env.to_dict()

        if channel:
            result.update(
                {
                    "running": channel.is_running(),
                    "connected": channel.is_connected(),
                    "healthy": channel.is_healthy(),
                    "label": channel.label,
                    "capabilities": channel.capabilities.model_dump(),
                }
            )

            metrics = channel.get_metrics()
            if metrics:
                result["metrics"] = metrics.to_dict()

        return result

    def get_all_status(self) -> dict[str, Any]:
        """Get status of all channels"""
        channels = {}
        for channel_id in self.list_channels():
            status = self.get_status(channel_id)
            if status:
                channels[channel_id] = status

        return {
            "running": self._running,
            "total": len(channels),
            "running_count": len(self.list_running()),
            "enabled_count": len(self.list_enabled()),
            "channels": channels,
        }

    # =========================================================================
    # Event System
    # =========================================================================

    def add_event_listener(self, listener: ChannelEventListener) -> None:
        """Add event listener for channel events"""
        self._event_listeners.append(listener)

    def remove_event_listener(self, listener: ChannelEventListener) -> None:
        """Remove event listener"""
        if listener in self._event_listeners:
            self._event_listeners.remove(listener)

    async def _emit_event(
        self,
        event_type_str: str,
        channel_id: str,
        data: dict[str, Any],
    ) -> None:
        """
        Emit channel event to all listeners using unified Event system

        Args:
            event_type_str: String event type (e.g., "registered", "started")
            channel_id: Channel ID
            data: Event data
        """
        # Map string event type to EventType enum
        event_type_map = {
            "registered": EventType.CHANNEL_REGISTERED,
            "unregistered": EventType.CHANNEL_UNREGISTERED,
            "starting": EventType.CHANNEL_STARTING,
            "started": EventType.CHANNEL_STARTED,
            "ready": EventType.CHANNEL_READY,
            "stopping": EventType.CHANNEL_STOPPING,
            "stopped": EventType.CHANNEL_STOPPED,
            "error": EventType.CHANNEL_ERROR,
        }

        event_type = event_type_map.get(event_type_str, EventType.CHANNEL_ERROR)

        # Create unified Event
        event = Event(type=event_type, source="channel-manager", channel_id=channel_id, data=data)

        # Notify legacy listeners (for backward compatibility)
        for listener in self._event_listeners:
            try:
                await listener(event_type_str, channel_id, data)
            except Exception as e:
                logger.error(f"Event listener error: {e}")

    # =========================================================================
    # Internal Methods
    # =========================================================================

    def _get_or_create_channel(self, channel_id: str) -> ChannelPlugin | None:
        """Get existing channel or create from class"""
        if channel_id in self._channels:
            return self._channels[channel_id]

        if channel_id in self._channel_classes:
            channel_class = self._channel_classes[channel_id]
            channel = channel_class()
            self._channels[channel_id] = channel
            return channel

        return None

    def _create_message_handler(self, channel_id: str) -> MessageHandler:
        """
        Create message handler for a channel

        This is where the magic happens:
        - Builds complete MsgContext from InboundMessage
        - Normalizes and finalizes context (sender metadata, mention gating, etc.)
        - Gets the appropriate AgentRuntime (channel-specific or default)
        - Creates a handler that processes messages through the Agent
        - Sends responses back via the channel
        """

        async def handler(message: InboundMessage) -> None:
            env = self._runtime_envs.get(channel_id)

            # Check for custom handler first
            if env and env.custom_message_handler:
                await env.custom_message_handler(message)
                return

            # Get channel for sending response
            channel = self._channels.get(channel_id)
            if not channel:
                logger.error(f"Channel not found: {channel_id}")
                return

            logger.info(f"📨 [{channel_id}] Message from {message.sender_name}: {message.text}")

            try:
                # Build MsgContext from InboundMessage
                from openclaw.auto_reply.inbound_context import (
                    MsgContext,
                    finalize_inbound_context,
                )

                # ----------------------------------------------------------------
                # Routing: resolve agent + session key via bindings (mirrors TS
                # resolveAgentRoute called from every channel handler).
                # ----------------------------------------------------------------
                from openclaw.routing.resolve_route import resolve_agent_route
                from openclaw.routing.session_key import resolve_thread_session_keys
                from openclaw.config.loader import load_config as _load_cfg

                _cfg = _load_cfg()

                # Normalise peer_kind — TS uses "direct" / "group" / "channel"
                peer_kind = (
                    "group" if message.chat_type in ("group", "channel")
                    else "direct"
                )

                _route = resolve_agent_route(
                    cfg=_cfg,
                    channel=channel_id,
                    account_id=message.account_id,
                    peer={"kind": peer_kind, "id": str(message.chat_id)},
                )
                agent_id = _route.agent_id
                session_key = _route.session_key
                main_session_key = _route.main_session_key

                # Feishu broadcast override: if metadata carries a specific broadcast
                # agent_id + session_key, use those instead of the routed values.
                # Mirrors TS buildBroadcastSessionKey + broadcast dispatch in feishu/bot.ts.
                _bcast_agent_id = (message.metadata or {}).get("feishu_broadcast_agent_id")
                _bcast_session_key = (message.metadata or {}).get("feishu_broadcast_session_key")
                if _bcast_agent_id:
                    agent_id = str(_bcast_agent_id)
                    if _bcast_session_key:
                        session_key = str(_bcast_session_key)
                    else:
                        # Reconstruct session key with the target agent ID prefix
                        from openclaw.channels.feishu.bot import build_broadcast_session_key as _bsk
                        session_key = _bsk(session_key, _route.agent_id, agent_id)

                # Fail-closed guard (mirrors TS): if this is a named (non-default)
                # account and no binding matched, drop rather than using default agent.
                # "default" account always passes — only explicitly-named accounts
                # (e.g. "bot-2", a second bot token) are dropped when they have no binding.
                if (
                    message.account_id
                    and message.account_id != "default"
                    and _route.matched_by == "default"
                ):
                    _default_acct = getattr(
                        getattr(_cfg, "agents", None), "defaults", None
                    )
                    _default_acct_id = (
                        getattr(_default_acct, "accountId", None) if _default_acct else None
                    ) or ""
                    if message.account_id != _default_acct_id:
                        logger.debug(
                            "[%s] dropping message: named account '%s' has no binding (matched_by=default)",
                            channel_id,
                            message.account_id,
                        )
                        return

                # Thread suffix: apply when the message is inside a topic/thread.
                # Feishu: feishu_root_id indicates a topic thread.
                # Telegram: a thread_id meta key (set by forum topic routing).
                _meta = message.metadata or {}
                _thread_id = (
                    _meta.get("thread_id")
                    or _meta.get("feishu_root_id")
                    or None
                )
                if _thread_id:
                    thread_keys = resolve_thread_session_keys(
                        base_session_key=session_key,
                        thread_id=str(_thread_id),
                    )
                    session_key = thread_keys.get("session_key", session_key)

                # Use per-agent runtime (falls back to default if no binding override)
                runtime = self.get_runtime_for_agent(agent_id)
                if not runtime:
                    runtime = self.get_runtime(channel_id)
                if not runtime:
                    logger.error("No AgentRuntime available for channel=%s agent=%s", channel_id, agent_id)
                    return

                logger.info("[%s] Built session key: %s (agent=%s, matched_by=%s)", channel_id, session_key, agent_id, _route.matched_by)

                # Propagate command metadata from InboundMessage (set by command_dispatcher.py)
                _meta = message.metadata or {}
                _command_authorized = bool(_meta.get("command_authorized", False))
                _command_body = _meta.get("command_body") or message.text or ""
                _command_source = _meta.get("command_source") or (
                    "native" if _meta.get("event_type") == "command" else None
                )

                ctx = MsgContext(
                    Body=message.text or "",
                    RawBody=message.text or "",
                    BodyForAgent=_command_body or message.text or "",
                    CommandBody=_command_body or message.text or "",
                    SessionKey=session_key,  # Use proper session key
                    From=message.sender_id,
                    To=channel_id,
                    ChatType=message.chat_type,
                    SenderName=message.sender_name,
                    SenderId=message.sender_id,
                    ConversationLabel=f"{channel_id}:{message.chat_id}",
                    OriginatingChannel=channel_id,
                    OriginatingTo=message.chat_id,
                    CommandAuthorized=_command_authorized,
                    CommandSource=_command_source,  # type: ignore[call-arg]
                )

                # Add reply context if present
                if message.reply_to:
                    ctx.ReplyToId = message.reply_to

                # Add media metadata if present (legacy URL-based path)
                if message.metadata:
                    media_url = message.metadata.get("file_url") or message.metadata.get("photo_url")
                    if media_url:
                        ctx.MediaUrls = [media_url]
                        ctx.MediaUrl = media_url

                # Finalize context (applies normalization, sender metadata, etc.)
                ctx = finalize_inbound_context(ctx)

                logger.debug(f"[{channel_id}] Context finalized: BodyForAgent length={len(ctx.BodyForAgent or '')}, ChatType={ctx.ChatType}")

                # Get or create session using per-agent SessionManager
                session = None
                session_workspace: str | None = None
                workspace_root: str | None = str(resolve_state_dir() / "workspace")
                _sandbox_enabled = False
                _ws_access = "none"
                _session_mgr = self.get_session_manager(agent_id)
                if _session_mgr:
                    session = _session_mgr.get_or_create_session_by_key(session_key)
                    logger.info(f"[{channel_id}] Session created/retrieved: key={session_key}, uuid={session.session_id}")

                    # Resolve workspace — mirrors TS behaviour:
                    # - Agent workspace root = _session_mgr.workspace_dir (never session.workspace_dir
                    #   which may carry stale cwd from old JSONL files).
                    # - Sandbox OFF or workspaceAccess=rw → cwd = workspace root directly.
                    # - Sandbox ON + workspaceAccess≠rw → cwd = per-session subdir inside
                    #   sandboxes root (~/.openclaw/sandboxes/<slug>/), NOT inside workspace/.
                    _workspace_root = (
                        Path(_session_mgr.workspace_dir)
                        if hasattr(_session_mgr, "workspace_dir") and _session_mgr.workspace_dir
                        else resolve_state_dir() / "workspace"
                    )
                    workspace_root = str(_workspace_root)

                    _sandbox_enabled = False
                    _sb_cfg = None
                    try:
                        # Load config fresh — self.config is not always set on ChannelManager
                        try:
                            from openclaw.config.loader import load_config as _load_cfg_sb
                            _cfg_sb = _load_cfg_sb()
                        except Exception:
                            _cfg_sb = getattr(self, "config", None)
                        if isinstance(_cfg_sb, dict):
                            _sb_cfg = (
                                _cfg_sb.get("agents", {})
                                .get("defaults", {})
                                .get("sandbox")
                            ) or _cfg_sb.get("sandbox")
                        elif _cfg_sb is not None:
                            _agents = getattr(_cfg_sb, "agents", None)
                            _defaults = getattr(_agents, "defaults", None) if _agents else None
                            _sb_cfg = getattr(_defaults, "sandbox", None) if _defaults else None
                            if not _sb_cfg:
                                _sb_cfg = getattr(_cfg_sb, "sandbox", None)
                        if _sb_cfg:
                            _sb_mode = (
                                _sb_cfg.get("mode", "off")
                                if isinstance(_sb_cfg, dict)
                                else getattr(_sb_cfg, "mode", "off")
                            )
                            _sandbox_enabled = str(_sb_mode).strip().lower() not in ("off", "")
                    except Exception:
                        pass

                    _ws_access = "none"
                    if _sb_cfg and _sandbox_enabled:
                        _ws_access = str(
                            _sb_cfg.get("workspaceAccess", "none")
                            if isinstance(_sb_cfg, dict)
                            else getattr(_sb_cfg, "workspaceAccess", None)
                            or getattr(_sb_cfg, "workspace_access", "none")
                        ).strip().lower()

                    if _sandbox_enabled and _ws_access != "rw":
                        # Per-session sandbox subdir lives in ~/.openclaw/sandboxes/ (NOT workspace/).
                        # Mirrors TS: DEFAULT_SANDBOX_WORKSPACE_ROOT = path.join(STATE_DIR, "sandboxes")
                        from openclaw.config.paths import resolve_default_sandbox_workspace_root
                        from openclaw.agents.session_workspace import (
                            resolve_session_workspace_dir,
                            resolve_sandbox_scope_key,
                        )
                        _sandbox_root_cfg = (
                            _sb_cfg.get("workspaceRoot")
                            if isinstance(_sb_cfg, dict)
                            else getattr(_sb_cfg, "workspaceRoot", None)
                        )
                        _sandbox_root = Path(_sandbox_root_cfg).expanduser() if _sandbox_root_cfg else resolve_default_sandbox_workspace_root()
                        _scope = str(
                            _sb_cfg.get("scope", "session")
                            if isinstance(_sb_cfg, dict)
                            else getattr(_sb_cfg, "scope", "session")
                        )
                        _scope_key = resolve_sandbox_scope_key(_scope, session_key)
                        if _scope == "shared":
                            session_workspace = str(_sandbox_root)
                        else:
                            session_workspace = str(resolve_session_workspace_dir(
                                workspace_root=_sandbox_root,
                                session_key=_scope_key,
                            ))
                        logger.info(f"[{channel_id}] Sandbox session workspace (ws_access={_ws_access}, scope={_scope}): {session_workspace}")

                        # Bootstrap sandbox workspace — mirrors TS ensureSandboxWorkspaceLayout:
                        # copies SOUL.md, USER.md, TOOLS.md etc. from agent workspace (write-if-missing).
                        # Without this, a fresh sandbox dir has no soul/config → broken agent.
                        try:
                            from openclaw.agents.sandbox.workspace import ensure_sandbox_workspace
                            _skip_bs = False
                            try:
                                _cfg_bs = _cfg_sb if _cfg_sb is not None else {}
                                _skip_bs = bool(
                                    _cfg_bs.get("agents", {}).get("defaults", {}).get("skipBootstrap", False)
                                    if isinstance(_cfg_bs, dict)
                                    else getattr(
                                        getattr(getattr(_cfg_bs, "agents", None), "defaults", None),
                                        "skipBootstrap", False,
                                    )
                                )
                            except Exception:
                                pass
                            await ensure_sandbox_workspace(
                                workspace_dir=session_workspace,
                                seed_from=workspace_root,
                                skip_bootstrap=_skip_bs,
                            )
                            logger.debug(
                                "[%s] Sandbox workspace bootstrapped from %s → %s",
                                channel_id, workspace_root, session_workspace,
                            )
                        except Exception as _boot_exc:
                            logger.debug("Sandbox workspace bootstrap (non-fatal): %s", _boot_exc)

                        # Sync skills into sandbox — mirrors TS syncSkillsToWorkspace.
                        # Copies all skill dirs (bundled + managed + workspace) into
                        # <sandbox>/skills/ so the agent can read SKILL.md from its CWD.
                        try:
                            from openclaw.agents.skills.workspace import sync_skills_to_workspace
                            await sync_skills_to_workspace(
                                source_workspace_dir=workspace_root,
                                target_workspace_dir=session_workspace,
                                config=_cfg_sb,
                            )
                            logger.debug(
                                "[%s] Skills synced to sandbox %s",
                                channel_id, session_workspace,
                            )
                        except Exception as _sync_exc:
                            logger.debug("Sandbox skills sync (non-fatal): %s", _sync_exc)
                    else:
                        session_workspace = workspace_root
                        if _sandbox_enabled:
                            logger.info(f"[{channel_id}] Sandbox on, workspaceAccess=rw → using workspace root: {session_workspace}")
                        else:
                            logger.info(f"[{channel_id}] Using workspace root as cwd (sandbox off): {session_workspace}")

                    # Record inbound session metadata (mirrors TS recordSessionMetaFromInbound).
                    # Uses preserve-activity semantics: does NOT refresh updatedAt so that
                    # idle-reset logic continues to track actual agent-turn timestamps.
                    try:
                        _session_mgr.update_session_meta_preserve_activity(session_key, {
                            "lastChannel": channel_id,
                            "chatType": message.chat_type,
                        })
                        # updateLastRoute: write delivery context to mainSessionKey for DMs
                        # so that outbound reply routing can always find the right target
                        # even when dmScope != "main" (mirrors TS updateLastRoute).
                        if message.chat_type == "direct" and main_session_key != session_key:
                            _session_mgr.update_session_meta_preserve_activity(main_session_key, {
                                "lastChannel": channel_id,
                                "lastTo": str(message.chat_id),
                                "lastAccountId": message.account_id,
                            })
                    except Exception as _meta_err:
                        logger.debug("[%s] Failed to record session metadata: %s", channel_id, _meta_err)

                # Use BodyForAgent (properly formatted with sender metadata for groups)
                message_text = ctx.BodyForAgent or ctx.Body

                # ---------------------------------------------------------------
                # Universal /new and /reset detection — mirrors TS initSessionState.
                # Covers Feishu, Discord, Slack, and any channel whose native
                # command handlers pass /new through as plain text.
                # (Telegram native command handlers already dispatch
                # BARE_SESSION_RESET_PROMPT directly, so they never reach here
                # with message_text="/new".)
                # ---------------------------------------------------------------
                import re as _re_mod
                _RESET_CMD_RE = _re_mod.compile(r"^/(new|reset)(?:\s+([\s\S]*))?$", _re_mod.IGNORECASE)
                _reset_match = _RESET_CMD_RE.match(message_text.strip()) if message_text else None
                _reset_triggered = False
                if _reset_match:
                    _reset_reason = "new" if (_reset_match.group(1) or "").lower() == "new" else "reset"
                    _post_reset_body = (_reset_match.group(2) or "").strip()
                    try:
                        from openclaw.gateway.api.sessions_methods import SessionsResetMethod
                        from openclaw.gateway.handlers import BARE_SESSION_RESET_PROMPT
                        _rm = SessionsResetMethod()
                        _reset_result = await _rm.execute(
                            connection=None,
                            params={"key": session_key, "reason": _reset_reason, "archiveTranscript": True},
                        )
                        _reset_triggered = True
                        # Refresh session after reset
                        if _session_mgr:
                            session = _session_mgr.get_or_create_session_by_key(session_key)

                        # Mirrors TS sendResetSessionNotice / buildResetSessionNoticeText
                        _notice_model = ""
                        try:
                            _notice_model = (getattr(runtime, "model_str", "") or "")
                        except Exception:
                            pass
                        _notice_text = f"✅ New session started · model: {_notice_model}" if _notice_model else "✅ New session started"
                        try:
                            await channel.send_text(
                                target=str(message.chat_id),
                                text=_notice_text,
                            )
                        except Exception:
                            pass

                        # Replace message body with BARE_SESSION_RESET_PROMPT (or post-reset args)
                        message_text = _post_reset_body if _post_reset_body else BARE_SESSION_RESET_PROMPT
                        logger.info("[%s] /new or /reset detected — session reset, sending greeting prompt", channel_id)
                    except Exception as _reset_err:
                        logger.warning("[%s] Universal reset failed: %s", channel_id, _reset_err)

                # ---------------------------------------------------------------
                # isFirstTurnInSession — mirrors TS:
                #   const isFirstTurnInSession = isNewSession || !currentSystemSent;
                # On first turn after a session reset (systemSent=False), rebuild the
                # system prompt from disk so SOUL.md / USER.md / MEMORY.md are fresh.
                # Mirrors TS resolveBootstrapContextForRun + clearBootstrapSnapshot.
                # ---------------------------------------------------------------
                _is_first_turn = False
                try:
                    _se = _session_mgr.get_session_entry(session_key) if _session_mgr else None
                    _system_sent = getattr(_se, "systemSent", None) if _se else None
                    _is_first_turn = (_system_sent is False) or _reset_triggered
                except Exception:
                    _is_first_turn = _reset_triggered

                # Check bootstrap stale flag (set by SessionsResetMethod)
                from openclaw.agents.bootstrap_cache import consume_bootstrap_stale
                _bootstrap_stale = consume_bootstrap_stale(session_key)
                _is_first_turn = _is_first_turn or _bootstrap_stale

                # Per-first-turn bootstrap rebuild — reads workspace files fresh from disk.
                # This ensures SOUL.md / USER.md / MEMORY.md changes are picked up after /new.
                _fresh_system_prompt: str | None = None
                if _is_first_turn:
                    try:
                        _fresh_system_prompt = self._build_system_prompt_with_bootstrap(channel_id=channel_id)
                        logger.info("[%s] Rebuilt system prompt from disk on first turn (session reset)", channel_id)
                        # Mark systemSent=True so subsequent turns use cached prompt
                        if _session_mgr:
                            try:
                                _session_mgr.update_session_meta_preserve_activity(session_key, {"systemSent": True})
                            except Exception:
                                pass
                    except Exception as _bsp_err:
                        logger.warning("[%s] bootstrap rebuild failed: %s", channel_id, _bsp_err)

                logger.info(f"[{channel_id}] Starting dispatch with {len(self.tools)} tools")

                from openclaw.auto_reply.reply.typing import create_typing_controller

                # Wire up typing indicator (mirrors TS bot-message-dispatch.ts)
                _typing_target = str(message.chat_id)
                _typing_message_id: str | None = getattr(message, "message_id", None)

                async def _send_typing() -> None:
                    if hasattr(channel, "send_typing"):
                        try:
                            await channel.send_typing(
                                _typing_target, message_id=_typing_message_id
                            )
                        except Exception:
                            pass

                async def _stop_typing() -> None:
                    if hasattr(channel, "stop_typing"):
                        try:
                            await channel.stop_typing(
                                _typing_target, message_id=_typing_message_id
                            )
                        except Exception:
                            pass

                def _on_typing_cleanup() -> None:
                    asyncio.ensure_future(_stop_typing())

                typing_ctrl = create_typing_controller(
                    on_reply_start=_send_typing,
                    on_cleanup=_on_typing_cleanup,
                )
                await typing_ctrl.on_reply_start()
                await typing_ctrl.start_typing_loop()

                # Keep the raw send_typing fn on ctx so that followup_runner can
                # create a *fresh* TypingController for each queued turn — mirrors
                # TS createFollowupRunner receiving the TypingController reference.

                # Build image data URLs from inbound attachments, and transcribe audio.
                # Mirrors TS bot-message-context.ts allMedia / audio-preflight flow.
                inbound_images: list[str] | None = None
                inbound_attachments = getattr(message, "attachments", None) or []
                for att in inbound_attachments:
                    # att is a ChatAttachment Pydantic model — access via attributes, not .get()
                    if isinstance(att, dict):
                        mime = att.get("mime_type") or att.get("mimeType") or ""
                        content = att.get("content") or ""
                        att_type = att.get("type") or ""
                        filename = att.get("filename") or att.get("file_name") or ""
                    else:
                        mime = att.mime_type or ""
                        content = att.content or ""
                        att_type = att.type or ""
                        filename = getattr(att, "filename", "") or getattr(att, "file_name", "") or ""
                    if content and (att_type == "image" or mime.startswith("image/")):
                        if inbound_images is None:
                            inbound_images = []
                        inbound_images.append(f"data:{mime or 'image/jpeg'};base64,{content}")
                    elif content and (att_type in ("voice", "audio") or mime.startswith("audio/")):
                        # Audio/voice attachment — transcribe and replace placeholder text.
                        # Mirrors TS audio-preflight.ts transcribeFirstAudio().
                        try:
                            _audio_cfg: dict = {}
                            try:
                                from openclaw.config.loader import load_config as _lc
                                _raw_cfg = _lc()
                                if hasattr(_raw_cfg, "model_dump"):
                                    _audio_cfg = _raw_cfg.model_dump()
                                elif isinstance(_raw_cfg, dict):
                                    _audio_cfg = _raw_cfg
                            except Exception:
                                pass
                            audio_transcript = await _transcribe_audio_attachment(
                                content=content,
                                mime=mime,
                                filename=filename or "voice.ogg",
                                config=_audio_cfg,
                            )
                            if audio_transcript:
                                message_text = audio_transcript
                                logger.info(
                                    f"[{channel_id}] Audio transcription: {len(audio_transcript)} chars"
                                )
                        except Exception as _audio_err:
                            logger.warning(
                                f"[{channel_id}] Audio transcription failed: {_audio_err}"
                            )

                # --- Telegram streaming preview (draft stream) ---
                # Create a TelegramDraftStream for Telegram channels so that
                # each text_delta from the agent is shown as a live preview
                # (editMessageText throttled at 1s). Mirrors TS draft-stream.ts.
                _draft_stream = None
                _stream_callback = None
                _feishu_dispatcher = None

                _status_reactions = None
                _reasoning_level = "off"
                _reasoning_draft = None
                _reasoning_callback = None

                # block_send_fn — sends each agent reasoning block (text before a tool
                # call) as a separate visible message. Mirrors TS sendBlockReply.
                # Enabled for Telegram DMs and Feishu thread replies.
                _block_send_fn = None

                if hasattr(channel, "_app") and channel._app is not None:
                    # Telegram channel
                    
                    # ✅ FIX: Resolve preview streaming mode (mirrors TS L182)
                    # Determines if draft/preview streaming is enabled at all.
                    # Default: "partial" (streaming enabled)
                    # Values: "off" | "partial" | "block"
                    _stream_mode = "partial"  # Default per TS resolveTelegramPreviewStreamMode
                    _tg_cfg_stream = getattr(channel, "_config", None) or {}
                    
                    # Check telegram.streaming (preferred) or legacy telegram.streamMode
                    if "streaming" in _tg_cfg_stream:
                        _streaming_val = _tg_cfg_stream["streaming"]
                        if _streaming_val == False or _streaming_val == "off":
                            _stream_mode = "off"
                        elif _streaming_val == True or _streaming_val in ("partial", "progress"):
                            _stream_mode = "partial"
                        elif _streaming_val == "block":
                            _stream_mode = "block"
                    elif "streamMode" in _tg_cfg_stream:
                        _stream_mode = _tg_cfg_stream.get("streamMode", "partial")
                    
                    _preview_streaming_enabled = (_stream_mode != "off")
                    
                    logger.info(
                        f"[telegram] Preview streaming: mode={_stream_mode}, "
                        f"enabled={_preview_streaming_enabled}, chat={message.chat_id}"
                    )
                    
                    # Block reply configuration — mirrors TS bot-message-dispatch.ts L169-184.
                    #
                    # TS has TWO separate concepts:
                    # 1. accountBlockStreamingEnabled (L171-174) — simple config check,
                    #    gates draft stream creation
                    # 2. disableBlockStreaming (L305-313) — complex logic passed as a
                    #    reply option to control block message behavior
                    #
                    # Python must separate these two concepts correctly.
                    _account_block_streaming_enabled = False
                    _force_block_for_reasoning = False
                    _block_streaming_enabled = False  # for reply option (disableBlockStreaming)
                    _should_send_tool_summaries = True
                    _block_cfg = None
                    try:
                        from openclaw.auto_reply.reply.block_streaming import resolve_block_streaming_config
                        
                        # Resolve reasoning level
                        _reasoning_level_for_block = "off"
                        try:
                            from openclaw.channels.telegram.reasoning import resolve_reasoning_level
                            _tg_cfg_for_r = getattr(channel, "_config", None) or {}
                            _reasoning_level_for_block = resolve_reasoning_level(_tg_cfg_for_r)
                        except Exception:
                            pass
                        
                        _force_block_for_reasoning = (_reasoning_level_for_block == "on")
                        
                        _block_cfg = resolve_block_streaming_config(
                            cfg=_cfg,
                            channel="telegram",
                            account_id=message.account_id,
                        )
                        
                        # TS L171-174: accountBlockStreamingEnabled is JUST the config
                        # boolean — telegram.blockStreaming or agents.defaults.blockStreamingDefault.
                        # resolve_block_streaming_config already resolves this correctly.
                        _account_block_streaming_enabled = _block_cfg.enabled
                        
                        # shouldSendToolSummaries only affects the reply option
                        # (disableBlockStreaming), NOT draft stream creation.
                        _should_send_tool_summaries = (
                            getattr(message, "chat_type", "") != "group"
                            and getattr(message, "command_source", "") != "native"
                        )
                    except Exception as _block_cfg_err:
                        logger.debug("Failed to resolve block streaming config: %s", _block_cfg_err)
                    
                    # Draft stream creation gate — mirrors TS L183-184:
                    #   canStreamAnswerDraft = previewStreamingEnabled
                    #     && !accountBlockStreamingEnabled
                    #     && !forceBlockStreamingForReasoning
                    #
                    # accountBlockStreamingEnabled is the RAW config value
                    # (NOT conditioned on shouldSendToolSummaries).
                    _can_stream_answer_draft = (
                        _preview_streaming_enabled and 
                        not _account_block_streaming_enabled and
                        not _force_block_for_reasoning
                    )
                    
                    # TS disableBlockStreaming (L305-313):
                    #   !previewStreamingEnabled → true (disable)
                    #   forceBlockStreamingForReasoning → false (don't disable)
                    #   explicit config → inverse of config value
                    #   canStreamAnswerDraft → true (disable -- draft takes over)
                    #   else → undefined
                    # Python _block_streaming_enabled is the inverse (True = block streaming ON)
                    if not _preview_streaming_enabled:
                        _block_streaming_enabled = False
                    elif _force_block_for_reasoning:
                        _block_streaming_enabled = True
                    elif _account_block_streaming_enabled:
                        _block_streaming_enabled = _should_send_tool_summaries
                    elif _can_stream_answer_draft:
                        _block_streaming_enabled = False
                    else:
                        _block_streaming_enabled = False
                    
                    # Archived preview tracking (mirrors TS archivedAnswerPreviews / archivedReasoningPreviewIds)
                    _archived_answer_previews: list[dict] = []
                    _archived_reasoning_preview_ids: list[int] = []

                    def _on_superseded_answer_preview(info: dict) -> None:
                        _archived_answer_previews.append(info)

                    def _on_superseded_reasoning_preview(info: dict) -> None:
                        mid = info.get("message_id")
                        if mid is not None:
                            _archived_reasoning_preview_ids.append(mid)

                    if _can_stream_answer_draft:
                        try:
                            from openclaw.channels.telegram.draft_stream import TelegramDraftStream
                            _is_dm_tg = getattr(message, "chat_type", "") == "direct"
                            _draft_stream = TelegramDraftStream(
                                bot_api=channel._app.bot,
                                chat_id=int(message.chat_id),
                                reply_to_message_id=int(message.message_id) if message.message_id else None,
                                throttle_ms=1000,
                                min_initial_chars=30,
                                is_dm=_is_dm_tg,
                                on_superseded_preview=_on_superseded_answer_preview,
                            )
                            # .update() is synchronous — create_task handles the async
                            # send/edit internally via asyncio.create_task inside the class.
                            _stream_callback = _draft_stream.update
                            logger.info(
                                f"[telegram] Answer draft stream ENABLED: stream_mode={_stream_mode}, "
                                f"account_block_streaming={_account_block_streaming_enabled}, "
                                f"force_for_reasoning={_force_block_for_reasoning}, chat={message.chat_id}"
                            )
                        except Exception as _ds_err:
                            logger.debug("Failed to create TelegramDraftStream: %s", _ds_err)
                            _draft_stream = None
                            _stream_callback = None
                    else:
                        # Answer draft disabled: either streamMode=off, block streaming, or reasoning force
                        _draft_stream = None
                        _stream_callback = None
                        logger.info(
                            f"[telegram] Answer draft stream DISABLED: stream_mode={_stream_mode}, "
                            f"preview_enabled={_preview_streaming_enabled}, "
                            f"account_block_streaming={_account_block_streaming_enabled}, "
                            f"force_for_reasoning={_force_block_for_reasoning}, chat={message.chat_id}"
                        )

                    # Setup block_send_fn when block streaming is enabled
                    if _block_streaming_enabled:
                        _tg_chat_id_for_block = int(message.chat_id)
                        # Tracks whether the first block has been sent — used to add a
                        # human-like delay between consecutive blocks (mirrors TS getHumanDelay).
                        _block_sent_flags = [False]

                        async def _tg_block_send(
                            text: str,
                            _cid=_tg_chat_id_for_block,
                            _flags=_block_sent_flags,
                            _meta=message.metadata,
                        ) -> None:
                            try:
                                if _flags[0]:
                                    # TS Telegram path does NOT pass humanDelay to the reply
                                    # dispatcher, so inter-block delay defaults to 0.  Only
                                    # apply a delay when _human_delay_config is present in
                                    # message metadata (configurable per-channel).
                                    _hd_cfg = (_meta or {}).get("_human_delay_config")
                                    if _hd_cfg:
                                        import random as _random
                                        _lo = _hd_cfg.get("min_s", 0.8)
                                        _hi = _hd_cfg.get("max_s", 2.5)
                                        await asyncio.sleep(_random.uniform(_lo, _hi))
                                _flags[0] = True
                                await channel.send_text(
                                    target=str(_cid),
                                    text=text,
                                )
                                # Reset the draft stream so the NEXT text segment starts
                                # a fresh streaming preview, rather than editing the
                                # preview that corresponds to the just-sent block message.
                                # Mirrors TS forceNewMessage() called after onAssistantMessageStart.
                                # Note: In block streaming mode, _draft_stream is None for answer,
                                # but reasoning_draft may exist independently.
                                if _draft_stream is not None and hasattr(_draft_stream, "force_new_message"):
                                    _draft_stream.force_new_message()
                            except Exception as _e:
                                logger.debug("[telegram] block_send_fn error: %s", _e)

                        _block_send_fn = _tg_block_send

                    # Reasoning lane — second TelegramDraftStream for <think> content.
                    # Mirrors TS reasoning-lane-coordinator.ts + lane-delivery.ts.
                    _reasoning_level = "off"
                    _reasoning_draft = None
                    _reasoning_callback = None
                    try:
                        _tg_cfg_r = getattr(channel, "_config", None) or {}
                        from openclaw.channels.telegram.reasoning import resolve_reasoning_level
                        _reasoning_level = resolve_reasoning_level(_tg_cfg_r)
                        if _reasoning_level == "stream":
                            from openclaw.channels.telegram.draft_stream import TelegramDraftStream as _TDS2
                            _reasoning_draft = _TDS2(
                                bot_api=channel._app.bot,
                                chat_id=int(message.chat_id),
                                reply_to_message_id=None,
                                throttle_ms=1000,
                                on_superseded_preview=_on_superseded_reasoning_preview,
                            )
                            _reasoning_callback = _reasoning_draft.update
                    except Exception as _rl_err:
                        logger.debug("Failed to create reasoning draft stream: %s", _rl_err)
                        _reasoning_level = "off"
                        _reasoning_draft = None
                        _reasoning_callback = None

                    # Status reactions — emoji on user's inbound message showing agent state.
                    # Mirrors TS createStatusReactionController in bot-message-context.ts.
                    try:
                        _tg_cfg = getattr(channel, "_config", None) or {}
                        _sr_cfg = (
                            _tg_cfg.get("messages", {}).get("statusReactions")
                            or _tg_cfg.get("statusReactions")
                            or {}
                        )
                        if _sr_cfg.get("enabled") and message.message_id:
                            from openclaw.channels.telegram.status_reactions import TelegramStatusReactions
                            _status_reactions = TelegramStatusReactions(
                                bot=channel._app.bot,
                                chat_id=int(message.chat_id),
                                message_id=int(message.message_id),
                                emojis=_sr_cfg.get("emojis") or {},
                            )
                            # Set queued immediately on message arrival
                            await _status_reactions.set_queued()
                    except Exception as _sr_err:
                        logger.debug("Failed to create TelegramStatusReactions: %s", _sr_err)
                        _status_reactions = None

                elif (
                    channel.__class__.__name__ == "SlackChannel"
                    and getattr(channel, "_native_streaming_enabled", False)
                ):
                    # Slack native AI streaming — chat.startStream / appendStream / stopStream.
                    # Requires thread_ts (the ts of the inbound message, which becomes the thread).
                    # For DMs, recipient_user_id is also required by the Slack API.
                    # Mirrors TS src/slack/streaming.ts + dispatch.ts deliverWithStreaming.
                    try:
                        thread_ts = (
                            message.metadata.get("thread_ts")
                            or message.message_id
                        ) if message.metadata else message.message_id

                        if thread_ts:
                            from openclaw.channels.slack.streaming import SlackDraftStream as _SDS
                            _is_dm_slack = getattr(message, "chat_type", "") == "direct"
                            _slack_draft = _SDS(
                                client=channel._app.client,
                                channel=str(message.chat_id),
                                thread_ts=str(thread_ts),
                                team_id=getattr(channel, "_team_id", None),
                                user_id=str(message.sender_id) if _is_dm_slack else None,
                            )
                            _draft_stream = _slack_draft
                            _stream_callback = _slack_draft.update
                        else:
                            logger.debug("[slack-stream] no thread_ts available — streaming disabled")
                    except Exception as _ss_err:
                        logger.debug("Failed to create SlackDraftStream: %s", _ss_err)
                        _draft_stream = None
                        _stream_callback = None

                elif channel.__class__.__name__ == "FeishuChannel":
                    # Feishu channel — FeishuReplyDispatcher handles typing indicator +
                    # CardKit streaming card. Mirrors TS extensions/feishu/src/reply-dispatcher.ts.
                    try:
                        import time as _time_module
                        from openclaw.channels.feishu.accounts import get_default_account as _gda
                        from openclaw.channels.feishu.client import create_feishu_client as _cfc
                        from openclaw.channels.feishu.reply_dispatcher import create_feishu_reply_dispatcher as _cfrd

                        _feishu_cfg = getattr(channel, "_cfg", None)
                        if _feishu_cfg:
                            _feishu_account = _gda(_feishu_cfg)
                            if _feishu_account:
                                _feishu_client = _cfc(_feishu_account)
                                _is_p2p = getattr(message, "chat_type", "") == "direct"

                                # Detect Feishu topic-thread context: messages inside a thread
                                # carry thread_id in metadata. CardKit streaming is not supported
                                # in topic threads — we fall back to block reply messages instead.
                                _feishu_thread_id = None
                                if message.metadata:
                                    _feishu_thread_id = (
                                        message.metadata.get("thread_id")
                                        or message.metadata.get("root_id")
                                    )
                                _feishu_thread_reply = bool(_feishu_thread_id)

                                _feishu_dispatcher = _cfrd(
                                    _feishu_client,
                                    _feishu_account,
                                    chat_id=str(message.chat_id),
                                    is_p2p=_is_p2p,
                                    reply_to_message_id=str(message.message_id) if message.message_id else None,
                                    message_timestamp=_time_module.time(),
                                    thread_reply=_feishu_thread_reply,
                                    root_id=_feishu_thread_id,
                                    session_workspace=session_workspace,
                                )
                                # Start typing indicator immediately (emoji reaction on inbound msg)
                                await _feishu_dispatcher.start_typing()

                                if _feishu_thread_reply:
                                    # Thread reply mode: CardKit not supported. Wire block_send_fn
                                    # so each agent reasoning step is sent as a plain message.
                                    # This mirrors TS sending intermediate blocks in DM mode.
                                    _draft_stream = _feishu_dispatcher
                                    _feishu_disp_ref = _feishu_dispatcher

                                    async def _feishu_thread_block_send(text: str, _d=_feishu_disp_ref) -> None:
                                        try:
                                            await _d.send_block(text)
                                        except Exception as _fe:
                                            logger.debug("[feishu] thread block_send_fn error: %s", _fe)

                                    _block_send_fn = _feishu_thread_block_send
                                else:
                                    # Start streaming card — creates a CardKit card with streaming_mode:true
                                    # and sends it as an interactive message.
                                    streaming_ok = await _feishu_dispatcher.start_streaming()
                                    _draft_stream = _feishu_dispatcher
                                    if streaming_ok:
                                        # Wire incremental updates: each text_delta feeds into the card
                                        _stream_callback = _feishu_dispatcher.stream_update
                                    else:
                                        # CardKit streaming failed — log a helpful warning so the
                                        # issue is diagnosable. Common causes: missing cardkit:card
                                        # scope, CardKit API not enabled for the app, or domain mismatch.
                                        if getattr(_feishu_dispatcher, "streaming_enabled", False):
                                            logger.warning(
                                                "[feishu] CardKit streaming start failed for account '%s'. "
                                                "Falling back to non-streaming delivery. "
                                                "Check that the 'cardkit:card' scope is enabled in your "
                                                "Feishu app console (Developer Platform → Permissions → "
                                                "Search 'cardkit'). The '⏳ Thinking...' card will not "
                                                "appear until streaming is working.",
                                                getattr(_feishu_account, "account_id", "unknown"),
                                            )
                    except Exception as _fs_err:
                        logger.debug("Failed to create FeishuReplyDispatcher: %s", _fs_err)
                        _feishu_dispatcher = None
                        _draft_stream = None

                # Build a channel send function for the ReplyContext.
                # Feishu: routes text through FeishuReplyDispatcher.send() so it respects
                #   render_mode (auto/card/raw) and handles chunk splitting.
                # Telegram: passes buttons=kwarg for InlineKeyboardMarkup support.
                # Other channels: standard channel.send_text() path.
                # Resolve Telegram forum topic thread_id from the inbound message metadata.
                # Mirrors TS buildTelegramThreadReplyParams() used in get-reply-run.ts.
                _tg_thread_id: int | None = None
                if message.metadata:
                    _raw_tid = message.metadata.get("message_thread_id") or message.metadata.get("thread_id")
                    if isinstance(_raw_tid, int) and _raw_tid > 0:
                        _tg_thread_id = _raw_tid

                async def _channel_send(
                    text: str | None,
                    target: Any,
                    *,
                    reply_to: Any = None,
                    media_url: str | None = None,
                    media_type: str | None = None,
                    buttons: Any = None,
                    **_kw: Any,
                ) -> None:
                    if media_url:
                        await channel.send_media(
                            target=target,
                            media_url=media_url,
                            media_type=media_type or "document",
                            reply_to=reply_to,
                        )
                    elif text:
                        if _feishu_dispatcher is not None:
                            # Feishu fallback path (streaming not active): route through
                            # dispatcher.send() which respects render_mode + stops typing.
                            await _feishu_dispatcher.send(text, buttons=buttons)
                        else:
                            # Telegram and other channels
                            try:
                                await channel.send_text(
                                    target=target,
                                    text=text,
                                    reply_to=reply_to,
                                    buttons=buttons,
                                    session_key=session_key,
                                    message_thread_id=_tg_thread_id,
                                )
                            except TypeError:
                                # Fallback: channel.send_text doesn't accept all kwargs
                                await channel.send_text(
                                    target=target,
                                    text=text,
                                    reply_to=reply_to,
                                )

                # Resolve session_id for active-run tracking
                session_id_for_run = session.session_id if session else session_key

                # Resolve queue settings using the full 5-level priority chain (Gap 8)
                from openclaw.auto_reply.reply.queue import resolve_queue_settings
                _session_entry_for_queue = None
                if _session_mgr:
                    try:
                        _session_entry_for_queue = _session_mgr.get_session_entry(session_key)
                    except Exception:
                        pass
                _channel_id_for_queue = getattr(message, "channel", None) or getattr(self, "channel_id", None)
                _queue_settings = resolve_queue_settings(
                    _cfg,
                    channel=str(_channel_id_for_queue) if _channel_id_for_queue else None,
                    session_entry=_session_entry_for_queue,
                )

                # Build ReplyContext and dispatch via pipeline
                # (steer / enqueue-followup / interrupt / run-now)
                from openclaw.auto_reply.reply.agent_runner import ReplyContext, run_prepared_reply

                # Materialize inbound images to the session workspace so the LLM
                # gets real, stable file paths it can reference when calling tools.
                # Without this, models may hallucinate UUID paths that don't exist.
                # Mirrors TS resolveInboundMedia() which saves images before dispatch.
                inbound_image_paths: list[str] = []
                if inbound_images and session_workspace:
                    try:
                        import base64 as _b64
                        from pathlib import Path as _Path
                        _incoming_dir = _Path(session_workspace) / "incoming"
                        _incoming_dir.mkdir(parents=True, exist_ok=True)
                        for _img_idx, _img_data_url in enumerate(inbound_images):
                            if isinstance(_img_data_url, str) and _img_data_url.startswith("data:"):
                                try:
                                    _header, _data_str = _img_data_url.split(",", 1)
                                    _mime = _header.split(";")[0].split(":", 1)[1]
                                    _ext = {"image/jpeg": ".jpg", "image/png": ".png",
                                            "image/gif": ".gif", "image/webp": ".webp"}.get(_mime, ".jpg")
                                    _fname = f"image_{message.message_id}_{_img_idx}{_ext}"
                                    _dest = _incoming_dir / _fname
                                    _dest.write_bytes(_b64.b64decode(_data_str))
                                    inbound_image_paths.append(str(_dest))
                                    logger.debug("Materialized inbound image to %s", _dest)
                                except Exception as _me:
                                    logger.debug("Could not materialize inbound image %d: %s", _img_idx, _me)
                    except Exception as _mat_exc:
                        logger.debug("Inbound image materialization skipped: %s", _mat_exc)

                # Build MEDIA reply hint when inbound media is present.
                # Mirrors TS get-reply-run.ts mediaReplyHint (line 368-372):
                # "To send an image back, prefer the message tool (media/path/filePath). If you must inline, use MEDIA:..."
                # This hint is added to the USER MESSAGE (not system prompt) to guide LLM output.
                effective_message_text = message_text
                _media_reply_hint = None
                
                if inbound_images:
                    if inbound_image_paths:
                        # Tell the LLM the exact paths — prevents hallucinated UUID paths.
                        _path_list = ", ".join(f"`{p}`" for p in inbound_image_paths)
                        _media_note = f"[Inbound image(s) saved to: {_path_list}]"
                        _media_reply_hint = (
                            "The image is also visible in your vision context — "
                            "you do NOT need to call image_analyze unless you want a separate model's view."
                        )
                    else:
                        _media_note = "[Inbound image(s) received]"
                        _media_reply_hint = (
                            "The image is visible in your vision context — "
                            "you do NOT need to call image_analyze."
                        )
                    
                    # Add generic file sending hint (applies to ALL files, not just images)
                    _media_reply_hint += (
                        "\n\n**To send a file back (image/video/document):**\n"
                        "1. **Best**: Use the `message` tool with `media` parameter\n"
                        "2. **Alternative**: Output `MEDIA:` line in your response:\n"
                        "   - Remote: `MEDIA:https://example.com/image.jpg` (spaces ok, quote if needed)\n"
                        "   - Relative: `MEDIA:./image.jpg` or `MEDIA:image.jpg`\n"
                        "   - **Avoid**: Absolute paths like `MEDIA:/Users/...` (blocked for security)\n"
                        "   - **Avoid**: Tilde paths like `MEDIA:~/...` (blocked)\n"
                        "Keep caption text in the text body, not in the MEDIA line."
                    )
                    effective_message_text = f"{_media_note}\n{_media_reply_hint}\n\n{message_text}".strip()
                
                elif not inbound_images:
                    # No inbound media, but still provide hint for OUTPUT file delivery
                    # This ensures the LLM knows how to send generated/downloaded files
                    _media_reply_hint = (
                        "💡 **File Delivery Tip**: To send files to the user:\n"
                        "- **Best**: Use `message` tool with `media` parameter\n"
                        "- **Alternative**: Output a line starting with `MEDIA:` followed by the file path:\n"
                        "  Examples:\n"
                        "  • `MEDIA:https://example.com/cat.jpg` (remote URL)\n"
                        "  • `MEDIA:./nature_sample.mp4` (relative path in workspace)\n"
                        "  • `MEDIA:presentations/demo.pptx` (relative path)\n"
                        "  ⚠️ Avoid absolute paths (MEDIA:/...) and ~ paths — blocked for security\n"
                        "Keep your caption/description in the regular text, not in the MEDIA line."
                    )
                    effective_message_text = f"{_media_reply_hint}\n\n{message_text}".strip()

                # Build per-turn system prompt with inbound meta injected.
                # Mirrors TS get-reply-run.ts buildInboundMetaSystemPrompt(sessionCtx)
                # which is passed as extraSystemPromptParts[0] every turn, ensuring
                # the agent always has chat_id, channel, chat_type in its context.
                # On first turn after reset, use freshly-rebuilt prompt from disk.
                _base_system_prompt = _fresh_system_prompt if _fresh_system_prompt else self.system_prompt
                try:
                    from openclaw.auto_reply.inbound_meta import build_inbound_meta_system_prompt as _build_meta
                    _inbound_meta = _build_meta(ctx)
                    _turn_system_prompt = (_base_system_prompt or "") + "\n\n" + _inbound_meta
                except Exception:
                    _turn_system_prompt = _base_system_prompt

                # Inject ## Sandbox section when sandbox is enabled.
                # Mirrors TS build_sandbox_section injected into system prompt.
                if _sandbox_enabled:
                    try:
                        from openclaw.agents.system_prompt_sections import build_sandbox_section
                        _sb_info: dict = {
                            "enabled": True,
                            "workspace_access": _ws_access,
                        }
                        if session_workspace and _ws_access != "rw":
                            _sb_info["workspace_dir"] = session_workspace
                        _sb_section_lines = build_sandbox_section(_sb_info)
                        if _sb_section_lines:
                            _turn_system_prompt = (_turn_system_prompt or "") + "\n\n" + "\n".join(_sb_section_lines)
                    except Exception:
                        pass

                # When sandbox uses a per-session workspace (sandbox on + ws_access != rw),
                # inject the path so the agent knows where to save files.
                # When sandbox is OFF or workspaceAccess=rw, the agent uses the workspace
                # root directly and can read/edit SOUL.md, USER.md, etc. — no note needed.
                if session_workspace and _sandbox_enabled and _ws_access != "rw":
                    _ws_note = (
                        "\n\n## Session Workspace\n"
                        f"Your **session workspace** for this conversation is: `{session_workspace}`\n"
                        "Save all generated files, downloads, and outputs **inside this directory** "
                        "(e.g. `{session_workspace}/report.pdf`, `{session_workspace}/output.mp4`).\n"
                        "Do NOT scatter files into the parent workspace root."
                    ).replace("{session_workspace}", session_workspace)
                    _turn_system_prompt = (_turn_system_prompt or "") + _ws_note

                # Inject per-turn channel note so the agent always knows which channel
                # it is responding in and can use MEDIA: tokens / message tool correctly.
                # Mirrors TS attempt.ts runtimeChannel injection into system prompt params.
                _channel_note = (
                    f"\n\n## Current Channel\n"
                    f"You are responding via **{channel_id}** (chat_id: `{message.chat_id}`).\n"
                    f"To deliver a file to the user, place a `MEDIA:` token on its own line "
                    f"anywhere in your reply (e.g. `MEDIA:/abs/path/file.pdf` or "
                    f"`MEDIA:https://example.com/image.jpg`). "
                    f"Do NOT write markdown download links or say you cannot send — use MEDIA: directly."
                )
                _turn_system_prompt = (_turn_system_prompt or "") + _channel_note

                # Inject per-turn elevated level into system prompt so the model
                # always knows the current elevated state (mirrors TS per-turn injection
                # in get-reply-run.ts extraSystemPromptParts).
                try:
                    _session_entry = session or {}
                    _cur_elevated = (
                        _session_entry.get("elevatedLevel")
                        if isinstance(_session_entry, dict)
                        else getattr(_session_entry, "elevatedLevel", None)
                    )
                    if _cur_elevated and _cur_elevated != "off":
                        _level_desc = (
                            "ask (per-command approval required)"
                            if _cur_elevated in ("ask", "on")
                            else "full (auto-approve all host commands)"
                        )
                        _elevated_note = (
                            f"\n\n## Elevated Exec — Current Level\n"
                            f"Elevated exec is active for this session: **{_level_desc}**.\n"
                            "Use /elevated off to disable."
                        )
                        _turn_system_prompt = (_turn_system_prompt or "") + _elevated_note
                except Exception:
                    pass

                # Build a per-dispatch tool list where MessageTool is replaced with a
                # channel-context-bound copy so the agent can call message(action=sendAttachment)
                # without needing to specify the channel explicitly.
                # Mirrors TS createMessageTool({ currentChannelProvider, currentChannelId })
                # called fresh per-dispatch in attempt.ts.
                _dispatch_tools = self.tools
                
                # CRITICAL FIX: Inject session_key into CronTool for this dispatch
                # This ensures new cron jobs created during this session have the correct session_key
                try:
                    from openclaw.agents.tools.cron import CronTool as _CronTool
                    _existing_cron_tool = next((t for t in self.tools if isinstance(t, _CronTool)), None)
                    if _existing_cron_tool is not None:
                        logger.debug(f"[{channel_id}] Injecting session_key={session_key} into CronTool")
                        _existing_cron_tool.set_agent_session_key(session_key)
                        _existing_cron_tool.set_chat_context(channel_id, str(message.chat_id))
                except Exception as _cron_inject_err:
                    logger.warning(f"[{channel_id}] Failed to inject session_key into CronTool: {_cron_inject_err}")
                
                try:
                    from openclaw.agents.tools.channel_actions import create_message_tool as _cmt, MessageTool as _MT
                    _existing_msg_tool = next((t for t in self.tools if isinstance(t, _MT)), None)
                    if _existing_msg_tool is not None:
                        _bound_msg_tool = _cmt(
                            channel_registry=_existing_msg_tool.channel_registry,
                            current_channel_provider=channel_id,
                            current_channel_id=str(message.chat_id),
                        )
                        _dispatch_tools = [
                            _bound_msg_tool if isinstance(t, _MT) else t
                            for t in self.tools
                        ]
                except Exception as _mte:
                    logger.debug("Per-dispatch MessageTool binding failed: %s", _mte)

                # ------------------------------------------------------------------
                # Skill dispatch + SKILL.md injection (must run in channel_manager
                # because ReplyContext is created here, bypassing get_reply.py).
                #
                # Path 1 — Slash commands: mirrors TS resolveSkillCommandInvocation
                #   in get-reply-inline-actions.ts. Handles /pptx [args] and
                #   /skill pptx [args]. Args are extracted and passed as user input.
                #
                # Path 2 — Keyword detection (Python-specific Gemini workaround):
                #   TS relies on Claude following "## Skills (mandatory)" system
                #   prompt instructions. Gemini ignores those, so we proactively
                #   inject the full SKILL.md into the message for always=True skills.
                # ------------------------------------------------------------------
                try:
                    import re as _re_sk
                    from openclaw.agents.skills.workspace import load_workspace_skill_entries, filter_skill_entries
                    from pathlib import Path as _SPath

                    # Workspace for skill loading — prefer session_workspace, fall back to state dir
                    _skill_ws = session_workspace
                    if not _skill_ws:
                        from openclaw.config.paths import resolve_state_dir as _rsd_ski
                        _skill_ws = str(_rsd_ski() / "workspace")

                    _cfg_dict = (
                        _cfg.model_dump() if hasattr(_cfg, "model_dump") else
                        (_cfg if isinstance(_cfg, dict) else {})
                    )
                    _sk_entries = load_workspace_skill_entries(_skill_ws, _cfg_dict)
                    _sk_eligible = filter_skill_entries(_sk_entries, _cfg_dict, None)
                    _always_entries = [e for e in _sk_eligible if e.metadata and e.metadata.always]

                    def _read_skill_md_cm(loc: object) -> str | None:
                        if not loc:
                            return None
                        try:
                            return _SPath(str(loc)).read_text(encoding="utf-8").strip() or None
                        except Exception:
                            return None

                    def _build_skill_rewrite_cm(skill_name: str, user_input: str, content: str | None) -> str:
                        """Mirrors TS promptParts join in get-reply-inline-actions.ts lines 238-242."""
                        parts = [f'Use the "{skill_name}" skill for this request.']
                        if content:
                            parts.append(
                                "The skill instructions from SKILL.md are preloaded below"
                                " — follow them exactly:\n\n" + content
                            )
                        if user_input and user_input.strip():
                            parts.append(f"User input:\n{user_input.strip()}")
                        return "\n\n".join(parts)

                    _msg_stripped = (message_text or "").strip()
                    _injected = False

                    # Path 1: Slash command dispatch — mirrors TS resolveSkillCommandInvocation.
                    # Handles /pptx [args]  and  /skill pptx [args].
                    if _msg_stripped.startswith("/"):
                        _slash_m = _re_sk.match(r"^/([^\s]+)(?:\s+([\s\S]+))?$", _msg_stripped)
                        if _slash_m:
                            _cmd_name = _slash_m.group(1).lower()
                            _cmd_args = (_slash_m.group(2) or "").strip()
                            # Resolve /skill pptx [args] format (mirrors TS /skill handler)
                            if _cmd_name == "skill" and _cmd_args:
                                _skill_re = _re_sk.match(r"^([^\s]+)(?:\s+([\s\S]+))?$", _cmd_args)
                                if _skill_re:
                                    _cmd_name = _skill_re.group(1).lower()
                                    _cmd_args = (_skill_re.group(2) or "").strip()
                            for _ae in _always_entries:
                                if _ae.skill.name.lower() == _cmd_name:
                                    _sc = _read_skill_md_cm(getattr(_ae.skill, "location", None))
                                    # Use extracted args as user input (aligns with TS skillInvocation.args)
                                    _user_inp = _cmd_args if _cmd_args else _msg_stripped
                                    effective_message_text = _build_skill_rewrite_cm(_ae.skill.name, _user_inp, _sc)
                                    _injected = True
                                    logger.info(
                                        "[%s] Skill slash-cmd /%s → rewrite%s",
                                        channel_id, _ae.skill.name,
                                        " (SKILL.md injected)" if _sc else "",
                                    )
                                    break

                    # Path 2: Keyword detection for natural language (Gemini workaround).
                    if not _injected:
                        _SKILL_KEYWORDS: dict[str, list[str]] = {
                            "pptx": ["ppt", ".pptx", "presentation", "slide", "deck",
                                     "幻灯片", "演示文稿", "演示", "pptx"],
                            "docx": [".docx", "word document", "word doc", "docx"],
                            "pdf": [".pdf", "pdf file", "pdf document"],
                            "xlsx": [".xlsx", ".xls", "excel", "spreadsheet", "表格", "xlsx"],
                        }
                        _body_lower = _msg_stripped.lower()
                        for _ae in _always_entries:
                            _kws = _SKILL_KEYWORDS.get(_ae.skill.name)
                            if not _kws:
                                continue
                            for _kw in _kws:
                                if _kw in _body_lower:
                                    _sc = _read_skill_md_cm(getattr(_ae.skill, "location", None))
                                    effective_message_text = _build_skill_rewrite_cm(_ae.skill.name, _msg_stripped, _sc)
                                    _injected = True
                                    logger.info(
                                        "[%s] Skill keyword '%s' → skill '%s'%s",
                                        channel_id, _kw, _ae.skill.name,
                                        " (SKILL.md injected)" if _sc else "",
                                    )
                                    break
                            if _injected:
                                break
                except Exception as _ski_exc:
                    logger.debug("Skill injection (non-fatal): %s", _ski_exc)

                reply_ctx = ReplyContext(
                    session_id=session_id_for_run,
                    session_key=session_key,
                    message_text=effective_message_text,
                    runtime=runtime,
                    session=session,
                    tools=_dispatch_tools,
                    channel_send=_channel_send,
                    chat_target=message.chat_id,
                    channel_id=channel_id,
                    reply_to_id=message.message_id,
                    queue_settings=_queue_settings,
                    images=inbound_images,
                    system_prompt=_turn_system_prompt,
                    originating_channel=channel_id,
                    originating_to=message.chat_id,
                    session_workspace=session_workspace,
                    typing_ctrl=typing_ctrl,
                    typing_send_fn=_send_typing,
                    stream_callback=_stream_callback,
                    draft_stream=_draft_stream,
                    status_reactions=_status_reactions,
                    reasoning_level=_reasoning_level,
                    reasoning_stream_callback=_reasoning_callback,
                    reasoning_draft_stream=_reasoning_draft,
                    block_send_fn=_block_send_fn,
                    ctx_metadata=message.metadata,  # ✅ Pass metadata from InboundMessage
                )

                # Fire-and-forget: handler returns immediately.
                # run_prepared_reply handles steer/queue/run logic and will
                # call typing_ctrl.mark_run_complete() / mark_dispatch_idle()
                # on its own when the turn finishes.
                asyncio.create_task(run_prepared_reply(reply_ctx))

            except Exception as e:
                try:
                    typing_ctrl.cleanup()
                except Exception:
                    pass
                logger.error(f"Error dispatching message: {e}", exc_info=True)
                try:
                    await channel.send_text(
                        target=message.chat_id,
                        text=f"Sorry, I encountered an error: {str(e)[:100]}",
                    )
                except Exception:
                    pass

        return handler

    def to_dict(self) -> dict[str, Any]:
        """Convert manager to dictionary"""
        return self.get_all_status()


# ============================================================================
# Plugin Discovery
# ============================================================================


def discover_channel_plugins() -> dict[str, type[ChannelPlugin]]:
    """
    Discover available channel plugin classes

    This scans the channels module and returns all available plugin classes.

    Returns:
        Dict mapping channel_id to plugin class
    """
    from ..channels import (
        DiscordChannel,
        EnhancedTelegramChannel,
        SlackChannel,
        TelegramChannel,
        WebChatChannel,
    )

    # Try to import optional channels
    plugins: dict[str, type[ChannelPlugin]] = {}

    # Core channels (always available)
    core_channels = [
        ("telegram", TelegramChannel),
        ("telegram-enhanced", EnhancedTelegramChannel),
        ("discord", DiscordChannel),
        ("slack", SlackChannel),
        ("webchat", WebChatChannel),
    ]

    for channel_id, channel_class in core_channels:
        try:
            # Verify it's a valid channel
            instance = channel_class()
            actual_id = instance.id or channel_id
            plugins[actual_id] = channel_class
            logger.debug(f"Discovered channel plugin: {actual_id}")
        except Exception as e:
            logger.debug(f"Could not load channel {channel_id}: {e}")

    # Try to load additional optional channels
    optional_channels = [
        ("whatsapp", "WhatsAppChannel"),
        ("signal", "SignalChannel"),
        ("matrix", "MatrixChannel"),
        ("teams", "TeamsChannel"),
        ("line", "LineChannel"),
        ("imessage", "iMessageChannel"),
    ]

    for channel_id, class_name in optional_channels:
        try:
            module = __import__(f"openclaw.channels.{channel_id}", fromlist=[class_name])
            channel_class = getattr(module, class_name, None)
            if channel_class:
                instance = channel_class()
                actual_id = instance.id or channel_id
                plugins[actual_id] = channel_class
                logger.debug(f"Discovered optional channel: {actual_id}")
        except Exception:
            pass  # Optional channel not available

    return plugins


def load_channel_plugins(
    config: dict[str, Any] | None = None,
) -> dict[str, type[ChannelPlugin]]:
    """
    Load channel plugins based on configuration

    Args:
        config: Optional config dict with channel settings
               {"channels": {"telegram": {"enabled": true}, ...}}

    Returns:
        Dict of enabled channel plugins
    """
    all_plugins = discover_channel_plugins()

    if not config:
        return all_plugins

    # Filter by config
    channels_config = config.get("channels", {})

    enabled_plugins = {}
    for channel_id, plugin_class in all_plugins.items():
        channel_config = channels_config.get(channel_id, {})

        # Default to enabled if not specified
        if channel_config.get("enabled", True):
            enabled_plugins[channel_id] = plugin_class

    return enabled_plugins
