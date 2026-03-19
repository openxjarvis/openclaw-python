"""Daemon (gateway service) management commands — mirrors TS src/cli/daemon-cli.ts"""
from __future__ import annotations

from openclaw.config.paths import resolve_state_dir

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

console = Console()
daemon_app = typer.Typer(help="Manage gateway as a system service", no_args_is_help=True)

_PLIST_LABEL = "com.openclaw.gateway"
_SYSTEMD_SERVICE = "openclaw-gateway"
_WINDOWS_TASK = "OpenClawGateway"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_platform() -> str:
    s = platform.system().lower()
    if s == "darwin":
        return "macos"
    elif s == "linux":
        return "linux"
    elif s == "windows":
        return "windows"
    return s


def _get_openclaw_bin() -> str:
    """Resolve the path to the openclaw executable."""
    from shutil import which
    p = which("openclaw")
    if p:
        return p
    # Fallback: python -m openclaw.cli
    return f"{sys.executable} -m openclaw.cli"


def _get_log_path() -> Path:
    """Get rolling log file path (matches TS daily logs)."""
    log_dir = resolve_state_dir() / "tmp"
    log_dir.mkdir(parents=True, exist_ok=True)
    # Use rolling log format: openclaw-YYYY-MM-DD.log
    from datetime import datetime
    date_str = datetime.now().strftime("%Y-%m-%d")
    return log_dir / f"openclaw-{date_str}.log"


def _get_plist_path() -> Path:
    """macOS launchd plist path."""
    return Path.home() / "Library" / "LaunchAgents" / f"{_PLIST_LABEL}.plist"


def _get_systemd_path() -> Path:
    """Linux systemd user service path."""
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    return systemd_dir / f"{_SYSTEMD_SERVICE}.service"


def _get_project_dir() -> str:
    """Return the openclaw-python project directory (where .env lives).
    Resolved from the installed package location so it works regardless of cwd.
    """
    try:
        import openclaw
        return str(Path(openclaw.__file__).parent.parent)
    except Exception:
        return str(Path.cwd())


def _build_launchd_plist(bin_path: str, port: int, log_path: Path) -> str:
    """Build the launchd plist XML, including WorkingDirectory and any API-key
    env vars found in the credentials store.  This mirrors the TS daemon which
    also injects environment at service install time.
    """
    project_dir = _get_project_dir()

    # Collect env vars that should be injected into the daemon environment.
    # TS-aligned: the plist EnvironmentVariables block mirrors what TS sets in
    # the launchd/systemd service via ensureOpenClawAgentEnv and dotenv loading.
    env_pairs: list[tuple[str, str]] = []

    # ── OPENCLAW_AGENT_DIR / PI_CODING_AGENT_DIR (critical for pi_coding_agent) ──
    # Mirrors TS ensureOpenClawAgentEnv() — lets pi_coding_agent find
    # ~/.openclaw/agents/main/agent/auth-profiles.json regardless of cwd.
    try:
        from openclaw.config.auth_profiles import resolve_agent_dir
        agent_dir = str(resolve_agent_dir())
    except Exception:
        import pathlib
        agent_dir = str(pathlib.resolve_state_dir() / "agents" / "main" / "agent")
    env_pairs.append(("OPENCLAW_AGENT_DIR", agent_dir))
    env_pairs.append(("PI_CODING_AGENT_DIR", agent_dir))

    # ── API keys (from auth-profiles.json → current process env → skip) ──────
    _key_vars = [
        ("google",    "GOOGLE_API_KEY"),
        ("google",    "GEMINI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("openai",    "OPENAI_API_KEY"),
    ]
    try:
        from openclaw.config.auth_profiles import get_api_key as _gk
        seen_providers: set[str] = set()
        for provider, env_name in _key_vars:
            if provider in seen_providers:
                continue
            key = _gk(provider) or os.environ.get(env_name, "")
            if key:
                env_pairs.append((env_name, key))
                seen_providers.add(provider)
    except Exception:
        for _, env_name in _key_vars:
            val = os.environ.get(env_name, "")
            if val:
                env_pairs.append((env_name, val))

    # Also inject PATH so the process can find system tools
    path_val = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin")
    env_pairs.append(("PATH", path_val))

    env_xml = ""
    if env_pairs:
        pairs_xml = "\n".join(
            f"        <key>{k}</key>\n        <string>{v}</string>"
            for k, v in env_pairs
        )
        env_xml = f"""    <key>EnvironmentVariables</key>
    <dict>
{pairs_xml}
    </dict>
"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{bin_path}</string>
        <string>gateway</string>
        <string>run</string>
        <string>--port</string>
        <string>{port}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{project_dir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
{env_xml}</dict>
</plist>"""


def _probe_gateway(url: Optional[str], token: Optional[str], timeout_ms: int) -> dict:
    try:
        from .gateway_rpc_cli import GatewayRpcOpts, call_gateway_from_cli
        opts = GatewayRpcOpts(url=url, token=token, timeout=timeout_ms, json_output=True)
        result = call_gateway_from_cli("health", opts, {})
        return result or {}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# daemon status
# ---------------------------------------------------------------------------

@daemon_app.command("status")
def daemon_status(
    probe: bool = typer.Option(True, "--probe/--no-probe", help="Probe gateway via RPC"),
    deep: bool = typer.Option(False, "--deep", help="Scan system-level services"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
    timeout: int = typer.Option(10_000, "--timeout", help="Timeout in ms"),
):
    """Show gateway service status"""
    plat = _get_platform()
    status: dict = {
        "platform": plat,
        "installed": False,
        "running": False,
        "probe": None,
    }

    if plat == "macos":
        plist = _get_plist_path()
        status["installed"] = plist.exists()
        if plist.exists():
            try:
                result = subprocess.run(
                    ["launchctl", "list", _PLIST_LABEL],
                    capture_output=True, text=True
                )
                status["running"] = result.returncode == 0
            except Exception:
                pass

    elif plat == "linux":
        svc = _get_systemd_path()
        status["installed"] = svc.exists()
        if svc.exists():
            try:
                result = subprocess.run(
                    ["systemctl", "--user", "is-active", _SYSTEMD_SERVICE],
                    capture_output=True, text=True
                )
                status["running"] = result.stdout.strip() == "active"
            except Exception:
                pass

    elif plat == "windows":
        try:
            result = subprocess.run(
                ["schtasks", "/Query", "/TN", _WINDOWS_TASK],
                capture_output=True, text=True
            )
            status["installed"] = result.returncode == 0
            if result.returncode == 0:
                status["running"] = "Running" in result.stdout
        except Exception:
            pass

    if probe:
        probe_result = _probe_gateway(url, token, timeout)
        status["probe"] = probe_result
        if probe_result and not probe_result.get("error"):
            status["reachable"] = True
        else:
            status["reachable"] = False

    if json_output:
        import json
        console.print(json.dumps(status, indent=2))
        return

    installed_icon = "[green]✓[/green]" if status["installed"] else "[red]✗[/red]"
    running_icon = "[green]●[/green]" if status["running"] else "[red]○[/red]"
    console.print(f"[bold]Gateway Service Status[/bold]\n")
    console.print(f"  Platform:  {plat}")
    console.print(f"  Installed: {installed_icon}")
    console.print(f"  Running:   {running_icon}")

    if probe:
        probe_result = status.get("probe") or {}
        if probe_result.get("error"):
            console.print(f"  Reachable: [red]✗[/red] ({probe_result['error']})")
        else:
            console.print(f"  Reachable: [green]✓[/green]")

    if not status["installed"]:
        console.print(f"\n  Install with: [cyan]openclaw daemon install[/cyan]")


# ---------------------------------------------------------------------------
# daemon install
# ---------------------------------------------------------------------------

@daemon_app.command("install")
def daemon_install(
    port: int = typer.Option(18789, "--port", help="Gateway port"),
    force: bool = typer.Option(False, "--force", help="Reinstall/overwrite"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Install gateway as a system service"""
    import json as _json

    plat = _get_platform()
    bin_path = _get_openclaw_bin()
    log_path = _get_log_path()

    if plat == "macos":
        plist_path = _get_plist_path()
        if plist_path.exists() and not force:
            console.print("[yellow]Service already installed.[/yellow] Use --force to reinstall.")
            return

        plist_content = _build_launchd_plist(bin_path, port, log_path)

        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content, encoding="utf-8")
        try:
            subprocess.run(["launchctl", "load", str(plist_path)], check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Failed to load service:[/red] {e.stderr.decode()}")
            raise typer.Exit(1)

        if json_output:
            console.print(_json.dumps({"installed": True, "path": str(plist_path)}))
        else:
            console.print(f"[green]✓[/green] Gateway service installed (launchd)")
            console.print(f"  Plist: {plist_path}")
            console.print(f"  Logs:  {log_path}")

    elif plat == "linux":
        svc_path = _get_systemd_path()
        if svc_path.exists() and not force:
            console.print("[yellow]Service already installed.[/yellow] Use --force to reinstall.")
            return

        unit_content = f"""[Unit]
Description=OpenClaw Gateway
After=network.target

[Service]
ExecStart={bin_path} gateway run --port {port}
Restart=always
StandardOutput=append:{log_path}
StandardError=append:{log_path}

[Install]
WantedBy=default.target
"""
        svc_path.write_text(unit_content, encoding="utf-8")
        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True)
            subprocess.run(["systemctl", "--user", "enable", _SYSTEMD_SERVICE], check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Failed to enable service:[/red] {e}")
            raise typer.Exit(1)

        if json_output:
            console.print(_json.dumps({"installed": True, "path": str(svc_path)}))
        else:
            console.print(f"[green]✓[/green] Gateway service installed (systemd)")
            console.print(f"  Unit:  {svc_path}")
            console.print(f"  Logs:  {log_path}")

    elif plat == "windows":
        try:
            cmd = (
                f'schtasks /Create /SC ONLOGON /TN "{_WINDOWS_TASK}" '
                f'/TR "{bin_path} gateway run --port {port}" /RL HIGHEST'
            )
            if force:
                cmd += " /F"
            subprocess.run(cmd, shell=True, check=True, capture_output=True)
            if json_output:
                console.print(_json.dumps({"installed": True}))
            else:
                console.print(f"[green]✓[/green] Gateway service installed (Task Scheduler)")
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Failed to create task:[/red] {e}")
            raise typer.Exit(1)

    else:
        console.print(f"[red]Unsupported platform:[/red] {plat}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# daemon uninstall
# ---------------------------------------------------------------------------

@daemon_app.command("uninstall")
def daemon_uninstall(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Uninstall the gateway service"""
    import json as _json
    plat = _get_platform()

    if plat == "macos":
        plist_path = _get_plist_path()
        if not plist_path.exists():
            console.print("[yellow]Service not installed[/yellow]")
            return
        try:
            subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
        except Exception:
            pass
        plist_path.unlink(missing_ok=True)
        if json_output:
            console.print(_json.dumps({"uninstalled": True}))
        else:
            console.print("[green]✓[/green] Gateway service uninstalled (launchd)")

    elif plat == "linux":
        svc_path = _get_systemd_path()
        try:
            subprocess.run(["systemctl", "--user", "disable", "--now", _SYSTEMD_SERVICE], capture_output=True)
        except Exception:
            pass
        svc_path.unlink(missing_ok=True)
        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        except Exception:
            pass
        if json_output:
            console.print(_json.dumps({"uninstalled": True}))
        else:
            console.print("[green]✓[/green] Gateway service uninstalled (systemd)")

    elif plat == "windows":
        try:
            subprocess.run(
                ["schtasks", "/Delete", "/TN", _WINDOWS_TASK, "/F"],
                capture_output=True
            )
            if json_output:
                console.print(_json.dumps({"uninstalled": True}))
            else:
                console.print("[green]✓[/green] Gateway task removed (Task Scheduler)")
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    else:
        console.print(f"[red]Unsupported platform:[/red] {plat}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# daemon start / stop / restart
# ---------------------------------------------------------------------------

@daemon_app.command("start")
def daemon_start(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Start the gateway service"""
    import json as _json
    plat = _get_platform()
    try:
        if plat == "macos":
            plist_path = _get_plist_path()
            if not plist_path.exists():
                console.print("[red]Service not installed.[/red] Run: openclaw daemon install")
                raise typer.Exit(1)
            subprocess.run(["launchctl", "load", str(plist_path)], check=True, capture_output=True)
        elif plat == "linux":
            subprocess.run(["systemctl", "--user", "start", _SYSTEMD_SERVICE], check=True, capture_output=True)
        elif plat == "windows":
            subprocess.run(["schtasks", "/Run", "/TN", _WINDOWS_TASK], check=True, capture_output=True)
        else:
            console.print(f"[red]Unsupported platform:[/red] {plat}")
            raise typer.Exit(1)
        if json_output:
            console.print(_json.dumps({"started": True}))
        else:
            console.print("[green]✓[/green] Gateway service started")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to start service:[/red] {e}")
        raise typer.Exit(1)


@daemon_app.command("stop")
def daemon_stop(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Stop the gateway service"""
    import json as _json
    plat = _get_platform()
    try:
        if plat == "macos":
            subprocess.run(["launchctl", "stop", _PLIST_LABEL], check=True, capture_output=True)
        elif plat == "linux":
            subprocess.run(["systemctl", "--user", "stop", _SYSTEMD_SERVICE], check=True, capture_output=True)
        elif plat == "windows":
            subprocess.run(["schtasks", "/End", "/TN", _WINDOWS_TASK], check=True, capture_output=True)
        else:
            console.print(f"[red]Unsupported platform:[/red] {plat}")
            raise typer.Exit(1)
        if json_output:
            console.print(_json.dumps({"stopped": True}))
        else:
            console.print("[green]✓[/green] Gateway service stopped")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to stop service:[/red] {e}")
        raise typer.Exit(1)


@daemon_app.command("restart")
def daemon_restart(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Restart the gateway service"""
    daemon_stop(json_output=False)
    daemon_start(json_output=False)
    if json_output:
        import json as _json
        console.print(_json.dumps({"restarted": True}))
    else:
        console.print("[green]✓[/green] Gateway service restarted")


# ---------------------------------------------------------------------------
# Programmatic helpers (used by onboarding and other non-CLI callers)
# ---------------------------------------------------------------------------

def is_service_installed() -> bool:
    """Return True if the gateway service plist/unit file exists on disk."""
    plat = _get_platform()
    if plat == "macos":
        return _get_plist_path().exists()
    elif plat == "linux":
        return _get_systemd_path().exists()
    return False


def is_service_running() -> bool:
    """Return True if the gateway service is currently running."""
    plat = _get_platform()
    try:
        if plat == "macos":
            result = subprocess.run(
                ["launchctl", "list"],
                capture_output=True, text=True, timeout=5
            )
            return _PLIST_LABEL in result.stdout
        elif plat == "linux":
            result = subprocess.run(
                ["systemctl", "--user", "is-active", _SYSTEMD_SERVICE],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip() == "active"
    except Exception:
        pass
    return False


def install_service_programmatic(port: int = 18789) -> bool:
    """Install the gateway service without typer. Returns True on success."""
    plat = _get_platform()
    bin_path = _get_openclaw_bin()
    log_path = _get_log_path()

    try:
        if plat == "macos":
            plist_path = _get_plist_path()
            plist_content = _build_launchd_plist(bin_path, port, log_path)
            plist_path.parent.mkdir(parents=True, exist_ok=True)
            plist_path.write_text(plist_content, encoding="utf-8")
            subprocess.run(["launchctl", "load", str(plist_path)], check=True, capture_output=True)
            return True

        elif plat == "linux":
            svc_path = _get_systemd_path()
            unit_content = f"""[Unit]
Description=OpenClaw Gateway
After=network.target

[Service]
ExecStart={bin_path} gateway run --port {port}
Restart=always
StandardOutput=append:{log_path}
StandardError=append:{log_path}

[Install]
WantedBy=default.target
"""
            svc_path.write_text(unit_content, encoding="utf-8")
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True)
            subprocess.run(["systemctl", "--user", "enable", "--now", _SYSTEMD_SERVICE], check=True, capture_output=True)
            return True

    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Service install failed: %s", e)
        return False

    return False


def start_service_programmatic() -> bool:
    """Start the gateway service without typer. Returns True on success."""
    plat = _get_platform()
    try:
        if plat == "macos":
            plist_path = _get_plist_path()
            if not plist_path.exists():
                return False
            subprocess.run(["launchctl", "load", str(plist_path)], check=True, capture_output=True)
            return True
        elif plat == "linux":
            subprocess.run(["systemctl", "--user", "start", _SYSTEMD_SERVICE], check=True, capture_output=True)
            return True
    except Exception:
        pass
    return False


def stop_service_programmatic() -> bool:
    """Stop the gateway service without typer. Returns True on success."""
    plat = _get_platform()
    try:
        if plat == "macos":
            subprocess.run(["launchctl", "stop", _PLIST_LABEL], capture_output=True)
            return True
        elif plat == "linux":
            subprocess.run(["systemctl", "--user", "stop", _SYSTEMD_SERVICE], capture_output=True)
            return True
    except Exception:
        pass
    return False
