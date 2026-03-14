"""OpenClaw CLI - Unified command-line interface"""

import os
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..config.loader import load_config

app = typer.Typer(
    name="openclaw",
    help="🦞 OpenClaw - Personal AI Assistant Platform",
    add_completion=False,
    no_args_is_help=True,
)

console = Console()


@app.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context):
    """
    Common initialization for all commands.
    Load environment variables from .env files (matches TypeScript behavior).
    """
    from ..infra.dotenv import load_dot_env
    load_dot_env(quiet=True)


@app.command()
def start(
    port: int = typer.Option(18789, "--port", "-p", help="WebSocket port for gateway"),
    telegram: bool = typer.Option(True, "--telegram/--no-telegram", help="Enable Telegram channel"),
    log_level: str = typer.Option("INFO", "--log-level", "-l", help="Log level"),
    force: bool = typer.Option(False, "--force", "-f", help="Kill any existing gateway process and restart"),
):
    """
    Start OpenClaw server with all features (Gateway + Channels)
    """
    console.print(Panel.fit(
        "🦞 [bold cyan]OpenClaw Python[/bold cyan]\n"
        "Starting full-featured server...",
        border_style="cyan"
    ))
    
    # Check configuration (matches TS gateway-cli/run.ts behavior)
    # Verify config exists and gateway.mode is local
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    if not config_path.exists():
        config_path = Path.home() / ".openclaw" / "config.json"
    
    if not config_path.exists():
        console.print(
            "\n[red]Error:[/red] Configuration not found.\n\n"
            "Please run onboarding first:\n"
            "  [cyan]$ uv run openclaw onboard[/cyan]\n"
            "or use setup command:\n"
            "  [cyan]$ uv run openclaw setup --wizard[/cyan]"
        )
        raise typer.Exit(1)
    
    # Verify gateway.mode is local (matches TS requirement)
    try:
        import json
        with open(config_path) as f:
            config_data = json.load(f)
        
        gateway_mode = config_data.get("gateway", {}).get("mode")
        if gateway_mode != "local":
            console.print(
                f"\n[red]Error:[/red] Gateway start blocked.\n\n"
                f"Current gateway.mode: [yellow]{gateway_mode or 'unset'}[/yellow]\n"
                f"Required: [green]local[/green]\n\n"
                "Update your config file or run onboarding again:\n"
                "  [cyan]$ uv run openclaw onboard[/cyan]"
            )
            raise typer.Exit(1)
    except json.JSONDecodeError as e:
        console.print(f"\n[red]Error:[/red] Invalid configuration file: {e}")
        raise typer.Exit(1)
    
    # Start server
    try:
        # Setup logging with daily log files (matching TypeScript)
        log_dir = Path.home() / ".openclaw" / "tmp"
        log_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_file = str(log_dir / f"openclaw-{date_str}.log")
        
        from ..monitoring import setup_logging
        setup_logging(
            level=log_level.upper(),
            format_type="colored",
            log_file=log_file,
        )
        
        console.print("[green]✓[/green] Starting Gateway + Telegram...")
        
        # Use GatewayBootstrap to initialize and start all components
        import asyncio
        from ..gateway.bootstrap import GatewayBootstrap
        
        async def start_gateway():
            bootstrap = GatewayBootstrap()
            results = await bootstrap.bootstrap(force=force)
            
            # Keep running
            console.print(f"\n[green]✓[/green] Gateway running on ws://127.0.0.1:{results.get('gateway_port', 18789)}")
            console.print("[dim]Press Ctrl+C to stop[/dim]\n")
            
            # Setup signal handlers for graceful shutdown
            import signal
            shutdown_event = asyncio.Event()
            
            def signal_handler(sig, frame):
                shutdown_event.set()
            
            # Register signal handlers (only on Unix-like systems)
            try:
                signal.signal(signal.SIGINT, signal_handler)
                signal.signal(signal.SIGTERM, signal_handler)
            except AttributeError:
                # Windows doesn't have SIGTERM
                signal.signal(signal.SIGINT, signal_handler)
            
            # Wait for shutdown signal
            try:
                await shutdown_event.wait()
            except asyncio.CancelledError:
                pass
            finally:
                console.print("\n[yellow]Shutting down...[/yellow]")
                await bootstrap.shutdown()
        
        asyncio.run(start_gateway())
            
    except KeyboardInterrupt:
        console.print("\n\n[yellow]Shutting down...[/yellow]")
    except Exception as e:
        from openclaw.gateway.error_codes import GatewayLockError
        if isinstance(e, GatewayLockError):
            # Try to read PID from the lock file for a helpful message
            try:
                from openclaw.infra.gateway_lock import resolve_gateway_lock_path
                import json as _json
                _lock_path = resolve_gateway_lock_path(str(config_path))
                _lock_data = _json.loads(_lock_path.read_text()) if _lock_path.exists() else {}
                _pid = _lock_data.get("pid", "")
                pid_hint = f" (PID: {_pid})" if _pid else ""
            except Exception:
                pid_hint = ""
            console.print(
                f"\n[yellow]⚠ Gateway already running{pid_hint}[/yellow]\n"
                f"  To force-restart: [cyan]uv run openclaw start --force[/cyan]\n"
                f"  Or kill manually: [cyan]kill {pid_hint.strip(' ()').replace('PID: ', '') or '<pid>'}[/cyan]"
            )
        else:
            console.print(f"\n[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def version():
    """Show OpenClaw version"""
    from openclaw import __version__
    console.print(f"[cyan]OpenClaw Python[/cyan] v{__version__}")


@app.command()
def doctor(
    repair: bool = typer.Option(
        False,
        "--repair",
        "--fix",
        help="Apply recommended fixes automatically"
    ),
    deep: bool = typer.Option(
        False,
        "--deep",
        help="Deep system scan (checks Gateway, channels, etc.)"
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output results as JSON"
    ),
    generate_gateway_token: bool = typer.Option(
        False,
        "--generate-gateway-token",
        help="Generate and save a new gateway authentication token"
    ),
):
    """Run diagnostics and check configuration"""
    console.print("[cyan]Running diagnostics...[/cyan]\n")

    issues = []
    warnings = []
    fixes = []
    check_results = []

    # Check 1: Python version
    py_version = sys.version_info
    if py_version < (3, 11):
        issues.append(
            f"Python {py_version.major}.{py_version.minor} is not supported. "
            "Please upgrade to Python 3.11+"
        )
        check_results.append({
            "check": "python_version",
            "status": "error",
            "message": f"Python {py_version.major}.{py_version.minor} < 3.11",
        })
    else:
        console.print(
            f"[green]✓[/green] Python {py_version.major}.{py_version.minor}.{py_version.micro}"
        )
        check_results.append({
            "check": "python_version",
            "status": "ok",
            "message": f"{py_version.major}.{py_version.minor}.{py_version.micro}",
        })

    # Check 2: Config file
    from ..config.loader import get_config_path, load_config

    config_path = get_config_path()
    if not config_path.exists():
        warnings.append(f"Config file not found at {config_path}")
        fixes.append(("create_config", "openclaw onboard"))
        console.print(f"[yellow]⚠[/yellow]  Config file not found")
        console.print(f"    Run: openclaw onboard")
        check_results.append({
            "check": "config_file",
            "status": "warning",
            "message": "not found",
            "fix": "openclaw onboard",
        })
    else:
        console.print(f"[green]✓[/green] Config file: {config_path}")

        # Try loading config
        try:
            config = load_config()
            console.print("[green]✓[/green] Config file is valid")
            check_results.append({
                "check": "config_file",
                "status": "ok",
                "message": "valid",
            })
        except Exception as e:
            issues.append(f"Config file is invalid: {e}")
            fixes.append(("fix_config", "openclaw configure"))
            console.print(f"[red]✗[/red] Config file is invalid: {e}")
            check_results.append({
                "check": "config_file",
                "status": "error",
                "message": str(e),
                "fix": "openclaw configure",
            })

    # Check 3: Workspace
    workspace = Path.home() / ".openclaw" / "workspace"
    if not workspace.exists():
        warnings.append(f"Workspace not found at {workspace}")
        fixes.append(("create_workspace", f"mkdir -p {workspace}"))
        console.print(f"[yellow]⚠[/yellow]  Workspace not found")
        
        if repair:
            workspace.mkdir(parents=True, exist_ok=True)
            console.print(f"[green]✓[/green] Created workspace: {workspace}")
        else:
            console.print(f"    Run with --repair to create")
        
        check_results.append({
            "check": "workspace",
            "status": "warning",
            "message": "not found",
            "fix": f"mkdir -p {workspace}",
        })
    else:
        console.print(f"[green]✓[/green] Workspace: {workspace}")
        check_results.append({
            "check": "workspace",
            "status": "ok",
            "message": str(workspace),
        })

    # Check 4: Dependencies
    try:
        import anthropic
        console.print("[green]✓[/green] anthropic package installed")
        check_results.append({"check": "anthropic", "status": "ok"})
    except ImportError:
        warnings.append("anthropic package not installed")
        fixes.append(("install_anthropic", "uv add anthropic"))
        console.print("[yellow]⚠[/yellow]  anthropic package not installed")
        check_results.append({
            "check": "anthropic",
            "status": "warning",
            "fix": "uv add anthropic",
        })

    # Deep checks (if requested)
    if deep:
        console.print("\n[cyan]Running deep checks...[/cyan]")

        # Check 5: Gateway status
        try:
            from .daemon_cmd import is_service_installed, is_service_running
            status = {
                "installed": is_service_installed(),
                "running": is_service_running() if is_service_installed() else False,
            }
            
            if status["installed"]:
                if status["running"]:
                    console.print("[green]✓[/green] Gateway service is running")
                    check_results.append({
                        "check": "gateway_service",
                        "status": "ok",
                        "message": "running",
                    })
                else:
                    warnings.append("Gateway service installed but not running")
                    fixes.append(("start_gateway", "openclaw gateway start"))
                    console.print("[yellow]⚠[/yellow]  Gateway service not running")
                    console.print("    Run: openclaw gateway start")
                    check_results.append({
                        "check": "gateway_service",
                        "status": "warning",
                        "message": "not running",
                        "fix": "openclaw gateway start",
                    })
            else:
                console.print("[yellow]⚠[/yellow]  Gateway service not installed")
                console.print("    Run: openclaw gateway install")
                check_results.append({
                    "check": "gateway_service",
                    "status": "info",
                    "message": "not installed",
                    "fix": "openclaw gateway install",
                })
        except Exception as e:
            console.print(f"[yellow]⚠[/yellow]  Could not check Gateway status: {e}")

        # Check 6: Channel credentials
        if config_path.exists():
            try:
                config = load_config()
                
                for channel_name in ["telegram", "discord", "slack"]:
                    channel_cfg = getattr(config.channels, channel_name, None)
                    if channel_cfg and channel_cfg.get("enabled"):
                        bot_token = channel_cfg.get("bot_token")
                        if bot_token:
                            console.print(f"[green]✓[/green] {channel_name.title()} credentials configured")
                            check_results.append({
                                "check": f"{channel_name}_credentials",
                                "status": "ok",
                            })
                        else:
                            warnings.append(f"{channel_name} enabled but no credentials")
                            console.print(f"[yellow]⚠[/yellow]  {channel_name.title()} enabled but no credentials")
                            check_results.append({
                                "check": f"{channel_name}_credentials",
                                "status": "warning",
                                "message": "no credentials",
                            })
            except Exception:
                pass

    # Check: generate gateway token if requested
    if generate_gateway_token:
        import secrets
        from ..config.loader import get_config_path, load_config, save_config
        token = secrets.token_hex(32)
        try:
            cfg = load_config()
            if not hasattr(cfg, "gateway") or cfg.gateway is None:
                cfg.gateway = {}
            if isinstance(cfg.gateway, dict):
                cfg.gateway["token"] = token
            else:
                cfg.gateway.token = token
            save_config(cfg)
            console.print(f"[green]✓[/green] Gateway token generated and saved to config")
            console.print(f"    Token: {token[:8]}...{token[-4:]}")
        except Exception as e:
            console.print(f"[yellow]⚠[/yellow]  Could not save gateway token: {e}")
            console.print(f"    Token: {token}")
        check_results.append({"check": "gateway_token", "status": "ok", "message": "generated"})

    # Check: legacy config key migrations (channel.*.token → channels.*)
    if config_path.exists():
        try:
            import json as _json
            raw = config_path.read_text()
            raw_cfg = _json.loads(raw)
            legacy_keys = [k for k in raw_cfg if k.startswith("channel.")]
            if legacy_keys:
                warnings.append(f"Legacy config keys found: {legacy_keys}. Migrate to 'channels.*'")
                console.print(f"[yellow]⚠[/yellow]  Legacy config keys: {legacy_keys}")
                if repair:
                    for k in legacy_keys:
                        parts = k.split(".")
                        if len(parts) >= 2:
                            raw_cfg.setdefault("channels", {}).setdefault(parts[1], {})[parts[-1]] = raw_cfg.pop(k)
                    config_path.write_text(_json.dumps(raw_cfg, indent=2))
                    console.print("[green]✓[/green] Migrated legacy config keys")
                check_results.append({"check": "legacy_config_keys", "status": "warning", "message": str(legacy_keys)})
            else:
                check_results.append({"check": "legacy_config_keys", "status": "ok"})
        except Exception:
            pass

    # Check: state directory permissions
    state_dir = Path.home() / ".openclaw"
    if state_dir.exists():
        import stat
        mode = state_dir.stat().st_mode
        if mode & stat.S_IWOTH:
            warnings.append(f"State directory {state_dir} is world-writable (security risk)")
            console.print(f"[yellow]⚠[/yellow]  State directory is world-writable")
            if repair:
                state_dir.chmod(mode & ~stat.S_IWOTH)
                console.print(f"[green]✓[/green] Fixed state directory permissions")
            check_results.append({"check": "state_dir_permissions", "status": "warning"})
        else:
            check_results.append({"check": "state_dir_permissions", "status": "ok"})

    # Check: gateway port binding probe (deep)
    if deep:
        import socket
        gateway_port = 4747
        try:
            from ..config.loader import load_config as _lc
            _cfg = _lc()
            gateway_port = getattr(getattr(_cfg, "gateway", None), "port", None) or 4747
        except Exception:
            pass
        try:
            with socket.create_connection(("127.0.0.1", gateway_port), timeout=1):
                console.print(f"[green]✓[/green] Gateway reachable on port {gateway_port}")
                check_results.append({"check": "gateway_port", "status": "ok", "port": gateway_port})
        except (ConnectionRefusedError, OSError):
            console.print(f"[yellow]⚠[/yellow]  Gateway not reachable on port {gateway_port}")
            check_results.append({"check": "gateway_port", "status": "warning", "port": gateway_port})

        # Check: skills directory
        skills_dir = Path.home() / ".openclaw" / "skills"
        if skills_dir.exists():
            skill_files = list(skills_dir.glob("*.md")) + list(skills_dir.glob("*.yaml")) + list(skills_dir.glob("*.yml"))
            console.print(f"[green]✓[/green] Skills: {len(skill_files)} file(s) in {skills_dir}")
            check_results.append({"check": "skills", "status": "ok", "count": len(skill_files)})
        else:
            console.print(f"[yellow]⚠[/yellow]  Skills directory not found: {skills_dir}")
            check_results.append({"check": "skills", "status": "info", "message": "no skills directory"})

    # Apply automatic fixes if requested
    if repair and fixes and not json_output:
        console.print("\n[cyan]Applying fixes...[/cyan]")
        for fix_id, fix_cmd in fixes:
            if fix_id == "create_workspace":
                # Already handled above
                continue
            
            console.print(f"  To fix '{fix_id}', run: {fix_cmd}")

    # Summary
    console.print()
    if json_output:
        import json
        result = {
            "issues": len(issues),
            "warnings": len(warnings),
            "checks": check_results,
        }
        print(json.dumps(result, indent=2))
    else:
        if issues:
            console.print(f"[red]✗[/red] {len(issues)} issue(s) found:")
            for issue in issues:
                console.print(f"  - {issue}")
        if warnings:
            console.print(f"[yellow]⚠[/yellow]  {len(warnings)} warning(s):")
            for warning in warnings:
                console.print(f"  - {warning}")
        if not issues and not warnings:
            console.print("[green]✓[/green] All checks passed!")
        
        if fixes and not repair:
            console.print("\n[cyan]Suggested fixes:[/cyan]")
            console.print("  Run with --repair to apply fixes automatically")
            console.print("  Or run these commands manually:")
            for fix_id, fix_cmd in fixes:
                console.print(f"    {fix_cmd}")

    if issues:
        raise typer.Exit(1)


# Register subcommand modules
from .gateway_cmd import gateway_app
from .channels_cmd import channels_app
from .agent_cmd import agent_app, agents_app
from .reset_cmd import reset_app
from .uninstall_cmd import uninstall_app
from .dashboard_cmd import dashboard_app
from .config_cmd import config_app
from .status_cmd import status_app
from .memory_cmd import memory_app
from .models_cmd import models_app
from .skills_cmd import skills_app
from .tools_cmd import tools_app
from .logs_cmd import logs_app
from .message_cmd import message_app
from .browser_cmd import browser_app
from .system_cmd import system_app
from .cron_cmd import cron_app
from .hooks_cmd import hooks_app
from .plugins_cmd import plugins_app
from .security_cmd import security_app
from .sandbox_cmd import sandbox_app
from .nodes_cmd import nodes_app
from .pairing_cmd import pairing_app
from .daemon_cmd import daemon_app
from .dns_cmd import dns_app
from .devices_cmd import devices_app
from .update_cmd import update_app
from .misc_cmd import register_misc_commands

app.add_typer(gateway_app, name="gateway")
app.add_typer(channels_app, name="channels")
app.add_typer(pairing_app, name="pairing")
app.add_typer(agent_app, name="agent")
app.add_typer(agents_app, name="agents")  # Separate top-level command (not nested under agent)
app.add_typer(reset_app, name="reset")
app.add_typer(uninstall_app, name="uninstall")
app.add_typer(dashboard_app, name="dashboard")
app.add_typer(config_app, name="config")
app.add_typer(status_app, name="status")
app.add_typer(memory_app, name="memory")
app.add_typer(models_app, name="models")
app.add_typer(skills_app, name="skills")
app.add_typer(tools_app, name="tools")
app.add_typer(logs_app, name="logs")
app.add_typer(message_app, name="message")
app.add_typer(browser_app, name="browser")
app.add_typer(system_app, name="system")
app.add_typer(cron_app, name="cron")
app.add_typer(hooks_app, name="hooks")
app.add_typer(plugins_app, name="plugins")
app.add_typer(security_app, name="security")
app.add_typer(sandbox_app, name="sandbox")
app.add_typer(nodes_app, name="nodes")
app.add_typer(daemon_app, name="daemon")
app.add_typer(dns_app, name="dns")
app.add_typer(devices_app, name="devices")
app.add_typer(update_app, name="update")

# Note: pairing_app already added above with channels
# Register misc commands (tui, update, onboard, setup, configure, etc)
register_misc_commands(app)


def main():
    """CLI entry point"""
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[red]Error:[/red] {e}")
        sys.exit(1)


@app.command()
def cleanup(
    ports: str = typer.Option("18789,8080", help="Comma-separated list of ports to free"),
    kill_all: bool = typer.Option(False, "--kill-all", "-k", help="Kill all openclaw processes"),
):
    """
    Clean up ports and processes.
    
    Useful when gateway fails to stop properly and ports remain occupied.
    """
    import subprocess
    
    console.print("[cyan]🧹 Cleaning up OpenClaw processes and ports...[/cyan]\n")
    
    # Parse ports
    port_list = [p.strip() for p in ports.split(",") if p.strip()]
    
    # Kill processes on specified ports
    if port_list:
        ports_str = ",".join(port_list)
        console.print(f"   Freeing ports: [yellow]{ports_str}[/yellow]")
        
        try:
            # Use lsof to find and kill processes
            cmd = f"lsof -ti:{ports_str} | xargs kill -9 2>/dev/null || true"
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                console.print(f"   [green]✓[/green] Freed ports: {ports_str}")
            else:
                console.print(f"   [dim]ℹ No processes found on ports: {ports_str}[/dim]")
        except Exception as e:
            console.print(f"   [red]⚠ Failed to free ports: {e}[/red]", style="red")
    
    # Kill all openclaw processes
    if kill_all:
        console.print("   Killing all openclaw processes...")
        try:
            cmd = "ps aux | grep -E '(openclaw|uv run openclaw)' | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null || true"
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                console.print("   [green]✓[/green] Killed all openclaw processes")
            else:
                console.print("   [dim]ℹ No openclaw processes found[/dim]")
        except Exception as e:
            console.print(f"   [red]⚠ Failed to kill processes: {e}[/red]")
    
    console.print("\n[green]✓ Cleanup complete[/green]")


if __name__ == "__main__":
    main()
