# Cron Delivery 问题分析 - 根本原因

## 🔴 问题根源

从日志分析，cron job 成功执行并生成了新闻摘要，但**没有发送到 Telegram**。

### 关键发现

查看 `openclaw/cron/isolated_agent/run.py` 第 298-506 行：

```python
if payloads and has_helpers:
    # ... 大量 delivery 逻辑 ...
    # 包括 subagent announce flow
    # 包括 run_subagent_announce_flow
    # 这里才会真正发送消息！
else:
    # ❌ 只是设置 summary，没有任何 delivery！
    summary = result.get("summary")
    output_text = result.get("output_text") or result.get("outputText")

return _build_result(...)  # delivered=False (从未改变)
```

### 问题分析

1. **条件判断**: `if payloads and has_helpers:`
   - `payloads` 存在 ✅ (从代码看应该有)
   - `has_helpers` 存在 ✅ (helpers 模块导入应该成功)
   
2. **但是进入了哪个分支？**
   - 如果进入 `if` 分支 → 应该调用 `run_subagent_announce_flow`
   - 但日志中**完全没有** announce flow 相关日志！

3. **可能的原因**：
   - ❌ `payloads` 为空列表 `[]` (虽然不是 `None`，但 `if []` 为 False)
   - ❌ helpers 导入失败（但之前测试通过了）
   - ❌ 进入了 announce flow 但被某个条件跳过了

---

## 🔍 调试分析

### 从日志看执行流程

```
2026-03-15 13:26:00,044 | INFO | Cron job started: cron-62d5b3f8
2026-03-15 13:26:00,052 | INFO | Created pi_coding_agent.AgentSession
...
[搜索新闻]
...
2026-03-15 13:27:27,293 | INFO | agent_end has 18 messages
2026-03-15 13:27:27,293 | INFO | chunk[1] type=text text=根据搜索结果，以下是最新中文新闻的简要总结...
2026-03-15 13:27:27,352 | INFO | Cron job finished: cron-62d5b3f8, status=ok, duration=87308ms
```

**关键缺失**：
- ❌ 没有 "cron: resolved delivery target" 日志
- ❌ 没有 "cron delivery: sending to" 日志
- ❌ 没有 "run_subagent_announce_flow" 相关日志
- ❌ 没有 announce 相关的任何输出

这说明**根本没有进入 delivery 逻辑**！

---

## 🤔 可能的原因

### 原因 1: `payloads` 是空列表

从 `cron_bootstrap.py` 第 315-320 行：

```python
payloads: list[dict[str, Any]] = []
if response_text.strip():  # ← 如果 response_text 为空？
    payloads.append({
        "text": response_text,
        "role": "assistant",
    })
```

**检查点**：`response_text` 可能为空！

### 原因 2: delivery.mode 设置不正确

从 Telegram 日志看，用户说：
> "帮我设置网上找找有没新闻，每三分钟一次，连续三次"

创建任务的代码在哪里？delivery 配置是什么？

### 原因 3: announce flow 的条件不满足

从 `run.py` 第 342-348 行：

```python
if (
    delivery_requested
    and not skip_heartbeat
    and not skip_messaging_tool
    and synthesized_text
    and not delivered
):
    # 执行 announce flow
```

可能某个条件不满足：
- `delivery_requested` = False？
- `synthesized_text` = None？
- `delivered` = True？

---

## 📋 TypeScript 版本对比

需要检查的 TS 文件：
1. `src/cron/isolated-agent/run.ts` - announce flow 逻辑
2. `src/gateway/server-cron.ts` - cron bootstrap
3. `src/agents/subagent-announce/index.ts` - announce 实现

关键差异点：
- TS 版本在什么条件下触发 delivery？
- TS 版本的 `payloads` 是如何构建的？
- TS 版本的 `delivery.mode` 默认值是什么？

---

## 🔧 下一步诊断

1. **添加调试日志**：在关键分支点添加日志
2. **检查 job 配置**：查看实际创建的 cron job 的 delivery 配置
3. **检查 payloads**：确认 payloads 是否为空
4. **检查 delivery_requested**：确认是否为 True

---

## 💡 临时解决方案

如果 delivery.mode 没有正确设置，可以尝试：

```python
# 在 Telegram 中创建任务时明确指定 delivery
cron.add({
    "name": "新闻搜索",
    "schedule": {"kind": "every", "everyMs": 180000},
    "payload": {
        "kind": "agentTurn",
        "message": "搜索最新的中文新闻，简要总结3-5条重要新闻"
    },
    "delivery": {
        "mode": "announce",  # ← 明确设置！
        "channel": "last",
        "to": None
    }
})
```
