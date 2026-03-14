"""
Configuration wizard for updating specific sections
Allows users to modify configuration after initial onboarding
"""

from __future__ import annotations

from ..config.loader import load_config, save_config
from ..config.schema import OpenClawConfig
from .config import configure_telegram_enhanced, configure_discord_enhanced
from .auth import configure_auth


async def run_configure_wizard(section: str | None = None) -> dict:
    """Run configuration wizard for specific section
    
    Args:
        section: Configuration section to update (gateway, channels, agents, tools)
                If None, will prompt user to choose
    
    Returns:
        Updated configuration dictionary
    """
    print("\n" + "=" * 70)
    print("🔧 OpenClaw Configuration Wizard")
    print("=" * 70)
    
    # Load existing configuration
    try:
        config = load_config()
        print(f"✓ Loaded configuration from {config.__class__.__name__}")
    except Exception as e:
        print(f"Error loading configuration: {e}")
        print("Creating new configuration...")
        config = OpenClawConfig()
    
    # Select section if not provided
    if section is None:
        print("\nWhat would you like to configure?")
        print("  1. gateway    - Gateway settings (port, auth, etc.)")
        print("  2. channels   - Channel configurations (Telegram, Discord, etc.)")
        print("  3. agents     - Agent settings (models, workspace)")
        print("  4. tools      - Tool configurations")
        print("  5. security   - Security settings")
        
        section_choice = input("\nChoose section [1]: ").strip()
        section_map = {
            "1": "gateway",
            "2": "channels",
            "3": "agents",
            "4": "tools",
            "5": "security",
        }
        section = section_map.get(section_choice, "gateway")
    
    # Configure selected section
    if section == "gateway":
        config = await configure_gateway_section(config)
    elif section == "channels":
        config = await configure_channels_section(config)
    elif section == "agents":
        config = await configure_agents_section(config)
    elif section == "tools":
        config = await configure_tools_section(config)
    elif section == "security":
        config = await configure_security_section(config)
    else:
        print(f"Unknown section: {section}")
        return config.model_dump()
    
    # Save configuration
    print("\n" + "-" * 70)
    save_choice = input("Save configuration? [Y/n]: ").strip().lower()
    if save_choice != "n":
        try:
            save_config(config)
            print("✓ Configuration saved!")
        except Exception as e:
            print(f"Error saving configuration: {e}")
    
    print("=" * 70 + "\n")
    
    return config.model_dump()


async def configure_gateway_section(config: OpenClawConfig) -> OpenClawConfig:
    """Configure Gateway section"""
    print("\n" + "=" * 70)
    print("Gateway Configuration")
    print("=" * 70)
    
    # Port
    current_port = config.gateway.port
    port_input = input(f"\nGateway port [{current_port}]: ").strip()
    if port_input:
        config.gateway.port = int(port_input)
    
    # Mode
    print("\nGateway mode:")
    print("  1. local  - Run Gateway on this machine")
    print("  2. remote - Connect to remote Gateway")
    
    current_mode = config.gateway.mode
    mode_input = input(f"Mode [{current_mode}] [1]: ").strip()
    if mode_input == "2":
        config.gateway.mode = "remote"
        
        # Remote URL
        remote_url = input("Remote Gateway URL: ").strip()
        if hasattr(config.gateway, "remote_url"):
            config.gateway.remote_url = remote_url
    else:
        config.gateway.mode = "local"
    
    # Auth
    print("\nGateway authentication:")
    print("  1. token    - Token-based auth (recommended)")
    print("  2. password - Password-based auth")
    print("  3. none     - No authentication (local only)")
    
    auth_choice = input("Auth mode [1]: ").strip()
    if auth_choice == "2":
        config.gateway.auth.mode = "password"
        password = input("Gateway password: ").strip()
        config.gateway.auth.password = password
    elif auth_choice == "3":
        config.gateway.auth.mode = "none"
    else:
        config.gateway.auth.mode = "token"
        token = input("Gateway token (leave empty to generate): ").strip()
        if not token:
            import secrets
            token = secrets.token_urlsafe(32)
            print(f"Generated token: {token}")
        config.gateway.auth.token = token
    
    print("\n✓ Gateway configuration updated")
    return config


async def configure_channels_section(config: OpenClawConfig) -> OpenClawConfig:
    """Configure Channels section"""
    print("\n" + "=" * 70)
    print("Channels Configuration")
    print("=" * 70)
    print("\nWhich channel would you like to configure?")
    print("  1. Telegram")
    print("  2. Discord")
    print("  3. Slack")
    
    channel_choice = input("\nChoose channel [1]: ").strip()
    
    if channel_choice == "2":
        # Discord
        dc_config = configure_discord_enhanced()
        if dc_config:
            config.channels.discord = dc_config
            print("✓ Discord configured")
    elif channel_choice == "3":
        # Slack
        import os
        token = os.getenv("SLACK_BOT_TOKEN") or input("Slack bot token: ").strip()
        if token:
            config.channels.slack = {
                "enabled": True,
                "bot_token": token,
                "dm_policy": "pairing",
            }
            print("✓ Slack configured")
    else:
        # Telegram (default)
        tg_config = configure_telegram_enhanced()
        if tg_config:
            config.channels.telegram = tg_config
            print("✓ Telegram configured")
    
    return config


async def configure_agents_section(config: OpenClawConfig) -> OpenClawConfig:
    """Configure Agents section"""
    print("\n" + "=" * 70)
    print("Agents Configuration")
    print("=" * 70)
    
    # Default model
    print("\nDefault model:")
    print("  1. claude-3-5-sonnet-20241022 (Anthropic)")
    print("  2. gpt-4o (OpenAI)")
    print("  3. gemini-2.0-flash (Google)")
    print("  4. Custom")
    
    model_choice = input("Choose default model [1]: ").strip()
    
    if model_choice == "2":
        config.agents.defaults.model = "gpt-4o"
    elif model_choice == "3":
        config.agents.defaults.model = "gemini-2.0-flash"
    elif model_choice == "4":
        custom_model = input("Enter custom model: ").strip()
        if custom_model:
            config.agents.defaults.model = custom_model
    else:
        config.agents.defaults.model = "claude-3-5-sonnet-20241022"
    
    # Workspace
    current_workspace = config.agents.defaults.workspace or "~/.openclaw/workspace"
    workspace_input = input(f"\nWorkspace directory [{current_workspace}]: ").strip()
    if workspace_input:
        config.agents.defaults.workspace = workspace_input
    
    print("\n✓ Agents configuration updated")
    return config


async def configure_tools_section(config: OpenClawConfig) -> OpenClawConfig:
    """Configure Tools section"""
    print("\n" + "=" * 70)
    print("Tools Configuration")
    print("=" * 70)
    
    # Tool profile
    print("\nTool profile:")
    print("  1. full      - All tools enabled (recommended)")
    print("  2. coding    - Coding-focused tools")
    print("  3. messaging - Messaging-focused tools")
    print("  4. minimal   - Minimal tool set")
    
    profile_choice = input("Choose tool profile [1]: ").strip()
    profile_map = {
        "2": "coding",
        "3": "messaging",
        "4": "minimal",
    }
    config.tools.profile = profile_map.get(profile_choice, "full")
    
    # Bash security
    print("\nBash execution security:")
    print("  1. full      - Full access (no restrictions)")
    print("  2. allowlist - Only approved commands")
    print("  3. deny      - No bash execution")
    
    security_choice = input("Choose security mode [1]: ").strip()
    security_map = {
        "2": "allowlist",
        "3": "deny",
    }
    config.tools.exec.security = security_map.get(security_choice, "full")
    
    print("\n✓ Tools configuration updated")
    return config


async def configure_security_section(config: OpenClawConfig) -> OpenClawConfig:
    """Configure Security section"""
    print("\n" + "=" * 70)
    print("Security Configuration")
    print("=" * 70)
    
    # Gateway auth
    print("\nUpdate Gateway authentication?")
    update_auth = input("[y/N]: ").strip().lower()
    
    if update_auth == "y":
        print("\nAuth mode:")
        print("  1. token    - Token-based (recommended)")
        print("  2. password - Password-based")
        print("  3. none     - No auth (local only, not recommended)")
        
        auth_choice = input("Choose mode [1]: ").strip()
        
        if auth_choice == "2":
            config.gateway.auth.mode = "password"
            password = input("Set password: ").strip()
            config.gateway.auth.password = password
        elif auth_choice == "3":
            config.gateway.auth.mode = "none"
            print("⚠️  Warning: No authentication - only use on trusted networks!")
        else:
            config.gateway.auth.mode = "token"
            regen = input("Generate new token? [y/N]: ").strip().lower()
            if regen == "y":
                import secrets
                token = secrets.token_urlsafe(32)
                config.gateway.auth.token = token
                print(f"New token: {token}")
                print("⚠️  Save this token - you'll need it to connect!")
    
    # Channel DM policies
    print("\nUpdate channel DM policies?")
    update_dm = input("[y/N]: ").strip().lower()
    
    if update_dm == "y":
        for channel_name in ["telegram", "discord", "slack"]:
            channel_cfg = getattr(config.channels, channel_name, None)
            if channel_cfg and channel_cfg.get("enabled"):
                print(f"\n{channel_name.title()} DM policy:")
                print("  1. open      - Allow all users")
                print("  2. pairing   - Require approval (recommended)")
                print("  3. allowlist - Specific users only")
                
                current_policy = channel_cfg.get("dm_policy", "pairing")
                policy_input = input(f"Policy [{current_policy}] [2]: ").strip()
                
                if policy_input == "1":
                    channel_cfg["dm_policy"] = "open"
                elif policy_input == "3":
                    channel_cfg["dm_policy"] = "allowlist"
                    allow_from = input("Allowed user IDs (comma-separated): ").strip()
                    if allow_from:
                        channel_cfg["allow_from"] = [x.strip() for x in allow_from.split(",")]
                else:
                    channel_cfg["dm_policy"] = "pairing"
    
    print("\n✓ Security configuration updated")
    return config