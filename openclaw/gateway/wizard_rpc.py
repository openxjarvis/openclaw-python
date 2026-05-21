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

_GATEWAY_MODES = frozenset({"local", "remote"})
_FLOW_VALUES = frozenset({"quickstart", "advanced", "manual"})


def resolve_wizard_start_params(params: dict) -> dict[str, Any]:
    """Resolve wizard.start RPC params — mirrors TS WizardStartParamsSchema.

    TS ``mode`` is gateway placement (``local`` | ``remote``) only.
    ``flow`` (quickstart | advanced) is a Python extension for setup depth;
    ``manual`` normalizes to ``advanced``.
    """
    raw_mode = params.get("mode")
    raw_flow = params.get("flow")
    workspace = params.get("workspace")

    gateway_mode: str | None = None
    flow = "quickstart"

    if isinstance(raw_mode, str) and raw_mode.strip():
        m = raw_mode.strip().lower()
        if m in _GATEWAY_MODES:
            gateway_mode = m
        elif m in _FLOW_VALUES:
            # Legacy: clients sent setup flow in ``mode`` before ``flow`` existed.
            flow = "advanced" if m == "manual" else m
        else:
            return {"error": f"invalid mode: {raw_mode}"}

    if isinstance(raw_flow, str) and raw_flow.strip():
        f = raw_flow.strip().lower()
        if f not in _FLOW_VALUES:
            return {"error": f"invalid flow: {raw_flow}"}
        flow = "advanced" if f == "manual" else f

    if gateway_mode == "remote" and flow == "quickstart":
        flow = "advanced"

    return {
        "gateway_mode": gateway_mode,
        "flow": flow,
        "workspace": workspace,
    }


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

        resolved = resolve_wizard_start_params(params)
        if "error" in resolved:
            return resolved

        flow = resolved["flow"]
        gateway_mode = resolved["gateway_mode"]
        workspace = resolved["workspace"]

        async def _runner(prompter):
            from ..wizard.onboarding import run_onboarding_wizard

            await run_onboarding_wizard(
                flow=flow,
                mode=gateway_mode,
                workspace_dir=workspace,
            )

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
