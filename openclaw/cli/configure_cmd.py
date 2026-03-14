"""
Configure command - Simplified configuration wizard

Provides basic reconfiguration capabilities similar to TypeScript configure wizard.
This is a simplified version focusing on workspace and sessions setup.
"""
import typer
from pathlib import Path
from rich.console import Console

console = Console()
app = typer.Typer(help="Reconfigure OpenClaw installation")


@app.command("workspace")
def configure_workspace(
    workspace_dir: str = typer.Option(
        None,
        "--dir",
        "-d",
        help="Workspace directory to configure"
    ),
    agent_id: str = typer.Option(
        "main",
        "--agent",
        "-a",
        help="Agent ID for sessions directory"
    ),
    skip_bootstrap: bool = typer.Option(
        False,
        "--skip-bootstrap",
        help="Skip bootstrap file creation"
    ),
):
    """
    Reconfigure workspace and sessions directories
    
    Ensures workspace and sessions directories are properly set up.
    Similar to TypeScript configure wizard workspace section.
    """
    from openclaw.agents.ensure_workspace_and_sessions import ensure_workspace_and_sessions
    from openclaw.infra.path_utils import shorten_home_path
    
    try:
        # Resolve workspace directory
        if workspace_dir:
            ws_dir = Path(workspace_dir).expanduser().resolve()
        else:
            ws_dir = Path.home() / ".openclaw" / "workspace"
        
        console.print(f"\n[bold cyan]Configuring OpenClaw Workspace[/bold cyan]")
        console.print(f"Workspace: {shorten_home_path(ws_dir)}")
        console.print(f"Agent ID: {agent_id}")
        console.print(f"Skip Bootstrap: {skip_bootstrap}\n")
        
        # Run unified workspace and sessions setup
        result = ensure_workspace_and_sessions(
            workspace_dir=ws_dir,
            agent_id=agent_id,
            skip_bootstrap=skip_bootstrap,
        )
        
        console.print("[green]✓[/green] Workspace configured successfully")
        console.print(f"  Workspace: {shorten_home_path(result['workspace']['dir'])}")
        console.print(f"  Sessions: {shorten_home_path(result['sessions_dir'])}")
        
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command("verify")
def verify_setup(
    agent_id: str = typer.Option(
        "main",
        "--agent",
        "-a",
        help="Agent ID to verify"
    ),
):
    """
    Verify OpenClaw setup
    
    Checks that workspace and sessions directories exist and are properly configured.
    """
    from openclaw.config.sessions.paths import resolve_agent_sessions_dir
    from openclaw.infra.path_utils import shorten_home_path
    
    try:
        console.print(f"\n[bold cyan]Verifying OpenClaw Setup[/bold cyan]")
        console.print(f"Agent ID: {agent_id}\n")
        
        # Check workspace
        workspace_dir = Path.home() / ".openclaw" / "workspace"
        if workspace_dir.exists():
            console.print(f"[green]✓[/green] Workspace exists: {shorten_home_path(workspace_dir)}")
            
            # Check bootstrap files
            bootstrap_files = [
                "SOUL.md", "IDENTITY.md", "AGENTS.md", 
                "TOOLS.md", "USER.md", "HEARTBEAT.md"
            ]
            missing = []
            for filename in bootstrap_files:
                if not (workspace_dir / filename).exists():
                    missing.append(filename)
            
            if missing:
                console.print(f"  [yellow]Missing files:[/yellow] {', '.join(missing)}")
            else:
                console.print("  [green]✓[/green] All bootstrap files present")
        else:
            console.print(f"[red]✗[/red] Workspace not found: {shorten_home_path(workspace_dir)}")
        
        # Check sessions directory
        sessions_dir = resolve_agent_sessions_dir(agent_id)
        if sessions_dir.exists():
            console.print(f"[green]✓[/green] Sessions directory exists: {shorten_home_path(sessions_dir)}")
        else:
            console.print(f"[red]✗[/red] Sessions directory not found: {shorten_home_path(sessions_dir)}")
        
        console.print("\n[bold]Setup verification complete[/bold]")
        
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
