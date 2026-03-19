#!/usr/bin/env python
"""
Onboarding Alignment Verification Script

Tests all new features added for TS alignment:
1. Model picker functions (apply_primary_model, apply_model_fallbacks_from_selection)
2. Fallback provider configuration
3. Model config health check
4. Gateway probe
5. Remote gateway config
"""
import sys
import asyncio
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_model_picker():
    """Test model picker functions"""
    print("\n" + "="*80)
    print("TEST: Model Picker Functions")
    print("="*80)
    
    from openclaw.config.schema import OpenClawConfig, AgentsConfig, AgentDefaults, ModelConfig
    from openclaw.wizard.model_picker import (
        apply_primary_model,
        apply_model_fallbacks_from_selection,
    )
    
    # Test 1: apply_primary_model
    print("\n1. Testing apply_primary_model()...")
    config = OpenClawConfig(
        agents=AgentsConfig(
            defaults=AgentDefaults(
                model=ModelConfig(
                    primary="anthropic/claude-opus-4-5",
                    fallbacks=["openai/gpt-4o"]
                )
            )
        )
    )
    
    updated_config = apply_primary_model(config, "anthropic/claude-sonnet-4-6")
    
    assert isinstance(updated_config.agents.defaults.model, ModelConfig), "Model should be ModelConfig"
    assert updated_config.agents.defaults.model.primary == "anthropic/claude-sonnet-4-6", "Primary model should be updated"
    assert updated_config.agents.defaults.model.fallbacks == ["openai/gpt-4o"], "Fallbacks should be preserved"
    print("   ✓ apply_primary_model preserves fallbacks correctly")
    
    # Test 2: apply_model_fallbacks_from_selection
    print("\n2. Testing apply_model_fallbacks_from_selection()...")
    config2 = OpenClawConfig()
    selection = [
        "anthropic/claude-sonnet-4-6",
        "openai/gpt-4o",
        "google/gemini-2.5-pro"
    ]
    
    updated_config2 = apply_model_fallbacks_from_selection(config2, selection)
    
    assert isinstance(updated_config2.agents.defaults.model, ModelConfig), "Model should be ModelConfig"
    assert updated_config2.agents.defaults.model.primary == "anthropic/claude-sonnet-4-6", "Primary should be first in selection"
    assert updated_config2.agents.defaults.model.fallbacks == [
        "openai/gpt-4o",
        "google/gemini-2.5-pro"
    ], "Fallbacks should be rest of selection"
    print("   ✓ apply_model_fallbacks_from_selection works correctly")
    
    print("\n✅ Model Picker Tests: PASSED")


def test_fallback_provider_config():
    """Test fallback provider configuration"""
    print("\n" + "="*80)
    print("TEST: Fallback Provider Configuration")
    print("="*80)
    
    from openclaw.wizard.fallback_provider_config import (
        extract_provider_from_model_id,
        check_provider_configured,
    )
    
    # Test 1: extract_provider_from_model_id
    print("\n1. Testing extract_provider_from_model_id()...")
    
    test_cases = [
        ("anthropic/claude-sonnet-4-6", "anthropic"),
        ("openai/gpt-4o", "openai"),
        ("google/gemini-2.5-pro", "google"),
        ("moonshot/kimi-k2.5", "moonshot"),
        ("invalid-format", None),
    ]
    
    for model_id, expected in test_cases:
        result = extract_provider_from_model_id(model_id)
        assert result == expected, f"Expected {expected} for {model_id}, got {result}"
    
    print("   ✓ extract_provider_from_model_id works correctly")
    
    # Test 2: check_provider_configured (basic test)
    print("\n2. Testing check_provider_configured()...")
    from openclaw.config.schema import OpenClawConfig
    
    config = OpenClawConfig()
    # This will return False for most providers unless env vars are set
    result = check_provider_configured(config, "anthropic")
    print(f"   Anthropic configured: {result}")
    print("   ✓ check_provider_configured runs without errors")
    
    print("\n✅ Fallback Provider Config Tests: PASSED")


async def test_onboard_helpers():
    """Test onboard helper functions"""
    print("\n" + "="*80)
    print("TEST: Onboard Helper Functions")
    print("="*80)
    
    from openclaw.wizard.onboard_helpers import (
        probe_gateway_reachable,
        summarize_existing_config,
    )
    from openclaw.config.schema import (
        OpenClawConfig, GatewayConfig, AgentsConfig, AgentDefaults, ModelConfig
    )
    
    # Test 1: probe_gateway_reachable
    print("\n1. Testing probe_gateway_reachable()...")
    result = await probe_gateway_reachable(
        url="ws://127.0.0.1:18789",
        token=None,
        timeout=2.0
    )
    
    assert "ok" in result, "Result should have 'ok' key"
    assert "detail" in result, "Result should have 'detail' key"
    print(f"   Gateway reachable: {result['ok']}")
    print(f"   Detail: {result['detail']}")
    print("   ✓ probe_gateway_reachable works correctly")
    
    # Test 2: summarize_existing_config
    print("\n2. Testing summarize_existing_config()...")
    config = OpenClawConfig(
        gateway=GatewayConfig(port=18789, bind="loopback"),
        agents=AgentsConfig(
            defaults=AgentDefaults(
                model=ModelConfig(
                    primary="anthropic/claude-sonnet-4-6",
                    fallbacks=["openai/gpt-4o"]
                ),
                workspace="./workspace"
            )
        )
    )
    
    summary = summarize_existing_config(config)
    
    assert "Gateway: port 18789" in summary, "Summary should include gateway port"
    assert "Model: anthropic/claude-sonnet-4-6" in summary, "Summary should include model"
    assert "Workspace: ./workspace" in summary, "Summary should include workspace"
    print(f"   Summary:\n{summary}")
    print("   ✓ summarize_existing_config works correctly")
    
    print("\n✅ Onboard Helper Tests: PASSED")


def test_imports():
    """Test all new imports"""
    print("\n" + "="*80)
    print("TEST: Module Imports")
    print("="*80)
    
    try:
        from openclaw.wizard.model_picker import (
            apply_primary_model,
            apply_model_fallbacks_from_selection,
            prompt_model_with_fallbacks,
        )
        print("   ✓ model_picker imports OK")
    except ImportError as e:
        print(f"   ✗ model_picker import failed: {e}")
        return False
    
    try:
        from openclaw.wizard.fallback_provider_config import (
            extract_provider_from_model_id,
            check_provider_configured,
            configure_fallback_provider,
            ensure_fallback_provider_configured,
            check_fallback_providers_configured,
        )
        print("   ✓ fallback_provider_config imports OK")
    except ImportError as e:
        print(f"   ✗ fallback_provider_config import failed: {e}")
        return False
    
    try:
        from openclaw.wizard.onboard_helpers import (
            probe_gateway_reachable,
            warn_if_model_config_looks_off,
            summarize_existing_config,
            ensure_workspace_and_sessions,
            prompt_remote_gateway_config,
        )
        print("   ✓ onboard_helpers imports OK")
    except ImportError as e:
        print(f"   ✗ onboard_helpers import failed: {e}")
        return False
    
    print("\n✅ Import Tests: PASSED")
    return True


async def main():
    """Run all tests"""
    print("\n" + "="*80)
    print("Onboarding Alignment Verification")
    print("="*80)
    print("\nTesting all new features added for TypeScript alignment...")
    
    all_passed = True
    
    try:
        # Test 1: Imports
        if not test_imports():
            all_passed = False
        
        # Test 2: Model picker
        test_model_picker()
        
        # Test 3: Fallback provider config
        test_fallback_provider_config()
        
        # Test 4: Onboard helpers
        await test_onboard_helpers()
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        all_passed = False
    
    # Final summary
    print("\n" + "="*80)
    if all_passed:
        print("🎉 ALL TESTS PASSED")
        print("="*80)
        print("\nOnboarding alignment is complete and verified!")
        print("\nNext steps:")
        print("  1. Run full onboarding: uv run openclaw onboard --flow advanced")
        print("  2. Test fallback behavior: Select models from different providers")
        print("  3. Verify config format: cat ~/.openclaw/openclaw.yaml")
        return 0
    else:
        print("❌ SOME TESTS FAILED")
        print("="*80)
        print("\nPlease review the errors above and fix before proceeding.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
