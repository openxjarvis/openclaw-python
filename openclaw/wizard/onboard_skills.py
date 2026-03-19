"""Skills setup during onboarding - aligned with TypeScript onboard-skills.ts

Implements full interactive flow:
- Status display (eligible, missing, unsupported OS, blocked)
- Multi-select install via questionary.checkbox
- API Key configuration for skills with primaryEnv
- Always asks "Configure skills now?" regardless of mode (matches TS behavior)

Aligned with TypeScript behavior: setupSkills() always prompts the user,
no special handling for QuickStart vs Advanced modes.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any
from ..config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


def _format_skill_hint(skill: dict) -> str:
    """Format skill hint (description + install label)."""
    desc = (skill.get("description") or "").strip()
    install_label = ""
    install_opts = skill.get("install") or []
    if install_opts:
        install_label = (install_opts[0].get("label") or "").strip()
    combined = f"{desc} — {install_label}" if desc and install_label else desc or install_label
    if not combined:
        return "install"
    return combined[:90] + "…" if len(combined) > 90 else combined


def _detect_binary(name: str) -> bool:
    """Check if binary is on PATH."""
    import shutil
    return shutil.which(name) is not None


def _detect_package_manager() -> str:
    """Detect available Python package manager."""
    if _detect_binary("uv"):
        return "uv"
    if _detect_binary("poetry"):
        return "poetry"
    return "pip"


async def _install_skill_deps(
    skill_name: str,
    install_id: str,
    workspace_dir: Path,
) -> dict[str, Any]:
    """
    Install skill dependencies (brew/npm/go/uv).
    Returns {ok: bool, message: str, stdout?: str, stderr?: str, code?: int}.
    """
    try:
        from openclaw.agents.skills.workspace import load_workspace_skill_entries
        from openclaw.agents.skills.installer import install_skill_dependencies

        entries = load_workspace_skill_entries(workspace_dir)
        target = next((e for e in entries if e.skill.name == skill_name), None)
        if not target:
            return {"ok": False, "message": f"Skill '{skill_name}' not found"}

        install_specs = []
        for spec in (getattr(target.skill.metadata, "install", None) or []):
            spec_id = getattr(spec, "id", None) or f"{getattr(spec, 'kind', '')}-0"
            if spec_id == install_id:
                install_specs = [spec]
                break
        if not install_specs:
            install_specs = getattr(target.skill.metadata, "install", None) or []

        if not install_specs:
            return {"ok": False, "message": "No install spec found"}

        try:
            success, errors = await install_skill_dependencies(target.skill, install_specs)
            if success:
                return {"ok": True, "message": ""}
            return {"ok": False, "message": "; ".join(errors) if errors else "Install failed"}
        except Exception as e:
            # Fallback: run via subprocess
            spec = install_specs[0]
            kind = getattr(spec, "kind", "")
            cmd = []
            if kind == "brew" and getattr(spec, "formula", None):
                cmd = ["brew", "install", spec.formula]
            elif kind == "node" and getattr(spec, "package", None):
                cmd = ["npm", "install", "-g", spec.package]
            elif kind == "uv" and getattr(spec, "package", None):
                cmd = [sys.executable, "-m", "uv", "pip", "install", spec.package]
            elif kind == "go" and getattr(spec, "module", None):
                cmd = ["go", "install", spec.module]

            if not cmd:
                return {"ok": False, "message": f"Unsupported install kind: {kind}"}

            result = subprocess.run(cmd, capture_output=True, text=True)
            return {
                "ok": result.returncode == 0,
                "message": result.stderr or result.stdout or "",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "code": result.returncode,
            }
    except Exception as e:
        logger.exception("Skill install failed")
        return {"ok": False, "message": str(e)}


def _upsert_skill_entry(config: dict, skill_key: str, patch: dict) -> dict:
    """Upsert skill entry in config (matches TS upsertSkillEntry)."""
    config = dict(config)
    skills = config.get("skills") or {}
    entries = dict(skills.get("entries") or {})
    existing = entries.get(skill_key) or {}
    if isinstance(existing, dict):
        entries[skill_key] = {**existing, **patch}
    else:
        entries[skill_key] = patch
    config["skills"] = {**skills, "entries": entries}
    return config


async def setup_skills(
    workspace_dir: Path | None = None,
    config: dict | None = None,
    mode: str = "quickstart",
) -> dict[str, Any]:
    """
    Setup skills during onboarding (aligned with TS setupSkills).

    Args:
        workspace_dir: Workspace directory (default: ~/.openclaw/workspace)
        config: Current config dict (will be updated with API keys / install prefs)
        mode: "quickstart" or "advanced"

    Returns:
        Dict with installed, configured, config (updated config to save)
    """
    from openclaw.agents.skills_status import build_workspace_skill_status
    from . import prompter

    ws = workspace_dir or (resolve_state_dir() / "workspace")
    cfg = dict(config) if config else {}

    print("\n" + "=" * 60)
    print("🛠️  SKILLS SETUP")
    print("=" * 60)

    # Get bundled skills directory (project root / skills)
    project_root = Path(__file__).parent.parent.parent
    bundled_skills_dir = project_root / "skills"

    report = build_workspace_skill_status(ws, config=cfg, bundled_skills_dir=bundled_skills_dir)
    skills = report.get("skills", [])

    eligible = [s for s in skills if s.get("eligible")]
    missing = [
        s for s in skills
        if not s.get("eligible") and not s.get("disabled") and not s.get("blockedByAllowlist")
        and len(s.get("missing", {}).get("os", []) or []) == 0
    ]
    unsupported_os = [
        s for s in skills
        if not s.get("disabled") and not s.get("blockedByAllowlist")
        and len(s.get("missing", {}).get("os", []) or []) > 0
    ]
    blocked = [s for s in skills if s.get("blockedByAllowlist")]

    # Status summary
    prompter.note(
        "\n".join([
            f"Eligible: {len(eligible)}",
            f"Missing requirements: {len(missing)}",
            f"Unsupported on this OS: {len(unsupported_os)}",
            f"Blocked by allowlist: {len(blocked)}",
        ]),
        title="Skills status",
    )
    print()

    # Ask user if they want to configure skills (same behavior for all modes)
    try:
        should_configure = prompter.confirm(
            "Configure skills now? (recommended)",
            default=True,
        )
    except prompter.WizardCancelledError:
        return {"installed": [], "config": cfg, "skipped": True}

    if not should_configure:
        return {"installed": [], "config": cfg, "skipped": True}

    # Multi-select install (unified for QuickStart and Advanced)
    installable = [
        s for s in missing
        if (s.get("install") or []) and (s.get("missing", {}).get("bins") or [])
    ]

    installed: list[str] = []

    if installable:
        choices = [
            {"name": "Skip for now", "value": "__skip__", "description": "Continue without installing dependencies"},
            *[
                {
                    "name": f"{s.get('emoji') or '🧩'} {s.get('name', '')}",
                    "value": s.get("name", ""),
                    "description": _format_skill_hint(s),
                }
                for s in installable
            ],
        ]

        # Customize message based on mode
        message = "Install missing skill dependencies"

        try:
            selected = prompter.checkbox(
                message,
                choices=choices,
            )
        except prompter.WizardCancelledError:
            selected = ["__skip__"]

        to_install = [n for n in selected if n != "__skip__"]

        # Homebrew prompt if needed
        import platform as _plat
        if _plat.system() != "Windows":
            needs_brew = any(
                any(opt.get("kind") == "brew" for opt in (s.get("install") or []))
                for s in installable if s.get("name") in to_install
            )
            if needs_brew and not _detect_binary("brew"):
                prompter.note(
                    "Many skill dependencies are shipped via Homebrew.\n"
                    "Without brew, you'll need to build from source or download releases manually.",
                    title="Homebrew recommended",
                )
                try:
                    if prompter.confirm("Show Homebrew install command?", default=True):
                        prompter.note(
                            'Run: /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
                            title="Homebrew install",
                        )
                except prompter.WizardCancelledError:
                    pass

        for name in to_install:
            target = next((s for s in installable if s.get("name") == name), None)
            if not target or not (target.get("install") or []):
                continue
            install_id = target["install"][0].get("id") or target["install"][0].get("kind", "install")
            print(f"\n📥 Installing {name}…")
            result = await _install_skill_deps(name, install_id, ws)
            if result.get("ok"):
                installed.append(name)
                print(f"   ✓ Installed {name}")
            else:
                msg = result.get("message", "Unknown error")
                print(f"   ✗ Install failed: {name} — {msg[:80]}")
                if result.get("stderr"):
                    print(f"     {result['stderr'][:200]}")

    # API Key configuration for missing env (unified for both modes)
    for skill in missing:
        primary = skill.get("primaryEnv")
        missing_env = skill.get("missing", {}).get("env") or []
        if not primary or not missing_env:
            continue
        try:
            wants = prompter.confirm(
                f"Set {primary} for {skill.get('name', '')}?",
                default=False,
            )
            if wants:
                value = prompter.text(f"Enter {primary}", default="")
                if value and value.strip():
                    cfg = _upsert_skill_entry(cfg, skill.get("skillKey", skill["name"]), {"apiKey": value.strip()})
                    print(f"  ✓ {primary} saved for {skill.get('name', '')}")
        except prompter.WizardCancelledError:
            break

    # Summary
    print(f"\n✅ Skills setup complete. Installed: {len(installed)}")
    
    return {"installed": installed, "count": len(installed), "config": cfg}


__all__ = ["setup_skills"]
