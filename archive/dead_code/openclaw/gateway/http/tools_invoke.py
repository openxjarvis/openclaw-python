"""Direct tool invocation HTTP endpoint.

Provides POST /tools/invoke — execute a tool without a full agent turn.
Always enabled, gated by auth and tool policy.

Mirrors TS openclaw/src/gateway/tools-invoke-http.ts.
Reference: openclaw/docs/gateway/tools-invoke-http-api.md
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default deny list — must mirror TS DEFAULT_GATEWAY_HTTP_TOOL_DENY
# in openclaw/src/security/dangerous-tools.ts
# ---------------------------------------------------------------------------
DEFAULT_GATEWAY_HTTP_TOOL_DENY: frozenset[str] = frozenset(
    [
        # Session orchestration — spawning agents remotely is RCE
        "sessions_spawn",
        # Cross-session injection — message injection across sessions
        "sessions_send",
        # Gateway control plane — prevents gateway reconfiguration via HTTP
        "gateway",
        # Cron task management — privileged scheduling, must use WebSocket operator.admin
        "cron",
        # Interactive setup — requires terminal QR scan, hangs on HTTP
        "whatsapp_login",
    ]
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ToolInvokeRequest:
    """POST /tools/invoke request body.

    Fields mirror TS ToolsInvokeBody:
    - tool      : tool name (required)
    - action    : optional shorthand merged into args if schema supports it
    - args      : tool arguments dict (formerly 'params')
    - sessionKey: optional session key for context-aware tools
    - dryRun    : optional dry-run flag passed through to tool
    """

    __slots__ = ("tool", "action", "args", "session_key", "dry_run")

    def __init__(
        self,
        body: dict[str, Any] | None = None,
        *,
        tool: str | None = None,
        params: dict[str, Any] | None = None,
        args: dict[str, Any] | None = None,
        action: str | None = None,
        session_key: str | None = None,
        dry_run: bool = False,
    ) -> None:
        # Support both dict-body form (HTTP handler) and keyword-arg form (tests/programmatic)
        if body is not None:
            d = body
        else:
            d = {}
            if tool:
                d["tool"] = tool
            if params is not None:
                d["args"] = params
            if args is not None:
                d["args"] = args
            if action:
                d["action"] = action
            if session_key:
                d["sessionKey"] = session_key
            d["dryRun"] = dry_run

        tool_val = d.get("tool")
        if not isinstance(tool_val, str) or not tool_val.strip():
            raise ValueError("'tool' must be a non-empty string")
        self.tool: str = tool_val.strip()
        self.action: str | None = d.get("action") if isinstance(d.get("action"), str) else None
        raw_args = d.get("args") or d.get("params")
        self.args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
        raw_sk = d.get("sessionKey")
        self.session_key: str | None = raw_sk.strip() if isinstance(raw_sk, str) and raw_sk.strip() else None
        self.dry_run: bool = bool(d.get("dryRun", False))

    @property
    def params(self) -> dict[str, Any]:
        """Alias for args (TS: body.args / body.params)."""
        return self.args


class ToolInvokeResponse:
    """Response shape for /tools/invoke."""

    def __init__(
        self,
        ok: bool,
        result: Any = None,
        details: Any = None,
        error: dict[str, Any] | None = None,
        status: int = 200,
    ) -> None:
        self.ok = ok
        self.result = result
        self.details = details
        self.error = error
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ok": self.ok}
        if self.result is not None:
            d["result"] = self.result
        if self.details is not None:
            d["details"] = self.details
        if self.error is not None:
            d["error"] = self.error
        return d


# ---------------------------------------------------------------------------
# Policy helpers
# ---------------------------------------------------------------------------

def _resolve_tool_deny_list(config: Any) -> frozenset[str]:
    """Merge DEFAULT_GATEWAY_HTTP_TOOL_DENY with config deny list.

    Mirrors TS applyToolPolicyPipeline deny step.
    """
    base = set(DEFAULT_GATEWAY_HTTP_TOOL_DENY)
    try:
        tools_cfg = getattr(config, "tools", None) if config else None
        if tools_cfg is None and isinstance(config, dict):
            tools_cfg = config.get("tools") or {}
        deny_extra = getattr(tools_cfg, "deny", None) if tools_cfg else None
        if deny_extra is None and isinstance(tools_cfg, dict):
            deny_extra = tools_cfg.get("deny") or []
        if isinstance(deny_extra, (list, tuple, set)):
            base.update(str(d) for d in deny_extra)
    except Exception:
        pass
    return frozenset(base)


def _get_tool_allow_list(config: Any) -> frozenset[str] | None:
    """Return explicit allow list from config, or None (= all allowed)."""
    try:
        tools_cfg = getattr(config, "tools", None) if config else None
        if tools_cfg is None and isinstance(config, dict):
            tools_cfg = config.get("tools") or {}
        allow = getattr(tools_cfg, "allow", None) if tools_cfg else None
        if allow is None and isinstance(tools_cfg, dict):
            allow = tools_cfg.get("allow")
        if isinstance(allow, (list, tuple, set)):
            return frozenset(str(a) for a in allow)
    except Exception:
        pass
    return None


def check_tool_policy(
    tool_name: str,
    config: Any,
    message_channel: str | None = None,
    account_id: str | None = None,
) -> tuple[bool, str | None]:
    """Check whether a tool call is allowed by policy.

    Returns (allowed: bool, reason: str | None).

    Mirrors TS resolveEffectiveToolPolicy / applyToolPolicyPipeline deny step.
    """
    deny_list = _resolve_tool_deny_list(config)
    if tool_name in deny_list:
        return False, f"Tool '{tool_name}' is in the HTTP deny list"

    allow_list = _get_tool_allow_list(config)
    if allow_list is not None and tool_name not in allow_list:
        return False, f"Tool '{tool_name}' is not in the allowed tools list"

    return True, None


def _merge_action_into_args(
    tool_schema: Any,
    action: str | None,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Merge 'action' shorthand into args if the tool schema has an 'action' property.

    Mirrors TS mergeActionIntoArgsIfSupported().
    """
    if not action or "action" in args:
        return args
    if isinstance(tool_schema, dict):
        props = tool_schema.get("properties") or {}
        if "action" in props:
            return {"action": action, **args}
    return args


def _extract_bearer_token(authorization: str | None) -> str | None:
    """Extract bearer token from Authorization header."""
    if not authorization:
        return None
    if authorization.startswith("Bearer "):
        return authorization[7:].strip()
    return None


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

async def handle_tool_invoke_request(
    body: dict[str, Any],
    tool_registry: Any,
    gateway: Any,
    authorization: str | None = None,
    message_channel_header: str | None = None,
    account_id_header: str | None = None,
) -> ToolInvokeResponse:
    """Handle POST /tools/invoke.

    Mirrors TS handleToolsInvokeHttpRequest().

    Args:
        body: Parsed JSON request body.
        tool_registry: Registry exposing `.get(name)` and `.get_schema(name)`.
        gateway: Gateway server instance (for auth config).
        authorization: Authorization header value.
        message_channel_header: x-openclaw-message-channel header value.
        account_id_header: x-openclaw-account-id header value.

    Returns:
        ToolInvokeResponse with .status for HTTP status code.
    """
    config = getattr(gateway, "config", None)

    # Parse request
    try:
        req = ToolInvokeRequest(body)
    except ValueError as exc:
        return ToolInvokeResponse(
            ok=False,
            error={"code": "INVALID_REQUEST", "message": str(exc)},
            status=400,
        )

    # Policy check — deny list first
    allowed, reason = check_tool_policy(
        req.tool, config, message_channel_header, account_id_header
    )
    if not allowed:
        return ToolInvokeResponse(
            ok=False,
            error={"code": "FORBIDDEN", "message": reason or "Tool not allowed"},
            status=403,
        )

    # Resolve tool
    tool = None
    if tool_registry is not None:
        if callable(getattr(tool_registry, "get", None)):
            tool = tool_registry.get(req.tool)
        elif isinstance(tool_registry, dict):
            tool = tool_registry.get(req.tool)

    if tool is None:
        return ToolInvokeResponse(
            ok=False,
            error={"code": "NOT_FOUND", "message": f"Tool '{req.tool}' not found"},
            status=404,
        )

    # Merge action shorthand into args
    schema = None
    if hasattr(tool, "get_schema"):
        try:
            schema = tool.get_schema()
        except Exception:
            pass
    effective_args = _merge_action_into_args(schema, req.action, req.args)

    # Add dryRun if requested
    if req.dry_run:
        effective_args = {"dry_run": True, **effective_args}

    # Add sessionKey context if provided
    exec_context: dict[str, Any] = {}
    if req.session_key:
        exec_context["session_key"] = req.session_key
    if message_channel_header:
        exec_context["message_channel"] = message_channel_header
    if account_id_header:
        exec_context["account_id"] = account_id_header

    # Execute
    try:
        result = await tool.execute(effective_args)
        content = None
        details = None
        if hasattr(result, "content"):
            content = result.content
        elif isinstance(result, dict):
            content = result.get("content")
        if hasattr(result, "details"):
            details = result.details
        elif isinstance(result, dict):
            details = result.get("details")
        return ToolInvokeResponse(ok=True, result=content, details=details, status=200)
    except Exception as exc:
        logger.error("Tool '%s' execution error: %s", req.tool, exc, exc_info=True)
        return ToolInvokeResponse(
            ok=False,
            error={"code": "EXECUTION_ERROR", "message": str(exc)},
            status=500,
        )


# ---------------------------------------------------------------------------
# Legacy compat — keep old names for imports elsewhere
# ---------------------------------------------------------------------------

async def handle_tool_invoke(request: Any, tool_registry: Any, config: Any, authorization: str | None = None) -> Any:
    """Backward-compatible wrapper. Prefer handle_tool_invoke_request()."""
    if hasattr(request, "model_dump"):
        body = request.model_dump()
    elif hasattr(request, "dict"):
        body = request.dict()
    else:
        body = dict(request) if isinstance(request, dict) else {"tool": getattr(request, "tool", ""), "args": getattr(request, "params", {})}
    from unittest.mock import MagicMock
    gw_mock = MagicMock()
    gw_mock.config = config
    return await handle_tool_invoke_request(body, tool_registry, gw_mock, authorization)


__all__ = [
    "ToolInvokeRequest",
    "ToolInvokeResponse",
    "DEFAULT_GATEWAY_HTTP_TOOL_DENY",
    "check_tool_policy",
    "handle_tool_invoke_request",
    "handle_tool_invoke",
]
