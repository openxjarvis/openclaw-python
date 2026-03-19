"""Gateway logs commands"""
import typer
from pathlib import Path
from typing import Optional
from rich.console import Console
from datetime import datetime
import time

from openclaw.config.paths import resolve_state_dir

console = Console()

logs_app = typer.Typer(help="Gateway file logs", no_args_is_help=False)

# Use rolling log path (matches TS)
def _get_default_log() -> Path:
    """Get today's rolling log file path."""
    log_dir = resolve_state_dir() / "tmp"
    date_str = datetime.now().strftime("%Y-%m-%d")
    return log_dir / f"openclaw-{date_str}.log"


def _do_tail(
    log_file: Path,
    limit: int,
    follow: bool,
    interval_ms: int,
    json_output: bool,
) -> None:
    if not log_file.exists():
        console.print(f"[yellow]Log file not found:[/yellow] {log_file}")
        return

    with open(log_file, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()[-limit:]

    if json_output:
        for line in lines:
            console.print(line.rstrip())
    else:
        console.print(f"[dim]Tailing {log_file}[/dim]\n")
        for line in lines:
            console.print(line.rstrip())

    if follow:
        if not json_output:
            console.print(f"\n[dim]Following (Ctrl+C to stop)...[/dim]\n")
        try:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if line:
                        console.print(line.rstrip())
                    else:
                        time.sleep(interval_ms / 1000.0)
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopped[/yellow]")


@logs_app.callback(invoke_without_command=True)
def logs(
    ctx: typer.Context,
    limit: int = typer.Option(200, "--limit", help="Max lines to return"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output (like tail -f)"),
    interval: int = typer.Option(1000, "--interval", help="Polling interval in ms (with --follow)"),
    json_output: bool = typer.Option(False, "--json", help="Emit raw log lines without formatting"),
    log_file: Optional[str] = typer.Option(None, "--file", help="Log file path (default: ~/.openclaw/tmp/openclaw-YYYY-MM-DD.log)"),
):
    """Show gateway file logs. Use --follow / -f to tail live output."""
    if ctx.invoked_subcommand is not None:
        return
    
    path = Path(log_file) if log_file else _get_default_log()
    try:
        _do_tail(path, limit=limit, follow=follow, interval_ms=interval, json_output=json_output)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@logs_app.command("tail")
def logs_tail(
    limit: int = typer.Option(200, "--limit", "-n", help="Max lines to return"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output (like tail -f)"),
    interval: int = typer.Option(1000, "--interval", help="Polling interval in ms (with --follow)"),
    json_output: bool = typer.Option(False, "--json", help="Emit raw log lines without formatting"),
    log_file: Optional[str] = typer.Option(None, "--file", help="Log file path (default: ~/.openclaw/tmp/openclaw-YYYY-MM-DD.log)"),
):
    """Tail gateway logs (alias for 'openclaw logs'). Use -f to follow live."""
    path = Path(log_file) if log_file else _get_default_log()
    try:
        _do_tail(path, limit=limit, follow=follow, interval_ms=interval, json_output=json_output)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@logs_app.command("show")
def logs_show(
    limit: int = typer.Option(200, "--limit", "-n", help="Max lines to return"),
    json_output: bool = typer.Option(False, "--json", help="Emit raw log lines without formatting"),
    log_file: Optional[str] = typer.Option(None, "--file", help="Log file path"),
):
    """Show recent gateway log lines (no follow)."""
    path = Path(log_file) if log_file else _get_default_log()
    try:
        _do_tail(path, limit=limit, follow=False, interval_ms=1000, json_output=json_output)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
