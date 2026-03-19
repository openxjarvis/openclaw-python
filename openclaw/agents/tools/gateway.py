"""
Gateway tool for managing the OpenClaw gateway process.

Aligned with TypeScript openclaw/src/agents/tools/gateway-tool.ts
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .base import AgentTool, ToolResult
from ...config.paths import resolve_state_dir

logger = logging.getLogger(__name__)

# Module-level alias for testability (tests can patch openclaw.agents.tools.gateway.create_client)
try:
    from openclaw.gateway.rpc_client import create_client
except ImportError:
    create_client = None  # type: ignore[assignment]


GATEWAY_ACTIONS = [
    "restart",
    "config.get",
    "config.schema",
    "config.apply",
    "config.patch",
    "update.run",
]


class GatewayTool(AgentTool):
    """
    Restart, apply config, or update the gateway in-place.
    
    Matches TypeScript createGatewayTool() from gateway-tool.ts lines 64-229
    """
    
    def __init__(
        self,
        agent_session_key: str | None = None,
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize gateway tool.
        
        Args:
            agent_session_key: Current session key
            config: OpenClaw configuration
        """
        super().__init__()
        self.name = "gateway"
        self.description = (
            "Restart, apply config, or update the gateway in-place (SIGUSR1). "
            "Use config.patch for safe partial config updates (merges with existing). "
            "Use config.apply only when replacing entire config. "
            "Both trigger restart after writing. "
            "Always pass a human-readable completion message via the `note` parameter "
            "so the system can deliver it to the user after restart."
        )
        self.agent_session_key = agent_session_key
        self.config = config or {}
    
    def get_schema(self) -> dict[str, Any]:
        """
        Get tool schema matching TS GatewayToolSchema (gateway-tool.ts lines 42-58)
        """
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": GATEWAY_ACTIONS,
                    "description": "Gateway action to perform",
                },
                "delayMs": {
                    "type": "number",
                    "description": "Restart delay in milliseconds (for restart action)",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for restart (for restart action)",
                },
                "gatewayUrl": {
                    "type": "string",
                    "description": "Gateway WebSocket URL (optional)",
                },
                "gatewayToken": {
                    "type": "string",
                    "description": "Gateway auth token (optional)",
                },
                "timeoutMs": {
                    "type": "number",
                    "description": "Request timeout in milliseconds",
                },
                "raw": {
                    "type": "string",
                    "description": "Raw config JSON (for config.apply/config.patch)",
                },
                "baseHash": {
                    "type": "string",
                    "description": "Base config hash for conflict detection",
                },
                "sessionKey": {
                    "type": "string",
                    "description": "Session key for delivery routing",
                },
                "note": {
                    "type": "string",
                    "description": "Human-readable completion message",
                },
                "restartDelayMs": {
                    "type": "number",
                    "description": "Delay before restart after config write",
                },
            },
            "required": ["action"],
        }
    
    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """
        Execute gateway action.
        
        Matches TS gateway-tool.ts execute logic (lines 74-229)
        """
        action = params.get("action", "")
        
        if not action:
            return ToolResult(success=False, content="", error="action required")
        
        try:
            if action == "restart":
                return await self._handle_restart(params)
            
            elif action == "config.get":
                return await self._handle_config_get(params)
            
            elif action == "config.schema":
                return await self._handle_config_schema(params)
            
            elif action == "config.apply":
                return await self._handle_config_apply(params)
            
            elif action == "config.patch":
                return await self._handle_config_patch(params)
            
            elif action == "update.run":
                return await self._handle_update_run(params)
            
            else:
                return ToolResult(
                    success=False,
                    content="",
                    error=f"Unknown action: {action}",
                )
        
        except Exception as e:
            logger.error(f"Gateway tool error: {e}", exc_info=True)
            return ToolResult(
                success=False,
                content="",
                error=str(e),
            )
    
    async def _handle_restart(self, params: dict[str, Any]) -> ToolResult:
        """Handle gateway restart action (TS lines 77-125)"""
        # Check if restart is enabled
        commands_config = self.config.get("commands", {})
        if not commands_config.get("restart", False):
            return ToolResult(
                success=False,
                content="",
                error="Gateway restart is disabled. Set commands.restart=true to enable.",
            )
        
        session_key = params.get("sessionKey", "").strip() or self.agent_session_key or ""
        delay_ms = params.get("delayMs", 2000)
        reason = params.get("reason", "").strip()[:200] if params.get("reason") else None
        note = params.get("note", "").strip() if params.get("note") else None
        
        # Write restart sentinel for recovery after restart
        try:
            from pathlib import Path
            import json as json_lib
            
            sentinel_dir = resolve_state_dir() / "gateway"
            sentinel_dir.mkdir(parents=True, exist_ok=True)
            sentinel_path = sentinel_dir / "restart-sentinel.json"
            
            sentinel_data = {
                "timestamp": int(time.time() * 1000),
                "sessionKey": session_key,
                "reason": reason,
                "note": note,
            }
            
            with open(sentinel_path, "w") as f:
                json_lib.dump(sentinel_data, f, indent=2)
            
            logger.info(f"Wrote restart sentinel to {sentinel_path}")
        except Exception as e:
            logger.warning(f"Failed to write restart sentinel: {e}")
        
        # Schedule restart via gateway RPC
        try:
            client = await create_client()
            await client.call("gateway.restart", {
                "delayMs": delay_ms,
                "reason": reason,
            })
        except Exception as e:
            logger.warning(f"Failed to schedule restart via RPC: {e}")
        
        logger.info(
            f"Gateway restart requested: delayMs={delay_ms}, reason={reason}, note={note}"
        )
        
        scheduled = {
            "ok": True,
            "action": "restart",
            "delayMs": delay_ms,
            "reason": reason,
            "note": note,
        }
        
        return ToolResult(
            success=True,
            content=json.dumps(scheduled, indent=2),
            metadata=scheduled,
        )
    
    async def _handle_config_get(self, params: dict[str, Any]) -> ToolResult:
        """Handle config.get action"""
        # Call gateway RPC
        result = await self._call_gateway_tool("config.get", params, {})
        
        return ToolResult(
            success=True,
            content=json.dumps(result, indent=2),
            metadata=result,
        )
    
    async def _handle_config_schema(self, params: dict[str, Any]) -> ToolResult:
        """Handle config.schema action"""
        # Call gateway RPC
        result = await self._call_gateway_tool("config.schema", params, {})
        
        return ToolResult(
            success=True,
            content=json.dumps(result, indent=2),
            metadata=result,
        )
    
    async def _handle_config_apply(self, params: dict[str, Any]) -> ToolResult:
        """Handle config.apply action"""
        raw = params.get("raw", "").strip()
        
        if not raw:
            return ToolResult(success=False, content="", error="raw config JSON required")
        
        # Validate JSON
        try:
            config_obj = json.loads(raw)
        except json.JSONDecodeError as e:
            return ToolResult(
                success=False,
                content="",
                error=f"Invalid JSON: {e}",
            )
        
        # Prepare gateway call params
        gateway_params = {
            "config": config_obj,
            "baseHash": params.get("baseHash"),
        }
        
        # Call gateway RPC
        result = await self._call_gateway_tool("config.apply", params, gateway_params)
        # Gateway restart/wake is handled server-side in response to config.apply
        return ToolResult(
            success=True,
            content=json.dumps(result, indent=2),
            metadata=result,
        )
    
    async def _handle_config_patch(self, params: dict[str, Any]) -> ToolResult:
        """Handle config.patch action"""
        raw = params.get("raw", "").strip()
        
        if not raw:
            return ToolResult(success=False, content="", error="raw config patch JSON required")
        
        # Validate JSON
        try:
            patch_obj = json.loads(raw)
        except json.JSONDecodeError as e:
            return ToolResult(
                success=False,
                content="",
                error=f"Invalid JSON: {e}",
            )
        
        # Prepare gateway call params
        gateway_params = {
            "patch": patch_obj,
            "baseHash": params.get("baseHash"),
        }
        
        # Call gateway RPC
        result = await self._call_gateway_tool("config.patch", params, gateway_params)
        # Gateway restart/wake is handled server-side in response to config.patch
        return ToolResult(
            success=True,
            content=json.dumps(result, indent=2),
            metadata=result,
        )
    
    async def _handle_update_run(self, params: dict[str, Any]) -> ToolResult:
        """Handle update.run action"""
        # Call gateway RPC
        result = await self._call_gateway_tool("update.run", params, {})
        # Gateway restart/wake is handled server-side after update
        return ToolResult(
            success=True,
            content=json.dumps(result, indent=2),
            metadata=result,
        )
    
    async def _call_gateway_tool(
        self,
        method: str,
        tool_params: dict[str, Any],
        rpc_params: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Call gateway RPC method.
        
        Args:
            method: RPC method name
            tool_params: Original tool parameters (for gateway options)
            rpc_params: RPC-specific parameters
            
        Returns:
            RPC result
        """
        gateway_url = tool_params.get("gatewayUrl")
        gateway_token = tool_params.get("gatewayToken")
        timeout_ms = tool_params.get("timeoutMs", 20000)
        
        logger.info(
            f"Gateway RPC: method={method}, timeout={timeout_ms}ms"
        )
        
        try:
            # Create RPC client (uses config if gateway_url not provided)
            if gateway_url:
                from openclaw.gateway.rpc_client import GatewayRPCClient
                client = GatewayRPCClient(url=gateway_url, auth_token=gateway_token)
            else:
                client = await create_client()
            
            # Call the gateway method
            result = await client.call(method, rpc_params)
            
            # Wrap result in standard format
            return {"ok": True, "result": result}
        
        except Exception as e:
            logger.error(f"Gateway RPC call failed: {e}", exc_info=True)
            return {
                "ok": False,
                "error": str(e),
            }


def create_gateway_tool(
    agent_session_key: str | None = None,
    config: dict[str, Any] | None = None,
) -> GatewayTool:
    """
    Create gateway tool instance.
    
    Matches TS createGatewayTool() from gateway-tool.ts line 64
    
    Args:
        agent_session_key: Current session key
        config: OpenClaw configuration
        
    Returns:
        GatewayTool instance
    """
    return GatewayTool(agent_session_key=agent_session_key, config=config)
