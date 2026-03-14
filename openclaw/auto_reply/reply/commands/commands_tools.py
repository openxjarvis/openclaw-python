"""Tool/utility commands.

Port of TypeScript:
  commands-bash.ts    → /bash
  commands-subagents.ts → /subagents
  commands-allowlist.ts + commands-approve.ts → /allowlist, /approve
  commands-ptt.ts     → /ptt (push-to-talk)
  commands-tts.ts     → /tts (text-to-speech)
  commands-plugin.ts  → /plugin
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any

from ..get_reply import ReplyPayload

logger = logging.getLogger(__name__)


async def handle_tools_command(
    name: str,
    args: str,
    ctx: Any,
    cfg: dict[str, Any],
    session_key: str,
    runtime: Any,
) -> ReplyPayload | None:
    if name in ("bash", "shell"):
        return await _handle_bash(args, ctx, cfg, session_key)
    if name == "subagents":
        return await _handle_subagents(args, ctx, cfg, session_key)
    if name == "allowlist":
        return await _handle_allowlist(args, ctx, cfg)
    if name == "approve":
        return await _handle_approve(args, ctx, cfg)
    if name == "ptt":
        return await _handle_ptt(args, ctx, cfg, session_key)
    if name == "tts":
        return await _handle_tts(args, ctx, cfg, session_key)
    if name == "plugin":
        return await _handle_plugin(args, ctx, cfg)
    return None


# ---------------------------------------------------------------------------
# /bash <command>  (mirrors TS commands-bash.ts → handleBashChatCommand)
# ---------------------------------------------------------------------------

_BASH_TIMEOUT_SECONDS = 30
_BASH_MAX_OUTPUT_CHARS = 4096


async def _handle_bash(
    args: str,
    ctx: Any,
    cfg: dict[str, Any],
    session_key: str,
) -> ReplyPayload:
    if not args.strip():
        return ReplyPayload(text="Usage: /bash <command>")

    # Authorization check — only allowed senders may run bash
    authorized = getattr(ctx, "CommandAuthorized", None)
    if authorized is False:
        return ReplyPayload(text="Not authorized to run bash commands.")

    try:
        result = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            ),
            timeout=_BASH_TIMEOUT_SECONDS,
        )
        stdout_bytes, _ = await result.communicate()
        output = stdout_bytes.decode("utf-8", errors="replace")
        exit_code = result.returncode or 0
    except asyncio.TimeoutError:
        return ReplyPayload(text=f"Command timed out after {_BASH_TIMEOUT_SECONDS}s")
    except Exception as exc:
        return ReplyPayload(text=f"Error running command: {exc}")

    if len(output) > _BASH_MAX_OUTPUT_CHARS:
        output = output[:_BASH_MAX_OUTPUT_CHARS] + "\n[…truncated]"

    status = "✅" if exit_code == 0 else f"❌ (exit {exit_code})"
    return ReplyPayload(text=f"```\n{output.rstrip()}\n```\n{status}")


# ---------------------------------------------------------------------------
# /subagents
# ---------------------------------------------------------------------------

async def _handle_subagents(
    args: str,
    ctx: Any,
    cfg: dict[str, Any],
    session_key: str,
) -> ReplyPayload:
    """Handle /subagents commands with full action support"""
    tokens = args.strip().split()
    if not tokens or tokens[0].lower() == "list":
        return await _subagents_list(session_key, cfg)
    
    action = tokens[0].lower()
    rest_tokens = tokens[1:]
    
    if action in ("stop", "kill", "abort"):
        # Simple stop all - legacy behavior
        return await _subagents_stop(session_key, cfg)
    elif action == "log":
        return await _subagents_log(session_key, cfg, rest_tokens)
    elif action == "info":
        return await _subagents_info(session_key, cfg, rest_tokens)
    elif action == "send":
        return await _subagents_send(session_key, cfg, rest_tokens, ctx)
    elif action == "steer":
        return await _subagents_steer(session_key, cfg, rest_tokens, ctx)
    elif action == "spawn":
        return await _subagents_spawn(session_key, cfg, rest_tokens, ctx)
    
    return ReplyPayload(text="Usage: /subagents [list|log|info|send|steer|spawn|stop]")


async def _subagents_list(session_key: str, cfg: dict[str, Any]) -> ReplyPayload:
    try:
        from openclaw.agents.subagent_registry import list_subagent_runs_for_requester
        from openclaw.routing.session_key import normalize_main_key
        requester = normalize_main_key(session_key) if session_key else session_key
        runs = list_subagent_runs_for_requester(requester)
        if not runs:
            return ReplyPayload(text="No sub-agents running.")
        active = [r for r in runs if not r.get("ended_at")]
        done = len(runs) - len(active)
        lines = [f"Sub-agents: {len(active)} active, {done} done"]
        for r in active[:5]:
            label = r.get("label") or r.get("child_session_key") or r.get("run_id") or "(unknown)"
            lines.append(f"  ● {label}")
        return ReplyPayload(text="\n".join(lines))
    except Exception as exc:
        return ReplyPayload(text=f"Could not list sub-agents: {exc}")


async def _subagents_stop(session_key: str, cfg: dict[str, Any]) -> ReplyPayload:
    try:
        from ..get_reply import set_abort_memory, format_abort_reply_text
        if session_key:
            set_abort_memory(session_key, True)
        return ReplyPayload(text=format_abort_reply_text())
    except Exception as exc:
        return ReplyPayload(text=f"Could not stop sub-agents: {exc}")


async def _subagents_log(session_key: str, cfg: dict[str, Any], rest_tokens: list[str]) -> ReplyPayload:
    """Show subagent log/history"""
    if not rest_tokens:
        return ReplyPayload(text="📜 Usage: /subagents log <id|#> [limit] [tools]")
    
    target = rest_tokens[0]
    include_tools = "tools" in [t.lower() for t in rest_tokens]
    
    # Parse limit
    limit = 20
    for token in rest_tokens[1:]:
        if token.isdigit():
            limit = min(200, max(1, int(token)))
            break
    
    try:
        from openclaw.agents.subagent_registry import get_global_registry
        from openclaw.routing.session_key import normalize_main_key
        
        registry = get_global_registry()
        requester = normalize_main_key(session_key) if session_key else session_key
        runs = registry.list_runs_for_requester(requester)
        
        # Resolve target
        run = _resolve_subagent_target(runs, target)
        if isinstance(run, str):  # Error message
            return ReplyPayload(text=run)
        
        child_session_key = run.child_session_key
        
        # Get history via gateway call (would need gateway client)
        # For now, return basic info
        label = run.label or child_session_key or run.run_id or "(unknown)"
        return ReplyPayload(text=f"📜 Subagent log: {label}\n(History retrieval not yet implemented)")
        
    except Exception as exc:
        return ReplyPayload(text=f"Could not get subagent log: {exc}")


async def _subagents_info(session_key: str, cfg: dict[str, Any], rest_tokens: list[str]) -> ReplyPayload:
    """Show subagent detailed info"""
    if not rest_tokens:
        return ReplyPayload(text="ℹ️ Usage: /subagents info <id|#>")
    
    target = rest_tokens[0]
    
    try:
        from openclaw.agents.subagent_registry import get_global_registry
        from openclaw.routing.session_key import normalize_main_key
        import time
        
        registry = get_global_registry()
        requester = normalize_main_key(session_key) if session_key else session_key
        runs = registry.list_runs_for_requester(requester)
        
        # Resolve target
        run = _resolve_subagent_target(runs, target)
        if isinstance(run, str):  # Error message
            return ReplyPayload(text=run)
        
        # Format timestamps
        def format_ts(ts_ms):
            if not ts_ms:
                return "n/a"
            now_ms = int(time.time() * 1000)
            age_ms = now_ms - ts_ms
            age_s = age_ms // 1000
            if age_s < 60:
                age_str = f"{age_s}s ago"
            elif age_s < 3600:
                age_str = f"{age_s // 60}m ago"
            else:
                age_str = f"{age_s // 3600}h ago"
            return f"{age_str}"
        
        # Calculate runtime
        runtime = "n/a"
        if run.started_at:
            end_ms = run.ended_at or int(time.time() * 1000)
            runtime_ms = end_ms - run.started_at
            runtime_s = runtime_ms // 1000
            if runtime_s < 60:
                runtime = f"{runtime_s}s"
            elif runtime_s < 3600:
                runtime = f"{runtime_s // 60}m {runtime_s % 60}s"
            else:
                runtime = f"{runtime_s // 3600}h {(runtime_s % 3600) // 60}m"
        
        # Status
        if run.ended_at:
            status = "finished"
        elif run.started_at:
            status = "running"
        else:
            status = "pending"
        
        label = run.label or run.child_session_key or run.run_id or "(unknown)"
        
        lines = [
            "ℹ️ Subagent info",
            f"Status: {status}",
            f"Label: {label}",
            f"Task: {run.task}",
            f"Run: {run.run_id}",
            f"Session: {run.child_session_key}",
            f"Runtime: {runtime}",
            f"Created: {format_ts(run.created_at)}",
            f"Started: {format_ts(run.started_at)}",
            f"Ended: {format_ts(run.ended_at)}",
            f"Cleanup: {run.cleanup}",
        ]
        
        if run.archive_at_ms:
            lines.append(f"Archive: {format_ts(run.archive_at_ms)}")
        
        return ReplyPayload(text="\n".join(lines))
        
    except Exception as exc:
        return ReplyPayload(text=f"Could not get subagent info: {exc}")


async def _subagents_send(session_key: str, cfg: dict[str, Any], rest_tokens: list[str], ctx: Any) -> ReplyPayload:
    """Send message to subagent"""
    if len(rest_tokens) < 2:
        return ReplyPayload(text="Usage: /subagents send <id|#> <message>")
    
    target = rest_tokens[0]
    message = " ".join(rest_tokens[1:])
    
    try:
        from openclaw.agents.subagent_registry import get_global_registry
        from openclaw.routing.session_key import normalize_main_key
        
        registry = get_global_registry()
        requester = normalize_main_key(session_key) if session_key else session_key
        runs = registry.list_runs_for_requester(requester)
        
        # Resolve target
        run = _resolve_subagent_target(runs, target)
        if isinstance(run, str):  # Error message
            return ReplyPayload(text=run)
        
        child_session_key = run.child_session_key
        label = run.label or child_session_key or run.run_id or "(unknown)"
        
        # Send message via gateway (would need gateway client)
        return ReplyPayload(text=f"✉️ Message sent to {label}\n(Gateway integration not yet implemented)")
        
    except Exception as exc:
        return ReplyPayload(text=f"Could not send message: {exc}")


async def _subagents_steer(session_key: str, cfg: dict[str, Any], rest_tokens: list[str], ctx: Any) -> ReplyPayload:
    """Steer (abort and redirect) subagent"""
    if len(rest_tokens) < 2:
        return ReplyPayload(text="Usage: /subagents steer <id|#> <message>")
    
    target = rest_tokens[0]
    message = " ".join(rest_tokens[1:])
    
    try:
        from openclaw.agents.subagent_registry import get_global_registry
        from openclaw.routing.session_key import normalize_main_key
        
        registry = get_global_registry()
        requester = normalize_main_key(session_key) if session_key else session_key
        runs = registry.list_runs_for_requester(requester)
        
        # Resolve target
        run = _resolve_subagent_target(runs, target)
        if isinstance(run, str):  # Error message
            return ReplyPayload(text=run)
        
        if run.ended_at:
            label = run.label or run.child_session_key or run.run_id or "(unknown)"
            return ReplyPayload(text=f"{label} is already finished.")
        
        child_session_key = run.child_session_key
        label = run.label or child_session_key or run.run_id or "(unknown)"
        
        # Abort and send new message (would need gateway client + abort mechanism)
        return ReplyPayload(text=f"🔄 Steering {label}...\n(Steer mechanism not yet implemented)")
        
    except Exception as exc:
        return ReplyPayload(text=f"Could not steer subagent: {exc}")


async def _subagents_spawn(session_key: str, cfg: dict[str, Any], rest_tokens: list[str], ctx: Any) -> ReplyPayload:
    """Manually spawn a subagent"""
    if len(rest_tokens) < 2:
        return ReplyPayload(text="Usage: /subagents spawn <agentId> <task> [--model <model>] [--thinking <level>]")
    
    agent_id = rest_tokens[0]
    task_parts = []
    model = None
    thinking = None
    
    i = 1
    while i < len(rest_tokens):
        if rest_tokens[i] == "--model" and i + 1 < len(rest_tokens):
            model = rest_tokens[i + 1]
            i += 2
        elif rest_tokens[i] == "--thinking" and i + 1 < len(rest_tokens):
            thinking = rest_tokens[i + 1]
            i += 2
        else:
            task_parts.append(rest_tokens[i])
            i += 1
    
    task = " ".join(task_parts)
    if not task:
        return ReplyPayload(text="Task cannot be empty")
    
    try:
        from openclaw.agents.subagent_spawn import spawn_subagent_direct, SpawnSubagentParams
        from openclaw.routing.session_key import normalize_main_key
        
        requester = normalize_main_key(session_key) if session_key else session_key
        
        params = SpawnSubagentParams(
            task=task,
            agentId=agent_id,
            model=model,
            thinking=thinking,
            mode="run",
            cleanup="keep",
            expectsCompletionMessage=True,
        )
        
        # Context from current session
        context = {
            "agentSessionKey": requester,
            "agentChannel": getattr(ctx, "OriginatingChannel", None) or getattr(ctx, "Channel", None),
            "agentAccountId": getattr(ctx, "AccountId", None),
            "agentTo": getattr(ctx, "To", None),
            "agentThreadId": getattr(ctx, "MessageThreadId", None),
            "agentGroupId": None,
            "agentGroupChannel": None,
            "agentGroupSpace": None,
        }
        
        result = await spawn_subagent_direct(params, context)
        
        if result.status == "accepted":
            short_run_id = result.runId[:8] if result.runId else "unknown"
            return ReplyPayload(
                text=f"Spawned subagent {agent_id} (session {result.childSessionKey}, run {short_run_id})."
            )
        else:
            return ReplyPayload(text=f"Spawn failed: {result.error or result.status}")
        
    except Exception as exc:
        return ReplyPayload(text=f"Could not spawn subagent: {exc}")


def _resolve_subagent_target(runs: list, target: str):
    """
    Resolve target ID/label/number to a run record.
    Returns run record or error string.
    """
    if not runs:
        return "No subagents found"
    
    # Try exact label match
    for run in runs:
        if run.label and run.label == target:
            return run
    
    # Try run_id prefix
    for run in runs:
        if run.run_id.startswith(target):
            return run
    
    # Try numeric index (#1, #2, etc.)
    if target.startswith("#"):
        try:
            idx = int(target[1:]) - 1
            if 0 <= idx < len(runs):
                return runs[idx]
        except ValueError:
            pass
    
    # Try label prefix
    prefix_matches = [run for run in runs if run.label and run.label.startswith(target)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    elif len(prefix_matches) > 1:
        return f"Ambiguous target: {target}"
    
    return f"Unknown subagent: {target}"


# ---------------------------------------------------------------------------
# /allowlist
# ---------------------------------------------------------------------------

async def _handle_allowlist(args: str, ctx: Any, cfg: dict[str, Any]) -> ReplyPayload:
    parts = args.strip().split(None, 1)
    sub = parts[0].lower() if parts else "list"
    target = parts[1].strip() if len(parts) > 1 else ""

    if not sub or sub == "list":
        allow_from = cfg.get("allowFrom") or []
        if not allow_from:
            return ReplyPayload(text="Allowlist: empty (all senders allowed)")
        return ReplyPayload(text="Allowlist:\n" + "\n".join(f"  • {x}" for x in allow_from))

    if sub == "add" and target:
        allow_from = list(cfg.get("allowFrom") or [])
        if target not in allow_from:
            allow_from.append(target)
            cfg["allowFrom"] = allow_from
        return ReplyPayload(text=f"Added {target} to allowlist.")

    if sub in ("remove", "del", "rm") and target:
        allow_from = list(cfg.get("allowFrom") or [])
        if target in allow_from:
            allow_from.remove(target)
            cfg["allowFrom"] = allow_from
            return ReplyPayload(text=f"Removed {target} from allowlist.")
        return ReplyPayload(text=f"{target} not in allowlist.")

    return ReplyPayload(text="Usage: /allowlist [list|add <id>|remove <id>]")


# ---------------------------------------------------------------------------
# /approve <sender-id>
# ---------------------------------------------------------------------------

async def _handle_approve(args: str, ctx: Any, cfg: dict[str, Any]) -> ReplyPayload:
    target = args.strip()
    if not target:
        return ReplyPayload(text="Usage: /approve <sender-id>")
    return await _handle_allowlist(f"add {target}", ctx, cfg)


# ---------------------------------------------------------------------------
# /ptt [on|off|status]  — Push-to-talk
# ---------------------------------------------------------------------------

async def _handle_ptt(
    args: str,
    ctx: Any,
    cfg: dict[str, Any],
    session_key: str,
) -> ReplyPayload:
    sub = args.strip().lower()
    try:
        from openclaw.tts.tts import set_ptt_enabled, is_ptt_enabled, resolve_tts_config
        tts_cfg = resolve_tts_config(cfg)
        if not sub or sub == "status":
            enabled = is_ptt_enabled(tts_cfg)
            return ReplyPayload(text=f"Push-to-talk: {'enabled' if enabled else 'disabled'}")
        if sub == "on":
            set_ptt_enabled(tts_cfg, True)
            return ReplyPayload(text="Push-to-talk enabled.")
        if sub == "off":
            set_ptt_enabled(tts_cfg, False)
            return ReplyPayload(text="Push-to-talk disabled.")
    except Exception:
        pass
    return ReplyPayload(text="Usage: /ptt [on|off|status]")


# ---------------------------------------------------------------------------
# /tts [on|off|status|provider <name>|limit <n>|summary <on|off>|audio <text>]
# ---------------------------------------------------------------------------

async def _handle_tts(
    args: str,
    ctx: Any,
    cfg: dict[str, Any],
    session_key: str,
) -> ReplyPayload:
    sub_parts = args.strip().split(None, 1) if args.strip() else ["status"]
    sub = sub_parts[0].lower()
    sub_args = sub_parts[1].strip() if len(sub_parts) > 1 else ""

    try:
        from openclaw.tts.tts import (
            is_tts_enabled, set_tts_enabled, get_tts_provider, set_tts_provider,
            get_tts_max_length, set_tts_max_length,
            is_summarization_enabled, set_summarization_enabled,
            text_to_speech, resolve_tts_config, resolve_tts_prefs_path,
        )
        tts_cfg = resolve_tts_config(cfg)
        prefs_path = resolve_tts_prefs_path(tts_cfg)

        if sub == "on":
            set_tts_enabled(prefs_path, True)
            return ReplyPayload(text="TTS enabled.")
        if sub == "off":
            set_tts_enabled(prefs_path, False)
            return ReplyPayload(text="TTS disabled.")
        if sub == "status":
            enabled = is_tts_enabled(tts_cfg, prefs_path)
            provider = get_tts_provider(tts_cfg, prefs_path)
            max_len = get_tts_max_length(prefs_path)
            summarize = is_summarization_enabled(prefs_path)
            lines = [
                f"TTS status: {'enabled' if enabled else 'disabled'}",
                f"Provider: {provider}",
                f"Max length: {max_len}",
                f"Auto-summary: {'on' if summarize else 'off'}",
            ]
            return ReplyPayload(text="\n".join(lines))
        if sub == "provider":
            if not sub_args:
                p = get_tts_provider(tts_cfg, prefs_path)
                return ReplyPayload(text=f"TTS provider: {p}")
            set_tts_provider(prefs_path, sub_args.lower())
            return ReplyPayload(text=f"TTS provider set to {sub_args.lower()}.")
        if sub == "limit":
            if not sub_args:
                n = get_tts_max_length(prefs_path)
                return ReplyPayload(text=f"TTS limit: {n} chars")
            try:
                n = int(sub_args)
                set_tts_max_length(prefs_path, n)
                return ReplyPayload(text=f"TTS limit set to {n}.")
            except ValueError:
                return ReplyPayload(text="Usage: /tts limit <number>")
        if sub == "summary":
            if not sub_args:
                s = is_summarization_enabled(prefs_path)
                return ReplyPayload(text=f"TTS auto-summary: {'on' if s else 'off'}")
            set_summarization_enabled(prefs_path, sub_args.lower() == "on")
            return ReplyPayload(text=f"TTS summary {'enabled' if sub_args.lower() == 'on' else 'disabled'}.")
        if sub == "audio":
            if not sub_args:
                return ReplyPayload(text="Usage: /tts audio <text>")
            channel = str(
                getattr(ctx, "Surface", None) or getattr(ctx, "Provider", None) or "unknown"
            ).lower()
            result = await text_to_speech(text=sub_args, cfg=cfg, channel=channel, prefs_path=prefs_path)
            if result.get("success") and result.get("audio_path"):
                return ReplyPayload(
                    media_url=result["audio_path"],
                    audio_as_voice=result.get("voice_compatible", False),
                )
            return ReplyPayload(text=f"TTS error: {result.get('error', 'unknown')}")
    except ImportError:
        pass
    except Exception as exc:
        logger.warning(f"/tts error: {exc}")
        return ReplyPayload(text=f"TTS error: {exc}")

    return ReplyPayload(
        text="TTS usage: /tts [on|off|status|provider <name>|limit <n>|summary <on|off>|audio <text>]"
    )


# ---------------------------------------------------------------------------
# /plugin [list|info <name>]
# ---------------------------------------------------------------------------

async def _handle_plugin(args: str, ctx: Any, cfg: dict[str, Any]) -> ReplyPayload:
    sub = args.strip().lower() if args else "list"
    try:
        from openclaw.plugins.hook_runner import get_global_hook_runner
        runner = get_global_hook_runner()
        if not runner:
            return ReplyPayload(text="No plugins loaded.")
        plugins = getattr(runner, "plugins", []) or []
        if not plugins:
            return ReplyPayload(text="No plugins loaded.")
        lines = ["Plugins:"]
        for p in plugins:
            name = getattr(p, "name", None) or str(p)
            version = getattr(p, "version", None)
            lines.append(f"  • {name}" + (f" v{version}" if version else ""))
        return ReplyPayload(text="\n".join(lines))
    except Exception as exc:
        return ReplyPayload(text=f"Could not list plugins: {exc}")
