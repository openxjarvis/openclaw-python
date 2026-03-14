#!/bin/bash
# 快速重启 OpenClaw Gateway

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           🔄 重启 OpenClaw Gateway                            ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

echo "⏸️  停止 Gateway..."
uv run openclaw gateway stop

echo ""
echo "⏳ 等待进程完全停止..."
sleep 2

echo ""
echo "🚀 启动 Gateway..."
uv run openclaw gateway start

echo ""
echo "✅ Gateway 已重启！"
echo ""
echo "📊 验证配置:"
uv run python3 << 'PYEOF'
import json
from pathlib import Path

config_path = Path.home() / ".openclaw" / "openclaw.json"
with open(config_path) as f:
    config = json.load(f)

agent_model = config.get("agent", {}).get("model")
agents_model = config.get("agents", {}).get("defaults", {}).get("model")

print(f"  agent.model: {agent_model}")
print(f"  agents.defaults.model: {agents_model}")

if agent_model == "kimi-coding/k2p5" and agents_model == "kimi-coding/k2p5":
    print("\n✅ 配置正确！使用 kimi-coding/k2p5")
else:
    print(f"\n⚠️  配置异常")
PYEOF

