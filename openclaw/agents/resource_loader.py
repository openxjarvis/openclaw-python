"""
Project context resource loader - aligned with coding-agent core/resource-loader.ts

Loads project context files (AGENTS.md, CLAUDE.md) from:
1. Global directory (~/.openclaw/)
2. Project directory tree (cwd to root)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List
from openclaw.config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


@dataclass
class ContextFile:
    """
    Context file representation - aligned with coding-agent
    
    Contains file path and content for project context
    """
    path: str
    content: str


def load_project_context_files(
    cwd: str | None = None,
    agent_dir: str | None = None,
) -> List[ContextFile]:
    """
    Load project context files - aligned with coding-agent loadProjectContextFiles()
    
    Loading order:
    1. Global directory (~/.openclaw/ or specified agent_dir)
    2. Project directories (from cwd up to root)
    
    Context files searched: AGENTS.md, CLAUDE.md
    
    Args:
        cwd: Current working directory (defaults to os.getcwd())
        agent_dir: Agent configuration directory (defaults to ~/.openclaw/)
        
    Returns:
        List of context files (ordered: global first, then root to cwd)
    """
    resolved_cwd = Path(cwd or os.getcwd())
    resolved_agent_dir = Path(agent_dir or resolve_state_dir())
    
    context_files: List[ContextFile] = []
    seen_paths: set[str] = set()
    
    # 1. Load global context from agent directory
    global_context = _load_context_file_from_dir(resolved_agent_dir)
    if global_context and global_context.path not in seen_paths:
        context_files.append(global_context)
        seen_paths.add(global_context.path)
        logger.debug(f"Loaded global context from {global_context.path}")
    
    # 2. Load project context from cwd up to root
    ancestor_files: List[ContextFile] = []
    current_dir = resolved_cwd
    root = Path("/")
    
    # Traverse up to root
    while True:
        context_file = _load_context_file_from_dir(current_dir)
        if context_file and context_file.path not in seen_paths:
            # Insert at beginning so root comes first
            ancestor_files.insert(0, context_file)
            seen_paths.add(context_file.path)
            logger.debug(f"Loaded project context from {context_file.path}")
        
        # Stop at root
        if current_dir == root:
            break
        
        # Move to parent
        parent_dir = current_dir.parent
        if parent_dir == current_dir:
            # Reached top (shouldn't happen but safety check)
            break
        current_dir = parent_dir
    
    # Add ancestor files (root to cwd order)
    context_files.extend(ancestor_files)
    
    if context_files:
        logger.info(f"Loaded {len(context_files)} context files")
    
    return context_files


def _load_context_file_from_dir(directory: Path) -> ContextFile | None:
    """
    Load context file from a directory - aligned with coding-agent
    
    Searches for AGENTS.md or CLAUDE.md in the directory.
    Returns first match found.
    
    Args:
        directory: Directory to search
        
    Returns:
        ContextFile if found, None otherwise
    """
    # Check if directory exists
    if not directory.exists() or not directory.is_dir():
        return None
    
    # Try each context filename
    for filename in ["AGENTS.md", "CLAUDE.md"]:
        file_path = directory / filename
        if file_path.exists() and file_path.is_file():
            try:
                content = file_path.read_text(encoding="utf-8")
                return ContextFile(path=str(file_path), content=content)
            except Exception as e:
                logger.warning(f"Failed to load {file_path}: {e}")
    
    return None


def build_system_prompt_with_context(
    base_prompt: str,
    context_files: List[ContextFile],
    tools: List[Any] | None = None,
    skills: List[str] | None = None,
) -> str:
    """
    Build system prompt with project context - aligned with coding-agent buildSystemPrompt()
    
    Constructs a system prompt that includes:
    - Base prompt
    - Project context files
    - Available tools documentation
    - Available skills
    
    Args:
        base_prompt: Base system prompt
        context_files: Project context files
        tools: Available tools (with name, description attributes)
        skills: Available skill names
        
    Returns:
        Complete system prompt with context
    """
    parts = [base_prompt]
    
    # Add project context
    if context_files:
        parts.append("\n\n## Project Context\n")
        parts.append(
            "The following context files provide important information about this project:\n"
        )
        
        for ctx in context_files:
            # Extract filename from path
            filename = Path(ctx.path).name
            parts.append(f"\n### From {filename} ({ctx.path})\n\n{ctx.content}\n")
    
    # Add tools documentation
    if tools:
        parts.append("\n\n## Available Tools\n")
        parts.append("You have access to the following tools:\n\n")
        
        for tool in tools:
            tool_name = getattr(tool, "name", "unknown")
            tool_desc = getattr(tool, "description", "No description")
            parts.append(f"- **{tool_name}**: {tool_desc}\n")
    
    # Add skills
    if skills:
        parts.append("\n\n## Available Skills\n")
        parts.append("You have access to the following skills:\n\n")
        parts.append("\n".join(f"- {s}" for s in skills))
        parts.append("\n")
    
    return "".join(parts)


def find_project_root(start_dir: str | None = None) -> Path | None:
    """
    Find project root by looking for version control markers
    
    Searches for .git, .svn, or pyproject.toml/package.json markers.
    
    Args:
        start_dir: Starting directory (defaults to cwd)
        
    Returns:
        Project root path if found, None otherwise
    """
    current = Path(start_dir or os.getcwd())
    root = Path("/")
    
    # Markers that indicate project root
    markers = [".git", ".svn", "pyproject.toml", "package.json", "Cargo.toml", "go.mod"]
    
    while current != root:
        for marker in markers:
            if (current / marker).exists():
                logger.debug(f"Found project root at {current} (marker: {marker})")
                return current
        
        parent = current.parent
        if parent == current:
            break
        current = parent
    
    return None


def get_relative_path(path: str, base: str | None = None) -> str:
    """
    Get relative path from base directory
    
    Args:
        path: Absolute or relative path
        base: Base directory (defaults to cwd)
        
    Returns:
        Relative path string
    """
    try:
        path_obj = Path(path)
        base_obj = Path(base or os.getcwd())
        return str(path_obj.relative_to(base_obj))
    except ValueError:
        # Not relative to base, return as-is
        return path


def load_file_content(file_path: str) -> str:
    """
    Load file content safely with encoding detection
    
    Args:
        file_path: Path to file
        
    Returns:
        File content as string
        
    Raises:
        FileNotFoundError: If file doesn't exist
        IOError: If file can't be read
    """
    path = Path(file_path)
    
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    
    # Try UTF-8 first (most common)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Try latin-1 as fallback
        try:
            return path.read_text(encoding="latin-1")
        except Exception as e:
            raise IOError(f"Failed to read file {file_path}: {e}") from e


def is_binary_file(file_path: str, sample_size: int = 8192) -> bool:
    """
    Check if file is binary by sampling content
    
    Args:
        file_path: Path to file
        sample_size: Number of bytes to sample
        
    Returns:
        True if file appears to be binary
    """
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(sample_size)
        
        # Check for null bytes (strong indicator of binary)
        if b"\x00" in chunk:
            return True
        
        # Check for high ratio of non-text bytes
        text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)))
        non_text = sum(1 for byte in chunk if byte not in text_chars)
        
        return non_text / len(chunk) > 0.3 if chunk else False
        
    except Exception:
        return True  # Assume binary if can't read
