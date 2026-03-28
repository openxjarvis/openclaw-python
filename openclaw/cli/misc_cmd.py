"""Miscellaneous commands (tui, update, etc)"""

import typer
from rich.console import Console
from ..config.paths import resolve_state_dir

console = Console()


def register_misc_commands(app: typer.Typer):
    """Register miscellaneous commands to the main app"""
    
    @app.command("tui")
    def tui():
        """Launch Terminal UI"""
        try:
            import asyncio
            from ..tui.tui import run_tui
            console.print("[cyan]Launching Terminal UI...[/cyan]")
            asyncio.run(run_tui())
        except KeyboardInterrupt:
            console.print("\n[yellow]TUI stopped[/yellow]")
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)
    
    @app.command("update")
    def update(
        check: bool = typer.Option(False, "--check", help="Check for updates only"),
        force: bool = typer.Option(False, "--force", help="Force update"),
    ):
        """Update OpenClaw"""
        console.print("[yellow]⚠[/yellow]  Update not yet implemented")
    
    @app.command("onboard")
    def onboard(
        workspace: str = typer.Option(None, "--workspace", help="Workspace directory"),
        install_daemon: bool = typer.Option(None, "--install-daemon/--no-install-daemon", help="Install Gateway service"),
        skip_health: bool = typer.Option(False, "--skip-health", help="Skip health check"),
        skip_ui: bool = typer.Option(False, "--skip-ui", help="Skip UI selection prompts"),
        skip_channels: bool = typer.Option(False, "--skip-channels", help="Skip channel setup"),
        skip_skills: bool = typer.Option(False, "--skip-skills", help="Skip skills setup"),
        non_interactive: bool = typer.Option(False, "--non-interactive", help="Run without prompts"),
        accept_risk: bool = typer.Option(False, "--accept-risk", help="Accept risk acknowledgement"),
        flow: str = typer.Option(None, "--flow", help="Onboarding flow: quickstart|advanced|manual"),
        mode: str = typer.Option(None, "--mode", help="Onboarding mode: local|remote"),
        reset: bool = typer.Option(False, "--reset", help="Reset config before onboarding"),
        reset_scope: str = typer.Option(None, "--reset-scope", help="Reset scope: config|config+creds|full"),
        gateway_port: int = typer.Option(None, "--gateway-port", help="Gateway port"),
        gateway_bind: str = typer.Option(None, "--gateway-bind", help="Gateway bind: loopback|lan|auto"),
        gateway_auth: str = typer.Option(None, "--gateway-auth", help="Gateway auth: token|password|none"),
        gateway_token: str = typer.Option(None, "--gateway-token", help="Gateway auth token"),
        gateway_password: str = typer.Option(None, "--gateway-password", help="Gateway auth password"),
        remote_url: str = typer.Option(None, "--remote-url", help="Remote gateway URL"),
        remote_token: str = typer.Option(None, "--remote-token", help="Remote gateway token"),
    ):
        """Interactive wizard to set up the gateway, workspace, and channels"""
        try:
            import asyncio
            from pathlib import Path
            from ..wizard.onboarding import run_onboarding_wizard
            
            console.print("[cyan]Starting onboarding wizard...[/cyan]\n")
            
            workspace_dir = Path(workspace) if workspace else resolve_state_dir() / "workspace"
            
            result = asyncio.run(run_onboarding_wizard(
                workspace_dir=workspace_dir,
                install_daemon=install_daemon,
                skip_health=skip_health,
                skip_ui=skip_ui,
                skip_skills=skip_skills,
                skip_channels=skip_channels,
                non_interactive=non_interactive,
                accept_risk=accept_risk,
                flow=flow,
                mode=mode,
                reset=reset,
                reset_scope=reset_scope,
                gateway_port=gateway_port,
                gateway_bind=gateway_bind,
                gateway_auth=gateway_auth,
                gateway_token=gateway_token,
                gateway_password=gateway_password,
                remote_url=remote_url,
                remote_token=remote_token,
            ))
            
            if result.get("completed"):
                console.print("\n[green]✓[/green] Onboarding completed successfully!")
            elif result.get("skipped"):
                console.print("\n[yellow]Onboarding skipped[/yellow]")
                if reason := result.get("reason"):
                    console.print(f"  Reason: {reason}")
            
        except KeyboardInterrupt:
            console.print("\n[yellow]Onboarding cancelled[/yellow]")
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            import traceback
            traceback.print_exc()
            raise typer.Exit(1)
    
    @app.command("setup")
    def setup():
        """Run setup wizard"""
        console.print("[yellow]⚠[/yellow]  Setup wizard not yet implemented")
    
    @app.command("configure")
    def configure(
        section: str = typer.Option(
            None,
            "--section",
            help="Configuration section (gateway, channels, agents, tools, security)"
        ),
    ):
        """Run configuration wizard"""
        try:
            import asyncio
            from ..wizard.configure import run_configure_wizard
            
            console.print("[cyan]Starting configuration wizard...[/cyan]\n")
            result = asyncio.run(run_configure_wizard(section=section))
            
        except KeyboardInterrupt:
            console.print("\n[yellow]Configuration cancelled[/yellow]")
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)
    
    @app.command("docs")
    def docs():
        """Open documentation"""
        console.print("[cyan]📚 Documentation:[/cyan]")
        console.print("https://github.com/your-org/openclaw-python")
    
    @app.command("webhooks")
    def webhooks():
        """Manage webhooks"""
        console.print("[yellow]⚠[/yellow]  Webhooks management not yet implemented")
    
    @app.command("directory")
    def directory():
        """Show OpenClaw directories"""
        from pathlib import Path
        console.print("[cyan]OpenClaw Directories:[/cyan]")
        console.print(f"  Config: {Path.home() / '.openclaw'}")
        console.print(f"  State: {Path.home() / '.openclaw' / 'state'}")
        console.print(f"  Logs: {Path.home() / '.openclaw' / 'logs'}")
    
    @app.command("completion")
    def completion():
        """Shell completion setup"""
        console.print("[yellow]⚠[/yellow]  Shell completion not yet implemented")
    
    @app.command("approvals")
    def approvals():
        """Manage approvals"""
        console.print("[yellow]⚠[/yellow]  Approvals management not yet implemented")
    
    @app.command("acp")
    def acp():
        """Approvals Control Panel"""
        console.print("[yellow]⚠[/yellow]  ACP not yet implemented")

    @app.command("qr")
    def qr(
        remote: bool = typer.Option(False, "--remote", help="Use gateway remote URL"),
        url: str = typer.Option(None, "--url", help="Override gateway URL"),
        public_url: str = typer.Option(None, "--public-url", help="Public URL for pairing"),
        token: str = typer.Option(None, "--token", help="Force token auth"),
        password: str = typer.Option(None, "--password", help="Force password auth"),
        setup_code_only: bool = typer.Option(False, "--setup-code-only", help="Print only the setup code"),
        no_ascii: bool = typer.Option(False, "--no-ascii", help="Skip ASCII QR art"),
        json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    ):
        """Generate a pairing QR code and setup code"""
        import json as _json

        if token and password:
            console.print("[red]Error:[/red] --token and --password are mutually exclusive")
            raise typer.Exit(1)

        try:
            from ..config.loader import load_config
            cfg = load_config()

            gateway_cfg = cfg.gateway
            gateway_url = url or public_url
            if not gateway_url and gateway_cfg:
                port = gateway_cfg.port or 18789
                bind = gateway_cfg.bind or "loopback"
                host = "127.0.0.1" if bind == "loopback" else "0.0.0.0"
                gateway_url = f"http://{host}:{port}"

            auth_mode = "token" if token else ("password" if password else None)
            auth_value = token or password

            if not auth_mode and gateway_cfg and gateway_cfg.auth:
                auth_mode = gateway_cfg.auth.mode
                auth_value = gateway_cfg.auth.token or gateway_cfg.auth.password

            import hashlib
            import base64
            payload = _json.dumps({
                "gatewayUrl": gateway_url,
                "auth": {"mode": auth_mode, "value": auth_value} if auth_mode else None,
            }, separators=(",", ":"))
            setup_code = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")

            if json_output:
                console.print(_json.dumps({
                    "setupCode": setup_code,
                    "gatewayUrl": gateway_url,
                    "auth": auth_mode,
                }, indent=2))
            elif setup_code_only:
                console.print(setup_code)
            else:
                console.print(f"\n[cyan]Gateway URL:[/cyan] {gateway_url}")
                console.print(f"[cyan]Auth:[/cyan] {auth_mode or 'none'}")
                console.print(f"\n[cyan]Setup Code:[/cyan]\n  {setup_code}")
                try:
                    import qrcode  # type: ignore[import-untyped]
                    qr_obj = qrcode.QRCode(border=1)
                    qr_obj.add_data(setup_code)
                    qr_obj.make(fit=True)
                    console.print()
                    qr_obj.print_ascii(invert=True)
                except ImportError:
                    if not no_ascii:
                        console.print("\n[dim]Install 'qrcode' for ASCII QR: pip install qrcode[/dim]")

        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1)

    @app.command("secrets")
    def secrets_cmd(
        action: str = typer.Argument("audit", help="Action: reload, audit, configure, apply"),
        json_output: bool = typer.Option(False, "--json", help="Output JSON"),
        check: bool = typer.Option(False, "--check", help="Exit non-zero on audit findings"),
        gateway_url: str = typer.Option(None, "--url", help="Gateway URL for reload"),
        gateway_token: str = typer.Option(None, "--token", help="Gateway auth token"),
        timeout: int = typer.Option(30000, "--timeout", help="Gateway RPC timeout (ms)"),
        apply_from: str = typer.Option(None, "--from", help="Plan file for apply"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Dry run for apply"),
    ):
        """Secrets management: reload, audit, configure, apply"""
        import asyncio

        if action == "reload":
            async def _reload():
                try:
                    from ..gateway.rpc_client import call_gateway
                    result = await call_gateway(
                        method="secrets.reload",
                        params=None,
                        url=gateway_url,
                        token=gateway_token,
                        timeout_ms=timeout,
                    )
                    if json_output:
                        import json as _json
                        console.print(_json.dumps(result, indent=2))
                    else:
                        console.print("[green]Secrets reloaded[/green]")
                except Exception as e:
                    console.print(f"[red]Error reloading secrets:[/red] {e}")
                    raise typer.Exit(1)
            asyncio.run(_reload())

        elif action == "audit":
            try:
                from ..config.loader import load_config
                from ..secrets.audit import run_secrets_audit
                cfg = load_config()
                findings = run_secrets_audit(cfg)
                if json_output:
                    import json as _json
                    console.print(_json.dumps([f.__dict__ if hasattr(f, "__dict__") else str(f) for f in findings], indent=2))
                elif findings:
                    console.print(f"[yellow]Found {len(findings)} secret issue(s):[/yellow]")
                    for f in findings[:20]:
                        console.print(f"  - {f}")
                    if check:
                        raise typer.Exit(1)
                else:
                    console.print("[green]No secret issues found[/green]")
            except (ImportError, ModuleNotFoundError):
                console.print("[yellow]Secrets audit not yet fully implemented[/yellow]")

        elif action == "configure":
            console.print("[yellow]Interactive secrets configuration not yet implemented[/yellow]")

        elif action == "apply":
            if not apply_from:
                console.print("[red]Error:[/red] --from is required for apply")
                raise typer.Exit(1)
            console.print(f"[yellow]Secrets apply from '{apply_from}' not yet implemented[/yellow]")

        else:
            console.print(f"[red]Unknown action:[/red] {action}")
            console.print("Available: reload, audit, configure, apply")
            raise typer.Exit(1)

    @app.command("health")
    def health(
        json_output: bool = typer.Option(False, "--json", help="Output JSON"),
        timeout: int = typer.Option(10000, "--timeout", help="Connection timeout (ms)"),
        verbose: bool = typer.Option(False, "--verbose", help="Verbose output"),
        debug: bool = typer.Option(False, "--debug", help="Alias for --verbose"),
    ):
        """Fetch health from the running gateway"""
        import asyncio

        async def _health():
            try:
                from ..gateway.rpc_client import call_gateway
                from ..config.loader import load_config
                cfg = load_config()

                params = {"probe": True} if (verbose or debug) else None
                result = await call_gateway(
                    method="health",
                    params=params,
                    timeout_ms=timeout,
                )

                if json_output:
                    import json as _json
                    console.print(_json.dumps(result, indent=2))
                else:
                    if isinstance(result, dict):
                        status = result.get("status", "unknown")
                        color = "green" if status == "ok" else "yellow" if status == "degraded" else "red"
                        console.print(f"[{color}]Gateway: {status}[/{color}]")

                        channels = result.get("channels", [])
                        if channels:
                            console.print(f"\n[cyan]Channels ({len(channels)}):[/cyan]")
                            for ch in channels:
                                ch_name = ch.get("name", "?")
                                ch_status = ch.get("status", "?")
                                ch_color = "green" if ch_status == "connected" else "yellow" if ch_status == "connecting" else "red"
                                console.print(f"  [{ch_color}]{ch_name}: {ch_status}[/{ch_color}]")

                        agents = result.get("agents", [])
                        if agents and (verbose or debug):
                            console.print(f"\n[cyan]Agents ({len(agents)}):[/cyan]")
                            for a in agents:
                                console.print(f"  {a.get('id', '?')}: {a.get('status', '?')}")
                    else:
                        console.print(str(result))

            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")
                raise typer.Exit(1)

        asyncio.run(_health())
