"""Onboarding wizard

First-run onboarding experience for new users.
Matches TypeScript openclaw/src/wizard/onboarding.ts
"""
from __future__ import annotations

# Apply nest_asyncio to allow questionary to work in async contexts
import nest_asyncio
nest_asyncio.apply()

import json
import logging
import secrets
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..agents.model_catalog import load_model_catalog, ModelCatalogEntry
from ..agents.agent_paths import resolve_openclaw_agent_dir
from ..config.loader import load_config, save_config
from ..config.schema import (
    OpenClawConfig, AgentConfig, GatewayConfig, ChannelsConfig, AuthConfig, ModelsConfig,
    TelegramChannelConfig, ChannelConfig, FeishuChannelConfig,
)
from .auth import configure_auth, check_env_api_key
from .config import configure_telegram_enhanced, configure_discord_enhanced, configure_feishu_enhanced, configure_whatsapp_enhanced, configure_agent
from .onboard_hooks import setup_hooks
from .onboard_skills import setup_skills
from .onboard_finalize import finalize_onboarding

logger = logging.getLogger(__name__)


def _config_to_dict(config: "OpenClawConfig") -> dict:
    """Convert OpenClawConfig to dict for skills/hooks setup."""
    if hasattr(config, "model_dump"):
        return config.model_dump(exclude_none=True)
    if isinstance(config, dict):
        return dict(config)
    return {}


def _merge_config_from_dict(config: "OpenClawConfig", d: dict) -> None:
    """Merge skills/hooks from dict back into OpenClawConfig (in-place)."""
    if not d:
        return
    if "skills" in d and d["skills"]:
        from openclaw.config.schema import SkillsConfig
        skills_cfg = d["skills"]
        if isinstance(skills_cfg, dict):
            if config.skills is None:
                config.skills = SkillsConfig()
            entries = skills_cfg.get("entries") or {}
            if entries:
                config.skills.entries = {**(config.skills.entries or {}), **entries}
            install = skills_cfg.get("install")
            if install:
                config.skills.install = {**(config.skills.install or {}), **install}
    if "hooks" in d and d["hooks"]:
        from openclaw.config.schema import HooksConfig, InternalHooksConfig
        hooks_cfg = d["hooks"]
        if isinstance(hooks_cfg, dict):
            internal = hooks_cfg.get("internal") or {}
            if internal and isinstance(internal, dict):
                if config.hooks is None:
                    config.hooks = HooksConfig(internal=InternalHooksConfig(enabled=True, entries={}))
                entries = internal.get("entries") or {}
                if entries and config.hooks.internal:
                    config.hooks.internal.entries = {**(config.hooks.internal.entries or {}), **entries}
                    config.hooks.internal.enabled = internal.get("enabled", True)


async def check_gateway_health(port: int = 18789, token: Optional[str] = None) -> dict:
    """Check if Gateway is reachable and healthy
    
    Args:
        port: Gateway port
        token: Authentication token (if required)
    
    Returns:
        Dict with 'ok' (bool) and 'detail' (str) keys
    """
    try:
        import httpx
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            
            response = await client.get(
                f"http://127.0.0.1:{port}/health",
                headers=headers
            )
            response.raise_for_status()
            return {"ok": True, "detail": "Gateway reachable"}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


async def run_onboarding_wizard(
    config: Optional[dict] = None,
    workspace_dir: Optional[Path] = None,
    install_daemon: Optional[bool] = None,
    skip_health: bool = False,
    skip_ui: bool = False,
    non_interactive: bool = False,
    accept_risk: bool = False,
    flow: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> dict:
    """
    Run onboarding wizard
    
    Guides new users through initial setup:
    - Risk confirmation
    - Mode selection (QuickStart/Advanced)
    - API key configuration
    - Model selection
    - Gateway configuration
    - Channel setup
    - Gateway service installation (optional)
    
    Args:
        config: Existing Gateway configuration (optional)
        workspace_dir: Workspace directory (optional)
        install_daemon: Whether to install Gateway service (None=auto-decide based on flow)
        skip_health: Skip health check after installation
        skip_ui: Skip UI selection prompts
        non_interactive: Run without prompts (requires accept_risk=True)
        accept_risk: Accept risk acknowledgement
        flow: Onboarding flow type: "quickstart" or "advanced"
        
    Returns:
        Dict with wizard results
    """
    logger.info("Starting onboarding wizard")
    
    print("\n" + "=" * 80)
    print("🚀 Welcome to OpenClaw Onboarding!")
    print("=" * 80)
    print("\nThis wizard will help you set up OpenClaw for the first time.")
    print("You can exit anytime with Ctrl+C")
    
    # Step 1: Risk confirmation
    if non_interactive:
        if not accept_risk:
            print("[red]Error:[/red] --accept-risk is required for --non-interactive mode")
            return {"completed": False, "skipped": True, "reason": "Risk not accepted"}
    else:
        if not _confirm_risks():
            return {"completed": False, "skipped": True, "reason": "User declined"}
    
    # Step 2: Mode selection
    if flow:
        flow_normalized = flow.lower().strip()
        if flow_normalized in ["quickstart", "advanced"]:
            mode = flow_normalized
            print(f"\n✓ Using {mode} mode")
        else:
            print(f"[yellow]Warning:[/yellow] Invalid --flow value '{flow}'. Using interactive mode selection.")
            mode = _select_mode()
    else:
        mode = _select_mode()
    
    # Step 3: Load or create config
    try:
        existing_config_dict = load_config(as_dict=True)
        print("\n✓ Found existing configuration")
        
        if mode == "quickstart":
            print("QuickStart mode: Using existing configuration as base")
            # Convert dict to OpenClawConfig object
            claw_config = OpenClawConfig(**existing_config_dict) if existing_config_dict else OpenClawConfig()
            # NOTE: QuickStart will preserve old model config until provider auth step updates it
        else:
            action = _prompt_config_action()
            if action == "reset":
                print("Creating fresh configuration...")
                claw_config = OpenClawConfig()
            elif action == "modify":
                print("Modifying existing configuration...")
                # Convert dict to OpenClawConfig object
                claw_config = OpenClawConfig(**existing_config_dict) if existing_config_dict else OpenClawConfig()
            else:  # keep
                print("Keeping existing configuration...")
                return {"completed": True, "skipped": False, "kept_existing": True}
    except Exception as e:
        logger.info(f"No existing config: {e}")
        print("\nCreating new configuration...")
        claw_config = OpenClawConfig()
    
    # Step 4: Provider configuration
    # Note: _configure_provider now directly modifies claw_config via handler chain
    provider_config = await _configure_provider(mode, claw_config)
    if provider_config:
        # Config already updated by handlers, just extract provider info
        # The model is already written to agents.defaults.model by handlers
        _provider_name = provider_config.get("provider")
        
        # No need to re-write model here - handlers already did it
        # Just handle any special provider-specific config that wasn't in the original code path
    
    # Step 4.5: Interactive model selection (NEW - aligns with TS onboarding)
    # After provider auth is set up, let user choose the specific model
    # IMPORTANT: This step should ALWAYS run after provider config (unless skipped)
    if provider_config and not non_interactive:
        auth_choice = provider_config.get("auth_choice")
        
        # Skip model selection only for these specific cases
        skip_model_selection = (
            auth_choice in ("skip", None) or
            auth_choice == "custom-api-key"  # Custom provider already selected model
        )
        
        # IMPORTANT: For kimi-code-api-key in QuickStart, auto-set model without prompting
        if auth_choice == "kimi-code-api-key" and mode == "quickstart":
            # Auto-set kimi-coding/k2p5 for QuickStart + Kimi Code
            if not claw_config.agents:
                from openclaw.config.schema import AgentsConfig
                claw_config.agents = AgentsConfig()
            if not claw_config.agents.defaults:
                from openclaw.config.schema import AgentDefaults
                claw_config.agents.defaults = AgentDefaults()
            if not claw_config.agent:
                from openclaw.config.schema import AgentConfig
                claw_config.agent = AgentConfig()
            
            claw_config.agents.defaults.model = "kimi-coding/k2p5"
            claw_config.agent.model = "kimi-coding/k2p5"
            print(f"\n✓ Auto-selected model: kimi-coding/k2p5 (Kimi Code default)")
            skip_model_selection = True
        
        if not skip_model_selection:
            from .model_picker import prompt_default_model
            
            print("\n" + "-" * 80)
            print("Model Selection")
            print("-" * 80)
            print("\nChoose which model to use by default.")
            
            # Extract provider name from auth_choice
            # auth_choice can be like "kimi-code-api-key", "moonshot-api-key", "apiKey", etc.
            # We need to map it to the provider name used in _PROVIDER_MODELS
            provider_name = None
            if auth_choice:
                # Common mappings
                if "moonshot" in auth_choice or "kimi" in auth_choice:
                    provider_name = "moonshot"
                elif "anthropic" in auth_choice or auth_choice == "apiKey":
                    provider_name = "anthropic"
                elif "openai" in auth_choice:
                    provider_name = "openai"
                elif "gemini" in auth_choice or "google" in auth_choice:
                    provider_name = "google"
                elif "xai" in auth_choice:
                    provider_name = "xai"
                elif "mistral" in auth_choice:
                    provider_name = "mistral"
                elif "qianfan" in auth_choice:
                    provider_name = "qianfan"
                elif "zai" in auth_choice:
                    provider_name = "zai"
                elif "xiaomi" in auth_choice:
                    provider_name = "xiaomi"
                elif "openrouter" in auth_choice:
                    provider_name = "openrouter"
                elif "huggingface" in auth_choice:
                    provider_name = "huggingface"
                elif "together" in auth_choice:
                    provider_name = "together"
                elif "litellm" in auth_choice:
                    provider_name = "litellm"
                elif "venice" in auth_choice:
                    provider_name = "venice"
                elif "synthetic" in auth_choice:
                    provider_name = "synthetic"
                elif "volcengine" in auth_choice:
                    provider_name = "volcengine"
                elif "byteplus" in auth_choice:
                    provider_name = "byteplus"
                elif "ollama" in auth_choice:
                    provider_name = "ollama"
            
            try:
                model_result = await prompt_default_model(
                    config=claw_config,
                    allow_keep=False,  # No existing model to keep in fresh onboarding
                    include_manual=True,
                    include_vllm=(mode == "advanced"),
                    preferred_provider=provider_name,
                    message="Select default model:",
                )
                
                if model_result.get("model"):
                    selected_model = model_result["model"]
                    
                    # Update config with selected model
                    if not claw_config.agents:
                        from openclaw.config.schema import AgentsConfig
                        claw_config.agents = AgentsConfig()
                    if not claw_config.agents.defaults:
                        from openclaw.config.schema import AgentDefaults
                        claw_config.agents.defaults = AgentDefaults()
                    
                    claw_config.agents.defaults.model = selected_model
                    
                    # Also update legacy agent.model
                    if not claw_config.agent:
                        from openclaw.config.schema import AgentConfig
                        claw_config.agent = AgentConfig()
                    
                    # Extract model name (strip provider prefix if present)
                    model_name = selected_model.split("/")[1] if "/" in selected_model else selected_model
                    claw_config.agent.model = model_name
                    
                    print(f"✓ Default model set to {selected_model}")
                    
                    # Step 4.6: Fallback models (optional, 循环添加)
                    fallback_models = []
                    max_fallbacks = 3
                    
                    while len(fallback_models) < max_fallbacks:
                        try:
                            count_msg = f" (already have {len(fallback_models)})" if fallback_models else ""
                            add_fallback = prompter.confirm(
                                f"Add fallback model?{count_msg}",
                                default=False
                            )
                        except Exception:
                            add_fb = input(f"\nAdd fallback model?{count_msg} [y/N]: ").strip().lower()
                            add_fallback = (add_fb == "y")
                        
                        if not add_fallback:
                            break
                        
                        # 选择 fallback model（不过滤 provider，允许跨 provider）
                        try:
                            excluded_models = [selected_model] + fallback_models
                            fallback_result = await prompt_default_model(
                                config=claw_config,
                                allow_keep=False,
                                include_manual=True,
                                include_vllm=False,
                                preferred_provider=None,  # 允许所有 providers
                                message=f"Select fallback model #{len(fallback_models) + 1}:",
                                exclude_models=excluded_models,
                            )
                            
                            if fallback_result.get("model"):
                                fb_model = fallback_result["model"]
                                
                                # Check if provider is configured, prompt to configure if not
                                from .fallback_provider_config import ensure_fallback_provider_configured
                                provider_configured = await ensure_fallback_provider_configured(
                                    config=claw_config,
                                    model_id=fb_model,
                                    interactive=True
                                )
                                
                                if provider_configured:
                                    fallback_models.append(fb_model)
                                    print(f"✓ Added fallback: {fb_model}")
                                else:
                                    print(f"⚠️  Skipped fallback: {fb_model} (provider not configured)")
                            else:
                                # User cancelled or kept current
                                break
                        except Exception as e:
                            logger.warning(f"Fallback model selection failed: {e}")
                            break
                    
                    # 更新配置：如果有 fallback，使用 primary + fallbacks 格式
                    if fallback_models:
                        from openclaw.config.schema import ModelConfig
                        claw_config.agents.defaults.model = ModelConfig(
                            primary=selected_model,
                            fallbacks=fallback_models
                        )
                        print(f"\n✓ Model configuration:")
                        print(f"  Primary: {selected_model}")
                        print(f"  Fallbacks: {', '.join(fallback_models)}")
                    # 否则保持单模型字符串格式
                    else:
                        claw_config.agents.defaults.model = selected_model
            except Exception as e:
                logger.error(f"Model selection failed: {e}", exc_info=True)
                print("⚠️  Could not complete model selection. Using provider default.")
    
    # Step 5: Agent configuration
    if mode == "advanced":
        agent_config = await _configure_agent_settings()
        if agent_config:
            if not claw_config.agents:
                from openclaw.config.schema import AgentsConfig
                claw_config.agents = AgentsConfig()
            if not claw_config.agents.defaults:
                from openclaw.config.schema import AgentDefaults
                claw_config.agents.defaults = AgentDefaults()
            claw_config.agents.defaults.workspace = agent_config.get("workspace", "./workspace")
    
    # Step 6: Gateway configuration
    gateway_config = await _configure_gateway(mode)
    if gateway_config:
        if not claw_config.gateway:
            claw_config.gateway = GatewayConfig()
        claw_config.gateway.port = gateway_config.get("port", 18789)
        claw_config.gateway.bind = gateway_config.get("bind", "loopback")
        
        # Handle authentication properly
        if "auth_token" in gateway_config or "auth_password" in gateway_config:
            if not claw_config.gateway.auth:
                claw_config.gateway.auth = AuthConfig()
            
            if "auth_token" in gateway_config:
                claw_config.gateway.auth.token = gateway_config["auth_token"]
                claw_config.gateway.auth.mode = "token"
            if "auth_password" in gateway_config:
                claw_config.gateway.auth.password = gateway_config["auth_password"]
                claw_config.gateway.auth.mode = "password"
    
    # Step 7: Channels configuration
    channels_config = await _configure_channels(mode)
    if channels_config:
        if not claw_config.channels:
            claw_config.channels = ChannelsConfig()
        if "telegram" in channels_config:
            tg = channels_config["telegram"]
            claw_config.channels.telegram = (
                TelegramChannelConfig.model_validate(tg) if isinstance(tg, dict) else tg
            )
        if "discord" in channels_config:
            dc = channels_config["discord"]
            claw_config.channels.discord = (
                ChannelConfig.model_validate(dc) if isinstance(dc, dict) else dc
            )
        if "whatsapp" in channels_config:
            wa = channels_config["whatsapp"]
            claw_config.channels.whatsapp = (
                ChannelConfig.model_validate(wa) if isinstance(wa, dict) else wa
            )
        if "feishu" in channels_config:
            fs = channels_config["feishu"]
            claw_config.channels.feishu = (
                FeishuChannelConfig.model_validate(fs) if isinstance(fs, dict) else fs
            )
    
    # Step 7.8: Permission level
    if not non_interactive:
        chosen_preset = await _configure_permissions(mode, claw_config)
        if chosen_preset:
            from .permission_presets import apply_preset
            claw_config = apply_preset(claw_config, chosen_preset)

    # Step 7.5: Collect user information for workspace
    user_info = {}
    if not non_interactive and (mode == "advanced" or mode == "quickstart"):
        print("\n" + "-" * 80)
        print("User Profile Setup")
        print("-" * 80)
        print("\nLet's personalize your experience.")
        
        # User name
        try:
            user_name = text("What's your name? (Optional)", default="")
        except Exception:
            user_name = input("\nWhat's your name? [Optional, press Enter to skip]: ").strip()
        
        if user_name:
            user_info["name"] = user_name
            try:
                user_info["what_to_call_them"] = text(
                    f"How should the agent address you?",
                    default=user_name
                )
            except Exception:
                user_info["what_to_call_them"] = input(f"How should the agent address you? [{user_name}]: ").strip() or user_name
        
        # Timezone
        import datetime
        try:
            local_tz = datetime.datetime.now().astimezone().tzinfo
            tz_str = str(local_tz)
        except Exception:
            tz_str = "UTC"
        
        try:
            user_timezone = text("Your timezone?", default=tz_str)
        except Exception:
            user_timezone = input(f"Your timezone? [{tz_str}]: ").strip() or tz_str
        user_info["timezone"] = user_timezone
        
        # Agent personality preference
        personality_choices = [
            {"name": "1. Professional - Formal and focused", "value": "1"},
            {"name": "2. Friendly - Warm and conversational", "value": "2"},
            {"name": "3. Concise - Brief and to the point", "value": "3"},
            {"name": "4. Custom - I'll configure it later", "value": "4"},
        ]
        
        try:
            from .prompter import select
            personality_choice = select(
                "What kind of agent personality do you prefer?",
                choices=personality_choices,
                default="2"
            )
        except Exception:
            print("\nWhat kind of agent personality do you prefer?")
            print("  1. Professional - Formal and focused")
            print("  2. Friendly - Warm and conversational")
            print("  3. Concise - Brief and to the point")
            print("  4. Custom - I'll configure it later")
            personality_choice = input("\nSelect [2]: ").strip() or "2"
        
        personality_map = {
            "1": "professional",
            "2": "friendly",
            "3": "concise",
            "4": "custom"
        }
        user_info["preferred_vibe"] = personality_map.get(personality_choice, "friendly")
    
    # Store user info for later use
    if user_info:
        # Store in a temporary attribute on config
        claw_config._user_info = user_info
    
    # Step 8: Save configuration
    print("\n" + "-" * 80)
    print("Configuration Summary:")
    print("-" * 80)
    print(f"Provider: {provider_config.get('provider', 'Not configured') if provider_config else 'Not configured'}")
    _m = (claw_config.agents.defaults.model if claw_config.agents and claw_config.agents.defaults else None) or (claw_config.agent.model if claw_config.agent else "Default")
    if isinstance(_m, dict):
        print(f"Model: {_m.get('primary', '')}  (fallbacks: {', '.join(_m.get('fallbacks', []))})")
    else:
        print(f"Model: {_m}")
    print(f"Gateway Port: {claw_config.gateway.port if claw_config.gateway else 18789}")
    print(f"Gateway Bind: {claw_config.gateway.bind if claw_config.gateway else 'loopback'}")
    if channels_config:
        if "telegram" in channels_config:
            print("✓ Telegram configured")
        if "discord" in channels_config:
            print("✓ Discord configured")
    print("-" * 80)
    
    try:
        from .prompter import confirm
        save_choice = confirm("Save this configuration?", default=True)
        if not save_choice:
            print("Configuration not saved. Exiting...")
            return {"completed": False, "skipped": True, "reason": "User chose not to save"}
    except Exception:
        save_choice = input("\nSave this configuration? [Y/n]: ").strip().lower()
        if save_choice == "n":
            print("Configuration not saved. Exiting...")
            return {"completed": False, "skipped": True, "reason": "User chose not to save"}
    
    # Add TS-aligned configuration fields
    from datetime import datetime, timezone
    from openclaw.config.schema import (
        WizardConfig, MessagesConfig, CommandsConfig, HooksConfig,
        InternalHooksConfig, CompactionConfig, SubagentsConfig,
        AgentDefaults, AgentsConfig, GatewayTailscaleConfig, GatewayNodesConfig,
        PluginsConfig, PluginEntryConfig,
    )

    # --- agents.defaults: compaction, maxConcurrent, subagents ---
    if not claw_config.agents:
        claw_config.agents = AgentsConfig()
    if not claw_config.agents.defaults:
        claw_config.agents.defaults = AgentDefaults()
    _defs = claw_config.agents.defaults
    if _defs.compaction is None:
        _defs.compaction = CompactionConfig(mode="safeguard")
    if _defs.maxConcurrent is None:
        _defs.maxConcurrent = 4
    if _defs.subagents is None:
        _defs.subagents = SubagentsConfig(maxConcurrent=8)
    elif _defs.subagents.maxConcurrent is None:
        _defs.subagents.maxConcurrent = 8

    # --- gateway: tailscale + nodes defaults ---
    if not claw_config.gateway:
        claw_config.gateway = GatewayConfig()
    if claw_config.gateway.tailscale is None:
        claw_config.gateway.tailscale = GatewayTailscaleConfig(mode="off", reset_on_exit=False)
    if claw_config.gateway.nodes is None:
        claw_config.gateway.nodes = GatewayNodesConfig(
            deny_commands=[
                "camera.snap", "camera.clip", "screen.record",
                "calendar.add", "contacts.add", "reminders.add",
            ]
        )

    # --- plugins.entries: mark telegram plugin enabled when configured ---
    _has_telegram = bool(
        claw_config.channels
        and claw_config.channels.telegram
        and claw_config.channels.telegram.enabled
    )
    if _has_telegram:
        if claw_config.plugins is None:
            claw_config.plugins = PluginsConfig()
        if claw_config.plugins.entries is None:
            claw_config.plugins.entries = {}
        claw_config.plugins.entries["telegram"] = PluginEntryConfig(enabled=True)

    # --- wizard: map 'quickstart' → 'local' to match TS lastRunMode values ---
    _last_run_mode = "local" if mode == "quickstart" else mode

    if not claw_config.wizard:
        claw_config.wizard = WizardConfig(
            lastRunAt=datetime.now(timezone.utc).isoformat(),
            lastRunVersion="0.6.0",
            lastRunCommand="onboard",
            lastRunMode=_last_run_mode,
        )
    else:
        claw_config.wizard.last_run_at = datetime.now(timezone.utc).isoformat()
        claw_config.wizard.last_run_command = "onboard"
        claw_config.wizard.last_run_mode = _last_run_mode

    if not claw_config.messages:
        claw_config.messages = MessagesConfig(ackReactionScope="group-mentions")

    if not claw_config.commands:
        claw_config.commands = CommandsConfig(native="auto", nativeSkills="auto")

    if not claw_config.hooks:
        claw_config.hooks = HooksConfig(
            internal=InternalHooksConfig(
                enabled=True,
                entries={
                    "boot-md": {"enabled": True},
                    "bootstrap-extra-files": {"enabled": True},
                    "command-logger": {"enabled": True},
                    "session-memory": {"enabled": True},
                }
            )
        )
    else:
        # TS does not write top-level hooks.enabled — remove it from output by
        # setting to None so exclude_none=True drops it during serialization.
        # The internal hooks block is the authoritative enablement signal.
        claw_config.hooks.enabled = None  # type: ignore[assignment]
    
    try:
        save_config(claw_config)
        print("✓ Configuration saved!")
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        print(f"✗ Error saving configuration: {e}")
        return {"completed": False, "error": str(e)}
    
    # Step 9: Mark onboarding complete
    if workspace_dir:
        mark_onboarding_complete(workspace_dir)
    else:
        mark_onboarding_complete(Path.home() / ".openclaw" / "workspace")
    
    # Step 9.2: Ensure root directories (identity, delivery-queue, canvas, completions, logs)
    print("\n" + "~" * 60)
    print("Initializing directories...")
    print("~" * 60)
    
    try:
        from ..agents.ensure_root_dirs import ensure_root_directories
        
        # Determine OpenClaw root directory (parent of workspace)
        root_dir = workspace_dir.parent if workspace_dir else (Path.home() / ".openclaw")
        
        # Ensure all root directories
        root_result = ensure_root_directories(root_dir)
        
        if "identity" in root_result and "device_id" in root_result["identity"]:
            print(f"✓ Created device identity: {root_result['identity']['device_id']}")
        if "delivery_queue" in root_result:
            print("✓ Created delivery queue directory")
        if "completions" in root_result:
            print("✓ Created shell completion scripts")
        if "canvas" in root_result:
            print("✓ Created canvas with index.html")
        if "logs" in root_result:
            print("✓ Created log directories")
            
    except Exception as e:
        logger.warning(f"Failed to ensure root directories: {e}")
        print("⚠ Could not create some directories (non-fatal)")
    
    # Step 9.3: Populate workspace with user information
    if hasattr(claw_config, '_user_info') and claw_config._user_info:
        print("\n" + "~" * 60)
        print("Personalizing workspace...")
        print("~" * 60)
        
        try:
            from ..agents.populate_workspace import populate_user_md, populate_soul_md, populate_identity_md
            from ..agents.ensure_workspace_and_sessions import ensure_workspace_and_sessions
            
            # Determine workspace directory
            ws_dir = workspace_dir or (Path.home() / ".openclaw" / "workspace")
            
            # Ensure workspace and sessions exist (unified)
            ensure_workspace_and_sessions(
                workspace_dir=ws_dir,
                skip_bootstrap=False
            )
            
            # Write user info
            populate_user_md(ws_dir, claw_config._user_info)
            print("✓ Created USER.md with your information")
            
            # Update SOUL.md with vibe
            if "preferred_vibe" in claw_config._user_info:
                populate_soul_md(ws_dir, claw_config._user_info["preferred_vibe"])
                print("✓ Updated SOUL.md with your preferences")
            
            # Create IDENTITY.md placeholder
            populate_identity_md(ws_dir)
            print("✓ Created IDENTITY.md template")
            
            print(f"\n✓ Workspace files created at: {ws_dir}")
            print("  You can edit these files anytime to customize your agent's behavior.")
            
        except Exception as e:
            logger.warning(f"Failed to populate workspace: {e}")
            print("⚠ Could not auto-populate workspace files")
            print(f"  You can manually edit files in: {ws_dir if 'ws_dir' in locals() else workspace_dir}")
    
    # Step 9.4: Ensure workspace exists, then configure skills and hooks (after workspace init)
    ws_dir = workspace_dir or (Path.home() / ".openclaw" / "workspace")
    hooks_result: dict = {}
    skills_result: dict = {}
    try:
        from ..agents.ensure_workspace_and_sessions import ensure_workspace_and_sessions
        ensure_workspace_and_sessions(
            workspace_dir=ws_dir,
            skip_bootstrap=False,
        )
    except Exception as e:
        logger.warning(f"Failed to ensure workspace: {e}")
    
    # Skills and hooks setup (TS order: after workspace, before finalize)
    try:
        cfg_dict = _config_to_dict(claw_config)
        hooks_result = await setup_hooks(workspace_dir=ws_dir, config=cfg_dict, mode=mode)
        skills_result = await setup_skills(workspace_dir=ws_dir, config=cfg_dict, mode=mode)
        
        # Merge skills/hooks config updates back into claw_config
        if skills_result.get("config"):
            _merge_config_from_dict(claw_config, skills_result["config"])
        if hooks_result.get("config"):
            _merge_config_from_dict(claw_config, hooks_result["config"])
        
        if skills_result.get("config") or hooks_result.get("config"):
            try:
                save_config(claw_config)
                print("✓ Skills/hooks configuration saved!")
            except Exception as e:
                logger.warning(f"Failed to save skills/hooks config: {e}")
    except Exception as e:
        logger.warning(f"Skills/hooks setup failed: {e}")
    
    # Step 9.5: Install Gateway service (if requested)
    gateway_installed = False
    gateway_running = False
    
    # Determine if we should install daemon
    should_install_daemon = install_daemon
    if should_install_daemon is None:
        # Auto-decide based on flow: quickstart installs by default
        should_install_daemon = (mode == "quickstart")
    
    if should_install_daemon:
        print("\n" + "~" * 60)
        print("Installing Gateway Service...")
        print("~" * 60)
        
        try:
            from ..cli.daemon_cmd import (
                is_service_installed,
                is_service_running,
                install_service_programmatic,
                start_service_programmatic,
                stop_service_programmatic,
            )
            gateway_port = claw_config.gateway.port if claw_config.gateway else 18789
            
            # Check if already installed
            if is_service_installed():
                if non_interactive or mode == "quickstart":
                    action = "R"  # Auto-restart in non-interactive/quickstart
                    print("Gateway service already installed. Restarting...")
                else:
                    action_choices = [
                        {"name": "Restart - Restart the existing service", "value": "R"},
                        {"name": "Reinstall - Remove and reinstall", "value": "I"},
                        {"name": "Skip - Keep as is", "value": "S"},
                    ]
                    
                    try:
                        from .prompter import select
                        action = select(
                            "Gateway service already installed:",
                            choices=action_choices,
                            default="R"
                        )
                    except Exception:
                        action = input("\nGateway service already installed. [R]estart / Re[i]nstall / [S]kip? [R]: ").strip().upper() or "R"
                
                if action == "S":
                    print("Skipping Gateway service installation")
                    should_install_daemon = False
                    gateway_installed = True
                    gateway_running = is_service_running()
                elif action in ("R", "I"):
                    print("Stopping existing service...")
                    stop_service_programmatic()
                    if action == "I":
                        print("Uninstalling existing service...")
                        from ..cli.daemon_cmd import daemon_uninstall
                        try:
                            daemon_uninstall(json_output=False)
                        except SystemExit:
                            pass
                    print("Installing Gateway service...")
                    ok = install_service_programmatic(port=gateway_port)
                    if ok:
                        print("✓ Gateway service installed and started")
                        gateway_installed = True
                        gateway_running = True
                    else:
                        print("✗ Gateway service install failed")
                        gateway_running = False
            
            if should_install_daemon and not gateway_installed:
                print("Installing Gateway service...")
                ok = install_service_programmatic(port=gateway_port)
                if ok:
                    print("✓ Gateway service installed")
                    gateway_installed = True
                    gateway_running = True
                else:
                    print("✗ Gateway service install failed")
                    print("You can install it manually with:")
                    print("  $ uv run openclaw daemon install")
                    gateway_installed = False
                    gateway_running = False
                    
        except Exception as e:
            logger.error(f"Failed to install/start Gateway: {e}")
            print(f"✗ Gateway service installation failed: {e}")
            print("\nYou can install it manually later with:")
            print("  $ uv run openclaw daemon install")
            print("  $ uv run openclaw gateway start")
            gateway_installed = False
            gateway_running = False
    
    # Step 9.6: Health check
    gateway_port = claw_config.gateway.port if claw_config.gateway else 18789
    gateway_token = None
    if claw_config.gateway and claw_config.gateway.auth:
        gateway_token = claw_config.gateway.auth.token
    
    if not skip_health and gateway_running:
        print("\nRunning health check...")
        
        # Wait a bit for service to start
        import asyncio
        await asyncio.sleep(2)
        
        health = await check_gateway_health(
            port=gateway_port,
            token=gateway_token
        )
        
        if health["ok"]:
            print("✓ Gateway is healthy")
        else:
            print(f"⚠ Gateway health check failed: {health['detail']}")
            print("Troubleshooting: https://docs.openclaw.ai/gateway/troubleshooting")
    
    # Step 9.7: UI selection prompt (optional, for advanced mode)
    if not skip_ui and gateway_running and mode == "advanced" and not non_interactive:
        ui_choices = [
            {"name": "1. Open Web UI (recommended)", "value": "1"},
            {"name": "2. Start interactive chat", "value": "2"},
            {"name": "3. Do this later", "value": "3"},
        ]
        
        try:
            from .prompter import select
            choice = select("How would you like to start?", choices=ui_choices, default="1")
        except Exception:
            print("\n" + "~" * 60)
            print("How would you like to start?")
            print("~" * 60)
            print("  1. Open Web UI (recommended)")
            print("  2. Start interactive chat")
            print("  3. Do this later")
            choice = input("\nSelect option [1]: ").strip() or "1"
        
        if choice == "1":
            import webbrowser
            url = f"http://localhost:{gateway_port}"
            print(f"\nOpening {url} in your browser...")
            if webbrowser.open(url):
                print("✓ Opened Web UI in your browser")
            else:
                print(f"⚠ Could not open browser automatically. Please visit: {url}")
        elif choice == "2":
            print("\n💬 Interactive chat mode:")
            print("Use the following command to start chatting:")
            print(f"  $ uv run openclaw chat --interactive")
            print("\nOr send a single message:")
            print(f"  $ uv run openclaw chat 'Hello!'")
        else:
            print("\n✓ You can access the Web UI anytime:")
            print(f"  http://localhost:{gateway_port}")
    
    # Step 10: Display comprehensive next steps
    print("\n" + "=" * 80)
    print("🎉 Onboarding Complete!")
    print("=" * 80)
    
    # Gateway information
    gateway_port = claw_config.gateway.port if claw_config.gateway else 18789
    gateway_token = None
    if claw_config.gateway and claw_config.gateway.auth:
        gateway_token = claw_config.gateway.auth.token
    
    print("\n📡 Gateway Information:")
    print(f"  Port: {gateway_port}")
    print(f"  Web UI: http://localhost:{gateway_port}")
    print(f"  WebSocket: ws://localhost:{gateway_port}")
    if gateway_token:
        print(f"  Auth token: {gateway_token[:20]}... (stored in config)")
        print(f"  View full token: uv run openclaw config get gateway.auth.token")
    
    # Service status
    if gateway_installed and gateway_running:
        print("\n✓ Gateway service: Installed and Running")
        print("  Manage with:")
        print("    $ uv run openclaw gateway status")
        print("    $ uv run openclaw gateway stop")
        print("    $ uv run openclaw gateway restart")
    elif gateway_installed:
        print("\n⚠ Gateway service: Installed but not running")
        print("  Start with:")
        print("    $ uv run openclaw gateway start")
    else:
        print("\n⚠ Gateway service: Not installed")
        print("  Install with:")
        print("    $ uv run openclaw gateway install")
        print("    $ uv run openclaw gateway start")
    
    # Channels
    if channels_config:
        print("\n📱 Configured Channels:")
        if "telegram" in channels_config:
            print("  ✓ Telegram - Open Telegram and message your bot")
        if "discord" in channels_config:
            print("  ✓ Discord - Invite your bot to a server")
    
    # Next steps
    print("\n🚀 Next Steps:")
    if not gateway_running:
        print("  1. Start the Gateway:")
        print("     $ uv run openclaw gateway start")
        print()
        print("  2. Open the Control UI:")
        print(f"     http://localhost:{gateway_port}")
        if gateway_token:
            print("     (Paste the auth token if prompted)")
    else:
        print(f"  1. Open the Control UI: http://localhost:{gateway_port}")
        if gateway_token:
            print("     (Paste the auth token if prompted)")
        print()
        print("  2. Or use the CLI:")
        print("     $ uv run openclaw chat 'Hello!'")
        print("     $ uv run openclaw chat --interactive")
    
    print("\n📚 Documentation:")
    print("  Getting started: https://docs.openclaw.ai/getting-started")
    print("  Configuration:   https://docs.openclaw.ai/gateway/configuration")
    print("  Security:        https://docs.openclaw.ai/security")
    print("  Troubleshooting: https://docs.openclaw.ai/gateway/troubleshooting")
    
    print("\n💡 Useful Commands:")
    print("  View config:     uv run openclaw config show")
    print("  Health check:    uv run openclaw doctor")
    print("  List channels:   uv run openclaw channels list")
    print("  List cron jobs:  uv run openclaw cron list")
    print("  View logs:       uv run openclaw logs tail")
    
    # Check if BOOTSTRAP.md exists
    ws_dir = workspace_dir or (Path.home() / ".openclaw" / "workspace")
    bootstrap_path = ws_dir / "BOOTSTRAP.md"
    if bootstrap_path.exists():
        print("\n🎯 First-time Setup:")
        print("  When you first chat with your agent, it will guide you through:")
        print("  - Choosing the agent's name and personality")
        print("  - Refining your preferences")
        print("  - Setting up any additional details")
        print("\n  This is a one-time conversation to help the agent learn about you.")
    
    print("\n⚡ Tips:")
    print("  - Install globally to avoid typing 'uv run':")
    print("    $ uv pip install -e .")
    print("  - Enable shell completion:")
    print("    $ uv run openclaw completion --install")
    print("  - Run in foreground to see logs:")
    print("    $ uv run openclaw start")
    
    print("\n🔒 Security:")
    print("  Run security audit: uv run openclaw security audit")
    print("  Docs: https://docs.openclaw.ai/security")
    
    print("\n" + "=" * 80 + "\n")
    
    # Step 11: Finalize (skills/hooks already done in Step 9.4)
    finalize_result = await finalize_onboarding(mode=mode, skip_ui=skip_ui)

    logger.info("Onboarding wizard complete")
    
    return {
        "completed": True,
        "skipped": False,
        "mode": mode,
        "provider": provider_config.get("provider") if provider_config else None,
        "hooks": hooks_result,
        "skills": skills_result,
        "finalize": finalize_result,
    }


def _confirm_risks() -> bool:
    """Confirm user understands risks"""
    print("\n" + "-" * 80)
    print("⚠️  Important: Security & Risks")
    print("-" * 80)
    print("""
OpenClaw is an AI agent that can:
  • Execute commands on your system
  • Read and modify files
  • Make network requests
  • Use your API keys

Please ensure:
  ✓ You trust the codebase
  ✓ You understand the permissions granted
  ✓ You review agent actions carefully
  ✓ You keep your API keys secure

This is experimental software. Use at your own risk.
""")
    
    try:
        from . import prompter
        confirmed = prompter.confirm(
            "Do you understand and accept these risks?",
            default=False,
        )
        return confirmed
    except Exception:
        # Fallback to text input
        confirm = input("Do you understand and accept these risks? [y/N]: ").strip().lower()
        return confirm == "y"


def _select_mode() -> str:
    """Select onboarding mode using interactive UI"""
    from . import prompter
    
    print("\n" + "-" * 80)
    print("Onboarding Mode")
    print("-" * 80)
    print("\nChoose your setup mode:")
    
    try:
        choice = prompter.select(
            "Select mode:",
            choices=[
                {
                    "name": "QuickStart - Fast setup with recommended settings (5 min)",
                    "value": "quickstart",
                },
                {
                    "name": "Advanced - Customize everything (15 min)",
                    "value": "advanced",
                },
            ],
        )
        print(f"\n✓ {choice.title()} mode selected")
        return choice
    except prompter.WizardCancelledError:
        print("\n✓ QuickStart mode selected (default)")
        return "quickstart"


def _prompt_config_action() -> str:
    """Prompt for action on existing config using interactive UI"""
    from . import prompter
    
    print("\nWhat would you like to do with existing configuration?")
    
    try:
        choice = prompter.select(
            "Select action:",
            choices=[
                {"name": "Keep - Use existing configuration", "value": "keep"},
                {"name": "Modify - Update specific settings", "value": "modify"},
                {"name": "Reset - Start fresh", "value": "reset"},
            ],
        )
        return choice
    except prompter.WizardCancelledError:
        return "modify"  # Default to modify
    except prompter.WizardCancelledError:
        return "modify"  # Default to modify
    
    # Old fallback code (should not reach here)
    if choice == "1":
        return "keep"
    elif choice == "3":
        return "reset"
    else:
        return "modify"


# Per-provider model menus (curated short-list, mirrors TS model catalog).
# Format: (model_id, display_hint)
_PROVIDER_MODELS: dict[str, list[tuple[str, str]]] = {
    # Aligned with TypeScript version defaults
    "anthropic": [
        ("anthropic/claude-sonnet-4-6",         "Claude Sonnet 4.6       (recommended)"),
        ("anthropic/claude-opus-4-6",           "Claude Opus 4.6         (most capable)"),
        ("anthropic/claude-opus-4-5",           "Claude Opus 4.5"),
        ("anthropic/claude-3-7-sonnet-20250219", "Claude 3.7 Sonnet       (reasoning)"),
        ("anthropic/claude-3-5-haiku-20241022", "Claude 3.5 Haiku        (fast/cheap)"),
        ("anthropic/claude-opus-4-0",           "Claude Opus 4.0         (reasoning)"),
        ("anthropic/claude-haiku-4-5",          "Claude Haiku 4.5"),
    ],
    "openai": [
        ("openai/gpt-5.1-codex", "GPT-5.1 Codex    (recommended)"),
        ("openai/gpt-4o",        "GPT-4o"),
        ("openai/gpt-5.2",       "GPT-5.2          (reasoning)"),
        ("openai/gpt-5-mini",    "GPT-5 Mini       (reasoning)"),
        ("openai/gpt-4o-mini",   "GPT-4o Mini      (fast/cheap)"),
        ("openai/o3-mini",       "o3-mini          (reasoning)"),
        ("openai/gpt-5.3-codex", "GPT-5.3 Codex    (code specialist)"),
    ],
    "google": [
        ("google/gemini-3-pro-preview",   "Gemini 3 Pro        (recommended)"),
        ("google/gemini-2.5-pro",         "Gemini 2.5 Pro      (reasoning)"),
        ("google/gemini-3-flash-preview", "Gemini 3 Flash      (reasoning)"),
        ("google/gemini-2.5-flash",       "Gemini 2.5 Flash"),
        ("google/gemini-2.0-flash",       "Gemini 2.0 Flash    (fast)"),
        ("google/gemini-2.0-flash-lite",  "Gemini 2.0 Flash Lite (cheap)"),
    ],
    "gemini": [  # Alias for google
        ("google/gemini-3-pro-preview",   "Gemini 3 Pro        (recommended)"),
        ("google/gemini-2.5-pro",         "Gemini 2.5 Pro      (reasoning)"),
        ("google/gemini-2.5-flash",       "Gemini 2.5 Flash"),
    ],
    "moonshot": [
        ("kimi-coding/k2p5",       "Kimi Coding k2p5    (recommended, Anthropic API)"),
        ("moonshot/moonshot-v1-8k", "Moonshot v1 8k      (standard API)"),
        ("moonshot/kimi-k2.5",     "Kimi k2.5           (legacy, standard API)"),
    ],
    "kimi-coding": [
        ("kimi-coding/k2p5",       "Kimi Code k2p5      (code specialist)"),
    ],
    "xai": [
        ("xai/grok-4",             "Grok 4              (recommended)"),
        ("xai/grok-3",             "Grok 3"),
        ("xai/grok-4-1-fast",      "Grok 4.1 Fast"),
    ],
    "mistral": [
        ("mistral/mistral-large-latest", "Mistral Large       (recommended)"),
        ("mistral/mistral-small-latest", "Mistral Small       (fast/cheap)"),
    ],
    "qianfan": [
        ("qianfan/deepseek-v3.2",  "DeepSeek v3.2       (recommended)"),
    ],
    "zai": [
        ("zai/glm-5",              "GLM-5               (recommended)"),
    ],
    "xiaomi": [
        ("xiaomi/mimo-v2-flash",   "Mimo v2 Flash       (recommended)"),
    ],
    "openrouter": [
        ("openrouter/auto",        "Auto (OpenRouter picks best)"),
    ],
    "huggingface": [
        ("huggingface/deepseek-ai/DeepSeek-R1", "DeepSeek R1  (recommended)"),
    ],
    "together": [
        ("together/moonshotai/Kimi-K2.5", "Kimi K2.5     (recommended)"),
    ],
    "litellm": [
        ("litellm/claude-opus-4-6", "Claude Opus 4.6    (recommended)"),
    ],
    "venice": [
        ("venice/llama-3.3-70b",   "Llama 3.3 70B       (recommended)"),
    ],
    "synthetic": [
        ("synthetic/hf:MiniMaxAI/MiniMax-M2.5", "MiniMax M2.5 (recommended)"),
    ],
    "volcengine": [
        ("volcengine/doubao-seed-1-8-251228", "Doubao Seed 1.8"),
    ],
    "byteplus": [
        ("byteplus/seed-1-8-251228", "Seed 1.8"),
    ],
    "ollama": [
        ("ollama/llama3",          "Llama 3"),
        ("ollama/mistral",         "Mistral"),
        ("ollama/codellama",       "CodeLlama"),
        ("ollama/deepseek-r1",     "DeepSeek R1"),
    ],
}


async def _get_provider_models_dynamic(provider: str) -> list[tuple[str, str]]:
    """Load models for a provider dynamically from model catalog.
    
    Returns list of (model_id, display_label) tuples.
    Falls back to hardcoded _PROVIDER_MODELS if dynamic loading fails.
    
    Aligns with TS model-picker.ts which uses loadModelCatalog().
    """
    try:
        catalog = await load_model_catalog(use_cache=False)
        
        # Normalize provider name (gemini -> google)
        catalog_provider = "google" if provider == "gemini" else provider
        
        # Filter models for this provider
        provider_models = [
            entry for entry in catalog
            if entry.provider.lower() == catalog_provider.lower()
        ]
        
        if not provider_models:
            # Fallback to hardcoded
            return _PROVIDER_MODELS.get(provider, [])
        
        # Build display options with metadata
        options: list[tuple[str, str]] = []
        for entry in provider_models:
            # Format: "provider/model-id"
            full_id = f"{provider}/{entry.id}"
            
            # Build display label with metadata
            label_parts = [entry.name]
            if entry.context_window:
                ctx_display = f"{entry.context_window // 1000}k"
                label_parts.append(f"({ctx_display})")
            if entry.reasoning:
                label_parts.append("[reasoning]")
            
            label = " ".join(label_parts)
            options.append((full_id, label))
        
        return options if options else _PROVIDER_MODELS.get(provider, [])
        
    except Exception as e:
        logger.debug(f"Dynamic model loading failed for {provider}: {e}")
        # Fallback to hardcoded
        return _PROVIDER_MODELS.get(provider, [])


async def _pick_model(provider: str, exclude: list[str] | None = None) -> str | None:
    """Show numbered model menu for *provider* and return the chosen model id.

    Returns None if the user presses Enter with no input (accept default) or
    chooses an invalid option (falls back to first entry).
    
    Now supports dynamic model loading from model catalog (aligns with TS).
    """
    # Try dynamic loading first, fallback to hardcoded
    options = await _get_provider_models_dynamic(provider)
    
    # Filter excluded
    if exclude:
        options = [(mid, hint) for mid, hint in options if mid not in exclude]
    
    if not options:
        return None

    # Build choices for questionary
    choices = []
    for i, (mid, hint) in enumerate(options, 1):
        default_tag = " (default)" if i == 1 else ""
        choices.append({"name": f"{i}. {hint}{default_tag}", "value": mid})
    
    try:
        from .prompter import select
        return select("Select model:", choices=choices, default=options[0][0])
    except Exception:
        print()
        for i, (mid, hint) in enumerate(options, 1):
            default_tag = "  ← default" if i == 1 else ""
            print(f"  {i}. {hint}{default_tag}")

        raw = input(f"\nSelect model [1]: ").strip()
        if not raw:
            return options[0][0]
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx][0]
        except ValueError:
            pass
        return options[0][0]


async def _configure_ollama() -> dict:
    """Configure native Ollama provider.

    Prompts for the Ollama server URL, performs live /api/tags discovery,
    lets the user pick (or type) a model, and optionally collects an API key
    for secured Ollama instances.

    Returns a dict compatible with the models.providers.ollama config block.
    Mirrors the Ollama runtime discovery in TS models-config.providers.ts
    (discoverOllamaModels / buildOllamaProvider).
    """
    import httpx

    # --- 1. Prompt base URL ---
    try:
        from .prompter import text
        raw_url = text("Ollama server URL:", default="http://127.0.0.1:11434")
    except Exception:
        raw_url = input("\nOllama server URL [http://127.0.0.1:11434]: ").strip()
    base_url = raw_url.rstrip("/").rstrip("/v1").rstrip("/") if raw_url else "http://127.0.0.1:11434"
    # Ensure no /v1 suffix (native Ollama API lives at /api/*, not /v1/*)
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]

    # --- 2. Connectivity check + model discovery via GET /api/tags ---
    discovered_models: list[dict] = []
    model_names: list[str] = []

    print(f"\nConnecting to Ollama at {base_url} ...")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            raw_models = data.get("models", [])

            for m in raw_models[:200]:  # cap at 200 (OLLAMA_SHOW_MAX_MODELS)
                model_name: str = m.get("name") or m.get("model") or ""
                if not model_name:
                    continue
                model_id = model_name
                display = model_name
                is_reasoning = any(kw in model_name.lower() for kw in ("r1", "reasoning"))

                # Query /api/show for context window (best-effort)
                ctx_window = 65536
                try:
                    show_resp = await client.post(
                        f"{base_url}/api/show",
                        json={"name": model_name},
                        timeout=5.0,
                    )
                    if show_resp.status_code == 200:
                        show_data = show_resp.json()
                        modelfile = show_data.get("modelfile") or show_data.get("parameters") or ""
                        import re
                        m_ctx = re.search(r"(?i)num_ctx\s+(\d+)", modelfile)
                        if not m_ctx:
                            params_str = str(show_data.get("details") or show_data.get("model_info") or "")
                            m_ctx = re.search(r"(?i)num_ctx[\"':\s]+(\d+)", params_str)
                        if m_ctx:
                            ctx_window = int(m_ctx.group(1))
                except Exception:
                    pass

                discovered_models.append({
                    "id": model_id,
                    "name": display,
                    "contextWindow": ctx_window,
                    "maxTokens": 8192,
                    "reasoning": is_reasoning,
                    "input": ["text"],
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                })
                model_names.append(model_name)

        if discovered_models:
            print(f"  Found {len(discovered_models)} model(s):")
            for i, m in enumerate(discovered_models, 1):
                ctx_k = m["contextWindow"] // 1024
                default_tag = "  <- default" if i == 1 else ""
                print(f"    {i}. {m['name']}  (ctx {ctx_k}k){default_tag}")
        else:
            print("  Connected but no models found. Make sure you have pulled at least one model.")
            print("  Hint: ollama pull llama3")

    except Exception as e:
        print(f"  Could not reach Ollama: {e}")
        print("  Make sure Ollama is running: ollama serve")

    # --- 3. Model selection ---
    model_id: str
    if discovered_models:
        # Build choices for questionary
        choices = []
        for i, m in enumerate(discovered_models, 1):
            ctx_k = m["contextWindow"] // 1024
            default_tag = " (default)" if i == 1 else ""
            choices.append({"name": f"{i}. {m['name']} (ctx {ctx_k}k){default_tag}", "value": m["id"]})
        
        try:
            from .prompter import select
            model_id = select("Select Ollama model:", choices=choices, default=discovered_models[0]["id"])
        except Exception:
            raw = input(f"\nSelect model [1]: ").strip()
            if not raw:
                model_id = discovered_models[0]["id"]
            else:
                try:
                    idx = int(raw) - 1
                    if 0 <= idx < len(discovered_models):
                        model_id = discovered_models[idx]["id"]
                    else:
                        model_id = discovered_models[0]["id"]
                except ValueError:
                    # User typed a name directly
                    model_id = raw
    else:
        # Manual entry
        try:
            from .prompter import text
            raw = text("Enter model name (e.g. llama3, mistral):", default="llama3")
        except Exception:
            raw = input("Enter model name (e.g. llama3, mistral): ").strip()
        model_id = raw if raw else "llama3"
        discovered_models = [{
            "id": model_id,
            "name": model_id,
            "contextWindow": 65536,
            "maxTokens": 8192,
            "reasoning": False,
            "input": ["text"],
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        }]

    # --- 4. Optional API key (for secured Ollama instances) ---
    try:
        from .prompter import text
        raw_key = text("API Key (leave blank if not secured):", default="")
    except Exception:
        raw_key = input("\nAPI Key [leave blank if not secured]: ").strip()
    api_key = raw_key if raw_key else "ollama-local"

    return {
        "provider": "ollama",
        "model": f"ollama/{model_id}",
        "base_url": base_url,
        "api_key": api_key,
        "discovered_models": discovered_models,
    }


def _derive_custom_provider_id(base_url: str) -> str:
    """Derive a provider ID from a base URL, e.g. http://127.0.0.1:11434/v1 → custom-127-0-0-1-11434.
    Mirrors resolveCustomProviderId logic in TS onboard-custom.ts.
    """
    try:
        parsed = urllib.parse.urlparse(base_url)
        netloc = parsed.netloc  # e.g. "127.0.0.1:11434"
        safe = netloc.replace(".", "-").replace(":", "-")
        return f"custom-{safe}"
    except Exception:
        return "custom-provider"


async def _configure_custom_provider() -> dict:
    """Configure a custom OpenAI/Anthropic-compatible endpoint.

    Mirrors TS promptCustomApiConfig() in src/commands/onboard-custom.ts:
    URL prompt → compatibility select → model ID prompt → verify loop →
    endpoint ID prompt → alias prompt.

    Returns a dict containing all fields needed to write models.providers.<id>.
    """
    import httpx

    _DEFAULT_URL = "http://127.0.0.1:11434/v1"

    # --- 1. API Base URL ---
    while True:
        try:
            from .prompter import text
            raw_url = text("API Base URL:", default=_DEFAULT_URL)
        except Exception:
            raw_url = input(f"\nAPI Base URL [{_DEFAULT_URL}]: ").strip()
        base_url = raw_url if raw_url else _DEFAULT_URL
        base_url = base_url.rstrip("/")
        try:
            parsed = urllib.parse.urlparse(base_url)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError("Invalid URL")
            break
        except Exception:
            print("  Invalid URL. Please enter a full URL including scheme, e.g. http://127.0.0.1:11434/v1")

    # Optional API key
    try:
        from .prompter import text
        raw_key = text("API Key (leave blank if not required):", default="")
    except Exception:
        raw_key = input("API Key (leave blank if not required): ").strip()
    api_key: Optional[str] = raw_key if raw_key else None

    # --- 2. Compatibility ---
    compat_choices = [
        {"name": "1. OpenAI-compatible (uses /chat/completions)", "value": "openai"},
        {"name": "2. Anthropic-compatible (uses /messages)", "value": "anthropic"},
        {"name": "3. Unknown — detect automatically", "value": "unknown"},
    ]
    
    try:
        from .prompter import select
        compatibility = select("Endpoint compatibility:", choices=compat_choices, default="openai")
    except Exception:
        print("\nEndpoint compatibility:")
        print("  1. OpenAI-compatible  (uses /chat/completions)")
        print("  2. Anthropic-compatible  (uses /messages)")
        print("  3. Unknown — detect automatically")
        compat_choice = input("\nSelect [1]: ").strip() or "1"
        compat_map = {"1": "openai", "2": "anthropic", "3": "unknown"}
        compatibility = compat_map.get(compat_choice, "openai")

    # --- 3. Model ID ---
    while True:
        try:
            from .prompter import text
            model_id = text("Model ID (e.g. llama3, claude-3-7-sonnet):")
        except Exception:
            model_id = input('\nModel ID (e.g. llama3, claude-3-7-sonnet): ').strip()
        if model_id:
            break
        print("  Model ID is required.")

    # --- 4. Verify loop ---
    _OPENAI_PAYLOAD = lambda mid: {
        "model": mid,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }
    _ANTHROPIC_PAYLOAD = lambda mid: {
        "model": mid,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }

    resolved_compat = compatibility  # may change after auto-detect
    while True:
        print(f"\nVerifying endpoint {base_url} with model {model_id} ...")
        verify_ok = False
        detected_compat = resolved_compat

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers: dict = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"

                if resolved_compat in ("openai", "unknown"):
                    try:
                        r = await client.post(
                            f"{base_url}/chat/completions",
                            json=_OPENAI_PAYLOAD(model_id),
                            headers=headers,
                        )
                        if r.status_code < 500:
                            verify_ok = True
                            detected_compat = "openai"
                    except Exception:
                        pass

                if not verify_ok and resolved_compat in ("anthropic", "unknown"):
                    try:
                        r = await client.post(
                            f"{base_url}/messages",
                            json=_ANTHROPIC_PAYLOAD(model_id),
                            headers=headers,
                        )
                        if r.status_code < 500:
                            verify_ok = True
                            detected_compat = "anthropic"
                    except Exception:
                        pass

        except Exception as e:
            print(f"  Connection failed: {e}")

        if verify_ok:
            resolved_compat = detected_compat
            api_field = "openai-completions" if resolved_compat == "openai" else "anthropic"
            print(f"  Endpoint verified ({api_field})")
            break
        else:
            print("  Could not verify endpoint.")
            
            fix_choices = [
                {"name": "1. Change base URL", "value": "1"},
                {"name": "2. Change model", "value": "2"},
                {"name": "3. Change base URL and model", "value": "3"},
                {"name": "4. Skip verification (use as-is)", "value": "4"},
            ]
            
            try:
                from .prompter import select
                fix_choice = select("What would you like to change?", choices=fix_choices, default="4")
            except Exception:
                print("\nWhat would you like to change?")
                print("  1. Change base URL")
                print("  2. Change model")
                print("  3. Change base URL and model")
                print("  4. Skip verification (use as-is)")
                fix_choice = input("Select [4]: ").strip() or "4"

            if fix_choice == "1" or fix_choice == "3":
                try:
                    from .prompter import text
                    raw_url = text("New base URL:", default=base_url)
                except Exception:
                    raw_url = input(f"New base URL [{base_url}]: ").strip()
                if raw_url:
                    base_url = raw_url.rstrip("/")
            if fix_choice == "2" or fix_choice == "3":
                try:
                    from .prompter import text
                    new_model = text("New model ID:", default=model_id)
                except Exception:
                    new_model = input(f"New model ID [{model_id}]: ").strip()
                if new_model:
                    model_id = new_model
            if fix_choice == "4":
                api_field = "openai-completions" if resolved_compat in ("openai", "unknown") else "anthropic"
                break

    # --- 5. Endpoint ID ---
    default_pid = _derive_custom_provider_id(base_url)
    try:
        from .prompter import text
        raw_pid = text("Endpoint ID:", default=default_pid)
    except Exception:
        raw_pid = input(f"\nEndpoint ID [{default_pid}]: ").strip()
    provider_id = raw_pid if raw_pid else default_pid

    # --- 6. Model alias ---
    try:
        from .prompter import text
        raw_alias = text("Model alias (optional, e.g. local, ollama):", default="")
    except Exception:
        raw_alias = input('Model alias (optional, e.g. local, ollama): ').strip()
    alias: Optional[str] = raw_alias if raw_alias else None

    model_definition = {
        "id": model_id,
        "name": f"{model_id} (Custom Provider)",
        "contextWindow": 8192,
        "maxTokens": 4096,
        "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "reasoning": False,
    }

    return {
        "provider": provider_id,
        "model": f"{provider_id}/{model_id}",
        "base_url": base_url,
        "api": api_field,
        "api_key": api_key,
        "alias": alias,
        "model_definition": model_definition,
    }


async def _configure_permissions(mode: str, config: "OpenClawConfig") -> Optional[str]:
    """Ask user to choose a permission preset. Returns the chosen preset key or None."""
    from .permission_presets import (
        display_presets_menu, detect_preset_level, PRESET_ORDER, DEFAULT_PRESET, PRESETS
    )

    print("\n" + "-" * 80)
    print("Permission Level")
    print("-" * 80)
    print("\nThis controls what the agent is allowed to do on your machine and who can")
    print("talk to the bot. You can always change this later with:")
    print("  uv run openclaw security preset\n")

    current = detect_preset_level(config)
    display_presets_menu(current)

    # Build default: standard for quickstart/fresh, keep current for advanced
    if mode == "advanced" and current:
        default_key = current
    else:
        default_key = DEFAULT_PRESET

    default_num = PRESET_ORDER.index(default_key) + 1

    def _safe_input(prompt: str) -> str:
        """Wrap input() so StopIteration (exhausted mock) becomes EOFError.

        PEP 479 converts StopIteration raised inside a coroutine to RuntimeError
        before any except clause can catch it.  By catching it in a plain function
        and re-raising as EOFError we keep the async frame clean.
        """
        try:
            return input(prompt)
        except StopIteration:
            raise EOFError("non-interactive")

    while True:
        try:
            from .prompter import select
            # Build choices for questionary
            choices = []
            for i, key in enumerate(PRESET_ORDER, 1):
                preset = PRESETS[key]
                is_default = (key == default_key)
                name = f"{i}. {preset['label']} - {preset['tagline']}"
                if is_default:
                    name += " (default)"
                choices.append({"name": name, "value": key})
            
            # Use questionary select
            chosen = select(
                "Select permission level:",
                choices=choices,
                default=default_key
            )
            print(f"✓ Selected: {PRESETS[chosen]['label']}  — {PRESETS[chosen]['tagline']}")
            return chosen
        except Exception:
            # Fallback to input()
            try:
                raw = _safe_input(f"Select [1-{len(PRESET_ORDER)}] (default {default_num} = {PRESETS[default_key]['label']}): ").strip()
            except EOFError:
                # Non-interactive / test environment — use the default silently
                print(f"✓ Using: {PRESETS[default_key]['label']} (default)")
                return default_key
            if not raw:
                print(f"✓ Using: {PRESETS[default_key]['label']}")
                return default_key
            if raw.isdigit() and 1 <= int(raw) <= len(PRESET_ORDER):
                chosen = PRESET_ORDER[int(raw) - 1]
                print(f"✓ Selected: {PRESETS[chosen]['label']}  — {PRESETS[chosen]['tagline']}")
                return chosen
            # Also accept the name directly
            if raw.lower() in PRESET_ORDER:
                chosen = raw.lower()
                print(f"✓ Selected: {PRESETS[chosen]['label']}  — {PRESETS[chosen]['tagline']}")
                return chosen
            print(f"  Invalid choice. Enter a number 1–{len(PRESET_ORDER)} or a preset name.")


async def _configure_provider(mode: str, claw_config: "OpenClawConfig") -> Optional[dict]:
    """Configure LLM provider (aligned with TS)
    
    Uses new handler chain architecture for 25+ providers.
    Mirrors openclaw/src/wizard/onboarding.ts _configure_provider logic.
    
    Args:
        mode: Onboarding mode ("quickstart" or "advanced")
        claw_config: Current configuration object
        
    Returns:
        Dict with provider info or None if skipped
    """
    # Import new auth choice infrastructure
    from .auth_choice_prompt import prompt_auth_choice_grouped
    from .auth_handlers.base import apply_auth_choice_chain
    from .auth_handlers.anthropic import apply_auth_choice_anthropic
    from .auth_handlers.openai import apply_auth_choice_openai
    from .auth_handlers.google import apply_auth_choice_google
    from .auth_handlers.api_providers import apply_auth_choice_api_providers
    from .auth_handlers.minimax import apply_auth_choice_minimax
    from .auth_handlers.moonshot import apply_auth_choice_moonshot
    from .auth_handlers.zai import apply_auth_choice_zai
    from .auth_handlers.oauth import apply_auth_choice_oauth
    from .auth_handlers.vllm import apply_auth_choice_vllm
    from .auth_handlers.custom import apply_auth_choice_custom
    from .auth_handlers.github_copilot import (
        apply_auth_choice_github_copilot,
        apply_auth_choice_copilot_proxy,
    )
    
    # QuickStart: Check environment variables for common providers
    if mode == "quickstart":
        from .auth import check_env_api_key
        
        # Check for Anthropic, OpenAI, Google keys in environment
        for provider_name in ["anthropic", "openai", "gemini"]:
            if check_env_api_key(provider_name):
                print(f"\n✓ Found {provider_name.title()} API key in environment")
                try:
                    from .prompter import confirm
                    use_it = confirm(f"Use {provider_name.title()}?", default=True)
                    if not use_it:
                        continue
                except Exception:
                    use_it = input(f"Use {provider_name.title()}? [Y/n]: ").strip().lower()
                    if use_it == "n":
                        continue
                
                # Map provider name to auth_choice
                auth_choice_map = {
                    "anthropic": "apiKey",
                    "openai": "openai-api-key",
                    "gemini": "gemini-api-key",
                }
                auth_choice = auth_choice_map[provider_name]
                
                # Apply auth choice using handler chain
                handlers = [
                    apply_auth_choice_anthropic,
                    apply_auth_choice_openai,
                    apply_auth_choice_google,
                ]
                
                result = await apply_auth_choice_chain(
                    handlers=handlers,
                    auth_choice=auth_choice,
                    config=claw_config,
                    set_default_model=True,
                    opts={},
                )
                
                # NOTE: Model selection moved to Step 4.5 (prompt_default_model)
                # No need to check or modify model_value here
                
                return {
                    "provider": auth_choice,
                    "model": None,  # Will be set in Step 4.5
                    "auth_choice": auth_choice,
                }
    
    # Prompt for auth choice using grouped selection UI
    auth_choice = await prompt_auth_choice_grouped(include_skip=True)
    
    if auth_choice == "skip":
        return None
    
    # Handler chain (aligned with TS order)
    handlers = [
        apply_auth_choice_anthropic,
        apply_auth_choice_vllm,
        apply_auth_choice_openai,
        apply_auth_choice_oauth,
        apply_auth_choice_api_providers,  # Handles 20+ simple API key providers
        apply_auth_choice_minimax,
        apply_auth_choice_moonshot,
        apply_auth_choice_zai,
        apply_auth_choice_github_copilot,
        apply_auth_choice_google,
        apply_auth_choice_copilot_proxy,
        apply_auth_choice_custom,
    ]
    
    # Apply auth choice using handler chain
    result = await apply_auth_choice_chain(
        handlers=handlers,
        auth_choice=auth_choice,
        config=claw_config,
        set_default_model=True,
        opts={},
    )
    
    # Get model value from config (may be pre-existing from config file)
    model_value = result.config.agents.defaults.model if result.config.agents and result.config.agents.defaults else None
    
    # NOTE: Removed _ask_fallbacks() call here
    # Fallback/model selection is now handled uniformly in Step 4.5 (prompt_default_model)
    # This ensures consistent UX across all providers and auth methods
    
    return {
        "provider": auth_choice,
        "model": model_value,  # May be None or pre-existing value
        "auth_choice": auth_choice,
    }


_FALLBACK_PROVIDER_ORDER = ["anthropic", "openai", "gemini", "ollama"]


async def _pick_fallback_model(exclude: list[str]) -> str | None:
    """Show a cross-provider model picker for fallback selection.

    - First asks which provider (defaults to same, but all are available).
    - Supports "Enter custom model name" entry for any provider/model string.
    - Returns a model id like "anthropic/claude-3-5-haiku-20241022" or None.

    Mirrors TS promptDefaultModel() which shows all providers + manual entry.
    """
    providers_display = [
        ("anthropic", "Anthropic (Claude)"),
        ("openai",    "OpenAI (GPT)"),
        ("gemini",    "Google (Gemini)"),
        ("ollama",    "Ollama (local)"),
        ("custom",    "Enter custom model name"),
    ]

    print()
    # Build questionary choices
    choices = []
    for i, (provider, label) in enumerate(providers_display, 1):
        choices.append({"name": f"{i}. {label}", "value": provider})
    
    try:
        from .prompter import select, text
        chosen_provider = select("Select provider:", choices=choices, default=providers_display[0][0])
    except Exception:
        for i, (_, label) in enumerate(providers_display, 1):
            print(f"  {i}. {label}")
        raw = input(f"\nSelect provider [1]: ").strip()
        if not raw:
            idx = 0
        else:
            try:
                idx = int(raw) - 1
                if not (0 <= idx < len(providers_display)):
                    idx = 0
            except ValueError:
                idx = 0
        chosen_provider, _ = providers_display[idx]

    if chosen_provider == "custom":
        try:
            model_str = text("Enter model id (e.g. anthropic/claude-3-5-haiku-20241022):")
            if not model_str:
                return None
        except Exception:
            model_str = input("Enter model id (e.g. anthropic/claude-3-5-haiku-20241022): ").strip()
            if not model_str:
                return None
        return model_str

    model = await _pick_model(chosen_provider, exclude=exclude)
    return model


async def _ask_fallbacks(provider: str, primary: str) -> str | dict:
    """Ask if the user wants fallback models. Returns a string (no fallbacks)
    or a dict {primary, fallbacks} suitable for agents.defaults.model.

    Supports cross-provider fallbacks and custom model name entry,
    mirroring TS promptDefaultModel() + applyModelFallbacksFromSelection().
    """
    try:
        from .prompter import confirm
        add_fb = confirm("Add fallback models?", default=False)
    except Exception:
        add_fb = input("\nAdd fallback models? [y/N]: ").strip().lower() == "y"
    
    if not add_fb:
        return primary

    fallbacks: list[str] = []
    print(
        "\nSelect fallback models (shown in priority order, up to 3). "
        "You can choose from any provider.\n"
        "Enter 'done' or leave blank to finish."
    )
    while len(fallbacks) < 3:
        print(f"\nFallback #{len(fallbacks) + 1}:  (primary: {primary})")
        chosen = await _pick_fallback_model(exclude=[primary] + fallbacks)
        if not chosen:
            break
        fallbacks.append(chosen)
        print(f"  ✓ Added: {chosen}")
        if len(fallbacks) >= 3:
            break
        try:
            from .prompter import confirm
            another = confirm("Add another fallback?", default=False)
        except Exception:
            another = input("Add another fallback? [y/N]: ").strip().lower() == "y"
        if not another:
            break

    if not fallbacks:
        return primary
    return {"primary": primary, "fallbacks": fallbacks}


async def _configure_agent_settings() -> Optional[dict]:
    """Configure agent settings"""
    print("\n" + "-" * 80)
    print("Agent Settings")
    print("-" * 80)
    
    return await configure_agent()


async def _configure_gateway(mode: str) -> Optional[dict]:
    """Configure Gateway settings"""
    print("\n" + "-" * 80)
    print("Gateway Configuration")
    print("-" * 80)
    
    config = {}
    
    # Port
    if mode == "quickstart":
        config["port"] = 18789
        print(f"\nUsing default port: {config['port']}")
    else:
        try:
            from .prompter import text
            port_input = text("Gateway port:", default="18789")
            config["port"] = int(port_input) if port_input else 18789
        except Exception:
            port_input = input("\nGateway port [18789]: ").strip()
            config["port"] = int(port_input) if port_input else 18789
    
    # Bind mode
    if mode == "quickstart":
        config["bind"] = "loopback"
        print("Using loopback mode (local access only)")
    else:
        bind_choices = [
            {"name": "1. loopback - Local access only (recommended)", "value": "loopback"},
            {"name": "2. lan - LAN access", "value": "lan"},
            {"name": "3. auto - Auto-detect", "value": "auto"},
        ]
        
        try:
            from .prompter import select
            config["bind"] = select("Bind mode:", choices=bind_choices, default="loopback")
        except Exception:
            print("\nBind mode:")
            print("  1. loopback  - Local access only (recommended)")
            print("  2. lan       - LAN access")
            print("  3. auto      - Auto-detect")
            bind_choice = input("\nSelect bind mode [1]: ").strip()
            bind_map = {"1": "loopback", "2": "lan", "3": "auto"}
            config["bind"] = bind_map.get(bind_choice, "loopback")
    
    # Authentication
    if mode == "quickstart":
        # Generate token
        config["auth_token"] = secrets.token_urlsafe(32)
        print("\n✓ Generated authentication token")
    else:
        auth_choices = [
            {"name": "1. Token - Random token (recommended)", "value": "1"},
            {"name": "2. Password - Custom password", "value": "2"},
            {"name": "3. None - No authentication (local only)", "value": "3"},
        ]
        
        try:
            from .prompter import select, password as prompt_password
            auth_choice = select("Authentication:", choices=auth_choices, default="1")
        except Exception:
            print("\nAuthentication:")
            print("  1. Token     - Random token (recommended)")
            print("  2. Password  - Custom password")
            print("  3. None      - No authentication (local only)")
            auth_choice = input("\nSelect auth mode [1]: ").strip() or "1"
        
        if auth_choice == "2":
            try:
                from .prompter import password as prompt_password
                password = prompt_password("Enter password:")
            except Exception:
                password = input("Enter password: ").strip()
            if password:
                config["auth_password"] = password
        elif auth_choice == "3":
            print("⚠️  Warning: No authentication - use only for local development")
        else:
            config["auth_token"] = secrets.token_urlsafe(32)
            print("✓ Generated authentication token")
    
    return config


_CHANNEL_MENU: list[tuple[str, str]] = [
    ("telegram",  "Telegram"),
    ("discord",   "Discord"),
    ("feishu",    "Feishu / Lark"),
    ("whatsapp",  "WhatsApp"),
]


async def _configure_channels(mode: str) -> Optional[dict]:
    """Configure one or more channels sequentially.

    After finishing each channel the user is asked "Add another channel?",
    mirroring the TS wizard which loops until the user declines or all
    channels have been configured.
    """
    print("\n" + "-" * 80)
    print("Channels Configuration")
    print("-" * 80)

    if mode == "quickstart":
        try:
            from .prompter import confirm
            setup_channels = confirm("Configure channels now?", default=False)
        except Exception:
            setup_channels = input("\nConfigure channels now? [y/N]: ").strip().lower() == "y"
        
        if not setup_channels:
            print("Skipping channels. You can configure them later with:")
            print("  $ openclaw configure channels")
            return None

    channels_config: dict = {}

    while True:
        # Build the menu, marking already-configured channels
        available = [
            (key, label)
            for key, label in _CHANNEL_MENU
            if key not in channels_config
        ]
        if not available:
            print("\n✓ All supported channels have been configured.")
            break

        configured_str = (
            f"  (already configured: {', '.join(channels_config)})" if channels_config else ""
        )
        print(f"\nWhich channel would you like to configure?{configured_str}")
        for i, (_, label) in enumerate(available, 1):
            print(f"  {i}. {label}")
        print(f"  {len(available) + 1}. Done / Skip")

        # Build choices for questionary
        choices = []
        for i, (key, label) in enumerate(available, 1):
            choices.append({"name": f"{i}. {label}", "value": key})
        choices.append({"name": f"{len(available) + 1}. Done / Skip", "value": "done"})
        
        try:
            from .prompter import select
            key = select(
                "Select channel:",
                choices=choices,
                default="done" if channels_config else available[0][0]
            )
            if key == "done":
                break
            # Find label for the chosen key
            label = next((l for k, l in available if k == key), key)
        except Exception:
            raw = input(f"\nSelect [{'1' if not channels_config else str(len(available) + 1)}]: ").strip()
            if not raw:
                choice_idx = 0 if not channels_config else len(available)  # default: first or Done
            else:
                try:
                    choice_idx = int(raw) - 1
                except ValueError:
                    choice_idx = len(available)  # treat invalid as Done

            if choice_idx < 0 or choice_idx >= len(available):
                # "Done" selected or out-of-range
                break

            key, label = available[choice_idx]
        print("\n" + "~" * 60)
        print(f"Configuring {label}")
        print("~" * 60)

        cfg = None
        if key == "telegram":
            cfg = configure_telegram_enhanced()
        elif key == "discord":
            cfg = configure_discord_enhanced()
        elif key == "feishu":
            cfg = configure_feishu_enhanced()
        elif key == "whatsapp":
            cfg = configure_whatsapp_enhanced()

        if cfg:
            channels_config[key] = cfg
            print(f"\n✓ {label} configured.")

        # Ask about another channel only if there are more to configure
        remaining = [k for k, _ in _CHANNEL_MENU if k not in channels_config]
        if not remaining:
            break
        try:
            from .prompter import confirm
            another = confirm("Configure another channel?", default=False)
        except Exception:
            another = input("\nConfigure another channel? [y/N]: ").strip().lower() == "y"
        if not another:
            break

    return channels_config if channels_config else None


def mark_onboarding_complete(workspace_dir: Path) -> None:
    """Mark onboarding as complete by writing workspace-state.json.

    Matches TypeScript behavior: only writes onboardingCompletedAt into
    workspace-state.json. Does NOT write a separate onboarding-complete
    marker file (that was a Python-only addition not present in TS).
    """
    now_iso = datetime.now().isoformat()
    write_workspace_state(workspace_dir, onboarding_completed_at=now_iso)
    logger.info("Onboarding marked complete in workspace-state.json: %s", workspace_dir)


def write_workspace_state(
    workspace_dir: Path,
    bootstrap_seeded_at: Optional[str] = None,
    onboarding_completed_at: Optional[str] = None,
) -> None:
    """Write or update workspace-state.json.

    Delegates to ensure_workspace.write_workspace_state() which uses the
    correct path: {workspaceDir}/.openclaw/workspace-state.json — matching
    TypeScript workspace.ts WORKSPACE_STATE_DIRNAME = ".openclaw".
    """
    from openclaw.agents.ensure_workspace import write_workspace_state as _ws_write
    _ws_write(
        workspace_dir,
        bootstrap_seeded_at=bootstrap_seeded_at,
        onboarding_completed_at=onboarding_completed_at,
    )


def is_first_run(workspace_dir: Path) -> bool:
    """Check if this is the first run (onboarding not yet completed).

    Matches TypeScript: checks onboardingCompletedAt in workspace-state.json.
    """
    from openclaw.agents.ensure_workspace import is_workspace_onboarding_completed
    return not is_workspace_onboarding_completed(workspace_dir)


# Alias matching TS runInteractiveOnboarding — used by bootstrap and tests
run_interactive_onboarding = run_onboarding_wizard
