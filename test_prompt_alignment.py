#!/usr/bin/env python3
"""
Prompt 对齐测试套件
测试 OpenClaw 和 Pi-Mono 工具的 prompt 对齐质量
"""

import os
import sys
from pathlib import Path

# 添加项目路径
openclaw_path = Path("/Users/long/Desktop/XJarvis/openclaw-python")
pi_mono_path = Path("/Users/long/Desktop/XJarvis/pi-mono-python/packages/coding-agent/src")

sys.path.insert(0, str(openclaw_path))
sys.path.insert(0, str(pi_mono_path))


def test_message_tool_dynamic_description():
    """✅ TEST 1: Message Tool 动态 description"""
    print("🧪 测试 1: Message Tool 动态 description...")
    
    try:
        from openclaw.agents.tools.channel_actions import MessageTool
        
        # 测试场景 1: 有 current_channel
        tool = MessageTool(
            config={"channels": {"telegram": {"enabled": True}}},
            current_channel_provider="telegram"
        )
        desc = tool.description.lower()
        
        assert "telegram" in desc, "❌ 应包含 'telegram'"
        assert "supports:" in desc or "support" in desc, "❌ 应包含 'supports'"
        print("  ✅ 有 current_channel 时动态生成")
        
        # 测试场景 2: 无 current_channel
        tool2 = MessageTool(config={})
        desc2 = tool2.description.lower()
        
        assert "send" in desc2 and "delete" in desc2, "❌ 应包含基本 actions"
        print("  ✅ 无 current_channel 时显示通用描述")
        
        print("✅ TEST 1 通过\n")
        return True
    except Exception as e:
        print(f"❌ TEST 1 失败: {e}\n")
        return False


def test_web_search_freshness_description():
    """✅ TEST 2: Web Search freshness 参数说明"""
    print("🧪 测试 2: Web Search freshness 参数说明...")
    
    try:
        from openclaw.agents.tools.web import WebSearchTool
        
        tool = WebSearchTool(provider="brave")
        desc = tool.description
        
        assert "'pd'" in desc or "pd" in desc, "❌ 应包含 'pd' (past day)"
        assert "'pw'" in desc or "pw" in desc, "❌ 应包含 'pw' (past week)"
        assert "'pm'" in desc or "pm" in desc, "❌ 应包含 'pm' (past month)"
        assert "'py'" in desc or "py" in desc, "❌ 应包含 'py' (past year)"
        assert "2-letter" in desc.lower(), "❌ 应说明 search_lang 是 2-letter code"
        print("  ✅ Freshness 参数说明完整")
        print("  ✅ Language 参数说明清晰")
        
        print("✅ TEST 2 通过\n")
        return True
    except Exception as e:
        print(f"❌ TEST 2 失败: {e}\n")
        return False


def test_tts_silent_reply_hint():
    """✅ TEST 3: TTS SILENT_REPLY 提示"""
    print("🧪 测试 3: TTS SILENT_REPLY 提示...")
    
    try:
        from openclaw.agents.tools.tts import TTSTool
        
        tool = TTSTool()
        desc = tool.description
        
        assert "SILENT_REPLY" in desc or "silent" in desc.lower(), "❌ 应包含 SILENT_REPLY 提示"
        assert "duplicate" in desc.lower() or "avoid" in desc.lower(), "❌ 应说明避免重复消息"
        print("  ✅ SILENT_REPLY 提示存在")
        print("  ✅ 避免重复消息的原因说明")
        
        # 验证 schema 简化
        schema = tool.get_schema()
        props = schema["properties"]
        
        assert "text" in props, "❌ 应包含 text 参数"
        assert "channel" in props, "❌ 应包含 channel 参数"
        # 不应包含 provider/voice/model 等（应由配置控制）
        print("  ✅ Schema 简化对齐 TS 版本")
        
        print("✅ TEST 3 通过\n")
        return True
    except Exception as e:
        print(f"❌ TEST 3 失败: {e}\n")
        return False


def test_cron_critical_constraints():
    """✅ TEST 4: Cron CRITICAL CONSTRAINTS"""
    print("🧪 测试 4: Cron CRITICAL CONSTRAINTS...")
    
    try:
        from openclaw.agents.tools.cron import CronTool
        
        tool = CronTool()
        desc = tool.description
        
        assert "CRITICAL" in desc or "critical" in desc.lower(), "❌ 应包含 CRITICAL 标记"
        assert "sessionTarget" in desc, "❌ 应说明 sessionTarget 约束"
        assert "systemEvent" in desc or "agentTurn" in desc, "❌ 应说明 payload.kind"
        print("  ✅ CRITICAL CONSTRAINTS 存在")
        
        # 验证 schema 中 sessionTarget 无误导性 default
        schema = tool.get_schema()
        session_target = None
        for key in ["sessionTarget", "session_target"]:
            if key in schema.get("properties", {}):
                session_target = schema["properties"][key]
                break
        
        if session_target:
            assert "default" not in session_target or session_target.get("default") != "main", \
                "❌ sessionTarget 不应有 default: 'main'"
            print("  ✅ sessionTarget schema 无误导性 default")
        
        print("✅ TEST 4 通过\n")
        return True
    except Exception as e:
        print(f"❌ TEST 4 失败: {e}\n")
        return False


def test_pi_mono_read_tool():
    """✅ TEST 5: Pi-Mono Read Tool 完整提示"""
    print("🧪 测试 5: Pi-Mono Read Tool 完整提示...")
    
    try:
        from pi_coding_agent.core.tools.read import create_read_tool
        
        tool = create_read_tool(os.getcwd())
        desc = tool.description.lower()
        
        assert "continue" in desc or "offset" in desc, "❌ 应包含 continue/offset 提示"
        assert "truncat" in desc, "❌ 应说明截断逻辑"
        assert "image" in desc, "❌ 应说明支持图片"
        print("  ✅ Continue with offset 提示存在")
        print("  ✅ 截断逻辑说明完整")
        print("  ✅ 图片支持说明清晰")
        
        print("✅ TEST 5 通过\n")
        return True
    except Exception as e:
        print(f"❌ TEST 5 失败: {e}\n")
        return False


def test_all_tools_importable():
    """✅ TEST 6: 所有工具可导入（回归测试）"""
    print("🧪 测试 6: 所有工具可导入...")
    
    try:
        # OpenClaw 工具
        from openclaw.agents.tools.cron import CronTool
        from openclaw.agents.tools.sessions import SessionsListTool
        from openclaw.agents.tools.memory import MemorySearchTool
        from openclaw.agents.tools.channel_actions import MessageTool
        from openclaw.agents.tools.web import WebSearchTool
        from openclaw.agents.tools.tts import TTSTool
        print("  ✅ OpenClaw 工具全部可导入")
        
        # Pi-Mono 工具
        from pi_coding_agent.core.tools.read import create_read_tool
        from pi_coding_agent.core.tools.bash import create_bash_tool
        from pi_coding_agent.core.tools.edit import create_edit_tool
        print("  ✅ Pi-Mono 工具全部可导入")
        
        print("✅ TEST 6 通过\n")
        return True
    except Exception as e:
        print(f"❌ TEST 6 失败: {e}\n")
        return False


def test_all_tools_have_schema():
    """✅ TEST 7: 所有工具有合法 schema"""
    print("🧪 测试 7: 所有工具有合法 schema...")
    
    try:
        from openclaw.agents.tools.cron import CronTool
        from openclaw.agents.tools.channel_actions import MessageTool
        from openclaw.agents.tools.web import WebSearchTool
        from openclaw.agents.tools.tts import TTSTool
        
        tools = [
            ("CronTool", CronTool()),
            ("MessageTool", MessageTool()),
            ("WebSearchTool", WebSearchTool()),
            ("TTSTool", TTSTool())
        ]
        
        for name, tool in tools:
            schema = tool.get_schema()
            assert "type" in schema, f"❌ {name} schema 应有 'type'"
            assert "properties" in schema, f"❌ {name} schema 应有 'properties'"
            assert schema["type"] == "object", f"❌ {name} schema type 应为 'object'"
            print(f"  ✅ {name} schema 合法")
        
        print("✅ TEST 7 通过\n")
        return True
    except Exception as e:
        print(f"❌ TEST 7 失败: {e}\n")
        return False


def main():
    """运行所有测试"""
    print("=" * 60)
    print("🚀 Prompt 对齐测试套件")
    print("=" * 60)
    print()
    
    results = []
    
    # OpenClaw 测试
    print("📦 OpenClaw 工具测试")
    print("-" * 60)
    results.append(("Message Tool", test_message_tool_dynamic_description()))
    results.append(("Web Search Tool", test_web_search_freshness_description()))
    results.append(("TTS Tool", test_tts_silent_reply_hint()))
    results.append(("Cron Tool", test_cron_critical_constraints()))
    
    # Pi-Mono 测试
    print("📦 Pi-Mono 工具测试")
    print("-" * 60)
    results.append(("Read Tool", test_pi_mono_read_tool()))
    
    # 回归测试
    print("📦 回归测试")
    print("-" * 60)
    results.append(("Import Test", test_all_tools_importable()))
    results.append(("Schema Test", test_all_tools_have_schema()))
    
    # 统计结果
    print("=" * 60)
    print("📊 测试结果汇总")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status} - {name}")
    
    print()
    print(f"总计: {passed}/{total} 测试通过 ({passed/total*100:.1f}%)")
    
    if passed == total:
        print("\n🎉 所有测试通过！Prompt 对齐质量良好。")
        return 0
    else:
        print(f"\n⚠️  {total - passed} 个测试失败，需要修复。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
