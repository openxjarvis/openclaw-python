"""Model configuration and discovery commands — mirrors TS src/cli/models-cli.ts"""
from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from ..config.paths import resolve_state_dir

console = Console()
models_app = typer.Typer(help="Model discovery, scanning, and configuration", no_args_is_help=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_cfg():
    from ..config.loader import load_config
    return load_config()


def _save_cfg(cfg):
    from ..config.loader import save_config
    save_config(cfg)


def _cfg_dict(cfg) -> dict:
    """Return config as a mutable dict."""
    import dataclasses, copy
    if isinstance(cfg, dict):
        return copy.deepcopy(cfg)
    try:
        return json.loads(json.dumps(cfg, default=lambda o: o.__dict__))
    except Exception:
        return {}


def _get_nested(d: dict, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, default)
        if d is None:
            return default
    return d


def _set_nested(d: dict, keys: list, value):
    for k in keys[:-1]:
        if k not in d or not isinstance(d[k], dict):
            d[k] = {}
        d = d[k]
    d[keys[-1]] = value


def _unset_nested(d: dict, keys: list):
    for k in keys[:-1]:
        if not isinstance(d, dict) or k not in d:
            return
        d = d[k]
    if isinstance(d, dict):
        d.pop(keys[-1], None)


def _save_raw(raw: dict):
    """Write raw config dict back to file."""
    from ..config.loader import write_config_file
    write_config_file(raw)


def _load_raw() -> dict:
    """Load config as a plain dict (for mutation)."""
    from pathlib import Path as _Path
    from ..config.loader import load_config_raw
    from ..config.paths import resolve_config_path
    try:
        cfg_path = resolve_config_path()
        if cfg_path and _Path(cfg_path).exists():
            return load_config_raw(_Path(cfg_path)) or {}
    except Exception:
        pass
    # Fallback: default location
    default = _resolve_state_dir() / "openclaw.json"
    if default.exists():
        try:
            return load_config_raw(default) or {}
        except Exception:
            pass
    return {}


# ---------------------------------------------------------------------------
# Default callback — show status
# ---------------------------------------------------------------------------

@models_app.callback(invoke_without_command=True)
def models_default(ctx: typer.Context):
    """Show model status (default action)."""
    if ctx.invoked_subcommand is None:
        _show_status(json_output=False, plain=False, check=False, probe=False, agent=None)


# ---------------------------------------------------------------------------
# models list
# ---------------------------------------------------------------------------

@models_app.command("list")
def list_models(
    all_models: bool = typer.Option(False, "--all", help="Show full model catalog"),
    local: bool = typer.Option(False, "--local", help="Filter to local models"),
    provider: Optional[str] = typer.Option(None, "--provider", help="Filter by provider"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    plain: bool = typer.Option(False, "--plain", help="Plain line output"),
):
    """List models (configured by default, full catalog with --all)"""
    try:
        from .gateway_rpc_cli import GatewayRpcOpts, call_gateway_from_cli, gateway_unreachable_message
        from ..gateway.rpc_client import GatewayRPCError

        opts = GatewayRpcOpts(json_output=json_output)
        params: dict = {}
        if all_models:
            params["all"] = True
        if local:
            params["local"] = True
        if provider:
            params["provider"] = provider

        result = call_gateway_from_cli("models.list", opts, params)
        models = result.get("models", result) if isinstance(result, dict) else result or []

        if json_output:
            console.print(json.dumps(models if isinstance(models, list) else [models], indent=2))
            return

        if plain:
            for m in (models if isinstance(models, list) else []):
                mid = m.get("id", m) if isinstance(m, dict) else m
                console.print(mid)
            return

        if not models:
            console.print("[yellow]No models found[/yellow]")
            return

        table = Table(title="Models")
        table.add_column("ID", style="cyan")
        table.add_column("Provider", style="green")
        table.add_column("Name", style="yellow")
        table.add_column("Context", justify="right")

        for m in (models if isinstance(models, list) else []):
            if isinstance(m, dict):
                table.add_row(
                    m.get("id", ""),
                    m.get("provider", ""),
                    m.get("name", ""),
                    str(m.get("contextLength", "")),
                )
            else:
                table.add_row(str(m), "", "", "")

        console.print(table)

    except Exception as e:
        _fallback_models_from_config(json_output, plain)


def _fallback_models_from_config(json_output: bool, plain: bool):
    """Show models from local config when gateway is unreachable."""
    raw = _load_raw()
    aliases = _get_nested(raw, "models", "aliases") or {}
    default_model = _get_nested(raw, "agents", "defaults", "model") or ""

    if json_output:
        out = [{"id": v, "alias": k} for k, v in aliases.items()]
        if default_model:
            out.insert(0, {"id": default_model, "alias": "(default)"})
        console.print(json.dumps(out, indent=2))
        return

    console.print("[dim](Gateway unreachable — showing local config)[/dim]\n")
    if default_model:
        console.print(f"Default model: [cyan]{default_model}[/cyan]")
    if aliases:
        console.print("\nModel aliases:")
        for alias, model_id in aliases.items():
            console.print(f"  [green]{alias}[/green] → {model_id}")


# ---------------------------------------------------------------------------
# models status
# ---------------------------------------------------------------------------

@models_app.command("status")
def status(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    plain: bool = typer.Option(False, "--plain", help="Plain output"),
    check: bool = typer.Option(False, "--check", help="Exit non-zero if auth expiring/expired"),
    probe: bool = typer.Option(False, "--probe", help="Probe configured provider auth"),
    agent: Optional[str] = typer.Option(None, "--agent", help="Agent id"),
):
    """Show configured model state"""
    _show_status(json_output=json_output, plain=plain, check=check, probe=probe, agent=agent)


def _show_status(json_output: bool, plain: bool, check: bool, probe: bool, agent: Optional[str]):
    raw = _load_raw()
    defaults = _get_nested(raw, "agents", "defaults") or {}
    image_model = defaults.get("imageModel", "(not set)")
    aliases = _get_nested(raw, "models", "aliases") or {}

    # Read model + fallbacks from agents.defaults.model (canonical path)
    primary, fallbacks = _get_primary_and_fallbacks(raw)
    model = primary or "(not set)"

    if json_output:
        console.print(json.dumps({
            "model": model,
            "imageModel": image_model,
            "fallbacks": fallbacks,
            "aliases": aliases,
        }, indent=2))
        return

    console.print("[bold]Model Configuration[/bold]\n")
    console.print(f"  Default model:       [cyan]{model}[/cyan]")
    console.print(f"  Default image model: [cyan]{image_model}[/cyan]")
    if fallbacks:
        console.print(f"  Fallbacks:           {', '.join(fallbacks)}")
    if aliases:
        console.print("\n  Aliases:")
        for k, v in aliases.items():
            console.print(f"    [green]{k}[/green] → {v}")

    if probe:
        console.print("\n[dim]Auth probe not yet implemented — requires gateway connection[/dim]")


# ---------------------------------------------------------------------------
# models set
# ---------------------------------------------------------------------------

@models_app.command("set")
def set_model(
    model: str = typer.Argument(..., help="Model id or alias"),
):
    """Set the default model (writes to agents.defaults.model)"""
    raw = _load_raw()
    _set_nested(raw, ["agents", "defaults", "model"], model)
    try:
        _save_raw(raw)
        console.print(f"[green]✓[/green] Default model set to: [cyan]{model}[/cyan]")
    except Exception as e:
        console.print(f"[red]Error saving config:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# models set-image
# ---------------------------------------------------------------------------

@models_app.command("set-image")
def set_image_model(
    model: str = typer.Argument(..., help="Model id or alias"),
):
    """Set the image model (writes to agents.defaults.imageModel)"""
    raw = _load_raw()
    _set_nested(raw, ["agents", "defaults", "imageModel"], model)
    try:
        _save_raw(raw)
        console.print(f"[green]✓[/green] Image model set to: [cyan]{model}[/cyan]")
    except Exception as e:
        console.print(f"[red]Error saving config:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# models scan
# ---------------------------------------------------------------------------

@models_app.command("scan")
def scan(
    no_probe: bool = typer.Option(False, "--no-probe", help="Skip live probes"),
    yes: bool = typer.Option(False, "--yes", help="Accept defaults without prompting"),
    max_candidates: int = typer.Option(6, "--max-candidates", help="Max fallback candidates"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Scan OpenRouter for free/available models and populate fallback list"""
    try:
        from .gateway_rpc_cli import GatewayRpcOpts, call_gateway_from_cli
        from ..gateway.rpc_client import GatewayRPCError

        opts = GatewayRpcOpts(json_output=json_output)
        params = {
            "noProbe": no_probe,
            "maxCandidates": max_candidates,
        }
        result = call_gateway_from_cli("models.scan", opts, params)

        if json_output:
            console.print(json.dumps(result, indent=2))
            return

        console.print(f"[green]✓[/green] Model scan complete")
        if isinstance(result, dict):
            added = result.get("added", [])
            if added:
                console.print(f"  Added {len(added)} fallback model(s):")
                for m in added:
                    console.print(f"    + {m}")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# models aliases subcommand group
# ---------------------------------------------------------------------------

aliases_app = typer.Typer(help="Manage model aliases")
models_app.add_typer(aliases_app, name="aliases")


@aliases_app.command("list")
def aliases_list(json_output: bool = typer.Option(False, "--json", help="Output JSON")):
    """List model aliases"""
    raw = _load_raw()
    aliases = _get_nested(raw, "models", "aliases") or {}

    if json_output:
        console.print(json.dumps(aliases, indent=2))
        return

    if not aliases:
        console.print("[yellow]No model aliases configured[/yellow]")
        return

    table = Table(title="Model Aliases")
    table.add_column("Alias", style="cyan")
    table.add_column("Model ID", style="green")
    for alias, model_id in aliases.items():
        table.add_row(alias, model_id)
    console.print(table)


@aliases_app.command("add")
def aliases_add(
    alias: str = typer.Argument(..., help="Alias name"),
    model_id: str = typer.Argument(..., help="Model ID"),
):
    """Add or update a model alias"""
    raw = _load_raw()
    if "models" not in raw or not isinstance(raw.get("models"), dict):
        raw["models"] = {}
    if "aliases" not in raw["models"] or not isinstance(raw["models"].get("aliases"), dict):
        raw["models"]["aliases"] = {}
    raw["models"]["aliases"][alias] = model_id
    try:
        _save_raw(raw)
        console.print(f"[green]✓[/green] Alias [cyan]{alias}[/cyan] → {model_id}")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@aliases_app.command("remove")
def aliases_remove(alias: str = typer.Argument(..., help="Alias name to remove")):
    """Remove a model alias"""
    raw = _load_raw()
    aliases = _get_nested(raw, "models", "aliases")
    if not aliases or alias not in aliases:
        console.print(f"[yellow]Alias not found:[/yellow] {alias}")
        raise typer.Exit(1)
    del aliases[alias]
    try:
        _save_raw(raw)
        console.print(f"[green]✓[/green] Alias [cyan]{alias}[/cyan] removed")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# Default callback
@aliases_app.callback(invoke_without_command=True)
def aliases_default(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        aliases_list()


# ---------------------------------------------------------------------------
# models fallbacks subcommand group
# ---------------------------------------------------------------------------

fallbacks_app = typer.Typer(help="Manage fallback models")
models_app.add_typer(fallbacks_app, name="fallbacks")


def _get_primary_and_fallbacks(raw: dict) -> tuple[str, list[str]]:
    """Read agents.defaults.model and return (primary, fallbacks).

    Mirrors the TS storage format: agents.defaults.model is either a plain
    string (primary only) or {primary, fallbacks} when fallbacks are set.
    This is the same path that bootstrap.py reads at runtime.
    """
    m = _get_nested(raw, "agents", "defaults", "model") or ""
    if isinstance(m, dict):
        return m.get("primary", ""), list(m.get("fallbacks") or [])
    return str(m), []


def _set_primary_and_fallbacks(raw: dict, primary: str, fallbacks: list[str]) -> None:
    """Write primary + fallbacks back to agents.defaults.model.

    Writes a plain string when fallbacks is empty (keeps config clean),
    otherwise writes {primary, fallbacks} object — matching TS format.
    """
    if fallbacks:
        _set_nested(raw, ["agents", "defaults", "model"], {"primary": primary, "fallbacks": fallbacks})
    else:
        _set_nested(raw, ["agents", "defaults", "model"], primary)


@fallbacks_app.command("list")
def fallbacks_list(json_output: bool = typer.Option(False, "--json", help="Output JSON")):
    """List fallback models"""
    raw = _load_raw()
    _, fallbacks = _get_primary_and_fallbacks(raw)
    if json_output:
        console.print(json.dumps(fallbacks, indent=2))
        return
    if not fallbacks:
        console.print("[yellow]No fallback models configured[/yellow]")
        return
    console.print("[bold]Fallback models:[/bold]")
    for i, m in enumerate(fallbacks):
        console.print(f"  {i + 1}. {m}")


@fallbacks_app.command("add")
def fallbacks_add(model: str = typer.Argument(..., help="Model ID to add")):
    """Add a model to the fallback list"""
    raw = _load_raw()
    primary, fallbacks = _get_primary_and_fallbacks(raw)
    if model in fallbacks:
        console.print(f"[yellow]Already in fallbacks:[/yellow] {model}")
        return
    fallbacks.append(model)
    _set_primary_and_fallbacks(raw, primary, fallbacks)
    try:
        _save_raw(raw)
        console.print(f"[green]✓[/green] Added to fallbacks: {model}")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@fallbacks_app.command("remove")
def fallbacks_remove(model: str = typer.Argument(..., help="Model ID to remove")):
    """Remove a model from the fallback list"""
    raw = _load_raw()
    primary, fallbacks = _get_primary_and_fallbacks(raw)
    if model not in fallbacks:
        console.print(f"[yellow]Not in fallbacks:[/yellow] {model}")
        raise typer.Exit(1)
    fallbacks.remove(model)
    _set_primary_and_fallbacks(raw, primary, fallbacks)
    try:
        _save_raw(raw)
        console.print(f"[green]✓[/green] Removed from fallbacks: {model}")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@fallbacks_app.command("clear")
def fallbacks_clear(yes: bool = typer.Option(False, "--yes", help="Skip confirmation")):
    """Clear all fallback models"""
    if not yes:
        confirm = typer.confirm("Clear all fallback models?", default=False)
        if not confirm:
            console.print("Cancelled")
            return
    raw = _load_raw()
    primary, _ = _get_primary_and_fallbacks(raw)
    _set_primary_and_fallbacks(raw, primary, [])
    try:
        _save_raw(raw)
        console.print("[green]✓[/green] Fallback models cleared")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@fallbacks_app.callback(invoke_without_command=True)
def fallbacks_default(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        fallbacks_list()


# ---------------------------------------------------------------------------
# models image-fallbacks subcommand group
# ---------------------------------------------------------------------------

image_fallbacks_app = typer.Typer(help="Manage image fallback models")
models_app.add_typer(image_fallbacks_app, name="image-fallbacks")


@image_fallbacks_app.command("list")
def image_fallbacks_list(json_output: bool = typer.Option(False, "--json", help="Output JSON")):
    """List image fallback models"""
    raw = _load_raw()
    fallbacks = _get_nested(raw, "models", "imageFallbacks") or []
    if json_output:
        console.print(json.dumps(fallbacks, indent=2))
        return
    if not fallbacks:
        console.print("[yellow]No image fallback models configured[/yellow]")
        return
    console.print("[bold]Image fallback models:[/bold]")
    for i, m in enumerate(fallbacks):
        console.print(f"  {i + 1}. {m}")


@image_fallbacks_app.command("add")
def image_fallbacks_add(model: str = typer.Argument(..., help="Model ID to add")):
    """Add a model to the image fallback list"""
    raw = _load_raw()
    raw.setdefault("models", {})
    fb = raw["models"].get("imageFallbacks") or []
    if model in fb:
        console.print(f"[yellow]Already in image fallbacks:[/yellow] {model}")
        return
    fb.append(model)
    raw["models"]["imageFallbacks"] = fb
    try:
        _save_raw(raw)
        console.print(f"[green]✓[/green] Added to image fallbacks: {model}")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@image_fallbacks_app.command("remove")
def image_fallbacks_remove(model: str = typer.Argument(..., help="Model ID to remove")):
    """Remove a model from the image fallback list"""
    raw = _load_raw()
    fb = _get_nested(raw, "models", "imageFallbacks") or []
    if model not in fb:
        console.print(f"[yellow]Not in image fallbacks:[/yellow] {model}")
        raise typer.Exit(1)
    fb.remove(model)
    raw.setdefault("models", {})["imageFallbacks"] = fb
    try:
        _save_raw(raw)
        console.print(f"[green]✓[/green] Removed from image fallbacks: {model}")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@image_fallbacks_app.command("clear")
def image_fallbacks_clear(yes: bool = typer.Option(False, "--yes", help="Skip confirmation")):
    """Clear all image fallback models"""
    if not yes:
        confirm = typer.confirm("Clear all image fallback models?", default=False)
        if not confirm:
            console.print("Cancelled")
            return
    raw = _load_raw()
    raw.setdefault("models", {})["imageFallbacks"] = []
    try:
        _save_raw(raw)
        console.print("[green]✓[/green] Image fallback models cleared")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@image_fallbacks_app.callback(invoke_without_command=True)
def image_fallbacks_default(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        image_fallbacks_list()


# ---------------------------------------------------------------------------
# models auth subcommand group
# ---------------------------------------------------------------------------

auth_app = typer.Typer(help="Manage model provider auth profiles")
models_app.add_typer(auth_app, name="auth")

# auth order sub-group
auth_order_app = typer.Typer(help="Manage per-agent auth order overrides")
auth_app.add_typer(auth_order_app, name="order")


@auth_app.command("add")
def auth_add():
    """Interactive auth helper (configure a new provider credential)"""
    console.print("[yellow]⚠[/yellow]  Interactive auth setup not yet implemented")
    console.print("To add credentials, edit your config or set environment variables:")
    console.print("  ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY")


@auth_app.command("login")
def auth_login(
    provider: Optional[str] = typer.Option(None, "--provider", help="Provider id"),
    method: Optional[str] = typer.Option(None, "--method", help="Auth method id"),
):
    """Run a provider auth flow"""
    console.print("[yellow]⚠[/yellow]  Provider OAuth flow not yet implemented")
    if provider:
        console.print(f"Provider: {provider}")


@auth_app.command("setup-token")
def auth_setup_token(
    provider: Optional[str] = typer.Option(None, "--provider", help="Provider id"),
):
    """Run provider CLI to create/sync a token"""
    console.print("[yellow]⚠[/yellow]  Token setup flow not yet implemented")


@auth_app.command("paste-token")
def auth_paste_token(
    provider: Optional[str] = typer.Option(None, "--provider", help="Provider id (e.g. google, anthropic, openai)"),
    profile_id: Optional[str] = typer.Option(None, "--profile-id", help="Auth profile id (default: <provider>:default)"),
    key: Optional[str] = typer.Option(None, "--key", help="API key (if omitted, prompted interactively)"),
):
    """Paste an API key into auth-profiles.json"""
    from ..config.auth_profiles import set_api_key, resolve_auth_store_path

    if not provider:
        provider = typer.prompt("Provider (e.g. google, anthropic, openai, openrouter)")
    provider = provider.strip().lower()

    if not key:
        import sys
        if sys.stdin.isatty():
            key = typer.prompt(f"Paste {provider} API key", hide_input=True)
        else:
            key = sys.stdin.readline().strip()

    if not key:
        console.print("[red]✗[/red]  No key provided — aborted")
        raise typer.Exit(1)

    pid = profile_id or f"{provider}:default"
    try:
        set_api_key(provider, key, profile_id=pid)
        path = resolve_auth_store_path()
        console.print(f"[green]✓[/green]  Saved [bold]{pid}[/bold] → {path}")
    except Exception as exc:
        console.print(f"[red]✗[/red]  Failed to save key: {exc}")
        raise typer.Exit(1)


@auth_app.command("login-github-copilot")
def auth_login_github_copilot():
    """Login to GitHub Copilot"""
    console.print("[yellow]⚠[/yellow]  GitHub Copilot login not yet implemented")


@auth_order_app.command("get")
def auth_order_get(
    agent: str = typer.Argument(..., help="Agent id"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show per-agent auth order override"""
    raw = _load_raw()
    agents_list = _get_nested(raw, "agents", "agents") or []
    entry = next((a for a in agents_list if isinstance(a, dict) and a.get("id") == agent), None)
    if not entry:
        console.print(f"[yellow]Agent not found:[/yellow] {agent}")
        raise typer.Exit(1)
    order = entry.get("authOrder") or entry.get("authProfileOrder") or []
    if json_output:
        console.print(json.dumps(order, indent=2))
    else:
        console.print(f"Auth order for [cyan]{agent}[/cyan]:")
        if order:
            for i, p in enumerate(order):
                console.print(f"  {i + 1}. {p}")
        else:
            console.print("  (using default)")


@auth_order_app.command("set")
def auth_order_set(
    agent: str = typer.Argument(..., help="Agent id"),
    profile: list[str] = typer.Argument(..., help="Auth profile IDs in order"),
):
    """Set per-agent auth order override"""
    raw = _load_raw()
    agents_list = _get_nested(raw, "agents", "agents") or []
    found = False
    for entry in agents_list:
        if isinstance(entry, dict) and entry.get("id") == agent:
            entry["authProfileOrder"] = list(profile)
            found = True
            break
    if not found:
        console.print(f"[yellow]Agent not found:[/yellow] {agent}")
        raise typer.Exit(1)
    try:
        _save_raw(raw)
        console.print(f"[green]✓[/green] Auth order for [cyan]{agent}[/cyan] set to: {', '.join(profile)}")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@auth_order_app.command("clear")
def auth_order_clear(agent: str = typer.Argument(..., help="Agent id")):
    """Clear per-agent auth order override"""
    raw = _load_raw()
    agents_list = _get_nested(raw, "agents", "agents") or []
    found = False
    for entry in agents_list:
        if isinstance(entry, dict) and entry.get("id") == agent:
            entry.pop("authProfileOrder", None)
            entry.pop("authOrder", None)
            found = True
            break
    if not found:
        console.print(f"[yellow]Agent not found:[/yellow] {agent}")
        raise typer.Exit(1)
    try:
        _save_raw(raw)
        console.print(f"[green]✓[/green] Auth order cleared for [cyan]{agent}[/cyan]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
