#!/bin/bash

# Cron 修复重启脚本

echo "========================================"
echo "🔄 准备重启 OpenClaw Gateway"
echo "========================================"
echo

# 检查当前是否有 gateway 进程
GATEWAY_PID=$(pgrep -f "openclaw start" | head -1)

if [ -n "$GATEWAY_PID" ]; then
    echo "⚠️  检测到正在运行的 gateway (PID: $GATEWAY_PID)"
    echo "   正在停止..."
    kill -TERM $GATEWAY_PID
    sleep 2
    
    # 检查是否还在运行
    if ps -p $GATEWAY_PID > /dev/null 2>&1; then
        echo "   强制停止..."
        kill -9 $GATEWAY_PID
        sleep 1
    fi
    echo "   ✅ Gateway 已停止"
else
    echo "ℹ️  没有检测到正在运行的 gateway"
fi

echo
echo "========================================"
echo "🚀 启动 Gateway"
echo "========================================"
echo

cd "$(dirname "$0")"

# 使用 uv 启动
echo "执行: uv run openclaw start"
echo
uv run openclaw start
