"""Cron service integration with gateway"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from ..config.paths import resolve_state_dir

if TYPE_CHECKING:
    from ..agents.providers import LLMProvider
    from ..agents.session import SessionManager
    from ..agents.tools.base import AgentTool
    from ..channels.base import BaseChannel
    from ..cron import CronService
    from ..cron.types import CronJob

logger = logging.getLogger(__name__)


async def run_isolated_cron_job(
    job: CronJob,
    provider: LLMProvider,
    tools: list[AgentTool],
    session_manager: SessionManager,
    channel_registry: dict[str, BaseChannel] | None = None,
) -> dict[str, Any]:
    """
    Run isolated cron job with full integration
    
    Args:
        job: Cron job to run
        provider: LLM provider
        tools: Available tools
        session_manager: Session manager
        channel_registry: Channel registry for delivery
        
    Returns:
        Execution result
    """
    from ..cron.isolated_agent.delivery import deliver_result
    from ..cron.isolated_agent.run import run_cron_isolated_agent_turn as run_isolated_agent_turn
    
    # Get sessions directory
    sessions_dir = session_manager.sessions_dir if hasattr(session_manager, "sessions_dir") else resolve_state_dir() / "sessions"
    
    try:
        # Run isolated agent turn
        result = await run_isolated_agent_turn(
            job=job,
            provider=provider,
            tools=tools,
            sessions_dir=sessions_dir,
            system_prompt=None,  # TODO: Get from config
        )
        
        # Deliver result if channel registry provided
        if channel_registry and job.delivery:
            try:
                delivery_success = await deliver_result(
                    job=job,
                    result=result,
                    channel_registry=channel_registry,
                    session_history=None,  # TODO: Get from session
                )
                
                result["delivered"] = delivery_success
            except Exception as e:
                logger.error(f"Error delivering cron job result: {e}", exc_info=True)
                result["delivery_error"] = str(e)
        
        return result
        
    except Exception as e:
        logger.error(f"Error running isolated cron job: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }


def setup_cron_callbacks(
    cron_service: CronService,
    provider: LLMProvider,
    tools: list[AgentTool],
    session_manager: SessionManager,
    channel_registry: dict[str, BaseChannel] | None = None,
) -> None:
    """
    Setup cron service callbacks
    
    Args:
        cron_service: Cron service
        provider: LLM provider
        tools: Available tools
        session_manager: Session manager
        channel_registry: Channel registry
    """
    # Setup isolated agent callback
    async def isolated_agent_callback(job: CronJob) -> dict[str, Any]:
        return await run_isolated_cron_job(
            job=job,
            provider=provider,
            tools=tools,
            session_manager=session_manager,
            channel_registry=channel_registry,
        )
    
    cron_service.on_isolated_agent = isolated_agent_callback
    
    logger.info("Cron service callbacks configured")
