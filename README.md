# OpenClaw Python

> Python implementation of [OpenClaw](https://github.com/badlogic/pi-mono) — a self-hosted personal AI assistant gateway.
>
> **[中文 README →](README_CN.md)**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tested: Telegram + Gemini](https://img.shields.io/badge/Tested-Telegram%20%2B%20Gemini-green.svg)](#status)
[![Tested: Feishu + Gemini](https://img.shields.io/badge/Tested-Feishu%20%2B%20Gemini-green.svg)](#status)
[![Guide (English)](https://img.shields.io/badge/📖_Guide-English-blue.svg)](GUIDE.md)
[![指南 (中文)](https://img.shields.io/badge/📖_使用指南-中文-red.svg)](GUIDE_CN.md)

---

> 📖 **Setup & Config Guide:** &nbsp; [English → GUIDE.md](GUIDE.md) &nbsp;·&nbsp; [中文 → GUIDE_CN.md](GUIDE_CN.md)
>
> 内容包括：安装、Telegram/飞书配置、权限设置、发文件排查、openclaw.json 全字段说明

---

> ⚠️ **Beta Notice** — This project is in active development and continuously aligned with the TypeScript OpenClaw reference. Bugs and rough edges exist; updates are frequent. Feedback and bug reports are welcome!
>
> ⚠️ **测试版声明** — 本项目持续对齐 TypeScript 版 OpenClaw，正在快速迭代中。欢迎反馈问题和需求！

---

## Preview

<img src="assets/telegram-preview.jpg" alt="Jarvis on Telegram" width="260" />&nbsp;<img src="assets/IMG_1511.jpg" alt="Jarvis on Feishu" width="260" />&nbsp;<img src="assets/IMG_1544.jpg" alt="Jarvis on Feishu" width="260" />

*Jarvis responding on Telegram and Feishu — powered by OpenClaw Python*

---

## What it does

> ### 🐍 [TypeScript OpenClaw](https://github.com/badlogic/pi-mono) is awesome — but don't forget the Python version! 100% feature-aligned.

A self-hosted AI gateway that connects your messaging channels to LLMs:

- **Feishu (Lark)** — Full feature support: WebSocket real-time connection, streaming card output, media (image/file/voice), reactions, pairing, multi-account, Bitable, Wiki, Doc tools
- **Telegram** — Fully operational with robust polling (conflict-free restart logic, health monitor, streaming progress, queue control)
- **Other channels** — Discord, Slack, WhatsApp, Signal, IRC (code complete, runtime verification in progress)
- **Web UI** — Chat, session management, config at `http://localhost:18789`
- **Cron scheduler** — Autonomous scheduled tasks with flexible timing
- **Sub-agents** — Spawn, registry, thread binding, Docker sandbox
- **Permission presets** — Quick security level switching (Relaxed/Trusted/Standard/Strict)

### 🌟 LLM Provider Support (25+ Providers)

Full parity with TypeScript version, supporting multiple authentication methods:

- **Mainstream AI:** Anthropic (Claude), OpenAI, Google Gemini, xAI (Grok), Mistral AI
- **Chinese Services:** Kimi Coding (k2p5), MiniMax, Moonshot, Z.AI (GLM), Qwen, Qianfan (Baidu), Volcengine, BytePlus, Xiaomi
- **Aggregators & Proxies:** OpenRouter, LiteLLM, Kilo Gateway, Vercel AI Gateway, Cloudflare AI Gateway, OpenCode Zen, Synthetic
- **Self-Hosted & Open Source:** vLLM, Together AI, Hugging Face, Venice AI
- **Custom Providers:** Any OpenAI/Anthropic-compatible endpoint

**Authentication:** API Keys (fully supported), OAuth/Portal flows (partial support)

**Streaming:** Full Anthropic Messages API streaming support with proper event handling

📖 **See [Provider Configuration Guide](docs/providers.md) for complete details**

---

## Quick Start

### Prerequisites

- **Python 3.11+** — Check with `python3 --version`
- **[uv](https://docs.astral.sh/uv/)** — Fast Python package manager
- **LLM API key** — Gemini (recommended), Claude, or OpenAI

Install `uv` if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Installation

Clone both repositories as siblings (required structure):

```bash
# Create a workspace folder (any name works)
mkdir my-workspace && cd my-workspace

# Clone both repos
git clone https://github.com/openxjarvis/pi-mono-python.git
git clone https://github.com/openxjarvis/openclaw-python.git

# Install dependencies
cd openclaw-python
uv sync
```

Your folder structure should look like:

```
my-workspace/
├── openclaw-python/       ← main application
└── pi-mono-python/        ← required sibling (agent core)
```

### First-Time Setup

Run the interactive setup wizard:

```bash
uv run openclaw onboard
```

The wizard will guide you through:

1. **LLM Provider** — Choose Gemini, Claude, OpenAI, etc.
2. **API Keys** — Enter your API key (saved to `.env`)
3. **Channel Setup** — Configure Telegram, Feishu, or skip
4. **Gateway Port** — Default is 18789
5. **Workspace** — Agent working directory

### Start the Gateway

```bash
uv run openclaw start
```

OpenClaw is now running:

- **Web UI:** http://localhost:18789
- **Telegram:** Send a message to your bot
- **Feishu:** Message your Feishu bot directly

### Update to Latest Version

```bash
cd openclaw-python
git pull && uv sync

cd ../pi-mono-python
git pull && uv sync

# Restart the gateway
uv run openclaw restart
```

---

## Dependencies: pi-mono-python

`openclaw-python` depends on **[pi-mono-python](https://github.com/openxjarvis/pi-mono-python)** — a companion repo that provides the core agent and LLM infrastructure as local packages:

| Package | Provides |
|---|---|
| `pi-ai` | Unified LLM streaming layer (Gemini, Anthropic, OpenAI, …) |
| `pi-agent` | Agent loop, tool execution, session state |
| `pi-coding-agent` | Coding agent with file/bash/search tools |
| `pi-tui` | Terminal UI rendering engine |

Both repos must be cloned as siblings inside the same parent directory (any name works):

```
my-workspace/
├── openclaw-python/       ← this repo
└── pi-mono-python/        ← required sibling
```

---

## Feishu (Lark) — Full Feature Support

飞书是目前功能最完整的渠道，支持所有功能：

| Feature | Status |
|---------|--------|
| WebSocket long-connection | ✅ |
| Streaming card output (实时流式卡片) | ✅ |
| Image / File / Voice message | ✅ |
| Message reactions (reaction ACK) | ✅ |
| Pairing / allowlist / DM policy | ✅ |
| Multi-account | ✅ |
| Bitable (多维表格) tools | ✅ |
| Wiki / Doc read & write | ✅ |
| Mention / group chat | ✅ |

---

## Telegram — Optimized

- Conflict-free polling (fixes self-inflicted 409 loop from dual-start bug)
- PTB internal retry loop handles transient conflicts automatically
- Health monitor with `get_me()` checks every 60s
- Update offset persistence across restarts
- Deduplication for all update types
- **Streaming progress** — DMs show reasoning steps; groups show live preview bubble
- **Queue control** — `/stop` to abort, `/queue` to change behavior (interrupt/steer/followup/collect)
- **3-minute auto-timeout** — Prevents stuck runs

---

## Configuration

### Quick Configuration

The easiest way to configure OpenClaw is through the interactive wizard:

```bash
uv run openclaw onboard
```

### Manual Configuration

Edit `~/.openclaw/openclaw.json` for advanced settings:

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

### Essential CLI Commands

```bash
# Gateway management
uv run openclaw start         # Start the gateway
uv run openclaw stop          # Stop the gateway
uv run openclaw restart       # Restart the gateway
uv run openclaw status        # Check gateway status

# User management (for pairing mode)
uv run openclaw pairing list              # List pairing requests
uv run openclaw pairing approve <user_id> # Approve a user
uv run openclaw pairing deny <user_id>    # Deny a user

# Security presets
uv run openclaw security preset           # View/switch permission levels
uv run openclaw security preset trusted   # Set to trusted mode

# Configuration
uv run openclaw onboard                   # Run setup wizard again
uv run openclaw config show               # Show current config
```

For complete CLI reference and detailed configuration, see **[GUIDE.md](GUIDE.md)**.

---

## Permissions & Troubleshooting

> **If the agent says it "can't do" something, the cause is usually a permission config — not a code bug.**

OpenClaw has several independent permission layers. Check these before debugging code:

### 1. Channel Access — Who can talk to the bot

Controls who can send direct messages to your bot. Configured per channel in `~/.openclaw/openclaw.json`:

| Policy | Behavior | Use Case |
|---|---|---|
| `pairing` (default) | New users must request access and be approved via CLI | Recommended for personal bots |
| `allowlist` | Only pre-approved users can interact | Team/group usage |
| `open` | Any user can interact immediately | Public bots (use with caution) |
| `disabled` | No DM access allowed | Channel-only mode |

**Example:**

```json
{ "channels": { "telegram": { "dmPolicy": "pairing" } } }
```

**Approve users in pairing mode:**

```bash
# List pending requests
uv run openclaw pairing list

# Approve a user
uv run openclaw pairing approve <user_id>
```

### 2. Bash Execution — What shell commands the agent can run

Controls whether the agent can execute shell commands. Configured in `~/.openclaw/openclaw.json`:

| Setting | Effect | Example Commands |
|---------|--------|------------------|
| `deny` | **Cannot run any shell commands** | Agent can only read/write files |
| `allowlist` | Only whitelisted binaries allowed | `python`, `git`, `ffmpeg`, etc. |
| `full` | Can run any command | Recommended for trusted environments |

**Example configuration:**

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

> **Note:** The `exec.security` setting only affects the `bash` tool. File read/write operations are always available regardless of this setting.

**Quick preset switching:**

```bash
# View current permission level
uv run openclaw security preset

# Switch to trusted mode (recommended for personal use)
uv run openclaw security preset trusted
```

Available presets: `relaxed` · `trusted` · `standard` · `strict`

### 3. Feishu App Scopes — What Feishu API features work

If a Feishu tool fails with "Access denied" or "scope required", you need to enable that scope in the [Feishu Developer Console](https://open.feishu.cn/):

| Scope | Required for |
|-------|-------------|
| `im:message`, `im:message:send_as_bot` | Basic messaging (required) |
| `im:message.reaction:write` | Typing indicator reactions |
| `task:task:write` | Creating / updating tasks |
| `calendar:calendar.event:write` | Creating calendar events |
| `bitable:app` | Bitable (spreadsheet) tools |
| `docx:document`, `wiki:wiki` | Doc / Wiki read & write |
| `drive:drive` | Cloud drive file access |

After enabling new scopes, you must **publish a new app version** in the Feishu console for changes to take effect.

### Common Permission Issues

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Agent says "I cannot run commands" | `exec.security: deny` | Set to `allowlist` or `full` |
| Agent can't generate files with scripts | `exec.security: deny` blocks bash | Agent can still use `write_file` for text; enable bash for scripts |
| Feishu task/calendar tools fail | Missing API scope | Enable scope in Feishu console and republish |
| Bot doesn't respond to new users | DM policy is `pairing` | Approve via `uv run openclaw pairing approve` or set `dmPolicy: open` |
| Agent can write files but can't run Python | `exec.security: allowlist` missing `python` | Add `python` to `safe_bins` |
| Bot stuck / unresponsive after complex task | Agent run looping or timed out | Send `/stop` (auto-timeout after 3 min) |

**Quick fix:** Run `uv run openclaw security preset` to see current permission level and switch to a preset (Trusted recommended for personal use).

See **[GUIDE.md](GUIDE.md)** for full configuration reference.

---

## Status

Continuously aligned with the TypeScript [OpenClaw](https://github.com/badlogic/pi-mono) reference.

### Channels

| Channel | Status | Notes |
|---------|--------|-------|
| **Telegram** | ✅ Production ready | Fully tested and operational |
| **Feishu (Lark)** | ✅ Production ready | Full feature support |
| **Ollama (local models)** | ✅ Production ready | Tested locally |
| Discord / Slack / WhatsApp / Signal / IRC | 🔧 Runtime verification in progress | Code complete |

### AI Providers

| Provider | Status | Models / Notes |
|----------|--------|--------|
| **Google Gemini** | ✅ Production | Gemini 2.5 Pro, Gemini 2.0 Flash, Gemini 1.5 Pro/Flash |
| **Anthropic Claude** | ✅ Production | Claude 3.5 Sonnet, Claude 3.5 Haiku, Claude 3 Opus |
| **OpenAI** | ✅ Production | GPT-4o, o1, o3-mini |
| **DeepSeek** | ✅ Production | DeepSeek-V3, DeepSeek-R1 |
| **Ollama (local models)** | ✅ Production | Llama 3.3, Mistral, Qwen, CodeLlama (local) |
| **AWS Bedrock** | ✅ Production | Claude 3.x, Llama 3.3, Mistral |
| **Mistral AI** | ✅ Implemented | Mistral Large, Mistral Medium |
| **xAI (Grok)** | ✅ Implemented | Grok series models |
| **Kimi (Moonshot)** | ✅ Implemented | Kimi Coding (k2p5) |
| **MiniMax** | ✅ Implemented | MiniMax series |
| **Moonshot** | ✅ Implemented | Moonshot series |
| **Zhipu AI (GLM)** | ✅ Implemented | GLM-4, ChatGLM series |
| **Qwen (Alibaba)** | ✅ Implemented | Qwen series models |
| **Qianfan (Baidu)** | ✅ Implemented | ERNIE (Wenxin) series |
| **Volcengine** | ✅ Implemented | Doubao series |
| **BytePlus** | ✅ Implemented | ByteDance international |
| **Xiaomi AI** | ✅ Implemented | Xiaomi LLM |
| **OpenRouter** | ✅ Implemented | Multi-provider aggregator |
| **LiteLLM** | ✅ Implemented | Unified interface proxy |
| **Together AI** | ✅ Implemented | Open-source model hosting |
| **Hugging Face** | ✅ Implemented | Inference API |
| **vLLM** | ✅ Implemented | Self-hosted inference engine |
| **Venice AI** | ✅ Implemented | Privacy-first AI platform |
| **Kilo Gateway** | ✅ Implemented | API gateway |
| **Vercel AI Gateway** | ✅ Implemented | Vercel AI SDK |
| **Cloudflare AI Gateway** | ✅ Implemented | Cloudflare Workers AI |
| **OpenCode Zen** | ✅ Implemented | Coding-focused |
| **Synthetic** | ✅ Implemented | Synthetic data generation |
| **Custom endpoints** | ✅ Implemented | Any OpenAI/Anthropic-compatible endpoint |

### Core Infrastructure

| Component | Status |
|-----------|--------|
| Gateway server + Web UI | ✅ Production |
| Session management | ✅ Production |
| Tool system | ✅ Production |
| Skill system | ✅ Production |
| Cron scheduler | ✅ Production |
| Sub-agents (spawn, registry) | ✅ Production |
| Docker sandbox | ✅ Implemented |
| Context compaction | ✅ Production |

---

## Development

```bash
# Run tests
uv run pytest

# Lint
uv run ruff check .
uv run ruff format .

# Build Web UI (if modifying frontend)
cd openclaw/web/ui-src
npm install && npm run build
```

---

## Related Projects

- **OpenClaw TypeScript** — [github.com/badlogic/pi-mono](https://github.com/badlogic/pi-mono) — upstream reference implementation
- **pi-mono-python** — [github.com/openxjarvis/pi-mono-python](https://github.com/openxjarvis/pi-mono-python) — core agent infrastructure

---

## License

MIT — see [LICENSE](LICENSE) for details.
