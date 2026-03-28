"""
Telegram channel onboarding adapter — mirrors TS
src/channels/plugins/onboarding/telegram.ts
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .types import ChannelOnboardingAdapter, ChannelOnboardingStatus, DmPolicy

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"


def _can_use_env_token() -> str | None:
    """Check if TELEGRAM_BOT_TOKEN is set in environment."""
    return os.environ.get(TELEGRAM_BOT_TOKEN_ENV)


async def fetch_telegram_chat_id(token: str, username: str) -> int | None:
    """Resolve a @username to a numeric Telegram user ID via getUpdates.

    Mirrors TS fetchTelegramChatId().
    """
    try:
        import httpx

        clean = username.lstrip("@")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getUpdates")
            data = resp.json()
            for update in data.get("result", []):
                msg = update.get("message", {})
                user = msg.get("from", {})
                if user.get("username", "").lower() == clean.lower():
                    return user.get("id")
    except Exception as e:
        logger.debug("fetch_telegram_chat_id failed: %s", e)
    return None


class TelegramOnboardingAdapter(ChannelOnboardingAdapter):
    """Onboarding adapter for Telegram channel.

    Mirrors TS telegramOnboardingAdapter from
    src/channels/plugins/onboarding/telegram.ts.
    """

    def __init__(self) -> None:
        super().__init__("telegram")

    @property
    def quickstart_score(self) -> int:
        """Higher = more likely to be pre-selected in quickstart.

        TS: 1 if configured, 10 if not (newcomer-friendly).
        """
        return 10

    @property
    def selection_hint(self) -> str:
        """Short label for the channel picker."""
        return "recommended · newcomer-friendly"
    
    async def get_status(self, config: dict[str, Any]) -> ChannelOnboardingStatus:
        """Check Telegram configuration status"""
        channels = config.get("channels", {})
        telegram_config = channels.get("telegram", {})
        
        has_token = bool(telegram_config.get("token") or telegram_config.get("botToken"))
        enabled = telegram_config.get("enabled", False)
        dm_policy = telegram_config.get("dmPolicy", "off")
        allow_from = telegram_config.get("allowFrom", [])
        
        # Check for issues
        issues = []
        if enabled and not has_token:
            issues.append("Telegram bot token missing")
        
        if dm_policy == "allowlist" and not allow_from:
            issues.append("Allowlist is empty")
        
        return ChannelOnboardingStatus(
            configured=has_token,
            enabled=enabled,
            has_token=has_token,
            dm_policy=dm_policy,
            issues=issues if issues else None,
        )
    
    async def configure(
        self,
        config: dict[str, Any],
        prompter: Any,
    ) -> dict[str, Any]:
        """Interactive Telegram configuration.

        Mirrors TS configure() — detects env var, validates token format.
        """
        await self._show_token_help(prompter)

        env_token = _can_use_env_token()
        token: str | None = None

        if env_token:
            use_env = True
            try:
                use_env = await prompter.confirm(
                    message=f"Found {TELEGRAM_BOT_TOKEN_ENV} in environment. Use it?",
                    default=True,
                )
            except Exception:
                pass
            if use_env:
                token = env_token
                print(f"✓ Using token from {TELEGRAM_BOT_TOKEN_ENV}")

        if not token:
            token = await prompter.text(
                message="Enter Telegram bot token",
                placeholder="123456:ABC...",
                validate=lambda v: None if v and len(v) > 10 else "Token required",
            )

        channels = config.get("channels", {})
        telegram_config = channels.get("telegram", {})

        telegram_config["enabled"] = True
        telegram_config["botToken"] = token
        telegram_config["dmPolicy"] = "pairing"
        telegram_config["groupPolicy"] = "allowlist"

        channels["telegram"] = telegram_config
        config["channels"] = channels

        logger.info("Telegram configured with bot token")
        return config

    async def disable(self, config: dict[str, Any]) -> dict[str, Any]:
        """Disable Telegram channel. Mirrors TS adapter disable()."""
        channels = config.get("channels", {})
        tg = channels.get("telegram", {})
        tg["enabled"] = False
        channels["telegram"] = tg
        config["channels"] = channels
        return config
    
    async def configure_dm_policy(
        self,
        config: dict[str, Any],
        prompter: Any,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        """Configure Telegram DM policy"""
        
        # Show help
        await self._show_user_id_help(prompter)
        
        # Prompt for DM policy
        policy_choice = await prompter.select(
            message="Telegram DM policy",
            choices=[
                {"value": "off", "label": "Off - no DMs allowed"},
                {"value": "allowlist", "label": "Allowlist - specific users only"},
                {"value": "open", "label": "Open - anyone can DM (careful!)"},
            ]
        )
        
        dm_policy: DmPolicy = policy_choice["value"]  # type: ignore
        
        # Update config
        channels = config.get("channels", {})
        telegram_config = channels.get("telegram", {})
        
        telegram_config["dmPolicy"] = dm_policy
        
        # If allowlist, prompt for user IDs
        if dm_policy == "allowlist":
            user_ids = await prompter.text(
                message="Telegram user IDs (comma-separated)",
                placeholder="123456789, 987654321",
                validate=lambda v: None if v and v.strip() else "At least one user ID required"
            )
            
            # Parse user IDs
            parsed_ids = [
                uid.strip()
                for uid in str(user_ids).split(",")
                if uid.strip()
            ]
            
            telegram_config["allowFrom"] = parsed_ids
        elif dm_policy == "open":
            # For open policy, set wildcard
            telegram_config["allowFrom"] = ["*"]
        else:
            # For off, clear allowFrom
            telegram_config.pop("allowFrom", None)
        
        channels["telegram"] = telegram_config
        config["channels"] = channels
        
        logger.info(f"Telegram DM policy set to: {dm_policy}")
        
        return config
    
    async def validate_token(self, token: str) -> bool:
        """Validate Telegram bot token format"""
        # Telegram bot tokens are in format: <bot_id>:<hash>
        # Example: 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
        
        if not token or ":" not in token:
            return False
        
        parts = token.split(":", 1)
        if len(parts) != 2:
            return False
        
        bot_id, hash_part = parts
        
        # Bot ID should be numeric
        if not bot_id.isdigit():
            return False
        
        # Hash should be at least 30 characters
        if len(hash_part) < 30:
            return False
        
        return True
    
    async def _show_token_help(self, prompter: Any) -> None:
        """Show help for obtaining Telegram bot token"""
        help_text = """
        Telegram Bot Token Setup:
        
        1. Open Telegram and chat with @BotFather
        2. Run /newbot (or /mybots to manage existing)
        3. Follow the prompts to create your bot
        4. Copy the token (looks like: 123456:ABC...)
        
        Tip: You can also set TELEGRAM_BOT_TOKEN environment variable
        
        Docs: https://docs.openclaw.ai/channels/telegram
        """
        
        await prompter.note(help_text.strip(), "Telegram Setup")
    
    async def _show_user_id_help(self, prompter: Any) -> None:
        """Show help for finding Telegram user IDs"""
        help_text = """
        Finding Telegram User IDs:
        
        1. DM your bot, then check logs: openclaw logs --follow
           Look for "from.id" in the message
        
        2. Use Telegram API directly:
           https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
           Look for message.from.id
        
        3. Use third-party bots:
           - @userinfobot
           - @getidsbot
        
        User IDs are numeric (e.g., 123456789)
        """
        
        await prompter.note(help_text.strip(), "Telegram User IDs")
    
    def get_help_text(self) -> str:
        """Get help text for Telegram setup"""
        return """
        Telegram Channel Setup:
        
        1. Create a bot via @BotFather on Telegram
        2. Get the bot token from BotFather
        3. Configure DM policy (off/allowlist/open)
        4. Add allowed user IDs if using allowlist
        
        See: https://docs.openclaw.ai/channels/telegram
        """
