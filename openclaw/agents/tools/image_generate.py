"""image_generate tool — AI image generation.

Mirrors TypeScript src/agents/tools/image-generate-tool.ts

Delegates to the image_generation_provider registered via plugin API.
If no provider is registered, returns an informative error.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

TOOL_NAME = "image_generate"
TOOL_DESCRIPTION = (
    "Generate an image from a text prompt using AI. "
    "Returns the image as a URL or base64 data URI."
)


class ImageGenerateTool:
    """AI image generation tool."""

    name = TOOL_NAME
    description = TOOL_DESCRIPTION

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Text description of the image to generate.",
                },
                "size": {
                    "type": "string",
                    "description": "Image dimensions, e.g. '1024x1024', '1792x1024'.",
                    "default": "1024x1024",
                },
                "quality": {
                    "type": "string",
                    "enum": ["standard", "hd"],
                    "description": "Image quality level.",
                    "default": "standard",
                },
                "style": {
                    "type": "string",
                    "enum": ["natural", "vivid"],
                    "description": "Image style.",
                    "default": "natural",
                },
                "n": {
                    "type": "integer",
                    "description": "Number of images to generate.",
                    "default": 1,
                },
            },
            "required": ["prompt"],
        }

    async def execute(self, params: dict[str, Any], context: Any = None) -> dict[str, Any]:
        prompt = params.get("prompt", "")
        if not prompt:
            return {"ok": False, "error": "prompt is required"}

        # Try plugin-registered image generation provider
        try:
            provider = _get_image_generation_provider()
            if provider:
                result = await provider.generate(params)
                return {"ok": True, **result}
        except Exception as exc:
            logger.exception("image_generate: provider error")
            return {"ok": False, "error": str(exc)}

        return {
            "ok": False,
            "error": "No image_generation_provider is registered. Install an image generation plugin.",
        }


def _get_image_generation_provider() -> Any | None:
    """Get the registered image generation provider from plugin registry."""
    try:
        from openclaw.plugins.plugin_manager import get_global_plugin_registry
        registry = get_global_plugin_registry()
        if registry:
            providers = getattr(registry, "media_providers", {})
            image_providers = providers.get("image_generation", [])
            if image_providers:
                return image_providers[-1]["provider"]  # last registered wins
    except Exception:
        pass
    return None
