"""Telegram channel plugin.

Mirrors TypeScript: openclaw/extensions/telegram/index.ts

Supports both single-bot (legacy botToken at top level) and
multi-account mode (accounts map with per-account botToken).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register(api) -> None:
    try:
        from openclaw.channels.telegram.enhanced_telegram import EnhancedTelegramChannel
    except ImportError:
        logger.warning("Telegram channel unavailable — install python-telegram-bot")
        return

    # Try to load config to detect multi-account mode
    try:
        config = api.get_config() if hasattr(api, "get_config") else None
    except Exception:
        config = None

    telegram_cfg: dict = {}
    if isinstance(config, dict):
        telegram_cfg = config.get("channels", {}).get("telegram", {}) or {}
    elif config is not None and hasattr(config, "channels"):
        channels = config.channels
        telegram_cfg = (
            getattr(channels, "telegram", None)
            or (isinstance(channels, dict) and channels.get("telegram"))
            or {}
        )
        if hasattr(telegram_cfg, "model_dump"):
            telegram_cfg = telegram_cfg.model_dump(by_alias=True, exclude_none=True)

    accounts: dict = telegram_cfg.get("accounts") or {}

    if accounts:
        # Multi-account mode — register one TelegramChannel per account
        # Mirrors TS: registerTelegramChannel() iterates accounts map
        default_account_id = telegram_cfg.get("defaultAccount") or next(iter(accounts), None)
        for account_id, account_cfg in accounts.items():
            if not isinstance(account_cfg, dict):
                continue
            bot_token = account_cfg.get("botToken") or account_cfg.get("bot_token")
            if not bot_token:
                logger.warning("Telegram account '%s' has no botToken — skipping", account_id)
                continue

            # Merge account config with top-level defaults (account overrides top-level)
            merged_cfg = {**telegram_cfg, **account_cfg, "accountId": account_id}
            merged_cfg.pop("accounts", None)  # remove nested accounts block

            channel = EnhancedTelegramChannel(bot_token=bot_token)
            channel._account_id = account_id
            channel._is_default_account = (account_id == default_account_id)

            try:
                api.register_channel(channel, merged_cfg)
                logger.info("Telegram account '%s' registered", account_id)
            except Exception as reg_err:
                # Fallback: register without per-account config (channel.start() gets config later)
                try:
                    api.register_channel(channel)
                    logger.info("Telegram account '%s' registered (config-less)", account_id)
                except Exception as e:
                    logger.error("Failed to register Telegram account '%s': %s", account_id, e)
    else:
        # Single-account / legacy mode
        bot_token = telegram_cfg.get("botToken") or telegram_cfg.get("bot_token")
        channel = EnhancedTelegramChannel(bot_token=bot_token if bot_token else None)
        try:
            api.register_channel(channel)
            logger.info("Telegram channel registered (single-account mode)")
        except Exception as e:
            logger.error("Failed to register Telegram channel: %s", e)

    # Register agent tools
    _register_tools(api)


def _register_tools(api) -> None:
    """Register Telegram-specific agent tools."""
    tool_modules = [
        ("telegram_messaging", "openclaw.channels.telegram.tools.telegram_messaging"),
        ("telegram_forum", "openclaw.channels.telegram.tools.telegram_forum"),
    ]
    for tool_name, module_path in tool_modules:
        try:
            import importlib
            mod = importlib.import_module(module_path)
            if hasattr(mod, "register"):
                mod.register(api)
                logger.debug("Telegram tool module registered: %s", tool_name)
        except ImportError:
            pass
        except Exception as e:
            logger.warning("Failed to register Telegram tool module '%s': %s", tool_name, e)


plugin = {
    "id": "telegram",
    "name": "Telegram",
    "description": (
        "Telegram bot channel with DM/group policies, multi-account support, "
        "voice messages, inline keyboards, webhooks, and ack reactions."
    ),
    "register": register,
}
