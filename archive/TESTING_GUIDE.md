# 🧪 Cron Delivery 修复测试指南

## ✅ 修复已完成

**根本原因：** `cron_bootstrap.py` 传入了错误的 `agent_id` 给 `resolve_delivery_target`。它传入的是 cron 自己的 isolated session 的 agent_id（`"default"`），而不是创建 job 的原始 agent_id（`"main"`）。

**修复方法：** 从 `job.session_key` 中提取原始的 agent_id，用于 session store 查找。

**已完成：**
- ✅ 修改 `openclaw/gateway/cron_bootstrap.py`
- ✅ 添加 agent_id 提取逻辑测试
- ✅ 重启 Gateway（新代码已生效）

## 🚀 现在测试！

### 步骤 1：通过 Telegram 创建 Cron Job

发送消息给 Telegram bot：

```
帮我设置一个新闻搜索任务，1分钟后执行，搜索最新科技新闻并总结
```

### 步骤 2：等待任务执行

等待大约 1 分钟，观察：

1. **Telegram 应该收到新闻消息**（修复前收不到）
2. **不应该收到 "Cron (error):" 消息**（修复前会收到）

### 步骤 3：检查日志（可选）

如果想看详细日志，运行：

```bash
tail -f /tmp/openclaw-gateway.log | grep -E "(resolving delivery|resolved delivery|subagent-announce|delivered text)"
```

**期望看到的日志：**

```
cron: resolving delivery with original_session_key=agent:main:telegram:direct:8366053063, lookup_agent_id=main
cron: resolved delivery target: {'channel': 'telegram', 'to': '8366053063'}
[subagent-announce] Cron job announce: 新闻搜索 → telegram:8366053063
cron: delivered text to telegram chat_id=8366053063 ✅
```

**修复前的错误日志（不应该再出现）：**

```
cron: resolved delivery target: {'channel': 'telegram', 'to': None}
[subagent-announce] Missing delivery target: channel=telegram, to=None
cron: enqueue system event to session='main': 'Cron (error): ...'
```

## 🎯 成功标准

1. ✅ Telegram 收到新闻消息
2. ✅ 日志显示 `lookup_agent_id=main`（而不是 `default`）
3. ✅ 日志显示 `to='8366053063'`（而不是 `None`）
4. ✅ 日志显示 `delivered text to telegram`

## 🐛 如果还是失败

### Debug Checklist

1. **检查 job 的 session_key：**
   ```bash
   cat ~/.openclaw/cron/jobs.json | jq '.jobs[] | {id, session_key, agent_id}'
   ```
   
   期望：`session_key` 应该类似 `"agent:main:telegram:direct:8366053063"` 或 `"main"`
   
   ❌ 如果是 `null` 或空字符串，说明 job 创建时没有正确保存 session_key

2. **检查 session store：**
   ```bash
   cat ~/.openclaw/agents/main/sessions/sessions.json | jq '.sessions["agent:main:main"]' | head -20
   ```
   
   期望：应该看到 `lastChannel: "telegram"` 和 `lastChatId: "8366053063"`

3. **检查 Gateway 日志中的 agent_id：**
   ```bash
   grep "resolving delivery" /tmp/openclaw-gateway.log | tail -5
   ```
   
   期望：应该看到 `lookup_agent_id=main`
   
   ❌ 如果是 `lookup_agent_id=default`，说明修复没有生效

4. **确认 Gateway 版本：**
   ```bash
   grep "original_session_key" /Users/long/Desktop/XJarvis/openclaw-python/openclaw/gateway/cron_bootstrap.py
   ```
   
   应该找到我们添加的修复代码

## 📝 对比测试

| 测试项 | 修复前 | 修复后（期望） |
|--------|--------|----------------|
| Telegram 收到新闻 | ❌ 收不到 | ✅ 能收到 |
| lookup_agent_id | `default` | `main` |
| resolved delivery to | `None` | `"8366053063"` |
| 错误消息 | ✅ 能收到 | ❌ 不应该出现 |

## 🎉 修复验证完成后

如果测试成功，可以：

1. **删除测试文件：**
   ```bash
   cd /Users/long/Desktop/XJarvis/openclaw-python
   rm -f test_*.py ANALYSIS_*.md COMPARISON_*.md SESSION_*.md CRON_DELIVERY_*.md
   ```

2. **Git 保存：**
   ```bash
   git add openclaw/gateway/cron_bootstrap.py
   git commit -m "fix(cron): use original session_key for delivery resolution
   
   修复 cron job delivery 失败的问题。原因是传入了错误的 agent_id
   (cron 自己的 isolated session) 给 resolve_delivery_target，
   导致查找不到原始 session 中保存的 delivery target 信息。
   
   现在从 job.session_key 提取原始 agent_id，确保能正确查找到
   创建 job 时所在 session 的 delivery 配置。"
   ```

## 🔧 技术细节

### 关键修复点

**Before:**
```python
agent_id = job_agent_id  # "default" - 错误的 cron session
resolved_delivery_target = await resolve_delivery_target(
    job=job,
    cfg=config_dict,
    agent_id=agent_id,  # 查找 agent:default:cron:... （空的）
)
```

**After:**
```python
original_session_key = getattr(job, "session_key", None)
lookup_agent_id = job_agent_id

if original_session_key:
    parts = original_session_key.split(":")
    if parts[0] == "agent" and len(parts) > 1:
        lookup_agent_id = parts[1]  # "main" - 正确的原始 agent
    else:
        lookup_agent_id = parts[0]

resolved_delivery_target = await resolve_delivery_target(
    job=job,
    cfg=config_dict,
    agent_id=lookup_agent_id,  # 查找 agent:main:... （有 Telegram info）
)
```

### 为什么这样修复

1. **Job 创建时：** `job.session_key` 被设置为创建时所在的 session，例如 `"agent:main:telegram:direct:8366053063"`

2. **Session Store 结构：** Delivery target 信息保存在 `agent:main:main` session 中（`lastChannel`, `lastChatId`）

3. **原来的错误：** 传入 `agent_id="default"` → resolver 查找 `agent:default:...` sessions → 找不到 delivery info

4. **修复后：** 从 `job.session_key` 提取 `agent_id="main"` → resolver 查找 `agent:main:main` session → ✅ 找到 Telegram delivery info
