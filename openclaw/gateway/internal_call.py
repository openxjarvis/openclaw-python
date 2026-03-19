"""Internal Gateway RPC calls (loopback within server)

Allows Gateway server components to call Gateway methods internally
without creating external WebSocket connections.

Mirrors TypeScript callGateway() pattern used in openclaw/src/agents/subagent-spawn.ts
"""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class GatewayInternalCallError(Exception):
    """Raised when internal gateway call fails"""
    pass


async def call_gateway_internal(
    gateway: Any,
    method: str,
    params: dict[str, Any],
    *,
    timeout_ms: int = 10_000,
) -> dict[str, Any]:
    """
    Call a Gateway method internally via loopback RPC.
    
    Mirrors TypeScript callGateway() usage in subagent-spawn.ts lines 702-724.
    Creates a WebSocket connection to the local Gateway server to ensure
    proper event delivery, queue management, and lifecycle handling.
    
    Args:
        gateway: Gateway server instance
        method: RPC method name (e.g., "agent", "sessions.patch", "sessions.delete")
        params: Method parameters
        timeout_ms: Timeout in milliseconds
    
    Returns:
        Method result dict
    
    Raises:
        GatewayInternalCallError: If call fails
    """
    if gateway is None:
        raise GatewayInternalCallError("Gateway instance required for internal call")
    
    # Get gateway configuration
    config = getattr(gateway, "config", None)
    if config is None:
        raise GatewayInternalCallError("Gateway config not available")
    
    # Resolve port from config (mirrors TS resolveGatewayPort)
    port = 18789  # Default
    try:
        if hasattr(config, "gateway") and config.gateway:
            gw_cfg = config.gateway
            if isinstance(gw_cfg, dict):
                port = gw_cfg.get("port", port)
            elif hasattr(gw_cfg, "port") and gw_cfg.port:
                port = gw_cfg.port
    except Exception as e:
        logger.debug(f"Failed to resolve gateway port, using default: {e}")
    
    url = f"ws://localhost:{port}"
    
    # Get auth token (if configured)
    auth_token = None
    try:
        if hasattr(config, "gateway") and config.gateway:
            gw_cfg = config.gateway
            if isinstance(gw_cfg, dict):
                auth_cfg = gw_cfg.get("auth") or {}
                auth_token = auth_cfg.get("token") if isinstance(auth_cfg, dict) else None
            elif hasattr(gw_cfg, "auth") and gw_cfg.auth:
                auth_token = getattr(gw_cfg.auth, "token", None)
    except Exception as e:
        logger.debug(f"Failed to resolve auth token: {e}")
    
    # Import RPC client
    try:
        from openclaw.gateway.rpc_client import GatewayRPCClient, GatewayRPCError
    except ImportError as e:
        raise GatewayInternalCallError(f"Failed to import GatewayRPCClient: {e}") from e
    
    # Create RPC client
    client = GatewayRPCClient(url=url, auth_token=auth_token)
    
    # Call method with timeout
    logger.debug(f"Internal RPC call: method={method}, timeout={timeout_ms}ms")
    
    try:
        result = await asyncio.wait_for(
            client.call(method, params),
            timeout=timeout_ms / 1000.0
        )
        
        logger.debug(f"Internal RPC call succeeded: method={method}")
        return result
        
    except asyncio.TimeoutError as e:
        error_msg = f"Internal gateway call timed out after {timeout_ms}ms: method={method}"
        logger.error(error_msg)
        raise GatewayInternalCallError(error_msg) from e
        
    except Exception as e:
        # Import GatewayRPCError to check exception type
        try:
            from openclaw.gateway.rpc_client import GatewayRPCError
            if isinstance(e, GatewayRPCError):
                error_msg = f"Internal RPC call failed: {e}"
            else:
                error_msg = f"Unexpected error in internal RPC call: {e}"
        except ImportError:
            error_msg = f"Internal RPC call failed: {e}"
        
        logger.error(f"{error_msg} (method={method})", exc_info=True)
        raise GatewayInternalCallError(error_msg) from e


# Convenience wrappers for common operations

async def call_agent_internal(
    gateway: Any,
    message: str,
    session_key: str,
    *,
    session_id: str | None = None,
    idempotency_key: str | None = None,
    lane: str | None = None,
    extra_system_prompt: str | None = None,
    thinking: str | None = None,
    timeout: int | None = None,
    label: str | None = None,
    spawned_by: str | None = None,
    group_id: str | None = None,
    group_channel: str | None = None,
    group_space: str | None = None,
    channel: str | None = None,
    to: str | None = None,
    account_id: str | None = None,
    thread_id: str | None = None,
    deliver: bool = False,
    timeout_ms: int = 10_000,
) -> dict[str, Any]:
    """
    Convenience wrapper for calling agent method internally.
    
    Mirrors TypeScript callGateway({method: "agent", ...}) pattern.
    """
    params = {
        "message": message,
        "sessionKey": session_key,
        "deliver": deliver,
    }
    
    if session_id:
        params["sessionId"] = session_id
    if idempotency_key:
        params["idempotencyKey"] = idempotency_key
    if lane:
        params["lane"] = lane
    if extra_system_prompt:
        params["extraSystemPrompt"] = extra_system_prompt
    if thinking:
        params["thinking"] = thinking
    if timeout is not None:
        params["timeout"] = timeout
    if label:
        params["label"] = label
    if spawned_by:
        params["spawnedBy"] = spawned_by
    if group_id:
        params["groupId"] = group_id
    if group_channel:
        params["groupChannel"] = group_channel
    if group_space:
        params["groupSpace"] = group_space
    if channel:
        params["channel"] = channel
    if to:
        params["to"] = to
    if account_id:
        params["accountId"] = account_id
    if thread_id:
        params["threadId"] = thread_id
    
    return await call_gateway_internal(
        gateway=gateway,
        method="agent",
        params=params,
        timeout_ms=timeout_ms,
    )


async def patch_session_internal(
    gateway: Any,
    key: str,
    patch: dict[str, Any],
    *,
    timeout_ms: int = 10_000,
) -> dict[str, Any]:
    """
    Convenience wrapper for patching session via internal RPC.
    
    Mirrors TypeScript callGateway({method: "sessions.patch", ...}) pattern.
    """
    return await call_gateway_internal(
        gateway=gateway,
        method="sessions.patch",
        params={"key": key, "patch": patch},
        timeout_ms=timeout_ms,
    )


async def delete_session_internal(
    gateway: Any,
    key: str,
    *,
    delete_transcript: bool = True,
    emit_lifecycle_hooks: bool = True,
    timeout_ms: int = 10_000,
) -> dict[str, Any]:
    """
    Convenience wrapper for deleting session via internal RPC.
    
    Mirrors TypeScript callGateway({method: "sessions.delete", ...}) pattern.
    """
    return await call_gateway_internal(
        gateway=gateway,
        method="sessions.delete",
        params={
            "key": key,
            "deleteTranscript": delete_transcript,
            "emitLifecycleHooks": emit_lifecycle_hooks,
        },
        timeout_ms=timeout_ms,
    )
