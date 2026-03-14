"""Cron run log configuration and utilities.

Mirrors TypeScript openclaw/src/cron/run-log.ts
Provides functions for resolving run log pruning options.
"""
from typing import Any


def parse_byte_size(size_str: str) -> int:
    """Parse byte size string like '2mb', '1gb' to bytes.
    
    Mirrors TS parseByteSize() logic from src/cron/run-log.ts
    
    Args:
        size_str: Size string like "2mb", "1gb", "1024", "100kb"
        
    Returns:
        Size in bytes
        
    Raises:
        ValueError: If format is invalid
        
    Example:
        >>> parse_byte_size("2mb")
        2097152
        >>> parse_byte_size("1gb")
        1073741824
        >>> parse_byte_size("1024")
        1024
    """
    size_str = size_str.strip().lower()
    
    # Extract number and unit
    import re
    match = re.match(r'^(\d+(?:\.\d+)?)\s*([kmgt]?b)?$', size_str)
    if not match:
        raise ValueError(f"Invalid byte size: {size_str}")
    
    num = float(match.group(1))
    unit = match.group(2) or 'b'
    
    multipliers = {
        'b': 1,
        'kb': 1024,
        'mb': 1024 ** 2,
        'gb': 1024 ** 3,
        'tb': 1024 ** 4,
    }
    
    return int(num * multipliers[unit])


def resolve_cron_run_log_prune_options(
    run_log_config: dict[str, Any] | None
) -> dict[str, int]:
    """Resolve run log pruning options from config.
    
    Mirrors TS resolveCronRunLogPruneOptions() from src/cron/run-log.ts:81-99
    
    Args:
        run_log_config: The runLog section of cron config
        
    Returns:
        Dict with 'max_bytes' and 'keep_lines' keys
        
    Example:
        >>> resolve_cron_run_log_prune_options({"maxBytes": "2mb", "keepLines": 1000})
        {'max_bytes': 2097152, 'keep_lines': 1000}
        
        >>> resolve_cron_run_log_prune_options({"maxBytes": 5000000})
        {'max_bytes': 5000000, 'keep_lines': 2000}
        
        >>> resolve_cron_run_log_prune_options(None)
        {'max_bytes': 2000000, 'keep_lines': 2000}
    """
    max_bytes = 2_000_000  # Default: 2MB
    keep_lines = 2_000     # Default: 2000 lines
    
    if not run_log_config:
        return {"max_bytes": max_bytes, "keep_lines": keep_lines}
    
    # Parse maxBytes
    if "maxBytes" in run_log_config:
        raw = run_log_config["maxBytes"]
        try:
            if isinstance(raw, int):
                max_bytes = raw
            elif isinstance(raw, str):
                max_bytes = parse_byte_size(raw)
        except (ValueError, TypeError):
            # Use default on parse error (matches TS behavior)
            pass
    
    # Parse keepLines
    if "keepLines" in run_log_config:
        raw = run_log_config["keepLines"]
        if isinstance(raw, (int, float)) and raw > 0:
            keep_lines = int(raw)
    
    return {"max_bytes": max_bytes, "keep_lines": keep_lines}
