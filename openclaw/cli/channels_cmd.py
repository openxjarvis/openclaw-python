"""Channel management commands — mirrors TS src/cli/channels-cli.ts"""
from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from ..config.loader import load_config

console = Console()
channels_app = typer.Typer(help="Messaging channel management")


@channels_app.command("list")
def list_channels(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List configured channels"""
    try:
        config = load_config()
        
        if json_output:
            result = {}
            if config.channels.telegram:
                result["telegram"] = {
                    "enabled": config.channels.telegram.enabled,
                    "configured": bool(config.channels.telegram.botToken),
                }
            console.print(json.dumps(result, indent=2))
            return
        
        table = Table(title="Channels")
        table.add_column("Channel", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Details", style="yellow")
        
        if config.channels.telegram:
            status = "✓ Enabled" if config.channels.telegram.enabled else "✗ Disabled"
            details = "Configured" if config.channels.telegram.botToken else "Not configured"
            table.add_row("Telegram", status, details)
        
        if config.channels.whatsapp:
            status = "✓ Enabled" if config.channels.whatsapp.enabled else "✗ Disabled"
            table.add_row("WhatsApp", status, "")
        
        if config.channels.discord:
            status = "✓ Enabled" if config.channels.discord.enabled else "✗ Disabled"
            table.add_row("Discord", status, "")
        
        if config.channels.slack:
            status = "✓ Enabled" if config.channels.slack.enabled else "✗ Disabled"
            table.add_row("Slack", status, "")
        
        console.print(table)
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@channels_app.command("status")
def status(
    probe: bool = typer.Option(False, "--probe", help="Probe channel credentials"),
    deep: bool = typer.Option(False, "--deep", help="Deep probe (includes connectivity tests)"),
    timeout: int = typer.Option(10000, "--timeout", help="Timeout in ms"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token_opt: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
):
    """Show channel connection status"""
    if not probe:
        # No probe, just list configured channels
        list_channels(json_output=json_output)
        return
    
    # Probe via gateway RPC
    try:
        from .gateway_rpc_cli import GatewayRpcOpts, call_gateway_from_cli
        
        opts = GatewayRpcOpts(url=url, token=token_opt, timeout=timeout, json_output=json_output)
        result = call_gateway_from_cli("channels.status", opts, {"probe": True, "deep": deep}, show_progress=False)
        
        if json_output:
            console.print(json.dumps(result, indent=2))
            return
        
        # Display probe results
        channels = result.get("channels", {})
        if not channels:
            console.print("[yellow]No channel status available[/yellow]")
            return
        
        table = Table(title="Channel Status (Probed)")
        table.add_column("Channel", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Connected", style="yellow")
        table.add_column("Details", style="dim")
        
        for channel_id, info in channels.items():
            status = info.get("status", "unknown")
            connected = info.get("connected", False)
            error = info.get("error", "")
            
            # Format status
            if status == "enabled" and connected:
                status_str = "[green]✓ Active[/green]"
            elif status == "enabled":
                status_str = "[yellow]⚠ Enabled[/yellow]"
            else:
                status_str = "[dim]✗ Disabled[/dim]"
            
            # Format connected
            conn_str = "[green]Yes[/green]" if connected else "[red]No[/red]"
            
            # Details
            details = error if error else ""
            
            table.add_row(channel_id.capitalize(), status_str, conn_str, details)
        
        console.print(table)
    
    except Exception as e:
        console.print(f"[red]Error probing channels:[/red] {e}")
        console.print("[dim]Hint: Make sure the gateway is running (openclaw start)[/dim]")
        raise typer.Exit(1)


@channels_app.command("add")
def add(
    channel: str = typer.Argument(..., help="Channel type (telegram, discord, slack)"),
    token: str = typer.Option(None, "--token", help="Bot token"),
    account_id: str = typer.Option(None, "--account-id", help="Account ID"),
    dm_policy: str = typer.Option("pairing", "--dm-policy", help="DM policy (open, pairing, allowlist)"),
):
    """Add or update a channel account"""
    from ..config.loader import save_config
    
    console.print(f"[cyan]Adding {channel} channel...[/cyan]\n")
    
    # Validate channel type
    if channel not in ["telegram", "discord", "slack", "whatsapp"]:
        console.print(f"[red]Unknown channel:[/red] {channel}")
        console.print("Supported channels: telegram, discord, slack, whatsapp")
        raise typer.Exit(1)
    
    # Load config
    try:
        config = load_config()
    except Exception as e:
        console.print(f"[red]Error loading config:[/red] {e}")
        console.print("Run: openclaw onboard")
        raise typer.Exit(1)
    
    # Get token (from option or prompt)
    if token is None:
        import os
        env_var = f"{channel.upper()}_BOT_TOKEN"
        env_token = os.getenv(env_var)
        
        if env_token:
            use_env = typer.confirm(f"Use {env_var} from environment?", default=True)
            if use_env:
                token = env_token
                console.print(f"✓ Using token from {env_var}")
        
        if token is None:
            token = typer.prompt(f"{channel.title()} bot token", hide_input=True)
    
    if not token:
        console.print("[red]Error:[/red] Token is required")
        raise typer.Exit(1)
    
    # Build channel config as proper Pydantic model so model_dump() emits
    # camelCase keys (botToken / dmPolicy) matching the TS config format.
    if channel == "telegram":
        from ..config.schema import TelegramChannelConfig, ChannelsConfig
        tg_cfg = TelegramChannelConfig(
            enabled=True,
            botToken=token,
            dmPolicy=dm_policy,
        )
        if not config.channels:
            config.channels = ChannelsConfig()
        config.channels.telegram = tg_cfg
    else:
        from ..config.schema import ChannelConfig, ChannelsConfig
        ch_cfg = ChannelConfig(enabled=True, botToken=token, dmPolicy=dm_policy)
        if not config.channels:
            config.channels = ChannelsConfig()
        setattr(config.channels, channel, ch_cfg)
    
    # Save
    try:
        save_config(config)
        console.print(f"\n[green]✓[/green] {channel.title()} channel configured")
        console.print(f"DM Policy: {dm_policy}")
        
        if account_id:
            console.print(f"Account ID: {account_id}")
        
        console.print("\nStart Gateway to activate: openclaw gateway run")
    except Exception as e:
        console.print(f"[red]Error saving config:[/red] {e}")
        raise typer.Exit(1)


@channels_app.command("remove")
def remove(
    channel: str = typer.Argument(..., help="Channel type (telegram, discord, slack)"),
):
    """Remove channel account"""
    from ..config.loader import save_config
    
    console.print(f"[cyan]Removing {channel} channel...[/cyan]\n")
    
    # Validate channel type
    if channel not in ["telegram", "discord", "slack", "whatsapp"]:
        console.print(f"[red]Unknown channel:[/red] {channel}")
        raise typer.Exit(1)
    
    # Load config
    try:
        config = load_config()
    except Exception as e:
        console.print(f"[red]Error loading config:[/red] {e}")
        raise typer.Exit(1)
    
    # Check if channel exists
    channel_cfg = getattr(config.channels, channel, None)
    if not channel_cfg or not channel_cfg.get("enabled"):
        console.print(f"[yellow]⚠[/yellow]  {channel.title()} channel is not configured")
        return
    
    # Confirm removal
    confirm = typer.confirm(f"Remove {channel.title()} channel configuration?", default=False)
    if not confirm:
        console.print("Cancelled")
        return
    
    # Remove channel config
    channel_cfg["enabled"] = False
    
    # Save
    try:
        save_config(config)
        console.print(f"[green]✓[/green] {channel.title()} channel removed")
    except Exception as e:
        console.print(f"[red]Error saving config:[/red] {e}")
        raise typer.Exit(1)


@channels_app.command("login")
def login(
    channel: str = typer.Argument(..., help="Channel type"),
    account: Optional[str] = typer.Option(None, "--account", help="Account id"),
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
    timeout: int = typer.Option(10_000, "--timeout", help="Timeout in ms"),
):
    """Link channel account (trigger login flow via gateway)"""
    import asyncio
    try:
        from .gateway_rpc_cli import GatewayRpcOpts, call_gateway_from_cli
        opts = GatewayRpcOpts(url=url, token=token, timeout=timeout)
        params: dict = {"channel": channel}
        if account:
            params["accountId"] = account
        result = call_gateway_from_cli("channels.login", opts, params)
        if isinstance(result, dict):
            if result.get("qrCode"):
                console.print("[cyan]Scan this QR code with your mobile app:[/cyan]")
                console.print(result["qrCode"])
            elif result.get("url"):
                console.print(f"[cyan]Open this URL to authenticate:[/cyan]\n{result['url']}")
            elif result.get("message"):
                console.print(result["message"])
            else:
                console.print(f"[green]✓[/green] Login flow initiated for {channel}")
        else:
            console.print(f"[green]✓[/green] Login flow initiated for {channel}")
    except Exception as e:
        # Fallback: show informational message for known channels
        console.print(f"[cyan]Login flow for {channel}:[/cyan]")
        if channel == "whatsapp":
            console.print("  Start the gateway — it will display a QR code automatically.")
            console.print("  Run: [cyan]openclaw gateway run[/cyan]")
        else:
            console.print(f"  Gateway is required for {channel} login.")
            console.print(f"  Configure with: [cyan]openclaw channels add {channel}[/cyan]")
        console.print(f"\n[dim]Gateway error: {e}[/dim]")


@channels_app.command("logout")
def logout(
    channel: str = typer.Option(..., "--channel", help="Channel to logout"),
    account: Optional[str] = typer.Option(None, "--account", help="Account id"),
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
    timeout: int = typer.Option(10_000, "--timeout", help="Timeout in ms"),
):
    """Log out of a channel session"""
    try:
        from .gateway_rpc_cli import GatewayRpcOpts, call_gateway_from_cli
        opts = GatewayRpcOpts(url=url, token=token, timeout=timeout)
        params: dict = {"channel": channel}
        if account:
            params["accountId"] = account
        result = call_gateway_from_cli("channels.logout", opts, params)
        console.print(f"[green]✓[/green] Logged out from {channel}")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@channels_app.command("capabilities")
def capabilities(
    channel: Optional[str] = typer.Option(None, "--channel", help="Channel name"),
    account: Optional[str] = typer.Option(None, "--account", help="Account id"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
    timeout: int = typer.Option(10_000, "--timeout", help="Timeout in ms"),
):
    """Show provider capabilities"""
    try:
        from .gateway_rpc_cli import GatewayRpcOpts, call_gateway_from_cli
        opts = GatewayRpcOpts(url=url, token=token, timeout=timeout, json_output=json_output)
        params: dict = {}
        if channel:
            params["channel"] = channel
        if account:
            params["accountId"] = account
        result = call_gateway_from_cli("channels.capabilities", opts, params)

        if json_output:
            console.print(json.dumps(result, indent=2, ensure_ascii=False))
            return

        caps = result.get("capabilities", result) if isinstance(result, dict) else result or {}
        if isinstance(caps, dict):
            for ch_name, ch_caps in caps.items():
                console.print(f"\n[bold]{ch_name}[/bold]")
                if isinstance(ch_caps, list):
                    for cap in ch_caps:
                        console.print(f"  • {cap}")
                elif isinstance(ch_caps, dict):
                    for k, v in ch_caps.items():
                        console.print(f"  {k}: {v}")
        else:
            console.print(str(caps))

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@channels_app.command("resolve")
def resolve(
    entries: list[str] = typer.Argument(..., help="Entries to resolve (names/usernames)"),
    channel: Optional[str] = typer.Option(None, "--channel", help="Channel name"),
    account: Optional[str] = typer.Option(None, "--account", help="Account id"),
    kind: str = typer.Option("auto", "--kind", help="Target kind (auto|user|group)"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
    timeout: int = typer.Option(10_000, "--timeout", help="Timeout in ms"),
):
    """Resolve channel/user names to IDs"""
    try:
        from .gateway_rpc_cli import GatewayRpcOpts, call_gateway_from_cli
        opts = GatewayRpcOpts(url=url, token=token, timeout=timeout, json_output=json_output)
        params: dict = {"entries": list(entries), "kind": kind}
        if channel:
            params["channel"] = channel
        if account:
            params["accountId"] = account
        result = call_gateway_from_cli("channels.resolve", opts, params)

        if json_output:
            console.print(json.dumps(result, indent=2, ensure_ascii=False))
            return

        resolved = result.get("resolved", result) if isinstance(result, dict) else result or []
        if not resolved:
            console.print("[yellow]No results found[/yellow]")
            return

        table = Table(title="Resolved Identities")
        table.add_column("Input", style="cyan")
        table.add_column("ID", style="green")
        table.add_column("Type", style="yellow")
        table.add_column("Display Name", style="dim")

        for item in (resolved if isinstance(resolved, list) else []):
            if isinstance(item, dict):
                table.add_row(
                    item.get("input", ""),
                    item.get("id", ""),
                    item.get("type", ""),
                    item.get("displayName", ""),
                )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@channels_app.command("logs")
def logs(
    channel: str = typer.Option("all", "--channel", help="Channel filter (or 'all')"),
    lines: int = typer.Option(200, "--lines", help="Number of lines to show"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    verbose: bool = typer.Option(False, "--verbose", help="Verbose connection logs"),
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
    timeout: int = typer.Option(10_000, "--timeout", help="Timeout in ms"),
):
    """Show recent channel logs"""
    try:
        from .gateway_rpc_cli import GatewayRpcOpts, call_gateway_from_cli
        opts = GatewayRpcOpts(url=url, token=token, timeout=timeout, json_output=json_output)
        params: dict = {"limit": lines}
        if channel and channel != "all":
            params["channel"] = channel
        if verbose:
            params["verbose"] = True
        result = call_gateway_from_cli("channels.logs", opts, params)

        log_lines = result.get("lines", result) if isinstance(result, dict) else result or []

        if json_output:
            console.print(json.dumps(log_lines, indent=2, ensure_ascii=False))
            return

        if not log_lines:
            console.print("[yellow]No log entries found[/yellow]")
            return

        for line in (log_lines if isinstance(log_lines, list) else [str(log_lines)]):
            console.print(line)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
