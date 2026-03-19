"""Pairing management commands"""

import typer
from rich.console import Console
from rich.table import Table
from ..config.paths import resolve_state_dir

console = Console()
pairing_app = typer.Typer(help="Channel pairing management")

# Mirrors TS PAIRING_APPROVED_MESSAGE from src/channels/plugins/pairing-message.ts
PAIRING_APPROVED_MESSAGE = "✅ OpenClaw access approved. Send a message to start chatting."


def _notify_pairing_approved(channel: str, sender_id: str, account_id: str | None = None) -> None:
    """Send an approval notification to the user via the channel's bot API.

    Mirrors TS notifyPairingApproved() → adapter.notifyApproval().
    Supports: telegram, feishu.
    """
    if channel == "telegram":
        _telegram_notify_approved(sender_id, account_id)
    elif channel in ("feishu", "lark"):
        _feishu_notify_approved(sender_id, account_id)
    else:
        raise NotImplementedError(f"Notification not yet implemented for channel: {channel}")


def _telegram_notify_approved(chat_id: str, account_id: str | None = None) -> None:
    """Send PAIRING_APPROVED_MESSAGE to a Telegram user via the Bot API."""
    import urllib.request
    import urllib.parse
    import json as _json

    # Resolve bot token from openclaw config
    bot_token: str | None = None
    try:
        from ..config.loader import load_config
        cfg = load_config()
        tg_cfg = None
        if cfg:
            channels = getattr(cfg, "channels", None)
            if channels:
                tg_cfg = getattr(channels, "telegram", None)
        if tg_cfg:
            bot_token = getattr(tg_cfg, "botToken", None) or getattr(tg_cfg, "bot_token", None)
    except Exception:
        pass

    # Fallback: read directly from openclaw.json
    if not bot_token:
        import json as _j
        from pathlib import Path
        cfg_path = resolve_state_dir() / "openclaw.json"
        if cfg_path.exists():
            raw = _j.loads(cfg_path.read_text())
            tg_raw = raw.get("channels", {}).get("telegram", {})
            bot_token = tg_raw.get("botToken") or tg_raw.get("bot_token")

    if not bot_token:
        raise RuntimeError("Telegram bot token not found in config")

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = _json.dumps({
        "chat_id": chat_id,
        "text": PAIRING_APPROVED_MESSAGE,
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = _json.loads(resp.read())
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")


def _feishu_notify_approved(open_id: str, account_id: str | None = None) -> None:
    """Send PAIRING_APPROVED_MESSAGE to a Feishu user via the tenant access token REST API.

    This runs in the CLI (sync) context, so we use urllib / requests directly.
    Mirrors TS feishuPlugin.pairing.notifyApproval().
    """
    import json as _json
    import urllib.request
    from pathlib import Path

    # Load Feishu credentials from openclaw.json
    cfg_path = resolve_state_dir() / "openclaw.json"
    if not cfg_path.exists():
        raise RuntimeError("openclaw.json not found — cannot send Feishu notification")

    raw = _json.loads(cfg_path.read_text())
    feishu_raw: dict = raw.get("channels", {}).get("feishu", {})

    # Resolve account-specific or top-level credentials
    accounts: dict = feishu_raw.get("accounts", {})
    acct_cfg: dict = {}
    if account_id and account_id in accounts:
        acct_cfg = accounts[account_id]
    elif accounts:
        # Use first account as default
        acct_cfg = next(iter(accounts.values()), {})

    app_id: str = acct_cfg.get("appId") or feishu_raw.get("appId") or ""
    app_secret: str = acct_cfg.get("appSecret") or feishu_raw.get("appSecret") or ""
    domain_raw: str = acct_cfg.get("domain") or feishu_raw.get("domain") or "feishu"

    if not app_id or not app_secret:
        raise RuntimeError("Feishu appId/appSecret not found in config")

    # Resolve API base URL
    if domain_raw == "lark":
        api_base = "https://open.larksuite.com/open-apis"
    elif domain_raw.startswith("http"):
        api_base = domain_raw.rstrip("/") + "/open-apis"
    else:
        api_base = "https://open.feishu.cn/open-apis"

    # Step 1: get tenant access token
    token_url = f"{api_base}/auth/v3/tenant_access_token/internal"
    token_payload = _json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    token_req = urllib.request.Request(
        token_url, data=token_payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(token_req, timeout=10) as resp:
        token_data = _json.loads(resp.read())
    if token_data.get("code") != 0:
        raise RuntimeError(f"Feishu token error: {token_data.get('msg')}")
    tenant_token: str = token_data["tenant_access_token"]

    # Step 2: send IM message to the user (open_id)
    msg_url = f"{api_base}/im/v1/messages?receive_id_type=open_id"
    msg_payload = _json.dumps({
        "receive_id": open_id,
        "msg_type": "text",
        "content": _json.dumps({"text": PAIRING_APPROVED_MESSAGE}),
    }).encode()
    msg_req = urllib.request.Request(
        msg_url,
        data=msg_payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {tenant_token}",
        },
    )
    with urllib.request.urlopen(msg_req, timeout=10) as resp:
        msg_data = _json.loads(resp.read())
    if msg_data.get("code") != 0:
        raise RuntimeError(f"Feishu IM error: code={msg_data.get('code')} msg={msg_data.get('msg')}")


@pairing_app.command("list")
def list_pairing_requests(
    channel: str = typer.Argument(..., help="Channel name (telegram, discord, etc)"),
    account: str = typer.Option("", "--account", help="Account ID (for multi-account channels)"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List pending pairing requests"""
    try:
        from ..pairing.pairing_store import list_channel_pairing_requests

        requests = list_channel_pairing_requests(channel, account_id=account or None)

        if not requests:
            account_suffix = f" (account: {account})" if account else ""
            console.print(f"[yellow]No pending pairing requests for {channel}{account_suffix}[/yellow]")
            return

        if json_output:
            import json
            reqs_data = [
                {
                    "code": r.code,
                    "id": r.id,
                    "created_at": r.created_at,
                    "meta": r.meta or {},
                }
                for r in requests
            ]
            console.print(json.dumps({"channel": channel, "account": account, "requests": reqs_data}, indent=2))
            return

        title_suffix = f" (account: {account})" if account else ""
        table = Table(title=f"Pending Pairing Requests - {channel}{title_suffix}")
        table.add_column("Code", style="cyan", no_wrap=True)
        table.add_column("Sender ID", style="green")
        table.add_column("Username", style="yellow")
        table.add_column("Name", style="white")
        table.add_column("Created", style="blue")

        for req in requests:
            meta = req.meta or {}
            username = meta.get("username", "-")
            full_name = meta.get("full_name", "-")

            table.add_row(
                req.code,
                req.id,
                f"@{username}" if username and username != "-" else "-",
                full_name,
                (req.created_at or "")[:10]
            )

        console.print(table)

        approve_cmd = f"uv run openclaw pairing approve {channel}"
        if account:
            approve_cmd += f" --account {account}"
        approve_cmd += " <code>"
        console.print(f"\n[dim]Approve with: {approve_cmd}[/dim]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@pairing_app.command("approve")
def approve_pairing_request(
    channel: str = typer.Argument(..., help="Channel name (telegram, discord, etc)"),
    code: str = typer.Argument(..., help="Pairing code to approve"),
    account: str = typer.Option("", "--account", help="Account ID (for multi-account channels)"),
    notify: bool = typer.Option(True, "--notify/--no-notify", help="Notify the requester on the same channel"),
):
    """Approve a pairing request"""
    try:
        from ..pairing.pairing_store import approve_channel_pairing_code

        console.print(f"[cyan]Approving pairing request...[/cyan]")
        console.print(f"  Channel: {channel}")
        console.print(f"  Code: {code}")
        if account:
            console.print(f"  Account: {account}")

        result = approve_channel_pairing_code(channel, code, account_id=account or None)

        if result:
            sender_id = result["id"]
            entry_data = result.get("entry", {})

            console.print(f"\n[green]✓[/green] Pairing request approved!")
            console.print(f"  Sender ID: {sender_id}")

            if isinstance(entry_data, dict):
                meta = entry_data.get("meta", {})
                if meta:
                    console.print(f"  Username: {meta.get('username', 'N/A')}")
                    console.print(f"  Name: {meta.get('full_name', 'N/A')}")

            console.print(f"\n[dim]Sender has been added to the allowFrom list.[/dim]")
            console.print(f"[dim]They can now send direct messages.[/dim]")

            if notify:
                try:
                    console.print(f"\n[cyan]Notifying requester...[/cyan]")
                    _notify_pairing_approved(channel, sender_id, account or None)
                    console.print(f"[green]✓[/green] Notification sent")
                except Exception as notify_err:
                    console.print(f"[yellow]Failed to notify requester: {notify_err}[/yellow]")
        else:
            console.print(f"[red]✗[/red] Pairing code not found or expired")
            list_cmd = f"uv run openclaw pairing list {channel}"
            if account:
                list_cmd += f" --account {account}"
            console.print(f"\n[yellow]Use '{list_cmd}' to see pending requests[/yellow]")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        import traceback
        traceback.print_exc()
        raise typer.Exit(1)


@pairing_app.command("deny")
def deny_pairing_request(
    channel: str = typer.Argument(..., help="Channel name"),
    code: str = typer.Argument(..., help="Pairing code to deny"),
):
    """Deny a pairing request"""
    try:
        from ..pairing.pairing_store import (
            list_channel_pairing_requests,
            _resolve_pairing_path,
            _write_pairing_requests,
        )

        path = _resolve_pairing_path(channel)
        requests = list_channel_pairing_requests(channel)

        found = False
        remaining = []
        for req in requests:
            if req.code == code:
                found = True
                console.print(f"[yellow]Denied pairing request:[/yellow]")
                console.print(f"  Code: {code}")
                console.print(f"  Sender: {req.id}")
            else:
                remaining.append(req)

        if found:
            _write_pairing_requests(path, remaining)
            console.print(f"\n[green]✓[/green] Request removed")
        else:
            console.print(f"[red]✗[/red] Pairing code not found")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@pairing_app.command("clear")
def clear_pairing_requests(
    channel: str = typer.Argument(..., help="Channel name"),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Clear all pending pairing requests"""
    try:
        from ..pairing.pairing_store import (
            list_channel_pairing_requests,
            _resolve_pairing_path,
            _write_pairing_requests,
        )

        requests = list_channel_pairing_requests(channel)

        if not requests:
            console.print(f"[yellow]No pending requests for {channel}[/yellow]")
            return

        if not confirm:
            response = input(f"\n⚠️  Clear {len(requests)} pending request(s)? [y/N]: ").strip().lower()
            if response != "y":
                console.print("Cancelled")
                return

        path = _resolve_pairing_path(channel)
        _write_pairing_requests(path, [])

        console.print(f"[green]✓[/green] Cleared {len(requests)} pairing request(s)")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@pairing_app.command("allowlist")
def show_allowlist(
    channel: str = typer.Argument(..., help="Channel name"),
):
    """Show allowFrom list for a channel"""
    try:
        from ..pairing.pairing_store import read_channel_allow_from_store
        from ..config.loader import load_config

        config = load_config()
        config_entries = []

        if channel == "telegram" and config.channels and config.channels.telegram:
            config_entries = config.channels.telegram.allowFrom or []

        all_entries = read_channel_allow_from_store(channel, config_entries)

        if not all_entries:
            console.print(f"[yellow]No entries in allowFrom list for {channel}[/yellow]")
            console.print(f"\n💡 [dim]Users with pairing mode will need approval[/dim]")
            return

        table = Table(title=f"AllowFrom List - {channel}")
        table.add_column("Entry", style="cyan")
        table.add_column("Source", style="yellow")

        for entry in all_entries:
            source = "config" if entry in config_entries else "pairing"
            table.add_row(entry, source)

        console.print(table)
        console.print(f"\n[dim]Total: {len(all_entries)} allowed sender(s)[/dim]")

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
