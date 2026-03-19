"""Hooks setup during onboarding - aligned with TypeScript onboard-hooks.ts

Implements full interactive flow:
- Status display
- Multi-select enable via questionary.checkbox
- Saves enabled hooks to config
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from ..config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


async def setup_hooks(
    workspace_dir: Path | None = None,
    config: dict | None = None,
    mode: str = "quickstart",
) -> dict[str, Any]:
    """
    Setup internal hooks during onboarding (aligned with TS setupInternalHooks).

    Args:
        workspace_dir: Workspace directory (default: ~/.openclaw/workspace)
        config: Current config dict (will be updated with enabled hooks)
        mode: "quickstart" or "advanced"

    Returns:
        Dict with configured, enabled, config (updated config to save)
    """
    from openclaw.hooks.hooks_status import build_workspace_hook_status
    from . import prompter

    ws = workspace_dir or (resolve_state_dir() / "workspace")
    cfg = dict(config) if config else {}

    print("\n" + "=" * 60)
    print("🪝 HOOKS SETUP")
    print("=" * 60)

    prompter.note(
        "Hooks let you automate actions when agent commands are issued.\n"
        "Example: Save session context to memory when you issue /new or /reset.\n\n"
        "Learn more: https://docs.openclaw.ai/automation/hooks",
        title="Hooks",
    )
    print()

    report = build_workspace_hook_status(str(ws), config=cfg)
    eligible_hooks = [h for h in report.hooks if h.eligible]

    if not eligible_hooks:
        prompter.note(
            "No eligible hooks found. You can configure hooks later in your config.",
            title="No Hooks Available",
        )
        return {"configured": False, "enabled": [], "config": cfg}

    # QuickStart: use defaults, skip interactive
    if mode == "quickstart":
        # Enable common defaults
        default_hooks = ["session-memory", "boot-md", "command-logger"]
        entries = cfg.get("hooks", {}).get("internal", {}).get("entries", {}) or {}
        entries = dict(entries)
        for name in default_hooks:
            if any(h.name == name for h in eligible_hooks):
                entries[name] = {"enabled": True}
        cfg.setdefault("hooks", {})["internal"] = {
            "enabled": True,
            "entries": entries,
        }
        print("⚡ QuickStart: Default hooks configured (session-memory, boot-md, command-logger)")
        return {"configured": True, "enabled": list(entries.keys()), "config": cfg}

    # Advanced: multiselect
    choices = [
        {"name": "Skip for now", "value": "__skip__", "description": ""},
        *[
            {
                "name": f"{h.emoji or '🔗'} {h.name}",
                "value": h.name,
                "description": h.description or "",
            }
            for h in eligible_hooks
        ],
    ]

    try:
        selected = prompter.checkbox(
            "Enable hooks? (Space to select, Enter to confirm)",
            choices=choices,
        )
    except prompter.WizardCancelledError:
        return {"configured": False, "enabled": [], "config": cfg}

    to_enable = [n for n in selected if n != "__skip__"]
    if not to_enable:
        return {"configured": False, "enabled": [], "config": cfg}

    # Merge into config
    entries = dict(cfg.get("hooks", {}).get("internal", {}).get("entries", {}) or {})
    for name in to_enable:
        entries[name] = {"enabled": True}

    cfg.setdefault("hooks", {})["internal"] = {
        "enabled": True,
        "entries": entries,
    }

    prompter.note(
        f"Enabled {len(to_enable)} hook{'s' if len(to_enable) != 1 else ''}: {', '.join(to_enable)}\n\n"
        "You can manage hooks later with:\n"
        "  uv run openclaw hooks list\n"
        "  uv run openclaw hooks enable <name>\n"
        "  uv run openclaw hooks disable <name>",
        title="Hooks Configured",
    )

    return {"configured": True, "enabled": to_enable, "config": cfg}


__all__ = ["setup_hooks"]
