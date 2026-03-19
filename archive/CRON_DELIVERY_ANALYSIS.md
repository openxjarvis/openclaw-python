# Cron Job问题分析报告

## 问题1: Telegram收不到消息

### 错误日志
```
2026-03-15 12:40:48,317 | ERROR | openclaw.gateway.cron_bootstrap | Cron job error cron-1813cdf8: 'NoneType' object has no attribute 'get'
```

### 根本原因

从日志分析，问题出在 `run.py` 的第 **446** 行：

```python
requester_origin=result.get("resolved_delivery") or {},
```

而 `result` 在某些情况下为 `None`，导致 `NoneType` 错误。

### 问题链路

1. **cron_bootstrap.py** (line 368-375) 调用 `run_cron_isolated_agent_turn`
2. **run.py** (line 229) 执行 `run_agent_fn(job=job, message=message)`
3. **run.py** (line 330-348) 尝试解析 `resolved_delivery`
4. **run.py** (line 446) **崩溃点**：`result.get("resolved_delivery")` 当 `result` 为 None

### 具体bug位置

**文件**: `openclaw/cron/isolated_agent/run.py`  
**行号**: Line 434-446

```python
if config is not None and agent_id:
    resolved_delivery_for_ann = result.get("resolved_delivery") or {}  # ← result可能为None
    ann_session_key = await resolve_cron_announce_session_key(
        config=config,
        agent_id=agent_id,
        fallback_session_key=fallback,
        delivery=resolved_delivery_for_ann,  # ← 传入空dict
    )

did_announce = await run_subagent_announce_flow(
    child_session_key=effective_session_key,
    child_run_id=f"{getattr(job, 'id', '?')}:{run_session_id}",
    requester_session_key=ann_session_key,
    requester_origin=result.get("resolved_delivery") or {},  # ← 再次访问result.get，崩溃
    # ...
)
```

### 修复方案

需要确保 `result` 永远不为 None，或者在访问前进行防御性检查。

**方案1**: 在 `run_agent_fn` 中保证返回值

**方案2**: 在 `run.py` 中添加防御性检查：

```python
# Line 330之前添加
if not isinstance(result, dict):
    logger.error("cron: run_agent_fn returned non-dict result: %s", type(result))
    return {
        "status": "error",
        "error": "invalid result from run_agent_fn",
        "delivered": False,
        "session_key": effective_session_key,
    }
```

---

## 问题2: WebUI时区显示不正确

### 现象

WebUI展示的cron任务时间与实际时间不符。

### 可能原因

1. **UTC vs 本地时间混淆**
   - 后端存储UTC时间
   - 前端显示时未转换为本地时区

2. **时间戳格式问题**
   - Python使用毫秒时间戳
   - JavaScript期望的时间戳单位不匹配

3. **Cron执行时间计算错误**
   - 从日志看：任务设置在 12:36, 12:39, 12:42...
   - WebUI可能显示不同的时间

### 需要检查的文件

1. `openclaw/cron/service.py` - Cron任务调度逻辑
2. `openclaw/gateway/handlers.py` - WebUI API响应
3. 前端时间渲染逻辑

### 检查点

```python
# 检查时间戳是否正确传递
job.state.next_run  # 是否为UTC毫秒时间戳？
job.state.last_run  # 是否正确记录？
```

---

## 修复优先级

1. **P0 (立即)**: 修复 `NoneType` 错误 - 阻止消息发送
2. **P1 (高)**: 修复时区显示 - 影响用户体验

---

## 临时解决方案

### 对于消息发送问题

用户可以：
1. 使用 `message` tool 主动推送（agent在prompt中明确指定）
2. 设置 `delivery.best_effort = true` 跳过错误

### 对于时区问题

用户需要：
1. 手动计算时区偏移
2. 或使用命令行 `openclaw cron list` 查看正确时间

---

## 下一步行动

1. [ ] 修复 `run.py` 的 None 检查
2. [ ] 测试修复后的消息发送
3. [ ] 调查前端时区转换逻辑
4. [ ] 统一后端时间戳格式
