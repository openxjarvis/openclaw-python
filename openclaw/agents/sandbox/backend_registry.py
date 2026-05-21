"""Pluggable sandbox backend registry.

Matches TypeScript openclaw/src/agents/sandbox/backend.ts
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Union

SandboxBackendId = str

SandboxBackendFactory = Callable[..., Awaitable[Any]]
SandboxBackendManager = Any


@dataclass
class RegisteredSandboxBackend:
    factory: SandboxBackendFactory
    manager: Optional[SandboxBackendManager] = None


SandboxBackendRegistration = Union[SandboxBackendFactory, RegisteredSandboxBackend]

_SANDBOX_BACKEND_FACTORIES: dict[SandboxBackendId, RegisteredSandboxBackend] = {}


def _normalize_sandbox_backend_id(backend_id: str) -> SandboxBackendId:
    normalized = (backend_id or "").strip().lower()
    if not normalized:
        raise ValueError("Sandbox backend id must not be empty.")
    return normalized


def register_sandbox_backend(
    backend_id: str,
    registration: SandboxBackendRegistration,
) -> Callable[[], None]:
    normalized_id = _normalize_sandbox_backend_id(backend_id)
    if callable(registration) and not isinstance(registration, RegisteredSandboxBackend):
        resolved = RegisteredSandboxBackend(factory=registration)
    else:
        resolved = registration  # type: ignore[assignment]
    previous = _SANDBOX_BACKEND_FACTORIES.get(normalized_id)

    def unregister() -> None:
        if previous:
            _SANDBOX_BACKEND_FACTORIES[normalized_id] = previous
        else:
            _SANDBOX_BACKEND_FACTORIES.pop(normalized_id, None)

    _SANDBOX_BACKEND_FACTORIES[normalized_id] = resolved
    return unregister


def get_sandbox_backend_factory(backend_id: str) -> Optional[SandboxBackendFactory]:
    entry = _SANDBOX_BACKEND_FACTORIES.get(_normalize_sandbox_backend_id(backend_id))
    return entry.factory if entry else None


def get_sandbox_backend_manager(backend_id: str) -> Optional[SandboxBackendManager]:
    entry = _SANDBOX_BACKEND_FACTORIES.get(_normalize_sandbox_backend_id(backend_id))
    return entry.manager if entry else None


def require_sandbox_backend_factory(backend_id: str) -> SandboxBackendFactory:
    factory = get_sandbox_backend_factory(backend_id)
    if factory:
        return factory
    raise ValueError(
        "\n".join(
            [
                f'Sandbox backend "{backend_id}" is not registered.',
                "Load the plugin that provides it, or set agents.defaults.sandbox.backend=docker.",
            ]
        )
    )


def _register_builtin_backends() -> None:
    from .ssh_backend import create_ssh_sandbox_backend, ssh_sandbox_backend_manager

    try:
        from .docker_backend import (  # type: ignore[import-not-found]
            create_docker_sandbox_backend,
            docker_sandbox_backend_manager,
        )
    except ImportError:
        async def _docker_not_implemented(*_args: Any, **_kwargs: Any) -> Any:
            raise NotImplementedError(
                "Docker sandbox backend is not yet implemented in openclaw-python."
            )

        create_docker_sandbox_backend = _docker_not_implemented  # type: ignore[assignment]
        docker_sandbox_backend_manager = None

    register_sandbox_backend(
        "docker",
        RegisteredSandboxBackend(
            factory=create_docker_sandbox_backend,
            manager=docker_sandbox_backend_manager,
        ),
    )
    register_sandbox_backend(
        "ssh",
        RegisteredSandboxBackend(
            factory=create_ssh_sandbox_backend,
            manager=ssh_sandbox_backend_manager,
        ),
    )


_register_builtin_backends()
