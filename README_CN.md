# OpenClaw Python

> [OpenClaw](https://github.com/badlogic/pi-mono) 的 Python 实现 — 自托管的个人 AI 助手网关

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![已测试: Telegram + Gemini](https://img.shields.io/badge/已测试-Telegram%20%2B%20Gemini-green.svg)](#状态)
[![已测试: 飞书 + Gemini](https://img.shields.io/badge/已测试-飞书%20%2B%20Gemini-green.svg)](#状态)
[![English Guide](https://img.shields.io/badge/📖_Guide-English-blue.svg)](GUIDE.md)
[![中文指南](https://img.shields.io/badge/📖_使用指南-中文-red.svg)](GUIDE_CN.md)

---

> ### 🐍 OpenClaw 虽极好，但别忘了用 Python 版！100% 对齐。

---

> 📖 **详细配置指南：** &nbsp; [English → GUIDE.md](GUIDE.md) &nbsp;·&nbsp; [中文 → GUIDE_CN.md](GUIDE_CN.md)
>
> 包含内容：安装步骤、Telegram/飞书配置、权限设置、文件发送排查、openclaw.json 完整字段说明

---

> ⚠️ **测试版本** — 本项目正在积极开发中，持续与 TypeScript 版 OpenClaw 保持对齐。可能存在 bug 和不完善之处，更新频繁。欢迎反馈问题和建议！

---

## 预览

<img src="assets/telegram-preview.jpg" alt="Jarvis on Telegram" width="260" />&nbsp;<img src="assets/IMG_1511.jpg" alt="Jarvis on Feishu" width="260" />&nbsp;<img src="assets/IMG_1544.jpg" alt="Jarvis on Feishu" width="260" />

*Jarvis 在 Telegram 和飞书上的响应展示 — 由 OpenClaw Python 驱动*

---

## 这是什么

OpenClaw 是一个自托管的 AI 网关，将你的消息渠道连接到大语言模型：

- **飞书 (Lark)** — 完整功能支持：WebSocket 实时连接、流式卡片输出、媒体消息（图片/文件/语音）、消息反应、配对机制、多账号、多维表格、知识库、文档工具
- **Telegram** — 完全可用，具备健壮的轮询机制（无冲突重启逻辑、健康监控、流式进度、队列控制）
- **其他渠道** — Discord、Slack、WhatsApp、Signal、IRC（代码已完成，运行时验证进行中）
- **Web UI** — 聊天、会话管理、配置界面，访问 `http://localhost:18789`
- **定时任务** — 自主的定时任务调度，支持灵活的时间配置
- **子代理** — 生成、注册、线程绑定、Docker 沙箱
- **权限预设** — 快速切换安全级别（宽松/受信/标准/严格）

### 🌟 支持的 LLM 提供商（25+ 个）

与 TypeScript 版本完全一致，支持多种认证方式：

- **主流 AI：** Anthropic (Claude)、OpenAI、Google Gemini、xAI (Grok)、Mistral AI
- **国内服务：** Kimi Coding (k2p5)、MiniMax、Moonshot、智谱 AI (GLM)、通义千问、千帆（百度）、火山引擎、BytePlus、小米
- **聚合器和代理：** OpenRouter、LiteLLM、Kilo Gateway、Vercel AI Gateway、Cloudflare AI Gateway、OpenCode Zen、Synthetic
- **自托管和开源：** vLLM、Together AI、Hugging Face、Venice AI
- **自定义提供商：** 任何兼容 OpenAI/Anthropic 的端点

**认证方式：** API Keys（完全支持）、OAuth/Portal 流程（部分支持）

**流式输出：** 完整的 Anthropic Messages API 流式支持，正确处理事件

📖 **详见 [提供商配置指南](docs/providers.md)**

---

## 快速开始

### 前置要求

- **Python 3.11+** — 检查版本：`python3 --version`
- **[uv](https://docs.astral.sh/uv/)** — 快速的 Python 包管理器
- **LLM API 密钥** — 推荐使用 Gemini、Claude 或 OpenAI

如果没有安装 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 安装步骤

克隆两个仓库作为兄弟目录（必需的结构）：

```bash
# 创建工作区文件夹（名称任意）
mkdir my-workspace && cd my-workspace

# 克隆两个仓库
git clone https://github.com/openxjarvis/pi-mono-python.git
git clone https://github.com/openxjarvis/openclaw-python.git

# 安装依赖
cd openclaw-python
uv sync
```

你的文件夹结构应该是：

```
my-workspace/
├── openclaw-python/       ← 主应用程序
└── pi-mono-python/        ← 必需的依赖（代理核心）
```

### 首次设置

运行交互式配置向导：

```bash
uv run openclaw onboard
```

向导会引导你完成：

1. **LLM 提供商** — 选择 Gemini、Claude、OpenAI 等
2. **API 密钥** — 输入你的 API 密钥（保存到 `.env`）
3. **渠道设置** — 配置 Telegram、飞书，或跳过
4. **网关端口** — 默认是 18789
5. **工作区** — 代理的工作目录

### 启动网关

```bash
uv run openclaw start
```

OpenClaw 现在正在运行：

- **Web UI：** http://localhost:18789
- **Telegram：** 向你的 bot 发送消息
- **飞书：** 直接向你的飞书 bot 发送消息

### 更新到最新版本

```bash
cd openclaw-python
git pull && uv sync

cd ../pi-mono-python
git pull && uv sync

# 重启网关
uv run openclaw restart
```

---

## 依赖项：pi-mono-python

`openclaw-python` 依赖于 **[pi-mono-python](https://github.com/openxjarvis/pi-mono-python)** — 一个配套仓库，提供核心代理和 LLM 基础设施作为本地包：

| 包 | 提供的功能 |
|---|---|
| `pi-ai` | 统一的 LLM 流式层（Gemini、Anthropic、OpenAI 等） |
| `pi-agent` | 代理循环、工具执行、会话状态 |
| `pi-coding-agent` | 编码代理，包含文件/bash/搜索工具 |
| `pi-tui` | 终端 UI 渲染引擎 |

两个仓库必须克隆为兄弟目录（父目录名称任意）：

```
my-workspace/
├── openclaw-python/       ← 本仓库
└── pi-mono-python/        ← 必需的兄弟仓库
```

---

## 飞书（Lark）— 完整功能支持

飞书是目前功能最完整的渠道，支持所有功能：

| 功能 | 状态 |
|---------|--------|
| WebSocket 长连接 | ✅ |
| 流式卡片输出（实时流式卡片） | ✅ |
| 图片/文件/语音消息 | ✅ |
| 消息反应（reaction ACK） | ✅ |
| 配对/白名单/私聊策略 | ✅ |
| 多账号支持 | ✅ |
| 多维表格（Bitable）工具 | ✅ |
| 知识库/文档读写 | ✅ |
| @提及/群聊 | ✅ |

---

## Telegram — 已优化

- 无冲突轮询（修复了双重启动导致的 409 循环 bug）
- PTB 内部重试循环自动处理短暂冲突
- 健康监控，每 60 秒进行 `get_me()` 检查
- 更新偏移量在重启之间持久化
- 所有更新类型的去重
- **流式进度** — 私聊显示推理步骤；群聊显示实时预览气泡
- **队列控制** — `/stop` 中止、`/queue` 改变行为（中断/引导/跟进/收集）
- **3 分钟自动超时** — 防止卡住

---

## 配置

### 快速配置

最简单的配置方式是通过交互式向导：

```bash
uv run openclaw onboard
```

### 手动配置

编辑 `~/.openclaw/openclaw.json` 进行高级设置：

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "botToken": "YOUR_BOT_TOKEN",
      "dmPolicy": "pairing"
    },
    "feishu": {
      "appId": "YOUR_APP_ID",
      "appSecret": "YOUR_APP_SECRET",
      "useWebSocket": true
    }
  },
  "tools": {
    "exec": {
      "security": "full",
      "ask": "on-miss"
    }
  }
}
```

### 常用 CLI 命令

```bash
# 网关管理
uv run openclaw start         # 启动网关
uv run openclaw stop          # 停止网关
uv run openclaw restart       # 重启网关
uv run openclaw status        # 检查网关状态

# 用户管理（配对模式）
uv run openclaw pairing list              # 列出配对请求
uv run openclaw pairing approve <user_id> # 批准用户
uv run openclaw pairing deny <user_id>    # 拒绝用户

# 安全预设
uv run openclaw security preset           # 查看/切换权限级别
uv run openclaw security preset trusted   # 设置为受信模式

# 配置
uv run openclaw onboard                   # 再次运行设置向导
uv run openclaw config show               # 显示当前配置
```

完整的 CLI 参考和详细配置，请参阅 **[GUIDE_CN.md](GUIDE_CN.md)**。

---

## 权限和故障排查

> **如果代理说它"不能做"某事，原因通常是权限配置问题 — 而不是代码 bug。**

OpenClaw 有几个独立的权限层。在调试代码之前，请检查这些：

### 1. 渠道访问 — 谁可以与 bot 对话

控制谁可以向你的 bot 发送私聊消息。在 `~/.openclaw/openclaw.json` 中按渠道配置：

| 策略 | 行为 | 使用场景 |
|---|---|---|
| `pairing`（默认） | 新用户必须请求访问并通过 CLI 批准 | 推荐用于个人 bot |
| `allowlist` | 仅预先批准的用户可以交互 | 团队/组使用 |
| `open` | 任何用户都可以立即交互 | 公共 bot（谨慎使用） |
| `disabled` | 不允许私聊访问 | 仅限渠道模式 |

**示例：**

```json
{ "channels": { "telegram": { "dmPolicy": "pairing" } } }
```

**在配对模式下批准用户：**

```bash
# 列出待处理的请求
uv run openclaw pairing list

# 批准用户
uv run openclaw pairing approve <user_id>
```

### 2. Bash 执行 — 代理可以运行哪些 shell 命令

控制代理是否可以执行 shell 命令。在 `~/.openclaw/openclaw.json` 中配置：

| 设置 | 效果 | 示例命令 |
|---------|--------|------------------|
| `deny` | **不能运行任何 shell 命令** | 代理只能读写文件 |
| `allowlist` | 仅允许白名单中的二进制文件 | `python`、`git`、`ffmpeg` 等 |
| `full` | 可以运行任何命令 | 推荐用于受信环境 |

**示例配置：**

```json
{
  "tools": {
    "exec": {
      "security": "full",
      "ask": "on-miss",
      "safe_bins": ["python", "ffmpeg", "git", "node", "npm"]
    }
  }
}
```

> **注意：** `exec.security` 设置仅影响 `bash` 工具。无论此设置如何，文件读写操作始终可用。

**快速预设切换：**

```bash
# 查看当前权限级别
uv run openclaw security preset

# 切换到受信模式（推荐个人使用）
uv run openclaw security preset trusted
```

可用预设：`relaxed`（宽松）· `trusted`（受信）· `standard`（标准）· `strict`（严格）

### 3. 飞书应用权限 — 哪些飞书 API 功能可用

如果飞书工具失败并显示"拒绝访问"或"需要权限"，你需要在[飞书开发者控制台](https://open.feishu.cn/)中启用该权限：

| 权限 | 用于 |
|-------|-------------|
| `im:message`、`im:message:send_as_bot` | 基本消息（必需） |
| `im:message.reaction:write` | 输入指示器反应 |
| `task:task:write` | 创建/更新任务 |
| `calendar:calendar.event:write` | 创建日历事件 |
| `bitable:app` | 多维表格工具 |
| `docx:document`、`wiki:wiki` | 文档/知识库读写 |
| `drive:drive` | 云盘文件访问 |

启用新权限后，必须在飞书控制台中**发布新的应用版本**才能使更改生效。

### 常见权限问题

| 症状 | 可能原因 | 修复方法 |
|---------|-------------|-----|
| 代理说"我不能运行命令" | `exec.security: deny` | 设置为 `allowlist` 或 `full` |
| 代理无法用脚本生成文件 | `exec.security: deny` 阻止 bash | 代理仍可使用 `write_file` 写文本；启用 bash 用于脚本 |
| 飞书任务/日历工具失败 | 缺少 API 权限 | 在飞书控制台启用权限并重新发布 |
| Bot 不响应新用户 | DM 策略是 `pairing` | 通过 `uv run openclaw pairing approve` 批准或设置 `dmPolicy: open` |
| 代理可以写文件但不能运行 Python | `exec.security: allowlist` 缺少 `python` | 将 `python` 添加到 `safe_bins` |
| Bot 在复杂任务后卡住/无响应 | 代理运行循环或超时 | 发送 `/stop`（3 分钟后自动超时） |

**快速修复：** 运行 `uv run openclaw security preset` 查看当前权限级别并切换到预设（推荐个人使用 Trusted）。

详见 **[GUIDE_CN.md](GUIDE_CN.md)** 获取完整配置参考。

---

## 状态

持续与 TypeScript 版 [OpenClaw](https://github.com/badlogic/pi-mono) 保持对齐。

### 渠道

| 渠道 | 状态 | 备注 |
|---------|--------|-------|
| **Telegram** | ✅ 生产就绪 | 完全测试和可用 |
| **飞书 (Lark)** | ✅ 生产就绪 | 完整功能支持 |
| **Ollama（本地模型）** | ✅ 生产就绪 | 本地测试通过 |
| Discord / Slack / WhatsApp / Signal / IRC | 🔧 运行时验证中 | 代码已完成 |

### AI 提供商

| 提供商 | 状态 | 模型/说明 |
|----------|--------|--------|
| **Google Gemini** | ✅ 生产环境 | Gemini 2.5 Pro、Gemini 2.0 Flash、Gemini 1.5 Pro/Flash |
| **Anthropic Claude** | ✅ 生产环境 | Claude 3.5 Sonnet、Claude 3.5 Haiku、Claude 3 Opus |
| **OpenAI** | ✅ 生产环境 | GPT-4o、o1、o3-mini |
| **DeepSeek** | ✅ 生产环境 | DeepSeek-V3、DeepSeek-R1 |
| **Ollama (本地)** | ✅ 生产环境 | Llama 3.3、Mistral、Qwen、CodeLlama |
| **AWS Bedrock** | ✅ 生产环境 | Claude 3.x、Llama 3.3、Mistral |
| **Mistral AI** | ✅ 已实现 | Mistral Large、Mistral Medium |
| **xAI (Grok)** | ✅ 已实现 | Grok 系列模型 |
| **Kimi (月之暗面)** | ✅ 已实现 | Kimi Coding (k2p5) |
| **MiniMax** | ✅ 已实现 | MiniMax 系列 |
| **Moonshot (月之暗面)** | ✅ 已实现 | Moonshot 系列 |
| **智谱 AI (GLM)** | ✅ 已实现 | GLM-4、ChatGLM 系列 |
| **通义千问 (Qwen)** | ✅ 已实现 | Qwen 系列模型 |
| **千帆 (百度)** | ✅ 已实现 | 文心一言系列 |
| **火山引擎 (Volcengine)** | ✅ 已实现 | 豆包系列 |
| **BytePlus** | ✅ 已实现 | 字节跳动海外版 |
| **小米 AI** | ✅ 已实现 | 小米大模型 |
| **OpenRouter** | ✅ 已实现 | 聚合多个 AI 提供商 |
| **LiteLLM** | ✅ 已实现 | 统一接口代理 |
| **Together AI** | ✅ 已实现 | 开源模型托管 |
| **Hugging Face** | ✅ 已实现 | Inference API |
| **vLLM** | ✅ 已实现 | 自托管推理引擎 |
| **Venice AI** | ✅ 已实现 | 隐私优先的 AI 平台 |
| **Kilo Gateway** | ✅ 已实现 | API 网关 |
| **Vercel AI Gateway** | ✅ 已实现 | Vercel AI SDK |
| **Cloudflare AI Gateway** | ✅ 已实现 | Cloudflare Workers AI |
| **OpenCode Zen** | ✅ 已实现 | 编码专用 |
| **Synthetic** | ✅ 已实现 | 合成数据生成 |
| **自定义端点** | ✅ 已实现 | 任何兼容 OpenAI/Anthropic 的端点 |

### 核心基础设施

| 组件 | 状态 |
|-----------|--------|
| 网关服务器 + Web UI | ✅ 生产环境 |
| 会话管理 | ✅ 生产环境 |
| 工具系统 | ✅ 生产环境 |
| 技能系统 | ✅ 生产环境 |
| 定时任务调度器 | ✅ 生产环境 |
| 子代理（生成、注册） | ✅ 生产环境 |
| Docker 沙箱 | ✅ 已实现 |
| 上下文压缩 | ✅ 生产环境 |

---

## 开发

```bash
# 运行测试
uv run pytest

# 代码检查
uv run ruff check .
uv run ruff format .

# 构建 Web UI（如果修改前端）
cd openclaw/web/ui-src
npm install && npm run build
```

---

## 相关项目

- **OpenClaw TypeScript** — [github.com/badlogic/pi-mono](https://github.com/badlogic/pi-mono) — 上游参考实现
- **pi-mono-python** — [github.com/openxjarvis/pi-mono-python](https://github.com/openxjarvis/pi-mono-python) — 核心代理基础设施

---

## 许可证

MIT — 详见 [LICENSE](LICENSE)。
