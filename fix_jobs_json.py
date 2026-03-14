#!/usr/bin/env python3
"""Fix jobs.json format and remove invalid jobs."""
import json
from pathlib import Path

jobs_path = Path.home() / ".openclaw" / "cron" / "jobs.json"
backup_path = Path.home() / ".openclaw" / "cron" / "jobs.json.backup"

# Read current file
with open(jobs_path) as f:
    data = json.load(f)

# Backup
with open(backup_path, 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print(f"✓ Backup created: {backup_path}")

# Convert to correct format
if isinstance(data, dict) and "jobs" in data and isinstance(data["jobs"], list):
    print(f"✓ Found list format with {len(data['jobs'])} jobs, converting to dict format...")
    
    # Convert list to dict
    jobs_dict = {}
    removed = []
    
    for job in data["jobs"]:
        job_id = job.get("id")
        if not job_id:
            print(f"  ⚠️  Skipping job without id: {job.get('name', 'unnamed')}")
            continue
        
        # Check for invalid every_ms
        schedule = job.get("schedule", {})
        if schedule.get("type") == "every" and schedule.get("every_ms") == 0:
            removed.append(f"{job_id} ({job.get('name', 'unnamed')})")
            print(f"  ✗ Removing invalid job: {job_id} - every_ms is 0")
            continue
        
        jobs_dict[job_id] = job
    
    # Write corrected format
    with open(jobs_path, 'w') as f:
        json.dump(jobs_dict, f, indent=2, ensure_ascii=False)
    
    print(f"\n✓ Fixed jobs.json:")
    print(f"  - Valid jobs: {len(jobs_dict)}")
    print(f"  - Removed: {len(removed)}")
    if removed:
        print(f"  - Removed jobs: {', '.join(removed)}")
    print(f"  - Format: dict (job_id -> job_data)")
    
elif isinstance(data, dict):
    print(f"✓ Already in dict format with {len(data)} jobs")
    
    # Still check for invalid jobs
    removed = []
    for job_id, job in list(data.items()):
        schedule = job.get("schedule", {})
        if schedule.get("type") == "every" and schedule.get("every_ms") == 0:
            removed.append(f"{job_id} ({job.get('name', 'unnamed')})")
            print(f"  ✗ Removing invalid job: {job_id} - every_ms is 0")
            del data[job_id]
    
    if removed:
        with open(jobs_path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"\n✓ Removed {len(removed)} invalid jobs")
        print(f"  - Removed jobs: {', '.join(removed)}")
else:
    print(f"✗ Unknown format: {type(data)}")
    exit(1)

print(f"\n✓ Done! Jobs file fixed.")
