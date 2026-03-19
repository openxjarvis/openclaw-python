# TypeScript vs Python Delivery Resolution 对比

## 1. 函数签名对比

### TypeScript
```typescript
export async function resolveDeliveryTarget(
  cfg: OpenClawConfig,
  agentId: string,
  jobPayload: {
    channel?: string | "last";
    to?: string;
    accountId?: string;
    sessionKey?: string;
  },
): Promise<DeliveryTargetResult>
```

位置: `openclaw/src/cron/isolated-agent/delivery-target.ts:22-201`

### Python
```python
async def resolve_delivery_target(
    job: "CronJob",
    session_history: list[dict[str, Any]] | None = None,
    *,
    cfg: Any = None,
    agent_id: str | None = None,
) -> DeliveryTarget
```

位置: `openclaw-python/openclaw/cron/isolated_agent/delivery.py:65-196`

**差异**: 
- TS 接收 `jobPayload` (字典)
- Python 接收完整的 `CronJob` 对象

## 2. 解析流程对比

### TypeScript 流程 (完整)

```
1. Load session store
   ↓
2. Find main session (mainSessionKey = "agent:${agentId}:main")
   ↓
3. Find thread session (if jobPayload.sessionKey exists)
   ↓
4. resolveSessionDeliveryTarget(entry, requestedChannel, explicitTo)
   ↓
5. If no channel found:
   a. Try fallback to entry.lastChannel
   b. Try resolveMessageChannelSelection(cfg)
   c. Set channelResolutionError if fails
   ↓
6. Retry resolveSessionDeliveryTarget with fallbackChannel
   ↓
7. Resolve accountId (priority):
   a. jobPayload.accountId (explicit)
   b. resolved.accountId (from session)
   c. buildChannelAccountBindings(cfg) (from config)
   ↓
8. Resolve threadId (conditional):
   - Keep if explicitly set OR same recipient as session's lastTo
   ↓
9. WhatsApp-specific: Resolve allowFrom
   ↓
10. Dock target via resolveOutboundTarget(channel, to, cfg, accountId, mode)
    ↓
11. Return {ok, channel, to, accountId, threadId, mode, error?}
```

### Python 流程 (简化)

```
1. Check if job.delivery exists (if not, return default channel)
   ↓
2. Extract requested_channel (delivery.channel or "last")
   ↓
3. Extract explicit_to (delivery.to)
   ↓
4. If explicit channel + explicit to → return immediately (explicit mode)
   ↓
5. Load session store (try to find session entry)
   ↓
6. If session entry found:
   - Resolve from session entry (channel, to, threadId)
   ↓
7. Fallback to session_history (legacy support)
   ↓
8. Fallback to config-driven channel selection
   ↓
9. Return DeliveryTarget (channel, to, account_id, thread_id, mode)
```

## 3. 关键差异

### 差异 1: Session Store 查询策略

#### TypeScript
```typescript
// 总是尝试加载 store
const store = sessionStorePath ? await loadSessionStore(sessionStorePath) : {};

// 查找 main session
const mainSessionKey = `agent:${normalizeAgentId(agentId)}:main`;

// 查找 thread session (优先级更高)
const threadSessionKey = jobPayload.sessionKey?.trim();
const threadEntry = threadSessionKey ? store[threadSessionKey] : undefined;
const main = threadEntry ?? store[mainSessionKey];

// 使用找到的 entry (thread 或 main)
const preliminary = resolveSessionDeliveryTarget({entry: main, ...});
```

**特点**:
- ✅ 先尝试 thread session，然后 fallback 到 main session
- ✅ 即使 store 为空，继续执行（不报错）

#### Python
```python
# 只在有 cfg 时才查询
if cfg is not None:
    try:
        store_entry = _load_session_entry(cfg, agent_id, thread_session_key)
    except Exception as exc:
        logger.debug("cron delivery: session store lookup failed: %s", exc)

if store_entry:
    resolved = _resolve_from_session_entry(...)
    if resolved.channel and (resolved.to or requested_channel == "last"):
        return resolved
```

**特点**:
- ✅ 异常不会中断流程
- ✅ 查询失败后继续执行 fallback

### 差异 2: Fallback 链完整性

#### TypeScript Fallback Chain
```typescript
1. Session entry (thread or main)
   ↓ (失败)
2. entry.lastChannel (from session)
   ↓ (失败)
3. resolveMessageChannelSelection(cfg)
   ↓ (失败)
4. channelResolutionError (但继续执行)
   ↓
5. 最终返回 {ok: false, error: ...} (但有 channel 信息)
```

#### Python Fallback Chain
```python
1. Explicit channel + to
   ↓ (失败)
2. Session store entry
   ↓ (失败)
3. Session history (legacy)
   ↓ (失败)
4. Config-driven channel selection
   ↓ (失败)
5. DEFAULT_CHAT_CHANNEL ("telegram")
```

**Python 更强**: 即使所有查询失败，也会返回 `DEFAULT_CHAT_CHANNEL`。

### 差异 3: 错误处理

#### TypeScript
```typescript
if (!channel) {
  return {
    ok: false,
    channel: undefined,
    to: undefined,
    accountId,
    threadId,
    mode,
    error: channelResolutionError ?? new Error("Channel is required..."),
  };
}
```

**特点**: 明确的 `ok: false` 返回，但包含详细的错误信息。

#### Python
```python
if not target.to:
    if target.error:
        msg = str(target.error)
    else:
        msg = "could not resolve delivery target"
    
    if getattr(delivery_obj, "best_effort", False):
        logger.warning("cron delivery: %s (best effort)", msg)
        return True  # ← 注意：即使失败也返回 True (best_effort 模式)
    
    logger.error("cron delivery: %s", msg)
    return False
```

**特点**: 
- ✅ 支持 `best_effort` 模式
- ❌ 但返回值是 `bool`，不包含详细的错误信息

### 差异 4: Account ID Resolution

#### TypeScript (复杂且完整)
```typescript
// 1. Explicit from jobPayload
const explicitAccountId = jobPayload.accountId?.trim() || undefined;

// 2. From session entry
let accountId = explicitAccountId ?? resolved.accountId;

// 3. From channel bindings config
if (!accountId && channel) {
  const bindings = buildChannelAccountBindings(cfg);
  const byAgent = bindings.get(channel);
  const boundAccounts = byAgent?.get(normalizeAgentId(agentId));
  if (boundAccounts && boundAccounts.length > 0) {
    accountId = boundAccounts[0];
  }
}

// 4. Final override from jobPayload (highest priority)
if (jobPayload.accountId) {
  accountId = jobPayload.accountId;
}
```

#### Python (简化)
```python
# 1. From delivery config (explicit)
explicit_account_id = getattr(delivery, "account_id", None) or None

if explicit_account_id:
    resolved.account_id = explicit_account_id
else:
    _try_resolve_account_id(resolved, cfg=cfg, agent_id=agent_id)

# _try_resolve_account_id 内部：
# 2. From channel bindings
bindings = build_channel_account_bindings(cfg)
by_agent = bindings.get(target.channel)
if by_agent:
    bound = by_agent.get(normalize_agent_id(agent_id))
    if bound and len(bound) > 0:
        target.account_id = bound[0]
```

**差异**: TS 有两次 jobPayload 检查（第 4 步是最终 override），Python 只检查一次。

### 差异 5: Thread ID Logic

#### TypeScript
```typescript
// Carry threadId when:
// 1. Explicitly set (from :topic: parsing or config)
// 2. OR delivering to same recipient as session's lastTo
const threadId =
  resolved.threadId &&
  (resolved.threadIdExplicit || (resolved.to && resolved.to === resolved.lastTo))
    ? resolved.threadId
    : undefined;
```

**智能判断**: 只有在显式设置或目标匹配时才保留 thread_id。

#### Python
```python
# Only carry thread_id when explicitly set or delivering to same recipient
carry_thread = thread_id is not None and (
    entry.get("lastThreadIdExplicit")
    or (to is not None and to == last_to)
)

return DeliveryTarget(
    channel=channel or DEFAULT_CHAT_CHANNEL,
    to=to,
    thread_id=thread_id if carry_thread else None,
    mode=mode,
)
```

**完全一致**: Python 实现了相同的逻辑。

### 差异 6: Outbound Target Docking

#### TypeScript
```typescript
const docked = resolveOutboundTarget({
  channel,
  to: toCandidate,
  cfg,
  accountId,
  mode,
  allowFrom: allowFromOverride,
});

if (!docked.ok) {
  return {
    ok: false,
    channel,
    to: undefined,  // ← 清空 to
    accountId,
    threadId,
    mode,
    error: docked.error,
  };
}

return {
  ok: true,
  channel,
  to: docked.to,  // ← 使用 docked 的 to
  accountId,
  threadId,
  mode,
};
```

**验证 + 规范化**: Docking 会验证 target 的有效性并规范化格式。

#### Python
```python
def _try_validate_outbound(target: DeliveryTarget, cfg: Any) -> None:
    if not target.to or not cfg:
        return
    try:
        from openclaw.infra.outbound.targets import resolve_outbound_target
        result = resolve_outbound_target(
            channel=target.channel,
            to=target.to,
            cfg=cfg,
            account_id=target.account_id,
            mode=target.mode,
        )
        if not result.get("ok"):
            err_msg = result.get("error") or "outbound target validation failed"
            target.error = ValueError(err_msg)
            target.to = None  # ← 清空无效的 to
        else:
            target.to = result.get("to") or target.to  # ← 更新 to
    except Exception:
        pass
```

**完全一致**: Python 也实现了 docking 验证。

## 4. cron_bootstrap.py 中的简化实现问题

### 问题代码
```python
# cron_bootstrap.py:322-348
resolved_delivery: dict[str, Any] = {}
try:
    delivery_config = getattr(job, "delivery", None)
    if delivery_config and getattr(delivery_config, "mode", "none") != "none":
        channel_mode = getattr(delivery_config, "channel", "last")
        if channel_mode == "last":
            # ❌ 直接调用 _extract_delivery_targets（简化版）
            running_channels = cm.list_running() if hasattr(cm, "list_running") else []
            all_keys = _list_all_session_keys(cm)
            agent_part = _extract_agent_part(job_session_key)
            targets = _extract_delivery_targets(all_keys, agent_part, running_channels)
            
            if targets:
                # 只有找到 targets 才设置
                channel_id, chat_id, thread_id = targets[0]
                resolved_delivery = {
                    "channel": channel_id,
                    "to": chat_id,
                }
                if thread_id is not None:
                    resolved_delivery["threadId"] = thread_id
            # ❌ 如果 targets 为空，resolved_delivery 保持为 {}
except Exception as e:
    logger.warning(f"cron: delivery resolution error: {e}", exc_info=True)
```

### 问题分析

**对比 TS**: TS 版本即使所有查询失败，也会：
1. 尝试多个 fallback
2. 调用 `resolveMessageChannelSelection(cfg)`
3. 返回明确的错误信息

**对比 Python `resolve_delivery_target()`**: 完整版有：
1. Session store fallback
2. Session history fallback
3. Config-driven channel selection
4. DEFAULT_CHAT_CHANNEL 兜底

**`cron_bootstrap.py` 的问题**:
- ❌ 只调用 `_extract_delivery_targets()`（无 fallback）
- ❌ 失败时 `resolved_delivery = {}`
- ❌ 传给 `subagent_announce` 的 `requester_origin={}` 导致 delivery 失败

## 5. 修复方案

### 正确的实现

```python
# cron_bootstrap.py:322-348 (修复后)
resolved_delivery: dict[str, Any] = {}
try:
    from openclaw.cron.isolated_agent.delivery import resolve_delivery_target
    
    # ✅ 使用完整的 delivery resolver
    resolved_delivery_target = await resolve_delivery_target(
        job=job,
        session_history=None,
        cfg=config_dict,
        agent_id=job_agent_id,
    )
    
    # ✅ 即使没有 to，也会有 channel (fallback 到 DEFAULT_CHAT_CHANNEL)
    resolved_delivery = {
        "channel": resolved_delivery_target.channel,
        "to": resolved_delivery_target.to,
    }
    
    if resolved_delivery_target.account_id:
        resolved_delivery["accountId"] = resolved_delivery_target.account_id
    if resolved_delivery_target.thread_id:
        resolved_delivery["threadId"] = resolved_delivery_target.thread_id
        
    logger.info(f"cron: resolved delivery: {resolved_delivery}")
    
except Exception as e:
    logger.warning(f"cron: delivery resolution error: {e}", exc_info=True)
    # ✅ Fallback: 至少设置 DEFAULT_CHAT_CHANNEL
    from openclaw.cron.isolated_agent.delivery import DEFAULT_CHAT_CHANNEL
    resolved_delivery = {"channel": DEFAULT_CHAT_CHANNEL, "to": None}
```

## 6. 总结对比表

| 特性 | TypeScript | Python (delivery.py) | Python (cron_bootstrap.py) |
|------|------------|----------------------|----------------------------|
| Session store 查询 | ✅ Thread + Main fallback | ✅ Thread + Main fallback | ✅ Main only (硬编码) |
| Fallback 链 | ✅ 3-step fallback | ✅ 4-step fallback | ❌ 无 fallback |
| Config-driven selection | ✅ 有 | ✅ 有 | ❌ 无 |
| DEFAULT_CHAT_CHANNEL | ❌ 无（返回 error） | ✅ 有 | ❌ 无 |
| Account ID resolution | ✅ 3-source priority | ✅ 2-source priority | ❌ 不支持 |
| Thread ID logic | ✅ 智能判断 | ✅ 智能判断 | ✅ 基础支持 |
| Outbound docking | ✅ 完整验证 | ✅ 完整验证 | ❌ 无验证 |
| Error handling | ✅ 详细 error object | ✅ 基础 error | ❌ 静默失败 |
| Best-effort mode | ❌ 无 | ✅ 有 | ❌ 无 |

## 7. 推荐修复

**在 `cron_bootstrap.py:322-348` 中**:
- ❌ 移除 `_extract_delivery_targets()` 调用
- ✅ 使用 `resolve_delivery_target()`（完整版）
- ✅ 与 TS 版本和 Python delivery.py 保持一致

**修改位置**: `openclaw/gateway/cron_bootstrap.py:322-348`

**预期结果**:
- ✅ 即使 session store 为空，也能通过 fallback 找到 delivery target
- ✅ 新闻消息能正常发送到 Telegram
- ✅ 与 TS 版本行为一致
