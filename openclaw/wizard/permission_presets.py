"""Permission preset definitions shared between onboarding and the security CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..config.schema import OpenClawConfig

# ---------------------------------------------------------------------------
# Preset definitions
# ---------------------------------------------------------------------------

STANDARD_SAFE_BINS: list[str] = [
    "python", "pip", "uv",
    "ffmpeg", "git",
    "node", "npm",
    "convert",
]

PRESETS: dict[str, dict] = {
    "relaxed": {
        "label": "Relaxed",
        "exec_security": "full",
        "dm_policy": "open",
        "group_policy": "open",
        "allow_within_provider": True,
        "allow_across_providers": True,
        "safe_bins": STANDARD_SAFE_BINS,
        "summary": "exec=full, dm=open, groups=open, outbound=all",
        "tagline": "Full capability, anyone can talk to the bot",
        "tradeoff": (
            "Agent can run ANY command. Bot is open to all users and all groups. "
            "Agent can send messages to any channel or provider. "
            "Use only on a trusted personal machine on a private network."
        ),
    },
    "trusted": {
        "label": "Trusted",
        "exec_security": "full",
        "dm_policy": "pairing",
        "group_policy": "allowlist",
        "allow_within_provider": True,
        "allow_across_providers": False,
        "safe_bins": STANDARD_SAFE_BINS,
        "summary": "exec=full, dm=pairing, groups=allowlist, outbound=within",
        "tagline": "Full capability, pairing required, groups by allowlist  ← recommended",
        "tradeoff": (
            "Agent can run ANY command. "
            "New DM users must pair first. Groups only receive messages if explicitly allowlisted. "
            "Agent can message other chats within the same channel but not across providers."
        ),
    },
    "standard": {
        "label": "Standard",
        "exec_security": "allowlist",
        "dm_policy": "pairing",
        "group_policy": "allowlist",
        "allow_within_provider": True,
        "allow_across_providers": False,
        "safe_bins": STANDARD_SAFE_BINS,
        "summary": "exec=allowlist, dm=pairing, groups=allowlist, outbound=within",
        "tagline": "Common tools allowed, pairing required, groups by allowlist",
        "tradeoff": (
            f"Allows: {', '.join(STANDARD_SAFE_BINS)}. "
            "Other shell commands are blocked or require approval. "
            "New DM users must pair first. Groups only if allowlisted. "
            "Agent can message within the same channel only."
        ),
    },
    "strict": {
        "label": "Strict",
        "exec_security": "deny",
        "dm_policy": "pairing",
        "group_policy": "disabled",
        "allow_within_provider": False,
        "allow_across_providers": False,
        "safe_bins": [],
        "summary": "exec=deny, dm=pairing, groups=disabled, outbound=none",
        "tagline": "No shell, no groups, no outbound messaging. Maximum safety.",
        "tradeoff": (
            "Agent CANNOT run any shell commands. "
            "File read/write tools still work. "
            "Group chats entirely disabled. Agent cannot proactively send messages. "
            "Safest for shared or untrusted environments."
        ),
    },
}

PRESET_ORDER: list[str] = ["relaxed", "trusted", "standard", "strict"]
DEFAULT_PRESET = "trusted"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def detect_preset_level(config: "OpenClawConfig") -> Optional[str]:
    """Return the preset name that best matches the current config, or None."""
    exec_cfg = config.tools.exec if (config.tools and config.tools.exec) else None
    security = exec_cfg.security if exec_cfg else "deny"

    # Determine effective dmPolicy and groupPolicy (use first configured channel's value)
    dm_policy = None
    group_policy = None
    channels = config.channels
    if channels:
        for attr in ("telegram", "feishu", "discord", "whatsapp", "slack"):
            ch = getattr(channels, attr, None)
            if ch and getattr(ch, "enabled", False):
                if dm_policy is None:
                    dm_policy = getattr(ch, "dmPolicy", None) or getattr(ch, "dm_policy", None)
                if group_policy is None:
                    group_policy = getattr(ch, "groupPolicy", None) or getattr(ch, "group_policy", None)
                if dm_policy and group_policy:
                    break

    # Determine outbound crossContext settings
    msg_cfg = config.tools.message if (config.tools and config.tools.message) else None
    cc = msg_cfg.cross_context if msg_cfg else None
    allow_within = cc.allow_within_provider if cc else None
    allow_across = cc.allow_across_providers if cc else None

    for key in PRESET_ORDER:
        p = PRESETS[key]
        if p["exec_security"] != security:
            continue
        if dm_policy is not None and p["dm_policy"] != dm_policy:
            continue
        if group_policy is not None and p["group_policy"] != group_policy:
            continue
        if allow_within is not None and p["allow_within_provider"] != allow_within:
            continue
        if allow_across is not None and p["allow_across_providers"] != allow_across:
            continue
        return key

    return None


def apply_preset(config: "OpenClawConfig", preset_name: str) -> "OpenClawConfig":
    """Apply a named preset to the config in-place and return it."""
    from ..config.schema import ToolsConfig, ExecToolConfig, MessageToolConfig, MessageCrossContextConfig

    preset = PRESETS.get(preset_name)
    if not preset:
        raise ValueError(f"Unknown preset: {preset_name!r}. Choose from: {', '.join(PRESET_ORDER)}")

    # Apply exec settings
    if not config.tools:
        config.tools = ToolsConfig()
    if not config.tools.exec:
        config.tools.exec = ExecToolConfig()

    config.tools.exec.security = preset["exec_security"]
    config.tools.exec.ask = "on-miss" if preset["exec_security"] != "deny" else "on-miss"
    config.tools.exec.safe_bins = list(preset["safe_bins"])

    # Apply dmPolicy and groupPolicy to all configured channels
    dm_policy = preset["dm_policy"]
    group_policy = preset["group_policy"]
    channels = config.channels
    if channels:
        for attr in ("telegram", "feishu", "discord", "whatsapp", "slack"):
            ch = getattr(channels, attr, None)
            if ch is not None:
                # Pydantic v2 models are not frozen by default — plain setattr works
                if hasattr(ch, "dmPolicy"):
                    setattr(ch, "dmPolicy", dm_policy)
                elif hasattr(ch, "dm_policy"):
                    setattr(ch, "dm_policy", dm_policy)
                if hasattr(ch, "groupPolicy"):
                    setattr(ch, "groupPolicy", group_policy)
                elif hasattr(ch, "group_policy"):
                    setattr(ch, "group_policy", group_policy)

    # Apply outbound crossContext settings
    if not config.tools.message:
        config.tools.message = MessageToolConfig()
    if not config.tools.message.cross_context:
        config.tools.message.cross_context = MessageCrossContextConfig()
    config.tools.message.cross_context.allow_within_provider = preset["allow_within_provider"]
    config.tools.message.cross_context.allow_across_providers = preset["allow_across_providers"]

    return config


def display_presets_menu(current_level: Optional[str] = None) -> None:
    """Print the permission level menu to stdout using Rich if available."""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box

        console = Console()
        console.print()
        console.print("[bold]Permission Level[/bold] — choose what the agent is allowed to do:\n")

        for i, key in enumerate(PRESET_ORDER, start=1):
            p = PRESETS[key]
            is_current = key == current_level
            marker = " [green]← current[/green]" if is_current else ""
            console.print(f"  [bold cyan]{i}. {p['label']}[/bold cyan]  {p['summary']}{marker}")
            console.print(f"     {p['tagline']}")
            console.print(f"     [dim]{p['tradeoff']}[/dim]")
            console.print()

    except ImportError:
        print("\nPermission Level — choose what the agent is allowed to do:\n")
        for i, key in enumerate(PRESET_ORDER, start=1):
            p = PRESETS[key]
            is_current = " <- current" if key == current_level else ""
            print(f"  {i}. {p['label']}  {p['summary']}{is_current}")
            print(f"     {p['tagline']}")
            print(f"     {p['tradeoff']}")
            print()
