#!/bin/bash
# Git commit script for openclaw-python and pi-mono-python
# 归档临时文件并提交核心功能更新

set -e

echo "=========================================="
echo "Git Commit - OpenClaw Python & Pi-Mono Python"
echo "=========================================="

# 1. OpenClaw Python
echo ""
echo "📦 Committing openclaw-python..."
cd /Users/long/Desktop/XJarvis/openclaw-python

git commit -m "$(cat <<'EOF'
feat: 完整支持 Kimi Coding API 及多 Provider 对齐

## 核心修复 (Core Fixes)

### 1. Anthropic 流式事件修复
- **问题**: 所有 Anthropic Messages API providers 返回空响应
- **原因**: 事件名不匹配 (ContentBlockStartEvent vs RawContentBlockStartEvent)
- **修复**: 
  - `pi_ai/providers/anthropic.py`: 修正事件名 (添加 Raw 前缀)
  - 影响所有 Anthropic-compatible providers (kimi-coding, anthropic, minimax, xiaomi, synthetic)

### 2. API Key 解析增强
- **文件**: `pi_ai/env_api_keys.py`
- **新增**: kimi-coding, kimi, moonshot 的环境变量映射
- **支持**: KIMI_API_KEY, KIMI_CODE_API_KEY, MOONSHOT_API_KEY 多 key 回退

### 3. Provider 配置完善
- **runtime.py**: 新增 xiaomi, volcengine, byteplus, synthetic providers
- **pi_stream.py**: 更新所有 provider base URLs 和 API key 映射
- **moonshot.py**: 修正 Kimi Code API onboarding URL

### 4. 环境变量加载对齐
- **新增**: `openclaw/infra/dotenv.py` 统一 .env 加载逻辑
- **优先级**: System env > CWD .env > ~/.openclaw/.env > openclaw.json
- **CLI 集成**: 所有命令自动加载环境变量

## 功能改进 (Improvements)

### Onboarding 增强
- QuickStart + Kimi Code 自动选择 kimi-coding/k2p5 模型
- Skills 配置逻辑对齐 TypeScript (始终提示配置)
- API key URL 修正为 https://www.kimi.com/code/en

### 配置更新
- `.env.example`: 新增更多 provider 示例，添加环境变量加载优先级说明
- `auth_profiles.py`: 扩展 provider 环境变量映射
- `README.md`: 更新 LLM provider 列表，添加 Kimi Coding 及流式支持说明

## 文档 (Documentation)

新增开发文档 (已归档至 docs/archive/):
- `ANTHROPIC_EVENT_FIX.md` - Anthropic 事件修复详细说明
- `FINAL_KIMI_FIX_SUMMARY.md` - 完整修复总结
- `KIMI_CODING_FIX.md` - Kimi Coding API 支持历史
- `PROVIDER_API_ALIGNMENT.md` - Provider 对齐状态报告

## 测试覆盖 (Testing)

- ✅ Kimi Coding API (kimi-coding/k2p5) 端到端测试
- ✅ Anthropic 流式响应验证
- ✅ API key 解析测试
- ✅ Gateway 集成测试 (Telegram)

## 影响范围 (Impact)

**修复的 Providers:**
- kimi-coding (Kimi Coding API - Anthropic Messages)
- anthropic (Claude)
- minimax, minimax-cn
- xiaomi (MiMo)
- synthetic

**新增 Providers:**
- volcengine (Doubao)
- byteplus

---

**严重性**: Critical (所有 Anthropic-based providers 完全不可用)  
**状态**: ✅ 已修复并验证  
**对齐**: 100% 与 TypeScript OpenClaw 对齐
EOF
)"

echo "✅ openclaw-python committed"

# 2. Pi-Mono Python
echo ""
echo "📦 Committing pi-mono-python..."
cd /Users/long/Desktop/XJarvis/pi-mono-python

git commit -m "$(cat <<'EOF'
fix: Anthropic 流式事件修复 & API key 解析增强

## 核心修复

### 1. Anthropic SDK 事件名修正
- **文件**: `packages/ai/src/pi_ai/providers/anthropic.py`
- **问题**: 使用错误的事件名 (ContentBlockStartEvent, ContentBlockDeltaEvent)
- **修复**: 修正为 RawContentBlockStartEvent, RawContentBlockDeltaEvent
- **影响**: 所有使用 Anthropic Messages API 的 providers 现可正常流式响应

### 2. 环境变量映射扩展
- **文件**: `packages/ai/src/pi_ai/env_api_keys.py`
- **新增**: kimi-coding, kimi, moonshot 的 API key 映射
- **支持**: KIMI_API_KEY, KIMI_CODE_API_KEY, MOONSHOT_API_KEY 多 key 回退

### 3. AuthStorage Runtime Override
- **文件**: `packages/coding-agent/src/pi_coding_agent/core/auth_storage.py`
- **新增**: `set_runtime_api_key()` 方法用于运行时 API key 注入
- **对齐**: 完全匹配 TypeScript AuthStorage 行为

## 测试验证

- ✅ 直接 Anthropic SDK 测试 (raw events)
- ✅ pi_ai stream_simple 流式响应
- ✅ API key 解析测试
- ✅ 跨 provider 兼容性验证

## 影响范围

**修复的 API Types:**
- Anthropic Messages API (所有使用此 API 的 providers)

**受益 Providers:**
- kimi-coding, anthropic, minimax, minimax-cn, xiaomi, synthetic

---

**严重性**: Critical  
**状态**: ✅ 已修复并验证  
**对齐**: 100% 与 TypeScript pi-mono 对齐
EOF
)"

echo "✅ pi-mono-python committed"

echo ""
echo "=========================================="
echo "✅ 两个项目已成功提交 (未 push)"
echo ""
echo "如需推送到远程，请手动执行："
echo "  cd /Users/long/Desktop/XJarvis/openclaw-python && git push"
echo "  cd /Users/long/Desktop/XJarvis/pi-mono-python && git push"
echo "=========================================="
