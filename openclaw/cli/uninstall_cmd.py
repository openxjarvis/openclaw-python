"""Uninstall command — mirrors TS src/commands/uninstall.ts"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

console = Console()
uninstall_app = typer.Typer(help="Uninstall the gateway service + local data (CLI remains)")


@uninstall_app.callback(invoke_without_command=True)
def uninstall_main(
    ctx: typer.Context,
    service: bool = typer.Option(False, "--service", help="Uninstall gateway service"),
    state: bool = typer.Option(False, "--state", help="Remove state + config (~/.openclaw)"),
    workspace: bool = typer.Option(False, "--workspace", help="Remove workspace (agent files)"),
    app: bool = typer.Option(False, "--app", help="Remove macOS app"),
    all_components: bool = typer.Option(False, "--all", help="Uninstall all components"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Non-interactive mode (requires --yes)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be removed without removing"),
):
    """
    Uninstall the gateway service + local data (CLI remains)
    
    Components:
      --service    Gateway service (launchd / systemd)
      --state      State + config (~/.openclaw)
      --workspace  Workspace (agent files)
      --app        macOS app (/Applications/OpenClaw.app)
      --all        All components
    
    Examples:
      openclaw uninstall --all --yes
      openclaw uninstall --service --state
      openclaw uninstall --dry-run --all
    """
    if ctx.invoked_subcommand is not None:
        return
    
    # Validation
    if non_interactive and not yes:
        console.print("[red]Error:[/red] Non-interactive mode requires --yes")
        raise typer.Exit(1)
    
    # Build scope selection
    scopes = set()
    had_explicit = any([service, state, workspace, app, all_components])
    
    if all_components or service:
        scopes.add("service")
    if all_components or state:
        scopes.add("state")
    if all_components or workspace:
        scopes.add("workspace")
    if all_components or app:
        scopes.add("app")
    
    # Interactive selection if no explicit scopes
    if not had_explicit:
        if non_interactive:
            console.print("[red]Error:[/red] Non-interactive mode requires explicit scopes (use --all)")
            raise typer.Exit(1)
        
        console.print("\n[cyan]Select components to uninstall:[/cyan]")
        console.print("  1. Gateway service (launchd / systemd)")
        console.print("  2. State + config (~/.openclaw)")
        console.print("  3. Workspace (agent files)")
        console.print("  4. macOS app (/Applications/OpenClaw.app)")
        console.print("  5. All of the above")
        
        choice = typer.prompt("\nEnter choice (1-5)", type=int, default=5)
        
        if choice == 1:
            scopes.add("service")
        elif choice == 2:
            scopes.add("state")
        elif choice == 3:
            scopes.add("workspace")
        elif choice == 4:
            scopes.add("app")
        elif choice == 5:
            scopes.update(["service", "state", "workspace", "app"])
        else:
            console.print("[red]Invalid choice[/red]")
            raise typer.Exit(1)
    
    if not scopes:
        console.print("Nothing selected.")
        return
    
    # Confirmation
    if not non_interactive and not yes:
        components = ", ".join(sorted(scopes))
        confirm = typer.confirm(f"\nUninstall: {components}?", default=False)
        if not confirm:
            console.print("Uninstall cancelled.")
            return
    
    # Resolve paths
    from ..config.paths import resolve_state_dir
    
    state_dir = resolve_state_dir()
    workspace_dir = resolve_state_dir() / "workspace"
    mac_app = Path("/Applications/OpenClaw.app")
    
    console.print("\n[cyan]Uninstalling components...[/cyan]")
    if dry_run:
        console.print("[yellow]DRY RUN - no files will be removed[/yellow]\n")
    
    # Uninstall service
    if "service" in scopes:
        if dry_run:
            console.print("[dim][dry-run] would stop and uninstall gateway service[/dim]")
        else:
            try:
                import subprocess
                # Try to stop gateway
                subprocess.run(
                    ["pkill", "-f", "openclaw.*gateway"],
                    capture_output=True,
                    text=True
                )
                
                # Try to uninstall service (macOS launchd)
                import platform
                if platform.system() == "Darwin":
                    plist_path = Path.home() / "Library/LaunchAgents/ai.openclaw.gateway.plist"
                    if plist_path.exists():
                        subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
                        plist_path.unlink()
                        console.print("[green]✓[/green] Uninstalled gateway service")
                    else:
                        console.print("[dim]  Gateway service not found[/dim]")
                else:
                    console.print("[yellow]⚠[/yellow]  Service uninstall not supported on this platform")
            except Exception as e:
                console.print(f"[red]⚠[/red] Failed to uninstall service: {e}")
    
    # Remove state
    if "state" in scopes:
        _remove_path(state_dir, dry_run)
    
    # Remove workspace
    if "workspace" in scopes:
        _remove_path(workspace_dir, dry_run)
    
    # Remove macOS app
    if "app" in scopes:
        if mac_app.exists():
            _remove_path(mac_app, dry_run)
        else:
            console.print(f"[dim]  Skip (not found): {mac_app}[/dim]")
    
    console.print("\n[green]✓[/green] Uninstall complete")
    console.print("[dim]CLI still installed. Remove via pip/uv if desired.[/dim]")
    
    # Tip about preserved workspaces
    if "state" in scopes and "workspace" not in scopes and workspace_dir.exists():
        console.print("[dim]Tip: workspaces were preserved. Re-run with --workspace to remove them.[/dim]")


def _remove_path(path: Path, dry_run: bool = False):
    """Remove a file or directory"""
    if not path.exists():
        console.print(f"[dim]  Skip (not found): {path}[/dim]")
        return
    
    if dry_run:
        if path.is_dir():
            console.print(f"[yellow]  [dry-run] would remove directory: {path}[/yellow]")
        else:
            console.print(f"[yellow]  [dry-run] would remove file: {path}[/yellow]")
        return
    
    try:
        if path.is_dir():
            shutil.rmtree(path)
            console.print(f"[green]✓[/green] Removed directory: {path}")
        else:
            path.unlink()
            console.print(f"[green]✓[/green] Removed file: {path}")
    except Exception as e:
        console.print(f"[red]⚠[/red] Failed to remove {path}: {e}")
