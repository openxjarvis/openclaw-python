# 端到端测试指南：Cron Delivery 完整修复

## 修复总结

已完成三个根本问题的修复：

### 修复 1: Session Store 加载错误
- **文件**: `openclaw/cron/isolated_agent/delivery.py`
- **修改**: 
  - 将 `load_session_store` 替换为 `load_session_store_from_path`，从文件系统真正加载 sessions.json
  - 修复 cfg 访问逻辑，同时支持 dict 和 object 类型
  - 添加 SessionEntry 到 dict 的转换
- **影响**: `resolve_delivery_target` 现在能正确读取 session store，找到 `lastTo`

### 修复 2: Session Key 注入缺失
- **文件**: `openclaw/agents/tools/cron.py`
- **修改**:
  - 添加 `agent_session_key` 参数到 CronTool 构造函数
  - 添加 `set_agent_session_key` setter 方法
  - 在 `_action_add` 中注入 session_key 到 job_config
  - 从 session_key 中提取 agent_id
  - 将 session_key 和 agent_id 传给 CronJob 构造函数
- **影响**: 新创建的 job 会包含完整的 session_key 和 agent_id

### 修复 3: 导出缺失的函数
- **文件**: `openclaw/config/sessions/__init__.py`
- **修改**: 导出 `resolve_store_path` 和 `load_session_store_from_path`
- **影响**: `delivery.py` 能正确 import 所需函数

## 测试步骤

### 1. 重启 Gateway

```bash
# 停止现有 gateway
pkill -f "openclaw.*gateway"

# 清空日志
> /tmp/openclaw-gateway-new.log

# 启动 gateway（根据项目配置调整命令）
cd /Users/long/Desktop/XJarvis/openclaw-python
uv run python -m openclaw.gateway.boot > /tmp/openclaw-gateway-new.log 2>&1 &

# 等待启动（5-10秒）
sleep 8

# 检查启动状态
tail -50 /tmp/openclaw-gateway-new.log
```

### 2. 通过 Telegram 创建新的 Cron Job

在 Telegram 中发送消息：

```
创建一个 cron job，每分钟搜索一次 AI 新闻，发送给我
```

或使用更具体的配置：

```
添加定时任务：
- 名称：AI新闻测试
- 间隔：每1分钟
- 任务：搜索最新的 AI 新闻，总结3条
- 发送给我
```

### 3. 验证 Job 创建

```bash
# 检查 jobs.json
cat ~/.openclaw/cron/jobs.json | python3 -m json.tool

# 期望看到新创建的 job 包含：
# - "sessionKey": "agent:main:telegram:direct:8366053063"
# - "agentId": "main"
```

**期望输出示例**:
```json
{
    "id": "cron-abc12345",
    "name": "AI新闻测试",
    "sessionKey": "agent:main:telegram:direct:8366053063",
    "agentId": "main",
    "delivery": {
        "mode": "announce",
        "channel": "last"
    },
    ...
}
```

### 4. 监控 Job 执行

```bash
# 实时监控日志
tail -f /tmp/openclaw-gateway-new.log | grep -E "(cron|session_key|delivery|telegram)"
```

**期望看到的日志**:
```
cron: job.session_key=agent:main:telegram:direct:8366053063
cron: resolving delivery with lookup_agent_id=main
cron delivery: found main session agent:main:main
cron delivery: resolved lastTo from session: telegram:8366053063
cron: resolved delivery target: {'channel': 'telegram', 'to': '8366053063'}
[subagent-announce] Cron job announce: AI新闻测试 → telegram:8366053063
cron: delivered text to telegram chat_id=8366053063
```

### 5. 验证 Telegram 接收消息

等待 cron job 执行（根据设置的间隔，最长1-2分钟），在 Telegram 中应该收到：

- 来自 OpenClaw 的消息
- 包含 AI 新闻搜索结果
- 格式清晰，包含标题和摘要

### 6. 检查执行历史

```bash
# 查看最近的 run log
ls -lt ~/.openclaw/cron/runs/ | head -5
cat ~/.openclaw/cron/runs/$(ls -t ~/.openclaw/cron/runs/ | head -1)
```

**期望看到**:
```json
{
    "job_id": "cron-abc12345",
    "status": "ok",
    "delivered": true,
    "delivery_channel": "telegram",
    "delivery_to": "8366053063",
    ...
}
```

## 调试问题

### 如果 Telegram 仍未收到消息

1. **检查 session store**:
```bash
cat ~/.openclaw/sessions.json | python3 -c "import sys, json; data=json.load(sys.stdin); print(json.dumps(data.get('agent:main:main', {}), indent=2))"
```

期望看到 `lastChannel: "telegram"` 和 `lastTo: "8366053063"`

2. **检查 job 的 session_key**:
```bash
cat ~/.openclaw/cron/jobs.json | python3 -c "import sys, json; jobs=json.load(sys.stdin)['jobs']; print('\n'.join(f\"{j['id']}: sessionKey={j.get('sessionKey')}\" for j in jobs))"
```

所有 job 应该都有非空的 `sessionKey`

3. **检查日志中的错误**:
```bash
grep -i "error\|exception\|failed" /tmp/openclaw-gateway-new.log | tail -20
```

### 如果 session_key 仍然是 None

说明 CronTool 的 `agent_session_key` 没有被正确设置。可能需要：

1. 检查 Pi runtime 是否正确传递 session_key
2. 或者确保 `set_chat_context` 在创建 job 前被调用
3. 或者在 gateway 处理 Telegram 消息时显式设置

## 成功标准

✅ 新创建的 job 在 jobs.json 中包含 `sessionKey` 和 `agentId`  
✅ Job 执行时日志显示正确的 delivery target  
✅ Telegram 收到 cron job 的结果消息  
✅ 日志中没有 "Missing delivery target" 或 "to=None" 错误  

## 下一步（可选）

如果基本测试通过，可以进一步测试：

1. 创建多个不同间隔的 cron job
2. 测试 job 的启用/禁用
3. 测试 job 的删除
4. 测试不同 payload 类型（如果支持）
5. 测试错误处理和 failure alert

## 回滚计划

如果测试失败且需要回滚：

```bash
# 备份修改后的文件
cd /Users/long/Desktop/XJarvis/openclaw-python
cp openclaw/cron/isolated_agent/delivery.py openclaw/cron/isolated_agent/delivery.py.new
cp openclaw/agents/tools/cron.py openclaw/agents/tools/cron.py.new
cp openclaw/config/sessions/__init__.py openclaw/config/sessions/__init__.py.new

# 从 git 恢复（如果有备份）
# git checkout openclaw/cron/isolated_agent/delivery.py
# git checkout openclaw/agents/tools/cron.py
# git checkout openclaw/config/sessions/__init__.py

# 重启 gateway
pkill -f "openclaw.*gateway"
# ... 重新启动
```
