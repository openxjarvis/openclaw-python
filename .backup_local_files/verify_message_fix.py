#!/usr/bin/env python3
"""
验证重复 Assistant 消息修复

检查 runtime.py 中的关键修复是否已实施
"""

import sys
from pathlib import Path

def check_file_content(file_path: Path, checks: list[dict]) -> bool:
    """检查文件内容是否包含预期的代码段"""
    try:
        content = file_path.read_text()
        all_passed = True
        
        print(f"\n🔍 检查文件: {file_path.relative_to(Path.cwd())}")
        print("=" * 60)
        
        for i, check in enumerate(checks, 1):
            name = check['name']
            pattern = check['pattern']
            should_exist = check.get('should_exist', True)
            
            exists = pattern in content
            passed = exists == should_exist
            
            status = "✅" if passed else "❌"
            action = "应该包含" if should_exist else "不应该包含"
            
            print(f"{status} 检查 {i}: {name}")
            if not passed:
                print(f"   期望: {action}")
                print(f"   实际: {'包含' if exists else '不包含'}")
                all_passed = False
        
        return all_passed
        
    except Exception as e:
        print(f"❌ 读取文件失败: {e}")
        return False


def main():
    project_root = Path(__file__).parent
    runtime_file = project_root / "openclaw" / "agents" / "runtime.py"
    
    if not runtime_file.exists():
        print(f"❌ 找不到文件: {runtime_file}")
        return 1
    
    # 定义检查项
    checks = [
        {
            'name': '初始化 initial_text 和 initial_tool_calls',
            'pattern': 'initial_text = ""  # Store initial assistant text',
            'should_exist': True
        },
        {
            'name': '初始化 initial_tool_calls',
            'pattern': 'initial_tool_calls = []  # Store tool calls for merging',
            'should_exist': True
        },
        {
            'name': '存储 initial_text',
            'pattern': 'initial_text = final_text',
            'should_exist': True
        },
        {
            'name': '存储 initial_tool_calls',
            'pattern': 'initial_tool_calls = tool_calls',
            'should_exist': True
        },
        {
            'name': '延迟添加 assistant 消息（当有 tool_calls 时）',
            'pattern': "# Don't add assistant message yet - wait for final response",
            'should_exist': True
        },
        {
            'name': '合并 tool_calls 和 final text',
            'pattern': 'final_tool_calls = initial_tool_calls if needs_tool_response else []',
            'should_exist': True
        },
        {
            'name': '添加单条完整的 assistant 消息',
            'pattern': '# Add single assistant message with both tool_calls and final text',
            'should_exist': True
        },
        {
            'name': '不应该有旧的立即添加逻辑（已移除）',
            'pattern': '# CRITICAL FIX: Add assistant message FIRST (before tool results)',
            'should_exist': False
        },
    ]
    
    all_passed = check_file_content(runtime_file, checks)
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ 所有检查通过！重复 Assistant 消息修复已正确实施。")
        print("\n下一步:")
        print("  1. 删除旧的 session 文件: rm ~/.openclaw/workspace/.sessions/*.json")
        print("  2. 启动 Gateway: uv run openclaw start")
        print("  3. 发送测试消息: '帮我上网看一下新闻每5分钟一次，做三次'")
        return 0
    else:
        print("❌ 部分检查未通过。请检查上述失败项。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
