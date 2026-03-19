# _extract_delivery_targets() 分析

## 函数签名
```python
def _extract_delivery_targets(
    all_session_keys: list[str],
    agent_part: str | None,
    running_channel_ids: list[str],
) -> list[tuple[str, str, int | None]]
```

位置: `openclaw/gateway/cron_bootstrap.py:744-863`

## 功能说明

从 session store (sessions.json) 中提取匹配的 delivery targets (channel_id, chat_id, thread_id)。

## 实现逻辑

### 1. 硬编码的 session store 路径
```python
sessions_file = Path.home() / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"
```

**问题**: 只查询 `agents/main` 目录，不支持其他 agent_id。

### 2. Agent 匹配逻辑
```python
target_agent = agent_part
if target_agent == "default":
    target_agent = "main"  # "default" 映射到 "main"

# 从 session_key 提取 agent
if session_key.startswith("agent:"):
    session_agent = parts[1]  # "agent:main:telegram:..." -> "main"
```

### 3. 过滤条件（必须全部满足）
```python
# 1. Agent 匹配
if session_agent != target_agent:
    continue

# 2. Channel 验证
if not last_channel or last_channel not in _KNOWN_CHANNELS:
    continue
if last_channel not in running_channel_ids:
    continue

# 3. Recipient 验证
if not last_to or not isinstance(last_to, str):
    continue

# 4. 跳过测试 chat ID (Telegram)
if last_channel == "telegram":
    if 0 < abs(chat_id_num) < 1000:
        continue  # 测试 ID
```

## 限制和问题

### 限制 1: 无 Fallback
**问题**: 如果找不到任何 targets，直接返回空列表 `[]`

**对比 TS**: TS 版本有完整的 fallback 链：
- Session store 查询
- Session history
- Config-driven channel selection
- DEFAULT_CHAT_CHANNEL

### 限制 2: 依赖 Session Store
**问题**: 必须有 `lastChannel` 和 `lastTo` 字段

**场景问题**:
- 新创建的 cron job（没有任何历史 session）
- Job 的 `session_key` 不在 session store 中
- Session store 未初始化

### 限制 3: 硬编码路径
**问题**: 只查询 `agents/main/sessions/sessions.json`

**影响**:
- 如果 cron job 的 `agent_id` 不是 "default" 或 "main"，找不到 sessions
- 不支持 multi-agent 场景

### 限制 4: Running Channels 依赖
```python
if last_channel not in running_channel_ids:
    continue
```

**问题**: 如果某个 channel 暂时未运行，即使 session store 有数据也会被跳过。

### 限制 5: 无错误恢复
**问题**: 任何异常都会导致返回空列表，没有任何 fallback。

```python
except Exception as e:
    logger.error(f"cron: failed to load session store: {e}", exc_info=True)
    # 直接返回空的 targets
```

## 与 resolve_delivery_target() 的对比

### `_extract_delivery_targets()` (简化版)
- ✅ 快速查询 session store
- ✅ 支持 thread_id
- ❌ 无 fallback
- ❌ 必须有 lastChannel + lastTo
- ❌ 硬编码路径
- ❌ 失败时返回空

### `resolve_delivery_target()` (完整版)
文件: `openclaw/cron/isolated_agent/delivery.py:65-196`

- ✅ 完整的 fallback 链
- ✅ Session store + Session history + Config-driven
- ✅ 支持 account_id 解析
- ✅ 支持 "last" channel 语义
- ✅ 即使没有 `to`，也会返回 channel
- ✅ 错误时有 DEFAULT_CHAT_CHANNEL 兜底

## 为什么错误消息能收到？

错误消息走的是 `_run_heartbeat_async()` -> `_deliver_via_channels()` -> `_extract_delivery_targets()`

**成功的关键**:
1. Session store 中有 `agent:main:main` session
2. 该 session 有 `lastChannel="telegram"` 和 `lastTo="8366053063"`
3. Telegram channel 正在运行
4. 所有过滤条件都通过

**日志验证**:
```
2026-03-15 13:53:34,794 | cron: ✅ delivery target telegram -> 8366053063 (session=agent:main:main)
```

## 为什么新闻消息收不到？

新闻消息走的是 `subagent_announce` -> `requester_origin` (来自 `resolved_delivery`)

**失败的原因**:
1. `cron_bootstrap.py:322-348` 调用 `_extract_delivery_targets()`
2. 但是传入的 `all_keys` 可能为空或者不包含匹配的 session
3. 返回空列表 `[]`
4. `resolved_delivery` 保持为 `{}`
5. `subagent_announce` 收到 `requester_origin={"channel": None, "to": None}`
6. Delivery 失败

**日志验证**:
```
2026-03-15 13:53:16,391 | [subagent-announce] Missing delivery target: channel=None, to=None
```

## 根本原因总结

**核心问题**: `_extract_delivery_targets()` 是一个**简化的、无 fallback 的查询函数**，不适合作为主要的 delivery resolution 逻辑。

**正确的做法**: 使用完整的 `resolve_delivery_target()`，它有：
1. ✅ 完整的 fallback 链
2. ✅ 多种解析策略
3. ✅ 错误恢复机制
4. ✅ 与 TS 版本一致

## 推荐修复

在 `cron_bootstrap.py:322-348` 中，替换：
```python
# 旧代码（简化版）
targets = _extract_delivery_targets(all_keys, agent_part, running_channels)
if targets:
    resolved_delivery = {...}

# 新代码（完整版）
from openclaw.cron.isolated_agent.delivery import resolve_delivery_target

resolved_delivery_target = await resolve_delivery_target(
    job=job,
    session_history=None,
    cfg=config_dict,
    agent_id=job_agent_id,
)

resolved_delivery = {
    "channel": resolved_delivery_target.channel,
    "to": resolved_delivery_target.to,
}
# ...
```
