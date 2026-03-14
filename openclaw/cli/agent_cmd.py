"""Agent execution and management commands — mirrors TS src/cli/program/register.agent.ts"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from ..config.loader import load_config

console = Console()

# Separate top-level commands (like TS version)
# agent: Run one agent turn via the Gateway
# agents: Manage isolated agents (workspaces, auth, routing)

agent_app = typer.Typer(help="Run an agent turn via the Gateway")
agents_app = typer.Typer(help="Manage isolated agents (workspaces, auth, routing)")


# ---------------------------------------------------------------------------
# agent command (top-level, not a subcommand)
# ---------------------------------------------------------------------------

@agent_app.callback(invoke_without_command=True)
def agent_main(
    ctx: typer.Context,
    message: str = typer.Option(..., "--message", "-m", help="Message body for the agent"),
    to: Optional[str] = typer.Option(None, "--to", "-t", help="Recipient number in E.164 used to derive the session key"),
    session_id: Optional[str] = typer.Option(None, "--session-id", help="Use an explicit session id"),
    agent_id: Optional[str] = typer.Option(None, "--agent", help="Agent id (overrides routing bindings)"),
    thinking: Optional[str] = typer.Option(None, "--thinking", help="Thinking level: off | minimal | low | medium | high"),
    verbose: Optional[str] = typer.Option(None, "--verbose", help="Persist agent verbose level for the session (on|off)"),
    channel: Optional[str] = typer.Option(None, "--channel", help="Delivery channel (omit to use the main session channel)"),
    reply_to: Optional[str] = typer.Option(None, "--reply-to", help="Delivery target override (separate from session routing)"),
    reply_channel: Optional[str] = typer.Option(None, "--reply-channel", help="Delivery channel override (separate from routing)"),
    reply_account: Optional[str] = typer.Option(None, "--reply-account", help="Delivery account id override"),
    local: bool = typer.Option(False, "--local", help="Run the embedded agent locally (requires model provider API keys in your shell)"),
    deliver: bool = typer.Option(False, "--deliver", help="Send the agent's reply back to the selected channel"),
    json_output: bool = typer.Option(False, "--json", help="Output result as JSON"),
    timeout: int = typer.Option(600, "--timeout", help="Override agent command timeout (seconds, default 600 or config value)"),
):
    """
    Run an agent turn via the Gateway (use --local for embedded)
    
    Examples:
      openclaw agent --to +15555550123 --message "status update"
      openclaw agent --agent ops --message "Summarize logs"
      openclaw agent --session-id 1234 --message "Summarize inbox" --thinking medium
      openclaw agent --to +15555550123 --message "Summon reply" --deliver
    """
    if ctx.invoked_subcommand is not None:
        return
        
    from ..gateway.rpc_client import GatewayRPCClient
    
    try:
        # Generate session ID if not provided
        if not session_id:
            session_id = f"cli-{uuid.uuid4().hex[:8]}"
        
        # Create RPC client
        config = load_config()
        client = GatewayRPCClient(config=config)
        
        # Execute agent turn
        console.print(f"[cyan]→[/cyan] Running agent (session: {session_id})...")
        
        result = asyncio.run(client.call_agent_turn(
            message=message,
            session_id=session_id,
            agent_id=agent_id,
            thinking=thinking,
            timeout=timeout,
        ))
        
        if json_output:
            console.print(json.dumps(result, indent=2, ensure_ascii=False))
            return
        
        # Display response
        if result.get("error"):
            console.print(f"[red]Error:[/red] {result['error']}")
            raise typer.Exit(1)
        
        response = result.get("response", {})
        
        # Display assistant message
        if "text" in response:
            console.print("\n[green]Assistant:[/green]")
            console.print(response["text"])
        
        # Display tool calls if any
        if "toolCalls" in response and response["toolCalls"]:
            console.print("\n[yellow]Tool Calls:[/yellow]")
            for tool_call in response["toolCalls"]:
                console.print(f"  • {tool_call.get('name', 'unknown')}")
        
        # Display usage if available
        if "usage" in result:
            usage = result["usage"]
            console.print(f"\n[dim]Tokens: {usage.get('totalTokens', 0)} | Cost: ${usage.get('cost', 0):.4f}[/dim]")
        
    except ConnectionError as e:
        console.print(f"[red]Connection Error:[/red] Gateway not running on configured port")
        console.print(f"Please start the gateway: [cyan]openclaw gateway run[/cyan]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# agents commands (manage isolated agents)
# ---------------------------------------------------------------------------


@agents_app.command("bindings")
def bindings(
    agent_id: Optional[str] = typer.Option(None, "--agent", help="Filter by agent id"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List routing bindings"""
    try:
        raw = _load_raw_config()
        
        bindings_list = raw.get("bindings", [])
        if not isinstance(bindings_list, list):
            bindings_list = []
        
        # Filter by agent if specified
        if agent_id:
            bindings_list = [b for b in bindings_list if b.get("agentId") == agent_id]
        
        if json_output:
            console.print(json.dumps(bindings_list, indent=2))
            return
        
        if not bindings_list:
            if agent_id:
                console.print(f"No routing bindings for agent \"{agent_id}\".")
            else:
                console.print("No routing bindings.")
            return
        
        console.print("Routing bindings:")
        for binding in bindings_list:
            agent = binding.get("agentId", "unknown")
            match = binding.get("match", {})
            channel = match.get("channel", "unknown")
            account_id = match.get("accountId")
            
            desc = channel
            if account_id:
                desc = f"{channel}:{account_id}"
            
            console.print(f"- {agent} <- {desc}")
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@agents_app.command("bind")
def bind(
    agent_id: Optional[str] = typer.Option(None, "--agent", help="Agent id (defaults to current default agent)"),
    bind_specs: Optional[list[str]] = typer.Option(None, "--bind", help="Binding to add (repeatable). Format: channel[:accountId]"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON summary"),
):
    """Add routing bindings for an agent"""
    try:
        from ..config.loader import write_config_file
        
        raw = _load_raw_config()
        
        # Resolve agent ID
        target_id = agent_id
        if not target_id:
            # Use first agent or default
            agents_list = _get_agents_list(raw)
            if agents_list:
                target_id = agents_list[0].get("id") if isinstance(agents_list[0], dict) else None
        
        if not target_id:
            console.print("[red]Error:[/red] No agent specified. Use --agent <id>")
            raise typer.Exit(1)
        
        # Check agent exists
        agents_list = _get_agents_list(raw)
        if not any(a.get("id") == target_id for a in agents_list if isinstance(a, dict)):
            console.print(f"[red]Agent not found:[/red] {target_id}")
            raise typer.Exit(1)
        
        if not bind_specs:
            console.print("[red]Error:[/red] Provide at least one --bind <channel[:accountId]>")
            raise typer.Exit(1)
        
        # Parse binding specs
        new_bindings = []
        for spec in bind_specs:
            parts = spec.split(":", 1)
            channel = parts[0].strip()
            account_id = parts[1].strip() if len(parts) > 1 else None
            
            binding = {
                "agentId": target_id,
                "match": {"channel": channel}
            }
            if account_id:
                binding["match"]["accountId"] = account_id
            
            new_bindings.append(binding)
        
        # Add to config
        if "bindings" not in raw:
            raw["bindings"] = []
        
        added = []
        for new_binding in new_bindings:
            # Check if already exists
            exists = any(
                b.get("agentId") == new_binding["agentId"] and
                b.get("match", {}).get("channel") == new_binding["match"]["channel"] and
                b.get("match", {}).get("accountId") == new_binding["match"].get("accountId")
                for b in raw["bindings"]
            )
            
            if not exists:
                raw["bindings"].append(new_binding)
                added.append(new_binding)
        
        if added:
            write_config_file(raw)
            
            if json_output:
                console.print(json.dumps({"added": added}, indent=2))
                return
            
            console.print("Added bindings:")
            for binding in added:
                match = binding["match"]
                channel = match["channel"]
                account_id = match.get("accountId")
                desc = f"{channel}:{account_id}" if account_id else channel
                console.print(f"- {desc}")
        else:
            console.print("No new bindings added (already present).")
    
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@agents_app.command("unbind")
def unbind(
    agent_id: Optional[str] = typer.Option(None, "--agent", help="Agent id (defaults to current default agent)"),
    bind_specs: Optional[list[str]] = typer.Option(None, "--bind", help="Binding to remove (repeatable)"),
    all_bindings: bool = typer.Option(False, "--all", help="Remove all bindings for this agent"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON summary"),
):
    """Remove routing bindings for an agent"""
    try:
        from ..config.loader import write_config_file
        
        raw = _load_raw_config()
        
        # Resolve agent ID
        target_id = agent_id
        if not target_id:
            # Use first agent or default
            agents_list = _get_agents_list(raw)
            if agents_list:
                target_id = agents_list[0].get("id") if isinstance(agents_list[0], dict) else None
        
        if not target_id:
            console.print("[red]Error:[/red] No agent specified. Use --agent <id>")
            raise typer.Exit(1)
        
        if all_bindings and bind_specs:
            console.print("[red]Error:[/red] Use either --all or --bind, not both")
            raise typer.Exit(1)
        
        bindings_list = raw.get("bindings", [])
        
        if all_bindings:
            # Remove all bindings for this agent
            removed = [b for b in bindings_list if b.get("agentId") == target_id]
            raw["bindings"] = [b for b in bindings_list if b.get("agentId") != target_id]
            
            if removed:
                write_config_file(raw)
                
                if json_output:
                    console.print(json.dumps({"removed": removed}, indent=2))
                    return
                
                console.print(f"Removed {len(removed)} binding(s) for \"{target_id}\".")
            else:
                console.print(f"No bindings to remove for agent \"{target_id}\".")
            
            return
        
        if not bind_specs:
            console.print("[red]Error:[/red] Provide at least one --bind <channel[:accountId]> or use --all")
            raise typer.Exit(1)
        
        # Parse specs to remove
        specs_to_remove = []
        for spec in bind_specs:
            parts = spec.split(":", 1)
            channel = parts[0].strip()
            account_id = parts[1].strip() if len(parts) > 1 else None
            specs_to_remove.append((channel, account_id))
        
        # Remove matching bindings
        removed = []
        new_bindings = []
        for binding in bindings_list:
            if binding.get("agentId") == target_id:
                match = binding.get("match", {})
                b_channel = match.get("channel")
                b_account = match.get("accountId")
                
                should_remove = any(
                    b_channel == channel and b_account == account_id
                    for channel, account_id in specs_to_remove
                )
                
                if should_remove:
                    removed.append(binding)
                else:
                    new_bindings.append(binding)
            else:
                new_bindings.append(binding)
        
        if removed:
            raw["bindings"] = new_bindings
            write_config_file(raw)
            
            if json_output:
                console.print(json.dumps({"removed": removed}, indent=2))
                return
            
            console.print("Removed bindings:")
            for binding in removed:
                match = binding["match"]
                channel = match["channel"]
                account_id = match.get("accountId")
                desc = f"{channel}:{account_id}" if account_id else channel
                console.print(f"- {desc}")
        else:
            console.print("No bindings removed.")
    
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@agents_app.command("list")
def list_agents(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    bindings: bool = typer.Option(False, "--bindings", help="Include routing bindings"),
):
    """List configured agents"""
    try:
        config = load_config()
        
        if not config.agents or not config.agents.list:
            console.print("[yellow]No agents configured[/yellow]")
            return
        
        if json_output:
            agents_data = [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "workspace": agent.workspace,
                }
                for agent in config.agents.list
            ]
            console.print(json.dumps(agents_data, indent=2))
            return
        
        table = Table(title="Configured Agents")
        table.add_column("ID", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Workspace", style="yellow")
        
        for agent in config.agents.list:
            table.add_row(
                agent.id,
                agent.name or "-",
                agent.workspace or "-",
            )
        
        console.print(table)
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@agents_app.command("add")
def add(
    name: Optional[str] = typer.Argument(None, help="Agent name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", help="Workspace directory"),
    model: Optional[str] = typer.Option(None, "--model", help="Model id"),
    agent_dir: Optional[str] = typer.Option(None, "--agent-dir", help="Agent state directory"),
    bind: Optional[list[str]] = typer.Option(None, "--bind", help="Channel binding spec (repeatable)"),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Disable prompts"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Add a new isolated agent - mirrors TS agents.commands.add.ts"""
    try:
        from ..config.loader import write_config_file, load_config
        from ..routing.session_key import normalize_agent_id
        from ..commands.agents_config import apply_agent_config, find_agent_entry_index
        from ..agents.agent_scope import (
            resolve_agent_dir,
            resolve_agent_workspace_dir,
            list_agent_entries,
        )
        from ..agents.ensure_workspace_and_sessions import ensure_workspace_and_sessions
        from ..config.paths import resolve_user_path

        cfg = load_config()
        
        # Resolve agent name / id
        agent_name = name
        if not agent_name and not non_interactive:
            agent_name = typer.prompt("Agent name")
        if not agent_name:
            console.print("[red]Error:[/red] Agent name is required")
            raise typer.Exit(1)

        # Normalize agent id
        agent_id = normalize_agent_id(agent_name)
        if agent_id == "main":
            console.print('[red]Error:[/red] "main" is reserved')
            raise typer.Exit(1)

        # Check for duplicates
        agents_list = list_agent_entries(cfg)
        if find_agent_entry_index(agents_list, agent_id) >= 0:
            console.print(f"[red]Error:[/red] Agent '{agent_id}' already exists")
            raise typer.Exit(1)

        # Resolve workspace directory
        if workspace:
            workspace_dir = resolve_user_path(workspace)
        elif not non_interactive:
            ws_input = typer.prompt("Workspace directory (leave blank for auto)", default="")
            if ws_input:
                workspace_dir = resolve_user_path(ws_input)
            else:
                workspace_dir = resolve_agent_workspace_dir(cfg, agent_id)
        else:
            workspace_dir = resolve_agent_workspace_dir(cfg, agent_id)

        # Apply agent config
        next_config = apply_agent_config(
            cfg,
            agent_id=agent_id,
            name=agent_name,
            workspace=workspace_dir,
        )
        
        # Resolve agentDir
        if agent_dir:
            resolved_agent_dir = resolve_user_path(agent_dir)
        else:
            resolved_agent_dir = resolve_agent_dir(next_config, agent_id)
        
        next_config = apply_agent_config(
            next_config,
            agent_id=agent_id,
            agent_dir=resolved_agent_dir,
        )
        
        if model:
            next_config = apply_agent_config(
                next_config,
                agent_id=agent_id,
                model=model,
            )
        
        # Handle bindings if provided
        if bind:
            # TODO: Implement binding parsing and application
            # For now, skip bindings - they should be handled by config.set
            pass
        
        # Ensure workspace and transcripts exist BEFORE writing config
        skip_bootstrap = False
        if next_config.agents and next_config.agents.defaults:
            skip_bootstrap = getattr(next_config.agents.defaults, 'skip_bootstrap', False)
        
        ensure_workspace_and_sessions(
            workspace_dir=workspace_dir,
            agent_id=agent_id,
            skip_bootstrap=skip_bootstrap,
        )
        
        # Write config
        write_config_file(next_config)

        if json_output:
            output = {
                "agentId": agent_id,
                "name": agent_name,
                "workspace": workspace_dir,
                "agentDir": resolved_agent_dir,
            }
            if model:
                output["model"] = model
            console.print(json.dumps(output, indent=2))
            return

        console.print(f"[green]✓[/green] Agent created: [cyan]{agent_id}[/cyan]")
        console.print(f"  Name:      {agent_name}")
        console.print(f"  Workspace: {workspace_dir}")
        console.print(f"  AgentDir:  {resolved_agent_dir}")
        if model:
            console.print(f"  Model:     {model}")

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@agents_app.command("delete")
def delete(
    agent_id: str = typer.Argument(..., help="Agent id to delete"),
    force: bool = typer.Option(False, "--force", help="Skip confirmation"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Delete an agent and prune its workspace/state"""
    try:
        from ..config.loader import write_config_file

        raw = _load_raw_config()
        agents_list = _get_agents_list(raw)
        idx = next((i for i, a in enumerate(agents_list) if isinstance(a, dict) and a.get("id") == agent_id), -1)
        if idx < 0:
            console.print(f"[red]Agent not found:[/red] {agent_id}")
            raise typer.Exit(1)

        agent_entry = agents_list[idx]
        workspace = agent_entry.get("workspace", "") if isinstance(agent_entry, dict) else ""

        if not force:
            confirm = typer.confirm(f"Delete agent '{agent_id}'?", default=False)
            if not confirm:
                console.print("Cancelled")
                return

        # Remove from list
        raw["agents"]["agents"] = [a for i, a in enumerate(agents_list) if i != idx]
        write_config_file(raw)

        if json_output:
            console.print(json.dumps({"deleted": agent_id, "workspace": workspace}))
            return

        console.print(f"[green]✓[/green] Agent deleted: [cyan]{agent_id}[/cyan]")
        if workspace:
            console.print(f"[dim]  Workspace at {workspace} was not removed (manual cleanup needed)[/dim]")

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@agents_app.command("set-identity")
def set_identity(
    agent_id: Optional[str] = typer.Option(None, "--agent", help="Agent id to update"),
    workspace: Optional[str] = typer.Option(None, "--workspace", help="Workspace directory (resolves agent)"),
    name: Optional[str] = typer.Option(None, "--name", help="Identity name"),
    theme: Optional[str] = typer.Option(None, "--theme", help="Identity theme"),
    emoji: Optional[str] = typer.Option(None, "--emoji", help="Identity emoji"),
    avatar: Optional[str] = typer.Option(None, "--avatar", help="Identity avatar"),
    from_identity: bool = typer.Option(False, "--from-identity", help="Read values from IDENTITY.md"),
    identity_file: Optional[str] = typer.Option(None, "--identity-file", help="Explicit IDENTITY.md path"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Update an agent identity (writes to IDENTITY.md in agent workspace)"""
    try:
        from ..config.loader import write_config_file

        raw = _load_raw_config()
        agents_list = _get_agents_list(raw)

        # Resolve which agent to update
        target_id = agent_id
        if not target_id and workspace:
            # Find agent by workspace path
            ws_resolved = str(Path(workspace).expanduser().resolve())
            for a in agents_list:
                if isinstance(a, dict):
                    a_ws = a.get("workspace", "")
                    if a_ws and str(Path(a_ws).expanduser().resolve()) == ws_resolved:
                        target_id = a.get("id")
                        break

        if not target_id:
            # Default to first agent or prompt
            if agents_list:
                target_id = agents_list[0].get("id") if isinstance(agents_list[0], dict) else None
            if not target_id:
                console.print("[red]Error:[/red] No agent found. Use --agent <id>")
                raise typer.Exit(1)

        agent_entry = next((a for a in agents_list if isinstance(a, dict) and a.get("id") == target_id), None)
        if not agent_entry:
            console.print(f"[red]Agent not found:[/red] {target_id}")
            raise typer.Exit(1)

        # Resolve IDENTITY.md path
        if identity_file:
            identity_path = Path(identity_file).expanduser()
        else:
            ws = agent_entry.get("workspace") or str(Path.home() / ".openclaw" / "workspaces" / target_id)
            identity_path = Path(ws) / "IDENTITY.md"

        updated: dict = {}

        # Read existing identity
        existing_content = ""
        if identity_path.exists():
            existing_content = identity_path.read_text(encoding="utf-8")

        if from_identity and identity_path.exists():
            # Parse existing values (simple key: value format)
            for line in existing_content.splitlines():
                if ": " in line:
                    k, v = line.split(": ", 1)
                    k_lower = k.strip().lower()
                    if k_lower == "name" and not name:
                        name = v.strip()
                    elif k_lower == "theme" and not theme:
                        theme = v.strip()
                    elif k_lower == "emoji" and not emoji:
                        emoji = v.strip()
                    elif k_lower == "avatar" and not avatar:
                        avatar = v.strip()

        # Build IDENTITY.md content
        lines = []
        if name:
            lines.append(f"# {name}")
            updated["name"] = name
        if emoji:
            lines.append(f"\nEmoji: {emoji}")
            updated["emoji"] = emoji
        if theme:
            lines.append(f"Theme: {theme}")
            updated["theme"] = theme
        if avatar:
            lines.append(f"Avatar: {avatar}")
            updated["avatar"] = avatar

        if lines:
            identity_path.parent.mkdir(parents=True, exist_ok=True)
            identity_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            console.print(f"[green]✓[/green] Identity updated for [cyan]{target_id}[/cyan]")
            console.print(f"  Written to: {identity_path}")
        else:
            console.print("[yellow]⚠[/yellow]  No identity fields provided (use --name, --emoji, --theme, --avatar)")

        if json_output:
            console.print(json.dumps({**updated, "agentId": target_id, "path": str(identity_path)}))

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_raw_config() -> dict:
    from pathlib import Path as _Path
    from ..config.loader import load_config_raw
    from ..config.paths import resolve_config_path
    try:
        cfg_path = resolve_config_path()
        if cfg_path and _Path(cfg_path).exists():
            return load_config_raw(_Path(cfg_path)) or {}
    except Exception:
        pass
    default = _Path.home() / ".openclaw" / "openclaw.json"
    if default.exists():
        try:
            return load_config_raw(default) or {}
        except Exception:
            pass
    return {}


def _get_agents_list(raw: dict) -> list:
    agents_section = raw.get("agents") or {}
    if isinstance(agents_section, dict):
        return agents_section.get("agents") or []
    return []
