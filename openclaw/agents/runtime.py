from __future__ import annotations

"""
DEPRECATED: Legacy MultiProviderRuntime implementation.

This module is deprecated in favor of PiAgentRuntime (openclaw.gateway.pi_runtime).
The MultiProviderRuntime class is kept for backwards compatibility only and will be 
removed in a future version.

⚠️  For all new code, use PiAgentRuntime instead:
    from openclaw.gateway.pi_runtime import PiAgentRuntime

Migration Guide:
- Replace MultiProviderRuntime with PiAgentRuntime
- PiAgentRuntime provides the same interface with better performance
- See openclaw.gateway.pi_runtime for detailed API documentation
"""

import asyncio
import logging
import os
import warnings
from collections.abc import AsyncIterator

# Issue deprecation warning when this module is imported
warnings.warn(
    "MultiProviderRuntime is deprecated and will be removed in a future version. "
    "Use PiAgentRuntime from openclaw.gateway.pi_runtime instead.",
    DeprecationWarning,
    stacklevel=2,
)

from ..events import Event, EventType
from .auth import AuthProfile, ProfileStore, RotationManager
from .compaction import CompactionManager, CompactionStrategy, TokenAnalyzer
from .context import ContextManager
from .errors import classify_error, format_error_message, is_retryable_error
from .failover import FailoverReason, FallbackChain, FallbackManager
from .formatting import FormatMode, ToolFormatter
from .history_utils import sanitize_session_history, limit_history_turns
from .providers import (
    AnthropicProvider,
    BedrockProvider,
    GeminiProvider,
    LLMMessage,
    LLMProvider,
    OllamaProvider,
    OpenAIProvider,
)
from .queuing import QueueManager
from .session import Session
from .thinking import ThinkingExtractor, ThinkingMode
from .tool_adapter import ToolDefinitionAdapter
from .tools.base import AgentTool

logger = logging.getLogger(__name__)

# Backwards compatibility: AgentEvent is now an alias to Event
AgentEvent = Event


class MultiProviderRuntime:
    """
    Enhanced Agent runtime with support for multiple LLM providers

    Supported providers:
    - anthropic: Claude models
    - openai: GPT models
    - gemini: Google Gemini
    - bedrock: AWS Bedrock
    - ollama: Local models
    - openai-compatible: Any OpenAI-compatible API

    Model format: "provider/model" or just "model" (defaults to anthropic)

    Examples:
        # Anthropic
        runtime = MultiProviderRuntime("anthropic/claude-opus-4-5")

        # OpenAI
        runtime = MultiProviderRuntime("openai/gpt-4")

        # Google Gemini
        runtime = MultiProviderRuntime("gemini/gemini-pro")

        # AWS Bedrock
        runtime = MultiProviderRuntime("bedrock/anthropic.claude-3-sonnet")

        # Ollama (local)
        runtime = MultiProviderRuntime("ollama/llama3")

        # OpenAI-compatible (custom base URL)
        runtime = MultiProviderRuntime(
            "lmstudio/model-name",
            base_url="http://localhost:1234/v1"
        )
    """

    def __init__(
        self,
        model: str = "google/gemini-3-pro-preview",
        api_key: str | None = None,
        base_url: str | None = None,
        max_retries: int = 3,
        enable_context_management: bool = True,
        # New advanced features
        thinking_mode: ThinkingMode = ThinkingMode.OFF,
        fallback_models: list[str] | None = None,
        auth_profiles: list[AuthProfile] | None = None,
        enable_queuing: bool = False,
        tool_format: FormatMode = FormatMode.MARKDOWN,
        compaction_strategy: CompactionStrategy = CompactionStrategy.KEEP_IMPORTANT,
        **kwargs,
    ):
        self.model_str = model
        self.api_key = api_key
        self.base_url = base_url
        self.max_retries = max_retries
        self.enable_context_management = enable_context_management
        self.extra_params = kwargs

        # Parse provider and model
        self.provider_name, self.model_name = self._parse_model(model)

        # Initialize provider
        self.provider = self._create_provider()

        # Initialize context manager
        if enable_context_management:
            self.context_manager = ContextManager(self.model_name)
        else:
            self.context_manager = None

        # Initialize new advanced features
        self.thinking_mode = thinking_mode
        self.thinking_extractor = ThinkingExtractor() if thinking_mode != ThinkingMode.OFF else None

        # Failover management
        self.fallback_chain = None
        self.fallback_manager = None
        if fallback_models:
            self.fallback_chain = FallbackChain(primary=model, fallbacks=fallback_models)
            self.fallback_manager = FallbackManager(self.fallback_chain)

        # Auth rotation
        self.auth_rotation = None
        if auth_profiles:
            store = ProfileStore()
            for profile in auth_profiles:
                store.add_profile(profile)
            self.auth_rotation = RotationManager(store)

        # Queuing
        self.queue_manager = QueueManager() if enable_queuing else None

        # Tool formatting
        self.tool_formatter = ToolFormatter(tool_format)

        # Advanced compaction
        self.compaction_strategy = compaction_strategy
        if self.context_manager:
            self.token_analyzer = TokenAnalyzer(self.model_name)
            self.compaction_manager = CompactionManager(self.token_analyzer, compaction_strategy)
        else:
            self.token_analyzer = None
            self.compaction_manager = None

        # Observer pattern: event listeners (e.g., Gateway)
        self.event_listeners: list = []
        
        # AgentLoop-style features
        self.steering_queue: list[str] = []  # Interrupt current turn with these messages
        self.followup_queue: list[str] = []  # Process these messages after current turn
        self.convert_to_llm_hook: Callable | None = None  # Message conversion hook
        self.transform_context_hook: Callable | None = None  # Context transformation hook
        
        # Extensions system (injected by gateway)
        self.extension_runtime: Any | None = None  # ExtensionRuntime instance

    def _parse_model(self, model: str) -> tuple[str, str]:
        """
        Parse model string into provider and model name

        Examples:
            "anthropic/claude-opus" -> ("anthropic", "claude-opus")
            "gemini/gemini-pro" -> ("gemini", "gemini-pro")
            "claude-opus" -> ("anthropic", "claude-opus")  # default
        """
        if "/" in model:
            parts = model.split("/", 1)
            return parts[0], parts[1]
        else:
            # Default to anthropic
            return "anthropic", model

    def add_event_listener(self, listener):
        """
        Register an event listener (observer pattern)

        The listener will be called for every AgentEvent produced during run_turn.
        This allows components like Gateway to observe agent events without direct coupling.

        Args:
            listener: Callable that accepts AgentEvent. Can be sync or async.

        Example:
            async def on_agent_event(event: AgentEvent):
                print(f"Agent event: {event.type}")

            agent_runtime.add_event_listener(on_agent_event)
        """
        self.event_listeners.append(listener)
        logger.debug(f"Registered event listener: {listener}")

    def remove_event_listener(self, listener):
        """Remove an event listener"""
        if listener in self.event_listeners:
            self.event_listeners.remove(listener)
            logger.debug(f"Removed event listener: {listener}")
    
    def add_steering_message(self, message: str):
        """
        Add a steering message to interrupt the current turn.
        Steering messages are processed immediately, interrupting the current turn.
        
        Args:
            message: Message to add to steering queue
        """
        self.steering_queue.append(message)
        logger.debug(f"Added steering message: {message[:50]}...")
    
    def add_followup_message(self, message: str):
        """
        Add a follow-up message to be processed after the current turn.
        Follow-up messages are queued and processed in order after turn completion.
        
        Args:
            message: Message to add to follow-up queue
        """
        self.followup_queue.append(message)
        logger.debug(f"Added follow-up message: {message[:50]}...")
    
    def check_steering(self) -> str | None:
        """
        Check if there are steering messages to process.
        Returns the next steering message if available.
        """
        if self.steering_queue:
            return self.steering_queue.pop(0)
        return None
    
    def check_followup(self) -> str | None:
        """
        Check if there are follow-up messages to process.
        Returns the next follow-up message if available.
        """
        if self.followup_queue:
            return self.followup_queue.pop(0)
        return None

    async def _notify_observers(self, event: Event):
        """Notify all registered observers of an event"""
        for listener in self.event_listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(event)
                else:
                    listener(event)
            except Exception as e:
                logger.error(f"Observer notification failed: {e}", exc_info=True)

    def _create_provider(self) -> LLMProvider:
        """Create appropriate provider based on provider name.

        Routes aligned with TS register-builtins.ts and openai-completions.ts.
        OpenAI-compatible providers (xAI, DeepSeek, Zhipu/ZAI, Groq, Mistral,
        Moonshot, Together, OpenRouter, HuggingFace, Cerebras) all use
        OpenAIProvider with provider-specific base_url and api_key env vars.
        """
        from .model_selection import normalize_provider_id
        provider_name = normalize_provider_id(self.provider_name)

        # Common parameters
        kwargs: dict = {
            "model": self.model_name,
            "api_key": self.api_key,
            "base_url": self.base_url,
            **self.extra_params,
        }

        # ── Core providers ─────────────────────────────────────────────────
        if provider_name == "anthropic":
            return AnthropicProvider(**kwargs)

        if provider_name == "openai":
            return OpenAIProvider(**kwargs)

        if provider_name in ("gemini", "google", "google-gemini"):
            return GeminiProvider(**kwargs)

        if provider_name in ("bedrock", "aws-bedrock", "amazon-bedrock"):
            return BedrockProvider(**kwargs)

        if provider_name == "ollama":
            return OllamaProvider(**kwargs)

        # ── OpenAI-compatible providers (mirrors TS register-builtins.ts) ──

        if provider_name == "xai":
            kwargs["base_url"] = kwargs.get("base_url") or "https://api.x.ai/v1"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("XAI_API_KEY")
            return OpenAIProvider(provider_name_override="xai", **kwargs)

        if provider_name in ("zai", "zhipu"):
            # ZAI coding API (aligned with TS)
            kwargs["base_url"] = kwargs.get("base_url") or "https://api.z.ai/api/coding/paas/v4"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("ZAI_API_KEY") or os.getenv("ZHIPU_API_KEY")
            return OpenAIProvider(provider_name_override="zai", **kwargs)

        if provider_name == "deepseek":
            kwargs["base_url"] = kwargs.get("base_url") or "https://api.deepseek.com"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("DEEPSEEK_API_KEY")
            return OpenAIProvider(provider_name_override="deepseek", **kwargs)

        if provider_name == "groq":
            kwargs["base_url"] = kwargs.get("base_url") or "https://api.groq.com/openai/v1"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("GROQ_API_KEY")
            return OpenAIProvider(provider_name_override="groq", **kwargs)

        if provider_name == "mistral":
            kwargs["base_url"] = kwargs.get("base_url") or "https://api.mistral.ai/v1"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("MISTRAL_API_KEY")
            return OpenAIProvider(provider_name_override="mistral", **kwargs)

        if provider_name == "moonshot":
            # URL selection: MOONSHOT_API_KEY → .cn, KIMI_API_KEY → .ai (matches TS CHANGELOG #68816)
            moonshot_key = os.getenv("MOONSHOT_API_KEY", "")
            kimi_key = os.getenv("KIMI_API_KEY", "")
            effective_key = moonshot_key or kimi_key or os.getenv("KIMI_CODE_API_KEY", "")
            default_url = (
                "https://api.moonshot.ai/v1" if (kimi_key and not moonshot_key)
                else "https://api.moonshot.cn/v1"
            )
            kwargs["base_url"] = kwargs.get("base_url") or default_url
            kwargs["api_key"] = kwargs.get("api_key") or effective_key
            # supportsDeveloperRole=False — Moonshot uses "user" role for system-level messages
            kwargs.setdefault("supports_developer_role", False)
            return OpenAIProvider(provider_name_override="moonshot", **kwargs)

        if provider_name in ("kimi-coding", "kimi"):
            # Kimi Coding API uses Anthropic Messages API format (aligned with TS)
            # TS: src/agents/models-config.providers.ts buildKimiCodingProvider()
            # base_url: "https://api.kimi.com/coding/" (with trailing slash per TS)
            # api: "anthropic-messages"
            kwargs["base_url"] = kwargs.get("base_url") or "https://api.kimi.com/coding/"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("KIMI_API_KEY") or os.getenv("KIMI_CODE_API_KEY")
            return AnthropicProvider(provider_name_override="kimi-coding", **kwargs)

        if provider_name == "together":
            kwargs["base_url"] = kwargs.get("base_url") or "https://api.together.xyz/v1"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("TOGETHER_API_KEY")
            return OpenAIProvider(provider_name_override="together", **kwargs)

        if provider_name == "openrouter":
            kwargs["base_url"] = kwargs.get("base_url") or "https://openrouter.ai/api/v1"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("OPENROUTER_API_KEY")
            return OpenAIProvider(provider_name_override="openrouter", **kwargs)

        if provider_name == "huggingface":
            kwargs["base_url"] = kwargs.get("base_url") or "https://api-inference.huggingface.co/models"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("HUGGINGFACE_API_KEY") or os.getenv("HF_API_KEY") or os.getenv("HF_TOKEN")
            return OpenAIProvider(provider_name_override="huggingface", **kwargs)

        if provider_name == "cerebras":
            kwargs["base_url"] = kwargs.get("base_url") or "https://api.cerebras.ai/v1"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("CEREBRAS_API_KEY")
            return OpenAIProvider(provider_name_override="cerebras", **kwargs)

        # ── Chinese AI providers ───────────────────────────────────────────

        if provider_name == "minimax":
            # MiniMax (uses Anthropic Messages API - aligned with TS)
            # TS: src/agents/models-config.providers.ts buildMinimaxProvider()
            # api: "anthropic-messages", authHeader: true (uses Authorization: Bearer)
            kwargs["base_url"] = kwargs.get("base_url") or "https://api.minimax.io/anthropic"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("MINIMAX_API_KEY")
            return AnthropicProvider(provider_name_override="minimax", **kwargs)

        if provider_name == "minimax-cn":
            # MiniMax China (uses Anthropic Messages API - aligned with TS)
            # TS: src/agents/models-config.providers.ts buildMinimaxPortalProvider()
            # api: "anthropic-messages", authHeader: true
            kwargs["base_url"] = kwargs.get("base_url") or "https://api.minimaxi.com/anthropic"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("MINIMAX_CN_API_KEY")
            return AnthropicProvider(provider_name_override="minimax-cn", **kwargs)

        if provider_name in ("opencode", "opencode-zen"):
            kwargs["base_url"] = kwargs.get("base_url") or os.getenv("OPENCODE_BASE_URL", "https://api.opencode.ai/v1")
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("OPENCODE_API_KEY")
            return OpenAIProvider(provider_name_override="opencode", **kwargs)

        if provider_name in ("qwen-portal", "qwen"):
            kwargs["base_url"] = kwargs.get("base_url") or "https://portal.qwen.ai/v1"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")
            return OpenAIProvider(provider_name_override="qwen", **kwargs)

        if provider_name == "xiaomi":
            # Xiaomi MiMo (uses Anthropic Messages API - aligned with TS)
            # TS: src/agents/models-config.providers.ts buildXiaomiProvider()
            # api: "anthropic-messages"
            kwargs["base_url"] = kwargs.get("base_url") or "https://api.xiaomimimo.com/anthropic"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("XIAOMI_API_KEY")
            return AnthropicProvider(provider_name_override="xiaomi", **kwargs)

        if provider_name in ("volcengine", "doubao"):
            # Volcengine Doubao (uses OpenAI Completions API - aligned with TS)
            # TS: src/agents/models-config.providers.ts buildDoubaoProvider()
            # api: "openai-completions"
            kwargs["base_url"] = kwargs.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("VOLCANO_ENGINE_API_KEY") or os.getenv("VOLCENGINE_API_KEY")
            return OpenAIProvider(provider_name_override="volcengine", **kwargs)

        if provider_name in ("volcengine-plan", "doubao-coding"):
            # Volcengine Doubao Coding (uses OpenAI Completions API - aligned with TS)
            # TS: src/agents/models-config.providers.ts buildDoubaoCodingProvider()
            kwargs["base_url"] = kwargs.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3/chat/completions/coding"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("VOLCANO_ENGINE_API_KEY") or os.getenv("VOLCENGINE_API_KEY")
            return OpenAIProvider(provider_name_override="volcengine-plan", **kwargs)

        if provider_name == "byteplus":
            # BytePlus (uses OpenAI Completions API - aligned with TS)
            # TS: src/agents/models-config.providers.ts buildBytePlusProvider()
            # api: "openai-completions"
            kwargs["base_url"] = kwargs.get("base_url") or "https://ark-us-east-1.bytepluses.com/api/v3"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("BYTEPLUS_API_KEY")
            return OpenAIProvider(provider_name_override="byteplus", **kwargs)

        if provider_name in ("byteplus-plan", "byteplus-coding"):
            # BytePlus Coding (uses OpenAI Completions API - aligned with TS)
            # TS: src/agents/models-config.providers.ts buildBytePlusCodingProvider()
            kwargs["base_url"] = kwargs.get("base_url") or "https://ark-us-east-1.bytepluses.com/api/v3/chat/completions/coding"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("BYTEPLUS_API_KEY")
            return OpenAIProvider(provider_name_override="byteplus-plan", **kwargs)

        if provider_name == "synthetic":
            # Synthetic (uses Anthropic Messages API - aligned with TS)
            # TS: src/agents/models-config.providers.ts buildSyntheticProvider()
            # api: "anthropic-messages"
            kwargs["base_url"] = kwargs.get("base_url") or "https://api.synthetic.ai/v1"
            kwargs["api_key"] = kwargs.get("api_key") or os.getenv("SYNTHETIC_API_KEY")
            return AnthropicProvider(provider_name_override="synthetic", **kwargs)

        if provider_name in ("lmstudio", "openai-compatible", "custom"):
            return OpenAIProvider(**kwargs)

        # Unknown provider — try OpenAI-compatible
        logger.warning(f"Unknown provider '{provider_name}', trying OpenAI-compatible mode")
        return OpenAIProvider(**kwargs)

    async def _stream_single_turn(
        self,
        session: Session,
        messages: list[LLMMessage],
        tools: list[AgentTool],
        max_tokens: int = 4096,
        is_followup: bool = False,
    ) -> AsyncIterator[Event | AgentEvent]:
        """Stream a single LLM turn without follow-up logic
        
        This method is used by ToolLoopOrchestrator for pi-ai style execution.
        It handles:
        - Single LLM call with provided messages
        - Immediate tool execution if tools are called
        - Event streaming
        - NO follow-up logic (orchestrator handles that)
        
        Args:
            session: Session for context
            messages: Prepared LLM messages
            tools: Available tools
            max_tokens: Max tokens for response
            is_followup: Whether this is a follow-up call (for logging)
            
        Yields:
            Events from the execution
        """
        logger.info(f"🔄 _stream_single_turn: {len(messages)} messages, {len(tools)} tools, followup={is_followup}")
        
        # Format tools for provider
        tools_param = []
        if tools:
            # Convert AgentTool objects to dict format
            tools_dict = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.get_schema() if hasattr(tool, 'get_schema') else tool.parameters,
                    "execute": tool.execute if hasattr(tool, 'execute') else None,
                }
                for tool in tools
            ]
            
            # Apply tool adapter for standardization
            adapted_tools = ToolDefinitionAdapter.to_tool_definitions(tools_dict)
            
            # Format for provider API
            tools_param = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {}),
                    },
                }
                for t in adapted_tools
            ]
            logger.info(f"🔧 Formatted {len(tools_param)} tools for provider")
        
        # Stream from provider
        accumulated_text = ""
        tool_calls = []
        
        async for response in self.provider.stream(
            messages=messages,
            tools=tools_param,
            max_tokens=max_tokens,
            **self.extra_params
        ):
            if response.type == "text_delta":
                text = response.content
                if not text:
                    continue
                
                accumulated_text += text
                
                # Stream text event
                event = Event(
                    type=EventType.AGENT_TEXT,
                    source="agent-runtime",
                    session_id=session.session_id,
                    data={"delta": {"type": "text_delta", "text": text}},
                )
                yield event
            
            elif response.type == "tool_call":
                tool_calls = response.tool_calls or []
                
                # Execute tools immediately
                for tc in tool_calls:
                    tool = next((t for t in tools if t.name == tc["name"]), None)
                    if tool:
                        # Emit tool execution start
                        start_event = Event(
                            type=EventType.TOOL_EXECUTION_START,
                            source="agent-runtime",
                            session_id=session.session_id,
                            data={
                                "tool_call_id": tc["id"],
                                "tool_name": tc["name"],
                                "args": tc["arguments"],
                            },
                        )
                        yield start_event
                        
                        # Execute tool
                        try:
                            # All tools use unified signature: execute(tool_call_id, params, signal, on_update)
                            # Old tools are wrapped by LegacyAgentTool adapter which handles the conversion
                            result = await tool.execute(
                                tool_call_id=tc["id"],
                                params=tc["arguments"],
                                signal=None,
                                on_update=None
                            )
                            result_str = str(result)
                            
                            # Add tool message to session
                            session.add_tool_message(
                                tool_call_id=tc["id"],
                                content=result_str,
                                name=tc["name"]
                            )
                            
                            # Emit tool execution end
                            end_event = Event(
                                type=EventType.TOOL_EXECUTION_END,
                                source="agent-runtime",
                                session_id=session.session_id,
                                data={
                                    "tool_call_id": tc["id"],
                                    "tool_name": tc["name"],
                                    "success": True,
                                    "result": result_str,
                                },
                            )
                            yield end_event
                            
                        except Exception as e:
                            error_msg = f"Tool execution failed: {str(e)}"
                            logger.error(f"Tool {tc['name']} failed: {e}", exc_info=True)
                            
                            # Add error as tool result
                            session.add_tool_message(
                                tool_call_id=tc["id"],
                                content=error_msg,
                                name=tc["name"]
                            )
                            
                            # Emit tool error
                            error_event = Event(
                                type=EventType.TOOL_EXECUTION_END,
                                source="agent-runtime",
                                session_id=session.session_id,
                                data={
                                    "tool_call_id": tc["id"],
                                    "tool_name": tc["name"],
                                    "success": False,
                                    "error": error_msg,
                                },
                            )
                            yield error_event
                
                # Add assistant message with tool calls
                if tool_calls:
                    session.add_assistant_message(
                        content=accumulated_text or "",
                        tool_calls=tool_calls
                    )
                    logger.info(f"✅ Added assistant message with {len(tool_calls)} tool calls")
            
            elif response.type == "done":
                # If we have text but no tool calls, add assistant message
                if accumulated_text and not tool_calls:
                    session.add_assistant_message(content=accumulated_text)
                    logger.info(f"✅ Added assistant message: {len(accumulated_text)} chars")
            
            elif response.type == "error":
                error_event = Event(
                    type=EventType.ERROR,
                    source="agent-runtime",
                    session_id=session.session_id,
                    data={"error": response.content},
                )
                yield error_event
                raise Exception(response.content)

    async def run_turn(
        self,
        session: Session,
        message: str,
        tools: list[AgentTool] | None = None,
        max_tokens: int = 4096,
        images: list[str] | None = None,
        system_prompt: str | None = None,
        get_steering_messages: Callable[[], Awaitable[list]] | None = None,
        get_followup_messages: Callable[[], Awaitable[list]] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """
        Run an agent turn with the configured provider

        .. deprecated:: 0.6.0
            Use :class:`AgentSession` with ``prompt()`` method instead for pi-ai style
            automatic tool loop handling. This method will be maintained for backward
            compatibility but new code should use AgentSession.

        Features:
        - Multi-provider support
        - Thinking mode extraction
        - Model fallback chains
        - Auth profile rotation
        - Session queuing
        - Advanced context compaction
        - Tool result formatting

        Args:
            session: Session to use
            message: User message
            tools: Optional list of tools
            max_tokens: Maximum tokens to generate
            images: Optional list of image URLs
            system_prompt: Optional system prompt (injected at session start)

        Yields:
            AgentEvent objects
        """
        import warnings
        warnings.warn(
            "MultiProviderRuntime.run_turn() is deprecated. "
            "Use AgentSession.prompt() for pi-ai style automatic tool loop handling.",
            DeprecationWarning,
            stacklevel=2
        )
        if tools is None:
            tools = []

        # Wrap in queue if enabled
        if self.queue_manager:
            # Queue management: ensure only one turn per session, respect global limits
            session_id = session.session_id if session else "default"
            
            # Check if queue is full (global limit)
            global_stats = self.queue_manager.get_global_lane().get_stats()
            max_queue_size = global_stats["max_concurrent"] * 2  # Allow some buffer
            
            if global_stats["queued"] + global_stats["active"] >= max_queue_size:
                # Queue is full, emit error
                error_event = AgentEvent(
                    "error",
                    {
                        "message": "Queue is full. Please try again later.",
                        "queue_size": global_stats["queued"],
                        "active": global_stats["active"],
                        "max_size": max_queue_size
                    }
                )
                yield error_event
                return
            
            # Execute with queue management
            # Note: generators can't be wrapped directly in enqueue_both,
            # so we track execution but don't enforce hard queuing for streaming
            logger.info(f"Executing turn with queue management for session {session_id}")
            async for event in self._run_turn_internal(session, message, tools, max_tokens, images, system_prompt):
                yield event
        else:
            async for event in self._run_turn_internal(session, message, tools, max_tokens, images, system_prompt):
                yield event

    async def _run_turn_internal(
        self,
        session: Session,
        message: str,
        tools: list[AgentTool],
        max_tokens: int,
        images: list[str] | None = None,
        system_prompt: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Internal run turn implementation"""
        # Inject system prompt at the start of the session (only if no messages yet)
        if system_prompt and len(session.messages) == 0:
            session.add_system_message(system_prompt)
            logger.info(f"✨ System prompt injected ({len(system_prompt)} chars)")
        
        # Add user message (with images if provided)
        if images:
            # Store images in session metadata for this message
            session.add_user_message(message)
            # Add images metadata to the last message
            if session.messages:
                last_msg = session.messages[-1]
                if not hasattr(last_msg, 'images'):
                    last_msg.images = images
                else:
                    last_msg.images = images
        else:
            session.add_user_message(message)
        
        # Call before_agent_start hook (openclaw-ts alignment)
        if self.extension_runtime:
            try:
                hook_results = await self.extension_runtime.emit(
                    "before_agent_start",
                    {
                        "prompt": message,
                        "messages": [m.model_dump() for m in session.messages],
                        "session_id": session.session_id,
                    }
                )
                
                # Process hook results (should be merged by ExtensionRuntime)
                if hook_results:
                    for result in hook_results:
                        if isinstance(result, dict):
                            # Handle prependContext
                            if result.get("prependContext"):
                                prepend_text = result["prependContext"]
                                # Prepend to the user message we just added
                                if session.messages and session.messages[-1].role == "user":
                                    original_content = session.messages[-1].content
                                    session.messages[-1].content = f"{prepend_text}\n\n{original_content}"
                                    logger.info(
                                        f"🧠 Prepended context to prompt "
                                        f"({len(prepend_text)} chars)"
                                    )
                            
                            # Handle systemPrompt modification
                            if result.get("systemPrompt"):
                                # Update system prompt if provided
                                new_system_prompt = result["systemPrompt"]
                                if session.messages and session.messages[0].role == "system":
                                    session.messages[0].content = new_system_prompt
                                    logger.info("🔧 System prompt modified by extension")
            except Exception as e:
                logger.warning(f"before_agent_start hook failed: {e}")

        # Check context window and compact if needed
        if self.compaction_manager and self.enable_context_management:
            messages_for_api = session.get_messages_for_api()
            current_tokens = self.token_analyzer.estimate_messages_tokens(messages_for_api)
            window = self.context_manager.check_context(current_tokens)

            if window.should_compress:
                logger.info(f"Context at {current_tokens}/{window.total_tokens} tokens, compacting")
                # Use advanced compaction
                target_tokens = int(window.total_tokens * 0.7)  # Use 70% of window
                compacted = self.compaction_manager.compact(messages_for_api, target_tokens)

                # Update session with compacted messages
                # Convert back to Message objects
                from .session import Message

                session.messages = [
                    Message(
                        role=m["role"],
                        content=m["content"],
                        tool_calls=m.get("tool_calls"),
                        tool_call_id=m.get("tool_call_id"),
                        name=m.get("name"),
                    )
                    for m in compacted
                ]

                event = AgentEvent(
                    "compaction",
                    {
                        "original_tokens": current_tokens,
                        "compacted_tokens": self.token_analyzer.estimate_messages_tokens(compacted),
                        "strategy": self.compaction_strategy.value,
                    },
                )
                await self._notify_observers(event)
                yield event

        event = Event(
            type=EventType.AGENT_STARTED,
            source="agent-runtime",
            session_id=session.session_id if session else None,
            data={"phase": "start"},
        )
        await self._notify_observers(event)
        yield event

        # Execute with retry logic and failover
        import time as _time
        _run_started_at = _time.monotonic()
        retry_count = 0
        thinking_state = {}  # State for streaming thinking extraction
        initial_text = ""  # Store initial assistant text (before tool calls)
        initial_tool_calls = []  # Store tool calls for merging with final response

        while retry_count <= self.max_retries:
            try:
                # Get current model (may change with failover)
                current_model = self.model_str
                if self.fallback_manager:
                    current_model = self.fallback_manager.get_current_model()
                    logger.info(f"Using model: {current_model}")

                # Smart image loading: Only load images explicitly referenced in prompts
                # Based on openclaw TypeScript: src/agents/pi-embedded-runner/run/images.ts
                from openclaw.agents.image_loader import smart_load_images
                
                # Apply smart image loading if images are provided
                images_to_use = None
                if images:
                    # Convert session messages to dict format for image loader
                    history_messages = [
                        {"role": msg.role, "content": msg.content, "images": msg.images}
                        for msg in session.messages
                    ] if session else []
                    
                    image_data = smart_load_images(
                        current_prompt=prompt,
                        history_messages=history_messages,
                        existing_images=images
                    )
                    images_to_use = image_data["current_images"]
                    
                    if image_data["loaded_count"] > 0 or image_data["skipped_count"] > 0:
                        logger.info(
                            f"Smart image loading: {image_data['loaded_count']} loaded, "
                            f"{image_data['skipped_count']} skipped"
                        )
                
                # Convert session messages to LLM format
                # CRITICAL: Only attach images to the LAST message (current turn)
                # IMPORTANT: Limit history to prevent context overflow
                
                all_messages = session.get_messages()
                
                # Phase 1: Sanitize history (remove invalid messages)
                # Convert to dict format for sanitization
                messages_dict = [
                    {
                        "role": m.role,
                        "content": m.content,
                        "tool_calls": getattr(m, 'tool_calls', None),
                        "tool_call_id": getattr(m, 'tool_call_id', None),
                        "name": getattr(m, 'name', None),
                    }
                    for m in all_messages
                ]
                sanitized_dict = sanitize_session_history(messages_dict)
                
                # Phase 2: Limit history turns (keep only recent N turns)
                # This prevents sending hundreds of old messages to the model
                max_turns = self.extra_params.get('max_history_turns', 50)  # Default 50 turns
                limited_dict = limit_history_turns(
                    sanitized_dict, 
                    max_turns=max_turns,
                    provider=self.provider_name
                )
                logger.info(f"🔄 Limited history: {len(sanitized_dict)} -> {len(limited_dict)} messages (max {max_turns} turns)")
                
                # Convert back to Message objects
                from .session import Message
                messages_to_send = [
                    Message(
                        role=m["role"],
                        content=m["content"],
                        tool_calls=m.get("tool_calls"),
                        tool_call_id=m.get("tool_call_id"),
                        name=m.get("name"),
                    )
                    for m in limited_dict
                ]
                
                if len(messages_to_send) < len(all_messages):
                    logger.info(
                        f"📊 History processed: {len(all_messages)} -> {len(messages_to_send)} messages "
                        f"(sanitized + limited for {self.provider_name})"
                    )
                
                # Apply transformContext hook (openclaw-ts alignment)
                # Allows pruning, filtering, or modifying messages before conversion
                if self.transform_context_hook:
                    try:
                        messages_to_send = await self.transform_context_hook(messages_to_send)
                        logger.debug(
                            f"Applied transform_context_hook: {len(all_messages)} -> {len(messages_to_send)} messages"
                        )
                    except Exception as e:
                        logger.warning(f"transform_context_hook failed: {e}, using original messages")
                
                # Fix Gemini message sequence (if using Gemini)
                if self.provider_name == "gemini" or self.provider_name == "google":
                    try:
                        from .gemini_message_fixer import fix_gemini_message_sequence, validate_gemini_sequence
                        
                        # Convert messages to dict format for validation
                        msgs_dict = []
                        for msg in messages_to_send:
                            msg_dict = {
                                "role": msg.role,
                                "content": msg.content,
                            }
                            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                                msg_dict["tool_calls"] = msg.tool_calls
                            if hasattr(msg, 'tool_call_id') and msg.tool_call_id:
                                msg_dict["tool_call_id"] = msg.tool_call_id
                            msgs_dict.append(msg_dict)
                        
                        # Validate and fix if needed
                        is_valid, error_msg = validate_gemini_sequence(msgs_dict)
                        if not is_valid:
                            logger.warning(f"⚠️ Invalid Gemini sequence: {error_msg}, attempting to fix")
                            fixed_msgs = fix_gemini_message_sequence(msgs_dict)
                            
                            # Convert back to Message objects
                            from .session import Message
                            messages_to_send = [
                                Message(
                                    role=m["role"],
                                    content=m.get("content", ""),
                                    tool_calls=m.get("tool_calls"),
                                    tool_call_id=m.get("tool_call_id"),
                                )
                                for m in fixed_msgs
                            ]
                            logger.info(f"✅ Fixed Gemini sequence: {len(msgs_dict)} -> {len(messages_to_send)} messages")
                    except Exception as fix_err:
                        logger.warning(f"Failed to fix Gemini sequence: {fix_err}")
                
                # Apply convertToLlm hook (openclaw-ts alignment)
                # Converts custom message types to LLM-compatible format
                if self.convert_to_llm_hook:
                    try:
                        llm_messages = await self.convert_to_llm_hook(messages_to_send, images_to_use)
                        logger.debug(
                            f"Applied convert_to_llm_hook: converted {len(messages_to_send)} messages"
                        )
                    except Exception as e:
                        logger.error(f"convert_to_llm_hook failed: {e}")
                        raise
                else:
                    # Default conversion: session messages to LLM format
                    llm_messages = []
                    for i, msg in enumerate(messages_to_send):
                        msg_images = None
                        
                        # ONLY attach images to the LAST message (current turn)
                        if i == len(messages_to_send) - 1 and images_to_use:
                            msg_images = images_to_use
                        
                        # Historical messages: no images
                        # CRITICAL FIX: Pass tool_calls, tool_call_id, and name from session messages
                        llm_messages.append(LLMMessage(
                            role=msg.role, 
                            content=msg.content, 
                            images=msg_images,
                            tool_calls=getattr(msg, 'tool_calls', None),
                            tool_call_id=getattr(msg, 'tool_call_id', None),
                            name=getattr(msg, 'name', None)
                        ))
                
                # DEBUG: Log message count and content
                logger.info(f"📝 Sending {len(llm_messages)} message(s) to provider")
                if len(llm_messages) <= 5:
                    # Log all messages if few
                    for idx, llm_msg in enumerate(llm_messages):
                        content_preview = llm_msg.content[:50] if llm_msg.content and len(llm_msg.content) > 50 else llm_msg.content
                        logger.info(f"  [{idx}] {llm_msg.role}: {repr(content_preview)}{'...' if llm_msg.content and len(llm_msg.content) > 50 else ''}")
                else:
                    # Log first and last few if many
                    for idx in [0, 1, len(llm_messages)-2, len(llm_messages)-1]:
                        if 0 <= idx < len(llm_messages):
                            llm_msg = llm_messages[idx]
                            content_preview = llm_msg.content[:50] if llm_msg.content and len(llm_msg.content) > 50 else llm_msg.content
                            logger.info(f"  [{idx}] {llm_msg.role}: {repr(content_preview)}{'...' if llm_msg.content and len(llm_msg.content) > 50 else ''}")
                    if len(llm_messages) > 4:
                        logger.info(f"  ... ({len(llm_messages) - 4} more messages) ...")

                # Format tools for provider (with adapter for error handling)
                tools_param = None
                if tools:
                    # Convert tools to dict format for adapter
                    tools_dict = [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": (
                                tool.get_schema()
                                if callable(getattr(tool, "get_schema", None))
                                else getattr(tool, "parameters", {})
                            ),
                            "execute": tool.execute if hasattr(tool, 'execute') else None,
                        }
                        for tool in tools
                    ]
                    
                    logger.info(f"🔧 Preparing {len(tools_dict)} tools for LLM")
                    
                    # Apply tool adapter for standardization and error handling
                    adapted_tools = ToolDefinitionAdapter.to_tool_definitions(tools_dict)
                    
                    # Format for provider API
                    tools_param = [
                        {
                            "type": "function",
                            "function": {
                                "name": t["name"],
                                "description": t.get("description", ""),
                                "parameters": t.get("parameters", {}),
                            },
                        }
                        for t in adapted_tools
                    ]
                    
                    logger.info(f"🔧 Formatted {len(tools_param)} tools for provider API")
                    logger.info(f"🔧 Tool names: {[t['function']['name'] for t in tools_param]}")

                # Stream from provider (may need multiple rounds for tool calling)
                accumulated_text = ""
                accumulated_thinking = ""
                tool_calls = []
                tool_results_to_add = []  # Store tool results to add after assistant message
                needs_tool_response = False
                tool_call_iterations = 0  # Track tool call iterations to prevent infinite loops
                MAX_TOOL_ITERATIONS = 5

                async for response in self.provider.stream(
                    messages=llm_messages, 
                    tools=tools_param, 
                    max_tokens=max_tokens,
                    **self.extra_params  # Pass enable_search and other params
                ):
                    if response.type == "text_delta":
                        text = response.content
                        
                        # Filter out empty text deltas
                        if not text:
                            continue
                        
                        accumulated_text += text

                        # Extract thinking if enabled
                        if self.thinking_mode != ThinkingMode.OFF and self.thinking_extractor:
                            thinking_delta, content_delta = (
                                self.thinking_extractor.extract_streaming(text, thinking_state)
                            )

                            # Stream thinking separately if mode is STREAM
                            if self.thinking_mode == ThinkingMode.STREAM and thinking_delta:
                                accumulated_thinking += thinking_delta
                                event = AgentEvent(
                                    "thinking",
                                    {"delta": {"text": thinking_delta}, "mode": "stream"},
                                )
                                await self._notify_observers(event)
                                yield event

                            # Stream content (non-thinking text)
                            if content_delta:
                                event = Event(
                                    type=EventType.AGENT_TEXT,
                                    source="agent-runtime",
                                    session_id=session.session_id if session else None,
                                    data={"delta": {"type": "text_delta", "text": content_delta}},
                                )
                                await self._notify_observers(event)
                                yield event
                        else:
                            # No thinking extraction, stream as-is
                            event = Event(
                                type=EventType.AGENT_TEXT,
                                source="agent-runtime",
                                session_id=session.session_id if session else None,
                                data={"delta": {"type": "text_delta", "text": text}},
                            )
                            await self._notify_observers(event)
                            yield event

                    elif response.type == "tool_call":
                        tool_calls = response.tool_calls or []
                        
                        # CRITICAL: Do NOT reinitialize tool_results_to_add here
                        # It's already initialized at line 698, reusing is correct
                        # Each tool_call response will append to the same list

                        # Execute tools
                        for tc in tool_calls:
                            tool = next((t for t in tools if t.name == tc["name"]), None)
                            if tool:
                                # Emit TOOL_EXECUTION_START event (pi-mono alignment)
                                start_event = Event(
                                    type=EventType.TOOL_EXECUTION_START,
                                    source="agent-runtime",
                                    session_id=session.session_id if session else None,
                                    data={
                                        "tool_call_id": tc["id"],
                                        "tool_name": tc["name"],
                                        "args": tc["arguments"],
                                    },
                                )
                                await self._notify_observers(start_event)
                                yield start_event
                                
                                # Format tool use (legacy event)
                                formatted_use = self.tool_formatter.format_tool_use(
                                    tc["name"], tc["arguments"]
                                )

                                event = AgentEvent(
                                    "tool_use",
                                    {
                                        "tool": tc["name"],
                                        "input": tc["arguments"],
                                        "formatted": formatted_use,
                                    },
                                )
                                await self._notify_observers(event)
                                yield event

                                # Execute tool
                                try:
                                    # Note: Steering messages support (pi-mono alignment) can be added later
                                    # when get_steering_messages callback is implemented in callers
                                    
                                    # Create cancellation signal
                                    cancel_signal = asyncio.Event()
                                    
                                    # Define streaming update callback
                                    def handle_tool_update(update_result):
                                        """Handle tool streaming updates"""
                                        # Emit intermediate update event
                                        update_event = Event(
                                            type=EventType.TOOL_EXECUTION_UPDATE,
                                            source="agent-runtime",
                                            session_id=session.session_id if session else None,
                                            data={
                                                "tool_call_id": tc["id"],
                                                "tool_name": tc["name"],
                                                "partial_result": {
                                                    "content": [c.model_dump() if hasattr(c, 'model_dump') else str(c) for c in update_result.content],
                                                    "details": update_result.details,
                                                },
                                            },
                                        )
                                        # Async notify (non-blocking)
                                        asyncio.create_task(self._notify_observers(update_event))
                                    
                                    # Check if tool supports new interface (has execute with 4 params)
                                    # Try new interface first
                                    try:
                                        from inspect import signature
                                        sig = signature(tool.execute)
                                        param_count = len([p for p in sig.parameters.values() if p.default == p.empty])
                                        
                                        if param_count >= 2:  # new interface: tool_call_id, params (signal, on_update optional)
                                            result = await tool.execute(
                                                tool_call_id=tc["id"],
                                                params=tc["arguments"],
                                                signal=cancel_signal,
                                                on_update=handle_tool_update,
                                            )
                                        else:
                                            # Legacy interface
                                            result = await tool.execute(tc["arguments"])
                                    except Exception:
                                        # Fallback to legacy interface
                                        result = await tool.execute(tc["arguments"])
                                    
                                    # Handle AgentToolResult vs LegacyToolResult
                                    from .types import AgentToolResult
                                    if isinstance(result, AgentToolResult):
                                        success = True
                                        # Merge content into text
                                        # CRITICAL FIX: Ensure result.content is not None and is iterable
                                        content_list = result.content if result.content else []
                                        output = "\n".join(
                                            c.text if hasattr(c, 'text') else str(c)
                                            for c in content_list
                                        ) or "No output"  # Ensure never empty string
                                        result_metadata = result.details or {}
                                    else:
                                        # Legacy ToolResult
                                        success = result.success if result else False
                                        # CRITICAL FIX: Ensure output is never None
                                        output = (result.content if result else None) or "No output"
                                        result_metadata = result.metadata if hasattr(result, 'metadata') else {}

                                    # Format tool result
                                    formatted_result = self.tool_formatter.format_tool_result(
                                        tc["name"], output, success
                                    )

                                    event = AgentEvent(
                                        "tool_result",
                                        {
                                            "tool": tc["name"],
                                            "result": output,
                                            "success": success,
                                            "formatted": formatted_result,
                                        },
                                    )
                                    await self._notify_observers(event)
                                    yield event

                                    # Check if tool generated a file (e.g., PPT, PDF, image)
                                    # Check both content (JSON string) and metadata
                                    file_path = None
                                    file_type = "document"
                                    caption = None
                                    
                                    if success:
                                        # 1. Try to parse output as JSON
                                        if isinstance(output, str):
                                            try:
                                                import json
                                                parsed = json.loads(output)
                                                if isinstance(parsed, dict):
                                                    file_path = parsed.get("file_path") or parsed.get("path")
                                                    file_type = parsed.get("file_type", "document")
                                                    caption = parsed.get("caption")
                                            except (json.JSONDecodeError, ValueError):
                                                pass
                                        elif isinstance(output, dict):
                                            file_path = output.get("file_path") or output.get("path")
                                            file_type = output.get("file_type", "document")
                                            caption = output.get("caption")
                                        
                                        # 2. Check metadata if not found in content
                                        if not file_path and result_metadata:
                                            file_path = result_metadata.get("file_path") or result_metadata.get("path")
                                            file_type = result_metadata.get("file_type", "document")
                                            caption = result_metadata.get("caption")
                                    
                                    if file_path:
                                        from pathlib import Path
                                        file_path_obj = Path(file_path)
                                        
                                        if file_path_obj.exists():
                                            # Emit file generated event
                                            file_event = Event(
                                                type=EventType.AGENT_FILE_GENERATED,
                                                source="agent-runtime",
                                                session_id=session.session_id if session else None,
                                                data={
                                                    "file_path": str(file_path_obj),
                                                    "file_type": file_type,
                                                    "file_name": file_path_obj.name,
                                                    "caption": caption or file_path_obj.stem,
                                                },
                                            )
                                            await self._notify_observers(file_event)
                                            yield file_event
                                            logger.info(f"📎 File generated and event emitted: {file_path_obj.name}")

                                    # Store tool result to add later (after assistant message)
                                    tool_results_to_add.append({
                                        "tool_call_id": tc["id"],
                                        "content": output,
                                        "name": tc["name"],
                                        "success": success
                                    })
                                    
                                    # Emit TOOL_EXECUTION_END event (success)
                                    end_event = Event(
                                        type=EventType.TOOL_EXECUTION_END,
                                        source="agent-runtime",
                                        session_id=session.session_id if session else None,
                                        data={
                                            "tool_call_id": tc["id"],
                                            "tool_name": tc["name"],
                                            "result": output,
                                            "success": success,
                                            "is_error": False,
                                        },
                                    )
                                    await self._notify_observers(end_event)
                                    yield end_event

                                except Exception as tool_error:
                                    error_msg = str(tool_error)
                                    formatted_error = self.tool_formatter.format_tool_result(
                                        tc["name"], error_msg, success=False
                                    )

                                    event = AgentEvent(
                                        "tool_result",
                                        {
                                            "tool": tc["name"],
                                            "result": error_msg,
                                            "success": False,
                                            "error": error_msg,
                                            "formatted": formatted_error,
                                        },
                                    )
                                    await self._notify_observers(event)
                                    yield event

                                    # Store error result to add later
                                    tool_results_to_add.append({
                                        "tool_call_id": tc["id"],
                                        "content": f"Error: {error_msg}",
                                        "name": tc["name"],
                                        "success": False
                                    })
                                    
                                    # Emit TOOL_EXECUTION_END event (error)
                                    end_event = Event(
                                        type=EventType.TOOL_EXECUTION_END,
                                        source="agent-runtime",
                                        session_id=session.session_id if session else None,
                                        data={
                                            "tool_call_id": tc["id"],
                                            "tool_name": tc["name"],
                                            "result": error_msg,
                                            "success": False,
                                            "is_error": True,
                                            "error": error_msg,
                                        },
                                    )
                                    await self._notify_observers(end_event)
                                    yield end_event

                    elif response.type == "done":
                        # Extract thinking if ON mode
                        final_text = accumulated_text or ""  # Ensure never None
                        if self.thinking_mode == ThinkingMode.ON and self.thinking_extractor:
                            extracted = self.thinking_extractor.extract(accumulated_text or "")
                            if extracted.has_thinking:
                                # Include thinking in response
                                event = AgentEvent(
                                    "thinking", {"content": extracted.thinking, "mode": "on"}
                                )
                                await self._notify_observers(event)
                                yield event
                                # CRITICAL FIX: Ensure extracted.content is never None
                                final_text = extracted.content or ""

                        # Add tool results to session FIRST (before assistant message)
                        if tool_results_to_add:
                            logger.debug(f"Adding {len(tool_results_to_add)} tool results to session")
                            for tr in tool_results_to_add:
                                session.add_tool_message(
                                    tool_call_id=tr["tool_call_id"],
                                    content=tr["content"],
                                    name=tr["name"]
                                )

                        # If there were tool calls, we need to continue the conversation
                        # to let the model generate a response based on tool results
                        if tool_calls:
                            logger.info(f"🔧 Tool calls completed ({len(tool_calls)} calls), will request final response from model")
                            needs_tool_response = True
                            # Store the initial text and tool_calls for later merging
                            initial_text = final_text
                            initial_tool_calls = tool_calls
                            logger.info(f"📌 Set needs_tool_response=True, initial_tool_calls={len(initial_tool_calls)}")
                            # Don't add assistant message yet - wait for final response
                            # Don't break yet - we'll make another API call after this loop
                        else:
                            # No tool calls - add assistant message now
                            # CRITICAL FIX: Ensure content is never None, even if empty
                            final_text_safe = final_text or ""
                            if final_text_safe or tool_calls:
                                session.add_assistant_message(final_text_safe, tool_calls)
                                logger.debug(f"Added assistant message to session (text={len(final_text_safe)} chars, tool_calls={len(tool_calls)})")
                            
                            # Record success for failover manager
                            if self.fallback_manager:
                                self.fallback_manager.record_success(current_model)

                        break

                    elif response.type == "error":
                        raise Exception(response.content)

                # If we need to get a response after tool execution, make another API call
                # But limit iterations to prevent infinite loops
                logger.info(f"🔍 Checking needs_tool_response: {needs_tool_response}, iterations: {tool_call_iterations}/{MAX_TOOL_ITERATIONS}")
                if needs_tool_response and tool_call_iterations < MAX_TOOL_ITERATIONS:
                    tool_call_iterations += 1
                    logger.info("📞 Making follow-up API call to get response based on tool results")
                elif needs_tool_response and tool_call_iterations >= MAX_TOOL_ITERATIONS:
                    logger.error(f"🔴 Maximum tool iterations ({MAX_TOOL_ITERATIONS}) reached. Stopping to prevent infinite loop.")
                    # Provide fallback response
                    fallback_text = "I've executed multiple tools but encountered difficulty generating a final response. The tool results have been processed."
                    session.add_assistant_message(content=fallback_text)
                    
                    # Send text event
                    text_event = Event(
                        type=EventType.TEXT,
                        source="agent-runtime",
                        session_id=session.session_id if session else None,
                        data={"delta": {"text": fallback_text}},
                    )
                    await self._notify_observers(text_event)
                    yield text_event
                    
                    needs_tool_response = False
                    
                    # Rebuild messages with tool results
                    # CRITICAL: Apply same history limiting as initial call
                    followup_all_messages = session.get_messages()
                    followup_messages_dict = [
                        {
                            "role": m.role,
                            "content": m.content,
                            "tool_calls": getattr(m, 'tool_calls', None),
                            "tool_call_id": getattr(m, 'tool_call_id', None),
                            "name": getattr(m, 'name', None),
                        }
                        for m in followup_all_messages
                    ]
                    followup_sanitized = sanitize_session_history(followup_messages_dict)
                    followup_limited = limit_history_turns(
                        followup_sanitized,
                        max_turns=max_turns,
                        provider=self.provider_name
                    )
                    logger.info(f"🔄 Follow-up limited history: {len(followup_all_messages)} -> {len(followup_limited)} messages")
                    
                    # Convert to LLM messages
                    llm_messages = []
                    for m in followup_limited:
                        llm_messages.append(LLMMessage(
                            role=m["role"],
                            content=m["content"],
                            images=None,  # No images in follow-up
                            tool_calls=m.get("tool_calls"),
                            tool_call_id=m.get("tool_call_id"),
                            name=m.get("name")
                        ))
                    
                    # REMOVED WORKAROUND: Do NOT add extra user message
                    # Gemini requires: function_call -> function_response -> model_response
                    # Adding extra user message breaks this sequence
                    # Instead, pass empty tools array to disable further tool calling
                    
                    # Reset for second response
                    accumulated_text = ""
                    tool_calls = []
                    
                    # CRITICAL: For follow-up call after tools, we need to pass tools
                    # but configure Gemini to prefer text responses
                    # Passing empty tools array causes Gemini to return empty response
                    # when history contains tool messages
                    logger.info(f"🔧 Follow-up call: passing tools but will prefer text response")
                    
                    # Track if follow-up call triggered more tools
                    followup_tool_calls = []
                    
                    async for response in self.provider.stream(
                        messages=llm_messages, 
                        tools=tools_param,  # ✅ Pass tools to maintain consistency with history
                        max_tokens=max_tokens,
                        **self.extra_params
                    ):
                        if response.type == "text_delta":
                            text = response.content
                            accumulated_text += text
                            
                            # Stream text to user
                            event = Event(
                                type=EventType.AGENT_TEXT,
                                source="agent-runtime",
                                session_id=session.session_id if session else None,
                                data={"delta": {"type": "text_delta", "text": text}},
                            )
                            await self._notify_observers(event)
                            yield event
                        
                        elif response.type == "tool_call":
                            # CRITICAL: Tool call in follow-up indicates infinite loop
                            # Stop immediately and return whatever text we have
                            followup_tool_calls = response.tool_calls or []
                            logger.warning(f"🔴 Tool call loop detected in follow-up (iteration {tool_call_iterations}): {[tc['name'] for tc in followup_tool_calls]}")
                            logger.warning(f"🛑 Stopping to prevent infinite loop")
                            
                            # Use accumulated text or provide fallback
                            final_text = accumulated_text or "I've executed the requested tools. The results are ready."
                            session.add_assistant_message(content=final_text)
                            logger.info(f"✅ Added assistant message (loop break): text={len(final_text)} chars")
                            
                            # Send text event if we have text
                            if final_text:
                                text_event = Event(
                                    type=EventType.TEXT,
                                    source="agent-runtime",
                                    session_id=session.session_id if session else None,
                                    data={"delta": {"text": final_text}},
                                )
                                await self._notify_observers(text_event)
                                yield text_event
                            
                            # Stop the loop
                            needs_tool_response = False
                            break
                            
                        elif response.type == "done":
                            # Got final text response
                            final_response_text = accumulated_text or ""  # Ensure never None
                            
                            # Add assistant message
                            if final_response_text:
                                session.add_assistant_message(content=final_response_text)
                                logger.info(f"✅ Added assistant message (follow-up done): text={len(final_response_text)} chars")
                            
                            break
                            
                        elif response.type == "error":
                            raise Exception(response.content)
                    
                    # Record success
                    if self.fallback_manager:
                        self.fallback_manager.record_success(current_model)

                # Emit model.usage diagnostic event (mirrors TS agent-runner.ts)
                try:
                    from openclaw.infra.diagnostic_events import log_model_usage
                    _duration_ms = (_time.monotonic() - _run_started_at) * 1000
                    _session_key = getattr(session, "session_id", "") or ""
                    log_model_usage(
                        session_key=_session_key,
                        provider=self.provider_name or "",
                        model=current_model,
                        usage={
                            "input": 0,
                            "output": 0,
                            "total": 0,
                        },
                        duration_ms=_duration_ms,
                        session_id=_session_key,
                    )
                except Exception as _diag_err:
                    logger.debug(f"model.usage diagnostic emit failed: {_diag_err}")

                # Success, exit retry loop
                event = Event(
                    type=EventType.AGENT_TURN_COMPLETE,
                    source="agent-runtime",
                    session_id=session.session_id if session else None,
                    data={"phase": "end"},
                )
                await self._notify_observers(event)
                yield event
                return

            except asyncio.CancelledError:
                # Handle abort/cancellation
                logger.info("Agent turn was cancelled/aborted")
                event = AgentEvent(
                    "turn_aborted",
                    {"reason": "Cancelled by user or system"}
                )
                await self._notify_observers(event)
                yield event
                raise  # Re-raise to properly propagate cancellation

            except Exception as e:
                # Check if should failover
                should_failover = False
                failover_reason = FailoverReason.UNKNOWN

                if self.fallback_manager:
                    should_failover, failover_reason = self.fallback_manager.should_failover(e)

                    if should_failover:
                        next_model = self.fallback_manager.get_next_model()
                        if next_model:
                            logger.info(f"Failing over from {current_model} to {next_model}")

                            # Update provider for new model
                            self.provider_name, self.model_name = self._parse_model(next_model)
                            self.provider = self._create_provider()

                            event = AgentEvent(
                                "failover",
                                {
                                    "from": current_model,
                                    "to": next_model,
                                    "reason": failover_reason.value,
                                    "error": str(e),
                                },
                            )
                            await self._notify_observers(event)
                            yield event

                            # Continue to next attempt (no sleep, immediate retry with new model)
                            continue

                # Check if retryable
                if not is_retryable_error(e) and not should_failover:
                    logger.error(f"Non-retryable error: {format_error_message(e)}")
                    event = AgentEvent(
                        "error",
                        {"message": format_error_message(e), "category": classify_error(e).value},
                    )
                    await self._notify_observers(event)
                    yield event

                    event = AgentEvent("lifecycle", {"phase": "end"})
                    await self._notify_observers(event)
                    yield event
                    return

                if retry_count >= self.max_retries:
                    logger.error(f"Max retries reached: {format_error_message(e)}")
                    event = AgentEvent(
                        "error",
                        {
                            "message": f"Max retries exceeded: {format_error_message(e)}",
                            "category": classify_error(e).value,
                        },
                    )
                    await self._notify_observers(event)
                    yield event

                    event = AgentEvent("lifecycle", {"phase": "end"})
                    await self._notify_observers(event)
                    yield event
                    return

                # Retry with exponential backoff
                retry_count += 1
                delay = min(2 ** (retry_count - 1), 30)
                logger.warning(f"Retry {retry_count}/{self.max_retries} after {delay}s: {e}")

                event = AgentEvent(
                    "retry",
                    {
                        "attempt": retry_count,
                        "max_retries": self.max_retries,
                        "delay": delay,
                        "error": str(e),
                    },
                )
                await self._notify_observers(event)
                yield event

                await asyncio.sleep(delay)


# Alias for backward compatibility
AgentRuntime = MultiProviderRuntime
