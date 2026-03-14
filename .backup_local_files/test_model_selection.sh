#!/bin/bash
# Quick Test Script for Model Selection Fix
# Run this to test the onboarding flow

set -e  # Exit on error

echo "=================================================="
echo "OpenClaw Model Selection Fix - Test Script"
echo "=================================================="
echo ""

# Check if in correct directory
if [ ! -f "pyproject.toml" ]; then
    echo "❌ Error: Must run from openclaw-python directory"
    exit 1
fi

echo "Step 1: Backup existing config (if any)"
if [ -f ~/.openclaw/clawdbot-config.json ]; then
    BACKUP_FILE=~/.openclaw/clawdbot-config.json.backup.$(date +%Y%m%d_%H%M%S)
    cp ~/.openclaw/clawdbot-config.json "$BACKUP_FILE"
    echo "✓ Backed up to: $BACKUP_FILE"
else
    echo "ℹ No existing config found"
fi

echo ""
echo "Step 2: Remove old config"
rm -rf ~/.openclaw/clawdbot-config.json
echo "✓ Old config removed"

echo ""
echo "Step 3: Starting onboarding..."
echo "=================================================="
echo ""
echo "🎯 IMPORTANT CHECKS:"
echo "  1. After 'API key saved', you should see:"
echo "     -------------------------"
echo "     Model Selection"
echo "     -------------------------"
echo ""
echo "  2. You should see a questionary menu with model options"
echo ""
echo "  3. NO 'Add fallback models? [y/N]:' prompt should appear"
echo ""
echo "=================================================="
echo ""

# Run onboarding
uv run openclaw onboard

echo ""
echo "=================================================="
echo "Test completed!"
echo ""
echo "Did you see the Model Selection interface? (Y/N)"
read -r response

if [[ "$response" =~ ^[Yy]$ ]]; then
    echo "✅ SUCCESS! Model selection is working correctly."
    echo ""
    echo "Next steps:"
    echo "  - Test with different providers"
    echo "  - Report any issues found"
else
    echo "❌ ISSUE DETECTED!"
    echo ""
    echo "Please report:"
    echo "  1. Which provider did you select?"
    echo "  2. What did you see after 'API key saved'?"
    echo "  3. Screenshot of the terminal output"
fi

echo ""
echo "=================================================="
