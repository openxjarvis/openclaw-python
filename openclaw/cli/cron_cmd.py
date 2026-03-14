"""Cron management commands — mirrors TS src/cli/cron-cli.ts"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from openclaw.config.paths import STATE_DIR as _STATE_DIR

console = Console()
cron_app = typer.Typer(help="Scheduled tasks (cron)", no_args_is_help=False)

_CRON_STORE = Path(_STATE_DIR) / "cron" / "jobs.json"
_CRON_HISTORY = Path(_STATE_DIR) / "cron" / "history.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_raw_config() -> dict:
    from ..config.loader import load_config_raw
    from ..config.paths import resolve_config_path
    try:
        cfg_path = resolve_config_path()
        if cfg_path and Path(cfg_path).exists():
            return load_config_raw(Path(cfg_path)) or {}
    except Exception:
        pass
    default = Path(_STATE_DIR) / "openclaw.json"
    if default.exists():
        try:
            return load_config_raw(default) or {}
        except Exception:
            pass
    return {}


def _save_raw(raw: dict):
    from ..config.loader import write_config_file
    write_config_file(raw)


def _get_cron_jobs(raw: dict) -> dict:
    """Return cron jobs dict from config."""
    cron_section = raw.get("cron") or {}
    if isinstance(cron_section, dict):
        return cron_section.get("jobs") or {}
    return {}


def _load_jobs_from_store() -> list:
    """Read jobs directly from ~/.openclaw/cron/jobs.json (the authoritative store)."""
    store = _CRON_STORE
    if not store.exists():
        return []
    try:
        data = json.loads(store.read_text())
        jobs = data.get("jobs", data) if isinstance(data, dict) else data
        if isinstance(jobs, list):
            return jobs
        if isinstance(jobs, dict):
            return list(jobs.values())
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Default callback
# ---------------------------------------------------------------------------

@cron_app.callback(invoke_without_command=True)
def cron_default(ctx: typer.Context):
    """List cron jobs (default action)."""
    if ctx.invoked_subcommand is None:
        list_crons(json_output=False)


# ---------------------------------------------------------------------------
# cron list
# ---------------------------------------------------------------------------

@cron_app.command("list")
def list_crons(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
):
    """List configured cron jobs"""
    # First try gateway RPC (if running)
    try:
        from .gateway_rpc_cli import GatewayRpcOpts, call_gateway_from_cli
        opts = GatewayRpcOpts(url=url, token=token, timeout=5_000, json_output=json_output)
        result = call_gateway_from_cli("cron.list", opts, {}, show_progress=False)
        jobs = result.get("jobs", result) if isinstance(result, dict) else result or []

        if json_output:
            console.print(json.dumps(jobs, indent=2, ensure_ascii=False))
            return

        _print_jobs_table(jobs if isinstance(jobs, list) else list(jobs.values()) if isinstance(jobs, dict) else [])
        return
    except Exception:
        pass

    # Fallback: read directly from the cron store JSON file, then from config
    jobs_list = _load_jobs_from_store()

    if not jobs_list:
        # Also try loading from config-defined jobs (mirrors TS cron-cli fallback)
        raw = _load_raw_config()
        config_jobs = _get_cron_jobs(raw)
        jobs_list = [
            {"id": jid, "name": jid, **jcfg}
            for jid, jcfg in config_jobs.items()
            if isinstance(jcfg, dict)
        ]

    if json_output:
        # Return a dict keyed by job id (mirrors TS shape)
        jobs_dict = {j.get("id", j.get("name", "")): j for j in jobs_list} if jobs_list else {}
        console.print(json.dumps(jobs_dict, indent=2, ensure_ascii=False))
        return

    if not jobs_list:
        console.print("[yellow]No cron jobs found[/yellow]")
        return

    _print_jobs_table(jobs_list)


def _print_jobs_table(jobs: list):
    if not jobs:
        console.print("[yellow]No cron jobs found[/yellow]")
        return

    table = Table(title=f"Cron Jobs ({len(jobs)})")
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Name", style="cyan")
    table.add_column("Schedule", style="green")
    table.add_column("Enabled", style="yellow")
    table.add_column("Last status", style="dim")

    for job in jobs:
        if not isinstance(job, dict):
            table.add_row("", str(job), "", "[green]✓[/green]", "")
            continue

        enabled = job.get("enabled", True) and not job.get("disabled", False)
        enabled_icon = "[green]✓[/green]" if enabled else "[red]✗[/red]"

        sched = job.get("schedule") or {}
        if isinstance(sched, dict):
            kind = sched.get("type") or sched.get("kind", "")
            if kind == "at":
                sched_str = f"at {sched.get('at', '')}"
            elif kind == "every":
                ms = sched.get("every_ms", 0)
                sched_str = f"every {ms // 60000}m" if ms >= 60000 else f"every {ms}ms"
            elif kind == "cron":
                sched_str = sched.get("expr") or sched.get("expression", "")
            else:
                sched_str = str(sched)
        else:
            sched_str = str(sched)

        state = job.get("state") or {}
        last = state.get("last_status") or ""
        if last == "error":
            last = f"[red]{last}[/red]"
        elif last == "ok":
            last = f"[green]{last}[/green]"
        elif last == "skipped":
            last = f"[yellow]{last}[/yellow]"

        job_id = str(job.get("id", ""))[:8]
        table.add_row(job_id, job.get("name", ""), sched_str, enabled_icon, last)

    console.print(table)


# ---------------------------------------------------------------------------
# cron enable / disable
# ---------------------------------------------------------------------------

@cron_app.command("enable")
def enable(
    name: str = typer.Argument(..., help="Cron job name"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Enable a cron job (clears its disabled flag)"""
    raw = _load_raw_config()
    jobs = _get_cron_jobs(raw)

    if name not in jobs:
        console.print(f"[red]Cron job not found:[/red] {name}")
        console.print(f"\nAvailable jobs: {', '.join(jobs.keys()) or '(none)'}")
        raise typer.Exit(1)

    job = jobs[name]
    if isinstance(job, dict):
        job.pop("disabled", None)
    else:
        job = {"schedule": str(job)}

    raw.setdefault("cron", {}).setdefault("jobs", {})[name] = job

    try:
        _save_raw(raw)
        if json_output:
            console.print(json.dumps({"enabled": name}))
        else:
            console.print(f"[green]✓[/green] Cron job enabled: [cyan]{name}[/cyan]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@cron_app.command("disable")
def disable(
    name: str = typer.Argument(..., help="Cron job name"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Disable a cron job (sets disabled: true)"""
    raw = _load_raw_config()
    jobs = _get_cron_jobs(raw)

    if name not in jobs:
        console.print(f"[red]Cron job not found:[/red] {name}")
        console.print(f"\nAvailable jobs: {', '.join(jobs.keys()) or '(none)'}")
        raise typer.Exit(1)

    job = jobs[name]
    if isinstance(job, dict):
        job["disabled"] = True
    else:
        job = {"schedule": str(job), "disabled": True}

    raw.setdefault("cron", {}).setdefault("jobs", {})[name] = job

    try:
        _save_raw(raw)
        if json_output:
            console.print(json.dumps({"disabled": name}))
        else:
            console.print(f"[green]✓[/green] Cron job disabled: [cyan]{name}[/cyan]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# cron add
# ---------------------------------------------------------------------------

@cron_app.command("add")
def add(
    message: str = typer.Argument(..., help="Message / task payload for the cron job"),
    at: str = typer.Option(..., "--at", help="Schedule expression (cron or ISO 8601 datetime)"),
    keep_after_run: bool = typer.Option(False, "--keep-after-run", help="Keep the job after it runs once"),
    agent_id: Optional[str] = typer.Option(None, "--agent", "-a", help="Target agent ID"),
    label: Optional[str] = typer.Option(None, "--label", "-l", help="Human-readable label"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Schedule a new cron job"""
    import uuid
    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "label": label or message[:40],
        "schedule": at,
        "message": message,
        "agentId": agent_id,
        "deleteAfterRun": not keep_after_run,
        "enabled": True,
    }
    store = _CRON_STORE
    store.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = []
        if store.exists():
            data = json.loads(store.read_text())
            existing = data.get("jobs", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        existing.append(job)
        store.write_text(json.dumps({"jobs": existing}, indent=2))
        if json_output:
            print(json.dumps({"ok": True, "id": job_id, "job": job}))
        else:
            console.print(f"[green]✓[/green] Cron job added: [cyan]{job_id[:8]}[/cyan]  schedule={at}")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# cron edit
# ---------------------------------------------------------------------------

@cron_app.command("edit")
def edit(
    job_id: str = typer.Argument(..., help="Job ID to edit"),
    at: Optional[str] = typer.Option(None, "--at", help="New schedule"),
    message: Optional[str] = typer.Option(None, "--message", "-m", help="New message/task"),
    label: Optional[str] = typer.Option(None, "--label", "-l", help="New label"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Edit an existing cron job"""
    store = _CRON_STORE
    if not store.exists():
        console.print(f"[red]No cron jobs found[/red]")
        raise typer.Exit(1)
    try:
        data = json.loads(store.read_text())
        jobs = data.get("jobs", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        found = False
        for job in jobs:
            if isinstance(job, dict) and (job.get("id", "").startswith(job_id) or job.get("id") == job_id):
                if at:
                    job["schedule"] = at
                if message:
                    job["message"] = message
                if label:
                    job["label"] = label
                found = True
                break
        if not found:
            console.print(f"[red]Job not found:[/red] {job_id}")
            raise typer.Exit(1)
        store.write_text(json.dumps({"jobs": jobs}, indent=2))
        if json_output:
            print(json.dumps({"ok": True, "id": job_id}))
        else:
            console.print(f"[green]✓[/green] Cron job updated: {job_id[:8]}")
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# cron delete
# ---------------------------------------------------------------------------

@cron_app.command("delete")
def delete(
    job_id: str = typer.Argument(..., help="Job ID to delete"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Delete a cron job"""
    store = _CRON_STORE
    if not store.exists():
        console.print(f"[yellow]No cron jobs found[/yellow]")
        raise typer.Exit(1)
    try:
        data = json.loads(store.read_text())
        jobs = data.get("jobs", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        before = len(jobs)
        jobs = [j for j in jobs if not (isinstance(j, dict) and (j.get("id", "").startswith(job_id) or j.get("id") == job_id))]
        removed = before - len(jobs)
        store.write_text(json.dumps({"jobs": jobs}, indent=2))
        if json_output:
            print(json.dumps({"ok": True, "removed": removed}))
        else:
            if removed:
                console.print(f"[green]✓[/green] Removed {removed} job(s)")
            else:
                console.print(f"[yellow]Job not found:[/yellow] {job_id}")
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# cron run
# ---------------------------------------------------------------------------

@cron_app.command("run")
def run(
    job_id: str = typer.Argument(..., help="Job ID to trigger immediately"),
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Trigger a cron job immediately"""
    try:
        from .gateway_rpc_cli import GatewayRpcOpts, call_gateway_from_cli
        opts = GatewayRpcOpts(url=url, token=token, timeout=10_000, json_output=json_output)
        result = call_gateway_from_cli("cron.run", opts, {"id": job_id}, show_progress=False)
        if json_output:
            print(json.dumps(result, indent=2))
        else:
            console.print(f"[green]✓[/green] Cron job triggered: {job_id}")
    except Exception as e:
        console.print(f"[yellow]⚠[/yellow]  Could not trigger via gateway: {e}")
        console.print("  (Start the gateway with: openclaw gateway run)")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# cron runs
# ---------------------------------------------------------------------------

@cron_app.command("runs")
def runs(
    job_id: str = typer.Option(..., "--id", help="Job ID to show runs for"),
    limit: int = typer.Option(20, "--limit", help="Max number of runs to show"),
    url: Optional[str] = typer.Option(None, "--url", help="Gateway WebSocket URL"),
    token: Optional[str] = typer.Option(None, "--token", help="Gateway auth token"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show run history for a cron job"""
    try:
        from .gateway_rpc_cli import GatewayRpcOpts, call_gateway_from_cli
        opts = GatewayRpcOpts(url=url, token=token, timeout=10_000, json_output=json_output)
        result = call_gateway_from_cli("cron.runs", opts, {"id": job_id, "limit": limit}, show_progress=False)
        
        if json_output:
            print(json.dumps(result, indent=2))
        else:
            entries = result.get("entries", [])
            if not entries:
                console.print(f"[yellow]No run history found for job {job_id}[/yellow]")
                return
            
            console.print(f"\n[cyan]Run History for Job:[/cyan] {job_id}")
            console.print(f"[dim]Showing last {len(entries)} runs[/dim]\n")
            
            for entry in entries:
                ts = entry.get("ts", "")
                status = entry.get("status", "unknown")
                action = entry.get("action", "")
                
                # Format status with color
                if status == "ok":
                    status_str = "[green]✓ ok[/green]"
                elif status == "skipped":
                    status_str = "[yellow]⊘ skipped[/yellow]"
                elif status == "error":
                    status_str = "[red]✗ error[/red]"
                else:
                    status_str = f"[dim]{status}[/dim]"
                
                console.print(f"  {status_str}  {action}  [dim]{ts}[/dim]")
                
                # Show error message if present
                if entry.get("error"):
                    console.print(f"    [red]Error:[/red] {entry['error']}")
                
                # Show reason for skips
                if entry.get("reason"):
                    console.print(f"    [dim]Reason:[/dim] {entry['reason']}")
            
            console.print("")
    except Exception as e:
        console.print(f"[red]Error:[/red] Could not retrieve runs: {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# cron status
# ---------------------------------------------------------------------------

@cron_app.command("status")
def status(
    job_id: Optional[str] = typer.Argument(None, help="Job ID (omit for all)"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show execution history/status of cron jobs"""
    log_file = _CRON_HISTORY
    if not log_file.exists():
        if json_output:
            print(json.dumps([]))
        else:
            console.print("[yellow]No cron execution history found[/yellow]")
        return
    try:
        entries = json.loads(log_file.read_text())
        if isinstance(entries, dict):
            entries = entries.get("history", [])
        if job_id:
            entries = [e for e in entries if isinstance(e, dict) and (e.get("jobId", "").startswith(job_id) or e.get("id", "").startswith(job_id))]
        if json_output:
            print(json.dumps(entries, indent=2))
        else:
            if not entries:
                console.print("[yellow]No history found[/yellow]")
                return
            t = Table(title="Cron History")
            t.add_column("Job ID", style="dim")
            t.add_column("Status")
            t.add_column("Ran At")
            for e in entries[-20:]:
                if not isinstance(e, dict):
                    continue
                st = e.get("status", "?")
                color = "green" if st == "ok" else "red" if st == "error" else "yellow"
                t.add_row(str(e.get("jobId", e.get("id", "")))[:8], f"[{color}]{st}[/{color}]", str(e.get("ranAt", e.get("ts", ""))))
            console.print(t)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
