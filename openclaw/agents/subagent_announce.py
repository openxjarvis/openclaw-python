"""
Subagent announce flow

Matches TypeScript src/agents/subagent-announce.ts (simplified)

Handles sending subagent results back to parent session.
"""
from __future__ import annotations

import logging
from typing import Optional

from openclaw.agents.subagent_registry import (
    SUBAGENT_RUNS,
    SubagentRunRecord,
)

logger = logging.getLogger(__name__)


def build_subagent_system_prompt(
    task: str,
    child_depth: int | None = None,
    mode: str = "run",
    max_spawn_depth: int = 1,
    requester_session_key: str | None = None,
    requester_origin: dict | None = None,
    child_session_key: str | None = None,
    label: str | None = None,
    acp_enabled: bool = False,
    # Legacy parameter names for backward compatibility
    depth: int | None = None,
    max_depth: int | None = None,
) -> str:
    """
    Build system prompt for subagent (matches TS buildSubagentSystemPrompt).
    
    Mirrors TS buildSubagentSystemPrompt() from subagent-announce.ts:964-1068.
    Supports both new TS-style params and legacy Python params for compatibility.
    
    Args:
        task: Task description
        child_depth: Spawn depth (1 = subagent, 2 = sub-subagent)
        mode: Run mode (run or session)
        max_spawn_depth: Maximum spawn depth
        requester_session_key: Parent session key
        requester_origin: Parent delivery context (channel, accountId, etc)
        child_session_key: Child session key
        label: Optional task label
        acp_enabled: Whether ACP routing guidance should be included
        depth: Legacy depth parameter (for compatibility)
        max_depth: Legacy max depth parameter (for compatibility)
    
    Returns:
        System prompt in Markdown format
    """
    # Resolve depth params (prefer new names, fall back to legacy)
    actual_depth = child_depth if child_depth is not None else (depth if depth is not None else 1)
    actual_max_depth = max_spawn_depth if max_spawn_depth != 1 else (max_depth if max_depth is not None else 1)
    
    # Determine if this subagent can spawn children
    can_spawn = actual_depth < actual_max_depth
    
    # Resolve parent label
    parent_label = "parent agent"
    if requester_session_key:
        if "cron" in requester_session_key:
            parent_label = "cron job scheduler"
        elif "main" in requester_session_key:
            parent_label = "main agent"
        elif requester_origin and requester_origin.get("channel"):
            parent_label = f"parent session via {requester_origin['channel']}"
        else:
            parent_label = requester_session_key
    
    # Build comprehensive Markdown prompt (matches TS structure)
    lines = [
        "# Subagent Context",
        "",
        f"You are a **subagent** spawned by {parent_label} for a specific task.",
        "",
        "## Your Role",
        f"- You were created to handle: {task}",
        "- Complete this task. That's your entire purpose.",
        "- Your results will automatically be delivered to your requester.",
        "",
        "## Rules",
        "1. **Focus**: Stay on task. Don't deviate from your assigned work.",
        "2. **Completion**: When done, provide a clear final result.",
        "3. **NO_REPLY**: If you have nothing useful to say, output `NO_REPLY` (no additional text).",
        "4. **No polling**: Don't check your own status or ask if the requester received your result.",
        "5. **Concise**: Be direct and efficient. Avoid unnecessary explanation.",
        "6. **Tools**: Use available tools as needed to complete your task.",
        "",
        "## Output Format",
        "- Provide your final result as plain text or structured output.",
        "- If the task asks for a specific format (JSON, CSV, etc), follow it exactly.",
        "- Use `NO_REPLY` if you have no useful output (e.g., task cannot be completed).",
        "",
        "## What You DON'T Do",
        "- Don't ask the requester for confirmation or feedback mid-task.",
        "- Don't provide status updates unless explicitly asked.",
        "- Don't explain your process unless the task requires it.",
        "- Don't repeat the task description back.",
        "",
    ]
    
    # Add sub-agent spawning section if allowed
    if can_spawn:
        lines.extend([
            "## Sub-Agent Spawning",
            f"You may spawn sub-agents using the `sessions_spawn` tool if your task requires delegation.",
            f"- Current depth: {actual_depth}/{actual_max_depth}",
            f"- You can spawn sub-agents at depth {actual_depth + 1}.",
            "- Use this for parallel or delegated work, not for simple sequential steps.",
            "",
        ])
    else:
        # Leaf worker - cannot spawn
        lines.extend([
            "## Sub-Agent Spawning",
            f"You are a **leaf worker** at max depth ({actual_depth}/{actual_max_depth}).",
            f"- You CANNOT spawn sub-agents.",
            "- Complete this task directly using available tools.",
            "",
        ])
    
    # Add session mode info
    if mode == "session":
        lines.extend([
            "## Session Mode",
            "This is a **persistent subagent session**.",
            "- Your session remains active after completing tasks.",
            "- The requester can send follow-up messages to this session.",
            "- Use this mode when ongoing interaction is expected.",
            "",
        ])
    
    # Add ACP routing guidance
    if acp_enabled:
        lines.extend([
            "## ACP Routing",
            "You have access to the ACP (Agent Communication Protocol) harness.",
            "- Use ACP for structured communication with your requester.",
            "- ACP allows sending progress updates, asking questions, or delivering partial results.",
            "",
        ])
    
    # Add session context
    if child_session_key or requester_session_key:
        lines.extend([
            "## Session Context",
        ])
        if child_session_key:
            lines.append(f"- Your session key: `{child_session_key}`")
        if requester_session_key:
            lines.append(f"- Requester session: `{requester_session_key}`")
        if label:
            lines.append(f"- Task label: {label}")
        lines.append("")
    
    return "\n".join(lines)


def format_subagent_announce(
    label: str,
    status: str,
    result: Optional[str] = None,
    error: Optional[str] = None,
    mode: str = "run",
) -> str:
    """
    Format subagent completion announcement.
    
    Matches TS buildCompletionDeliveryMessage() concept (simplified).
    
    Args:
        label: Task label
        status: Run status (completed, error, timeout, killed)
        result: Optional result text
        error: Optional error message
        mode: Run mode
    
    Returns:
        Formatted announcement text
    """
    # Build header
    if status == "error":
        header = (
            f"❌ Subagent {label} failed this task (session remains active)"
            if mode == "session"
            else f"❌ Subagent {label} failed"
        )
    elif status == "timeout":
        header = (
            f"⏱️ Subagent {label} timed out on this task (session remains active)"
            if mode == "session"
            else f"⏱️ Subagent {label} timed out"
        )
    elif status == "killed":
        header = f"🛑 Subagent {label} was killed"
    else:
        header = (
            f"✅ Subagent {label} completed this task (session remains active)"
            if mode == "session"
            else f"✅ Subagent {label} finished"
        )
    
    # Build body
    parts = [header]
    
    if result and result.strip():
        parts.append("")
        parts.append(result.strip())
    
    if error and error.strip():
        parts.append("")
        parts.append(f"Error: {error.strip()}")
    
    return "\n".join(parts)


async def extract_subagent_result(child_session_key: str) -> str:
    """
    Extract result from subagent transcript.
    
    Reads the latest assistant message from chat history.
    Matches TS readLatestSubagentOutput() concept.
    
    Args:
        child_session_key: Child session key
    
    Returns:
        Extracted result text
    """
    try:
        # Import here to avoid circular dependency
        from openclaw.agents.tools.agent_step import read_latest_assistant_reply
        
        result = await read_latest_assistant_reply(child_session_key, limit=50)
        if result and result.strip():
            return result
        return "(no output)"
    except Exception as e:
        logger.warning(f"Failed to extract subagent result: {e}")
        return "(extraction failed)"


async def run_subagent_announce_flow(
    *,
    child_session_key: str,
    child_run_id: str,
    requester_session_key: str,
    requester_origin: dict,
    task: str,
    timeout_ms: int | None = None,
    cleanup: str = "keep",
    round_one_reply: str | None = None,
    wait_for_completion: bool = True,
    announce_type: str | None = None,
    started_at: int | None = None,
    ended_at: int | None = None,
    outcome: dict | None = None,
    expects_completion_message: bool | None = None,
    best_effort_deliver: bool = False,
    signal: any = None,
    requester_display_key: str | None = None,
) -> bool:
    """
    Run the announce flow for a subagent.
    
    Matches TS runSubagentAnnounceFlow() signature (openclaw/src/agents/subagent-announce.ts:823).
    
    Sends the subagent's result back to the parent session.
    
    Args:
        child_session_key: Child session key
        child_run_id: Child run ID
        requester_session_key: Parent/requester session key
        requester_origin: Delivery context (channel, to, accountId, threadId)
        task: Task description/label
        timeout_ms: Timeout in milliseconds
        cleanup: "keep" or "delete" session after announce
        round_one_reply: Initial reply text (for cron jobs, this is the result)
        wait_for_completion: Whether to wait for completion
        announce_type: Type of announcement ("cron job", etc.)
        started_at: Start timestamp
        ended_at: End timestamp
        outcome: Run outcome (status, error, etc.)
        expects_completion_message: Whether this expects a completion message
        best_effort_deliver: Whether to treat delivery failures as best-effort
        signal: Abort signal
        requester_display_key: Display key for requester
    
    Returns:
        True if announce succeeded, False otherwise
    """
    from typing import Any
    
    # For cron jobs, use direct delivery path (TS line 319: expectsCompletionMessage: true)
    # This sends the message directly to the target channel instead of injecting into main session
    if announce_type == "cron job":
        logger.info(
            f"[subagent-announce] Cron job announce: {task[:50]} → {requester_session_key}"
        )
        
        # Use the round_one_reply directly (this is the agent's result text)
        announce_text = round_one_reply
        if not announce_text or not announce_text.strip():
            logger.debug("[subagent-announce] Empty cron result, skipping delivery")
            return True
        
        # Extract delivery target from requester_origin
        channel = requester_origin.get("channel")
        to = requester_origin.get("to")
        account_id = requester_origin.get("accountId")
        thread_id = requester_origin.get("threadId")
        
        if not channel or not to:
            logger.warning(
                f"[subagent-announce] Missing delivery target: channel={channel}, to={to}"
            )
            return False if not best_effort_deliver else True
        
        # Direct delivery via deliver_outbound_payloads (matches TS)
        try:
            from openclaw.infra.outbound.deliver import deliver_outbound_payloads
            from openclaw.config.loader import load_config
            from openclaw.routing.session_key import parse_agent_session_key
            
            # Load config for channel plugins
            cfg = load_config()
            
            # Extract agent ID from session key
            parsed = parse_agent_session_key(requester_session_key)
            agent_id = parsed.get("agent_id") if parsed else None
            
            # Build payloads
            payloads = [{"text": announce_text}]
            
            logger.info(
                f"[subagent-announce] Direct delivery to {channel}:{to} (agent={agent_id})"
            )
            
            # Deliver directly to channel
            results = await deliver_outbound_payloads(
                cfg=cfg,
                channel=channel,
                to=to,
                account_id=account_id,
                thread_id=thread_id,
                payloads=payloads,
                agent_id=agent_id,
            )
            
            # Check if any delivery succeeded
            delivered = any(r.get("ok") for r in results)
            
            if delivered:
                logger.info(
                    f"[subagent-announce] Successfully delivered cron result to {channel}:{to}"
                )
                return True
            else:
                error_msgs = [r.get("error") for r in results if r.get("error")]
                logger.warning(
                    f"[subagent-announce] Failed to deliver to {channel}:{to}: {error_msgs}"
                )
                return False if not best_effort_deliver else True
        
        except Exception as e:
            logger.error(
                f"[subagent-announce] Cron delivery error to {channel}:{to}: {e}",
                exc_info=True
            )
            return False if not best_effort_deliver else True
    
    # For non-cron subagents, check registry for existing run record
    record = SUBAGENT_RUNS.get(child_run_id)
    if record:
        # Don't announce if already sent
        if record.announce_sent:
            return True
        
        # Extract result from child session
        result = record.result or await extract_subagent_result(child_session_key)
        
        # Format announce message
        announce_text = format_subagent_announce(
            label=record.label or task[:50],
            status=record.status,
            result=result,
            error=record.error,
            mode=record.mode,
        )
        
        if not announce_text.strip():
            # Empty announce, mark as sent
            record.announce_sent = True
            record.announce_pending = False
            return True
        
        # Send to parent session via gateway (matches TS)
        try:
            from openclaw.gateway.rpc_client import GatewayRPCClient
            
            client = GatewayRPCClient()
            
            # Call agent method to inject message into parent session
            # Matches TS: callGateway({ method: "agent", params: {...} })
            await client.call(
                method="agent",
                params={
                    "message": announce_text,
                    "sessionKey": requester_session_key,
                    "channel": "internal",  # Internal message channel
                    "lane": "nested",       # Nested lane for subagent messages
                    "deliver": False,       # Don't deliver to external channels
                    # Add internal events if needed
                    "internalEvents": [{
                        "type": "subagent_completed",
                        "runId": child_run_id,
                        "childSessionKey": child_session_key,
                        "taskLabel": record.label or task,
                        "status": record.status,
                    }] if record.status == "completed" else None,
                },
            )
            
            logger.info(
                f"[subagent-announce] Successfully announced run={child_run_id} "
                f"→ {requester_session_key}"
            )
            
            record.announce_sent = True
            record.announce_pending = False
            return True
        
        except Exception as e:
            logger.error(f"Failed to announce subagent result for run={child_run_id}: {e}")
            # Don't mark as sent on failure, allow retry
            return False
    
    # No registry record - this is likely a cron job or direct call
    # Fall back to simple gateway injection
    if round_one_reply and round_one_reply.strip():
        try:
            from openclaw.gateway.rpc_client import GatewayRPCClient
            
            client = GatewayRPCClient()
            
            await client.call(
                method="agent",
                params={
                    "message": round_one_reply,
                    "sessionKey": requester_session_key,
                    "channel": "internal",
                    "lane": "nested",
                    "deliver": False,
                },
            )
            
            logger.info(
                f"[subagent-announce] Announced direct message → {requester_session_key}"
            )
            return True
        
        except Exception as e:
            logger.error(f"Failed to announce direct message: {e}")
            return False
    
    logger.warning(
        f"[subagent-announce] No announce path for run={child_run_id}, announce_type={announce_type}"
    )
    return False


__all__ = [
    "build_subagent_system_prompt",
    "format_subagent_announce",
    "extract_subagent_result",
    "run_subagent_announce_flow",
]
