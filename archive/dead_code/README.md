# Dead Code Archive

Files moved here were confirmed unused/unreachable in the `openclaw/` package tree.
Sub-paths mirror the original layout so any file can be restored by moving it back.

| File | Reason archived |
|------|----------------|
| `openclaw/channels/telegram_ext/` (5 files) | Zero imports anywhere; functionality duplicated in `channels/telegram/` |
| `openclaw/api/server.py`, `websocket.py`, `openai_compat.py` | Separate FastAPI layer never wired into the aiohttp gateway |
| `openclaw/gateway/chat_run_state.py` | Never imported; bootstrap uses `chat_state.ChatRunRegistry` |
| `openclaw/gateway/http/tools_invoke.py` | Implemented but no route registered in `server.py` |
| `openclaw/gateway/discovery.py` | `GatewayDiscovery` defined but bootstrap step is a no-op comment |
| `openclaw/discovery/mdns.py` | No imports; only referenced by a config field name |
| `openclaw/auto_reply/dispatch.py` | Duplicate `route_reply`; real implementation is `auto_reply/reply/route_reply.py` |
| `openclaw/auth/pairing.py` | No callers; pairing logic lives in the `pairing/` package |
| `openclaw/agents/tool_policy_pipeline.py` | Functions only reference themselves; never called externally |
