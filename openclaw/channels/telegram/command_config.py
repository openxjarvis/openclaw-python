"""Telegram /config command handler.

Mirrors TypeScript openclaw/src/auto-reply/reply/commands-config.ts.
Supports /config show [path], /config set path=value, /config unset path.

Access control:
  - Requires authorized sender (isAuthorizedSender).
  - Requires commands.config=true in the config.
  - Requires channels.<channelId>.configWrites=true for set/unset.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional
from ...config.paths import resolve_state_dir

logger = logging.getLogger(__name__)

# Pattern: /config set model.primary=google/gemini-3-pro-preview
_CONFIG_SET_RE = re.compile(r"^set\s+(\S+)\s*=\s*(.+)$", re.DOTALL)
# Pattern: /config unset model.primary
_CONFIG_UNSET_RE = re.compile(r"^unset\s+(\S+)$")
# Pattern: /config show [path]
_CONFIG_SHOW_RE = re.compile(r"^show(?:\s+(\S+))?$")


# ---------------------------------------------------------------------------
# Config path helpers — mirrors TS config-paths.ts
# ---------------------------------------------------------------------------

def _parse_config_path(path_str: str) -> list[str]:
    """Parse a dot-notation config path into a list of keys.

    e.g. "model.primary" → ["model", "primary"]
    Array indices are supported: "agents.list.0.id" → ["agents", "list", "0", "id"]
    """
    return [p for p in path_str.strip().split(".") if p]


def _get_config_value_at_path(config: dict, path: list[str]) -> Any:
    """Get a nested config value by path list."""
    node: Any = config
    for key in path:
        if isinstance(node, dict):
            node = node.get(key)
        elif isinstance(node, list):
            try:
                node = node[int(key)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return node


def _set_config_value_at_path(config: dict, path: list[str], value: Any) -> None:
    """Set a nested config value by path list, creating dicts as needed."""
    if not path:
        return
    node: Any = config
    for key in path[:-1]:
        if isinstance(node, dict):
            if key not in node or not isinstance(node[key], dict):
                node[key] = {}
            node = node[key]
        else:
            return
    if isinstance(node, dict):
        node[path[-1]] = value


def _unset_config_value_at_path(config: dict, path: list[str]) -> bool:
    """Remove a nested config value by path list.

    Returns True if the key was found and removed.
    """
    if not path:
        return False
    node: Any = config
    for key in path[:-1]:
        if isinstance(node, dict):
            node = node.get(key)
        else:
            return False
        if node is None:
            return False
    if isinstance(node, dict) and path[-1] in node:
        del node[path[-1]]
        return True
    return False


def _coerce_value(raw: str) -> Any:
    """Coerce a raw string value to the appropriate Python type.

    Mirrors TS behaviour: tries JSON parse first (handles numbers, booleans,
    null, arrays, objects), falls back to raw string.
    """
    raw = raw.strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


# ---------------------------------------------------------------------------
# Command parser — mirrors TS parseConfigCommand()
# ---------------------------------------------------------------------------

def parse_config_command(body: str) -> Optional[dict]:
    """Parse the body of a /config command.

    Returns a dict with keys:
      action: "show" | "set" | "unset" | "error"
      path:   str (for set/unset/show with path)
      value:  Any (for set only)
      message: str (for error only)
    Returns None if the body does not match any /config subcommand.
    """
    body = (body or "").strip()
    if not body or body == "config":
        return {"action": "show"}

    # Remove leading "config " prefix if present
    if body.startswith("config "):
        body = body[len("config "):].strip()

    m = _CONFIG_SHOW_RE.match(body)
    if m:
        return {"action": "show", "path": m.group(1)}

    m = _CONFIG_SET_RE.match(body)
    if m:
        return {"action": "set", "path": m.group(1), "value": _coerce_value(m.group(2))}

    m = _CONFIG_UNSET_RE.match(body)
    if m:
        return {"action": "unset", "path": m.group(1)}

    return {"action": "error", "message": f"Unknown /config subcommand: {body!r}. Use: show [path], set path=value, unset path"}


# ---------------------------------------------------------------------------
# Config write permission — mirrors TS resolveChannelConfigWrites()
# ---------------------------------------------------------------------------

def _resolve_channel_config_writes(cfg: dict, channel_id: Optional[str]) -> bool:
    """Return True if config writes are enabled for this channel."""
    if not channel_id:
        return False
    channels = cfg.get("channels") or {}
    if isinstance(channels, dict):
        ch = channels.get(channel_id) or {}
        if isinstance(ch, dict):
            return bool(ch.get("configWrites") or ch.get("config_writes"))
    return False


# ---------------------------------------------------------------------------
# Config read/write helpers
# ---------------------------------------------------------------------------

def _get_config_path() -> Path:
    """Resolve the openclaw.json config file path."""
    p = resolve_state_dir() / "openclaw.json"
    if p.exists():
        return p
    fallback = resolve_state_dir() / "config.json"
    if fallback.exists():
        return fallback
    return p


def _read_config_file() -> Optional[dict]:
    """Read and parse the config file. Returns None on error."""
    path = _get_config_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.error("config read error: %s", exc)
        return None


def _write_config_file(cfg: dict) -> None:
    """Write config to disk (atomic temp-file rename)."""
    import os
    import tempfile
    path = _get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".openclaw-", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Main handler — mirrors TS handleConfigCommand()
# ---------------------------------------------------------------------------

async def handle_config_command(
    *,
    command_body: str,
    is_authorized_sender: bool,
    channel_id: Optional[str],
    cfg: dict,
) -> Optional[dict]:
    """Handle a /config command received via Telegram.

    Returns a result dict with optional 'reply_text' key, or None to pass through.
    Mirrors TS handleConfigCommand().

    Args:
        command_body: The text after "/config" (e.g. "show model.primary").
        is_authorized_sender: Whether the sender is on the allow-list.
        channel_id: Normalized channel ID (e.g. "telegram").
        cfg: Current OpenClaw config dict.
    """
    parsed = parse_config_command(command_body)
    if not parsed:
        return None

    if not is_authorized_sender:
        logger.debug("Ignoring /config from unauthorized sender")
        return {"should_continue": False}

    # Check commands.config=true
    commands_cfg = cfg.get("commands") or {}
    if isinstance(commands_cfg, dict) and commands_cfg.get("config") is not True:
        return {
            "should_continue": False,
            "reply_text": "⚠️ /config is disabled. Set commands.config=true to enable.",
        }

    if parsed["action"] == "error":
        return {
            "should_continue": False,
            "reply_text": f"⚠️ {parsed['message']}",
        }

    # For set/unset: verify channel config writes permission
    if parsed["action"] in ("set", "unset"):
        if not _resolve_channel_config_writes(cfg, channel_id):
            label = channel_id or "this channel"
            hint = (
                f"channels.{channel_id}.configWrites=true"
                if channel_id
                else "channels.<channel>.configWrites=true"
            )
            return {
                "should_continue": False,
                "reply_text": (
                    f"⚠️ Config writes are disabled for {label}. Set {hint} to enable."
                ),
            }

    # Read current config
    config_data = _read_config_file()
    if config_data is None:
        return {
            "should_continue": False,
            "reply_text": "⚠️ Config file is invalid or missing; fix it before using /config.",
        }

    # ---- show ----
    if parsed["action"] == "show":
        path_str = parsed.get("path")
        if path_str:
            parts = _parse_config_path(path_str)
            value = _get_config_value_at_path(config_data, parts)
            rendered = json.dumps(value if value is not None else None, indent=2, ensure_ascii=False)
            return {
                "should_continue": False,
                "reply_text": f"⚙️ Config {path_str}:\n```json\n{rendered}\n```",
            }
        rendered = json.dumps(config_data, indent=2, ensure_ascii=False)
        return {
            "should_continue": False,
            "reply_text": f"⚙️ Config (raw):\n```json\n{rendered}\n```",
        }

    # ---- unset ----
    if parsed["action"] == "unset":
        path_str = parsed["path"]
        parts = _parse_config_path(path_str)
        removed = _unset_config_value_at_path(config_data, parts)
        if not removed:
            return {
                "should_continue": False,
                "reply_text": f"⚙️ No config value found for {path_str}.",
            }
        try:
            _write_config_file(config_data)
        except Exception as exc:
            return {
                "should_continue": False,
                "reply_text": f"⚠️ Failed to write config: {exc}",
            }
        return {
            "should_continue": False,
            "reply_text": f"⚙️ Config updated: {path_str} removed.",
        }

    # ---- set ----
    if parsed["action"] == "set":
        path_str = parsed["path"]
        value = parsed["value"]
        parts = _parse_config_path(path_str)
        _set_config_value_at_path(config_data, parts, value)
        try:
            _write_config_file(config_data)
        except Exception as exc:
            return {
                "should_continue": False,
                "reply_text": f"⚠️ Failed to write config: {exc}",
            }
        value_label = f'"{value}"' if isinstance(value, str) else json.dumps(value)
        return {
            "should_continue": False,
            "reply_text": f"⚙️ Config updated: {path_str}={value_label}",
        }

    return None
