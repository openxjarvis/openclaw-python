"""Wizard RPC handler — mirrors TS src/gateway/server-methods/wizard.ts

Uses the coroutine-driven WizardSession: the runner (the actual wizard
logic) runs as a concurrent coroutine and suspends whenever it needs
user input.  The RPC layer calls ``next()`` / ``answer()`` / ``cancel()``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from ..wizard.session import WizardSession, WizardCancelledError

logger = logging.getLogger(__name__)


class WizardRPCHandler:
    """Handles wizard-related RPC methods.

    Each ``wizard.start`` creates a :class:`WizardSession` backed by the
    actual onboarding runner coroutine.
    """

    def __init__(self, gateway: Any = None) -> None:
        self.gateway = gateway
        self.sessions: dict[str, WizardSession] = {}

    def _find_running(self) -> str | None:
        for sid, s in self.sessions.items():
            if s.get_status() == "running":
                return sid
        return None

    async def wizard_start(self, params: dict) -> dict:
        """Start a new wizard session.

        RPC: wizard.start
        """
        running = self._find_running()
        if running:
            return {"error": "wizard already running", "sessionId": running}

        mode = params.get("mode", "quickstart")
        workspace = params.get("workspace")

        if mode not in ("quickstart", "advanced", "manual", "local", "remote"):
            return {"error": f"invalid mode: {mode}"}

        async def _runner(prompter):
            from ..wizard.onboarding import run_onboarding_wizard

            await run_onboarding_wizard(flow=mode, workspace_dir=workspace)

        try:
            session_id = str(uuid.uuid4())
            session = WizardSession(runner=_runner)
            self.sessions[session_id] = session
            result = await session.next()
            if result.done:
                self.sessions.pop(session_id, None)
            return {"sessionId": session_id, **result.to_dict()}
        except Exception as e:
            logger.error("Failed to start wizard: %s", e, exc_info=True)
            return {"error": str(e)}

    async def wizard_next(self, params: dict) -> dict:
        """Advance wizard with optional answer.

        RPC: wizard.next
        """
        session_id = params.get("sessionId", "")
        session = self.sessions.get(session_id)
        if not session:
            return {"error": "wizard not found"}

        answer = params.get("answer")
        if answer and isinstance(answer, dict):
            step_id = str(answer.get("stepId", ""))
            value = answer.get("value")
            if session.get_status() != "running":
                return {"error": "wizard not running"}
            try:
                await session.answer(step_id, value)
            except Exception as exc:
                return {"error": str(exc)}

        result = await session.next()
        if result.done:
            self.sessions.pop(session_id, None)
        return result.to_dict()

    async def wizard_cancel(self, params: dict) -> dict:
        """Cancel running wizard.

        RPC: wizard.cancel
        """
        session_id = params.get("sessionId", "")
        session = self.sessions.get(session_id)
        if not session:
            return {"error": "wizard not found"}

        session.cancel()
        self.sessions.pop(session_id, None)
        return {"status": session.get_status(), "error": session.get_error()}

    async def wizard_status(self, params: dict) -> dict:
        """Get wizard session status.

        RPC: wizard.status
        """
        session_id = params.get("sessionId", "")
        session = self.sessions.get(session_id)
        if not session:
            return {"error": "wizard not found"}

        status = session.get_status()
        if status != "running":
            self.sessions.pop(session_id, None)
        return {"status": status, "error": session.get_error()}
