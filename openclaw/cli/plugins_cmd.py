"""Plugin management commands — mirrors TS src/cli/plugins-cli.ts"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

console = Console()
plugins_app = typer.Typer(help="Plugin management", no_args_is_help=True)

_EXTENSIONS_DIRNAME = "extensions"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_extensions_dir() -> Path:
    from ..config.paths import resolve_state_dir
    try:
        state_dir = resolve_state_dir()
        return Path(state_dir) / _EXTENSIONS_DIRNAME
    except Exception:
        # Fallback: still use resolve_state_dir() with default behavior
        from ..config.paths import resolve_new_state_dir
        return Path(resolve_new_state_dir()) / _EXTENSIONS_DIRNAME


def _load_raw_config() -> dict:
    from pathlib import Path as _P
    from ..config.loader import load_config_raw
    from ..config.paths import resolve_config_path, resolve_state_dir
    try:
        cfg_path = resolve_config_path()
        if cfg_path and _P(cfg_path).exists():
            return load_config_raw(_P(cfg_path)) or {}
    except Exception:
        pass
    try:
        default = _P(resolve_state_dir()) / "openclaw.json"
        if default.exists():
            return load_config_raw(default) or {}
    except Exception:
        pass
    return {}


def _save_raw(raw: dict):
    from ..config.loader import write_config_file
    write_config_file(raw)


def _scan_plugins(extensions_dir: Path) -> list[dict]:
    """Scan the extensions directory for installed plugins."""
    plugins = []
    if not extensions_dir.exists():
        return plugins

    for entry in sorted(extensions_dir.iterdir()):
        if not entry.is_dir():
            continue

        manifest_path = entry / "plugin.json"
        alt_manifest = entry / "package.json"
        manifest: dict = {}

        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        elif alt_manifest.exists():
            try:
                pkg = json.loads(alt_manifest.read_text(encoding="utf-8"))
                manifest = {
                    "id": pkg.get("name", entry.name),
                    "name": pkg.get("displayName") or pkg.get("name", entry.name),
                    "version": pkg.get("version", ""),
                    "description": pkg.get("description", ""),
                }
            except Exception:
                pass

        plugin_id = manifest.get("id") or entry.name
        plugin: dict = {
            "id": plugin_id,
            "name": manifest.get("name") or plugin_id,
            "version": manifest.get("version", ""),
            "description": manifest.get("description", ""),
            "path": str(entry),
            "loaded": False,
            "error": None,
        }

        # Try to determine if enabled from config
        try:
            raw = _load_raw_config()
            plugins_cfg = raw.get("plugins") or {}
            if isinstance(plugins_cfg, dict):
                enabled_list = plugins_cfg.get("enabled") or []
                disabled_list = plugins_cfg.get("disabled") or []
                plugin["enabled"] = (
                    plugin_id in enabled_list or
                    (not disabled_list and not enabled_list) or
                    plugin_id not in disabled_list
                )
            else:
                plugin["enabled"] = True
        except Exception:
            plugin["enabled"] = True

        # Try import to check if it loads
        try:
            init_py = entry / "__init__.py"
            if init_py.exists():
                plugin["loaded"] = True
        except Exception:
            pass

        plugins.append(plugin)

    return plugins


# ---------------------------------------------------------------------------
# Default callback
# ---------------------------------------------------------------------------

@plugins_app.callback(invoke_without_command=True)
def plugins_default(ctx: typer.Context):
    """List plugins (default action)."""
    if ctx.invoked_subcommand is None:
        list_plugins(json_output=False, enabled=False, verbose=False)


# ---------------------------------------------------------------------------
# plugins list
# ---------------------------------------------------------------------------

@plugins_app.command("list")
def list_plugins(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    enabled: bool = typer.Option(False, "--enabled", help="Only show enabled plugins"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed entries"),
):
    """List discovered plugins"""
    extensions_dir = _get_extensions_dir()
    plugins = _scan_plugins(extensions_dir)

    if enabled:
        plugins = [p for p in plugins if p.get("enabled", True)]

    if json_output:
        print(json.dumps(plugins, indent=2, ensure_ascii=False))
        return

    if not plugins:
        console.print("[yellow]No plugins found[/yellow]")
        console.print(f"  Extensions directory: {extensions_dir}")
        return

    table = Table(title=f"Plugins ({len(plugins)} found)")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Version", style="dim")
    table.add_column("Enabled", style="yellow")
    if verbose:
        table.add_column("Description", style="dim")

    for p in plugins:
        enabled_icon = "[green]✓[/green]" if p.get("enabled", True) else "[red]✗[/red]"
        row = [p["id"], p.get("name", p["id"]), p.get("version", ""), enabled_icon]
        if verbose:
            row.append(p.get("description", ""))
        table.add_row(*row)

    console.print(table)


# ---------------------------------------------------------------------------
# plugins info
# ---------------------------------------------------------------------------

@plugins_app.command("info")
def info(
    plugin_id: str = typer.Argument(..., help="Plugin ID"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show plugin details"""
    extensions_dir = _get_extensions_dir()
    plugins = _scan_plugins(extensions_dir)
    plugin = next((p for p in plugins if p["id"] == plugin_id), None)

    if not plugin:
        console.print(f"[red]Plugin not found:[/red] {plugin_id}")
        raise typer.Exit(1)

    if json_output:
        console.print(json.dumps(plugin, indent=2, ensure_ascii=False))
        return

    console.print(f"[bold]Plugin: {plugin['id']}[/bold]")
    console.print(f"  Name:        {plugin.get('name', '')}")
    console.print(f"  Version:     {plugin.get('version', '')}")
    console.print(f"  Description: {plugin.get('description', '')}")
    console.print(f"  Path:        {plugin.get('path', '')}")
    console.print(f"  Enabled:     {'yes' if plugin.get('enabled', True) else 'no'}")
    if plugin.get("error"):
        console.print(f"  [red]Error:[/red] {plugin['error']}")


# ---------------------------------------------------------------------------
# plugins enable / disable
# ---------------------------------------------------------------------------

@plugins_app.command("enable")
def enable(
    plugin_id: str = typer.Argument(..., help="Plugin ID"),
):
    """Enable a plugin in config"""
    raw = _load_raw_config()
    if "plugins" not in raw or not isinstance(raw.get("plugins"), dict):
        raw["plugins"] = {}

    disabled = raw["plugins"].get("disabled") or []
    if plugin_id in disabled:
        disabled.remove(plugin_id)
        raw["plugins"]["disabled"] = disabled

    enabled_list = raw["plugins"].get("enabled") or []
    if plugin_id not in enabled_list:
        enabled_list.append(plugin_id)
        raw["plugins"]["enabled"] = enabled_list

    try:
        _save_raw(raw)
        console.print(f"[green]✓[/green] Plugin enabled: [cyan]{plugin_id}[/cyan]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@plugins_app.command("disable")
def disable(
    plugin_id: str = typer.Argument(..., help="Plugin ID"),
):
    """Disable a plugin in config"""
    raw = _load_raw_config()
    if "plugins" not in raw or not isinstance(raw.get("plugins"), dict):
        raw["plugins"] = {}

    enabled_list = raw["plugins"].get("enabled") or []
    if plugin_id in enabled_list:
        enabled_list.remove(plugin_id)
        raw["plugins"]["enabled"] = enabled_list

    disabled = raw["plugins"].get("disabled") or []
    if plugin_id not in disabled:
        disabled.append(plugin_id)
        raw["plugins"]["disabled"] = disabled

    try:
        _save_raw(raw)
        console.print(f"[green]✓[/green] Plugin disabled: [cyan]{plugin_id}[/cyan]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# plugins install
# ---------------------------------------------------------------------------

@plugins_app.command("install")
def install(
    spec: str = typer.Argument(..., help="Plugin path or spec (e.g. ./my-plugin or plugin-name)"),
    link: bool = typer.Option(False, "--link", "-l", help="Symlink local path instead of copying"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Install a plugin from a local path or spec"""
    import shutil

    extensions_dir = _get_extensions_dir()
    extensions_dir.mkdir(parents=True, exist_ok=True)

    source = Path(spec).expanduser()
    if not source.exists():
        console.print(f"[red]Plugin path not found:[/red] {spec}")
        raise typer.Exit(1)

    plugin_name = source.name
    dest = extensions_dir / plugin_name

    if dest.exists():
        console.print(f"[yellow]Plugin already installed:[/yellow] {plugin_name}")
        console.print("Remove first with: [cyan]openclaw plugins uninstall {plugin_name}[/cyan]")
        raise typer.Exit(1)

    try:
        if link:
            dest.symlink_to(source.resolve())
            action = "Linked"
        else:
            if source.is_dir():
                shutil.copytree(source, dest)
            else:
                shutil.copy2(source, dest)
            action = "Installed"

        if json_output:
            console.print(json.dumps({"installed": plugin_name, "path": str(dest)}))
        else:
            console.print(f"[green]✓[/green] {action} plugin: [cyan]{plugin_name}[/cyan]")
            console.print(f"  Path: {dest}")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# plugins uninstall
# ---------------------------------------------------------------------------

@plugins_app.command("uninstall")
def uninstall(
    plugin_id: str = typer.Argument(..., help="Plugin ID"),
    keep_files: bool = typer.Option(False, "--keep-files", help="Keep files on disk"),
    force: bool = typer.Option(False, "--force", help="Skip confirmation"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Uninstall a plugin"""
    import shutil

    extensions_dir = _get_extensions_dir()
    plugin_dir = extensions_dir / plugin_id

    # Remove from config
    raw = _load_raw_config()
    if "plugins" in raw and isinstance(raw.get("plugins"), dict):
        for lst_key in ("enabled", "disabled"):
            lst = raw["plugins"].get(lst_key) or []
            if plugin_id in lst:
                lst.remove(plugin_id)
                raw["plugins"][lst_key] = lst

    # Remove files
    if not keep_files and plugin_dir.exists():
        if not force:
            confirm = typer.confirm(f"Remove plugin files at {plugin_dir}?", default=False)
            if not confirm:
                console.print("Keeping files. Config updated.")
                try:
                    _save_raw(raw)
                except Exception:
                    pass
                return
        try:
            if plugin_dir.is_symlink():
                plugin_dir.unlink()
            elif plugin_dir.is_dir():
                shutil.rmtree(plugin_dir)
            else:
                plugin_dir.unlink()
        except Exception as e:
            console.print(f"[red]Failed to remove files:[/red] {e}")
            raise typer.Exit(1)

    try:
        _save_raw(raw)
        if json_output:
            console.print(json.dumps({"uninstalled": plugin_id}))
        else:
            console.print(f"[green]✓[/green] Plugin uninstalled: [cyan]{plugin_id}[/cyan]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# plugins doctor
# ---------------------------------------------------------------------------

@plugins_app.command("doctor")
def doctor(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Report plugin load issues"""
    extensions_dir = _get_extensions_dir()
    plugins = _scan_plugins(extensions_dir)
    issues = []

    for p in plugins:
        plugin_path = Path(p.get("path", ""))
        plugin_issues = []

        # Check manifest
        has_manifest = (
            (plugin_path / "plugin.json").exists() or
            (plugin_path / "package.json").exists()
        )
        if not has_manifest:
            plugin_issues.append("Missing plugin.json or package.json manifest")

        # Check entrypoint
        has_entry = (
            (plugin_path / "__init__.py").exists() or
            (plugin_path / "index.js").exists() or
            (plugin_path / "index.ts").exists()
        )
        if not has_entry:
            plugin_issues.append("Missing entrypoint (__init__.py, index.js, or index.ts)")

        if plugin_issues:
            issues.append({"id": p["id"], "path": str(plugin_path), "issues": plugin_issues})

    if json_output:
        console.print(json.dumps(issues, indent=2))
        return

    if not issues:
        console.print("[green]✓[/green] All plugins look healthy")
        return

    console.print(f"[red]Found {len(issues)} plugin(s) with issues:[/red]\n")
    for item in issues:
        console.print(f"  [cyan]{item['id']}[/cyan]  {item['path']}")
        for iss in item["issues"]:
            console.print(f"    • {iss}")
