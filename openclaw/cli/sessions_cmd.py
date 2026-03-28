"""Sessions management CLI commands."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from openclaw.agents.session_maintenance import apply_session_maintenance
from openclaw.config.loader import load_config
from openclaw.config.paths import resolve_state_dir

app = typer.Typer(
    name="sessions",
    help="Session management commands",
    no_args_is_help=True,
)

console = Console()
logger = logging.getLogger(__name__)


@app.command()
def cleanup(
    agent: Optional[str] = typer.Option(
        None,
        "--agent",
        "-a",
        help="Agent ID to clean up (default: all agents)",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be cleaned up without actually deleting",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output results as JSON",
    ),
):
    """
    Clean up old sessions based on maintenance configuration.
    
    This command will:
    - Prune sessions older than configured threshold (default: 30 days)
    - Cap session count to maximum (default: 500)
    - Rotate large session stores (default: >10MB)
    - Enforce disk budget if configured
    - Archive removed session transcripts
    """
    try:
        config = load_config()
        state_dir = resolve_state_dir()
        
        # Determine which agents to clean up
        agents_config = getattr(config, "agents", None)
        
        if not agents_config:
            console.print("[yellow]No agents configured[/yellow]")
            raise typer.Exit(0)
        
        # Get agent list
        agent_list = agents_config.list if hasattr(agents_config, 'list') else []
        
        if agent:
            # Validate agent exists
            agent_ids = [a.id for a in agent_list]
            if agent not in agent_ids:
                console.print(f"[red]Error:[/red] Agent '{agent}' not found in configuration")
                raise typer.Exit(1)
            agents_to_clean = [agent]
        else:
            agents_to_clean = [a.id for a in agent_list]
        
        if not agents_to_clean:
            console.print("[yellow]No agents found in configuration[/yellow]")
            raise typer.Exit(0)
        
        all_results = {}
        
        for agent_id in agents_to_clean:
            # Find agent config from list
            agent_cfg = None
            for a in agent_list:
                if a.id == agent_id:
                    # Convert to dict for easier access
                    agent_cfg = a.model_dump() if hasattr(a, 'model_dump') else a.__dict__
                    break
            
            if not agent_cfg:
                continue
            
            # Get sessions directory and store path
            sessions_dir = state_dir / "sessions" / agent_id
            store_path = sessions_dir / "sessions.json"
            
            # Get workspace root for workspace cleanup
            workspace_root = state_dir / "workspace"
            
            if not sessions_dir.exists():
                if not json_output:
                    console.print(f"[dim]Skipping {agent_id}: no sessions directory[/dim]")
                continue
            
            # Get maintenance config
            session_cfg = agent_cfg.get("session", {})
            maintenance_config = session_cfg.get("maintenance")
            
            # Override mode if dry-run
            if dry_run:
                if maintenance_config is None:
                    maintenance_config = {}
                maintenance_config["mode"] = "warn"
            
            # Apply maintenance
            results = apply_session_maintenance(
                agent_id=agent_id,
                store_path=store_path,
                sessions_dir=sessions_dir,
                config=maintenance_config,
                active_session_key=None,
                workspace_root=workspace_root,
            )
            
            all_results[agent_id] = results
        
        # Output results
        if json_output:
            console.print(json.dumps(all_results, indent=2))
        else:
            # Display results in table
            table = Table(title="Session Cleanup Results")
            table.add_column("Agent", style="cyan")
            table.add_column("Mode", style="yellow")
            table.add_column("Pruned", justify="right")
            table.add_column("Capped", justify="right")
            table.add_column("Archived", justify="right")
            table.add_column("Workspaces", justify="right")
            table.add_column("Rotated", justify="center")
            
            for agent_id, results in all_results.items():
                mode = results.get("mode", "off")
                pruned = str(results.get("pruned", 0))
                capped = str(results.get("capped", 0))
                archived = str(results.get("archived", 0))
                workspaces = str(results.get("workspaces_cleaned", 0) + results.get("orphaned_workspaces_cleaned", 0))
                rotated = "✓" if results.get("rotated") else ""
                
                table.add_row(agent_id, mode, pruned, capped, archived, workspaces, rotated)
            
            console.print()
            console.print(table)
            console.print()
            
            if dry_run:
                console.print("[yellow]Dry run:[/yellow] No changes were made")
            
            # Show disk cleanup info if available
            for agent_id, results in all_results.items():
                disk_cleanup = results.get("disk_cleanup")
                if disk_cleanup and disk_cleanup.get("deleted_files", 0) > 0:
                    console.print(
                        f"\n[cyan]{agent_id}:[/cyan] Freed {disk_cleanup['freed_bytes'] / 1024 / 1024:.1f}MB "
                        f"by deleting {disk_cleanup['deleted_files']} old transcript files"
                    )
    
    except Exception as e:
        if json_output:
            console.print(json.dumps({"error": str(e)}))
        else:
            console.print(f"[red]Error:[/red] {e}")
        logger.exception("Session cleanup failed")
        raise typer.Exit(1)


@app.command()
def list(
    agent: Optional[str] = typer.Option(
        None,
        "--agent",
        "-a",
        help="Agent ID to list sessions for (default: all agents)",
    ),
):
    """
    List all active sessions.
    """
    try:
        config = load_config()
        state_dir = resolve_state_dir()
        
        agents_config = getattr(config, "agents", None)
        
        if not agents_config:
            console.print("[yellow]No agents configured[/yellow]")
            raise typer.Exit(0)
        
        agent_list = agents_config.list if hasattr(agents_config, 'list') else []
        
        if agent:
            agent_ids = [a.id for a in agent_list]
            if agent not in agent_ids:
                console.print(f"[red]Error:[/red] Agent '{agent}' not found")
                raise typer.Exit(1)
            agents_to_list = [agent]
        else:
            agents_to_list = [a.id for a in agent_list]
        
        table = Table(title="Active Sessions")
        table.add_column("Agent", style="cyan")
        table.add_column("Session Count", justify="right")
        table.add_column("Store Size", justify="right")
        
        for agent_id in agents_to_list:
            sessions_dir = state_dir / "sessions" / agent_id
            store_path = sessions_dir / "sessions.json"
            
            if not store_path.exists():
                continue
            
            # Load store to count sessions
            try:
                with open(store_path) as f:
                    store_data = json.load(f)
                session_count = len(store_data)
                store_size_mb = store_path.stat().st_size / 1024 / 1024
                
                table.add_row(
                    agent_id,
                    str(session_count),
                    f"{store_size_mb:.2f} MB"
                )
            except Exception:
                continue
        
        console.print()
        console.print(table)
        console.print()
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
