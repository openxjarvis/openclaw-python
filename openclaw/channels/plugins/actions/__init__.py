"""Interactive message actions for channels

Mirrors openclaw/src/channels/plugins/actions-types.ts

Note: This package provides action types and management.
Channel-specific action implementations are in this directory.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


ActionHandler = Callable[[str, dict[str, Any]], Awaitable[Any]]


@dataclass
class MessageAction:
    """Interactive message action"""
    
    id: str
    """Action identifier"""
    
    label: str
    """Action label"""
    
    handler: ActionHandler
    """Action handler function"""
    
    icon: str | None = None
    """Optional icon"""


class MessageActionsManager:
    """Manage interactive message actions."""
    
    def __init__(self):
        self.actions: dict[str, MessageAction] = {}
    
    def register_action(
        self,
        action_id: str,
        label: str,
        handler: ActionHandler,
        icon: str | None = None,
    ) -> None:
        """Register message action.
        
        Args:
            action_id: Action identifier
            label: Action label
            handler: Handler function
            icon: Optional icon
        """
        action = MessageAction(
            id=action_id,
            label=label,
            handler=handler,
            icon=icon,
        )
        self.actions[action_id] = action
        logger.info(f"Registered message action: {action_id}")
    
    def get_action(self, action_id: str) -> MessageAction | None:
        """Get action by ID.
        
        Args:
            action_id: Action identifier
            
        Returns:
            Action or None if not found
        """
        return self.actions.get(action_id)
    
    async def execute_action(
        self,
        action_id: str,
        message_id: str,
        data: dict[str, Any],
    ) -> Any:
        """Execute action.
        
        Args:
            action_id: Action identifier
            message_id: Message identifier
            data: Action data
            
        Returns:
            Action result
        """
        action = self.actions.get(action_id)
        if not action:
            logger.warning(f"Action not found: {action_id}")
            return None
        
        try:
            return await action.handler(message_id, data)
        except Exception as e:
            logger.error(f"Action execution error: {e}")
            raise


__all__ = [
    "MessageAction",
    "ActionHandler",
    "MessageActionsManager",
]
