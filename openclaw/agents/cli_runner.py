"""CLI agent runner for OpenClaw Python.

Mirrors TypeScript src/agents/cli-runner.ts implementation.
This module provides functionality to run agents via external CLI processes
(claude-cli, codex-cli, etc.) instead of embedded Pi runtime.
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openclaw.agents.cli_backends import resolve_cli_backend_config
from openclaw.agents.cli_runner_helpers import (
    ImageContent,
    append_image_paths_to_prompt,
    build_cli_args,
    build_cli_supervisor_scope_key,
    enqueue_cli_run,
    normalize_cli_model,
    parse_cli_json,
    parse_cli_jsonl,
    resolve_cli_no_output_timeout_ms,
    resolve_prompt_input,
    resolve_session_id_to_send,
    resolve_system_prompt_usage,
    write_cli_images,
)
from openclaw.process.supervisor import get_process_supervisor

logger = logging.getLogger("openclaw.agents.cli_runner")

# ============================================================================
# Result Types (mirroring TS EmbeddedPiRunResult)
# ============================================================================


@dataclass
class AgentMeta:
    """Agent metadata (mirrors TS structure)."""

    session_id: str
    provider: str
    model: str
    usage: Optional[Dict[str, Any]] = None


@dataclass
class EmbeddedPiRunResult:
    """Run result (mirrors TS EmbeddedPiRunResult)."""

    payloads: Optional[List[Dict[str, str]]]
    meta: Dict[str, Any]


# ============================================================================
# Main CLI Runner (mirroring cli-runner.ts runCliAgent)
# ============================================================================


async def run_cli_agent(
    *,
    session_id: str,
    session_key: Optional[str] = None,
    agent_id: Optional[str] = None,
    session_file: str,
    workspace_dir: str,
    config: Optional[Any] = None,
    prompt: str,
    provider: str,
    model: Optional[str] = None,
    think_level: Optional[str] = None,
    timeout_ms: int,
    run_id: str,
    extra_system_prompt: Optional[str] = None,
    stream_params: Optional[Any] = None,
    owner_numbers: Optional[List[str]] = None,
    cli_session_id: Optional[str] = None,
    images: Optional[List[ImageContent]] = None,
) -> EmbeddedPiRunResult:
    """Run CLI agent (mirrors TS runCliAgent).

    Phase 2 implementation with full system prompt building support.

    Args:
        session_id: Session identifier
        session_key: Session key for storage
        agent_id: Agent identifier
        session_file: Session file path
        workspace_dir: Working directory for agent
        config: OpenClaw configuration
        prompt: User prompt
        provider: CLI provider (e.g., "claude-cli", "codex-cli")
        model: Model identifier
        think_level: Thinking level
        timeout_ms: Overall timeout in milliseconds
        run_id: Run identifier
        extra_system_prompt: Additional system prompt text
        stream_params: Streaming parameters
        owner_numbers: Owner phone numbers
        cli_session_id: Existing CLI session ID to resume
        images: Image attachments

    Returns:
        EmbeddedPiRunResult with payloads and metadata
    """
    started = time.time() * 1000

    # Resolve workspace directory with fallback (mirrors TS cli-runner.ts line 55-70)
    from openclaw.agents.workspace_run import resolve_run_workspace_dir, redact_run_identifier
    
    workspace_resolution = resolve_run_workspace_dir(
        workspace_dir=workspace_dir,
        session_key=session_key,
        agent_id=agent_id,
        config=config,
    )
    resolved_workspace = workspace_resolution["workspace_dir"]
    
    # Log fallback if used
    if workspace_resolution["used_fallback"]:
        redacted_session_id = redact_run_identifier(session_id)
        redacted_session_key = redact_run_identifier(session_key)
        redacted_workspace = redact_run_identifier(resolved_workspace)
        logger.warning(
            f"[workspace-fallback] caller=runCliAgent reason={workspace_resolution['fallback_reason']} "
            f"run={run_id} session={redacted_session_id} sessionKey={redacted_session_key} "
            f"agent={workspace_resolution['agent_id']} workspace={redacted_workspace}"
        )
    
    workspace_dir = resolved_workspace

    # Resolve backend configuration
    backend_resolved = resolve_cli_backend_config(provider, config)
    if not backend_resolved:
        raise ValueError(f"Unknown CLI backend: {provider}")

    backend = backend_resolved.config
    model_id = (model or "default").strip() or "default"
    normalized_model = normalize_cli_model(model_id, backend)
    model_display = f"{provider}/{model_id}"

    # Build system prompt - use full builder with bootstrap, heartbeat, docs
    extra_sp = extra_system_prompt.strip() if extra_system_prompt else ""
    extra_with_tools_disabled = "\n".join(filter(None, [
        extra_sp,
        "Tools are disabled in this session. Do not call tools.",
    ]))
    
    try:
        from pathlib import Path
        from openclaw.agents.system_prompt import build_agent_system_prompt
        
        # Resolve bootstrap context files (mirrors TS resolveBootstrapContextForRun)
        context_files = []
        try:
            from openclaw.agents.bootstrap_context import resolve_bootstrap_context_for_run
            session_label = session_key or session_id
            bootstrap_result = await resolve_bootstrap_context_for_run(
                workspace_dir=workspace_dir,
                config=config,
                session_key=session_key,
                session_id=session_id,
            )
            context_files = bootstrap_result.get("contextFiles") or []
        except Exception as e:
            logger.debug(f"Could not resolve bootstrap context: {e}")
        
        # Resolve session agent IDs (mirrors TS resolveSessionAgentIds)
        session_agent_id = agent_id or "main"
        default_agent_id = "main"
        try:
            from openclaw.agents.agent_scope import resolve_session_agent_id
            session_agent_id = resolve_session_agent_id(
                session_key=session_key,
                config=config,
                agent_id=agent_id,
            )
        except Exception as e:
            logger.debug(f"Could not resolve session agent ID: {e}")
        
        # Resolve heartbeat prompt (mirrors TS resolveHeartbeatPrompt)
        heartbeat_prompt = None
        if session_agent_id == default_agent_id:
            try:
                from openclaw.agents.heartbeat import resolve_heartbeat_prompt
                heartbeat_config = None
                if config and hasattr(config, "agents"):
                    agents_config = getattr(config.agents, "defaults", None)
                    if agents_config:
                        heartbeat_config = getattr(agents_config, "heartbeat", None)
                        if heartbeat_config:
                            heartbeat_config = getattr(heartbeat_config, "prompt", None)
                heartbeat_prompt = resolve_heartbeat_prompt(heartbeat_config)
            except Exception as e:
                logger.debug(f"Could not resolve heartbeat prompt: {e}")
        
        # Resolve OpenClaw docs path (mirrors TS resolveOpenClawDocsPath)
        docs_path = None
        try:
            from openclaw.agents.docs import resolve_open_claw_docs_path
            docs_path = await resolve_open_claw_docs_path(
                workspace_dir=workspace_dir,
            )
        except Exception as e:
            logger.debug(f"Could not resolve docs path: {e}")
        
        # Build full system prompt with all context
        workspace_path = Path(workspace_dir)
        system_prompt = build_agent_system_prompt(
            workspace_dir=workspace_path,
            tool_names=[],  # CLI mode has no tools
            extra_system_prompt=extra_with_tools_disabled,
            owner_numbers=owner_numbers,
            heartbeat_prompt=heartbeat_prompt,
            docs_path=docs_path,
            context_files=context_files,
            agent_id=session_agent_id,
        )
        logger.debug("Using full system prompt builder with bootstrap, heartbeat, and docs")
    except Exception as e:
        # Fallback to simplified system prompt
        logger.warning(f"Using fallback system prompt due to error: {e}")
        system_prompt = extra_with_tools_disabled

    logger.info(f"Starting CLI agent: provider={provider} model={normalized_model}")

    # Helper function to execute CLI with given session ID
    async def execute_cli_with_session(cli_session_id_to_use: Optional[str] = None) -> Dict[str, Any]:
        # Resolve session ID
        resolved = resolve_session_id_to_send(backend, cli_session_id_to_use)
        resolved_session_id = resolved.session_id
        is_new = resolved.is_new

        # Determine if we're using resume mode
        use_resume = bool(
            cli_session_id_to_use
            and resolved_session_id
            and backend.resume_args
            and len(backend.resume_args) > 0
        )

        # Resolve system prompt usage
        system_prompt_arg = resolve_system_prompt_usage(
            backend=backend,
            is_new_session=is_new,
            system_prompt=system_prompt,
        )

        # Handle images
        image_paths: Optional[List[str]] = None
        cleanup_images = None
        prompt_text = prompt

        if images:
            image_payload = await write_cli_images(images)
            image_paths = image_payload["paths"]
            cleanup_images = image_payload["cleanup"]
            if not backend.image_arg:
                prompt_text = append_image_paths_to_prompt(prompt_text, image_paths)

        try:
            # Resolve prompt input (arg vs stdin)
            prompt_input = resolve_prompt_input(backend, prompt_text)
            stdin_payload = prompt_input.stdin or ""

            # Build base args
            base_args = backend.resume_args or backend.args or [] if use_resume else (backend.args or [])
            if use_resume and resolved_session_id:
                base_args = [arg.replace("{sessionId}", resolved_session_id) for arg in base_args]

            # Build full args
            args = build_cli_args(
                backend=backend,
                base_args=base_args,
                model_id=normalized_model,
                session_id=resolved_session_id,
                system_prompt=system_prompt_arg,
                image_paths=image_paths,
                prompt_arg=prompt_input.args_prompt,
                use_resume=use_resume,
            )

            # Serialize runs for this backend
            serialize = backend.serialize if backend.serialize is not None else True
            queue_key = backend_resolved.id if serialize else f"{backend_resolved.id}:{run_id}"

            # Execute CLI
            output = await enqueue_cli_run(queue_key, lambda: _spawn_cli_process(
                backend=backend,
                backend_id=backend_resolved.id,
                session_id=session_id,
                workspace_dir=workspace_dir,
                args=args,
                stdin_payload=stdin_payload,
                timeout_ms=timeout_ms,
                use_resume=use_resume,
                resolved_session_id=resolved_session_id,
                model_id=model_id,
                provider=provider,
            ))

            return output
        finally:
            if cleanup_images:
                await cleanup_images()

    # Try with provided CLI session ID first
    try:
        output = await execute_cli_with_session(cli_session_id)
        text = output["text"].strip() if output.get("text") else ""
        payloads = [{"text": text}] if text else None

        return EmbeddedPiRunResult(
            payloads=payloads,
            meta={
                "durationMs": int(time.time() * 1000 - started),
                "agentMeta": {
                    "sessionId": output.get("sessionId") or cli_session_id or session_id,
                    "provider": provider,
                    "model": model_id,
                    "usage": output.get("usage"),
                },
            },
        )
    except Exception as err:
        # Check for session_expired error and retry without session ID
        error_msg = str(err)
        if "session_expired" in error_msg.lower() and cli_session_id:
            logger.warning(f"CLI session expired, retrying without session: provider={provider}")

            output = await execute_cli_with_session(None)
            text = output["text"].strip() if output.get("text") else ""
            payloads = [{"text": text}] if text else None

            return EmbeddedPiRunResult(
                payloads=payloads,
                meta={
                    "durationMs": int(time.time() * 1000 - started),
                    "agentMeta": {
                        "sessionId": output.get("sessionId") or session_id,
                        "provider": provider,
                        "model": model_id,
                        "usage": output.get("usage"),
                    },
                },
            )
        raise


async def _spawn_cli_process(
    backend: Any,
    backend_id: str,
    session_id: str,
    workspace_dir: str,
    args: List[str],
    stdin_payload: str,
    timeout_ms: int,
    use_resume: bool,
    resolved_session_id: Optional[str],
    model_id: str,
    provider: str,
) -> Dict[str, Any]:
    """Spawn CLI process and parse output (internal helper).

    Args:
        backend: CLI backend configuration
        backend_id: Backend identifier
        session_id: OpenClaw session ID
        workspace_dir: Working directory
        args: CLI arguments
        stdin_payload: Stdin input
        timeout_ms: Overall timeout
        use_resume: Whether this is a resume run
        resolved_session_id: Resolved CLI session ID
        model_id: Model identifier
        provider: Provider name

    Returns:
        Dict with 'text', 'sessionId', and 'usage' fields
    """
    logger.info(f"cli exec: provider={provider} model={model_id} promptChars={len(stdin_payload or '')}")

    # Build environment
    env = dict(os.environ)
    if backend.env:
        env.update(backend.env)
    if backend.clear_env:
        for key in backend.clear_env:
            env.pop(key, None)

    # Resolve no-output timeout
    no_output_timeout_ms = resolve_cli_no_output_timeout_ms(backend, timeout_ms, use_resume)

    # Build scope key
    scope_key = build_cli_supervisor_scope_key(
        backend=backend,
        backend_id=backend_id,
        cli_session_id=resolved_session_id if use_resume else None,
    )

    # Spawn process via supervisor
    supervisor = get_process_supervisor()
    managed_run = await supervisor.spawn(
        session_id=session_id,
        backend_id=backend_id,
        scope_key=scope_key,
        replace_existing_scope=bool(use_resume and scope_key),
        argv=[backend.command] + args,
        timeout_ms=timeout_ms,
        no_output_timeout_ms=no_output_timeout_ms,
        cwd=workspace_dir,
        env=env,
        input_data=stdin_payload if stdin_payload else None,
        stdin_mode="pipe-closed",
    )

    # Wait for result
    result = await managed_run.wait()

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    if os.environ.get("OPENCLAW_CLAUDE_CLI_LOG_OUTPUT"):
        if stdout:
            logger.info(f"cli stdout:\n{stdout}")
        if stderr:
            logger.info(f"cli stderr:\n{stderr}")

    # Check for errors
    if result.exit_code != 0 or result.reason != "exit":
        if result.reason == "no-output-timeout" or result.no_output_timed_out:
            timeout_reason = f"CLI produced no output for {no_output_timeout_ms // 1000}s and was terminated."
            logger.warning(
                f"cli watchdog timeout: provider={provider} model={model_id} "
                f"noOutputTimeoutMs={no_output_timeout_ms}"
            )
            raise Exception(f"timeout: {timeout_reason}")

        if result.reason == "overall-timeout":
            timeout_reason = f"CLI exceeded timeout ({timeout_ms // 1000}s) and was terminated."
            raise Exception(f"timeout: {timeout_reason}")

        err = stderr or stdout or "CLI failed."
        raise Exception(err)

    # Parse output
    output_mode = (backend.resume_output or backend.output) if use_resume else backend.output

    if output_mode == "text":
        return {"text": stdout, "sessionId": None}

    if output_mode == "jsonl":
        parsed = parse_cli_jsonl(stdout, backend)
        if parsed:
            return {
                "text": parsed.text,
                "sessionId": parsed.session_id,
                "usage": parsed.usage.__dict__ if parsed.usage else None,
            }
        return {"text": stdout}

    # Default: json
    parsed = parse_cli_json(stdout, backend)
    if parsed:
        return {
            "text": parsed.text,
            "sessionId": parsed.session_id,
            "usage": parsed.usage.__dict__ if parsed.usage else None,
        }
    return {"text": stdout}


# ============================================================================
# Convenience wrapper (mirroring runClaudeCliAgent)
# ============================================================================


async def run_claude_cli_agent(
    *,
    session_id: str,
    session_key: Optional[str] = None,
    agent_id: Optional[str] = None,
    session_file: str,
    workspace_dir: str,
    config: Optional[Any] = None,
    prompt: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    think_level: Optional[str] = None,
    timeout_ms: int,
    run_id: str,
    extra_system_prompt: Optional[str] = None,
    owner_numbers: Optional[List[str]] = None,
    claude_session_id: Optional[str] = None,
    images: Optional[List[ImageContent]] = None,
) -> EmbeddedPiRunResult:
    """Run Claude CLI agent (convenience wrapper).

    Mirrors TS runClaudeCliAgent function.
    """
    return await run_cli_agent(
        session_id=session_id,
        session_key=session_key,
        agent_id=agent_id,
        session_file=session_file,
        workspace_dir=workspace_dir,
        config=config,
        prompt=prompt,
        provider=provider or "claude-cli",
        model=model or "opus",
        think_level=think_level,
        timeout_ms=timeout_ms,
        run_id=run_id,
        extra_system_prompt=extra_system_prompt,
        owner_numbers=owner_numbers,
        cli_session_id=claude_session_id,
        images=images,
    )
