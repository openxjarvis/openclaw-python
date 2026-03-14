#!/usr/bin/env python3
"""Add delivery config to enabled cron jobs without it."""
import json
from pathlib import Path

jobs_path = Path.home() / ".openclaw" / "cron" / "jobs.json"
backup_path = Path.home() / ".openclaw" / "cron" / "jobs.json.backup-delivery-fix"

# Read current file
with open(jobs_path) as f:
    data = json.load(f)

# Backup
with open(backup_path, 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print(f"✓ Backup created: {backup_path}")

# Fix jobs
jobs = data.get("jobs", [])
fixed_count = 0

for job in jobs:
    if not job.get("enabled"):
        continue
    
    if job.get("delivery"):
        continue  # Already has delivery
    
    # Add default delivery: announce to last channel (telegram)
    job["delivery"] = {
        "mode": "announce",
        "channel": "telegram",
        "to": "8366053063"  # Your Telegram chat ID
    }
    
    fixed_count += 1
    job_id = job.get("id", "?")
    job_name = job.get("name", "unnamed")
    print(f"✓ Added delivery to: {job_id} ({job_name})")

# Save
with open(jobs_path, 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"\n✓ Fixed {fixed_count} jobs")
print(f"✓ Jobs file updated: {jobs_path}")
