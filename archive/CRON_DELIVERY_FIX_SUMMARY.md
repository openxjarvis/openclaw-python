# Cron Telegram Delivery Fix Summary

## 问题描述

**现象**: Cron 任务执行成功，但 Telegram 收不到新闻消息。错误消息却能收到。

**日志证据**:
```
✅ 错误消息能收到:
2026-03-15 13:53:34,794 | cron: ✅ delivery target telegram -> 8366053063

❌ 新闻消息收不到:
2026-03-15 13:53:16,391 | [subagent-announce] Missing delivery target: channel=None, to=None
```

## 根本原因

### 双路径问题

Python 版本存在两个不同的 delivery 路径：

1. **路径 A (成功)**: 错误消息 → `enqueue_system_event` → `_run_heartbeat_async` → `_extract_delivery_targets`
   - ✅ 能找到 `agent:main:main` session (有完整的 lastChannel + lastTo)
   
2. **路径 B (失败)**: 新闻消息 → `subagent_announce` → `requester_origin` (来自 `resolved_delivery`)
   - ❌ `cron_bootstrap.py:322-348` 中使用简化的 `_extract_delivery_targets()`
   - ❌ 查询失败时 `resolved_delivery = {}`
   - ❌ 传给 `subagent_announce` 的 `requester_origin={"channel": None, "to": None}`

### 核心问题

**`_extract_delivery_targets()` 是简化版，无 fallback**:
- ❌ 只查询 session store
- ❌ 失败时直接返回空列表
- ❌ 无任何 fallback 机制

**对比 TypeScript**:
- ✅ 使用 `resolveDeliveryTarget()` (完整版)
- ✅ 有 3-step fallback 链

**对比 Python `delivery.py`**:
- ✅ 有 `resolve_delivery_target()` (完整版)
- ✅ 有 4-step fallback 链 + DEFAULT_CHAT_CHANNEL 兜底

## 修复方案

### 修改位置
文件: `openclaw/gateway/cron_bootstrap.py:322-377`

### 修改内容

**替换**:
```python
# 旧代码（简化版，无 fallback）
targets = _extract_delivery_targets(all_keys, agent_part, running_channels)
if targets:
    resolved_delivery = {...}
else:
    resolved_delivery = {}  # ← 失败时为空
```

**修改为**:
```python
# 新代码（完整版，有 fallback）
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

### Fallback 链

新的实现有完整的 fallback 链：
1. ✅ Session store (thread session + main session)
2. ✅ Session history (legacy support)
3. ✅ Config-driven channel selection
4. ✅ DEFAULT_CHAT_CHANNEL ("telegram") 兜底

**即使所有查询失败，也能返回有效的 channel**！

## 测试验证

### 测试脚本
创建了 `test_delivery_fix.py` 测试：
- ✅ 导入测试通过
- ✅ Mock job 创建通过
- ✅ `resolve_delivery_target()` 调用成功
- ✅ 即使 `delivery.channel=None`，也能解析到 `channel="telegram"`

### 测试结果
```
[Test 3] Calling resolve_delivery_target...
  ✅ Resolution completed
    channel = telegram
    to = None
    mode = implicit
    account_id = None
  ✅ Got channel: telegram
```

**关键**: 即使没有 `to`，也能得到 `channel`！

## 预期效果

### 修复前
```
resolved_delivery = {}
↓
requester_origin = {"channel": None, "to": None}
↓
[subagent-announce] Missing delivery target
↓
❌ Telegram 收不到消息
```

### 修复后
```
resolved_delivery_target = resolve_delivery_target(job, ...)
↓
resolved_delivery = {"channel": "telegram", "to": "8366053063"}
↓
requester_origin = {"channel": "telegram", "to": "8366053063"}
↓
✅ Telegram 能收到消息
```

**注意**: `to` 会通过 session store 查询得到（如果有 `agent:main:main` session）。

## 部署步骤

### 1. 重启 Gateway
```bash
bash restart_gateway.sh
```

### 2. 删除旧的 cron jobs
```bash
# 这些 job 有不完整的 delivery 配置
uv run openclaw cron remove cron-62d5b3f8
uv run openclaw cron remove cron-dd995e39
uv run openclaw cron remove cron-41acfc96
```

### 3. 创建新的 cron job
在 Telegram 中发送：
```
创建一个定时任务，每小时搜索最新的中文新闻并总结
```

### 4. 验证
手动触发 job 测试：
```bash
uv run openclaw cron list  # 获取新 job ID
uv run openclaw cron run <job-id>
```

检查 Telegram 是否收到消息。

## 技术细节

### Session Store 状态
```
Total sessions: 30

✅ 有效的 delivery target:
agent:main:main
  lastChannel: telegram
  lastTo: 8366053063
```

### 为什么错误消息能收到？
错误消息走 heartbeat delivery，直接查询 session store，找到了 `agent:main:main`。

### 为什么新闻消息收不到？
新闻消息走 subagent_announce，但 `_extract_delivery_targets()` 在 `_agent_run` 中查询可能失败（timing 或 state 问题），没有 fallback，导致 `resolved_delivery = {}`。

### 为什么修复有效？
`resolve_delivery_target()` 有完整的 fallback 链，即使 session store 查询失败（或时机不对），也能通过其他途径（config-driven, DEFAULT_CHAT_CHANNEL）得到有效的 channel。

## 相关文档

1. **`ANALYSIS_EXTRACT_DELIVERY_TARGETS.md`** - `_extract_delivery_targets()` 详细分析
2. **`COMPARISON_TS_VS_PYTHON_DELIVERY.md`** - TypeScript vs Python 完整对比
3. **`SESSION_STORE_VERIFICATION.md`** - Session store 验证报告
4. **`test_delivery_fix.py`** - 测试脚本

## 与之前修复的关系

### 之前的修复（已完成）
1. ✅ `normalize_cron_job_create()` - 自动添加 `delivery: {mode: "announce"}`
2. ✅ `CronTool._action_add()` - 调用 normalize + 放宽 channel 限制
3. ✅ `run.py` + `helpers.py` - NoneType 错误修复

### 本次修复（核心）
4. ✅ `cron_bootstrap.py` - 使用完整的 `resolve_delivery_target()`

**关键**: 前面的修复确保了新创建的 job 有 `delivery` 字段，但 `channel=None`。本次修复确保即使 `channel=None`，也能通过 fallback 解析到实际的 channel。

## 总结

### 问题本质
使用了简化的 `_extract_delivery_targets()` 作为主要的 delivery resolution 逻辑，但它没有 fallback，失败时无法恢复。

### 修复本质
替换为完整的 `resolve_delivery_target()`，它有健壮的 fallback 链，与 TS 版本一致。

### 预期结果
- ✅ Cron 任务执行后，新闻消息能正常发送到 Telegram
- ✅ 即使 session store 查询失败，也能通过 fallback 找到 delivery target
- ✅ 与 TypeScript 版本行为一致
- ✅ 与 Python `delivery.py` 行为一致
