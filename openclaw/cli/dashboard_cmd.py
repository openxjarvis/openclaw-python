"""Dashboard command — mirrors TS src/commands/dashboard.ts"""
from __future__ import annotations

import webbrowser
from typing import Optional
from urllib.parse import quote

import typer
from rich.console import Console

console = Console()
dashboard_app = typer.Typer(help="Open the Control UI with your current token")


@dashboard_app.callback(invoke_without_command=True)
def dashboard_main(
    ctx: typer.Context,
    no_open: bool = typer.Option(False, "--no-open", help="Don't open browser, only show URL"),
):
    """
    Open the Control UI with your current token
    
    Examples:
      openclaw dashboard
      openclaw dashboard --no-open
    """
    if ctx.invoked_subcommand is not None:
        return
    
    from ..config.loader import load_config
    
    try:
        config = load_config()
        
        # Resolve gateway settings
        port = config.gateway.port if config.gateway else 18789
        token = ""
        if config.gateway and config.gateway.auth:
            token = config.gateway.auth.token or ""
        
        # Build dashboard URL
        base_url = f"http://localhost:{port}"
        
        # Prefer URL fragment to avoid leaking auth tokens via query params
        if token:
            dashboard_url = f"{base_url}#token={quote(token)}"
        else:
            dashboard_url = base_url
        
        console.print(f"\n[cyan]Dashboard URL:[/cyan] {dashboard_url}")
        
        # Try to copy to clipboard
        try:
            import pyperclip
            pyperclip.copy(dashboard_url)
            console.print("[green]✓[/green] Copied to clipboard")
        except Exception:
            console.print("[dim]Copy to clipboard unavailable[/dim]")
        
        # Open in browser
        if not no_open:
            try:
                opened = webbrowser.open(dashboard_url)
                if opened:
                    console.print("[green]✓[/green] Opened in your browser. Keep that tab to control OpenClaw.")
                else:
                    console.print("[yellow]Could not open browser. Use the URL above.[/yellow]")
            except Exception as e:
                console.print(f"[yellow]Could not open browser: {e}[/yellow]")
                console.print("[dim]Use the URL above.[/dim]")
        else:
            console.print("[dim]Browser launch disabled (--no-open). Use the URL above.[/dim]")
        
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
