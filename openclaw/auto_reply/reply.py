"""Core get_reply implementation.

Main entry point for getting automated replies from agents.
Aligned with TypeScript src/auto-reply/reply/get-reply.ts
"""

from __future__ import annotations

import uuid
import asyncio
from pathlib import Path
from typing import Optional, Any

from openclaw.config.loader import load_config
from openclaw.config.schema import OpenClawConfig
from openclaw.agents.runtime import Agent
from openclaw.agents.context import AgentContext
from openclaw.routing.session_key import parse_session_key, build_agent_main_session_key

from .types import ReplyPayload, GetReplyOptions, ModelSelectedContext
from .directives import (
    extract_think_directive,
    extract_verbose_directive,
    extract_elevated_directive,
    extract_reasoning_directive
)
from .streaming_directives import create_streaming_directive_accumulator
from .tokens import SILENT_REPLY_TOKEN


async def get_reply(
    session_key: str,
    user_message: str,
    options: Optional[GetReplyOptions] = None,
    config_override: Optional[OpenClawConfig] = None
) -> Optional[ReplyPayload | list[ReplyPayload]]:
    """Get automated reply from agent.
    
    This is the main entry point for Auto-Reply system.
    
    Args:
        session_key: Session key for routing
        user_message: User's message text
        options: Optional reply options
        config_override: Optional config override
    
    Returns:
        ReplyPayload or list of ReplyPayload, or None if no reply
    """
    opts = options or GetReplyOptions()
    cfg = config_override or load_config()
    
    # Parse session key to get agent ID
    parsed_key = parse_session_key(session_key)
    agent_id = parsed_key.get("agent_id", "default")
    session_id = parsed_key.get("session_id", str(uuid.uuid4()))
    
    # Resolve agent configuration
    agent_cfg = cfg.agents.get(agent_id, {}) if cfg.agents else {}
    defaults_cfg = cfg.agents.get("defaults", {}) if cfg.agents else {}
    
    # Merge configuration
    merged_cfg = {**defaults_cfg, **agent_cfg}
    
    # Get model configuration
    provider = merged_cfg.get("provider", "anthropic")
    model = merged_cfg.get("model", "claude-3-5-sonnet-20241022")
    
    # Extract directives from user message
    think_result = extract_think_directive(user_message)
    verbose_result = extract_verbose_directive(think_result.cleaned)
    elevated_result = extract_elevated_directive(verbose_result.cleaned)
    reasoning_result = extract_reasoning_directive(elevated_result.cleaned)

    # Persist inline /elevated directive to session (mirrors TS directive-handling.persist.ts)
    # The slash-command handler (_handle_elevated) enforces allowFrom; we intentionally skip
    # the gate here so that inline directives embedded in multi-turn system prompts still work.
    # If a stricter gate is needed per-turn, wire ctx through get_reply.
    if elevated_result.has_directive and elevated_result.elevated_level:
        try:
            from openclaw.agents.sessions import patch_session_entry as _patch_elevated
            _normalized = "ask" if elevated_result.elevated_level == "on" else elevated_result.elevated_level
            _patch_elevated(session_key, {"elevatedLevel": _normalized}, cfg)
        except Exception:
            pass

    # Use cleaned message without directives
    cleaned_message = reasoning_result.cleaned

    # Apply directive overrides
    think_level = think_result.think_level
    verbose = verbose_result.verbose_level == "on" if verbose_result.verbose_level else None
    
    # Resolve workspace directory
    workspace_dir = merged_cfg.get("workspace", "./workspace")
    workspace_path = Path(workspace_dir)
    workspace_path.mkdir(parents=True, exist_ok=True)
    
    # Filter skills if specified
    skill_filter = opts.skill_filter
    
    # Notify model selection
    if opts.on_model_selected:
        opts.on_model_selected(
            ModelSelectedContext(
                provider=provider,
                model=model,
                think_level=think_level
            )
        )
    
    # Create agent context
    context = AgentContext(
        session_key=session_key,
        session_id=session_id,
        agent_id=agent_id,
        workspace_dir=workspace_path,
        config=cfg
    )
    
    # Create run ID
    run_id = opts.run_id or str(uuid.uuid4())
    
    # Notify agent run start
    if opts.on_agent_run_start:
        opts.on_agent_run_start(run_id)
    
    # Notify reply start
    if opts.on_reply_start:
        result = opts.on_reply_start()
        if asyncio.iscoroutine(result):
            await result
    
    # Create agent
    agent = Agent(
        agent_id=agent_id,
        provider=provider,
        model=model,
        workspace_dir=workspace_path,
        config=cfg
    )
    
    # Initialize streaming directive accumulator
    accumulator = create_streaming_directive_accumulator()
    
    # Response accumulation
    full_response = ""
    reply_payload = ReplyPayload()
    
    try:
        # Run agent
        async for chunk in agent.run_stream(
            messages=[{"role": "user", "content": cleaned_message}],
            context=context,
            run_id=run_id,
            think_level=think_level
        ):
            # Handle different chunk types
            if isinstance(chunk, dict):
                chunk_type = chunk.get("type")
                
                if chunk_type == "text":
                    # Accumulate text
                    text = chunk.get("content", "")
                    full_response += text
                    
                    # Parse directives from streaming output
                    parsed = accumulator.consume(text, final=False, silent_token=SILENT_REPLY_TOKEN)
                    
                    if parsed and opts.on_partial_reply:
                        # Create partial reply payload
                        partial_payload = ReplyPayload(
                            text=parsed.text,
                            media_url=parsed.media_url,
                            media_urls=parsed.media_urls,
                            reply_to_id=parsed.reply_to_id,
                            reply_to_tag=parsed.reply_to_tag,
                            reply_to_current=parsed.reply_to_current,
                            audio_as_voice=parsed.audio_as_voice,
                            is_silent=parsed.is_silent
                        )
                        
                        result = opts.on_partial_reply(partial_payload)
                        if asyncio.iscoroutine(result):
                            await result
                
                elif chunk_type == "thinking" and opts.on_reasoning_stream:
                    # Handle thinking/reasoning chunks
                    thinking_text = chunk.get("content", "")
                    reasoning_payload = ReplyPayload(text=thinking_text)
                    
                    result = opts.on_reasoning_stream(reasoning_payload)
                    if asyncio.iscoroutine(result):
                        await result
                
                elif chunk_type == "tool_use" and opts.on_tool_result:
                    # Handle tool results
                    tool_name = chunk.get("tool_name", "")
                    tool_result = chunk.get("result", "")
                    tool_payload = ReplyPayload(
                        text=f"Used tool: {tool_name}\nResult: {tool_result}"
                    )
                    
                    result = opts.on_tool_result(tool_payload)
                    if asyncio.iscoroutine(result):
                        await result
        
        # Finalize accumulated output
        final_parsed = accumulator.consume("", final=True, silent_token=SILENT_REPLY_TOKEN)
        
        if final_parsed:
            reply_payload = ReplyPayload(
                text=final_parsed.text or full_response,
                media_url=final_parsed.media_url,
                media_urls=final_parsed.media_urls,
                reply_to_id=final_parsed.reply_to_id,
                reply_to_tag=final_parsed.reply_to_tag,
                reply_to_current=final_parsed.reply_to_current,
                audio_as_voice=final_parsed.audio_as_voice,
                is_silent=final_parsed.is_silent
            )
        else:
            reply_payload = ReplyPayload(text=full_response)
        
        # Check for silent reply
        if reply_payload.is_silent or not reply_payload.has_content():
            return None
        
        # Call on_block_reply if provided
        if opts.on_block_reply:
            result = opts.on_block_reply(reply_payload)
            if asyncio.iscoroutine(result):
                await result
        
        return reply_payload
    
    except Exception as e:
        # Return error payload
        error_payload = ReplyPayload(
            text=f"Error: {str(e)}",
            is_error=True
        )
        return error_payload


async def get_reply_from_config(
    session_key: str,
    user_message: str,
    options: Optional[GetReplyOptions] = None,
    config_override: Optional[OpenClawConfig] = None
) -> Optional[ReplyPayload | list[ReplyPayload]]:
    """Get reply from agent using configuration.
    
    Wrapper around get_reply that loads configuration.
    
    Args:
        session_key: Session key
        user_message: User message
        options: Reply options
        config_override: Optional config override
    
    Returns:
        Reply payload or None
    """
    return await get_reply(
        session_key=session_key,
        user_message=user_message,
        options=options,
        config_override=config_override
    )
