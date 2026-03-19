"""Memory management commands"""

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from ..config.paths import resolve_state_dir

console = Console()
memory_app = typer.Typer(help="Memory search and indexing")


@memory_app.command("status")
def status(
    agent: str = typer.Option(None, "--agent", help="Agent id"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    deep: bool = typer.Option(False, "--deep", help="Probe embedding provider"),
    index: bool = typer.Option(False, "--index", help="Reindex if dirty (implies --deep)"),
    verbose: bool = typer.Option(False, "--verbose", help="Verbose logging"),
):
    """Show memory search index status"""
    try:
        from ..memory.manager import SimpleMemorySearchManager
        
        workspace_dir = resolve_state_dir() / "workspace"
        manager = SimpleMemorySearchManager(workspace_dir)
        
        mem_status = manager.status()
        
        if json_output:
            result = {
                "backend": mem_status.backend,
                "provider": mem_status.provider,
                "files": mem_status.files,
                "workspace_dir": mem_status.workspace_dir,
            }
            console.print(json.dumps(result, indent=2))
            return
        
        table = Table(title="Memory Status")
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        
        table.add_row("Backend", mem_status.backend)
        table.add_row("Provider", mem_status.provider)
        table.add_row("Files", str(mem_status.files))
        table.add_row("Workspace", mem_status.workspace_dir)
        
        console.print(table)
        
        if index:
            console.print("\n[cyan]Reindexing...[/cyan]")
            asyncio.run(manager.sync())
            console.print("[green]✓[/green] Index complete")
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@memory_app.command("index")
def index_memory(
    agent: str = typer.Option(None, "--agent", help="Agent id"),
    force: bool = typer.Option(False, "--force", help="Force full reindex"),
    verbose: bool = typer.Option(False, "--verbose", help="Verbose logging"),
):
    """Reindex memory files"""
    try:
        from ..memory.manager import SimpleMemorySearchManager
        
        workspace_dir = resolve_state_dir() / "workspace"
        manager = SimpleMemorySearchManager(workspace_dir)
        
        console.print("[cyan]Indexing memory files...[/cyan]")
        asyncio.run(manager.sync())
        
        mem_status = manager.status()
        console.print(f"[green]✓[/green] Indexed {mem_status.files} files")
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@memory_app.command("search")
def search(
    query: str = typer.Argument(..., help="Search query"),
    agent: str = typer.Option(None, "--agent", help="Agent id"),
    max_results: int = typer.Option(10, "--max-results", help="Max results"),
    min_score: float = typer.Option(0.0, "--min-score", help="Minimum score"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Search memory files"""
    try:
        from ..memory.manager import SimpleMemorySearchManager
        
        workspace_dir = resolve_state_dir() / "workspace"
        manager = SimpleMemorySearchManager(workspace_dir)
        
        results = asyncio.run(manager.search(query, {
            "maxResults": max_results,
            "minScore": min_score,
        }))
        
        if json_output:
            results_data = [
                {
                    "path": r.path,
                    "start_line": r.start_line,
                    "end_line": r.end_line,
                    "score": r.score,
                    "snippet": r.snippet,
                }
                for r in results
            ]
            console.print(json.dumps({"results": results_data}, indent=2))
            return
        
        if not results:
            console.print("[yellow]No matches found[/yellow]")
            return
        
        console.print(f"\n[cyan]Found {len(results)} results:[/cyan]\n")
        
        for result in results:
            console.print(f"[green]{result.score:.3f}[/green] {result.path}:{result.start_line}-{result.end_line}")
            console.print(f"[dim]{result.snippet}[/dim]")
            console.print()
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
