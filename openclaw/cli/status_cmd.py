"""Status, health, and sessions commands — mirrors TS src/commands/status.command.ts"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table
from ..config.paths import resolve_state_dir

console = Console()
status_app = typer.Typer(help="Status and health checks", no_args_is_help=False)


# ---------------------------------------------------------------------------
# Gateway RPC helpers
# ---------------------------------------------------------------------------

def _call_gw(method: str, params: dict[str, Any] | None = None, timeout_ms: int = 5_000) -> Any:
    """Call a gateway RPC method, returning None on error (graceful degradation)."""
    try:
        from .gateway_rpc_cli import GatewayRpcOpts, call_gateway_from_cli
        from ..gateway.rpc_client import GatewayRPCError

        opts = GatewayRpcOpts(timeout=timeout_ms, json_output=True)
        return call_gateway_from_cli(method, opts, params or {}, show_progress=False)
    except Exception:
        return None


def _ping_gateway(timeout_ms: int = 3_000) -> tuple[bool, int]:
    """Return (reachable, latency_ms). Latency is -1 if unreachable."""
    t0 = time.monotonic()
    result = _call_gw("health", {}, timeout_ms=timeout_ms)
    if result is not None:
        latency = int((time.monotonic() - t0) * 1000)
        return True, latency
    return False, -1


def _format_uptime(seconds: int) -> str:
    if seconds < 0:
        return "—"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _format_age_ms(ts_ms: int | float | None) -> str:
    """Convert a Unix-ms timestamp to a human-readable relative age string."""
    if not ts_ms:
        return "—"
    try:
        now_ms = time.time() * 1000
        diff_ms = now_ms - float(ts_ms)
        diff_s = diff_ms / 1000
        if diff_s < 0:
            return "just now"
        if diff_s < 60:
            return f"{int(diff_s)}s ago"
        if diff_s < 3600:
            return f"{int(diff_s // 60)}m ago"
        if diff_s < 86400:
            return f"{int(diff_s // 3600)}h ago"
        return f"{int(diff_s // 86400)}d ago"
    except Exception:
        return str(ts_ms)


def _channel_state_style(state: str, running: bool, connected: bool) -> tuple[str, str]:
    """Return (label, rich style) for a channel state."""
    state_lower = (state or "").lower()
    if state_lower in ("ok", "connected") or (running and connected):
        return "OK", "green"
    if state_lower in ("warn", "degraded") or running:
        return "WARN", "yellow"
    if state_lower in ("off", "disabled", "stopped"):
        return "OFF", "dim"
    if state_lower in ("setup", "unconfigured", "not_configured"):
        return "SETUP", "cyan"
    return state_lower or "—", "dim"


# ---------------------------------------------------------------------------
# status (default)
# ---------------------------------------------------------------------------

@status_app.command("status")
def status(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    all_info: bool = typer.Option(False, "--all", help="Full diagnosis (config + logs)"),
    deep: bool = typer.Option(False, "--deep", help="Probe channels"),
    usage: bool = typer.Option(False, "--usage", help="Show usage/quota snapshots"),
    timeout: int = typer.Option(10_000, "--timeout", help="Timeout in ms"),
):
    """Show gateway health, channel states, and recent sessions"""
    try:
        from ..config.loader import load_config
        config = load_config()
    except Exception as exc:
        console.print(f"[red]Error loading config:[/red] {exc}")
        raise typer.Exit(1)

    probe_timeout = min(timeout, 5_000)
    deep_timeout = timeout

    # ---- Fetch live data from running gateway ----
    reachable, latency_ms = _ping_gateway(timeout_ms=probe_timeout)

    health_data: dict[str, Any] = {}
    channels_data: dict[str, Any] = {}
    sessions_data: dict[str, Any] = {}
    agents_data: dict[str, Any] = {}

    if reachable:
        health_data = _call_gw("health", {}, timeout_ms=probe_timeout) or {}
        channels_data = _call_gw(
            "channels.status",
            {"probe": deep, "timeoutMs": deep_timeout if deep else probe_timeout},
            timeout_ms=deep_timeout if deep else probe_timeout,
        ) or {}
        sessions_data = _call_gw("sessions.list", {"limit": 20}, timeout_ms=probe_timeout) or {}
        agents_data = _call_gw("agents.list", {}, timeout_ms=probe_timeout) or {}

    # ---- Derive values ----
    port = config.gateway.port if config.gateway else 18789
    agent_model = (config.agent.model if config.agent and hasattr(config.agent, "model") else None) or "default"

    gw_uptime_sec = int((health_data.get("gateway") or {}).get("uptimeSec", -1))
    gw_connections = int((health_data.get("gateway") or {}).get("connections", 0))
    sessions_count = int((health_data.get("sessions") or {}).get("count", 0))
    agents_count = int((health_data.get("agents") or {}).get("count", 0))

    # Prefer sessions.list count if available
    if sessions_data:
        raw_sessions = sessions_data.get("sessions", sessions_data) if isinstance(sessions_data, dict) else sessions_data
        if isinstance(raw_sessions, list):
            sessions_count = len(raw_sessions)
        elif isinstance(raw_sessions, dict):
            sessions_count = int(raw_sessions.get("count", sessions_count))

    # Agents from agents.list
    agents_list = (agents_data.get("agents") or []) if isinstance(agents_data, dict) else []
    if agents_list:
        agents_count = len(agents_list)

    # ---- Channel summary ----
    channels_summary = channels_data.get("channels", {}) if isinstance(channels_data, dict) else {}
    channel_order = channels_data.get("channelOrder", list(channels_summary.keys())) if isinstance(channels_data, dict) else []
    channel_labels = channels_data.get("channelLabels", {}) if isinstance(channels_data, dict) else {}

    # Supplement with health.channels.active when channels.status registry is empty
    if reachable and not channel_order:
        active_from_health: list[str] = (health_data.get("channels") or {}).get("active", [])
        for ch_name in active_from_health:
            if ch_name not in channels_summary:
                channel_order.append(ch_name)
                channels_summary[ch_name] = {"configured": True, "running": True, "connected": True, "state": "ok"}

    # Also include channels from config (enabled ones not yet in summary)
    cfg_channels: list[str] = []
    chans = getattr(config, "channels", None)
    if chans:
        for ch_name in ("telegram", "discord", "whatsapp", "feishu", "slack", "signal"):
            ch = getattr(chans, ch_name, None)
            if ch and getattr(ch, "enabled", False):
                cfg_channels.append(ch_name)
    for ch_name in cfg_channels:
        if ch_name not in channels_summary:
            channel_order.append(ch_name)
            channels_summary[ch_name] = {
                "configured": True,
                "running": reachable,
                "connected": reachable,
                "state": "ok" if reachable else "unknown",
            }

    channels_running = sum(1 for v in channels_summary.values() if isinstance(v, dict) and v.get("running"))

    # ---- JSON output ----
    if json_output:
        channels_json: dict[str, Any] = {}
        for cid, cdata in channels_summary.items():
            if not isinstance(cdata, dict):
                continue
            label, _ = _channel_state_style(cdata.get("state", ""), cdata.get("running", False), cdata.get("connected", False))
            channels_json[cid] = {
                "state": label.lower(),
                "running": cdata.get("running", False),
                "connected": cdata.get("connected", False),
            }
        result: dict[str, Any] = {
            "gateway": {
                "port": port,
                "running": reachable,
                "uptimeSec": gw_uptime_sec if reachable else None,
                "latencyMs": latency_ms if reachable else None,
                "connections": gw_connections if reachable else None,
            },
            "agent": {
                "model": agent_model,
                "count": agents_count,
            },
            "channels": channels_json,
            "sessions": {
                "count": sessions_count,
            },
        }
        console.print(json.dumps(result, indent=2))
        return

    # ---- Rich table output ----
    if all_info:
        _render_all(config, reachable, latency_ms, health_data, channels_data, sessions_data, agents_data, timeout)
        return

    # Overview table
    overview = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    overview.add_column("Item", style="cyan", no_wrap=True)
    overview.add_column("Value")

    if reachable:
        gw_value = f"[green]running[/green] · port {port} · {latency_ms}ms"
        if gw_connections:
            gw_value += f" · {gw_connections} conn"
        if gw_uptime_sec >= 0:
            gw_value += f" · up {_format_uptime(gw_uptime_sec)}"
    else:
        gw_value = f"[red]offline[/red] · port {port} (run: [cyan]openclaw gateway run[/cyan])"

    overview.add_row("Gateway", gw_value)
    overview.add_row("Agent", f"{agent_model}" + (f" · {agents_count} configured" if agents_count else ""))
    overview.add_row("Sessions", f"{sessions_count} active" if reachable else "—")
    overview.add_row("Channels", f"{channels_running} running" if reachable else f"{len(channel_order)} configured")
    if deep:
        overview.add_row("Probes", "[green]enabled[/green]")
    else:
        overview.add_row("Probes", "[dim]skipped (use --deep)[/dim]")

    console.print(overview)
    console.print()

    # Channels table
    if channel_order or channels_summary:
        ch_table = Table(title="Channels", show_header=True, header_style="bold")
        ch_table.add_column("Channel", style="cyan")
        ch_table.add_column("State")
        ch_table.add_column("Details", style="dim")

        for cid in channel_order:
            cdata = channels_summary.get(cid, {})
            label_str = channel_labels.get(cid, cid.title())
            if isinstance(cdata, dict):
                state_label, style = _channel_state_style(
                    cdata.get("state", ""),
                    cdata.get("running", False),
                    cdata.get("connected", False),
                )
                details_parts = []
                if cdata.get("configured"):
                    details_parts.append("configured")
                if cdata.get("running"):
                    details_parts.append("running")
                if cdata.get("connected"):
                    details_parts.append("connected")
                details = " · ".join(details_parts) or "—"
            else:
                state_label, style = "—", "dim"
                details = "—"

            ch_table.add_row(label_str, f"[{style}]{state_label}[/{style}]", details)

        console.print(ch_table)
        console.print()

    # Sessions table (brief)
    raw_sessions_list: list[dict[str, Any]] = []
    if isinstance(sessions_data, dict):
        raw = sessions_data.get("sessions", sessions_data)
        if isinstance(raw, list):
            raw_sessions_list = raw
    elif isinstance(sessions_data, list):
        raw_sessions_list = sessions_data

    if raw_sessions_list:
        sess_table = Table(title=f"Sessions ({len(raw_sessions_list)})", show_header=True, header_style="bold")
        sess_table.add_column("Key", style="cyan")
        sess_table.add_column("Kind", style="dim")
        sess_table.add_column("Age")
        sess_table.add_column("Model", style="dim")

        for s in raw_sessions_list[:10]:
            if not isinstance(s, dict):
                continue
            key = s.get("sessionKey") or s.get("key") or s.get("id", "—")
            kind = s.get("chatType") or s.get("kind") or ("DM" if "direct" in str(key) else "group")
            raw_age = s.get("age") or s.get("lastActiveRelative") or s.get("updatedAt")
            # updatedAt is Unix-ms when it's a large integer
            if isinstance(raw_age, (int, float)) and raw_age > 1_000_000_000_000:
                age = _format_age_ms(raw_age)
            else:
                age = str(raw_age) if raw_age else "—"
            model = s.get("model") or s.get("agentModel") or "—"
            # Trim long key for display; show channel:kind suffix
            key_str = str(key)
            key_display = key_str[-42:] if len(key_str) > 42 else key_str
            sess_table.add_row(key_display, str(kind), age, str(model))

        console.print(sess_table)
        console.print()

    if not reachable:
        console.print("[yellow]⚠[/yellow]  Gateway offline — start with: [cyan]openclaw gateway run[/cyan]")


# ---------------------------------------------------------------------------
# health subcommand
# ---------------------------------------------------------------------------

@status_app.command("health")
def health(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    timeout: int = typer.Option(10_000, "--timeout", help="Timeout in ms"),
):
    """Fetch health from the running gateway"""
    t0 = time.monotonic()
    result = _call_gw("health", {}, timeout_ms=min(timeout, 10_000))
    latency_ms = int((time.monotonic() - t0) * 1000)

    if result is None:
        if json_output:
            console.print(json.dumps({"ok": False, "error": "gateway unreachable"}))
        else:
            console.print("[red]✗[/red] Gateway is not running or unreachable.")
            console.print("  Start with: [cyan]openclaw gateway run[/cyan]")
        raise typer.Exit(1)

    if json_output:
        result["latencyMs"] = latency_ms
        console.print(json.dumps(result, indent=2))
        return

    gw = result.get("gateway") or {}
    uptime_sec = int(gw.get("uptimeSec", -1))
    connections = int(gw.get("connections", 0))
    channels = result.get("channels") or {}
    agents = result.get("agents") or {}
    sessions = result.get("sessions") or {}

    console.print(f"[green]✓[/green] Gateway [bold]healthy[/bold] · {latency_ms}ms")
    console.print()

    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_column("Key", style="cyan")
    tbl.add_column("Value")

    if uptime_sec >= 0:
        tbl.add_row("Uptime", _format_uptime(uptime_sec))
    tbl.add_row("Connections", str(connections))
    tbl.add_row("Channels active", str(channels.get("count", len(channels.get("active", [])))))
    tbl.add_row("Agents", str(agents.get("count", 0)))
    tbl.add_row("Sessions", str(sessions.get("count", 0)))
    tbl.add_row("Latency", f"{latency_ms}ms")

    console.print(tbl)


# ---------------------------------------------------------------------------
# sessions subcommand
# ---------------------------------------------------------------------------

@status_app.command("sessions")
def sessions(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    store: str = typer.Option(None, "--store", help="Session store path"),
    active: int = typer.Option(None, "--active", help="Only show sessions active in last N minutes"),
    limit: int = typer.Option(50, "--limit", help="Max sessions to show"),
):
    """List stored conversation sessions"""
    # Try gateway first for live session data
    result = _call_gw("sessions.list", {"limit": limit}, timeout_ms=5_000)

    raw_sessions: list[dict[str, Any]] = []
    if result is not None:
        if isinstance(result, dict):
            raw = result.get("sessions", result)
            raw_sessions = raw if isinstance(raw, list) else []
        elif isinstance(result, list):
            raw_sessions = result

    # Fallback: read from disk
    if not raw_sessions:
        sessions_dir = Path(store) if store else (resolve_state_dir() / "sessions")
        if sessions_dir.exists():
            for f in sorted(sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
                try:
                    data = json.loads(f.read_text())
                    raw_sessions.append(data)
                except Exception:
                    pass

    if json_output:
        console.print(json.dumps({"sessions": raw_sessions, "count": len(raw_sessions)}, indent=2))
        return

    if not raw_sessions:
        console.print("[yellow]No sessions found[/yellow]")
        return

    tbl = Table(title=f"Sessions ({len(raw_sessions)})", show_header=True, header_style="bold")
    tbl.add_column("Key", style="cyan")
    tbl.add_column("Kind", style="dim")
    tbl.add_column("Age")
    tbl.add_column("Model", style="dim")
    tbl.add_column("Tokens", style="dim")

    for s in raw_sessions:
        if not isinstance(s, dict):
            continue
        key = s.get("sessionKey") or s.get("key") or s.get("id", "—")
        kind = s.get("chatType") or s.get("kind") or ("DM" if "direct" in str(key) else "group" if "group" in str(key) else "—")
        raw_age = s.get("age") or s.get("lastActiveRelative") or s.get("updatedAt")
        if isinstance(raw_age, (int, float)) and raw_age > 1_000_000_000_000:
            age = _format_age_ms(raw_age)
        else:
            age = str(raw_age) if raw_age else "—"
        model = s.get("model") or s.get("agentModel") or "—"
        tokens = str(s.get("tokens") or s.get("totalTokens") or "—")
        key_str = str(key)
        tbl.add_row(key_str[-45:] if len(key_str) > 45 else key_str, str(kind), age, str(model), tokens)

    console.print(tbl)


# ---------------------------------------------------------------------------
# --all verbose diagnosis renderer
# ---------------------------------------------------------------------------

def _render_all(
    config: Any,
    reachable: bool,
    latency_ms: int,
    health_data: dict[str, Any],
    channels_data: dict[str, Any],
    sessions_data: dict[str, Any],
    agents_data: dict[str, Any],
    timeout: int,
) -> None:
    from ..config.paths import resolve_config_path

    port = config.gateway.port if config.gateway else 18789
    agent_model = (config.agent.model if config.agent and hasattr(config.agent, "model") else None) or "default"

    # ---- Config path ----
    try:
        cfg_path = str(resolve_config_path())
    except Exception:
        cfg_path = "~/.openclaw/openclaw.json"

    # ---- Overview table ----
    overview = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    overview.add_column("Item", style="cyan", no_wrap=True)
    overview.add_column("Value")

    overview.add_row("Config", cfg_path)

    if reachable:
        gw_value = f"[green]running[/green] · port {port} · {latency_ms}ms"
        gw_uptime = int((health_data.get("gateway") or {}).get("uptimeSec", -1))
        if gw_uptime >= 0:
            gw_value += f" · up {_format_uptime(gw_uptime)}"
    else:
        gw_value = f"[red]offline[/red] · port {port}"
    overview.add_row("Gateway", gw_value)
    overview.add_row("Agent", agent_model)

    gw_connections = int((health_data.get("gateway") or {}).get("connections", 0))
    if gw_connections:
        overview.add_row("Connections", str(gw_connections))

    agents_list = (agents_data.get("agents") or []) if isinstance(agents_data, dict) else []
    overview.add_row("Agents", f"{len(agents_list)} configured")

    sessions_count = int((health_data.get("sessions") or {}).get("count", 0))
    overview.add_row("Sessions", str(sessions_count))

    console.rule("[bold]OpenClaw Status (full)[/bold]")
    console.print(overview)
    console.print()

    # ---- Channels detail ----
    channels_summary = channels_data.get("channels", {}) if isinstance(channels_data, dict) else {}
    channel_order = channels_data.get("channelOrder", list(channels_summary.keys())) if isinstance(channels_data, dict) else []
    channel_labels = channels_data.get("channelLabels", {}) if isinstance(channels_data, dict) else {}
    channel_accounts = channels_data.get("channelAccounts", {}) if isinstance(channels_data, dict) else {}

    if channel_order:
        ch_table = Table(title="Channels (detail)", show_header=True, header_style="bold")
        ch_table.add_column("Channel", style="cyan")
        ch_table.add_column("Enabled")
        ch_table.add_column("State")
        ch_table.add_column("Details", style="dim")

        for cid in channel_order:
            cdata = channels_summary.get(cid, {})
            label_str = channel_labels.get(cid, cid.title())
            accts = channel_accounts.get(cid, [])
            enabled_str = "[green]yes[/green]" if (isinstance(cdata, dict) and cdata.get("configured")) else "[dim]no[/dim]"

            if isinstance(cdata, dict):
                state_label, style = _channel_state_style(
                    cdata.get("state", ""),
                    cdata.get("running", False),
                    cdata.get("connected", False),
                )
                # Show healthy status from account data
                healthy = all(a.get("healthy", False) for a in accts) if accts else cdata.get("connected", False)
                detail_parts = []
                if cdata.get("running"):
                    detail_parts.append("running")
                if healthy:
                    detail_parts.append("healthy")
                details = " · ".join(detail_parts) or "—"
            else:
                state_label, style, details = "—", "dim", "—"

            ch_table.add_row(label_str, enabled_str, f"[{style}]{state_label}[/{style}]", details)

        console.print(ch_table)
        console.print()

    # ---- Agents list ----
    if agents_list:
        ag_table = Table(title="Agents", show_header=True, header_style="bold")
        ag_table.add_column("Agent", style="cyan")
        ag_table.add_column("Model", style="dim")
        ag_table.add_column("Sessions", style="dim")

        for ag in agents_list:
            if not isinstance(ag, dict):
                continue
            ag_table.add_row(
                ag.get("id", "—"),
                ag.get("model") or agent_model,
                str(ag.get("sessions", "—")),
            )
        console.print(ag_table)
        console.print()

    # ---- Log tail ----
    if reachable:
        log_result = _call_gw("logs.tail", {"limit": 20}, timeout_ms=5_000)
        lines: list[str] = []
        if isinstance(log_result, dict):
            lines = log_result.get("lines", [])
        elif isinstance(log_result, list):
            lines = log_result

        if lines:
            console.rule("[dim]Gateway log (last 20 lines)[/dim]")
            for line in lines:
                console.print(f"[dim]{line}[/dim]")
            console.print()


# ---------------------------------------------------------------------------
# Default callback — invoke `status` when no subcommand given
# ---------------------------------------------------------------------------

@status_app.callback(invoke_without_command=True)
def status_default(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    all_info: bool = typer.Option(False, "--all", help="Full diagnosis (config + logs)"),
    deep: bool = typer.Option(False, "--deep", help="Probe channels"),
    usage: bool = typer.Option(False, "--usage", help="Show usage/quota snapshots"),
    timeout: int = typer.Option(10_000, "--timeout", help="Timeout in ms"),
):
    """Show gateway health, channel states, and recent sessions"""
    if ctx.invoked_subcommand is None:
        status(
            json_output=json_output,
            all_info=all_info,
            deep=deep,
            usage=usage,
            timeout=timeout,
        )
