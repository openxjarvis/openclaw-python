"""Gateway sidecar services startup

Coordinates startup of all sidecar services.
Matches TypeScript openclaw/src/gateway/server-startup.ts
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .server_browser import start_browser_control_server_if_enabled
from .server_canvas import start_canvas_host_server
from ..hooks.gmail_watcher import start_gmail_watcher
from ..hooks.loader import load_internal_hooks
from ..hooks.internal_hooks import (
    clear_internal_hooks,
    create_internal_hook_event,
    trigger_internal_hook,
    GatewayStartupHookContext,
    GatewayStartupHookEvent,
)
from ..plugins.services import start_plugin_services
from ..infra.device_identity import load_or_create_device_identity
from ..infra.outbound.delivery_queue import ensure_queue_dir
from ..infra.ensure_completions import ensure_completion_scripts
from ..config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


async def start_gateway_sidecars(params: dict) -> dict:
    """
    Start all Gateway sidecar services
    
    Sidecars:
    0. Initialize core infrastructure (identity, delivery-queue)
    1. Internal Hooks
    2. Browser Control Server
    3. Gmail Watcher
    4. Plugin Services
    5. Canvas Host Server
    
    Args:
        params: Dict containing:
            - cfg: Gateway config
            - plugin_registry: Plugin registry
            - workspace_dir: Workspace directory
            - default_workspace_dir: Default workspace directory
            - log_browser: Browser logger
            - log_hooks: Hooks logger
            - deps: CLI dependencies (optional)
            
    Returns:
        Dict with sidecar info
    """
    cfg = params.get("cfg", {})
    plugin_registry = params.get("plugin_registry", {})
    workspace_dir = params.get("workspace_dir", Path.home())
    default_workspace_dir = params.get("default_workspace_dir") or str(workspace_dir)
    log_browser = params.get("log_browser", logger)
    log_hooks = params.get("log_hooks", logger)
    deps = params.get("deps")
    
    results = {
        "browser_control": None,
        "gmail_watcher": None,
        "plugin_services": None,
        "canvas_host": None,
        "hooks_loaded": 0,
        "device_identity": None,
        "delivery_queue_initialized": False,
        "completions_initialized": False,
    }
    
    # 0. Initialize core infrastructure (TS alignment)
    # TS: openclaw/src/gateway/server-startup.ts ensures identity/, delivery-queue/, completions/
    try:
        # Initialize device identity
        device_identity = load_or_create_device_identity()
        results["device_identity"] = device_identity.device_id
        logger.info(f"Device identity initialized: {device_identity.device_id[:16]}...")
        
        # Ensure device-auth.json exists (TS alignment)
        from ..infra.device_auth_store import load_device_auth_store, save_device_auth_store
        try:
            auth_store = load_device_auth_store(device_identity.device_id)
            logger.debug(f"Device auth store initialized for {device_identity.device_id[:16]}...")
        except Exception as auth_err:
            logger.debug(f"Device auth store creation skipped: {auth_err}")
        
        # Initialize delivery queue directories
        state_dir = resolve_state_dir()
        queue_dir, failed_dir = ensure_queue_dir(state_dir)
        results["delivery_queue_initialized"] = True
        logger.debug(f"Delivery queue directories initialized: {queue_dir}")
        
        # Initialize shell completion scripts
        completion_results = ensure_completion_scripts()
        results["completions_initialized"] = any(completion_results.values())
        if results["completions_initialized"]:
            logger.debug("Shell completion scripts initialized")
        
        # Initialize devices directory (TS alignment)
        from ..auth.device_pairing import DevicePairingManager
        devices_manager = DevicePairingManager()  # 会自动创建 devices/ 并加载状态
        # Ensure devices directory and empty state files exist
        devices_manager.state_dir.mkdir(parents=True, exist_ok=True)
        if not devices_manager.paired_path.exists():
            devices_manager.paired_path.write_text("{}")
        if not devices_manager.pending_path.exists():
            devices_manager.pending_path.write_text("{}")
        results["devices_initialized"] = True
        logger.debug(f"Devices directory initialized: {devices_manager.state_dir}")
        
        # Initialize telegram directory
        from ..channels.telegram.update_offset_store import ensure_telegram_dir
        telegram_dir = ensure_telegram_dir()
        results["telegram_dir_initialized"] = True
        logger.debug(f"Telegram directory initialized: {telegram_dir}")
        
        # Initialize credentials directory (TS alignment)
        credentials_dir = state_dir / "credentials"
        credentials_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Create empty pairing files if they don't exist
        telegram_pairing = credentials_dir / "telegram-pairing.json"
        if not telegram_pairing.exists():
            telegram_pairing.write_text('{"version": 1, "requests": []}')
            telegram_pairing.chmod(0o600)
        telegram_allow = credentials_dir / "telegram-allowFrom.json"
        if not telegram_allow.exists():
            telegram_allow.write_text('{"version": 1, "allowFrom": []}')
            telegram_allow.chmod(0o600)
        results["credentials_initialized"] = True
        logger.debug(f"Credentials directory initialized: {credentials_dir}")
        
        # Initialize agents directory (TS alignment)
        agents_dir = state_dir / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
        main_agent_dir = agents_dir / "main"
        main_agent_dir.mkdir(parents=True, exist_ok=True)
        sessions_dir = main_agent_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        results["agents_dir_initialized"] = True
        logger.debug(f"Agents directory initialized: {agents_dir}")
        
        # Initialize cron directory (TS alignment)
        cron_dir = state_dir / "cron"
        cron_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
        jobs_file = cron_dir / "jobs.json"
        if not jobs_file.exists():
            jobs_file.write_text('{"version": 1, "jobs": []}')
        jobs_bak = cron_dir / "jobs.json.bak"
        if not jobs_bak.exists():
            jobs_bak.write_text('{"version": 1, "jobs": []}')
        results["cron_dir_initialized"] = True
        logger.debug(f"Cron directory initialized: {cron_dir}")
        
        # Ensure error log file exists
        log_dir = state_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        err_log = log_dir / "gateway.err.log"
        if not err_log.exists():
            err_log.touch()
            logger.debug("Created gateway.err.log")
        results["err_log_initialized"] = True
        
        # Initialize git repository in workspace (TS alignment)
        from ..agents.ensure_workspace import _ensure_git_repo
        if (workspace_dir / ".git").exists():
            logger.debug("Git repository already exists in workspace")
        else:
            try:
                _ensure_git_repo(workspace_dir)
                logger.info("Git repository initialized in workspace")
                results["git_initialized"] = True
            except Exception as git_err:
                logger.debug(f"Git init skipped: {git_err}")
                results["git_initialized"] = False
        
    except Exception as e:
        logger.error(f"Failed to initialize core infrastructure: {e}", exc_info=True)
    
    # 1. Load internal hook handlers from configuration and directory discovery
    try:
        # Clear any previously registered hooks to ensure fresh loading
        clear_internal_hooks()
        loaded_count = await load_internal_hooks(cfg, default_workspace_dir)
        results["hooks_loaded"] = loaded_count
        if loaded_count > 0:
            log_hooks.info(f"loaded {loaded_count} internal hook handler{'s' if loaded_count > 1 else ''}")
    except Exception as e:
        log_hooks.error(f"failed to load hooks: {e}", exc_info=True)
    
    # 2. Start Browser Control Server
    try:
        browser_control = await start_browser_control_server_if_enabled(cfg or {})
        results["browser_control"] = browser_control
        if browser_control:
            log_browser.info(f"Browser Control Server started on port {browser_control['port']}")
    except Exception as e:
        log_browser.error(f"Browser Control Server failed to start: {e}")
    
    # 3. Start Gmail Watcher
    try:
        gmail_result = await start_gmail_watcher(cfg)
        results["gmail_watcher"] = gmail_result
        if gmail_result.get("started"):
            log_hooks.info("Gmail watcher started")
        elif gmail_result.get("reason") not in ("hooks not enabled", "no gmail account configured"):
            log_hooks.warn(f"Gmail watcher not started: {gmail_result.get('reason')}")
    except Exception as e:
        log_hooks.error(f"Gmail watcher failed to start: {e}")
    
    # 4. Start Plugin Services
    if plugin_registry:
        try:
            plugin_services = await start_plugin_services(plugin_registry, workspace_dir)
            results["plugin_services"] = plugin_services
            logger.info("Plugin services started")
        except Exception as e:
            logger.error(f"Plugin services failed to start: {e}")
    
    # 5. Start Canvas Host Server
    try:
        canvas_host = await start_canvas_host_server(cfg)
        results["canvas_host"] = canvas_host
        if canvas_host:
            logger.info(f"Canvas Host Server started on port {canvas_host['port']}")
    except Exception as e:
        logger.error(f"Canvas Host Server failed to start: {e}")
    
    # 6. Trigger gateway:startup hook event
    # Use `or {}` to handle cfg["hooks"] = None (Pydantic model_dump includes None values)
    if (cfg.get("hooks") or {}).get("internal", {}).get("enabled"):
        # Small delay to let services fully initialize
        async def trigger_startup_hook():
            await asyncio.sleep(0.25)
            ctx = GatewayStartupHookContext(
                cfg=cfg,
                deps=deps,
                workspace_dir=default_workspace_dir,
            )
            hook_event = GatewayStartupHookEvent(
                type="gateway",
                action="startup",
                session_key="gateway:startup",
                context=ctx,
            )
            await trigger_internal_hook(hook_event)

        # Create task to trigger hook in background
        asyncio.create_task(trigger_startup_hook())
    
    return results
