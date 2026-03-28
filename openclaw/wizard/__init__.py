"""Setup wizard for OpenClaw.

Interactive configuration wizard for first-time setup.
"""

from __future__ import annotations

from .onboarding import run_onboarding_wizard, is_first_run
from .config import configure_agent, configure_channels
from .session import WizardSession, WizardStep, WizardPrompter, WizardCancelledError

__all__ = [
    "run_onboarding_wizard",
    "is_first_run",
    "configure_agent",
    "configure_channels",
    "WizardSession",
    "WizardStep",
    "WizardPrompter",
    "WizardCancelledError",
]
