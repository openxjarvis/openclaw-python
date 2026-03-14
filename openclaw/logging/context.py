"""Caller context extraction for logging metadata.

Aligned with TypeScript logging metadata structure.
"""

from __future__ import annotations

import inspect
import os
import platform
import sys
from pathlib import Path
from typing import Optional


def get_python_version() -> str:
    """Get Python version string.
    
    Returns:
        Python version (e.g., "3.11.5")
    """
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def get_hostname() -> str:
    """Get system hostname.
    
    Returns:
        Hostname or "unknown"
    """
    try:
        return platform.node() or "unknown"
    except Exception:
        return "unknown"


def get_caller_context(skip_frames: int = 0) -> dict:
    """Extract caller context from stack.
    
    Matches TypeScript _meta.path structure with:
    - fullFilePath
    - fileName
    - fileLine
    - fileColumn
    - method
    - etc.
    
    Args:
        skip_frames: Number of additional frames to skip
    
    Returns:
        Dictionary with path metadata
    """
    try:
        # Skip this function and the caller's frame by default
        # skip_frames allows additional skipping
        frame = inspect.currentframe()
        if frame is None:
            return _empty_context()
        
        # Skip: get_caller_context -> _emit -> trace/debug/info/etc
        for _ in range(3 + skip_frames):
            if frame.f_back is None:
                break
            frame = frame.f_back
        
        if frame is None:
            return _empty_context()
        
        # Extract info
        file_path = frame.f_code.co_filename
        line_number = frame.f_lineno
        function_name = frame.f_code.co_name
        
        # Convert to absolute path
        try:
            abs_path = str(Path(file_path).resolve())
        except Exception:
            abs_path = file_path
        
        # Get just the filename
        file_name = os.path.basename(file_path)
        
        # Build path metadata matching tslog format
        return {
            "fullFilePath": f"file://{abs_path}:{line_number}:0",
            "fileName": file_name,
            "fileNameWithLine": f"{file_name}:{line_number}",
            "fileColumn": "0",  # Python doesn't easily provide column
            "fileLine": str(line_number),
            "filePath": file_path,
            "filePathWithLine": f"{file_path}:{line_number}",
            "method": function_name,
        }
    except Exception:
        return _empty_context()


def _empty_context() -> dict:
    """Return empty context for error cases.
    
    Returns:
        Dictionary with minimal path metadata
    """
    return {
        "fullFilePath": "unknown",
        "fileName": "unknown",
        "fileNameWithLine": "unknown",
        "fileColumn": "0",
        "fileLine": "0",
        "filePath": "unknown",
        "filePathWithLine": "unknown",
        "method": "unknown",
    }
