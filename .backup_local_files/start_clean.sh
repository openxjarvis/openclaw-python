#!/bin/bash

# OpenClaw Clean Start Script
# Automatically stops conflicting instances and starts fresh

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

clear
echo ""
echo -e "${BOLD}${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${BLUE}║  🦞 OpenClaw Clean Startup${NC}           ${BOLD}${BLUE}║${NC}"
echo -e "${BOLD}${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""

# Step 1: Check for existing instances
echo -e "${BLUE}[1/4] Checking for existing instances...${NC}"
EXISTING=$(ps aux | grep -E "openclaw|Python.*openclaw" | grep -v grep | grep -v "start_clean" | wc -l)

if [ "$EXISTING" -gt 0 ]; then
    echo -e "${YELLOW}Found $EXISTING running instance(s)${NC}"
    echo ""
    ps aux | grep -E "openclaw" | grep -v grep | grep -v "start_clean"
    echo ""
    echo -e "${YELLOW}Stopping all instances...${NC}"
    
    # Get all PIDs
    PIDS=$(ps aux | grep -E "openclaw|Python.*openclaw" | grep -v grep | grep -v "start_clean" | awk '{print $2}')
    
    # Kill them
    for pid in $PIDS; do
        echo -n "  Killing PID $pid... "
        kill -9 $pid 2>/dev/null && echo -e "${GREEN}✓${NC}" || echo -e "${YELLOW}already stopped${NC}"
    done
    
    sleep 2
    echo -e "${GREEN}✓ All instances stopped${NC}"
else
    echo -e "${GREEN}✓ No existing instances found${NC}"
fi
echo ""

# Step 2: Check port 18789
echo -e "${BLUE}[2/4] Checking port 18789...${NC}"
PORT_IN_USE=$(lsof -i :18789 2>/dev/null | grep LISTEN | wc -l)

if [ "$PORT_IN_USE" -gt 0 ]; then
    echo -e "${RED}✗ Port 18789 is still in use!${NC}"
    lsof -i :18789 2>/dev/null | grep LISTEN
    echo ""
    echo -e "${RED}Please manually kill the process and try again.${NC}"
    exit 1
else
    echo -e "${GREEN}✓ Port 18789 is available${NC}"
fi
echo ""

# Step 3: Verify all fixes are applied
echo -e "${BLUE}[3/4] Verifying fixes...${NC}"

cd /Users/opendev/Desktop/ClawdBot2/openclaw-python

# Check key fixes
FIXES_COUNT=0
TOTAL_FIXES=11

echo -n "  Checking runtime.py fixes... "
if grep -q "final_response_params = {k: v for k, v in self.extra_params" openclaw/agents/runtime.py; then
    echo -e "${GREEN}✓${NC}"
    ((FIXES_COUNT++))
else
    echo -e "${RED}✗${NC}"
fi

echo -n "  Checking gemini_provider.py... "
if grep -q "tool_name = getattr(msg, 'name', None) or 'unknown_function'" openclaw/agents/providers/gemini_provider.py; then
    echo -e "${GREEN}✓${NC}"
    ((FIXES_COUNT++))
else
    echo -e "${RED}✗${NC}"
fi

echo -n "  Checking tsconfig.json... "
if [ -f "openclaw/web/ui-src/tsconfig.json" ]; then
    echo -e "${GREEN}✓${NC}"
    ((FIXES_COUNT++))
else
    echo -e "${RED}✗${NC}"
fi

echo ""
if [ "$FIXES_COUNT" -ge 2 ]; then
    echo -e "${GREEN}✓ Core fixes verified ($FIXES_COUNT checked)${NC}"
else
    echo -e "${YELLOW}⚠ Some fixes may be missing${NC}"
fi
echo ""

# Step 4: Start OpenClaw
echo -e "${BLUE}[4/4] Starting OpenClaw...${NC}"
echo ""
echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Starting in foreground mode...       ║${NC}"
echo -e "${GREEN}║  Press Ctrl+C to stop                 ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"
echo ""

# Start in foreground
exec uv run openclaw start
