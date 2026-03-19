# 端到端验证指南

## ✅ 所有修复已完成

### 修复清单

1. ✅ **改进 agent_id 提取逻辑** (`cron_bootstrap.py`)
   - 使用标准的 `parse_agent_session_key` 替代手动字符串分割
   - 修复了 `"cron:job-1"` → `"cron"` 的 bug
   - 对齐 TS 版本的 `normalizeAgentId` 行为

2. ✅ **添加 `resolve_agent_main_session_key` 函数** (`routing/session_key.py`)
   - 实现了与 TS 版本完全一致的主 session key 解析
   - 支持自定义 `mainKey` 配置
   - 正确处理 dict 和 object 类型的 config

3. ✅ **修复 Subagent Announce 类型错误** (`subagent_announce.py`)
   - 修复 `parsed.get("agent_id")` → `parsed.agent_id`
   - NamedTuple 使用属性访问，不是字典访问

4. ✅ **创建 TS/Python 对齐测试** (`test_ts_python_alignment.py`)
   - 5 个测试套件全部通过
   - 验证了关键函数与 TS 版本的对齐性

5. ✅ **Gateway 已重启**
   - 所有修复已生效
   - 可以开始端到端测试

## 🧪 端到端测试步骤

### 步骤 1: 通过 Telegram 创建测试 Cron Job

发送消息给 Telegram bot：

```
帮我创建一个测试任务，1分钟后执行，搜索"Python 3.13 新特性"并总结
```

### 步骤 2: 检查 Job 配置

```bash
cat ~/.openclaw/cron/jobs.json | jq '.jobs[] | {id, name, session_key, agent_id, delivery}'
```

**期望输出：**

```json
{
  "id": "cron-xxxxx",
  "name": "测试任务",
  "session_key": "agent:main:telegram:direct:8366053063",  // 或类似格式
  "agent_id": null,  // 可能为 null 或 "main"
  "delivery": {
    "mode": "announce",
    "channel": null,
    "to": null
  }
}
```

**关键检查：**
- ✅ `session_key` 不为空
- ✅ `session_key` 格式正确（`agent:main:...` 或简单格式）
- ✅ `delivery.mode` 为 `"announce"`

### 步骤 3: 等待任务执行并观察日志

```bash
tail -f /tmp/openclaw-gateway-new.log | grep -E "(resolving delivery|resolved delivery|subagent-announce|delivered text|Missing delivery)"
```

**期望日志（新版本）：**

```
cron: resolving delivery with original_session_key=agent:main:telegram:direct:8366053063, lookup_agent_id=main
cron: resolved delivery target: {'channel': 'telegram', 'to': '8366053063'}
[subagent-announce] Cron job announce: 测试任务 → telegram:8366053063
cron: delivered text to telegram chat_id=8366053063
```

**❌ 旧版本的错误日志（不应再出现）：**

```
cron: resolving delivery with original_session_key=agent:main:telegram:direct:8366053063, lookup_agent_id=default
cron: resolved delivery target: {'channel': 'telegram', 'to': None}
[subagent-announce] Missing delivery target: channel=telegram, to=None
cron: enqueue system event to session='main': 'Cron (error): ...'
```

### 步骤 4: 验证 Telegram 接收

- ✅ **成功标志**：Telegram 收到搜索结果消息
- ❌ **失败标志**：只收到 `"Cron (error): ..."` 消息

## 🔍 关键日志对比

| 指标 | 修复前（错误） | 修复后（正确） |
|------|----------------|----------------|
| `original_session_key` | `agent:main:telegram:direct:8366053063` | 同左（正确） |
| `lookup_agent_id` | `"default"` ❌ | `"main"` ✅ |
| `resolved channel` | `"telegram"` | `"telegram"` |
| `resolved to` | `None` ❌ | `"8366053063"` ✅ |
| Telegram 收到结果 | ❌ 否 | ✅ 是 |
| 错误消息 | ✅ 能收到 | ❌ 不应出现 |

## 📊 对齐验证总结

### 与 TS 版本的关键对齐点

| 功能 | TS 版本 | Python 版本（修复后） | 状态 |
|------|---------|---------------------|------|
| **Session Key 传递** | `job.sessionKey` 传给 resolver | `job.session_key` 传给 resolver | ✅ 对齐 |
| **Agent ID 解析** | `normalizeAgentId(agentId)` | `parse_agent_session_key().agent_id` | ✅ 对齐 |
| **Session Store 查找** | `store[sessionKey] ?? store[mainKey]` | 同左 | ✅ 对齐 |
| **Fallback 链** | lastChannel > config > error | lastChannel > config > DEFAULT | ⚠️ 更宽松（有意） |
| **Main Session Key** | `resolveAgentMainSessionKey(cfg, agentId)` | 同左 | ✅ 对齐 |

### 修复的具体 Bug

1. **Bug #1: 错误的 agent_id 提取**
   - **修复前**：`"cron:job-1"` → `lookup_agent_id = "cron"` ❌
   - **修复后**：`"cron:job-1"` → `lookup_agent_id = job_agent_id` ✅

2. **Bug #2: NamedTuple 访问错误**
   - **修复前**：`parsed.get("agent_id")` → AttributeError ❌
   - **修复后**：`parsed.agent_id` ✅

3. **Bug #3: 缺失函数导入**
   - **修复前**：`from openclaw.config.sessions import resolve_agent_main_session_key` → ImportError ❌
   - **修复后**：`from openclaw.routing.session_key import resolve_agent_main_session_key` ✅

## 🎯 测试成功标准

### 最低标准（必须满足）

1. ✅ Telegram 能收到 cron job 的结果消息
2. ✅ 日志显示 `lookup_agent_id=main`（而不是 `default`）
3. ✅ 日志显示 `to='8366053063'`（而不是 `None`）
4. ✅ 不再出现 `Missing delivery target` 警告

### 理想标准（更好的验证）

1. ✅ 多次测试都成功
2. ✅ 不同类型的 cron job（搜索、提醒等）都能正常投递
3. ✅ 日志格式与 TS 版本一致
4. ✅ 没有其他意外的错误或警告

## 🚀 如果测试失败

### Debug 检查清单

1. **检查 `job.session_key` 是否正确保存**
   ```bash
   cat ~/.openclaw/cron/jobs.json | jq '.jobs[] | .session_key'
   ```
   - 如果为 `null` 或空，说明 job 创建时未正确设置 `session_key`

2. **检查 session store 中是否有 delivery 信息**
   ```bash
   cat ~/.openclaw/agents/main/sessions/sessions.json | jq '.sessions["agent:main:main"]' | head -20
   ```
   - 应该看到 `lastChannel: "telegram"` 和 `lastTo: "8366053063"`

3. **检查日志中的 `lookup_agent_id`**
   ```bash
   grep "lookup_agent_id" /tmp/openclaw-gateway-new.log
   ```
   - 应该是 `"main"`，不是 `"default"` 或其他值

4. **验证修复已生效**
   ```bash
   grep "parse_agent_session_key" /Users/long/Desktop/XJarvis/openclaw-python/openclaw/gateway/cron_bootstrap.py
   ```
   - 应该找到 `from openclaw.routing.session_key import parse_agent_session_key`

## 📝 下一步

如果所有测试通过：

1. 清理临时测试文件
2. Git 提交修复
3. 更新文档说明 session key 的工作原理
4. 考虑添加集成测试到 CI/CD

如果测试失败：

1. 提供详细的日志和 `jobs.json` 内容
2. 说明具体的失败现象
3. 我会进一步诊断和修复
