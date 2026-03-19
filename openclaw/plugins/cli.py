"""Plugin CLI commands registration

Mirrors openclaw/src/plugins/cli.ts
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .plugin_manager import load_gateway_plugins
from .logger import create_plugin_loader_logger

logger = logging.getLogger(__name__)


async def register_plugin_cli_commands(
    program: Any,
    config: dict[str, Any] | None = None,
    workspace_dir: Path | str | None = None,
) -> None:
    """Register plugin CLI commands.
    
    Loads plugins and calls their CLI registrars to add commands to the program.
    
    Args:
        program: CLI program instance (e.g., argparse, click, typer)
        config: Optional OpenClaw configuration
        workspace_dir: Optional workspace directory
    """
    if config is None:
        from openclaw.config.loader import load_config
        config_obj = load_config()
        config = config_obj.model_dump() if hasattr(config_obj, "model_dump") else {}
    
    if isinstance(workspace_dir, str):
        workspace_dir = Path(workspace_dir)
    
    # Load plugins
    try:
        registry = await load_gateway_plugins(config, workspace_dir)
    except Exception as e:
        logger.error(f"Failed to load plugins for CLI: {e}")
        return
    
    # Get CLI registrars
    if not hasattr(registry, "cli_registrars"):
        logger.debug("No CLI registrars found in plugin registry")
        return
    
    plugin_logger = create_plugin_loader_logger(logger)
    
    # Iterate over registrars and call each one
    for registrar in registry.cli_registrars:
        try:
            # Call registrar with program, config, workspace_dir, logger
            registrar_fn = getattr(registrar, "register", None) or registrar
            
            if callable(registrar_fn):
                registrar_fn(
                    program=program,
                    config=config,
                    workspace_dir=str(workspace_dir) if workspace_dir else None,
                    logger=plugin_logger,
                )
                logger.debug(f"Registered CLI commands from {registrar}")
        except Exception as e:
            logger.error(f"Failed to register CLI commands from {registrar}: {e}")


__all__ = ["register_plugin_cli_commands"]
