#!/usr/bin/env python3
"""
最终对齐验证脚本

验证 openclaw-python 与 TS openclaw 的对齐状态
"""
import sys
from pathlib import Path

def test_config_alignment():
    """测试配置系统对齐"""
    print("🔍 验证配置系统对齐...")
    
    try:
        from openclaw.config import OpenClawConfig, load_config
        from openclaw.config.schema import (
            LoggingConfig,
            GatewayConfig,
            GatewayTlsConfig,
            GatewayReloadConfig,
            GatewayHttpConfig,
            GatewayRemoteConfig,
        )
        
        # 测试 OpenClawConfig 实例化
        cfg = OpenClawConfig()
        assert cfg.gateway is not None
        assert cfg.gateway.port == 18789
        
        # 测试 LoggingConfig 新字段
        logging_cfg = LoggingConfig(
            file="test.log",
            max_file_bytes="2mb",
            console_level="DEBUG",
            redact_sensitive=True
        )
        assert logging_cfg.file == "test.log"
        assert logging_cfg.max_file_bytes == "2mb"
        
        # 测试 Gateway 子配置
        tls_cfg = GatewayTlsConfig(cert="/path/to/cert.pem", key="/path/to/key.pem")
        assert tls_cfg.cert == "/path/to/cert.pem"
        
        print("  ✅ OpenClawConfig 对齐")
        print("  ✅ LoggingConfig 新字段正常")
        print("  ✅ GatewayConfig 子配置正常")
        return True
    except Exception as e:
        print(f"  ❌ 配置系统错误: {e}")
        return False


def test_subagent_registry_alignment():
    """测试 SubagentRegistry 对齐"""
    print("\n🔍 验证 SubagentRegistry 对齐...")
    
    try:
        from openclaw.agents.subagent_registry import (
            SubagentRunRecord,
            SubagentRegistry,
            register_subagent_run,
            mark_subagent_run_started,
            mark_subagent_run_terminated,
            complete_subagent_run,
            get_global_registry,
            MAX_ANNOUNCE_RETRY_COUNT,
            SUBAGENT_ANNOUNCE_TIMEOUT_MS,
        )
        
        # 测试常量
        assert MAX_ANNOUNCE_RETRY_COUNT == 3
        assert SUBAGENT_ANNOUNCE_TIMEOUT_MS == 120_000
        
        # 测试注册
        record = register_subagent_run(
            requester_session_key="test:main",
            child_session_key="test:child",
            task="test task",
            model="claude-3-5-sonnet-latest",
        )
        assert isinstance(record, SubagentRunRecord)
        assert record.task == "test task"
        assert record.model == "claude-3-5-sonnet-latest"
        assert record.requester_display_key == "test:main"  # Auto-inferred
        
        # 测试标记开始
        mark_subagent_run_started(record.run_id)
        assert record.started_at is not None
        
        # 测试标记结束
        outcome = {"status": "completed", "result": "success"}
        mark_subagent_run_terminated(record.run_id, outcome, "completed")
        assert record.ended_at is not None
        assert record.outcome == outcome
        
        # 测试兼容性别名
        complete_subagent_run(record.run_id, {"status": "ok"}, "test")
        
        # 测试 Registry 类
        registry = get_global_registry()
        assert hasattr(registry, '_runs')
        assert hasattr(registry, '_gateway')
        
        print("  ✅ SubagentRunRecord 数据结构对齐")
        print("  ✅ 常量导出正常")
        print("  ✅ API 签名兼容")
        print("  ✅ Registry 类接口完整")
        return True
    except Exception as e:
        print(f"  ❌ SubagentRegistry 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_media_system_alignment():
    """测试 Media 系统对齐"""
    print("\n🔍 验证 Media 系统对齐...")
    
    try:
        from openclaw.media.local_roots import (
            build_media_local_roots,
            get_default_media_local_roots,
            get_agent_scoped_media_local_roots,
        )
        
        # 测试 build_media_local_roots
        roots = build_media_local_roots("/test/state")
        assert isinstance(roots, list)
        assert all(isinstance(r, str) for r in roots)
        assert len(roots) == 5  # tmp, media, agents, workspace, sandboxes
        
        # 测试 get_default_media_local_roots  
        default_roots = get_default_media_local_roots()
        assert isinstance(default_roots, list)
        assert len(default_roots) >= 5
        
        # 测试 get_agent_scoped_media_local_roots
        agent_roots = get_agent_scoped_media_local_roots({}, "test-agent")
        assert isinstance(agent_roots, list)
        
        print("  ✅ build_media_local_roots 实现")
        print("  ✅ get_default_media_local_roots 实现")
        print("  ✅ 返回类型 list[str] 对齐")
        return True
    except Exception as e:
        print(f"  ❌ Media 系统错误: {e}")
        return False


def test_import_paths():
    """测试导入路径修正"""
    print("\n🔍 验证导入路径修正...")
    
    try:
        # 测试配置加载器
        from openclaw.config.loader import load_config
        
        # 测试 SubagentRegistry 所有导出
        from openclaw.agents.subagent_registry import (
            SubagentRunRecord,
            SUBAGENT_RUNS,
            register_subagent_run,
            complete_subagent_run,
            get_global_registry,
        )
        
        print("  ✅ openclaw.config.loader 导入正常")
        print("  ✅ SubagentRegistry 导出完整")
        return True
    except Exception as e:
        print(f"  ❌ 导入路径错误: {e}")
        return False


def main():
    """主验证流程"""
    print("=" * 60)
    print("OpenClaw Python 最终对齐验证")
    print("=" * 60)
    
    results = []
    
    results.append(("配置系统", test_config_alignment()))
    results.append(("SubagentRegistry", test_subagent_registry_alignment()))
    results.append(("Media 系统", test_media_system_alignment()))
    results.append(("导入路径", test_import_paths()))
    
    print("\n" + "=" * 60)
    print("📊 验证结果汇总")
    print("=" * 60)
    
    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{name:20s} {status}")
    
    total = len(results)
    passed = sum(1 for _, p in results if p)
    success_rate = (passed / total) * 100
    
    print("\n" + "=" * 60)
    print(f"总计: {passed}/{total} 通过 ({success_rate:.1f}%)")
    print("=" * 60)
    
    if passed == total:
        print("\n🎉 所有核心模块对齐验证通过！")
        return 0
    else:
        print(f"\n⚠️  {total - passed} 个模块需要进一步检查")
        return 1


if __name__ == "__main__":
    sys.exit(main())
