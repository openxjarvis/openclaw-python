"""Skills management commands"""

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from ..config.paths import resolve_state_dir

console = Console()
skills_app = typer.Typer(help="List and inspect available skills")


@skills_app.command("list")
def list_skills(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    eligible: bool = typer.Option(False, "--eligible", help="Show only eligible skills"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show more details"),
):
    """List all available skills"""
    try:
        from ..agents.skills.loader import load_skills_from_dir
        
        all_skills = []
        
        # Load from all sources
        project_root = Path(__file__).parent.parent.parent
        bundled_skills = project_root / "skills"
        managed_skills = resolve_state_dir() / "skills"
        workspace_skills = resolve_state_dir() / "skills"
        
        for skills_dir, source in [
            (bundled_skills, "bundled"),
            (managed_skills, "managed"),
            (workspace_skills, "workspace"),
        ]:
            if skills_dir.exists():
                loaded = load_skills_from_dir(skills_dir, source)
                all_skills.extend(loaded)
        
        # For now, show all skills (eligibility filtering can be added later)
        skills_to_show = all_skills
        
        if json_output:
            skills_data = [
                {
                    "name": skill.name,
                    "description": skill.description or "",
                    "source": skill.source,
                }
                for skill in skills_to_show
            ]
            console.print(json.dumps(skills_data, indent=2))
            return
        
        if not skills_to_show:
            console.print("[yellow]No skills found[/yellow]")
            return
        
        table = Table(title=f"Skills ({len(skills_to_show)})")
        table.add_column("Name", style="cyan")
        table.add_column("Source", style="yellow")
        table.add_column("Description", style="green")
        
        for skill in skills_to_show:
            desc = skill.description or ""
            if len(desc) > 60:
                desc = desc[:60] + "..."
            table.add_row(skill.name, skill.source, desc)
        
        console.print(table)
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@skills_app.command("info")
def info(
    name: str = typer.Argument(..., help="Skill name"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show detailed information about a skill"""
    try:
        from ..agents.skills.loader import load_skills_from_dir
        
        # Load from all sources
        project_root = Path(__file__).parent.parent.parent
        bundled_skills = project_root / "skills"
        managed_skills = resolve_state_dir() / "skills"
        workspace_skills = resolve_state_dir() / "skills"
        
        all_skills = []
        for skills_dir, source in [
            (bundled_skills, "bundled"),
            (managed_skills, "managed"),
            (workspace_skills, "workspace"),
        ]:
            if skills_dir.exists():
                loaded = load_skills_from_dir(skills_dir, source)
                all_skills.extend(loaded)
        
        skill = next((s for s in all_skills if s.name == name), None)
        if not skill:
            console.print(f"[red]Skill not found:[/red] {name}")
            raise typer.Exit(1)
        
        if json_output:
            skill_data = {
                "name": skill.name,
                "description": skill.description or "",
                "source": skill.source,
                "path": str(skill.file_path),
            }
            console.print(json.dumps(skill_data, indent=2))
            return
        
        console.print(f"\n[bold cyan]{skill.name}[/bold cyan]")
        console.print(f"[dim]Source: {skill.source}[/dim]")
        console.print(f"[dim]Path: {skill.file_path}[/dim]\n")
        console.print(skill.description or "No description")
        
        if skill.metadata:
            console.print(f"\n[cyan]Metadata:[/cyan]")
            for key, value in skill.metadata.items():
                console.print(f"  {key}: {value}")
        
        console.print()
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@skills_app.command("check")
def check(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Check which skills are ready vs missing requirements"""
    try:
        from ..agents.skills_status import build_workspace_skill_status
        
        # Use workspace dir or default
        workspace_dir = resolve_state_dir() / "workspace"
        
        # Get bundled skills directory (project root / skills)
        project_root = Path(__file__).parent.parent.parent
        bundled_skills_dir = project_root / "skills"
        
        # Build comprehensive status report
        report = build_workspace_skill_status(
            workspace_dir, 
            config=None,
            bundled_skills_dir=bundled_skills_dir,
        )
        
        # Filter skills by eligibility
        all_skills = report.get("skills", [])
        eligible = [s for s in all_skills if s.get("eligible")]
        missing = [s for s in all_skills if not s.get("eligible") and s.get("source") != "unsupported"]
        unsupported = [s for s in all_skills if s.get("source") == "unsupported"]
        
        eligible_count = len(eligible)
        missing_count = len(missing)
        unsupported_count = len(unsupported)
        total_count = len(all_skills)
        
        if json_output:
            result = {
                "total": total_count,
                "eligible": eligible_count,
                "missing": missing_count,
                "unsupported": unsupported_count,
                "ready": [s["name"] for s in eligible],
                "not_ready": [s["name"] for s in missing],
            }
            console.print(json.dumps(result, indent=2))
            return
        
        console.print(f"\n[cyan]Skills Status:[/cyan]")
        console.print(f"  Total: {total_count}")
        console.print(f"  [green]✓[/green] Ready: {eligible_count}")
        console.print(f"  [yellow]✗[/yellow] Missing requirements: {missing_count}")
        
        if unsupported_count > 0:
            console.print(f"  [red]✗[/red] Unsupported on this OS: {unsupported_count}")
        
        if eligible_count > 0:
            console.print(f"\n[green]Ready skills:[/green]")
            for skill in eligible:
                console.print(f"  • {skill['name']}")
        
        if missing_count > 0:
            console.print(f"\n[yellow]Not ready (missing requirements):[/yellow]")
            for skill in missing:
                missing_items = []
                missing_info = skill.get("missing", {})
                if missing_info.get("bins"):
                    missing_items.append(f"bins: {', '.join(missing_info['bins'])}")
                if missing_info.get("env"):
                    missing_items.append(f"env: {', '.join(missing_info['env'])}")
                
                info = f" ({'; '.join(missing_items)})" if missing_items else ""
                console.print(f"  • {skill['name']}{info}")
        
        console.print()
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@skills_app.command("install")
def install(
    skill_id: str = typer.Argument(..., help="Skill ID or name to install"),
    force: bool = typer.Option(False, "--force", "-f", help="Force reinstall even if already installed"),
):
    """Install dependencies for a specific skill"""
    try:
        from ..agents.skills.installer import install_skill_dependencies
        from ..agents.skills.loader import load_skills_from_dir
        
        # Find the skill
        project_root = Path(__file__).parent.parent.parent
        bundled_skills = project_root / "skills"
        managed_skills = resolve_state_dir() / "skills"
        workspace_skills = resolve_state_dir() / "skills"
        
        all_skills = []
        for skills_dir in [bundled_skills, managed_skills, workspace_skills]:
            if skills_dir.exists():
                loaded = load_skills_from_dir(skills_dir, "")
                all_skills.extend(loaded)
        
        # Match skill by ID or name
        target_skill = None
        for skill in all_skills:
            if skill.name.lower() == skill_id.lower() or skill.name.replace("-", " ").lower() == skill_id.lower():
                target_skill = skill
                break
        
        if not target_skill:
            console.print(f"[red]Skill not found:[/red] {skill_id}")
            console.print(f"[dim]Run 'openclaw skills list' to see available skills[/dim]")
            raise typer.Exit(1)
        
        # Install dependencies
        console.print(f"\n[cyan]Installing dependencies for {target_skill.name}...[/cyan]")
        
        # Check if skill has install specs
        install_specs = target_skill.metadata.get("install", []) if target_skill.metadata else []
        
        if not install_specs:
            console.print(f"[yellow]No installation instructions found for {target_skill.name}[/yellow]")
            return
        
        # Call async installer
        import asyncio
        success, installed = asyncio.run(install_skill_dependencies(target_skill, install_specs))
        
        if success:
            console.print(f"[green]✓[/green] Successfully installed {target_skill.name}")
            
            if installed:
                console.print(f"\n[dim]Installed packages:[/dim]")
                for pkg in installed:
                    console.print(f"  • {pkg}")
        else:
            console.print(f"[red]✗[/red] Failed to install {target_skill.name}")
            console.print(f"[dim]Some dependencies may require manual installation[/dim]")
            raise typer.Exit(1)
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# Default action
@skills_app.callback(invoke_without_command=True)
def skills_default(ctx: typer.Context):
    """List skills (default command)"""
    if ctx.invoked_subcommand is None:
        list_skills()
