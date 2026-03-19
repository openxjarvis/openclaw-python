# Session Store 验证报告

## 存储位置
```
/Users/long/.openclaw/agents/main/sessions/sessions.json
```

## 统计信息
- **总 session 数**: 30
- **存储文件**: 存在且可读

## 关键发现

### 1. 有效的 Delivery Target

**Session: `agent:main:main`**
```json
{
  "lastChannel": "telegram",
  "lastTo": "8366053063"
}
```

✅ **这是唯一有完整 delivery 信息的 session！**

### 2. 不完整的 Sessions

大多数 session 缺少 `lastChannel` 或 `lastTo`:

```
agent:main:telegram:direct:8366053063
  lastChannel: telegram
  lastTo: N/A  ← 缺失！

agent:main:telegram:group:1
  lastChannel: N/A
  lastTo: N/A

agent:default:cron:cron-08556e8c
  lastChannel: N/A
  lastTo: N/A
```

### 3. Cron Job Sessions

所有 cron job sessions 都没有 delivery 信息：
- `agent:default:cron:cron-08556e8c` - N/A, N/A
- `agent:default:cron:cron-1d869c96` - N/A, N/A
- ...

## 为什么错误消息能收到？

**成功路径**: 错误消息走 heartbeat delivery → 查询 session store

```python
# cron_bootstrap.py:785-804
targets = _extract_delivery_targets(all_keys, agent_part, running_channels)

# 查询逻辑：
1. 提取 agent_part = "default" (from cron job)
2. 规范化为 target_agent = "main" (default -> main)
3. 遍历所有 sessions，找到 session_agent = "main" 的
4. 找到 "agent:main:main" - lastChannel=telegram, lastTo=8366053063
5. ✅ 返回 target: (telegram, 8366053063, None)
```

**日志验证**:
```
2026-03-15 13:53:34,794 | cron: ✅ delivery target telegram -> 8366053063 (session=agent:main:main)
```

## 为什么新闻消息收不到？

**失败路径**: 新闻消息走 subagent_announce → requester_origin

### 问题 1: 错误的 Session Keys

在 `cron_bootstrap.py:322-348` 中：
```python
# 获取 session keys
all_keys = _list_all_session_keys(cm)
agent_part = _extract_agent_part(job_session_key)  # ← job_session_key 是什么？

# 如果 job_session_key = "agent:default:cron:cron-62d5b3f8"
# agent_part = "default"
# target_agent = "main" (default -> main 映射)
```

**预期行为**: 应该能找到 `agent:main:main` session。

### 问题 2: _list_all_session_keys() 可能返回空

```python
def _list_all_session_keys(cm: Any) -> list[str]:
    try:
        sm = getattr(cm, "session_manager", None)
        if sm:
            if hasattr(sm, "_get_session_store"):
                store = sm._get_session_store()
                return list(store.keys()) if store else []
            elif hasattr(sm, "_sessions"):
                return list(sm._sessions.keys())
    except Exception:
        pass
    return []  # ← 如果出错，返回空列表
```

**可能的情况**:
1. `cm.session_manager` 不存在
2. `_get_session_store()` 返回 None
3. 内存中的 `_sessions` 不完整

### 问题 3: _extract_delivery_targets() 的过滤条件

即使传入了正确的 session keys，还要通过所有过滤：
```python
# 1. Agent 匹配
if session_agent != target_agent:
    continue  # ← 可能被过滤

# 2. Channel 验证
if last_channel not in running_channel_ids:
    continue  # ← Telegram 必须在运行

# 3. Recipient 验证
if not last_to:
    continue  # ← 必须有 lastTo
```

从 session store 看，`agent:main:main` 应该能通过所有检查。

### 问题 4: 根本原因 - 在 _agent_run 中查询

关键问题在 `cron_bootstrap.py:157-348` 的 `_agent_run` 函数中：

```python
async def _agent_run(job: "CronJob", message: str) -> dict[str, Any]:
    # ...
    cm = deps.get_channel_manager()
    
    # 在这里调用 _list_all_session_keys(cm)
    all_keys = _list_all_session_keys(cm)
    
    # 问题：此时 cm 可能还未完全初始化
    # 或者 session_manager 的状态不正确
```

**对比 heartbeat delivery**: 在 `_run_heartbeat_async` 中调用，此时所有组件已完全初始化。

## 实际测试验证

让我检查日志中的 `_extract_delivery_targets` 调用：

### 成功的调用 (heartbeat delivery)
```
2026-03-15 13:53:34,794 | cron: _extract_delivery_targets looking for agent='main'
2026-03-15 13:53:34,794 | cron: loaded 29 sessions from store
2026-03-15 13:53:34,794 | cron: ✅ delivery target telegram -> 8366053063 (session=agent:main:main)
```

### 失败的调用 (subagent_announce)
```
2026-03-15 13:53:16,391 | [subagent-announce] Missing delivery target: channel=None, to=None
```

**注意**: 没有看到 `_extract_delivery_targets` 的日志！

**说明**: 在 `_agent_run` 中调用 `_extract_delivery_targets` 可能：
1. 根本没执行（异常或跳过）
2. 返回了空列表
3. `all_keys` 本身就是空的

## 结论

### Session Store 本身是健康的
- ✅ 文件存在
- ✅ 有 30 个 sessions
- ✅ `agent:main:main` 有完整的 delivery 信息

### 问题在于查询时机和方法
1. **查询方法**: `_extract_delivery_targets()` 太简化，无 fallback
2. **查询时机**: 在 `_agent_run` 中查询，可能 session_manager 状态不正确
3. **根本方案**: 应该使用完整的 `resolve_delivery_target()`

### 为什么需要 resolve_delivery_target()？

即使 `_extract_delivery_targets()` 能找到 session，也存在问题：
- ❌ 如果 session store 文件损坏/缺失 → 无 fallback
- ❌ 如果 session_manager 未初始化 → 无 fallback
- ❌ 如果 running_channel_ids 为空 → 无 fallback

而 `resolve_delivery_target()` 有：
- ✅ Session store fallback
- ✅ Session history fallback
- ✅ Config-driven channel selection
- ✅ DEFAULT_CHAT_CHANNEL 兜底

## 推荐修复

**替换 `cron_bootstrap.py:322-348` 的实现**:
```python
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

**预期结果**:
- ✅ 即使 `_list_all_session_keys` 返回空，也能通过 fallback 找到 delivery target
- ✅ 与 TS 版本行为一致
- ✅ 与 Python `delivery.py` 行为一致
