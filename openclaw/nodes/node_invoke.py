"""Node invoke handler — mirrors src/node-host/invoke.ts (core parts)"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from ..config.paths import resolve_state_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (mirrors invoke.ts)
# ---------------------------------------------------------------------------

OUTPUT_CAP = 200_000
OUTPUT_EVENT_TAIL = 20_000
DEFAULT_NODE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

BLOCKED_ENV_KEYS: frozenset[str] = frozenset([
    "NODE_OPTIONS",
    "PYTHONHOME",
    "PYTHONPATH",
    "PERL5LIB",
    "PERL5OPT",
    "RUBYOPT",
])

BLOCKED_ENV_PREFIXES: tuple[str, ...] = ("DYLD_", "LD_")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class NodeInvokeRequestPayload:
    id: str
    node_id: str
    command: str
    params_json: str | None = None
    timeout_ms: int | None = None
    idempotency_key: str | None = None


class SkillBinsProvider(Protocol):
    async def current(self, force: bool = False) -> set[str]: ...


# ---------------------------------------------------------------------------
# Env sanitization
# ---------------------------------------------------------------------------

def sanitize_env(overrides: dict[str, str] | None) -> dict[str, str] | None:
    """Remove dangerous env var keys from overrides.

    Mirrors sanitizeEnv() from invoke.ts.
    """
    if not overrides:
        return None

    merged: dict[str, str] = {k: v for k, v in os.environ.items() if isinstance(v, str)}
    for raw_key, value in overrides.items():
        key = raw_key.strip()
        if not key:
            continue
        upper = key.upper()
        if upper == "PATH":
            continue
        if upper in BLOCKED_ENV_KEYS:
            continue
        if any(upper.startswith(p) for p in BLOCKED_ENV_PREFIXES):
            continue
        merged[key] = value

    return merged


# ---------------------------------------------------------------------------
# Output truncation
# ---------------------------------------------------------------------------

def _truncate_output(raw: str, max_chars: int) -> tuple[str, bool]:
    if len(raw) <= max_chars:
        return raw, False
    return f"... (truncated) {raw[len(raw) - max_chars:]}", True


# ---------------------------------------------------------------------------
# Payload coercion
# ---------------------------------------------------------------------------

def coerce_node_invoke_payload(payload: Any) -> NodeInvokeRequestPayload | None:
    """Validate and normalize a raw node invoke payload.

    Mirrors coerceNodeInvokePayload() from invoke.ts.
    """
    if not payload or not isinstance(payload, dict):
        return None

    invoke_id = payload.get("id", "")
    if not isinstance(invoke_id, str):
        return None
    invoke_id = invoke_id.strip()

    node_id = payload.get("nodeId", "")
    if not isinstance(node_id, str):
        return None
    node_id = node_id.strip()

    command = payload.get("command", "")
    if not isinstance(command, str):
        return None
    command = command.strip()

    if not invoke_id or not node_id or not command:
        return None

    params_json: str | None = None
    if isinstance(payload.get("paramsJSON"), str):
        params_json = payload["paramsJSON"]
    elif payload.get("params") is not None:
        try:
            params_json = json.dumps(payload["params"])
        except Exception:
            pass

    timeout_ms: int | None = None
    if isinstance(payload.get("timeoutMs"), (int, float)):
        timeout_ms = int(payload["timeoutMs"])

    idempotency_key: str | None = None
    if isinstance(payload.get("idempotencyKey"), str):
        idempotency_key = payload["idempotencyKey"]

    return NodeInvokeRequestPayload(
        id=invoke_id,
        node_id=node_id,
        command=command,
        params_json=params_json,
        timeout_ms=timeout_ms,
        idempotency_key=idempotency_key,
    )


# ---------------------------------------------------------------------------
# Result builder
# ---------------------------------------------------------------------------

def build_node_invoke_result_params(
    frame: NodeInvokeRequestPayload,
    result: dict,
) -> dict:
    """Build the params for a node.invoke.result RPC call.

    Mirrors buildNodeInvokeResultParams() from invoke.ts.
    """
    params: dict = {
        "id": frame.id,
        "nodeId": frame.node_id,
        "ok": result.get("ok", False),
    }
    if result.get("payload") is not None:
        params["payload"] = result["payload"]
    if isinstance(result.get("payloadJSON"), str):
        params["payloadJSON"] = result["payloadJSON"]
    if result.get("error"):
        params["error"] = result["error"]
    return params


# ---------------------------------------------------------------------------
# system.run handler
# ---------------------------------------------------------------------------

async def _handle_system_run(params: dict, skill_bins: SkillBinsProvider | None) -> dict:
    """Execute system.run — runs a command in a subprocess."""
    command: list[str] = params.get("command") or []
    cwd: str | None = params.get("cwd")
    env_overrides: dict | None = params.get("env")
    timeout_ms: int | None = params.get("timeoutMs")
    approved: bool = bool(params.get("approved", False))

    if not command or not isinstance(command, list):
        return {"ok": False, "error": {"code": "INVALID_COMMAND", "message": "command must be a non-empty array"}}

    # Basic security: check against skill bins if provided
    if skill_bins:
        try:
            bins = await skill_bins.current()
            bin_name = os.path.basename(command[0])
            if bins and bin_name not in bins and not approved:
                return {
                    "ok": False,
                    "error": {
                        "code": "NOT_APPROVED",
                        "message": f"command not in approved bins: {bin_name}",
                    },
                }
        except Exception as e:
            logger.warning(f"[node_invoke] skill_bins.current() failed: {e}")

    sanitized_env = sanitize_env(env_overrides)
    timeout_s = max(0.1, (timeout_ms or 30_000) / 1000.0)

    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=sanitized_env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            timed_out = False
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return {
                "ok": False,
                "payload": {
                    "exitCode": None,
                    "timedOut": True,
                    "success": False,
                    "stdout": "",
                    "stderr": "",
                    "error": f"command timed out after {timeout_ms}ms",
                    "truncated": False,
                },
            }

        stdout_raw = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr_raw = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        stdout_txt, trunc_out = _truncate_output(stdout_raw, OUTPUT_CAP)
        stderr_txt, trunc_err = _truncate_output(stderr_raw, OUTPUT_CAP)

        exit_code = proc.returncode if proc.returncode is not None else -1
        success = exit_code == 0

        return {
            "ok": True,
            "payload": {
                "exitCode": exit_code,
                "timedOut": False,
                "success": success,
                "stdout": stdout_txt,
                "stderr": stderr_txt,
                "truncated": trunc_out or trunc_err,
            },
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "error": {"code": "NOT_FOUND", "message": f"command not found: {command[0]}"},
        }
    except Exception as e:
        return {"ok": False, "error": {"code": "EXEC_ERROR", "message": str(e)}}


# ---------------------------------------------------------------------------
# system.which handler
# ---------------------------------------------------------------------------

def _handle_system_which(params: dict) -> dict:
    bins_param = params.get("bins") or []
    found: dict[str, str | None] = {}
    for b in bins_param:
        if not isinstance(b, str) or not b.strip():
            continue
        found[b.strip()] = shutil.which(b.strip()) or None
    return {"ok": True, "payload": found}


# ---------------------------------------------------------------------------
# system.execApprovals helpers
# ---------------------------------------------------------------------------

def _resolve_exec_approvals_path() -> str:
    """Return the path to the exec-approvals JSON file.

    Mirrors TS resolveExecApprovalsPath (defaults to ~/.openclaw/exec-approvals.json).
    """
    env_path = os.environ.get("OPENCLAW_EXEC_APPROVALS_PATH")
    if env_path:
        return env_path
    return str(resolve_state_dir() / "exec-approvals.json")


def _hash_file_contents(path: str) -> str:
    """SHA-256 hash of file contents (hex), or empty string if file missing."""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


def _read_exec_approvals_snapshot() -> dict:
    """Read exec approvals file and return a snapshot dict.

    Mirrors TS readExecApprovalsSnapshot.
    """
    path = _resolve_exec_approvals_path()
    exists = os.path.isfile(path)
    file_content: dict = {}
    if exists:
        try:
            with open(path) as fh:
                file_content = json.load(fh)
        except Exception:
            file_content = {}
    return {
        "path": path,
        "exists": exists,
        "hash": _hash_file_contents(path) if exists else "",
        "file": file_content,
    }


def _redact_exec_approvals(file: dict) -> dict:
    """Redact sensitive socket paths from exec approvals file.

    Mirrors TS redactExecApprovals.
    """
    if not isinstance(file, dict):
        return file
    redacted = dict(file)
    if "socketPath" in redacted:
        redacted["socketPath"] = "[redacted]"
    return redacted


def _save_exec_approvals(file: dict) -> None:
    """Write the exec approvals file.

    Mirrors TS saveExecApprovals.
    """
    path = _resolve_exec_approvals_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(file, fh, indent=2)


async def _handle_exec_approvals_get() -> dict:
    """Handle system.execApprovals.get — returns the approvals snapshot."""
    try:
        snapshot = _read_exec_approvals_snapshot()
        return {
            "ok": True,
            "payload": {
                "path": snapshot["path"],
                "exists": snapshot["exists"],
                "hash": snapshot["hash"],
                "file": _redact_exec_approvals(snapshot["file"]),
            },
        }
    except Exception as exc:
        msg = str(exc)
        code = "TIMEOUT" if "timed out" in msg.lower() else "INVALID_REQUEST"
        return {"ok": False, "error": {"code": code, "message": msg}}


async def _handle_exec_approvals_set(params_json: str | None) -> dict:
    """Handle system.execApprovals.set — writes updated exec approvals file."""
    try:
        if not params_json:
            raise ValueError("INVALID_REQUEST: paramsJSON required")
        params = json.loads(params_json)
        file_data = params.get("file")
        if not file_data or not isinstance(file_data, dict):
            raise ValueError("INVALID_REQUEST: exec approvals file required")
        base_hash = params.get("baseHash")
        if base_hash is not None:
            snapshot = _read_exec_approvals_snapshot()
            if base_hash != snapshot["hash"]:
                raise ValueError(
                    "CONFLICT: exec approvals file has been modified since last read"
                )
        _save_exec_approvals(file_data)
        snapshot = _read_exec_approvals_snapshot()
        return {
            "ok": True,
            "payload": {
                "path": snapshot["path"],
                "exists": snapshot["exists"],
                "hash": snapshot["hash"],
                "file": _redact_exec_approvals(snapshot["file"]),
            },
        }
    except Exception as exc:
        return {"ok": False, "error": {"code": "INVALID_REQUEST", "message": str(exc)}}


# ---------------------------------------------------------------------------
# Node event emission helpers
# ---------------------------------------------------------------------------

async def send_node_event(
    gateway_client: Any,
    event: str,
    payload: Any,
) -> None:
    """Send a node event to the gateway (best-effort, never raises).

    Mirrors TS sendNodeEvent from invoke.ts.
    """
    if not gateway_client or not hasattr(gateway_client, "request"):
        return
    try:
        await gateway_client.request("node.event", {
            "event": event,
            "payloadJSON": json.dumps(payload) if payload is not None else None,
        })
    except Exception:
        pass


def _build_exec_event_payload(
    session_key: str,
    run_id: str,
    command: str | None = None,
    exit_code: int | None = None,
    timed_out: bool | None = None,
    success: bool | None = None,
    output: str | None = None,
    reason: str | None = None,
) -> dict:
    """Build an exec event payload, truncating output to OUTPUT_EVENT_TAIL.

    Mirrors TS buildExecEventPayload.
    """
    payload: dict = {
        "sessionKey": session_key,
        "runId": run_id,
        "host": "node",
    }
    if command is not None:
        payload["command"] = command
    if exit_code is not None:
        payload["exitCode"] = exit_code
    if timed_out is not None:
        payload["timedOut"] = timed_out
    if success is not None:
        payload["success"] = success
    if reason is not None:
        payload["reason"] = reason
    if output:
        trimmed = output.strip()
        if trimmed:
            truncated, _ = _truncate_output(trimmed, OUTPUT_EVENT_TAIL)
            payload["output"] = truncated
    return payload


async def send_exec_finished_event(
    gateway_client: Any,
    session_key: str,
    run_id: str,
    cmd_text: str,
    result: dict,
) -> None:
    """Emit an exec.finished node event after a successful command run.

    Mirrors TS sendExecFinishedEvent.
    """
    combined = "\n".join(
        s for s in [
            result.get("stdout"),
            result.get("stderr"),
            result.get("error"),
        ] if s
    )
    await send_node_event(
        gateway_client,
        "exec.finished",
        _build_exec_event_payload(
            session_key=session_key,
            run_id=run_id,
            command=cmd_text,
            exit_code=result.get("exitCode"),
            timed_out=result.get("timedOut"),
            success=result.get("success"),
            output=combined or None,
        ),
    )


# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

async def handle_invoke(
    payload: NodeInvokeRequestPayload,
    gateway_client: Any,
    skill_bins: SkillBinsProvider | None = None,
) -> None:
    """Dispatch a node invoke request and send the result back.

    Mirrors handleInvoke() from invoke.ts.
    """
    command = payload.command

    # ---------- system.execApprovals.get ----------
    if command == "system.execApprovals.get":
        result = await _handle_exec_approvals_get()
        result_params = build_node_invoke_result_params(payload, result)
        if gateway_client and hasattr(gateway_client, "request"):
            try:
                await gateway_client.request("node.invoke.result", result_params)
            except Exception as e:
                logger.warning(f"[node_invoke] failed to send invoke result: {e}")
        return

    # ---------- system.execApprovals.set ----------
    if command == "system.execApprovals.set":
        result = await _handle_exec_approvals_set(payload.params_json)
        result_params = build_node_invoke_result_params(payload, result)
        if gateway_client and hasattr(gateway_client, "request"):
            try:
                await gateway_client.request("node.invoke.result", result_params)
            except Exception as e:
                logger.warning(f"[node_invoke] failed to send invoke result: {e}")
        return

    # ---------- browser.proxy ----------
    if command == "browser.proxy":
        try:
            from openclaw.nodes.invoke_browser import run_browser_proxy_command
            payload_json = await run_browser_proxy_command(payload.params_json)
            result = {"ok": True, "payloadJSON": payload_json}
        except Exception as exc:
            result = {
                "ok": False,
                "error": {"code": "INVALID_REQUEST", "message": str(exc)},
            }
        result_params = build_node_invoke_result_params(payload, result)
        if gateway_client and hasattr(gateway_client, "request"):
            try:
                await gateway_client.request("node.invoke.result", result_params)
            except Exception as e:
                logger.warning(f"[node_invoke] failed to send invoke result: {e}")
        return

    # ---------- remaining commands ----------
    raw_params: dict = {}
    if payload.params_json:
        try:
            raw_params = json.loads(payload.params_json)
        except json.JSONDecodeError:
            pass

    try:
        if command == "system.run":
            result = await _handle_system_run(raw_params, skill_bins)
        elif command == "system.which":
            result = _handle_system_which(raw_params)
        else:
            result = {
                "ok": False,
                "error": {
                    "code": "UNAVAILABLE",
                    "message": "command not supported",
                },
            }
    except Exception as e:
        result = {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}}

    result_params = build_node_invoke_result_params(payload, result)

    if gateway_client and hasattr(gateway_client, "request"):
        try:
            await gateway_client.request("node.invoke.result", result_params)
        except Exception as e:
            logger.warning(f"[node_invoke] failed to send invoke result: {e}")
