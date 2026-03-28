"""Docker sandbox implementation

Provides isolated code execution using Docker containers.
Matches TypeScript openclaw/src/agents/sandbox/docker.ts
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import DEFAULT_SANDBOX_IMAGE, SANDBOX_AGENT_WORKSPACE_MOUNT

logger = logging.getLogger(__name__)


async def is_docker_available() -> bool:
    """Check if docker command is available.
    
    Mirrors TS isDockerAvailable() in commands/doctor-sandbox.ts L65-74.
    Returns True if `docker` command exists (indicating Docker CLI is installed).
    Does NOT require Docker daemon to be running.
    
    Returns:
        True if docker command exists
    """
    try:
        # Use --version which works even if daemon is not running
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=5.0
        )
        # docker --version should always succeed if CLI is installed
        return proc.returncode == 0
    except FileNotFoundError:
        # docker command not found in PATH
        return False
    except asyncio.TimeoutError:
        logger.debug("Docker --version command timed out")
        return False
    except Exception as e:
        logger.debug(f"Docker availability check failed: {e}")
        return False


async def exec_docker(args: list[str], allow_failure: bool = False, timeout_ms: int = 30000) -> dict[str, Any]:
    """
    Execute a Docker command
    
    Args:
        args: Docker command arguments
        allow_failure: If True, don't raise on non-zero exit
        timeout_ms: Timeout in milliseconds (default 30000)
        
    Returns:
        Dict with stdout, stderr, code
        
    Raises:
        RuntimeError: If command fails and allow_failure=False
    """
    timeout_sec = timeout_ms / 1000
    
    proc = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_sec
        )
    except asyncio.TimeoutError:
        proc.kill()
        if not allow_failure:
            raise RuntimeError(f"Docker command timed out after {timeout_ms}ms: docker {' '.join(args)}")
        return {
            "stdout": "",
            "stderr": f"Command timed out after {timeout_ms}ms",
            "code": -1,
        }
    
    stdout = stdout_bytes.decode() if stdout_bytes else ""
    stderr = stderr_bytes.decode() if stderr_bytes else ""
    code = proc.returncode or 0
    
    if code != 0 and not allow_failure:
        error_msg = stderr.strip() or f"docker {' '.join(args)} failed"
        raise RuntimeError(error_msg)
    
    return {
        "stdout": stdout,
        "stderr": stderr,
        "code": code,
    }


async def read_docker_port(container_name: str, port: int) -> int | None:
    """
    Read mapped port for a container
    
    Args:
        container_name: Container name
        port: Internal port
        
    Returns:
        Mapped external port or None
    """
    result = await exec_docker(
        ["port", container_name, f"{port}/tcp"],
        allow_failure=True
    )
    
    if result["code"] != 0:
        return None
    
    line = result["stdout"].strip().split('\n')[0]
    
    # Parse "0.0.0.0:12345" or "[::]:12345"
    import re
    match = re.search(r':(\d+)\s*$', line)
    if not match:
        return None
    
    mapped = int(match.group(1))
    return mapped if mapped > 0 else None


async def docker_image_exists(image: str) -> bool:
    """
    Check if a Docker image exists locally
    
    Args:
        image: Image name
        
    Returns:
        True if image exists
    """
    result = await exec_docker(["image", "inspect", image], allow_failure=True)
    
    if result["code"] == 0:
        return True
    
    stderr = result["stderr"].strip()
    if "No such image" in stderr:
        return False
    
    raise RuntimeError(f"Failed to inspect sandbox image: {stderr}")


async def ensure_docker_image(image: str):
    """
    Ensure Docker image exists, pulling if necessary
    
    Args:
        image: Image name
        
    Raises:
        RuntimeError: If image cannot be obtained
    """
    exists = await docker_image_exists(image)
    if exists:
        return
    
    if image == DEFAULT_SANDBOX_IMAGE:
        # Pull debian base and tag as default
        logger.info("Pulling default sandbox image...")
        await exec_docker(["pull", "debian:bookworm-slim"])
        await exec_docker(["tag", "debian:bookworm-slim", DEFAULT_SANDBOX_IMAGE])
        logger.info("Default sandbox image ready")
        return
    
    raise RuntimeError(
        f"Sandbox image not found: {image}. Build or pull it first."
    )


async def docker_container_state(name: str) -> dict[str, bool]:
    """
    Get container state
    
    Args:
        name: Container name
        
    Returns:
        Dict with exists and running booleans
    """
    result = await exec_docker(
        ["inspect", "-f", "{{.State.Running}}", name],
        allow_failure=True
    )
    
    if result["code"] != 0:
        return {"exists": False, "running": False}
    
    running = result["stdout"].strip() == "true"
    return {"exists": True, "running": running}


def normalize_docker_limit(value: str | int | None) -> str | None:
    """Normalize Docker resource limit value"""
    if value is None:
        return None
    
    if isinstance(value, int):
        return str(value) if value > 0 else None
    
    trimmed = str(value).strip()
    return trimmed if trimmed else None


@dataclass
class DockerSandboxConfig:
    """Docker sandbox configuration — mirrors TS sandbox/docker.ts security defaults."""
    
    image: str = DEFAULT_SANDBOX_IMAGE
    memory: str | None = None  # e.g. "512m", "1g"
    cpus: str | None = None  # e.g. "0.5", "2"
    cpu_shares: int | None = None
    ulimits: dict[str, dict[str, int]] | None = None
    workspace_access: str = "read-write"  # "read-only", "read-write", "none"
    network_mode: str = "bridge"  # "bridge", "none", "host"
    env: dict[str, str] = field(default_factory=dict)
    volumes: dict[str, str] = field(default_factory=dict)  # host_path: container_path

    # Security hardening (aligned with TS docker.ts createSandboxArgs)
    read_only_root: bool = True
    cap_drop_all: bool = True
    no_new_privileges: bool = True
    pids_limit: int = 256
    seccomp_profile: str | None = None  # path or "default"
    apparmor_profile: str | None = None  # profile name
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for hashing"""
        return {
            "image": self.image,
            "memory": self.memory,
            "cpus": self.cpus,
            "cpu_shares": self.cpu_shares,
            "ulimits": self.ulimits,
            "workspace_access": self.workspace_access,
            "network_mode": self.network_mode,
            "env": self.env,
            "volumes": self.volumes,
            "read_only_root": self.read_only_root,
            "cap_drop_all": self.cap_drop_all,
            "no_new_privileges": self.no_new_privileges,
            "pids_limit": self.pids_limit,
        }


class DockerSandbox:
    """Docker sandbox manager for isolated code execution"""
    
    def __init__(self, config: DockerSandboxConfig, workspace_dir: Path | None = None):
        """
        Initialize Docker sandbox
        
        Args:
            config: Sandbox configuration
            workspace_dir: Workspace directory to mount
        """
        self.config = config
        self.workspace_dir = workspace_dir
        self.container_name: str | None = None
        self._started = False
    
    async def start(self) -> str:
        """
        Start sandbox container.

        Returns:
            Container name
        """
        if self._started:
            return self.container_name or ""

        # Security validation before docker run (mirrors TS context.ts validateSandboxSecurity)
        try:
            from openclaw.security.validate_sandbox_security import validate_sandbox_security
            sandbox_cfg: dict = {}
            if getattr(self.config, "network_mode", None):
                sandbox_cfg["network"] = self.config.network_mode
            if getattr(self.config, "seccomp_profile", None):
                sandbox_cfg["seccompProfile"] = self.config.seccomp_profile
            if getattr(self.config, "apparmor_profile", None):
                sandbox_cfg["apparmorProfile"] = self.config.apparmor_profile
            # Collect bind mounts for validation
            binds: list[str] = []
            if self.workspace_dir:
                # Use /app to avoid reserved container path /workspace
                binds.append(f"{self.workspace_dir}:/app:rw")
            extra_binds = getattr(self.config, "binds", None)
            if extra_binds:
                binds.extend(extra_binds)
            if binds:
                sandbox_cfg["binds"] = binds

            allowed_source_roots: list[str] = []
            if self.workspace_dir:
                allowed_source_roots.append(str(self.workspace_dir))

            validate_sandbox_security(sandbox_cfg, allowed_source_roots=allowed_source_roots)
        except ImportError:
            pass  # validation module not yet available — skip

        # Ensure image exists
        await ensure_docker_image(self.config.image)
        
        # Generate container name
        import uuid
        import time
        
        timestamp = int(time.time())
        unique_id = str(uuid.uuid4())[:8]
        self.container_name = f"openclaw-sandbox-{timestamp}-{unique_id}"
        
        # Build docker run command
        args = ["run", "-d", "--name", self.container_name]
        
        # Resource limits
        if self.config.memory:
            mem = normalize_docker_limit(self.config.memory)
            if mem:
                args.extend(["--memory", mem])
        
        if self.config.cpus:
            cpus = normalize_docker_limit(self.config.cpus)
            if cpus:
                args.extend(["--cpus", cpus])
        
        if self.config.cpu_shares:
            args.extend(["--cpu-shares", str(self.config.cpu_shares)])
        
        # ulimits
        if self.config.ulimits:
            for name, limits in self.config.ulimits.items():
                soft = limits.get("soft")
                hard = limits.get("hard", soft)
                if soft is not None:
                    args.extend(["--ulimit", f"{name}={soft}:{hard}"])
        
        # Security hardening (mirrors TS createSandboxArgs)
        if self.config.cap_drop_all:
            args.extend(["--cap-drop", "ALL"])
        if self.config.no_new_privileges:
            args.extend(["--security-opt", "no-new-privileges"])
        if self.config.read_only_root:
            args.append("--read-only")
            # Writable tmpfs for /tmp and /run when root is read-only
            args.extend(["--tmpfs", "/tmp:rw,noexec,nosuid,size=256m"])
            args.extend(["--tmpfs", "/run:rw,noexec,nosuid,size=64m"])
        if self.config.pids_limit > 0:
            args.extend(["--pids-limit", str(self.config.pids_limit)])
        if self.config.seccomp_profile:
            args.extend(["--security-opt", f"seccomp={self.config.seccomp_profile}"])
        if self.config.apparmor_profile:
            args.extend(["--security-opt", f"apparmor={self.config.apparmor_profile}"])

        # Network
        args.extend(["--network", self.config.network_mode])
        
        # Environment variables — sanitize before injecting into container
        from .sanitize_env_vars import sanitize_env_vars
        sanitized = sanitize_env_vars(self.config.env)
        if sanitized.blocked:
            logger.info(
                "Sandbox: blocked %d sensitive env var(s) from container: %s",
                len(sanitized.blocked),
                ", ".join(sanitized.blocked),
            )
        for warning in sanitized.warnings:
            logger.warning("Sandbox env var warning: %s", warning)
        for key, value in sanitized.allowed.items():
            args.extend(["-e", f"{key}={value}"])
        
        # Workspace mount
        if self.workspace_dir and self.config.workspace_access != "none":
            mount_mode = "ro" if self.config.workspace_access == "read-only" else "rw"
            args.extend([
                "-v",
                f"{self.workspace_dir}:{SANDBOX_AGENT_WORKSPACE_MOUNT}:{mount_mode}"
            ])
        
        # Additional volumes
        for host_path, container_path in self.config.volumes.items():
            args.extend(["-v", f"{host_path}:{container_path}"])
        
        # Image and command (keep container running)
        args.extend([self.config.image, "sleep", "infinity"])
        
        logger.info(f"Starting sandbox container: {self.container_name}")
        
        # Run container
        await exec_docker(args)
        
        self._started = True
        return self.container_name
    
    async def stop(self):
        """Stop and remove sandbox container"""
        if not self.container_name:
            return
        
        logger.info(f"Stopping sandbox container: {self.container_name}")
        
        # Stop container
        await exec_docker(["stop", self.container_name], allow_failure=True)
        
        # Remove container
        await exec_docker(["rm", self.container_name], allow_failure=True)
        
        self._started = False
    
    async def exec_command(
        self,
        cmd: str,
        workdir: str | None = None,
        env_override: dict[str, str] | None = None,
        interactive: bool = True,
        stdin: bytes | None = None,
        allow_failure: bool = True,
    ) -> dict[str, Any]:
        """Execute command in sandbox container.

        Builds ``docker exec`` arguments in the same style as the TypeScript
        ``buildDockerExecArgs()`` helper.

        Args:
            cmd: Shell command string to execute via ``sh -c``.
            workdir: Working directory inside the container (``-w`` flag).
            env_override: Extra environment variables to pass (``-e KEY=val``).
            interactive: Pass ``-i`` flag (required for stdin/stdout pipes).
            stdin: Optional bytes to pipe to the process's stdin.
            allow_failure: If ``False``, raise on non-zero exit code.

        Returns:
            Dict with ``stdout``, ``stderr``, ``exit_code``, ``success``.
        """
        if not self.container_name or not self._started:
            raise RuntimeError("Sandbox not started")

        # Build exec args — mirrors TS buildDockerExecArgs()
        args: list[str] = ["exec"]

        if interactive:
            args.append("-i")

        if workdir:
            args.extend(["-w", workdir])

        for key, value in (env_override or {}).items():
            args.extend(["-e", f"{key}={value}"])

        args.extend([self.container_name, "sh", "-lc", cmd])

        proc = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await proc.communicate(input=stdin)
        code = proc.returncode or 0
        stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        if code != 0 and not allow_failure:
            msg = stderr_text.strip() or f"sandbox exec failed with code {code}"
            raise RuntimeError(msg)

        return {
            "stdout": stdout_text,
            "stderr": stderr_text,
            "exit_code": code,
            "success": code == 0,
        }
    
    async def __aenter__(self):
        """Context manager entry"""
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        await self.stop()
