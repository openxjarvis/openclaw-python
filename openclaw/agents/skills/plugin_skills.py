"""Plugin skills resolution (matches TypeScript agents/skills/plugin-skills.ts)"""

import logging
from pathlib import Path
from typing import Any
from openclaw.config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


def resolve_plugin_skills(
    plugin_dirs: list[Path] | None = None,
) -> list[dict[str, Any]]:
    """
    Resolve skills provided by installed plugins.
    
    Plugins can provide skills in their own skills/ directory.
    
    Args:
        plugin_dirs: Plugin directories to scan
    
    Returns:
        List of skill info dicts
    """
    if plugin_dirs is None:
        plugin_dirs = [
            resolve_state_dir() / "plugins",
        ]
    
    plugin_skills = []
    
    for plugin_dir in plugin_dirs:
        if not plugin_dir.exists():
            continue
        
        for plugin_path in plugin_dir.iterdir():
            if not plugin_path.is_dir():
                continue
            
            skills_dir = plugin_path / "skills"
            if not skills_dir.exists():
                continue
            
            for skill_dir in skills_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue
                
                try:
                    content = skill_md.read_text(encoding="utf-8")
                    
                    plugin_skills.append({
                        "name": skill_dir.name,
                        "source": f"plugin:{plugin_path.name}",
                        "path": str(skill_dir),
                        "content": content,
                    })
                    
                    logger.debug(
                        f"Found plugin skill: {skill_dir.name} "
                        f"from plugin {plugin_path.name}"
                    )
                except Exception as e:
                    logger.error(f"Failed to load plugin skill {skill_dir}: {e}")
    
    return plugin_skills
