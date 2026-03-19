"""
Pairing approval workflow (aligned with TypeScript pairing-store.ts)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .store import PairingStore, ChannelId
from openclaw.config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


def approve_pairing_request(
    channel: ChannelId,
    code: str,
) -> tuple[bool, str]:
    """
    Approve a pairing request by code
    
    This function:
    1. Finds the pairing request by code
    2. Adds the user to the channel's allowFrom list
    3. Removes the pairing request
    4. Saves the updated configuration
    
    Args:
        channel: Channel ID
        code: 8-character pairing code
        
    Returns:
        Tuple of (success, message)
    """
    # Find pairing request
    store = PairingStore(channel)
    request = store.find_by_code(code)
    
    if not request:
        return False, f"Pairing code not found or expired: {code}"
    
    # Update allowFrom list
    success = add_to_allow_from(channel, request.id)
    
    if not success:
        return False, f"Failed to update allowFrom for {channel}"
    
    # Remove pairing request
    store.remove_request(request.id)
    
    logger.info(f"Approved pairing for {channel} user {request.id}")
    
    return True, f"Approved {channel} user {request.id}"


def add_to_allow_from(
    channel: ChannelId,
    user_id: str,
) -> bool:
    """
    Add user ID to channel's allowFrom list
    
    Args:
        channel: Channel ID
        user_id: User ID to add
        
    Returns:
        True if successful
    """
    try:
        # Read current config
        config_path = resolve_state_dir() / "openclaw.json"
        
        if not config_path.exists():
            logger.error("Config file not found")
            return False
        
        with open(config_path, "r") as f:
            config = json.load(f)
        
        # Ensure channels structure exists
        if "channels" not in config:
            config["channels"] = {}
        
        if channel not in config["channels"]:
            config["channels"][channel] = {}
        
        channel_config = config["channels"][channel]
        
        # Ensure allowFrom exists
        if "allowFrom" not in channel_config:
            channel_config["allowFrom"] = []
        
        allow_from = channel_config["allowFrom"]
        
        # Add user ID if not already present
        if user_id not in allow_from:
            allow_from.append(user_id)
            channel_config["allowFrom"] = allow_from
            
            # Update DM policy to allowlist if not set
            if "dmPolicy" not in channel_config or channel_config["dmPolicy"] == "off":
                channel_config["dmPolicy"] = "allowlist"
            
            # Save config
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
                f.write("\n")
            
            logger.info(f"Added {user_id} to {channel} allowFrom")
            return True
        else:
            logger.info(f"User {user_id} already in {channel} allowFrom")
            return True
    
    except Exception as e:
        logger.error(f"Failed to update allowFrom: {e}")
        return False


def remove_from_allow_from(
    channel: ChannelId,
    user_id: str,
) -> bool:
    """
    Remove user ID from channel's allowFrom list
    
    Args:
        channel: Channel ID
        user_id: User ID to remove
        
    Returns:
        True if successful
    """
    try:
        config_path = resolve_state_dir() / "openclaw.json"
        
        if not config_path.exists():
            return False
        
        with open(config_path, "r") as f:
            config = json.load(f)
        
        if "channels" not in config or channel not in config["channels"]:
            return False
        
        channel_config = config["channels"][channel]
        
        if "allowFrom" not in channel_config:
            return False
        
        allow_from = channel_config["allowFrom"]
        
        if user_id in allow_from:
            allow_from.remove(user_id)
            channel_config["allowFrom"] = allow_from
            
            # Save config
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
                f.write("\n")
            
            logger.info(f"Removed {user_id} from {channel} allowFrom")
            return True
        
        return False
    
    except Exception as e:
        logger.error(f"Failed to update allowFrom: {e}")
        return False
