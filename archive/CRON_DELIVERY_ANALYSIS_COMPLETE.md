# Cron Job Telegram Delivery 问题完整分析

## 问题描述

用户创建的 cron job（新闻搜索任务）成功执行，但结果没有发送到 Telegram。

## 根本原因分析

### 1. **delivery 字段缺失**

**问题：** Agent 创建的 cron job 缺少 `delivery` 字段

检查现有 jobs：
```json
{
  "id": "cron-62d5b3f8",
  "name": "新闻搜索-第1次",
  "session_target": "isolated",
  "payload": {"kind": "agentTurn", "message": "搜索最新新闻"},
  "delivery": null  // ← 缺失！
}
```

**预期行为（TS 版本）：**
```json
{
  "delivery": {
    "mode": "announce",
    "channel": null,  // 运行时解析
    "to": null
  }
}
```

### 2. **Tool 层问题**

**位置：** `openclaw/agents/tools/cron.py` 的 `_action_add()` 方法

**原问题1 - 未调用 normalize：**
- 旧代码：手动构造 `CronJob` 各个字段
- 应该做：调用 `normalize_cron_job_create()` 来自动补全 `delivery`

**原问题2 - channel 条件过严：**
```python
# 旧代码（已修复）
if channel:  # ← 只有有 channel 才创建 delivery
    delivery = CronDelivery(...)
```

这导致即使 normalize 添加了 `{mode: "announce"}`，最终 `delivery` 还是 `None`。

**修复后：**
```python
# 新代码
if delivery_config:
    mode = delivery_config.get("mode", "announce")
    channel = delivery_config.get("channel", "")
    # ...
    # 即使 channel 为空也创建 delivery（运行时解析）
    delivery = CronDelivery(
        mode=mode,
        channel=channel or None,
        to=target or None,
        ...
    )
```

### 3. **session_key 和 agent_id 缺失**

**检查结果：**
```python
Job: cron-62d5b3f8
  session_key: MISSING  # ← 无法从 session store 查询
  agent_id: MISSING
```

**影响：**
- 无法从 session store 解析最后的聊天 channel
- 必须回退到 config 驱动的 channel 选择

**根本原因：**
- `CronTool` 初始化时没有设置 chat context
- `set_chat_context()` 方法存在但**从未被调用**

### 4. **Delivery 解析链路**

**完整链路：**
```
1. normalize_cron_job_create()  
   → 添加 delivery: {mode: "announce"}

2. CronTool._action_add()  
   → 创建 CronDelivery 对象（channel=None）

3. run_cron_isolated_agent_turn()  
   → 调用 resolve_delivery_target()

4. resolve_delivery_target() 解析顺序：
   a. 显式 channel + to → 直接使用
   b. session_key → 查询 session store
   c. session_history → 从历史消息解析
   d. config 驱动 → resolve_message_channel_selection()
   e. 最终回退 → DEFAULT_CHAT_CHANNEL
```

**当前问题：**
- (2) 修复后，`CronDelivery` 对象存在但 `channel=None`
- (3)-(4) 会走 session store 查询，但因为没有 `session_key`，查询失败
- 最终回退到 (4d)，需要验证这一步是否能正确解析 Telegram

## 修复状态

### ✅ 已完成

1. **normalize 层：** 添加调试日志，验证正常工作
   - 文件：`openclaw/cron/normalize.py`
   - 测试：`test_cron_delivery_fix.py` 全部通过

2. **tool 层：** 修改 `_action_add()` 调用 normalize + 放宽 channel 限制
   - 文件：`openclaw/agents/tools/cron.py`
   - 测试：`test_cron_tool_fix.py` 通过

3. **测试验证：**
   - `test_cron_fixes.py` - 21/21 tests passed（之前的 NoneType 修复）
   - `test_cron_delivery_fix.py` - normalize 层测试通过
   - `test_cron_tool_fix.py` - tool 层测试通过

### 🚧 待完成

1. **session_key 设置：**
   - 问题：`CronTool.set_chat_context()` 未被调用
   - 需要在 agent runtime / tool registry 中调用该方法
   - 或者在 tool 初始化时注入 `current_channel` 和 `current_chat_id`

2. **delivery resolver 验证：**
   - 验证 `resolve_message_channel_selection()` 能否正确返回 Telegram
   - 测试整个端到端流程（创建 job → 执行 → 发送）

3. **日志验证：**
   - 查看 Gateway 运行日志，确认 normalize 和 delivery resolver 的输出
   - 检查是否有错误或警告

## 推荐下一步

### 方案A：快速修复（推荐）

**在 tool 初始化时注入 chat context：**

修改 `openclaw/gateway/pi_runtime.py` 或 tool 使用处：
```python
# 在创建 agent session 时
cron_tool = registry.get("cron")
if cron_tool and hasattr(cron_tool, "set_chat_context"):
    # 从当前消息的 metadata 获取
    channel = message_metadata.get("channel")
    chat_id = message_metadata.get("chat_id")
    if channel and chat_id:
        cron_tool.set_chat_context(channel, chat_id)
```

### 方案B：完整方案

**在 normalize 或 tool 层自动推断 session_key：**
- 根据 agent_id + channel + chat_id 构造 session_key
- 格式：`f"agent:{agent_id}:{channel}:{chat_id}"`
- 参考 TS 版本 `buildSessionKey()` 逻辑

### 方案C：测试先行

**先测试当前修复是否足够：**
1. 重启 Gateway（已完成）
2. 删除旧的测试 jobs
3. 让 agent 创建新 job
4. 检查新 job 是否有 delivery 字段
5. 触发执行，看是否能收到消息

## 测试命令

```bash
# 1. 查看现有 jobs
uv run python -c "
import json
with open('~/.openclaw/cron/jobs.json') as f:
    data = json.load(f)
    for job in data['jobs']:
        print(f'{job[\"id\"]}: delivery={job.get(\"delivery\")}, session_key={job.get(\"session_key\")}')
"

# 2. 删除测试 jobs
uv run openclaw cron remove cron-62d5b3f8
uv run openclaw cron remove cron-dd995e39
uv run openclaw cron remove cron-41acfc96

# 3. 创建新 job（通过 agent）
# 在 Telegram 中发消息：创建一个定时任务，每小时搜索最新中文新闻

# 4. 检查新 job
uv run openclaw cron list

# 5. 手动触发执行
uv run openclaw cron run <job-id>

# 6. 查看执行日志
tail -n 200 /tmp/openclaw-gateway.log | grep -i "delivery\|cron\|telegram"
```

## 关键文件

1. **修改的文件：**
   - `openclaw/cron/normalize.py` - 添加了调试日志
   - `openclaw/agents/tools/cron.py` - 调用 normalize + 放宽 channel 限制

2. **测试文件：**
   - `test_cron_delivery_fix.py` - normalize 层测试
   - `test_cron_tool_fix.py` - tool 层测试
   - `test_cron_fixes.py` - 之前的 NoneType 修复测试

3. **需要检查的文件：**
   - `openclaw/gateway/pi_runtime.py` - agent runtime
   - `openclaw/gateway/channel_manager.py` - message handler
   - `openclaw/cron/isolated_agent/delivery.py` - delivery resolver
   - `openclaw/infra/outbound/channel_selection.py` - channel selection

## TypeScript 参考

- `src/cron/normalize.ts:466-480` - auto-delivery 逻辑
- `src/cron/isolated-agent/delivery-target.ts` - delivery resolver
- `src/cron/service.ts` - cron service
- `src/agents/tools/cron.ts` - tool 实现（查看是否设置 session_key）
