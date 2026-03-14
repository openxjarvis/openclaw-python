"""Test script to verify configuration alignment with TypeScript version

This script tests that:
1. All new configuration fields can be loaded correctly
2. Default values match TS version
3. Alias (camelCase) and snake_case names work correctly
"""
import json
from openclaw.config.schema import (
    CronConfig,
    CronRetryConfig,
    CronRunLogConfig,
    CronFailureAlertConfig,
    CronFailureDestinationConfig,
    UIConfig,
    UIAssistantConfig,
    UpdateConfig,
    UpdateAutoConfig,
    ShellEnvConfig,
    AgentModelEntryConfig,
    MessagesConfig,
)


def test_cron_config():
    """Test CronConfig with all new fields"""
    print("Testing CronConfig...")
    
    # Test with camelCase (from JSON)
    config_dict = {
        "enabled": True,
        "store": "~/.openclaw/cron/jobs.json",
        "maxConcurrentRuns": 2,
        "retry": {
            "maxAttempts": 3,
            "backoffMs": [30000, 60000, 300000],
            "retryOn": ["rate_limit", "network"]
        },
        "sessionRetention": "24h",
        "runLog": {
            "maxBytes": 2000000,
            "keepLines": 2000
        },
        "failureAlert": {
            "enabled": True,
            "after": 2,
            "cooldownMs": 3600000,
            "mode": "announce"
        },
        "failureDestination": {
            "channel": "telegram",
            "mode": "announce"
        }
    }
    
    config = CronConfig(**config_dict)
    
    # Verify fields
    assert config.enabled == True
    assert config.store == "~/.openclaw/cron/jobs.json"
    assert config.max_concurrent_runs == 2
    assert config.retry is not None
    assert config.retry.max_attempts == 3
    assert config.retry.backoff_ms == [30000, 60000, 300000]
    assert config.session_retention == "24h"
    assert config.run_log is not None
    assert config.run_log.max_bytes == 2000000
    assert config.run_log.keep_lines == 2000
    assert config.failure_alert is not None
    assert config.failure_alert.after == 2
    
    print("✅ CronConfig tests passed")


def test_ui_config():
    """Test UIConfig with assistant configuration"""
    print("\nTesting UIConfig...")
    
    config_dict = {
        "seamColor": "#0066cc",
        "assistant": {
            "name": "MyAssistant",
            "avatar": "🤖"
        }
    }
    
    config = UIConfig(**config_dict)
    
    assert config.seam_color == "#0066cc"
    assert config.assistant is not None
    assert config.assistant.name == "MyAssistant"
    assert config.assistant.avatar == "🤖"
    
    print("✅ UIConfig tests passed")


def test_update_config():
    """Test UpdateConfig with auto configuration"""
    print("\nTesting UpdateConfig...")
    
    config_dict = {
        "channel": "stable",
        "checkOnStart": True,
        "auto": {
            "enabled": False,
            "stableDelayHours": 6,
            "stableJitterHours": 12,
            "betaCheckIntervalHours": 1
        }
    }
    
    config = UpdateConfig(**config_dict)
    
    assert config.channel == "stable"
    assert config.check_on_start == True
    assert config.auto is not None
    assert config.auto.enabled == False
    assert config.auto.stable_delay_hours == 6
    assert config.auto.stable_jitter_hours == 12
    assert config.auto.beta_check_interval_hours == 1
    
    print("✅ UpdateConfig tests passed")


def test_shell_env_config():
    """Test ShellEnvConfig with timeoutMs"""
    print("\nTesting ShellEnvConfig...")
    
    config_dict = {
        "enabled": True,
        "timeoutMs": 15000
    }
    
    config = ShellEnvConfig(**config_dict)
    
    assert config.enabled == True
    assert config.timeout_ms == 15000
    
    print("✅ ShellEnvConfig tests passed")


def test_agent_model_entry_config():
    """Test AgentModelEntryConfig"""
    print("\nTesting AgentModelEntryConfig...")
    
    config_dict = {
        "alias": "fast",
        "params": {"cacheRetention": "ephemeral"},
        "streaming": True
    }
    
    config = AgentModelEntryConfig(**config_dict)
    
    assert config.alias == "fast"
    assert config.params == {"cacheRetention": "ephemeral"}
    assert config.streaming == True
    
    print("✅ AgentModelEntryConfig tests passed")


def test_messages_config():
    """Test expanded MessagesConfig"""
    print("\nTesting MessagesConfig...")
    
    config_dict = {
        "messagePrefix": "> ",
        "responsePrefix": "auto",
        "ackReaction": "👀",
        "ackReactionScope": "group-mentions",
        "removeAckAfterReply": True,
        "suppressToolErrors": False
    }
    
    config = MessagesConfig(**config_dict)
    
    assert config.message_prefix == "> "
    assert config.response_prefix == "auto"
    assert config.ack_reaction == "👀"
    assert config.ack_reaction_scope == "group-mentions"
    assert config.remove_ack_after_reply == True
    assert config.suppress_tool_errors == False
    
    print("✅ MessagesConfig tests passed")


def test_default_values():
    """Test that default values match TS version"""
    print("\nTesting default values...")
    
    # CronConfig defaults
    cron = CronConfig()
    assert cron.enabled == True  # Default: True
    assert cron.store is None  # Will use ~/.openclaw/cron/jobs.json at runtime
    assert cron.max_concurrent_runs is None  # Default: 1 at runtime
    
    # UpdateConfig defaults
    update = UpdateConfig()
    assert update.channel == "stable"
    assert update.check_on_start == False
    assert update.auto is None
    
    # ShellEnvConfig defaults
    shell_env = ShellEnvConfig()
    assert shell_env.enabled == False
    assert shell_env.timeout_ms is None  # Default: 15000 at runtime
    
    # MessagesConfig defaults
    messages = MessagesConfig()
    assert messages.ack_reaction_scope == "group-mentions"
    
    print("✅ Default values tests passed")


def test_snake_case_access():
    """Test that both camelCase and snake_case work"""
    print("\nTesting snake_case attribute access...")
    
    # CronConfig: maxConcurrentRuns -> max_concurrent_runs
    cron_dict = {"maxConcurrentRuns": 5}
    cron = CronConfig(**cron_dict)
    assert cron.max_concurrent_runs == 5
    
    # UIConfig: seamColor -> seam_color
    ui_dict = {"seamColor": "#ff0000"}
    ui = UIConfig(**ui_dict)
    assert ui.seam_color == "#ff0000"
    
    # UpdateConfig: checkOnStart -> check_on_start
    update_dict = {"checkOnStart": True}
    update = UpdateConfig(**update_dict)
    assert update.check_on_start == True
    
    print("✅ snake_case access tests passed")


def test_full_config_json():
    """Test loading a complete config JSON with all new fields"""
    print("\nTesting full config JSON loading...")
    
    full_config = {
        "cron": {
            "enabled": True,
            "store": "~/.openclaw/cron/jobs.json",
            "maxConcurrentRuns": 2,
            "retry": {
                "maxAttempts": 3,
                "backoffMs": [30000, 60000, 300000]
            },
            "sessionRetention": "24h",
            "runLog": {
                "maxBytes": "2mb",
                "keepLines": 2000
            },
            "failureAlert": {
                "enabled": True,
                "after": 2,
                "cooldownMs": 3600000
            }
        },
        "ui": {
            "seamColor": "#0066cc",
            "assistant": {
                "name": "TestAssistant",
                "avatar": "🤖"
            }
        },
        "update": {
            "channel": "stable",
            "checkOnStart": True,
            "auto": {
                "enabled": False,
                "stableDelayHours": 6
            }
        },
        "messages": {
            "ackReactionScope": "group-mentions",
            "removeAckAfterReply": True
        }
    }
    
    # Test individual configs
    cron = CronConfig(**full_config["cron"])
    assert cron.retry.max_attempts == 3
    
    ui = UIConfig(**full_config["ui"])
    assert ui.assistant.name == "TestAssistant"
    
    update = UpdateConfig(**full_config["update"])
    assert update.auto.stable_delay_hours == 6
    
    messages = MessagesConfig(**full_config["messages"])
    assert messages.remove_ack_after_reply == True
    
    print("✅ Full config JSON loading tests passed")


def main():
    """Run all tests"""
    print("=" * 60)
    print("Configuration Alignment Tests")
    print("=" * 60)
    
    try:
        test_cron_config()
        test_ui_config()
        test_update_config()
        test_shell_env_config()
        test_agent_model_entry_config()
        test_messages_config()
        test_default_values()
        test_snake_case_access()
        test_full_config_json()
        
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        print("\n配置对齐验证完成！所有新增字段都能正确加载和访问。")
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        raise
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        raise


if __name__ == "__main__":
    main()
