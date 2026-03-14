#!/usr/bin/env python3
"""Create a test cron job that runs immediately."""
import json
from pathlib import Path
import time

jobs_path = Path.home() / ".openclaw" / "cron" / "jobs.json"

# Read current jobs
with open(jobs_path) as f:
    data = json.load(f)

# Create test job that runs in 1 minute
now_ms = int(time.time() * 1000)
run_time_ms = now_ms + 60000  # 1 minute from now

test_job = {
    "id": "cron-test-delivery",
    "name": "测试Telegram消息推送",
    "enabled": True,
    "session_target": "isolated",
    "wake_mode": "next-heartbeat",
    "created_at_ms": now_ms,
    "updated_at_ms": now_ms,
    "schedule": {
        "type": "at",
        "at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime((now_ms + 60000) / 1000))
    },
    "payload": {
        "kind": "agentTurn",
        "message": "这是一个测试消息。请回复：'测试成功！现在时间是 ' + 当前时间。"
    },
    "delivery": {
        "mode": "announce",
        "channel": "telegram",
        "to": "8366053063"
    },
    "state": {}
}

# Add to jobs
jobs = data.get("jobs", [])

# Remove existing test job if any
jobs = [j for j in jobs if j.get("id") != "cron-test-delivery"]

jobs.append(test_job)
data["jobs"] = jobs

# Save
with open(jobs_path, 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"✓ Test job created: cron-test-delivery")
print(f"✓ Will run at: {test_job['schedule']['at']}")
print(f"✓ Delivery: telegram → 8366053063")
print(f"\nWait for 1 minute to see the result in Telegram!")
