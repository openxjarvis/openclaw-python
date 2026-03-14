#!/usr/bin/env python3
"""
Create a test cron job to verify delivery to Telegram.
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta

cron_file = Path.home() / ".openclaw" / "cron" / "jobs.json"

# Create a test cron job that runs every minute
test_job = {
    "version": 1,
    "jobs": [
        {
            "id": "test-delivery-" + datetime.now().strftime("%H%M%S"),
            "name": "Test Telegram Delivery",
            "enabled": True,
            "schedule": {
                "kind": "every",
                "interval": 3,  # Every 3 minutes
                "unit": "minutes"
            },
            "sessionTarget": "isolated",
            "payload": {
                "kind": "agentTurn",
                "text": "搜索最新新闻，返回2-3条简要标题。"
            },
            "delivery": {
                "mode": "announce",
                "channel": "telegram",
                "to": "8366053063"
            }
        }
    ]
}

# Write to file
cron_file.parent.mkdir(parents=True, exist_ok=True)
with open(cron_file, "w") as f:
    json.dump(test_job, f, indent=2, ensure_ascii=False)

print(f"✓ Created test cron job: {cron_file}")
print(f"  Job ID: {test_job['jobs'][0]['id']}")
print(f"  Schedule: Every 3 minutes")
print(f"  Delivery: telegram → 8366053063")
print("\nWait 3 minutes for job to trigger, then check:")
print("  tail -f ~/.openclaw/logs/gateway.log | grep -E 'cron|deliver|telegram'")
