"""Plugin configuration schema utilities

Mirrors openclaw/src/plugins/config-schema.ts
"""
from __future__ import annotations

from typing import Any, TypedDict


class SafeParseSuccess(TypedDict):
    """Safe parse success result"""
    success: bool  # Always True
    data: Any | None


class SafeParseError(TypedDict):
    """Safe parse error result"""
    success: bool  # Always False
    error: dict[str, Any]


SafeParseResult = SafeParseSuccess | SafeParseError


class OpenClawPluginConfigSchema:
    """Plugin configuration schema interface
    
    Provides validation and JSON schema for plugin config.
    """
    
    def safe_parse(self, data: Any) -> SafeParseResult:
        """Parse and validate plugin config data.
        
        Args:
            data: Configuration data to validate
            
        Returns:
            SafeParseResult with success status and data or error
        """
        raise NotImplementedError
    
    @property
    def json_schema(self) -> dict[str, Any]:
        """Get JSON schema for this config.
        
        Returns:
            JSON schema dict
        """
        raise NotImplementedError


class EmptyPluginConfigSchema(OpenClawPluginConfigSchema):
    """Empty plugin configuration schema
    
    Accepts undefined or empty objects, rejects non-empty objects.
    """
    
    def safe_parse(self, data: Any) -> SafeParseResult:
        """Validate that config is empty or undefined.
        
        Args:
            data: Configuration data to validate
            
        Returns:
            Success if data is None, empty dict, or missing; error otherwise
        """
        if data is None:
            return SafeParseSuccess(success=True, data=None)
        
        if isinstance(data, dict) and len(data) == 0:
            return SafeParseSuccess(success=True, data={})
        
        return SafeParseError(
            success=False,
            error={
                "issues": [
                    {
                        "message": "Empty config schema does not accept non-empty configuration",
                        "path": [],
                    }
                ]
            },
        )
    
    @property
    def json_schema(self) -> dict[str, Any]:
        """Get JSON schema for empty config.
        
        Returns:
            JSON schema that accepts nothing
        """
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }


def empty_plugin_config_schema() -> EmptyPluginConfigSchema:
    """Create an empty plugin configuration schema.
    
    Returns:
        EmptyPluginConfigSchema instance
    """
    return EmptyPluginConfigSchema()


__all__ = [
    "OpenClawPluginConfigSchema",
    "EmptyPluginConfigSchema",
    "SafeParseResult",
    "SafeParseSuccess",
    "SafeParseError",
    "empty_plugin_config_schema",
]
