"""
Configuration hot reload system.

Watches config file for changes and applies them without full restart
when possible (hybrid mode).

Reference: openclaw/src/gateway/server-config-watch.ts
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable
from ..config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


class ConfigWatcher:
    """
    Watch configuration file for changes and trigger reload.
    
    Modes:
    - Hybrid (default): Hot-apply safe changes, restart on critical
    - Manual: Require explicit restart
    
    Usage:
        watcher = ConfigWatcher(
            config_path=resolve_state_dir() / "openclaw.json",
            reload_callback=gateway.reload_config
        )
        
        await watcher.start()
        
        # Later...
        await watcher.stop()
    """
    
    def __init__(
        self,
        config_path: Path,
        reload_callback: Callable[[dict], Any],
        check_interval: float = 5.0
    ):
        """
        Initialize config watcher.
        
        Args:
            config_path: Path to config file
            reload_callback: Callback to call on config change
            check_interval: Check interval in seconds
        """
        self._config_path = config_path
        self._reload_callback = reload_callback
        self._check_interval = check_interval
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_mtime: float | None = None
    
    async def start(self) -> None:
        """Start watching config file"""
        if self._running:
            logger.warning("Config watcher already running")
            return
        
        if not self._config_path.exists():
            logger.warning(f"Config file not found: {self._config_path}")
            return
        
        self._last_mtime = self._config_path.stat().st_mtime
        self._running = True
        self._task = asyncio.create_task(self._watch_loop())
        
        logger.info(f"Config watcher started: {self._config_path}")
    
    async def stop(self) -> None:
        """Stop watching config file"""
        self._running = False
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        
        logger.info("Config watcher stopped")
    
    async def _watch_loop(self) -> None:
        """Main watch loop"""
        while self._running:
            try:
                await asyncio.sleep(self._check_interval)
                
                if not self._config_path.exists():
                    continue
                
                current_mtime = self._config_path.stat().st_mtime
                
                if current_mtime > self._last_mtime:
                    logger.info("Config file changed, reloading...")
                    self._last_mtime = current_mtime
                    
                    # Trigger reload
                    await self._trigger_reload()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Config watch error: {e}", exc_info=True)
    
    async def _trigger_reload(self) -> None:
        """Trigger config reload via callback"""
        try:
            # Load new config
            import json
            with open(self._config_path, 'r') as f:
                new_config = json.load(f)
            
            # Call reload callback
            if asyncio.iscoroutinefunction(self._reload_callback):
                await self._reload_callback(new_config)
            else:
                self._reload_callback(new_config)
                
        except Exception as e:
            logger.error(f"Config reload failed: {e}", exc_info=True)


def determine_restart_required(
    old_config: dict,
    new_config: dict
) -> bool:
    """
    Determine if config change requires full restart.
    
    Critical changes requiring restart:
    - Port changes
    - TLS certificate changes
    - Auth mode changes
    - Provider changes
    
    Safe changes (hot-reloadable):
    - Tool policies
    - Agent settings
    - Channel settings
    - Heartbeat config
    
    Args:
        old_config: Previous configuration
        new_config: New configuration
        
    Returns:
        True if restart required, False if hot-reload possible
    """
    # Check gateway port
    old_port = old_config.get('gateway', {}).get('port', 18789)
    new_port = new_config.get('gateway', {}).get('port', 18789)
    if old_port != new_port:
        return True
    
    # Check TLS settings
    old_tls = old_config.get('gateway', {}).get('tls', {})
    new_tls = new_config.get('gateway', {}).get('tls', {})
    if old_tls != new_tls:
        return True
    
    # Check auth mode
    old_auth = old_config.get('gateway', {}).get('auth', {})
    new_auth = new_config.get('gateway', {}).get('auth', {})
    if old_auth.get('mode') != new_auth.get('mode'):
        return True
    
    # Check provider changes
    old_providers = old_config.get('providers', {})
    new_providers = new_config.get('providers', {})
    if set(old_providers.keys()) != set(new_providers.keys()):
        return True
    
    # All other changes can be hot-reloaded
    return False


__all__ = [
    "ConfigWatcher",
    "determine_restart_required",
]
