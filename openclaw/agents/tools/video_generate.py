"""video_generate tool — AI video generation.

Mirrors TypeScript src/agents/tools/video-generate-tool.ts
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

TOOL_NAME = "video_generate"
TOOL_DESCRIPTION = (
    "Generate a short video clip from a text prompt using AI. "
    "Returns a URL to the generated video file."
)


class VideoGenerateTool:
    """AI video generation tool."""

    name = TOOL_NAME
    description = TOOL_DESCRIPTION

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Text description of the video to generate.",
                },
                "duration_seconds": {
                    "type": "number",
                    "description": "Desired duration in seconds (max depends on provider).",
                    "default": 5,
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["16:9", "9:16", "1:1", "4:3"],
                    "description": "Video aspect ratio.",
                    "default": "16:9",
                },
                "resolution": {
                    "type": "string",
                    "description": "Video resolution, e.g. '720p', '1080p'.",
                    "default": "720p",
                },
            },
            "required": ["prompt"],
        }

    async def execute(self, params: dict[str, Any], context: Any = None) -> dict[str, Any]:
        prompt = params.get("prompt", "")
        if not prompt:
            return {"ok": False, "error": "prompt is required"}

        try:
            provider = _get_video_generation_provider()
            if provider:
                result = await provider.generate(params)
                return {"ok": True, **result}
        except Exception as exc:
            logger.exception("video_generate: provider error")
            return {"ok": False, "error": str(exc)}

        return {
            "ok": False,
            "error": "No video_generation_provider is registered. Install a video generation plugin.",
        }


def _get_video_generation_provider() -> Any | None:
    try:
        from openclaw.plugins.plugin_manager import get_global_plugin_registry
        registry = get_global_plugin_registry()
        if registry:
            providers = getattr(registry, "media_providers", {})
            items = providers.get("video_generation", [])
            if items:
                return items[-1]["provider"]
    except Exception:
        pass
    return None
