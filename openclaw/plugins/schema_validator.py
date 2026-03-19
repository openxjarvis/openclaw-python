"""Plugin manifest schema validation — mirrors src/plugins/schema-validator.ts"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def validate_plugin_manifest(manifest: dict[str, Any]) -> list[str]:
    """
    Validate a plugin manifest against the expected schema.
    
    Args:
        manifest: Plugin manifest dict
    
    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []
    
    # Check required fields
    required_fields = ["name", "version"]
    for field in required_fields:
        if field not in manifest:
            errors.append(f"Missing required field: {field}")
    
    # Validate name
    if "name" in manifest:
        name = manifest["name"]
        if not isinstance(name, str):
            errors.append(f"Field 'name' must be a string, got {type(name).__name__}")
        elif not name.strip():
            errors.append("Field 'name' cannot be empty")
    
    # Validate version
    if "version" in manifest:
        version = manifest["version"]
        if not isinstance(version, str):
            errors.append(f"Field 'version' must be a string, got {type(version).__name__}")
        elif not version.strip():
            errors.append("Field 'version' cannot be empty")
    
    # Validate optional fields
    if "description" in manifest and not isinstance(manifest["description"], str):
        errors.append("Field 'description' must be a string")
    
    if "author" in manifest and not isinstance(manifest["author"], str):
        errors.append("Field 'author' must be a string")
    
    if "main" in manifest and not isinstance(manifest["main"], str):
        errors.append("Field 'main' must be a string")
    
    if "dependencies" in manifest and not isinstance(manifest["dependencies"], dict):
        errors.append("Field 'dependencies' must be an object")
    
    if "keywords" in manifest:
        keywords = manifest["keywords"]
        if not isinstance(keywords, list):
            errors.append("Field 'keywords' must be an array")
        elif not all(isinstance(k, str) for k in keywords):
            errors.append("All keywords must be strings")
    
    return errors


def validate_plugin_config(config: dict[str, Any]) -> list[str]:
    """
    Validate plugin configuration.
    
    Args:
        config: Plugin configuration dict
    
    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []
    
    # Basic structure validation
    if not isinstance(config, dict):
        errors.append("Plugin config must be a dictionary")
        return errors
    
    # Validate enabled field if present
    if "enabled" in config and not isinstance(config["enabled"], bool):
        errors.append("Field 'enabled' must be a boolean")
    
    return errors


def is_valid_manifest(manifest: dict[str, Any]) -> bool:
    """
    Check if a plugin manifest is valid.
    
    Args:
        manifest: Plugin manifest dict
    
    Returns:
        True if valid, False otherwise
    """
    errors = validate_plugin_manifest(manifest)
    return len(errors) == 0
