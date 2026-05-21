"""ACP Persistent Bindings.

Mirrors TypeScript src/acp/persistent-bindings.ts

Persistent bindings store user approval decisions across sessions,
so users aren't repeatedly asked for the same approval.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

BindingDecision = Literal["allow", "deny", "allow_once"]


@dataclass
class PersistentBinding:
    """A persisted approval binding.

    Mirrors TS PersistentBinding type.
    """

    operation: str              # operation name
    pattern: str | None = None  # optional glob pattern (e.g. path pattern)
    decision: BindingDecision = "allow"
    agent_id: str | None = None
    expires_at: float | None = None   # epoch seconds, None = never expires
    reason: str | None = None


@dataclass
class PersistentBindingsStore:
    """In-memory + file-backed store for persistent bindings."""

    _bindings: list[PersistentBinding] = field(default_factory=list)
    _file_path: Path | None = None

    def add(self, binding: PersistentBinding) -> None:
        """Add a binding."""
        self._bindings.append(binding)
        self._save()

    def get_decision(
        self,
        operation: str,
        agent_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> BindingDecision | None:
        """Look up a decision for an operation.

        Returns None if no binding exists (prompts user).
        """
        import time
        now = time.time()
        for binding in self._bindings:
            if binding.decision == "allow_once":
                continue  # one-time bindings are consumed on use
            if binding.expires_at and now > binding.expires_at:
                continue
            if binding.agent_id and binding.agent_id != agent_id:
                continue
            if binding.operation != operation and not _matches_pattern(binding.operation, operation):
                continue
            return binding.decision
        return None

    def remove(self, operation: str, agent_id: str | None = None) -> int:
        """Remove bindings for an operation. Returns count removed."""
        before = len(self._bindings)
        self._bindings = [
            b for b in self._bindings
            if not (b.operation == operation and (agent_id is None or b.agent_id == agent_id))
        ]
        removed = before - len(self._bindings)
        if removed:
            self._save()
        return removed

    def list_bindings(self) -> list[PersistentBinding]:
        return list(self._bindings)

    def _save(self) -> None:
        if not self._file_path:
            return
        try:
            data = [
                {
                    "operation": b.operation,
                    "pattern": b.pattern,
                    "decision": b.decision,
                    "agent_id": b.agent_id,
                    "expires_at": b.expires_at,
                    "reason": b.reason,
                }
                for b in self._bindings
            ]
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            self._file_path.write_text(json.dumps(data, indent=2))
        except Exception:
            logger.exception("Failed to save persistent bindings")


# Module-level default store
_default_store: PersistentBindingsStore | None = None


def get_bindings_store() -> PersistentBindingsStore:
    global _default_store
    if _default_store is None:
        try:
            from openclaw.config.paths import resolve_state_dir
            file_path = resolve_state_dir() / "acp_bindings.json"
            _default_store = PersistentBindingsStore(_file_path=file_path)
            _load_bindings(_default_store)
        except Exception:
            _default_store = PersistentBindingsStore()
    return _default_store


def persist_acp_binding(binding: PersistentBinding, store: PersistentBindingsStore | None = None) -> None:
    """Persist a binding decision.

    Mirrors TS persistAcpBinding().
    """
    (store or get_bindings_store()).add(binding)


def load_persistent_bindings(config: Any = None) -> list[PersistentBinding]:
    """Load all persistent bindings.

    Mirrors TS loadPersistentBindings().
    """
    return get_bindings_store().list_bindings()


def _load_bindings(store: PersistentBindingsStore) -> None:
    """Load bindings from disk into store."""
    if not store._file_path or not store._file_path.exists():
        return
    try:
        data = json.loads(store._file_path.read_text())
        for item in data:
            store._bindings.append(PersistentBinding(**item))
    except Exception:
        logger.exception("Failed to load persistent bindings")


def _matches_pattern(pattern: str, value: str) -> bool:
    """Simple glob-style pattern matching."""
    import fnmatch
    return fnmatch.fnmatch(value, pattern)
