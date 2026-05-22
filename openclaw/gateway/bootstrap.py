"""Gateway bootstrap sequence (matches TypeScript gateway/server.impl.ts startGatewayServer)

Implements the full 40-step TypeScript gateway initialization in Python.
"""
from __future__ import annotations


import asyncio
import logging
import os
import platform
import sys
from pathlib import Path
from typing import Any

from openclaw.config.paths import resolve_state_dir


# ✅ Setup global exception handler early (before any async code runs)
def _setup_global_exception_handler():
    """Install a global exception handler for uncaught exceptions.
    
    Mirrors TypeScript process.on('uncaughtException') and process.on('unhandledRejection').
    This ensures we always log unexpected errors instead of silently failing.
    """
    _original_excepthook = sys.excepthook
    _original_unraisablehook = sys.unraisablehook
    _logger = logging.getLogger("openclaw.gateway.bootstrap")
    
    def handle_exception(exc_type, exc_value, exc_traceback):
        # Allow KeyboardInterrupt to propagate normally
        if issubclass(exc_type, KeyboardInterrupt):
            _original_excepthook(exc_type, exc_value, exc_traceback)
            return
        
        _logger.critical(
            "🔥 UNCAUGHT EXCEPTION",
            exc_info=(exc_type, exc_value, exc_traceback)
        )
    
    def handle_unraisable(unraisable):
        _logger.error(
            f"🔥 UNRAISABLE EXCEPTION in {unraisable.object}: {unraisable.exc_value}",
            exc_info=(type(unraisable.exc_value), unraisable.exc_value, unraisable.exc_traceback)
        )
        if _original_unraisablehook:
            _original_unraisablehook(unraisable)
    
    sys.excepthook = handle_exception
    sys.unraisablehook = handle_unraisable

# Install handler immediately when module is imported
_setup_global_exception_handler()


# Module-level imports so tests can patch openclaw.gateway.bootstrap.load_config etc.
try:
    from ..config.loader import load_config
except ImportError:
    load_config = None  # type: ignore[assignment]

try:
    from ..config.loader import detect_legacy_config
except ImportError:
    try:
        from ..config.legacy import detect_legacy_config
    except ImportError:
        detect_legacy_config = None  # type: ignore[assignment]

try:
    from ..infra.diagnostic_events import start_diagnostic_heartbeat
except ImportError:
    start_diagnostic_heartbeat = None  # type: ignore[assignment]


def _config_as_dict(cfg) -> dict:
    """Convert config object or dict to plain dict safely.

    Uses exclude_none=True so that callers can safely use
    .get("key", {}) without hitting None — mirrors TS optional-chaining
    behaviour where absent/null keys produce undefined (not crash).
    """
    if cfg is None:
        return {}
    if isinstance(cfg, dict):
        # Strip explicit None values for consistency with model_dump path
        return {k: v for k, v in cfg.items() if v is not None}
    if hasattr(cfg, "model_dump"):
        return cfg.model_dump(exclude_none=True)
    return {}

logger = logging.getLogger(__name__)


class GatewayBootstrap:
    """
    Complete gateway bootstrap sequence matching TypeScript.
    
    Steps:
    1. Set environment variables
    2. Load and validate config
    3. Migrate legacy config if needed
    4. Start diagnostic heartbeat
    5. Initialize subagent registry
    6. Resolve default agent and workspace
    7. Load gateway plugins
    8. Create channel logs and runtime envs
    9. Resolve runtime config (bind, TLS, auth)
    10. Create default deps
    11. Create runtime state
    12. Build cron service
    13. Create channel manager
    14. Start discovery service
    15. Register skills change listener
    16. Start maintenance timers
    17. Register agent event handler
    18. Start heartbeat runner
    19. Start cron service
    20. Create exec approval manager
    21. Attach WebSocket handlers
    22. Log startup
    23. Start config reloader
    24. Create close handler
    """
    
    def __init__(self):
        self.config = None
        self.runtime = None
        self.provider = None  # legacy alias for self.runtime
        self.session_manager = None
        self.server = None
        self.tool_registry = None
        self.channel_manager = None
        self.skill_loader = None
        self.cron_service = None
        self.discovery = None
        self.config_reloader = None
        self.heartbeat_stop = None
        self._heartbeat_managers: list = []
        self._heartbeat_wake_disposer = None
        self.plugin_registry = None
        self._hook_runner = None
        self._maintenance_tasks: list[asyncio.Task] = []
        self._close_handlers: list[Any] = []
        self._gateway_lock = None  # GatewayLockHandle — released in shutdown()

    async def bootstrap(self, config_path: Path | None = None, allow_unconfigured: bool = False, force: bool = False) -> dict[str, Any]:
        """
        Run the full bootstrap sequence.
        
        Args:
            config_path: Optional path to configuration file
            allow_unconfigured: If True, continue even without config (for testing/dev)
        
        Returns:
            Dict with all initialized components
        """
        results = {"steps_completed": 0, "errors": []}

        # Record gateway start time for uptime tracking (mirrors TS process.uptime()).
        try:
            from openclaw.gateway.uptime import record_start_time
            record_start_time()
        except Exception:
            pass

        # Pre-Step 0: Acquire gateway PID lock (mirrors TS acquireGatewayLock).
        # This MUST happen before any channels start polling to ensure only one
        # gateway instance is ever running.  Without this, two instances can both
        # start Telegram polling simultaneously and fight forever with 409 Conflicts.
        try:
            from ..infra.gateway_lock import acquire_gateway_lock
            from ..config.paths import resolve_gateway_port, DEFAULT_GATEWAY_PORT
            from ..gateway.error_codes import GatewayLockError

            _lock_config_path = str(config_path) if config_path else None
            _lock_port = DEFAULT_GATEWAY_PORT  # best-effort; real port resolved later
            self._gateway_lock = acquire_gateway_lock(
                config_path=_lock_config_path,
                port=_lock_port,
                force=force,
            )
            logger.info("Gateway PID lock acquired (pid=%d)", os.getpid())
        except Exception as _lock_exc:
            from ..gateway.error_codes import GatewayLockError
            if isinstance(_lock_exc, GatewayLockError):
                raise
            # Non-fatal: if lock infra is broken, log and continue (avoids boot failure
            # on unusual filesystems / restricted environments).
            logger.warning("Gateway PID lock unavailable: %s — proceeding without lock", _lock_exc)

        # Pre-Step: Validate configuration exists (matches TS behavior)
        # Note: config_path is passed from CLI, defaults to openclaw.json not config.json
        if config_path:
            config_path_resolved = config_path
        else:
            # Check both possible config locations
            state_dir = resolve_state_dir()
            config_path_resolved = state_dir / "openclaw.json"
            if not config_path_resolved.exists():
                config_path_resolved = state_dir / "config.json"
        
        if not config_path_resolved.exists() and not allow_unconfigured:
            # First-run: attempt interactive onboarding (mirrors TS first-run flow)
            try:
                # Import via the module so tests can patch openclaw.wizard.onboarding.run_interactive_onboarding
                import openclaw.wizard.onboarding as _onboarding_mod
                _run_onboarding = getattr(_onboarding_mod, "run_interactive_onboarding", None)
                if _run_onboarding is None:
                    raise NotImplementedError("run_interactive_onboarding not available")
                logger.info("No config found — running interactive onboarding")
                await _run_onboarding(config_path=config_path_resolved)
                # After onboarding, proceed even if config wasn't created
                # (in tests, onboarding may be mocked to do nothing)
            except (ImportError, NotImplementedError):
                error_msg = (
                    "Configuration not found. Please run onboarding first:\n"
                    "  $ uv run openclaw onboard\n"
                    "or use setup command:\n"
                    "  $ uv run openclaw setup --wizard"
                )
                logger.error(error_msg)
                raise RuntimeError(error_msg)
        
        # Step 1: Set environment variables
        logger.info("Step 1: Setting environment variables")
        self._set_env_vars()
        results["steps_completed"] += 1
        
        # Step 2: Load and validate config
        logger.info("Step 2: Loading configuration")
        try:
            # Use module-level load_config (patchable in tests)
            import openclaw.gateway.bootstrap as _self_module
            _load_config = _self_module.load_config or (lambda p: {})
            self.config = _load_config(config_path)
            results["steps_completed"] += 1
            # Initialise the config service singleton with the resolved config path so
            # that gateway RPC methods (config.get / config.patch / config.save) can
            # read and write the live config file.
            try:
                from openclaw.gateway.config_service import ConfigService, set_config_service
                set_config_service(ConfigService(config_path_resolved))
            except Exception as _cs_err:
                logger.warning("Could not initialize config service: %s", _cs_err)
        except Exception as e:
            logger.error(f"Config load failed: {e}")
            results["errors"].append(f"config: {e}")
            return results
        
        # Step 3: Migrate legacy config
        logger.info("Step 3: Checking legacy config")
        try:
            from ..config.legacy import detect_legacy_config, migrate_legacy_config
            legacy = detect_legacy_config()
            if legacy:
                migrate_legacy_config(legacy)
                logger.info(f"Migrated legacy config from {legacy}")
        except Exception as e:
            logger.warning(f"Legacy migration skipped: {e}")
        results["steps_completed"] += 1
        
        # Step 3.5: Run gateway update check (writes ~/.openclaw/update-check.json)
        try:
            from ..infra.update_startup import run_gateway_update_check
            asyncio.create_task(run_gateway_update_check(self.config or {}))
        except Exception as exc:
            logger.debug("update-check: skipped: %s", exc)

        # Step 4: Start diagnostic heartbeat + stability recorder
        logger.info("Step 4: Starting diagnostic heartbeat")
        try:
            # Use module-level import so tests can patch it
            import openclaw.gateway.bootstrap as _bmod
            _sdh = _bmod.start_diagnostic_heartbeat
            if _sdh:
                _sdh()
            from openclaw.diagnostics.stability import start_diagnostic_stability_recorder
            start_diagnostic_stability_recorder()
        except Exception as e:
            logger.warning(f"Diagnostic heartbeat failed: {e}")
        results["steps_completed"] += 1
        
        # Step 4.5: Initialize OpenTelemetry diagnostics plugin (mirrors TS diagnostics-otel)
        logger.info("Step 4.5: Initializing OpenTelemetry diagnostics plugin")
        try:
            cfg_dict = _config_as_dict(self.config)
            diagnostics_cfg = cfg_dict.get("diagnostics") or {}
            otel_cfg = diagnostics_cfg.get("otel") or {}
            if diagnostics_cfg.get("enabled", False) and otel_cfg.get("enabled", False):
                import sys, os as _os
                # Add extensions directory to path so plugin.py can be imported
                _ext_dir = _os.path.join(_os.path.dirname(__file__), "..", "..", "extensions", "diagnostics-otel")
                _ext_dir = _os.path.normpath(_ext_dir)
                if _ext_dir not in sys.path:
                    sys.path.insert(0, _ext_dir)
                try:
                    from extensions.diagnostics_otel import plugin as _otel_plugin  # type: ignore[import]
                    _otel_plugin.plugin["register"](type("_OtelAPI", (), {"config": cfg_dict})())
                    logger.info("OpenTelemetry diagnostics plugin initialized")
                except ImportError:
                    # Try direct import from the extensions package
                    import importlib.util as _ilu
                    _spec = _ilu.spec_from_file_location("diagnostics_otel_plugin", _os.path.join(_ext_dir, "plugin.py"))
                    if _spec and _spec.loader:
                        _mod = _ilu.module_from_spec(_spec)
                        _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
                        _mod.plugin["register"](type("_OtelAPI", (), {"config": cfg_dict})())  # type: ignore[attr-defined]
                        logger.info("OpenTelemetry diagnostics plugin initialized (direct import)")
        except Exception as _otel_exc:
            logger.debug(f"OTel diagnostics plugin init skipped: {_otel_exc}")
        results["steps_completed"] += 0.5

        # Step 5: Initialize subagent registry
        logger.info("Step 5: Initializing subagent registry")
        # Subagent registry is lightweight - just a dict
        self._subagent_registry: dict[str, Any] = {}
        results["steps_completed"] += 1
        
        # Step 6: Resolve default agent and workspace
        logger.info("Step 6: Resolving default agent and workspace directory")
        
        # Use new agent scope functions (aligned with TS)
        try:
            from openclaw.agents.agent_scope import (
                resolve_default_agent_id,
                resolve_agent_workspace_dir,
            )
            
            default_agent_id = resolve_default_agent_id(self.config or {})
            workspace_dir = resolve_agent_workspace_dir(self.config or {}, default_agent_id)
            logger.info(f"Default agent: {default_agent_id}, workspace: {workspace_dir}")
        except Exception as e:
            logger.warning(f"Failed to use agent scope functions: {e}, falling back to default")
            # Fallback to legacy behavior
            if self.config and hasattr(self.config, 'agent') and hasattr(self.config.agent, 'workspace'):
                workspace_dir = Path(self.config.agent.workspace).expanduser().resolve()
            else:
                state_dir = resolve_state_dir()
                workspace_dir = state_dir / "workspace"
        
        # Ensure workspace exists with bootstrap files (matching TypeScript behavior)
        try:
            from ..agents.ensure_workspace_and_sessions import ensure_workspace_and_sessions
            skip_bootstrap = False
            if self.config and hasattr(self.config, 'agent'):
                skip_bootstrap = getattr(self.config.agent, 'skip_bootstrap', False)
            
            result = ensure_workspace_and_sessions(
                workspace_dir=workspace_dir,
                skip_bootstrap=skip_bootstrap,
            )
            logger.info(f"Workspace initialized: {workspace_dir}")
        except Exception as e:
            logger.warning(f"Failed to initialize workspace bootstrap files: {e}")
            workspace_dir.mkdir(parents=True, exist_ok=True)
        
        results["workspace_dir"] = str(workspace_dir)
        results["steps_completed"] += 1
        
        # Step 7: Load gateway plugins (mirrors TS loadGatewayPlugins)
        logger.info("Step 7: Loading gateway plugins")
        self.plugin_registry = None
        self._hook_runner = None
        try:
            from ..plugins.plugin_manager import load_gateway_plugins
            from ..plugins.hook_runner import PluginHookRunner
            cfg_dict = _config_as_dict(self.config)
            self.plugin_registry = await load_gateway_plugins(cfg_dict, workspace_dir)
            loaded_count = sum(1 for p in self.plugin_registry.plugins if p.status == "loaded")
            logger.info(
                f"Loaded {loaded_count}/{len(self.plugin_registry.plugins)} plugins "
                f"(tools={len(self.plugin_registry.tools)}, "
                f"typed_hooks={len(self.plugin_registry.typed_hooks)}, "
                f"channels={len(self.plugin_registry.channels)})"
            )
            # Create hook runner from loaded registry
            self._hook_runner = PluginHookRunner(self.plugin_registry)
            # Wire hook runner into global SubagentRegistry for subagent lifecycle hooks
            try:
                from ..agents.subagent_registry import get_global_registry
                _registry = get_global_registry()
                _registry.set_hook_runner(self._hook_runner)
            except Exception as _sub_exc:
                logger.debug(f"Could not wire hook_runner into SubagentRegistry: {_sub_exc}")
        except Exception as e:
            logger.warning(f"Plugin loading skipped: {e}")
            from ..plugins.types import create_empty_plugin_registry
            self.plugin_registry = create_empty_plugin_registry()
        
        # Initialize built-in agent harness registry (PI harness) — must happen
        # after plugins are loaded so plugin harnesses can also register.
        try:
            from ..agents.harness import initialize_harness_registry
            initialize_harness_registry()
            logger.debug("Agent harness registry initialized")
        except Exception as _harness_exc:
            logger.warning("Harness registry initialization failed: %s", _harness_exc)

        # ✅ CRITICAL: Wire gateway reference OUTSIDE the plugin try-except
        # This ensures subagent announce/completion flows work even if plugin loading fails
        try:
            from ..agents.subagent_registry import get_global_registry
            _registry = get_global_registry()
            _registry.set_gateway(self)
            logger.info("✅ Gateway reference wired to SubagentRegistry")
        except Exception as _gw_exc:
            logger.warning(f"❌ Failed to wire gateway into SubagentRegistry: {_gw_exc}")
        
        results["steps_completed"] += 1
        
        # Step 7.1: Check sandbox configuration and Docker availability
        logger.info("Step 7.1: Checking sandbox requirements")
        try:
            await self._check_sandbox_requirements()
        except Exception as e:
            logger.warning(f"Sandbox check failed: {e}")
        
        # Step 7.5: Start gateway sidecar services (TS alignment)
        logger.info("Step 7.5: Starting gateway sidecar services")
        try:
            from .server_startup import start_gateway_sidecars
            
            sidecar_params = {
                "cfg": _config_as_dict(self.config),
                "plugin_registry": self.plugin_registry,
                "workspace_dir": workspace_dir,
                "default_workspace_dir": str(workspace_dir),
                "log_browser": logger,
                "log_hooks": logger,
                "deps": None,  # Will be set up later
            }
            
            sidecar_results = await start_gateway_sidecars(sidecar_params)
            results["sidecar_services"] = sidecar_results
            logger.info(f"Sidecar services initialized: {list(sidecar_results.keys())}")
        except Exception as e:
            logger.error(f"Failed to start sidecar services: {e}", exc_info=True)
            results["errors"].append(f"sidecar_services: {e}")
        results["steps_completed"] += 0.5
        
        # Step 8: Create agent runtime — uses pi_coding_agent.AgentSession
        logger.info("Step 8: Creating agent runtime (pi_coding_agent)")
        try:
            # Resolve model + fallbacks from config (mirrors TS agents.defaults.model)
            from openclaw.config.schema import ModelConfig as _ModelConfig
            primary_model = "google/gemini-2.0-flash"
            fallback_models: list[str] = []
            if self.config.agents and self.config.agents.defaults:
                raw_model = self.config.agents.defaults.model
                if isinstance(raw_model, _ModelConfig):
                    primary_model = raw_model.primary
                    fallback_models = list(raw_model.fallbacks or [])
                elif isinstance(raw_model, dict):
                    primary_model = raw_model.get("primary", primary_model)
                    fallback_models = list(raw_model.get("fallbacks", []))
                else:
                    primary_model = str(raw_model)

            logger.info(
                f"Creating PiAgentRuntime with model: {primary_model}"
                + (f" (fallbacks: {fallback_models})" if fallback_models else "")
            )

            from ..gateway.pi_runtime import PiAgentRuntime
            self.runtime = PiAgentRuntime(
                model=primary_model,
                fallback_models=fallback_models,
                cwd=workspace_dir,
                config=_config_as_dict(self.config),
                hook_runner=self._hook_runner,
            )

            # Also keep a legacy reference for any code that checks type
            self.provider = self.runtime

            # Extensions (non-critical, best-effort)
            try:
                from ..extensions.runtime import ExtensionRuntime
                from ..extensions.memory_extension import create_memory_extension
                from ..extensions.api import ExtensionAPI
                from ..extensions.types import ExtensionContext

                extension_runtime = ExtensionRuntime()
                extension_context = ExtensionContext(
                    agent_id="main",
                    session_id=None,
                    workspace_dir=workspace_dir,
                    logger=logger,
                )
                extension_runtime.set_context(extension_context)

                memory_enabled = getattr(self.config, "memory_enabled", True)
                if memory_enabled:
                    memory_config = {
                        "auto_recall": getattr(self.config, "memory_auto_recall", True),
                        "auto_capture": getattr(self.config, "memory_auto_capture", False),
                        "min_score": getattr(self.config, "memory_min_score", 0.3),
                        "max_results": getattr(self.config, "memory_max_results", 3),
                    }
                    memory_ext = create_memory_extension(workspace_dir, memory_config)
                    ext_api = ExtensionAPI(
                        extension_id="memory-extension",
                        context=extension_context,
                    )
                    memory_ext.register(ext_api)
                    extension_runtime.register_handlers(ext_api._handlers)
                    logger.info(f"Memory extension registered (auto_recall={memory_config['auto_recall']})")

                self.runtime.extension_runtime = extension_runtime  # type: ignore[attr-defined]
                logger.info("Extensions runtime initialized")
            except Exception as ext_err:
                logger.warning(f"Failed to initialize extensions: {ext_err}")

            logger.info("PiAgentRuntime created")
        except Exception as e:
            logger.error(f"Runtime creation failed: {e}")
            results["errors"].append(f"runtime: {e}")
        results["steps_completed"] += 1

        # Step 9: Create session manager
        logger.info("Step 9: Creating session manager")
        try:
            from ..agents.session import SessionManager
            self.session_manager = SessionManager(workspace_dir=workspace_dir)
        except Exception as e:
            logger.error(f"Session manager creation failed: {e}")
            results["errors"].append(f"session_manager: {e}")
        results["steps_completed"] += 1
        
        # Step 10: Create tool registry
        logger.info("Step 10: Creating tool registry")
        try:
            from ..agents.tools.registry import ToolRegistry
            self.tool_registry = ToolRegistry(
                session_manager=self.session_manager,
                gateway=self,  # ✅ Pass gateway reference (self is the Gateway instance)
                auto_register=True,
            )
            
            # Register new tools
            from ..agents.tools.gateway import GatewayTool
            self.tool_registry.register(GatewayTool())

            # AgentsListTool — lets agent enumerate configured sub-agents (TS: agents-list-tool.ts)
            try:
                from ..agents.tools.agents_list import AgentsListTool
                cfg_dict = _config_as_dict(self.config)
                self.tool_registry.register(AgentsListTool(config=cfg_dict))
            except Exception as _e:
                logger.debug("AgentsListTool registration skipped: %s", _e)
            
            # Step 10b: Register plugin tools from the plugin registry into the tool registry.
            # Must happen HERE (before ChannelManager is created at Step 13) so that the
            # ChannelManager's frozen tools list includes all plugin tools (e.g. feishu_doc,
            # feishu_chat, feishu_bitable, etc.). Channels don't need to be running yet —
            # ChannelHandlerTool resolves the channel lazily via get_global_channel_registry().
            if self.plugin_registry:
                _plugin_tool_count = 0
                for _reg in (self.plugin_registry.tools or []):
                    try:
                        from ..plugins.channel_tool import ChannelHandlerTool
                        if isinstance(_reg.factory, ChannelHandlerTool):
                            try:
                                self.tool_registry.register(_reg.factory)
                                _plugin_tool_count += 1
                            except Exception as _te:
                                logger.debug("Could not register plugin tool %s: %s", _reg.names, _te)
                    except Exception:
                        pass
                if _plugin_tool_count:
                    logger.info(f"Registered {_plugin_tool_count} plugin tools from plugin registry")

            tool_count = len(self.tool_registry.list_tools())
            logger.info(f"Registered {tool_count} tools")
        except Exception as e:
            logger.error(f"Tool registry creation failed: {e}")
            results["errors"].append(f"tool_registry: {e}")
        results["steps_completed"] += 1
        
        # Step 11: Load skills
        logger.info("Step 11: Loading skills")
        try:
            from ..skills.loader import SkillLoader
            
            # Get project root (where skills/ directory is)
            project_root = Path(__file__).parent.parent.parent
            bundled_skills_dir = project_root / "skills"
            
            # SkillLoader expects config dict
            skill_config = {}
            if self.config and hasattr(self.config, 'skills'):
                skill_config = self.config.skills.model_dump() if hasattr(self.config.skills, 'model_dump') else {}
            
            self.skill_loader = SkillLoader(config=skill_config)
            loaded_skills = self.skill_loader.load_from_directory(bundled_skills_dir, source="bundled")
            # Store loaded skills in the loader's skills dict so _log_startup and other
            # consumers can access them via skill_loader.skills
            for skill in loaded_skills:
                self.skill_loader.skills[skill.name] = skill
            skill_count = len(loaded_skills)
            logger.info(f"Loaded {skill_count} skills from {bundled_skills_dir}")

            # Step 11b: Load skills from plugin directories (e.g. extensions/feishu/skills/).
            # Each plugin's openclaw.plugin.json can list skill directories via "skills": ["./skills"].
            # Without this, agents cannot see feishu tool usage patterns and resort to browser.
            if self.plugin_registry:
                _plugin_skill_count = 0
                for _prec in self.plugin_registry.plugins:
                    if _prec.status != "loaded" or not _prec.source:
                        continue
                    _plugin_py = Path(_prec.source)
                    _plugin_dir = _plugin_py.parent if _plugin_py.is_file() else _plugin_py
                    # Look for openclaw.plugin.json or plugin.json for skills list
                    for _manifest_name in ("openclaw.plugin.json", "plugin.json"):
                        _manifest_path = _plugin_dir / _manifest_name
                        if not _manifest_path.is_file():
                            continue
                        try:
                            import json as _json
                            _manifest = _json.loads(_manifest_path.read_text())
                            for _skill_rel in (_manifest.get("skills") or []):
                                _skill_dir = (_plugin_dir / _skill_rel).resolve()
                                if _skill_dir.is_dir():
                                    _pskills = self.skill_loader.load_from_directory(
                                        _skill_dir, source=f"plugin:{_prec.id}"
                                    )
                                    for _ps in _pskills:
                                        self.skill_loader.skills[_ps.name] = _ps
                                    if _pskills:
                                        _plugin_skill_count += len(_pskills)
                                        logger.debug(
                                            f"Loaded {len(_pskills)} skills for plugin '{_prec.id}' from {_skill_dir}"
                                        )
                        except Exception as _se:
                            logger.debug(f"Plugin skill loading error for {_prec.id}: {_se}")
                        break  # Use first manifest found
                if _plugin_skill_count:
                    logger.info(f"Loaded {_plugin_skill_count} plugin skills from plugin registry")
        except Exception as e:
            logger.warning(f"Skills loading failed: {e}")
        results["steps_completed"] += 1
        
        # Step 11.5: Create broadcast function for events
        logger.info("Step 11.5: Creating broadcast function")
        
        # Event queue for events before WebSocket server is ready
        self._event_queue: list[tuple[str, Any, dict | None]] = []
        
        def broadcast(event: str, payload: Any, opts: dict | None = None) -> None:
            """Broadcast event to WebSocket clients"""
            if hasattr(self, 'server') and self.server:
                # WebSocket server is ready, broadcast immediately via async task
                try:
                    asyncio.ensure_future(self.server.broadcast_event(event, payload))
                except Exception as e:
                    logger.warning(f"Broadcast failed: {e}")
            else:
                # Queue event for later (server not yet ready)
                self._event_queue.append((event, payload, opts))
        
        self.broadcast = broadcast
        results["steps_completed"] += 0.5
        
        # Step 12: Build cron service (NOT started yet)
        logger.info("Step 12: Building cron service")
        try:
            from .cron_bootstrap import build_gateway_cron_service
            from .types import GatewayDeps
            
            # Build cron service with dependency container
            self.cron_service_state = await build_gateway_cron_service(
                config=self.config,
                deps=GatewayDeps(
                    provider=self.provider,
                    tools=self.tool_registry.list_tools() if self.tool_registry else [],
                    session_manager=self.session_manager,
                    get_channel_manager=lambda: getattr(self, 'channel_manager', None),  # Lazy access
                ),
                broadcast=broadcast,
            )
            self.cron_service = self.cron_service_state.cron

            # Register in global registry so handlers can find it via get_cron_service()
            from openclaw.cron.service import set_cron_service
            set_cron_service(self.cron_service)

            logger.info(f"Cron service initialized with {len(self.cron_service.jobs)} jobs (start deferred)")
        except Exception as e:
            logger.warning(f"Cron service initialization failed: {e}")
        results["steps_completed"] += 1
        
        # Step 13: Create channel manager and start channels
        logger.info("Step 13: Creating channel manager")
        try:
            from .channel_manager import ChannelManager
            self.channel_manager = ChannelManager(
                default_runtime=self.runtime,
                session_manager=self.session_manager,
                tools=self.tool_registry.list_tools() if self.tool_registry else [],
                workspace_dir=workspace_dir,
            )
            
            # Register and start enabled channels from PluginRegistry.
            # Mirrors TS: listChannelPlugins() → channelManager.startChannels()
            started_count = 0
            channels_config = self.config.channels if self.config else None
            channels_config_dict = (
                channels_config.model_dump() if hasattr(channels_config, "model_dump") else {}
            ) if channels_config else {}

            if self.plugin_registry and self.plugin_registry.channels:
                for channel_reg in self.plugin_registry.channels:
                    channel = channel_reg.plugin
                    channel_id = getattr(channel, "id", None)
                    if not channel_id:
                        continue

                    # Get per-channel config from openclaw.json channels section
                    ch_config_raw = channels_config_dict.get(channel_id, {}) or {}
                    if not isinstance(ch_config_raw, dict):
                        ch_config_raw = {}

                    enabled = ch_config_raw.get("enabled", False)
                    if not enabled:
                        logger.debug(f"Channel '{channel_id}' not enabled, skipping")
                        continue

                    try:
                        self.channel_manager.register_instance(channel, config=ch_config_raw)
                        self.channel_manager.configure(channel_id, ch_config_raw)
                        success = await self.channel_manager.start_channel(channel_id)
                        if success:
                            started_count += 1
                            logger.info(f"✅ Channel '{channel_id}' started")
                        else:
                            logger.warning(f"⚠️  Channel '{channel_id}' start returned False")
                    except Exception as e:
                        logger.warning(f"❌ Failed to start channel '{channel_id}': {e}")

            # Auto-start built-in channels from openclaw.json config as a fallback
            # for when the corresponding extension plugin failed to load or is absent.
            # Channels that have a working extensions/ plugin (telegram, feishu) are
            # already started above via the plugin registry; the duplicate-check below
            # ensures we never start the same channel twice.
            _BUILTIN_CHANNELS: dict[str, str] = {
                "telegram": "openclaw.channels.telegram.enhanced_telegram.EnhancedTelegramChannel",
                "feishu": "openclaw.channels.feishu.channel.FeishuChannel",
            }
            for builtin_id, class_path in _BUILTIN_CHANNELS.items():
                ch_config_raw = channels_config_dict.get(builtin_id)
                if not ch_config_raw:
                    continue
                if isinstance(ch_config_raw, dict):
                    raw = ch_config_raw
                elif hasattr(ch_config_raw, "model_dump"):
                    raw = ch_config_raw.model_dump(by_alias=True, exclude_none=True)
                else:
                    raw = {}
                if not raw.get("enabled", False):
                    logger.debug(f"Built-in channel '{builtin_id}' not enabled, skipping")
                    continue
                if builtin_id in (self.channel_manager._channel_classes or {}):
                    logger.debug(f"Built-in channel '{builtin_id}' already registered (class), skipping")
                    continue
                existing_ch = self.channel_manager._channels.get(builtin_id)
                if existing_ch is not None:
                    if existing_ch.is_running():
                        logger.debug(
                            f"Built-in channel '{builtin_id}' already running (from plugin), skipping"
                        )
                        continue
                    logger.debug(
                        f"Built-in channel '{builtin_id}' already registered (instance), skipping"
                    )
                    continue
                try:
                    module_path, cls_name = class_path.rsplit(".", 1)
                    import importlib
                    mod = importlib.import_module(module_path)
                    cls = getattr(mod, cls_name)
                    self.channel_manager.register(builtin_id, cls)
                    self.channel_manager.configure(builtin_id, raw)
                    success = await self.channel_manager.start_channel(builtin_id)
                    if success:
                        started_count += 1
                        logger.info(f"✅ Built-in channel '{builtin_id}' started")
                    else:
                        logger.warning(f"⚠️  Built-in channel '{builtin_id}' start returned False")
                except Exception as e:
                    logger.warning(f"❌ Failed to start built-in channel '{builtin_id}': {e}", exc_info=True)

            logger.info(f"Started {started_count} channels from plugin registry")

            # Wire global channel registry for ChannelHandlerTool client resolution
            try:
                from ..plugins.channel_tool import set_global_channel_registry
                set_global_channel_registry(self.channel_manager)
            except Exception as _ctr_exc:
                logger.debug("Could not set global channel registry: %s", _ctr_exc)

            # Register channel plugin tools (e.g. feishu_chat, feishu_doc, etc.)
            # These are stored as PluginToolRegistration entries with ChannelHandlerTool
            # factory objects — wire them into the ToolRegistry now that channels are up.
            if self.plugin_registry and self.tool_registry:
                _channel_tool_count = 0
                for reg in (self.plugin_registry.tools or []):
                    from ..plugins.channel_tool import ChannelHandlerTool
                    if isinstance(reg.factory, ChannelHandlerTool):
                        try:
                            self.tool_registry.register(reg.factory)
                            _channel_tool_count += 1
                        except Exception as _te:
                            logger.debug("Could not register channel tool %s: %s", reg.names, _te)
                if _channel_tool_count:
                    logger.info(f"Registered {_channel_tool_count} channel plugin tools")

        except Exception as e:
            logger.error(f"Channel manager creation failed: {e}")
            results["errors"].append(f"channel_manager: {e}")
        results["steps_completed"] += 1
        
        # Step 13b: Start cron service (deferred until channel_manager is ready)
        if self.cron_service:
            try:
                await self.cron_service.start()
                logger.info("Cron service started")
            except Exception as e:
                logger.warning(f"Cron service start failed: {e}")
        
        # Step 13c: Start subagent auto-archive service
        try:
            from openclaw.agents.subagent_archive import start_archive_service
            start_archive_service(self.config, self)
            logger.info("Subagent auto-archive service started")
        except Exception as e:
            logger.warning(f"Subagent auto-archive service failed to start: {e}")

        # Step 14: Start discovery service
        logger.info("Step 14: Starting discovery service")
        # mDNS/Bonjour discovery is optional
        results["steps_completed"] += 1
        
        # Step 15: Register skills change listener
        logger.info("Step 15: Registering skills change listener")
        try:
            from ..agents.skills.refresh import register_skills_change_listener, ensure_skills_watcher
            
            def on_skills_change():
                logger.info("Skills changed, reloading...")
                if self.skill_loader:
                    self.skill_loader.load_all_skills()
            
            register_skills_change_listener(on_skills_change)
            
            # Watch skill directories
            state_dir = resolve_state_dir()
            skill_dirs = [
                Path(__file__).parent.parent / "skills",
                state_dir / "skills",
                workspace_dir / "skills",
            ]
            for d in skill_dirs:
                if d.exists():
                    ensure_skills_watcher(d)
        except Exception as e:
            logger.warning(f"Skills watcher failed: {e}")
        results["steps_completed"] += 1
        
        # Step 16: Start maintenance timers
        logger.info("Step 16: Starting maintenance timers")
        self._start_maintenance_timers()
        results["steps_completed"] += 1
        
        # Step 17: Register event handlers
        logger.info("Step 17: Registering event handlers")
        # Event handlers are registered within the gateway server
        results["steps_completed"] += 1
        
        # Step 18: Start heartbeat runner (HeartbeatManager per agent — mirrors TS startHeartbeatRunner)
        logger.info("Step 18: Starting heartbeat runner")
        try:
            from ..gateway.heartbeat import HeartbeatManager, resolve_heartbeat_config
            from ..infra.heartbeat_wake import set_heartbeat_wake_handler
            from ..agents.agent_scope import resolve_agent_workspace_dir

            self._heartbeat_managers: list[HeartbeatManager] = []
            self._heartbeat_wake_disposer = None

            cfg_dict = _config_as_dict(self.config)
            agents_cfg_dict = cfg_dict.get("agents") or {}
            defaults_cfg = agents_cfg_dict.get("defaults") or {}
            agents_list = agents_cfg_dict.get("agents") or []

            if self.config.agents and self.config.agents.agents:
                for agent in self.config.agents.agents:
                    # Find the per-agent config dict (may be empty if only defaults configured)
                    agent_cfg: dict = {}
                    for a in agents_list:
                        if isinstance(a, dict) and a.get("id") == agent.id:
                            agent_cfg = a
                            break

                    # Resolve workspace directory for empty HEARTBEAT.md check
                    workspace_dir = resolve_agent_workspace_dir(self.config, agent.id)

                    hb_config = resolve_heartbeat_config(agent_cfg, defaults_cfg, workspace_dir=workspace_dir)
                    if hb_config is None or not hb_config.enabled:
                        logger.debug("Heartbeat disabled for agent %s", agent.id)
                        continue

                    hb_mgr = HeartbeatManager(
                        config=hb_config,
                        agent_runtime=self,
                        session_key=f"agent:{agent.id}:main",
                    )
                    self._heartbeat_managers.append(hb_mgr)
                    logger.info(
                        "HeartbeatManager created for agent %s (every=%s)",
                        agent.id,
                        hb_config.every,
                    )

            # Wire the heartbeat wake handler so cron / manual triggers reach all managers
            async def _wake_handler(opts: dict) -> dict:
                for mgr in self._heartbeat_managers:
                    try:
                        await mgr.trigger_now()
                    except Exception as _exc:
                        logger.warning("Heartbeat wake error: %s", _exc)
                return {"status": "ran"}

            self._heartbeat_wake_disposer = set_heartbeat_wake_handler(_wake_handler)

            # Start all managers
            for hb_mgr in self._heartbeat_managers:
                asyncio.create_task(hb_mgr.start())

            # Provide an async-compatible stop callable for shutdown
            async def _stop_heartbeats_async() -> None:
                for mgr in self._heartbeat_managers:
                    await mgr.stop()
                if self._heartbeat_wake_disposer:
                    self._heartbeat_wake_disposer()

            # heartbeat_stop is called synchronously from shutdown(); wrap it
            def _stop_heartbeats() -> None:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.ensure_future(_stop_heartbeats_async())
                    else:
                        loop.run_until_complete(_stop_heartbeats_async())
                except Exception as _e:
                    logger.debug("Heartbeat stop error: %s", _e)

            self.heartbeat_stop = _stop_heartbeats

        except Exception as e:
            logger.warning(f"Heartbeat runner failed: {e}")
        results["steps_completed"] += 1
        
        # Step 18.5: Initialize QueueManager (mirrors TS command-queue + server-lanes)
        logger.info("Step 18.5: Initializing QueueManager")
        try:
            from openclaw.agents.queuing.queue import QueueManager
            from openclaw.agents.queuing.lanes import CommandLane
            self.queue_manager = QueueManager(max_concurrent_per_session=1, max_concurrent_global=10)
            cfg_dict = _config_as_dict(self.config)
            agents_defaults = (cfg_dict.get("agents") or {}).get("defaults") or {}
            main_concurrent = int(agents_defaults.get("maxConcurrent") or 4)
            subagent_cfg = agents_defaults.get("subagents") or {}
            subagent_concurrent = int(subagent_cfg.get("maxConcurrent") or 8)
            cron_concurrent = int((cfg_dict.get("cron") or {}).get("maxConcurrentRuns") or 1)
            self.queue_manager.set_lane_concurrency(CommandLane.MAIN, main_concurrent)
            self.queue_manager.set_lane_concurrency(CommandLane.SUBAGENT, subagent_concurrent)
            self.queue_manager.set_lane_concurrency(CommandLane.CRON, cron_concurrent)
            logger.info(
                "QueueManager initialized (main=%d, subagent=%d, cron=%d)",
                main_concurrent, subagent_concurrent, cron_concurrent,
            )
        except Exception as e:
            logger.warning(f"QueueManager init failed: {e}")
            self.queue_manager = None
        results["steps_completed"] += 0.5

        # Step 19: Set global handler instances
        logger.info("Step 19: Setting global handler instances")
        try:
            from .handlers import set_global_instances
            set_global_instances(
                self.session_manager,
                self.tool_registry,
                self.channel_manager,
                self.runtime,
                None,  # wizard_handler will be set after server creation
                queue_manager=self.queue_manager,
            )
        except Exception as e:
            logger.debug(f"Handler globals setup (optional): {e}")
        results["steps_completed"] += 1
        
        # Step 20: Start config reloader
        logger.info("Step 20: Starting config reloader")
        try:
            from ..config.reloader import ConfigReloader
            from ..config.loader import get_config_path
            
            config_file = get_config_path()
            self.config_reloader = ConfigReloader(
                config_path=config_file,
                reload_fn=load_config,
            )
            self.config_reloader.start()
        except Exception as e:
            logger.warning(f"Config reloader failed: {e}")
        results["steps_completed"] += 1
        
        # Step 21: Log startup
        logger.info("Step 21: Logging startup")
        self._log_startup()
        results["steps_completed"] += 1
        
        # Step 22: Start WebSocket server
        logger.info("Step 22: Starting WebSocket server")
        try:
            from .server import GatewayServer
            port = self.config.gateway.port if self.config and self.config.gateway else 18789
            
            # Pass the ToolRegistry object directly so GatewayServer can detect it
            # with isinstance() and skip creating a second registry (which would
            # trigger auto_register=True and raise "Tool already registered").
            tools = self.tool_registry if self.tool_registry else []

            self.server = GatewayServer(
                config=self.config,
                agent_runtime=self.runtime,
                session_manager=self.session_manager,
                tools=tools,
                system_prompt=None,  # Will be built from skills
                auto_discover_channels=False,  # We already created ChannelManager
            )
            
            # Pass bootstrap reference to server for accessing skill_loader
            self.server._bootstrap = self
            
            # Override the ChannelManager that GatewayServer created with our own
            # (since we already configured it in Step 13)
            self.server.channel_manager = self.channel_manager

            sidecar_services = results.get("sidecar_services") or {}
            canvas_host = sidecar_services.get("canvas_host") if isinstance(sidecar_services, dict) else None
            if isinstance(canvas_host, dict) and canvas_host.get("port"):
                self.server.canvas_host_port = int(canvas_host["port"])
            
            # Inject gateway reference into SessionsSpawnTool (required for subagent spawning)
            # SessionsSpawnTool needs the gateway object to make internal RPC calls, but it's
            # registered in Step 10 before GatewayServer exists. We inject it here post-creation.
            if self.tool_registry:
                sessions_spawn_tool = self.tool_registry.get("sessions_spawn")
                if sessions_spawn_tool:
                    sessions_spawn_tool.gateway = self.server
                    logger.debug("Injected gateway reference into SessionsSpawnTool")
            
            # Start server: launch as background task but wait briefly then
            # verify the port is actually bound before declaring success.
            # We must use create_task (not await) because server.start() runs
            # the aiohttp runner loop indefinitely. Instead we rely on the
            # server setting self.running = True once the site is up.
            import socket as _socket

            server_task = asyncio.create_task(self.server.start(start_channels=False))

            # Poll up to 3 s for the server to bind its port
            deadline = asyncio.get_running_loop().time() + 3.0
            bound = False
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.1)
                # If the task already finished it must have failed
                if server_task.done():
                    exc = server_task.exception() if not server_task.cancelled() else None
                    if exc:
                        raise exc
                    break
                # Try a quick connect to see if the port is listening
                try:
                    with _socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        bound = True
                        break
                except OSError:
                    continue

            if not bound and not server_task.done():
                # One final check — maybe loopback probe failed but server is up
                bound = getattr(self.server, "running", False)

            if not bound:
                server_task.cancel()
                raise OSError(f"Server failed to bind on port {port} within 3 s")

            logger.info(f"WebSocket server started on port {port}")
            results["gateway_port"] = port
            results["steps_completed"] += 1

            # Fire gateway_start plugin hook after server is bound (mirrors TS)
            asyncio.create_task(self._fire_gateway_start_hook())
        except Exception as e:
            logger.error(f"Server start failed: {e}")
            results["errors"].append(f"server_start: {e}")

        # Step 23: Initialize chat run state + dedupe tracker (m3)
        logger.info("Step 23: Initializing chat run state and deduplication tracker")
        try:
            from .chat_state import ChatRunRegistry
            self.chat_registry = ChatRunRegistry()
            if self.server:
                self.server.chat_registry = self.chat_registry
            logger.info("Chat run registry initialized")
        except Exception as e:
            logger.warning(f"Chat run state init failed: {e}")
        results["steps_completed"] += 1

        # Step 24: Initialize exec approval system (m5)
        logger.info("Step 24: Initializing exec approval system")
        try:
            from ..exec.approval_manager import ExecApprovalManager
            self.approval_manager = ExecApprovalManager()
            if self.server:
                self.server.approval_manager = self.approval_manager
            logger.info("Exec approval manager initialized")
        except Exception as e:
            logger.warning(f"Exec approval init failed: {e}")
        results["steps_completed"] += 1

        # Step 25: Run BOOT.md runner (m10)
        # NOTE: This early attempt may fail if runtime is not ready yet - that's OK,
        # the boot-md hook will retry on gateway:startup event (after channels are started)
        logger.info("Step 25: Running BOOT.md runner (early attempt)")
        try:
            await self._run_boot_once(workspace_dir)
        except Exception as e:
            logger.debug(f"BOOT.md early attempt failed (will retry on gateway:startup): {e}")
        results["steps_completed"] += 1

        # Step 26: Write workspace-state.json (onboarding tracking)
        logger.info("Step 26: Updating workspace-state.json")
        try:
            import time as _time
            from ..wizard.onboarding import write_workspace_state
            write_workspace_state(
                workspace_dir,
                bootstrap_seeded_at=str(int(_time.time() * 1000)),
            )
        except Exception as e:
            logger.debug(f"workspace-state.json update skipped: {e}")
        results["steps_completed"] += 1

        logger.info(
            f"Bootstrap complete: {results['steps_completed']} steps, "
            f"{len(results['errors'])} errors"
        )

        return results

    async def _run_boot_once(self, workspace_dir: Path) -> None:
        """Delegate to gateway.boot.run_boot_once() via agent command.

        Mirrors TypeScript runBootOnce() — runs BOOT.md through the agent
        runtime (not shell code blocks).  The runtime may not be ready yet
        at this point in the bootstrap sequence, so failures are logged as
        debug and the boot-md hook will retry on gateway:startup.
        """
        from openclaw.gateway.boot import run_boot_once

        cfg = self.config or {}
        result = await run_boot_once(cfg=cfg, workspace_dir=workspace_dir)
        if result.status == "ran":
            logger.info("BOOT.md executed via agent command")
        elif result.status == "skipped":
            logger.debug("BOOT.md skipped: %s", result.reason)
        else:
            logger.debug("BOOT.md run result: %s — %s", result.status, result.reason)
    
    def _set_env_vars(self) -> None:
        """Set required environment variables.

        TS-aligned priority chain (highest → lowest):
          1. Process environment (already set — never overridden)
          2. CWD .env                      (developer convenience, via dotenv.py)
          3. ~/.openclaw/.env              (global user .env, via dotenv.py)
          4. openclaw.json ``env`` block   (applyConfigEnvVars)
          5. auth-profiles.json API keys   (set as GOOGLE_API_KEY etc.)
          6. Fallback: ~/.pi/agent/auth.json (pi_coding_agent legacy)

        Also ensures OPENCLAW_AGENT_DIR / PI_CODING_AGENT_DIR are set so that
        pi_coding_agent finds auth-profiles.json in the correct location.

        TS references:
          src/infra/dotenv.ts         → load_dot_env()
          src/config/env-vars.ts      → apply_config_env_vars()
          src/agents/agent-paths.ts   → ensure_agent_env()
          src/agents/model-auth.ts    → resolve_api_key()
        """
        if self.config and self.config.gateway:
            os.environ["OPENCLAW_GATEWAY_PORT"] = str(self.config.gateway.port)

        # ── Step 1-3: Load .env files (CWD + ~/.openclaw/.env) ───────────────
        try:
            from openclaw.infra.dotenv import load_dot_env
            load_dot_env(quiet=True)
        except Exception as exc:
            logger.debug("dotenv load skipped: %s", exc)

        # ── Step 4: Apply openclaw.json ``env`` block ─────────────────────────
        # Mirrors TS applyConfigEnvVars() — only sets vars not already present
        try:
            from openclaw.config.schema import EnvConfig
            env_block = self.config.env if self.config else None
            if env_block is not None:
                if isinstance(env_block, EnvConfig):
                    env_vars = env_block.get_all_vars()
                elif isinstance(env_block, dict):
                    env_vars = {
                        k: v for k, v in env_block.items()
                        if isinstance(v, str) and k not in ("shellEnv", "vars")
                    }
                    env_vars.update((env_block.get("vars") or {}))
                else:
                    env_vars = {}
                for key, value in env_vars.items():
                    if not os.environ.get(key):
                        os.environ[key] = value
                        logger.debug("Set %s from config env block", key)
        except Exception as exc:
            logger.debug("Config env block apply failed: %s", exc)

        # ── Step 5: Ensure OPENCLAW_AGENT_DIR / PI_CODING_AGENT_DIR are set ──
        # Mirrors TS ensureOpenClawAgentEnv() — pi_coding_agent uses this to
        # locate auth-profiles.json.
        try:
            from openclaw.config.auth_profiles import ensure_agent_env
            agent_dir = ensure_agent_env()
            logger.debug("OPENCLAW_AGENT_DIR=%s", agent_dir)
        except Exception as exc:
            logger.debug("ensure_agent_env failed: %s", exc)

        # ── Step 6: Load API keys from auth-profiles.json → env vars ─────────
        # Mirrors TS: gateway startup reads auth-profiles.json and makes API
        # keys available to the runtime (which checks env vars first).
        _PROVIDER_ENV: dict[str, list[str]] = {
            "google":    ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
            "anthropic": ["ANTHROPIC_API_KEY"],
            "openai":    ["OPENAI_API_KEY"],
        }
        try:
            from openclaw.config.auth_profiles import get_api_key as _get_key
            for provider, env_names in _PROVIDER_ENV.items():
                if os.environ.get(env_names[0]):
                    continue  # already set from .env or shell
                key = _get_key(provider)
                if key:
                    for name in env_names:
                        os.environ[name] = key
                    logger.info("Loaded %s API key from auth-profiles.json → %s",
                                provider, env_names[0])
        except Exception as exc:
            logger.debug("auth-profiles.json key loading failed: %s", exc)

        # ── Step 7: Fallback — pi_coding_agent legacy ~/.pi/agent/auth.json ──
        _PROVIDER_ENV_LEGACY: dict[str, list[str]] = {
            "google":    ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
            "anthropic": ["ANTHROPIC_API_KEY"],
            "openai":    ["OPENAI_API_KEY"],
        }
        try:
            from pi_coding_agent.core.auth_storage import AuthStorage
            storage = AuthStorage()
            for provider, env_names in _PROVIDER_ENV_LEGACY.items():
                if os.environ.get(env_names[0]):
                    continue
                key = storage.get_api_key(provider)
                if key:
                    for name in env_names:
                        os.environ[name] = key
                    logger.debug("Loaded %s from legacy ~/.pi/agent/auth.json", provider)
        except Exception:
            pass
    
    def _start_maintenance_timers(self) -> None:
        """Start maintenance timer tasks"""
        
        async def session_cleanup():
            """Periodic session cleanup - runs every hour"""
            while True:
                try:
                    await asyncio.sleep(3600)  # Every hour
                    logger.debug("Running periodic session maintenance")
                    
                    # Import maintenance functions
                    from openclaw.agents.session_maintenance import apply_session_maintenance
                    from openclaw.config.paths import resolve_state_dir
                    
                    state_dir = resolve_state_dir()
                    
                    # Get all agents from config
                    agents_config = getattr(self.config, "agents", None)
                    if not agents_config:
                        continue
                    
                    agent_list = agents_config.list if hasattr(agents_config, 'list') else []
                    
                    for agent_entry in agent_list:
                        agent_id = agent_entry.id
                        # Convert to dict for easier access
                        agent_cfg = agent_entry.model_dump() if hasattr(agent_entry, 'model_dump') else agent_entry.__dict__
                        
                        try:
                            # Resolve paths
                            sessions_dir = state_dir / "sessions" / agent_id
                            store_path = sessions_dir / "sessions.json"
                            workspace_root = state_dir / "workspace"
                            
                            if not sessions_dir.exists():
                                continue
                            
                            # Get maintenance config
                            session_cfg = agent_cfg.get("session", {})
                            maintenance_config = session_cfg.get("maintenance")
                            
                            # Apply maintenance with workspace cleanup
                            results = apply_session_maintenance(
                                agent_id=agent_id,
                                store_path=store_path,
                                sessions_dir=sessions_dir,
                                config=maintenance_config,
                                active_session_key=None,
                                workspace_root=workspace_root,
                            )
                            
                            # Log results
                            if results.get("pruned", 0) > 0 or results.get("capped", 0) > 0:
                                logger.info(
                                    f"Session maintenance for {agent_id}: "
                                    f"pruned={results['pruned']}, capped={results['capped']}, "
                                    f"workspaces_cleaned={results.get('workspaces_cleaned', 0)}, "
                                    f"orphaned_cleaned={results.get('orphaned_workspaces_cleaned', 0)}, "
                                    f"rotated={results.get('rotated', False)}"
                                )
                        except Exception as e:
                            logger.warning(f"Session maintenance failed for {agent_id}: {e}")
                    
                except asyncio.CancelledError:
                    logger.debug("Session cleanup task cancelled")
                    break
                except Exception as e:
                    logger.error(f"Session cleanup error: {e}", exc_info=True)
        
        async def health_check():
            """Periodic health check"""
            while True:
                try:
                    await asyncio.sleep(60)  # Every minute
                    # Check component health
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Health check error: {e}")
        
        self._maintenance_tasks.append(asyncio.create_task(session_cleanup()))
        self._maintenance_tasks.append(asyncio.create_task(health_check()))
    
    async def _execute_heartbeat(self, agent_id: str, prompt: str) -> str | None:
        """Execute heartbeat for an agent"""
        if not self.runtime or not self.session_manager:
            return None
        
        session = self.session_manager.get_session(f"heartbeat-{agent_id}")
        tools = self.tool_registry.list_tools() if self.tool_registry else []
        
        response = ""
        async for event in self.runtime.run_turn(session, prompt, tools):
            if hasattr(event, 'text') and event.text:
                response += event.text
        
        return response if response else None
    
    async def _fire_gateway_start_hook(self) -> None:
        """Fire gateway_start plugin hook after full startup. Mirrors TS runGatewayStart."""
        if self._hook_runner:
            try:
                await self._hook_runner.run_gateway_start(
                    {"config": _config_as_dict(self.config)},
                    {"config": _config_as_dict(self.config), "workspace_dir": self.cwd if hasattr(self, "cwd") else None},
                )
            except Exception as exc:
                logger.warning(f"gateway_start hook failed: {exc}")

    async def _fire_gateway_stop_hook(self) -> None:
        """Fire gateway_stop plugin hook before shutdown. Mirrors TS runGatewayStop."""
        if self._hook_runner:
            try:
                await self._hook_runner.run_gateway_stop(
                    {"config": _config_as_dict(self.config)},
                    {"config": _config_as_dict(self.config), "workspace_dir": self.cwd if hasattr(self, "cwd") else None},
                )
            except Exception as exc:
                logger.warning(f"gateway_stop hook failed: {exc}")
    
    async def _check_sandbox_requirements(self) -> None:
        """Check if sandbox configuration is valid and Docker is available.
        
        If sandbox mode is enabled but Docker is unavailable, logs a warning.
        Does not prevent Gateway startup (allows users without Docker to run).
        """
        try:
            # Get sandbox config
            sandbox_cfg = None
            if self.config and self.config.agents and self.config.agents.defaults:
                sandbox_cfg = self.config.agents.defaults.sandbox
            
            if not sandbox_cfg:
                logger.debug("No sandbox configuration found")
                return
            
            sandbox_mode = sandbox_cfg.mode if hasattr(sandbox_cfg, "mode") else "off"
            
            if sandbox_mode == "off":
                logger.info("Sandbox mode: off (operations run directly in main process)")
                return
            
            # Sandbox is enabled, check Docker availability
            logger.info(f"Sandbox mode: {sandbox_mode} (requires Docker)")
            
            # Import is_docker_available from sandbox.docker module
            from ..agents.sandbox.docker import is_docker_available
            
            docker_available = await is_docker_available()
            
            if not docker_available:
                logger.warning(
                    "⚠️  Sandbox mode is '%s' but Docker command is NOT available",
                    sandbox_mode
                )
                logger.warning(
                    "   Docker is required for sandbox mode to function."
                )
                logger.warning(
                    "   Isolated sessions (cron jobs, sub-agents) will fail without Docker."
                )
                logger.warning(
                    "   Options:"
                )
                logger.warning(
                    "   1. Install Docker (recommended): https://docs.docker.com/get-docker/"
                )
                logger.warning(
                    "   2. Or disable sandbox in openclaw.json:"
                )
                logger.warning(
                    '      {"agents": {"defaults": {"sandbox": {"mode": "off"}}}}'
                )
                return
            
            logger.info("✅ Docker command is available")
            
            # Check if Docker daemon is running (optional check)
            try:
                import docker
                client = docker.from_env()
                client.ping()
                logger.info("✅ Docker daemon is running")
                
                # Log sandbox configuration details
                scope = sandbox_cfg.scope if hasattr(sandbox_cfg, "scope") else "session"
                access = sandbox_cfg.workspaceAccess if hasattr(sandbox_cfg, "workspaceAccess") else "rw"
                logger.info(f"   Sandbox scope: {scope}")
                logger.info(f"   Workspace access: {access}")
                
            except Exception as daemon_err:
                logger.warning(
                    "⚠️  Docker command available but daemon is not running: %s",
                    daemon_err
                )
                logger.warning(
                    "   Sandbox features will be unavailable until Docker daemon starts."
                )
                logger.warning(
                    "   Start Docker Desktop or run: sudo systemctl start docker"
                )
                
        except Exception as e:
            logger.debug(f"Sandbox requirements check failed: {e}")

    def _log_startup(self) -> None:
        """Log gateway startup information"""
        cfg = _config_as_dict(self.config)
        port = (cfg.get("gateway") or {}).get("port", 18789) if isinstance(cfg, dict) else (
            self.config.gateway.port if self.config and self.config.gateway else 18789
        )

        logger.info("=" * 60)
        logger.info(f"OpenClaw Gateway Started")
        logger.info(f"  Platform: {platform.system()} {platform.machine()}")
        logger.info(f"  Python: {platform.python_version()}")
        logger.info(f"  Port: {port}")
        try:
            # Show model info from runtime (includes fallbacks) if available, else from config
            model_str = None
            if self.runtime and hasattr(self.runtime, "model") and self.runtime.model:
                fallbacks = getattr(self.runtime, "fallback_models", []) or []
                if fallbacks:
                    model_str = f"primary='{self.runtime.model}' fallbacks={fallbacks!r}"
                else:
                    model_str = self.runtime.model
            elif self.config and not isinstance(self.config, dict):
                if self.config.agents and self.config.agents.defaults:
                    model_str = self.config.agents.defaults.model
            elif isinstance(self.config, dict):
                model_str = (self.config.get("agent") or {}).get("model") or (
                    (self.config.get("agents") or {}).get("defaults") or {}
                ).get("model")
            if model_str:
                logger.info(f"  Model: {model_str}")
        except Exception:
            pass
        if self.tool_registry:
            logger.info(f"  Tools: {len(self.tool_registry.list_tools())}")
        if self.skill_loader:
            logger.info(f"  Skills: {len(self.skill_loader.skills)}")
        logger.info("=" * 60)
    
    async def shutdown(self) -> None:
        """Graceful shutdown"""
        logger.info("Gateway shutting down...")

        # Fire gateway_stop plugin hook before stopping services (mirrors TS)
        await self._fire_gateway_stop_hook()
        
        # Stop Gateway server first (this stops WebSocket connections)
        if hasattr(self, 'server') and self.server:
            try:
                await self.server.stop()
            except Exception as e:
                logger.error(f"Gateway server stop error: {e}")
        
        # Stop heartbeat
        if self.heartbeat_stop:
            self.heartbeat_stop()
        
        # Stop maintenance timers
        for task in self._maintenance_tasks:
            try:
                task.cancel()
                # Give tasks a moment to cancel
                await asyncio.sleep(0.1)
            except Exception:
                pass
        
        # Stop config reloader
        if self.config_reloader:
            try:
                self.config_reloader.stop()
            except Exception:
                pass
        
        # Stop skills watchers
        try:
            from ..agents.skills.refresh import stop_all_watchers
            stop_all_watchers()
        except Exception:
            pass
        
        # Stop diagnostic heartbeat
        try:
            from ..infra.diagnostic_events import stop_diagnostic_heartbeat
            stop_diagnostic_heartbeat()
            from openclaw.diagnostics.stability import stop_diagnostic_stability_recorder
            stop_diagnostic_stability_recorder()
        except Exception:
            pass
        
        # Stop subagent auto-archive service
        try:
            from openclaw.agents.subagent_archive import stop_archive_service
            stop_archive_service()
            logger.info("Subagent auto-archive service stopped")
        except Exception as e:
            logger.debug(f"Failed to stop archive service: {e}")
        
        # Release PID lock last so the new process can acquire it immediately
        if self._gateway_lock is not None:
            try:
                self._gateway_lock.release()
                logger.debug("Gateway PID lock released")
            except Exception as _le:
                logger.debug("Gateway PID lock release error: %s", _le)

        logger.info("Gateway shutdown complete")
