"""Reset command — mirrors TS src/commands/reset.ts"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from ..config.paths import resolve_state_dir

console = Console()
reset_app = typer.Typer(help="Reset local config/state (keeps the CLI installed)")


@reset_app.callback(invoke_without_command=True)
def reset_main(
    ctx: typer.Context,
    scope: Optional[str] = typer.Option(
        None,
        "--scope",
        help='Reset scope: "config", "config+creds+sessions", or "full"'
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Non-interactive mode (requires --yes)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be removed without removing"),
):
    """
    Reset local config/state (keeps the CLI installed)
    
    Scopes:
      config                  - Remove openclaw.json only
      config+creds+sessions   - Remove config + credentials + sessions (keeps workspace + auth profiles)
      full                    - Remove everything (state dir + workspace)
    
    Examples:
      openclaw reset --scope config
      openclaw reset --scope full --yes
      openclaw reset --dry-run
    """
    if ctx.invoked_subcommand is not None:
        return
    
    # Validation
    if non_interactive and not yes:
        console.print("[red]Error:[/red] Non-interactive mode requires --yes")
        raise typer.Exit(1)
    
    # Resolve scope
    reset_scope = scope
    if not reset_scope:
        if non_interactive:
            console.print("[red]Error:[/red] Non-interactive mode requires --scope")
            raise typer.Exit(1)
        
        # Interactive selection
        console.print("\n[cyan]Select reset scope:[/cyan]")
        console.print("  1. config                  - Remove openclaw.json only")
        console.print("  2. config+creds+sessions   - Remove config + credentials + sessions")
        console.print("  3. full                    - Full reset (state dir + workspace)")
        
        choice = typer.prompt("\nEnter choice (1-3)", type=int, default=2)
        
        if choice == 1:
            reset_scope = "config"
        elif choice == 2:
            reset_scope = "config+creds+sessions"
        elif choice == 3:
            reset_scope = "full"
        else:
            console.print("[red]Invalid choice[/red]")
            raise typer.Exit(1)
    
    if reset_scope not in ["config", "config+creds+sessions", "full"]:
        console.print('[red]Error:[/red] Invalid --scope. Expected "config", "config+creds+sessions", or "full".')
        raise typer.Exit(1)
    
    # Confirmation
    if not non_interactive and not yes:
        confirm = typer.confirm(f"\nProceed with {reset_scope} reset?", default=False)
        if not confirm:
            console.print("Reset cancelled.")
            return
    
    # Resolve paths
    from ..config.paths import resolve_config_path, resolve_state_dir
    
    state_dir = resolve_state_dir()
    config_path = resolve_config_path() or resolve_state_dir() / "openclaw.json"
    oauth_dir = state_dir / "credentials"
    sessions_dir = state_dir / "agents"
    workspace_dir = resolve_state_dir() / "workspace"
    
    console.print(f"\n[cyan]Reset scope:[/cyan] {reset_scope}")
    if dry_run:
        console.print("[yellow]DRY RUN - no files will be removed[/yellow]\n")
    
    # Stop gateway if running (scope != config)
    if reset_scope != "config":
        if dry_run:
            console.print("[dim][dry-run] would stop gateway service[/dim]")
        else:
            try:
                # Try to stop gateway
                import subprocess
                result = subprocess.run(
                    ["pkill", "-f", "openclaw.*gateway"],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    console.print("[green]✓[/green] Stopped gateway")
            except Exception:
                pass  # Gateway not running or can't stop
    
    # Remove based on scope
    if reset_scope == "config":
        _remove_path(config_path, dry_run)
        console.print("\n[green]✓[/green] Config reset complete")
        console.print("[dim]Next: openclaw onboard[/dim]")
        return
    
    if reset_scope == "config+creds+sessions":
        _remove_path(config_path, dry_run)
        _remove_path(oauth_dir, dry_run)
        
        # Remove agent session directories
        if sessions_dir.exists():
            for agent_dir in sessions_dir.iterdir():
                if agent_dir.is_dir():
                    sessions = agent_dir / "sessions"
                    if sessions.exists():
                        _remove_path(sessions, dry_run)
        
        console.print("\n[green]✓[/green] Config + credentials + sessions reset complete")
        console.print("[dim]Next: openclaw onboard[/dim]")
        return
    
    if reset_scope == "full":
        # Remove state directory
        _remove_path(state_dir, dry_run)
        
        # Remove workspace
        _remove_path(workspace_dir, dry_run)
        
        console.print("\n[green]✓[/green] Full reset complete")
        console.print("[dim]Next: openclaw onboard[/dim]")
        return


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
