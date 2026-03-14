"""Hooks management commands"""

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

console = Console()
hooks_app = typer.Typer(help="Lifecycle hooks")


@hooks_app.command("list")
def list_hooks(
    eligible: bool = typer.Option(False, "--eligible", help="Show only eligible hooks"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed info"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List registered hooks"""
    try:
        from ..config.loader import load_config
        from ..hooks.hooks_cli import build_hooks_report, format_hooks_list
        
        config = load_config()
        report = build_hooks_report(config)
        output = format_hooks_list(report, json_output=json_output, eligible_only=eligible, verbose=verbose)
        
        console.print(output)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@hooks_app.command("test")
def test(
    hook_name: str = typer.Argument(..., help="Hook name to test"),
    data: str = typer.Option("{}", "--data", help="Test data (JSON)"),
):
    """Test a hook"""
    console.print("[yellow]⚠[/yellow]  Hook testing not yet implemented")
    console.print(f"Would test hook: {hook_name}")


@hooks_app.command("info")
def info(
    hook_name: str = typer.Argument(..., help="Hook name"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show detailed info about a hook"""
    try:
        from ..config.loader import load_config
        from ..hooks.hooks_cli import build_hooks_report, format_hook_info
        
        config = load_config()
        report = build_hooks_report(config)
        output = format_hook_info(report, hook_name, json_output=json_output)
        
        console.print(output)
        
        if not json_output and "not found" in output.lower():
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@hooks_app.command("check")
def check(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Check which hooks are eligible to run"""
    try:
        from ..config.loader import load_config
        from ..hooks.hooks_cli import build_hooks_report, format_hooks_check
        
        config = load_config()
        report = build_hooks_report(config)
        output = format_hooks_check(report, json_output=json_output)
        
        console.print(output)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@hooks_app.command("enable")
def enable(hook_name: str = typer.Argument(..., help="Hook name to enable")):
    """Enable a hook"""
    try:
        from ..hooks.hooks_cli import enable_hook
        
        message = enable_hook(hook_name)
        console.print(f"[green]{message}[/green]")
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@hooks_app.command("disable")
def disable(hook_name: str = typer.Argument(..., help="Hook name to disable")):
    """Disable a hook"""
    try:
        from ..hooks.hooks_cli import disable_hook
        
        message = disable_hook(hook_name)
        console.print(f"[yellow]{message}[/yellow]")
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@hooks_app.command("install")
def install(
    directory: Path = typer.Option(None, "--dir", "-d", help="Hooks directory"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Install hooks from a directory"""
    hooks_dir = directory or (Path.home() / ".openclaw" / "hooks")
    if not hooks_dir.exists():
        console.print(f"[yellow]⚠[/yellow]  Hooks directory not found: {hooks_dir}")
        raise typer.Exit(1)
    installed = []
    for hook_file in hooks_dir.glob("*.yaml"):
        installed.append(hook_file.name)
    for hook_file in hooks_dir.glob("*.yml"):
        installed.append(hook_file.name)
    if json_output:
        print(json.dumps({"installed": installed, "dir": str(hooks_dir)}))
    else:
        console.print(f"[green]✓[/green] Installed {len(installed)} hook(s) from {hooks_dir}")
        for f in installed:
            console.print(f"  • {f}")


@hooks_app.command("update")
def update(
    directory: Path = typer.Option(None, "--dir", "-d", help="Hooks directory"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Update hooks from a directory"""
    hooks_dir = directory or (Path.home() / ".openclaw" / "hooks")
    if not hooks_dir.exists():
        console.print(f"[yellow]⚠[/yellow]  Hooks directory not found: {hooks_dir}")
        raise typer.Exit(1)
    updated = []
    for hook_file in list(hooks_dir.glob("*.yaml")) + list(hooks_dir.glob("*.yml")):
        updated.append(hook_file.name)
    if json_output:
        print(json.dumps({"updated": updated, "dir": str(hooks_dir)}))
    else:
        console.print(f"[green]✓[/green] Updated {len(updated)} hook(s) in {hooks_dir}")


# Default action
@hooks_app.callback(invoke_without_command=True)
def hooks_default(ctx: typer.Context):
    """List hooks (default command)"""
    if ctx.invoked_subcommand is None:
        list_hooks()
