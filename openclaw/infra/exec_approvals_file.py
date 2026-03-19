"""
Exec approvals file management — mirrors TS src/infra/exec-approvals.ts

Manages ~/.openclaw/exec-approvals.json for persistent exec command approval
and provides policy resolution (security/ask/askFallback) for the exec tool.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import uuid
from pathlib import Path
from typing import Any, Literal
from ..config.paths import resolve_state_dir

# ──────────────────────────────────────────────────────────────────────────────
# Types
# ──────────────────────────────────────────────────────────────────────────────

ExecHost = Literal["sandbox", "gateway", "node"]
ExecSecurity = Literal["deny", "allowlist", "full"]
ExecAsk = Literal["off", "on-miss", "always"]
ExecApprovalDecision = Literal["allow-once", "allow-always", "deny"]

DEFAULT_EXEC_APPROVAL_TIMEOUT_MS = 120_000

_DEFAULT_SECURITY: ExecSecurity = "deny"
_DEFAULT_ASK: ExecAsk = "on-miss"
_DEFAULT_ASK_FALLBACK: ExecSecurity = "deny"
_DEFAULT_AUTO_ALLOW_SKILLS = False
_DEFAULT_AGENT_ID = "main"

_SECURITY_ORDER: dict[str, int] = {"deny": 0, "allowlist": 1, "full": 2}
_ASK_ORDER: dict[str, int] = {"off": 0, "on-miss": 1, "always": 2}


# ──────────────────────────────────────────────────────────────────────────────
# Path helpers
# ──────────────────────────────────────────────────────────────────────────────

def resolve_exec_approvals_path() -> str:
    """Return path to exec-approvals.json (mirrors TS resolveExecApprovalsPath)."""
    env_path = os.environ.get("OPENCLAW_EXEC_APPROVALS_PATH")
    if env_path:
        return env_path
    return str(resolve_state_dir() / "exec-approvals.json")


def resolve_exec_approvals_socket_path() -> str:
    """Return path to the approval UNIX socket (mirrors TS resolveExecApprovalsSocketPath)."""
    return str(resolve_state_dir() / "exec-approvals.sock")


# ──────────────────────────────────────────────────────────────────────────────
# Internal normalisation helpers
# ──────────────────────────────────────────────────────────────────────────────

def _hash_raw(raw: str | None) -> str:
    return hashlib.sha256((raw or "").encode()).hexdigest()


def _normalize_security(value: Any, fallback: ExecSecurity) -> ExecSecurity:
    if value in ("allowlist", "full", "deny"):
        return value  # type: ignore[return-value]
    return fallback


def _normalize_ask(value: Any, fallback: ExecAsk) -> ExecAsk:
    if value in ("always", "off", "on-miss"):
        return value  # type: ignore[return-value]
    return fallback


def _normalize_allowlist_pattern(value: str | None) -> str | None:
    trimmed = (value or "").strip()
    return trimmed.lower() if trimmed else None


def _coerce_allowlist_entries(allowlist: Any) -> list[dict] | None:
    """Coerce legacy/corrupted allowlists (strings, invalid entries) to proper dicts."""
    if not isinstance(allowlist, list):
        return None
    if len(allowlist) == 0:
        return allowlist
    result: list[dict] = []
    changed = False
    for item in allowlist:
        if isinstance(item, str):
            trimmed = item.strip()
            if trimmed:
                result.append({"pattern": trimmed})
                changed = True
        elif isinstance(item, dict):
            pattern = item.get("pattern")
            if isinstance(pattern, str) and pattern.strip():
                result.append(item)
            else:
                changed = True  # dropped invalid entry
        else:
            changed = True
    return result if changed else allowlist  # type: ignore[return-value]


def _ensure_allowlist_ids(allowlist: list[dict] | None) -> list[dict] | None:
    if not allowlist:
        return allowlist
    changed = False
    result: list[dict] = []
    for entry in allowlist:
        if entry.get("id"):
            result.append(entry)
        else:
            result.append({**entry, "id": str(uuid.uuid4())})
            changed = True
    return result if changed else allowlist


def normalize_exec_approvals(file: dict) -> dict:
    """Normalise exec-approvals.json dict (mirrors TS normalizeExecApprovals)."""
    agents: dict[str, Any] = dict(file.get("agents") or {})

    # Migrate legacy 'default' key to _DEFAULT_AGENT_ID
    legacy = agents.pop("default", None)
    if legacy and isinstance(legacy, dict):
        main = agents.get(_DEFAULT_AGENT_ID) or {}
        merged_allowlist: list[dict] = []
        seen: set[str] = set()
        for entry in list(main.get("allowlist") or []) + list(legacy.get("allowlist") or []):
            key = _normalize_allowlist_pattern(entry.get("pattern"))
            if key and key not in seen:
                seen.add(key)
                merged_allowlist.append(entry)
        agents[_DEFAULT_AGENT_ID] = {
            "security": main.get("security") or legacy.get("security"),
            "ask": main.get("ask") or legacy.get("ask"),
            "askFallback": main.get("askFallback") or legacy.get("askFallback"),
            "autoAllowSkills": (
                main.get("autoAllowSkills")
                if main.get("autoAllowSkills") is not None
                else legacy.get("autoAllowSkills")
            ),
            "allowlist": merged_allowlist or None,
        }

    for key, agent in list(agents.items()):
        if not isinstance(agent, dict):
            continue
        coerced = _coerce_allowlist_entries(agent.get("allowlist"))
        with_ids = _ensure_allowlist_ids(coerced)
        if with_ids is not agent.get("allowlist"):
            agents[key] = {**agent, "allowlist": with_ids}

    socket_info = file.get("socket") or {}
    socket_path = str(socket_info.get("path") or "").strip()
    socket_token = str(socket_info.get("token") or "").strip()
    raw_defaults = file.get("defaults") or {}

    return {
        "version": 1,
        "socket": {
            "path": socket_path or None,
            "token": socket_token or None,
        },
        "defaults": {
            "security": raw_defaults.get("security"),
            "ask": raw_defaults.get("ask"),
            "askFallback": raw_defaults.get("askFallback"),
            "autoAllowSkills": raw_defaults.get("autoAllowSkills"),
        },
        "agents": agents,
    }


# ──────────────────────────────────────────────────────────────────────────────
# File I/O
# ──────────────────────────────────────────────────────────────────────────────

def load_exec_approvals() -> dict:
    """Load + normalise exec-approvals.json (mirrors TS loadExecApprovals)."""
    path = resolve_exec_approvals_path()
    try:
        if not os.path.exists(path):
            return normalize_exec_approvals({"version": 1, "agents": {}})
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or parsed.get("version") != 1:
            return normalize_exec_approvals({"version": 1, "agents": {}})
        return normalize_exec_approvals(parsed)
    except Exception:
        return normalize_exec_approvals({"version": 1, "agents": {}})


def read_exec_approvals_snapshot() -> dict:
    """Read snapshot with hash (mirrors TS readExecApprovalsSnapshot)."""
    path = resolve_exec_approvals_path()
    if not os.path.exists(path):
        file = normalize_exec_approvals({"version": 1, "agents": {}})
        return {"path": path, "exists": False, "raw": None, "file": file, "hash": _hash_raw(None)}
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        parsed = json.loads(raw)
        file = (
            normalize_exec_approvals(parsed)
            if isinstance(parsed, dict) and parsed.get("version") == 1
            else normalize_exec_approvals({"version": 1, "agents": {}})
        )
    except Exception:
        file = normalize_exec_approvals({"version": 1, "agents": {}})
        raw = None
    return {"path": path, "exists": True, "raw": raw, "file": file, "hash": _hash_raw(raw)}


def save_exec_approvals(file: dict) -> None:
    """Save exec-approvals.json atomically with 0o600 permissions (mirrors TS saveExecApprovals)."""
    path = resolve_exec_approvals_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    content = json.dumps(file, indent=2) + "\n"
    import tempfile as _tempfile
    dir_ = os.path.dirname(path)
    fd, tmp = _tempfile.mkstemp(dir=dir_, prefix=".exec-approvals-tmp-")
    try:
        os.write(fd, content.encode())
        os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ensure_exec_approvals() -> dict:
    """Load, normalise, and ensure socket/token fields exist (mirrors TS ensureExecApprovals)."""
    loaded = load_exec_approvals()
    socket_info = loaded.get("socket") or {}
    socket_path = str(socket_info.get("path") or "").strip()
    token = str(socket_info.get("token") or "").strip()
    updated = {
        **loaded,
        "socket": {
            "path": socket_path or resolve_exec_approvals_socket_path(),
            "token": token or secrets.token_urlsafe(24),
        },
    }
    save_exec_approvals(updated)
    return updated


# ──────────────────────────────────────────────────────────────────────────────
# Policy resolution
# ──────────────────────────────────────────────────────────────────────────────

def resolve_exec_approvals(
    agent_id: str | None = None,
    overrides: dict | None = None,
) -> dict:
    """Resolve full exec-approvals context for an agent (mirrors TS resolveExecApprovals)."""
    file = ensure_exec_approvals()
    socket_info = file.get("socket") or {}
    return resolve_exec_approvals_from_file(
        file=file,
        agent_id=agent_id,
        overrides=overrides,
        path=resolve_exec_approvals_path(),
        socket_path=socket_info.get("path") or resolve_exec_approvals_socket_path(),
        token=socket_info.get("token") or "",
    )


def resolve_exec_approvals_from_file(
    *,
    file: dict,
    agent_id: str | None = None,
    overrides: dict | None = None,
    path: str | None = None,
    socket_path: str | None = None,
    token: str | None = None,
) -> dict:
    """Resolve exec-approvals for an agent from a file dict (mirrors TS resolveExecApprovalsFromFile)."""
    overrides = overrides or {}
    file = normalize_exec_approvals(file)
    defaults = file.get("defaults") or {}
    agent_key = agent_id or _DEFAULT_AGENT_ID
    agents = file.get("agents") or {}
    agent = agents.get(agent_key) or {}
    wildcard = agents.get("*") or {}

    fallback_security: ExecSecurity = overrides.get("security") or _DEFAULT_SECURITY
    fallback_ask: ExecAsk = overrides.get("ask") or _DEFAULT_ASK
    fallback_ask_fallback: ExecSecurity = overrides.get("askFallback") or _DEFAULT_ASK_FALLBACK
    fallback_auto: bool = bool(overrides.get("autoAllowSkills") or _DEFAULT_AUTO_ALLOW_SKILLS)

    resolved_defaults = {
        "security": _normalize_security(defaults.get("security"), fallback_security),
        "ask": _normalize_ask(defaults.get("ask"), fallback_ask),
        "askFallback": _normalize_security(
            defaults.get("askFallback") or fallback_ask_fallback, fallback_ask_fallback
        ),
        "autoAllowSkills": bool(
            defaults.get("autoAllowSkills")
            if defaults.get("autoAllowSkills") is not None
            else fallback_auto
        ),
    }
    resolved_agent = {
        "security": _normalize_security(
            agent.get("security") or wildcard.get("security") or resolved_defaults["security"],
            resolved_defaults["security"],
        ),
        "ask": _normalize_ask(
            agent.get("ask") or wildcard.get("ask") or resolved_defaults["ask"],
            resolved_defaults["ask"],
        ),
        "askFallback": _normalize_security(
            agent.get("askFallback")
            or wildcard.get("askFallback")
            or resolved_defaults["askFallback"],
            resolved_defaults["askFallback"],
        ),
        "autoAllowSkills": bool(
            agent.get("autoAllowSkills")
            if agent.get("autoAllowSkills") is not None
            else (
                wildcard.get("autoAllowSkills")
                if wildcard.get("autoAllowSkills") is not None
                else resolved_defaults["autoAllowSkills"]
            )
        ),
    }
    allowlist: list[dict] = [
        *(wildcard.get("allowlist") or []),
        *(agent.get("allowlist") or []),
    ]
    return {
        "path": path or resolve_exec_approvals_path(),
        "socketPath": socket_path or resolve_exec_approvals_socket_path(),
        "token": token or "",
        "defaults": resolved_defaults,
        "agent": resolved_agent,
        "allowlist": allowlist,
        "file": file,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Security policy helpers
# ──────────────────────────────────────────────────────────────────────────────

def min_security(a: ExecSecurity, b: ExecSecurity) -> ExecSecurity:
    """Return the more restrictive (lower) security level (mirrors TS minSecurity)."""
    return a if _SECURITY_ORDER.get(a, 0) <= _SECURITY_ORDER.get(b, 0) else b


def max_ask(a: ExecAsk, b: ExecAsk) -> ExecAsk:
    """Return the more permissive (higher) ask level (mirrors TS maxAsk)."""
    return a if _ASK_ORDER.get(a, 0) >= _ASK_ORDER.get(b, 0) else b


def requires_exec_approval(
    *,
    ask: ExecAsk,
    security: ExecSecurity,
    analysis_ok: bool,
    allowlist_satisfied: bool,
) -> bool:
    """Return True if human approval is needed (mirrors TS requiresExecApproval)."""
    return ask == "always" or (
        ask == "on-miss"
        and security == "allowlist"
        and (not analysis_ok or not allowlist_satisfied)
    )


# ──────────────────────────────────────────────────────────────────────────────
# Allowlist mutations
# ──────────────────────────────────────────────────────────────────────────────

def add_allowlist_entry(file: dict, agent_id: str | None, pattern: str) -> None:
    """Add a pattern to the per-agent allowlist and save (mirrors TS addAllowlistEntry)."""
    import time as _time
    trimmed = pattern.strip()
    if not trimmed:
        return
    target = agent_id or _DEFAULT_AGENT_ID
    agents: dict = file.setdefault("agents", {})
    existing: dict = agents.setdefault(target, {})
    allowlist: list[dict] = existing.setdefault("allowlist", [])
    if any(e.get("pattern") == trimmed for e in allowlist):
        return
    allowlist.append({
        "id": str(uuid.uuid4()),
        "pattern": trimmed,
        "lastUsedAt": int(_time.time() * 1000),
    })
    save_exec_approvals(file)


def record_allowlist_use(
    file: dict,
    agent_id: str | None,
    entry: dict,
    command: str,
    resolved_path: str | None = None,
) -> None:
    """Update lastUsedAt/lastUsedCommand on a matched entry and save (mirrors TS recordAllowlistUse)."""
    import time as _time
    target = agent_id or _DEFAULT_AGENT_ID
    agents: dict = file.setdefault("agents", {})
    existing: dict = agents.setdefault(target, {})
    allowlist: list[dict] = list(existing.get("allowlist") or [])
    updated: list[dict] = []
    for item in allowlist:
        if item.get("pattern") == entry.get("pattern"):
            updated.append({
                **item,
                "id": item.get("id") or str(uuid.uuid4()),
                "lastUsedAt": int(_time.time() * 1000),
                "lastUsedCommand": command,
                "lastResolvedPath": resolved_path,
            })
        else:
            updated.append(item)
    existing["allowlist"] = updated
    save_exec_approvals(file)


# ──────────────────────────────────────────────────────────────────────────────
# UNIX socket approval client
# ──────────────────────────────────────────────────────────────────────────────

async def request_exec_approval_via_socket(
    *,
    socket_path: str,
    token: str,
    request: dict,
    timeout_ms: int = 15_000,
) -> ExecApprovalDecision | None:
    """
    Send an approval request over a UNIX socket and wait for decision.
    Mirrors TS requestExecApprovalViaSocket() / requestJsonlSocket().

    Protocol (JSONL over Unix domain socket):
      → {"type":"request","token":"<bearer>","id":"<uuid>","request":{...}}
      ← {"type":"decision","decision":"allow-once"|"allow-always"|"deny"}
    """
    if not socket_path or not token:
        return None

    payload = json.dumps({
        "type": "request",
        "token": token,
        "id": str(uuid.uuid4()),
        "request": request,
    }) + "\n"

    connect_timeout = min(15.0, timeout_ms / 1000)
    total_timeout = timeout_ms / 1000

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(socket_path),
            timeout=connect_timeout,
        )
    except (OSError, asyncio.TimeoutError, AttributeError):
        # open_unix_connection not available on Windows or socket doesn't exist
        return None

    try:
        writer.write(payload.encode())
        await writer.drain()

        loop = asyncio.get_event_loop()
        deadline = loop.time() + total_timeout
        while loop.time() < deadline:
            remaining = deadline - loop.time()
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=min(remaining, 2.0))
            except asyncio.TimeoutError:
                continue
            if not line:
                break
            try:
                msg = json.loads(line.decode())
                if msg.get("type") == "decision" and msg.get("decision"):
                    decision = msg["decision"]
                    if decision in ("allow-once", "allow-always", "deny"):
                        return decision  # type: ignore[return-value]
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    return None


__all__ = [
    "ExecHost",
    "ExecSecurity",
    "ExecAsk",
    "ExecApprovalDecision",
    "DEFAULT_EXEC_APPROVAL_TIMEOUT_MS",
    "resolve_exec_approvals_path",
    "resolve_exec_approvals_socket_path",
    "normalize_exec_approvals",
    "load_exec_approvals",
    "read_exec_approvals_snapshot",
    "save_exec_approvals",
    "ensure_exec_approvals",
    "resolve_exec_approvals",
    "resolve_exec_approvals_from_file",
    "min_security",
    "max_ask",
    "requires_exec_approval",
    "add_allowlist_entry",
    "record_allowlist_use",
    "request_exec_approval_via_socket",
]
