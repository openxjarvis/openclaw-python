"""System and event commands — mirrors TS src/cli/system-cli.ts"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

import typer
from rich.console import Console

console = Console()
system_app = typer.Typer(help="System events and heartbeats", no_args_is_help=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rpc(method: str, params: dict, timeout_ms: int = 10_000,
         json_output: bool = False, url: Optional[str] = None, token: Optional[str] = None):
    from .gateway_rpc_cli import GatewayRpcOpts, call_gateway_from_cli
    opts = GatewayRpcOpts(url=url, token=token, timeout=timeout_ms, json_output=json_output)
    return call_gateway_from_cli(method, opts, params)


# ---------------------------------------------------------------------------
# system heartbeat (with subcommands)
# ---------------------------------------------------------------------------

heartbeat_app = typer.Typer(help="Heartbeat management", no_args_is_help=True)
system_app.add_typer(heartbeat_app, name="heartbeat")


@heartbeat_app.command("ping")
def heartbeat_ping(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
    timeout: int = typer.Option(10_000, "--timeout", help="Timeout in ms"),
):
    """Ping gateway heartbeat (also: openclaw system heartbeat)"""
    try:
        result = _rpc("system.heartbeat", {}, timeout_ms=timeout,
                      json_output=json_output, url=url, token=token)

        if json_output:
            console.print(json.dumps(result or {}, indent=2))
            return

        ts = result.get("timestamp") or result.get("at") or "" if isinstance(result, dict) else ""
        console.print(f"[green]✓[/green] Gateway heartbeat OK" + (f"  [{ts}]" if ts else ""))

    except Exception as e:
        console.print(f"[red]✗[/red] Gateway not responding: {e}")
        raise typer.Exit(1)


# Default heartbeat callback (for `openclaw system heartbeat` without subcommand)
@heartbeat_app.callback(invoke_without_command=True)
def heartbeat_default(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
    timeout: int = typer.Option(10_000, "--timeout", help="Timeout in ms"),
):
    """Ping gateway heartbeat"""
    if ctx.invoked_subcommand is None:
        # No subcommand, default to ping
        try:
            result = _rpc("system.heartbeat", {}, timeout_ms=timeout,
                          json_output=json_output, url=url, token=token)

            if json_output:
                console.print(json.dumps(result or {}, indent=2))
                return

            ts = result.get("timestamp") or result.get("at") or "" if isinstance(result, dict) else ""
            console.print(f"[green]✓[/green] Gateway heartbeat OK" + (f"  [{ts}]" if ts else ""))

        except Exception as e:
            console.print(f"[red]✗[/red] Gateway not responding: {e}")
            raise typer.Exit(1)


@heartbeat_app.command("last")
def heartbeat_last(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
    timeout: int = typer.Option(10_000, "--timeout", help="Timeout in ms"),
):
    """Show last heartbeat timestamp"""
    try:
        result = _rpc("last-heartbeat", {}, timeout_ms=timeout,
                      json_output=json_output, url=url, token=token)

        if json_output:
            console.print(json.dumps(result or {}, indent=2))
            return

        if isinstance(result, dict):
            ts = result.get("ts", result.get("timestamp", ""))
            if ts:
                console.print(f"[cyan]Last heartbeat:[/cyan] {ts}")
            else:
                console.print("[dim]No heartbeat data available[/dim]")
        else:
            console.print(str(result) if result else "[dim]No heartbeat data[/dim]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@heartbeat_app.command("enable")
def heartbeat_enable(
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
):
    """Enable heartbeat"""
    console.print("[yellow]Note:[/yellow] Heartbeat enable/disable is not yet fully implemented via CLI")
    console.print("  To enable heartbeat, update your config file:")
    console.print("  ~/.openclaw/openclaw.json -> agents.defaults.heartbeat.every: \"30m\"")


@heartbeat_app.command("disable")
def heartbeat_disable(
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
):
    """Disable heartbeat"""
    console.print("[yellow]Note:[/yellow] Heartbeat enable/disable is not yet fully implemented via CLI")
    console.print("  To disable heartbeat, update your config file:")
    console.print("  ~/.openclaw/openclaw.json -> agents.defaults.heartbeat.every: \"0m\"")



# ---------------------------------------------------------------------------
# system presence
# ---------------------------------------------------------------------------

@system_app.command("presence")
def presence(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
    timeout: int = typer.Option(10_000, "--timeout", help="Timeout in ms"),
):
    """Show presence state"""
    try:
        result = _rpc("system.presence", {}, timeout_ms=timeout,
                      json_output=json_output, url=url, token=token)

        if json_output:
            console.print(json.dumps(result or {}, indent=2))
            return

        if isinstance(result, dict):
            state = result.get("state", "unknown")
            sessions = result.get("activeSessions", result.get("sessions", ""))
            console.print(f"[bold]Presence:[/bold] {state}")
            if sessions:
                console.print(f"  Active sessions: {sessions}")
        else:
            console.print(str(result) if result else "[dim]No presence data[/dim]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# system event (enqueue a system event)
# ---------------------------------------------------------------------------

@system_app.command("event")
def event(
    text: str = typer.Argument(..., help="Event text to enqueue"),
    mode: str = typer.Option("now", "--mode", help="Wake mode: 'now' or 'next-heartbeat'"),
    session_key: Optional[str] = typer.Option(None, "--session", help="Target session key"),
    agent_id: Optional[str] = typer.Option(None, "--agent", help="Target agent ID"),
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Enqueue a system event and optionally wake the heartbeat"""
    try:
        params = {
            "text": text,
            "mode": mode,
        }
        if session_key:
            params["sessionKey"] = session_key
        if agent_id:
            params["agentId"] = agent_id
        
        result = _rpc("system.event", params, timeout_ms=10_000,
                      json_output=json_output, url=url, token=token)

        if json_output:
            console.print(json.dumps(result or {}, indent=2))
            return

        console.print(f"[green]✓[/green] System event enqueued")
        if mode == "now":
            console.print("  [dim]Heartbeat will run immediately[/dim]")
        else:
            console.print("  [dim]Event will be processed on next heartbeat[/dim]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# system events (list/follow)
# ---------------------------------------------------------------------------

@system_app.command("events")
def events(
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow events (stream continuously)"),
    filter_event: Optional[str] = typer.Option(None, "--filter", help="Event type filter"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON lines"),
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
    timeout: int = typer.Option(30_000, "--timeout", help="Timeout in ms"),
):
    """List or follow gateway system events"""
    if follow:
        _follow_events(filter_event=filter_event, json_output=json_output, url=url, token=token)
    else:
        # One-shot: fetch recent events
        try:
            result = _rpc("system.events.recent", {
                "filter": filter_event,
                "limit": 50,
            }, timeout_ms=timeout, json_output=json_output, url=url, token=token)

            events_list = result.get("events", result) if isinstance(result, dict) else result or []

            if json_output:
                console.print(json.dumps(events_list, indent=2, ensure_ascii=False))
                return

            if not events_list:
                console.print("[yellow]No recent events[/yellow]")
                return

            for evt in (events_list if isinstance(events_list, list) else []):
                if isinstance(evt, dict):
                    ts = evt.get("timestamp") or evt.get("at") or ""
                    etype = evt.get("type") or evt.get("event") or "event"
                    data = evt.get("data") or {}
                    console.print(f"[dim]{ts}[/dim] [cyan]{etype}[/cyan] {json.dumps(data, ensure_ascii=False) if data else ''}")
                else:
                    console.print(str(evt))

        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)


def _follow_events(
    filter_event: Optional[str],
    json_output: bool,
    url: Optional[str],
    token: Optional[str],
):
    """Subscribe to gateway event stream via WebSocket."""
    import websockets

    async def _stream():
        from ..cli.gateway_rpc_cli import _resolve_gateway_url, _resolve_auth_token
        ws_url = _resolve_gateway_url(url)
        auth_token = _resolve_auth_token(token)

        connect_kwargs = {}
        if auth_token:
            connect_kwargs["additional_headers"] = {"Authorization": f"Bearer {auth_token}"}

        if not json_output:
            console.print(f"[dim]Connecting to {ws_url}...[/dim]")
            console.print("[dim]Press Ctrl+C to stop[/dim]\n")

        try:
            async with websockets.connect(ws_url, **connect_kwargs) as ws:
                # Subscribe to events
                subscribe_msg = {
                    "jsonrpc": "2.0",
                    "method": "system.events.subscribe",
                    "params": {"filter": filter_event} if filter_event else {},
                    "id": 1,
                }
                await ws.send(json.dumps(subscribe_msg))

                async for raw_msg in ws:
                    try:
                        msg = json.loads(raw_msg)
                    except Exception:
                        continue

                    # Filter out non-event messages
                    if "event" not in msg and "type" not in msg:
                        continue

                    if json_output:
                        console.print(json.dumps(msg, ensure_ascii=False))
                    else:
                        ts = msg.get("timestamp") or msg.get("at") or ""
                        etype = msg.get("event") or msg.get("type") or "event"
                        data = msg.get("data") or {}
                        console.print(
                            f"[dim]{ts}[/dim] [cyan]{etype}[/cyan] "
                            f"{json.dumps(data, ensure_ascii=False) if data else ''}"
                        )

        except KeyboardInterrupt:
            if not json_output:
                console.print("\n[dim]Stopped[/dim]")

    try:
        asyncio.run(_stream())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
