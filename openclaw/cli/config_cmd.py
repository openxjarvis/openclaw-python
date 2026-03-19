"""Configuration management commands"""

import json
from pathlib import Path
from typing import Any

import pyjson5
import typer
from rich.console import Console
from openclaw.config.paths import resolve_state_dir

console = Console()
config_app = typer.Typer(help="Configuration management (get/set/unset)")


def get_config_path() -> Path:
    """Get config file path"""
    return resolve_state_dir() / "openclaw.json"


def load_config_raw() -> dict[str, Any]:
    """Load config as raw dict (no validation)"""
    config_path = get_config_path()
    if not config_path.exists():
        return {}
    
    with open(config_path, encoding="utf-8") as f:
        return pyjson5.load(f)


def save_config_raw(config: dict[str, Any]) -> None:
    """Save config dict to file"""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def parse_path(path_str: str) -> list[str]:
    """Parse dot-path or bracket notation into segments"""
    segments = []
    current = ""
    i = 0
    
    while i < len(path_str):
        ch = path_str[i]
        
        if ch == "\\":
            if i + 1 < len(path_str):
                current += path_str[i + 1]
                i += 2
                continue
        
        if ch == ".":
            if current:
                segments.append(current)
            current = ""
            i += 1
            continue
        
        if ch == "[":
            if current:
                segments.append(current)
            current = ""
            close = path_str.find("]", i)
            if close == -1:
                raise ValueError(f"Invalid path (missing ']'): {path_str}")
            inside = path_str[i + 1:close].strip()
            if not inside:
                raise ValueError(f"Invalid path (empty '[]'): {path_str}")
            segments.append(inside)
            i = close + 1
            continue
        
        current += ch
        i += 1
    
    if current:
        segments.append(current)
    
    return [s.strip() for s in segments if s.strip()]


def get_at_path(obj: Any, path: list[str]) -> tuple[bool, Any]:
    """Get value at path, return (found, value)"""
    current = obj
    
    for segment in path:
        if not isinstance(current, dict):
            return (False, None)
        
        if segment not in current:
            return (False, None)
        
        current = current[segment]
    
    return (True, current)


def set_at_path(obj: dict[str, Any], path: list[str], value: Any) -> None:
    """Set value at path, creating intermediate dicts as needed"""
    current = obj
    
    for i, segment in enumerate(path[:-1]):
        if segment not in current:
            current[segment] = {}
        elif not isinstance(current[segment], dict):
            current[segment] = {}
        current = current[segment]
    
    current[path[-1]] = value


def unset_at_path(obj: dict[str, Any], path: list[str]) -> bool:
    """Remove value at path, return True if found and removed"""
    if not path:
        return False
    
    current = obj
    
    for segment in path[:-1]:
        if not isinstance(current, dict) or segment not in current:
            return False
        current = current[segment]
    
    if not isinstance(current, dict) or path[-1] not in current:
        return False
    
    del current[path[-1]]
    return True


def parse_value(value_str: str) -> Any:
    """Parse value string as JSON5, fall back to raw string"""
    try:
        return pyjson5.loads(value_str)
    except Exception:
        return value_str


@config_app.command("get")
def get_config(path: str = typer.Argument(..., help="Config path (dot or bracket notation)")):
    """Get a config value by path"""
    try:
        segments = parse_path(path)
        if not segments:
            console.print("[red]Error:[/red] Path is empty")
            raise typer.Exit(1)
        
        config = load_config_raw()
        found, value = get_at_path(config, segments)
        
        if not found:
            console.print(f"[red]Config path not found:[/red] {path}")
            raise typer.Exit(1)
        
        if isinstance(value, (dict, list)):
            console.print(json.dumps(value, indent=2))
        else:
            console.print(str(value))
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@config_app.command("set")
def set_config(
    path: str = typer.Argument(..., help="Config path (dot or bracket notation)"),
    value: str = typer.Argument(..., help="Value (JSON5 or raw string)"),
):
    """Set a config value by path"""
    try:
        segments = parse_path(path)
        if not segments:
            console.print("[red]Error:[/red] Path is empty")
            raise typer.Exit(1)
        
        parsed_value = parse_value(value)
        config = load_config_raw()
        set_at_path(config, segments, parsed_value)
        save_config_raw(config)
        
        console.print(f"[green]✓[/green] Updated {path}")
        console.print("[dim]Restart the gateway to apply changes[/dim]")
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@config_app.command("unset")
def unset_config(path: str = typer.Argument(..., help="Config path to remove")):
    """Remove a config value by path"""
    try:
        segments = parse_path(path)
        if not segments:
            console.print("[red]Error:[/red] Path is empty")
            raise typer.Exit(1)
        
        config = load_config_raw()
        removed = unset_at_path(config, segments)
        
        if not removed:
            console.print(f"[red]Config path not found:[/red] {path}")
            raise typer.Exit(1)
        
        save_config_raw(config)
        console.print(f"[green]✓[/green] Removed {path}")
        console.print("[dim]Restart the gateway to apply changes[/dim]")
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@config_app.command("show")
def show_config():
    """Show current configuration"""
    try:
        config = load_config_raw()
        
        if not config:
            console.print("[yellow]No configuration file found[/yellow]")
            console.print(f"Config path: {get_config_path()}")
            return
        
        console.print("[cyan]Current Configuration:[/cyan]\n")
        console.print(json.dumps(config, indent=2))
        console.print(f"\n[dim]Config file: {get_config_path()}[/dim]")
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@config_app.command("path")
def show_path():
    """Show config file path"""
    console.print(str(get_config_path()))
