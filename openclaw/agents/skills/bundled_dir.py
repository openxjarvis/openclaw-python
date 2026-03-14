"""
Bundled skills directory resolution.

Mirrors openclaw/src/agents/skills/bundled-dir.ts
"""
import os
import sys
from pathlib import Path


def resolve_bundled_skills_dir() -> Path | None:
    """
    Resolve bundled skills directory.
    
    Mirrors TS resolveBundledSkillsDir() from bundled-dir.ts
    
    Priority:
    1. OPENCLAW_BUNDLED_SKILLS_DIR env var
    2. skills/ directory next to executable (for compiled binaries)
    3. Package root skills/ directory (for dev/installed)
    
    Returns:
        Path to bundled skills directory, or None if not found
    """
    # 1. Check environment override
    override = os.environ.get("OPENCLAW_BUNDLED_SKILLS_DIR", "").strip()
    if override:
        candidate = Path(override)
        if candidate.exists():
            return candidate
    
    # 2. Check sibling to executable (for compiled binaries)
    try:
        exec_dir = Path(sys.executable).parent
        sibling = exec_dir / "skills"
        if sibling.exists() and _looks_like_skills_dir(sibling):
            return sibling
    except Exception:
        pass
    
    # 3. Search up from current module to find package root
    try:
        # Start from this file's directory
        current = Path(__file__).parent
        for _ in range(6):
            candidate = current / "skills"
            if _looks_like_skills_dir(candidate):
                return candidate
            
            # Try going up
            parent = current.parent
            if parent == current:
                break
            current = parent
        
        # Also try from openclaw package root (openclaw-python/openclaw/)
        openclaw_root = Path(__file__).parent.parent
        candidate = openclaw_root.parent / "skills"
        if _looks_like_skills_dir(candidate):
            return candidate
            
    except Exception:
        pass
    
    return None


def _looks_like_skills_dir(dir_path: Path) -> bool:
    """
    Check if directory looks like a skills directory.
    
    Mirrors TS looksLikeSkillsDir() from bundled-dir.ts lines 6-27
    
    Args:
        dir_path: Directory to check
    
    Returns:
        True if directory contains skills
    """
    if not dir_path.exists() or not dir_path.is_dir():
        return False
    
    try:
        for entry in dir_path.iterdir():
            if entry.name.startswith("."):
                continue
            
            # Check for markdown files (might be skill root)
            if entry.is_file() and entry.name.endswith(".md"):
                return True
            
            # Check for subdirectories with SKILL.md
            if entry.is_dir():
                skill_md = entry / "SKILL.md"
                if skill_md.exists():
                    return True
    except Exception:
        return False
    
    return False
