"""Process supervisor for OpenClaw Python.

Mirrors TypeScript src/process/supervisor/ implementation for managing
child processes with timeout, output capture, and graceful termination.
"""

import asyncio
import logging
import os
import platform
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Literal, Optional, Protocol, Union

logger = logging.getLogger("openclaw.process.supervisor")

# ============================================================================
# Types (mirroring types.ts)
# ============================================================================

RunState = Literal["starting", "running", "exiting", "exited"]

TerminationReason = Literal[
    "manual-cancel",
    "overall-timeout",
    "no-output-timeout",
    "spawn-error",
    "signal",
    "exit",
]


@dataclass
class RunRecord:
    """Record of a managed process run (mirrors TS RunRecord)."""

    run_id: str
    session_id: str
    backend_id: str
    scope_key: Optional[str]
    pid: Optional[int]
    process_group_id: Optional[int]
    started_at_ms: int
    last_output_at_ms: int
    created_at_ms: int
    updated_at_ms: int
    state: RunState
    termination_reason: Optional[TerminationReason] = None
    exit_code: Optional[int] = None
    exit_signal: Optional[str] = None


@dataclass
class RunExit:
    """Exit information from a managed run (mirrors TS RunExit)."""

    reason: TerminationReason
    exit_code: Optional[int]
    exit_signal: Optional[str]
    duration_ms: int
    stdout: str
    stderr: str
    timed_out: bool
    no_output_timed_out: bool


class ManagedRunStdin(Protocol):
    """Protocol for stdin of a managed run."""

    def write(self, data: str) -> None: ...
    def end(self) -> None: ...
    def destroy(self) -> None: ...


@dataclass
class ManagedRun:
    """A managed process run (mirrors TS ManagedRun)."""

    run_id: str
    pid: Optional[int]
    started_at_ms: int
    stdin: Optional[ManagedRunStdin]
    wait: Callable[[], asyncio.Future[RunExit]]
    cancel: Callable[[Optional[TerminationReason]], None]


# ============================================================================
# RunRegistry (mirroring registry.ts)
# ============================================================================

DEFAULT_MAX_EXITED_RECORDS = 2000


def _now_ms() -> int:
    """Get current time in milliseconds."""
    return int(time.time() * 1000)


class RunRegistry:
    """Registry for tracking process run records (mirrors TS RunRegistry)."""

    def __init__(self, max_exited_records: Optional[int] = None):
        self._records: Dict[str, RunRecord] = {}
        self._max_exited_records = self._resolve_max_exited_records(max_exited_records)

    def _resolve_max_exited_records(self, value: Optional[int]) -> int:
        if not isinstance(value, int) or value < 1:
            return DEFAULT_MAX_EXITED_RECORDS
        return max(1, value)

    def _prune_exited_records(self) -> None:
        """Remove oldest exited records if we exceed the limit."""
        if not self._records:
            return

        exited = sum(1 for r in self._records.values() if r.state == "exited")
        if exited <= self._max_exited_records:
            return

        remove_count = exited - self._max_exited_records
        for run_id, record in list(self._records.items()):
            if remove_count <= 0:
                break
            if record.state == "exited":
                del self._records[run_id]
                remove_count -= 1

    def add(self, record: RunRecord) -> None:
        """Add a new run record."""
        self._records[record.run_id] = record

    def get(self, run_id: str) -> Optional[RunRecord]:
        """Get a run record by ID."""
        return self._records.get(run_id)

    def list(self) -> List[RunRecord]:
        """List all run records."""
        return list(self._records.values())

    def list_by_scope(self, scope_key: str) -> List[RunRecord]:
        """List all run records for a given scope."""
        if not scope_key.strip():
            return []
        return [r for r in self._records.values() if r.scope_key == scope_key]

    def update_state(
        self,
        run_id: str,
        state: RunState,
        patch: Optional[Dict[str, Any]] = None,
    ) -> Optional[RunRecord]:
        """Update the state of a run record."""
        current = self._records.get(run_id)
        if not current:
            return None

        updated_at_ms = _now_ms()
        patch = patch or {}

        # Update fields
        current.state = state
        current.updated_at_ms = updated_at_ms
        if "pid" in patch:
            current.pid = patch["pid"]
        if "termination_reason" in patch:
            current.termination_reason = patch["termination_reason"]
        if "exit_code" in patch:
            current.exit_code = patch["exit_code"]
        if "exit_signal" in patch:
            current.exit_signal = patch["exit_signal"]

        return current

    def touch_output(self, run_id: str) -> None:
        """Update the last output timestamp for a run."""
        current = self._records.get(run_id)
        if not current:
            return
        ts = _now_ms()
        current.last_output_at_ms = ts
        current.updated_at_ms = ts

    def finalize(
        self,
        run_id: str,
        exit_info: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Finalize a run record with exit information."""
        current = self._records.get(run_id)
        if not current:
            return None

        first_finalize = current.state != "exited"
        ts = _now_ms()

        current.state = "exited"
        current.termination_reason = current.termination_reason or exit_info.get("reason")
        current.exit_code = current.exit_code if current.exit_code is not None else exit_info.get("exit_code")
        current.exit_signal = current.exit_signal if current.exit_signal is not None else exit_info.get("exit_signal")
        current.updated_at_ms = ts

        self._prune_exited_records()
        return {"record": current, "first_finalize": first_finalize}

    def delete(self, run_id: str) -> None:
        """Delete a run record."""
        self._records.pop(run_id, None)


# ============================================================================
# Process killing utilities (mirroring kill-tree.ts)
# ============================================================================

DEFAULT_GRACE_MS = 3000
MAX_GRACE_MS = 60000


def _normalize_grace_ms(value: Optional[int]) -> int:
    """Normalize grace period to valid range."""
    if not isinstance(value, int) or value < 0:
        return DEFAULT_GRACE_MS
    return max(0, min(MAX_GRACE_MS, value))


def _is_process_alive(pid: int) -> bool:
    """Check if a process is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


async def _kill_process_tree_unix(pid: int, grace_ms: int) -> None:
    """Kill process tree on Unix systems with graceful shutdown."""
    # Step 1: Try graceful SIGTERM to process group
    try:
        os.killpg(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        # Process group doesn't exist, try direct
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            return  # Already gone

    # Step 2: Wait grace period, then SIGKILL if still alive
    await asyncio.sleep(grace_ms / 1000.0)

    # Try process group first
    if _is_process_alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
            return
        except (OSError, ProcessLookupError):
            pass

    # Fallback to direct kill
    if _is_process_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


async def _kill_process_tree_windows(pid: int, grace_ms: int) -> None:
    """Kill process tree on Windows with graceful shutdown."""
    # Step 1: Try graceful termination (taskkill without /F)
    try:
        subprocess.run(
            ["taskkill", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        pass

    # Step 2: Wait grace period, then force kill if still alive
    await asyncio.sleep(grace_ms / 1000.0)

    if not _is_process_alive(pid):
        return

    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        pass


def kill_process_tree(pid: int, grace_ms: Optional[int] = None) -> None:
    """Kill process tree with graceful shutdown (mirrors TS killProcessTree).

    This is the synchronous version that schedules the async work.
    """
    if not isinstance(pid, int) or pid <= 0:
        return

    grace = _normalize_grace_ms(grace_ms)

    # Schedule the async kill in the event loop
    try:
        loop = asyncio.get_event_loop()
        if platform.system() == "Windows":
            loop.create_task(_kill_process_tree_windows(pid, grace))
        else:
            loop.create_task(_kill_process_tree_unix(pid, grace))
    except RuntimeError:
        # No event loop, use sync approach
        if platform.system() == "Windows":
            asyncio.run(_kill_process_tree_windows(pid, grace))
        else:
            asyncio.run(_kill_process_tree_unix(pid, grace))


# ============================================================================
# Child Process Adapter (mirroring adapters/child.ts)
# ============================================================================


class _StdinAdapter:
    """Adapter for stdin pipe."""

    def __init__(self, stdin: Optional[asyncio.StreamWriter]):
        self.stdin = stdin
        self.destroyed = False

    def write(self, data: str) -> None:
        if self.stdin and not self.destroyed:
            try:
                self.stdin.write(data.encode("utf-8"))
            except Exception as e:
                logger.debug(f"stdin write failed: {e}")

    def end(self) -> None:
        if self.stdin and not self.destroyed:
            try:
                self.stdin.close()
            except Exception:
                pass

    def destroy(self) -> None:
        self.destroyed = True
        self.end()


@dataclass
class _ChildProcessAdapter:
    """Adapter for child process (mirrors TS ChildAdapter)."""

    process: asyncio.subprocess.Process
    pid: Optional[int]
    stdin: Optional[_StdinAdapter]
    stdout_listeners: List[Callable[[str], None]] = field(default_factory=list)
    stderr_listeners: List[Callable[[str], None]] = field(default_factory=list)
    _disposed: bool = False

    def on_stdout(self, listener: Callable[[str], None]) -> None:
        """Register stdout listener."""
        self.stdout_listeners.append(listener)

    def on_stderr(self, listener: Callable[[str], None]) -> None:
        """Register stderr listener."""
        self.stderr_listeners.append(listener)

    async def _stream_output(self, stream: Optional[asyncio.StreamReader], listeners: List[Callable[[str], None]]) -> None:
        """Stream output to listeners."""
        if not stream:
            return
        try:
            while True:
                chunk = await stream.read(8192)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                for listener in listeners:
                    try:
                        listener(text)
                    except Exception as e:
                        logger.debug(f"Output listener failed: {e}")
        except Exception as e:
            logger.debug(f"Stream output failed: {e}")

    async def wait(self) -> Dict[str, Any]:
        """Wait for process to exit."""
        # Start streaming tasks
        stdout_task = asyncio.create_task(self._stream_output(self.process.stdout, self.stdout_listeners))
        stderr_task = asyncio.create_task(self._stream_output(self.process.stderr, self.stderr_listeners))

        # Wait for process
        await self.process.wait()

        # Wait for streaming to complete
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

        code = self.process.returncode
        # Python doesn't provide signal info directly, infer from negative returncode on Unix
        sig = None
        if code is not None and code < 0 and platform.system() != "Windows":
            sig = signal.Signals(-code).name

        return {"code": code, "signal": sig}

    def kill(self, sig: Optional[str] = None) -> None:
        """Kill the process."""
        if not self.pid:
            return

        if sig is None or sig == "SIGKILL":
            kill_process_tree(self.pid)
        else:
            try:
                if platform.system() == "Windows":
                    self.process.terminate()
                else:
                    sig_num = getattr(signal, sig, signal.SIGTERM)
                    os.kill(self.pid, sig_num)
            except Exception:
                pass

    def dispose(self) -> None:
        """Dispose the adapter."""
        if self._disposed:
            return
        self._disposed = True
        self.stdout_listeners.clear()
        self.stderr_listeners.clear()


async def _create_child_adapter(
    argv: List[str],
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    windows_verbatim_arguments: bool = False,
    input_data: Optional[str] = None,
    stdin_mode: Literal["inherit", "pipe-open", "pipe-closed"] = "pipe-closed",
) -> _ChildProcessAdapter:
    """Create a child process adapter (mirrors TS createChildAdapter)."""
    if not argv:
        raise ValueError("argv cannot be empty")

    # Resolve command for Windows
    command = argv[0]
    if platform.system() == "Windows":
        lower = command.lower()
        if not (lower.endswith(".exe") or lower.endswith(".cmd") or lower.endswith(".bat")):
            basename = os.path.basename(lower)
            if basename in ("npm", "pnpm", "yarn", "npx"):
                command = f"{command}.cmd"

    resolved_argv = [command] + argv[1:]

    # Determine stdin mode
    if input_data is not None and stdin_mode == "pipe-closed":
        actual_stdin_mode = "pipe-closed"
    elif stdin_mode == "inherit":
        actual_stdin_mode = "inherit"
    else:
        actual_stdin_mode = stdin_mode

    # Prepare stdin
    stdin_arg = None
    if actual_stdin_mode == "inherit":
        stdin_arg = sys.stdin
    elif actual_stdin_mode in ("pipe-open", "pipe-closed"):
        stdin_arg = subprocess.PIPE

    # Set up process group (detached on Unix)
    preexec_fn = None
    if platform.system() != "Windows":
        preexec_fn = os.setsid

    # Spawn process
    process = await asyncio.create_subprocess_exec(
        *resolved_argv,
        stdin=stdin_arg,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        preexec_fn=preexec_fn,
    )

    # Handle stdin
    stdin_adapter = None
    if process.stdin:
        stdin_adapter = _StdinAdapter(process.stdin)
        if input_data is not None:
            stdin_adapter.write(input_data)
            stdin_adapter.end()
        elif actual_stdin_mode == "pipe-closed":
            stdin_adapter.end()

    return _ChildProcessAdapter(
        process=process,
        pid=process.pid,
        stdin=stdin_adapter,
    )


# ============================================================================
# ProcessSupervisor (mirroring supervisor.ts)
# ============================================================================


def _clamp_timeout(value: Optional[int]) -> Optional[int]:
    """Clamp timeout to valid range."""
    if not isinstance(value, int) or value <= 0:
        return None
    return max(1, value)


def _is_timeout_reason(reason: TerminationReason) -> bool:
    """Check if termination reason is a timeout."""
    return reason in ("overall-timeout", "no-output-timeout")


@dataclass
class _ActiveRun:
    """Internal tracking for active runs."""

    run: ManagedRun
    scope_key: Optional[str]


class ProcessSupervisor:
    """Process supervisor for managing child processes (mirrors TS ProcessSupervisor)."""

    def __init__(self):
        self._registry = RunRegistry()
        self._active: Dict[str, _ActiveRun] = {}

    def cancel(self, run_id: str, reason: TerminationReason = "manual-cancel") -> None:
        """Cancel a running process."""
        current = self._active.get(run_id)
        if not current:
            return
        self._registry.update_state(run_id, "exiting", {"termination_reason": reason})
        current.run.cancel(reason)

    def cancel_scope(self, scope_key: str, reason: TerminationReason = "manual-cancel") -> None:
        """Cancel all processes in a scope."""
        if not scope_key.strip():
            return
        for run_id, active_run in list(self._active.items()):
            if active_run.scope_key == scope_key:
                self.cancel(run_id, reason)

    async def spawn(
        self,
        *,
        run_id: Optional[str] = None,
        session_id: str,
        backend_id: str,
        scope_key: Optional[str] = None,
        replace_existing_scope: bool = False,
        argv: List[str],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        windows_verbatim_arguments: bool = False,
        input_data: Optional[str] = None,
        stdin_mode: Literal["inherit", "pipe-open", "pipe-closed"] = "pipe-closed",
        timeout_ms: Optional[int] = None,
        no_output_timeout_ms: Optional[int] = None,
        capture_output: bool = True,
        on_stdout: Optional[Callable[[str], None]] = None,
        on_stderr: Optional[Callable[[str], None]] = None,
    ) -> ManagedRun:
        """Spawn a managed process (mirrors TS spawn)."""
        actual_run_id = run_id.strip() if run_id else str(uuid.uuid4())

        if replace_existing_scope and scope_key and scope_key.strip():
            self.cancel_scope(scope_key, "manual-cancel")

        started_at_ms = _now_ms()
        record = RunRecord(
            run_id=actual_run_id,
            session_id=session_id,
            backend_id=backend_id,
            scope_key=scope_key.strip() if scope_key else None,
            pid=None,
            process_group_id=None,
            state="starting",
            started_at_ms=started_at_ms,
            last_output_at_ms=started_at_ms,
            created_at_ms=started_at_ms,
            updated_at_ms=started_at_ms,
        )
        self._registry.add(record)

        # State variables
        forced_reason: Optional[TerminationReason] = None
        settled = False
        stdout_buffer = ""
        stderr_buffer = ""
        timeout_timer: Optional[asyncio.TimerHandle] = None
        no_output_timer: Optional[asyncio.TimerHandle] = None

        overall_timeout_ms = _clamp_timeout(timeout_ms)
        no_output_timeout_ms_clamped = _clamp_timeout(no_output_timeout_ms)

        def set_forced_reason(reason: TerminationReason) -> None:
            nonlocal forced_reason
            if forced_reason:
                return
            forced_reason = reason
            self._registry.update_state(actual_run_id, "exiting", {"termination_reason": reason})

        cancel_adapter_fn: Optional[Callable[[TerminationReason], None]] = None

        def request_cancel(reason: TerminationReason) -> None:
            set_forced_reason(reason)
            if cancel_adapter_fn:
                cancel_adapter_fn(reason)

        def touch_output() -> None:
            self._registry.touch_output(actual_run_id)
            nonlocal no_output_timer
            if not no_output_timeout_ms_clamped or settled:
                return
            if no_output_timer:
                no_output_timer.cancel()
            loop = asyncio.get_event_loop()
            no_output_timer = loop.call_later(
                no_output_timeout_ms_clamped / 1000.0,
                lambda: request_cancel("no-output-timeout"),
            )

        def clear_timers() -> None:
            nonlocal timeout_timer, no_output_timer
            if timeout_timer:
                timeout_timer.cancel()
                timeout_timer = None
            if no_output_timer:
                no_output_timer.cancel()
                no_output_timer = None

        try:
            # Spawn the process
            adapter = await _create_child_adapter(
                argv=argv,
                cwd=cwd,
                env=env,
                windows_verbatim_arguments=windows_verbatim_arguments,
                input_data=input_data,
                stdin_mode=stdin_mode,
            )

            self._registry.update_state(actual_run_id, "running", {"pid": adapter.pid})

            def cancel_adapter(reason: TerminationReason) -> None:
                nonlocal settled
                if settled:
                    return
                adapter.kill("SIGKILL")

            cancel_adapter_fn = cancel_adapter

            # Set up timeout timers
            if overall_timeout_ms:
                loop = asyncio.get_event_loop()
                timeout_timer = loop.call_later(
                    overall_timeout_ms / 1000.0,
                    lambda: request_cancel("overall-timeout"),
                )
            if no_output_timeout_ms_clamped:
                loop = asyncio.get_event_loop()
                no_output_timer = loop.call_later(
                    no_output_timeout_ms_clamped / 1000.0,
                    lambda: request_cancel("no-output-timeout"),
                )

            # Set up output streaming
            def stdout_handler(chunk: str) -> None:
                nonlocal stdout_buffer
                if capture_output:
                    stdout_buffer += chunk
                if on_stdout:
                    on_stdout(chunk)
                touch_output()

            def stderr_handler(chunk: str) -> None:
                nonlocal stderr_buffer
                if capture_output:
                    stderr_buffer += chunk
                if on_stderr:
                    on_stderr(chunk)
                touch_output()

            adapter.on_stdout(stdout_handler)
            adapter.on_stderr(stderr_handler)

            # Create wait future
            async def wait_for_exit() -> RunExit:
                nonlocal settled
                try:
                    result = await adapter.wait()
                except Exception as err:
                    if not settled:
                        settled = True
                        clear_timers()
                        self._active.pop(actual_run_id, None)
                        adapter.dispose()
                        self._registry.finalize(
                            actual_run_id,
                            {
                                "reason": "spawn-error",
                                "exit_code": None,
                                "exit_signal": None,
                            },
                        )
                    raise

                if settled:
                    return RunExit(
                        reason=forced_reason or "exit",
                        exit_code=result["code"],
                        exit_signal=result["signal"],
                        duration_ms=_now_ms() - started_at_ms,
                        stdout=stdout_buffer,
                        stderr=stderr_buffer,
                        timed_out=_is_timeout_reason(forced_reason or "exit"),
                        no_output_timed_out=forced_reason == "no-output-timeout",
                    )

                settled = True
                clear_timers()
                adapter.dispose()
                self._active.pop(actual_run_id, None)

                reason: TerminationReason = (
                    forced_reason if forced_reason else ("signal" if result["signal"] else "exit")
                )

                exit_result = RunExit(
                    reason=reason,
                    exit_code=result["code"],
                    exit_signal=result["signal"],
                    duration_ms=_now_ms() - started_at_ms,
                    stdout=stdout_buffer,
                    stderr=stderr_buffer,
                    timed_out=_is_timeout_reason(reason),
                    no_output_timed_out=forced_reason == "no-output-timeout",
                )

                self._registry.finalize(
                    actual_run_id,
                    {
                        "reason": exit_result.reason,
                        "exit_code": exit_result.exit_code,
                        "exit_signal": exit_result.exit_signal,
                    },
                )

                return exit_result

            wait_future = asyncio.ensure_future(wait_for_exit())

            # Create ManagedRun
            managed_run = ManagedRun(
                run_id=actual_run_id,
                pid=adapter.pid,
                started_at_ms=started_at_ms,
                stdin=adapter.stdin,
                wait=lambda: wait_future,
                cancel=lambda reason="manual-cancel": request_cancel(reason),
            )

            self._active[actual_run_id] = _ActiveRun(
                run=managed_run,
                scope_key=scope_key.strip() if scope_key else None,
            )

            return managed_run

        except Exception as err:
            self._registry.finalize(
                actual_run_id,
                {
                    "reason": "spawn-error",
                    "exit_code": None,
                    "exit_signal": None,
                },
            )
            logger.warning(f"spawn failed: run_id={actual_run_id} reason={err}")
            raise

    async def reconcile_orphans(self) -> None:
        """Reconcile orphaned processes (no-op in Python implementation)."""
        pass

    def get_record(self, run_id: str) -> Optional[RunRecord]:
        """Get a run record by ID."""
        return self._registry.get(run_id)


# ============================================================================
# Global supervisor instance
# ============================================================================

_global_supervisor: Optional[ProcessSupervisor] = None


def get_process_supervisor() -> ProcessSupervisor:
    """Get the global process supervisor instance."""
    global _global_supervisor
    if _global_supervisor is None:
        _global_supervisor = ProcessSupervisor()
    return _global_supervisor


def create_process_supervisor() -> ProcessSupervisor:
    """Create a new process supervisor instance."""
    return ProcessSupervisor()
