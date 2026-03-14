#!/usr/bin/env python3
"""修复 cron jobs - 将 session_target 从 main 改为 isolated"""

import json
import shutil
from pathlib import Path

def fix_cron_jobs():
    """修复所有 cron jobs 的 session_target"""
    jobs_file = Path.home() / ".openclaw" / "cron" / "jobs.json"
    
    if not jobs_file.exists():
        print(f"❌ File not found: {jobs_file}")
        return False
    
    # 备份原文件
    backup_file = jobs_file.with_suffix(".json.backup")
    shutil.copy(jobs_file, backup_file)
    print(f"✅ Backup created: {backup_file}")
    
    # 读取现有配置
    with open(jobs_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    jobs = data.get("jobs", [])
    fixed_count = 0
    
    print(f"\n📊 Found {len(jobs)} cron jobs")
    print()
    
    for job in jobs:
        job_id = job.get("id", "unknown")
        name = job.get("name", "unnamed")
        session_target = job.get("session_target", "")
        payload = job.get("payload", {})
        payload_kind = payload.get("kind", "")
        
        print(f"Job: {name} ({job_id})")
        print(f"  session_target: {session_target}")
        print(f"  payload.kind: {payload_kind}")
        
        # 检查是否需要修复
        if session_target == "main" and payload_kind == "agentTurn":
            print(f"  ⚠️  ERROR: main + agentTurn is invalid!")
            print(f"  🔧 Fixing: session_target = 'isolated'")
            
            job["session_target"] = "isolated"
            
            # 添加 delivery 配置（isolated agentTurn 需要）
            if "delivery" not in job or not job["delivery"]:
                job["delivery"] = {"mode": "announce"}
                print(f"  🔧 Adding: delivery = {{'mode': 'announce'}}")
            
            # 清除错误状态
            if "state" in job:
                state = job["state"]
                if state.get("last_error") == 'main job requires payload.kind="systemEvent"':
                    state.pop("last_error", None)
                    state.pop("last_run_status", None)
                    state.pop("last_status", None)
                    print(f"  🔧 Cleared error state")
            
            fixed_count += 1
            print(f"  ✅ Fixed!")
        elif session_target == "main" and payload_kind == "systemEvent":
            print(f"  ✅ OK (main + systemEvent)")
        elif session_target == "isolated" and payload_kind == "agentTurn":
            print(f"  ✅ OK (isolated + agentTurn)")
        else:
            print(f"  ⚠️  Unknown combination")
        
        print()
    
    # 保存修复后的配置
    if fixed_count > 0:
        with open(jobs_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        
        print(f"✅ Fixed {fixed_count} jobs")
        print(f"✅ Saved to: {jobs_file}")
        print()
        print("🎉 All jobs are now valid!")
        print()
        print("📖 Rules:")
        print("  - main session → payload.kind must be 'systemEvent'")
        print("  - isolated session → payload.kind must be 'agentTurn'")
        print()
        print("💡 Your jobs now use:")
        print("  - session_target: 'isolated' (agent 在独立会话中运行)")
        print("  - payload.kind: 'agentTurn' (agent 执行任务并可以发送消息)")
        print("  - delivery.mode: 'announce' (结果发送到频道)")
        print()
        return True
    else:
        print("✅ No fixes needed - all jobs are valid!")
        return True

if __name__ == "__main__":
    print("=" * 60)
    print("🔧 OpenClaw Cron Jobs Fixer")
    print("=" * 60)
    print()
    
    try:
        success = fix_cron_jobs()
        if success:
            print("\n🔄 Please restart Gateway to apply changes:")
            print("   pkill -f 'openclaw gateway'")
            print("   uv run openclaw gateway run")
        exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
