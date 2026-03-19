# Critical Fix: Session Key Injection for CronTool

## 问题根源

从日志分析发现，虽然之前完成了所有的代码修复（delivery.py, cron.py），但是 **CronTool 在每次 dispatch 时没有接收到当前的 session_key**，导致创建的 job 始终 `session_key=None`。

### 日志证据

```
2026-03-15 17:28:41,352 | WARNING  | openclaw.agents.tools.cron | [cron tool] no session_key available for injection, delivery may fail
2026-03-15 17:28:47,977 | INFO     | openclaw.gateway.cron_bootstrap | cron: resolving delivery with original_session_key=None, lookup_agent_id=default
2026-03-15 17:28:47,977 | INFO     | openclaw.gateway.cron_bootstrap | cron: resolved delivery target: {'channel': 'telegram', 'to': None}
```

即使用户从 Telegram 创建 job：
```
2026-03-15 17:28:24,463 | INFO     | openclaw.gateway.channel_manager | [telegram] Built session key: agent:main:telegram:direct:8366053063
```

CronTool 仍然警告 "no session_key available"。

## 根本原因

`channel_manager.py` 在 dispatch 时使用的是静态的 `self.tools` 列表，这个列表在 ChannelManager 初始化时创建，CronTool 的 `_agent_session_key` 始终是 `None`。

虽然 CronTool 已经有了 `set_agent_session_key()` 方法，但 **从未被调用**。

## 解决方案

在 `channel_manager.py` 的 `_create_message_handler` 方法中，每次 dispatch 之前动态注入 session_key 到 CronTool。

### 修改位置

**文件**: `openclaw/gateway/channel_manager.py`  
**行号**: 2095 (在 `_dispatch_tools = self.tools` 之后)

### 修改内容

```python
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
```

### 逻辑说明

1. 在每次用户消息 dispatch 之前
2. 找到 `self.tools` 中的 CronTool 实例
3. 调用 `set_agent_session_key(session_key)` 注入当前会话的 session_key
4. 调用 `set_chat_context(channel_id, chat_id)` 作为备用方案

这样，当用户在 Telegram 中创建 cron job 时：
- `session_key = "agent:main:telegram:direct:8366053063"`
- CronTool 会将此 session_key 注入到 job_config
- Job 执行时能从 session store 找到 `lastTo: "8366053063"`
- 消息成功发送到 Telegram

## 对比 TS 版本

**TS 版本** (`tool-preparation.ts`):
```typescript
const cronTool = createCronTool({ 
  agentSessionKey: session.key  // 每次创建时传入
});
```

**Python 版本（修复后）**:
```python
# 在 dispatch 时动态注入
_existing_cron_tool.set_agent_session_key(session_key)
```

差异原因：
- TS 每次 dispatch 创建新的 tool 实例
- Python 使用共享的 tool 实例，需要动态更新状态

## 完整修复清单

现在所有的修复都已完成：

1. ✅ **修复 1**: `delivery.py` 使用 `load_session_store_from_path` 从文件加载
2. ✅ **修复 2**: `cron.py` 添加 `agent_session_key` 参数和注入逻辑
3. ✅ **修复 3**: `channel_manager.py` **动态注入 session_key 到 CronTool**（本次修复）

## 测试验证

Gateway 已重启并运行在 `http://127.0.0.1:18789`。

**下一步**：用户需要通过 Telegram 创建新的 cron job 来验证修复。

**期望日志**：
```
[telegram] Injecting session_key=agent:main:telegram:direct:8366053063 into CronTool
[cron tool] injected session_key: agent:main:telegram:direct:8366053063
cron: resolving delivery with original_session_key=agent:main:telegram:direct:8366053063, lookup_agent_id=main
cron: resolved delivery target: {'channel': 'telegram', 'to': '8366053063'}
cron: delivered text to telegram chat_id=8366053063
```

**期望结果**：Telegram 收到 cron job 的消息，不再有 "Cron (error)" 提示。
