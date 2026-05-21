"""music_generate tool — AI music/audio generation.

Mirrors TypeScript src/agents/tools/music-generate-tool.ts
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

TOOL_NAME = "music_generate"
TOOL_DESCRIPTION = (
    "Generate music or audio from a text prompt using AI. "
    "Returns a URL to the generated audio file."
)


class MusicGenerateTool:
    """AI music generation tool."""

    name = TOOL_NAME
    description = TOOL_DESCRIPTION

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Text description of the music/audio to generate.",
                },
                "duration_seconds": {
                    "type": "number",
                    "description": "Desired duration in seconds.",
                    "default": 30,
                },
                "style": {
                    "type": "string",
                    "description": "Musical style or genre (e.g. 'jazz', 'electronic', 'classical').",
                },
            },
            "required": ["prompt"],
        }

    async def execute(self, params: dict[str, Any], context: Any = None) -> dict[str, Any]:
        prompt = params.get("prompt", "")
        if not prompt:
            return {"ok": False, "error": "prompt is required"}

        try:
            provider = _get_music_generation_provider()
            if provider:
                result = await provider.generate(params)
                return {"ok": True, **result}
        except Exception as exc:
            logger.exception("music_generate: provider error")
            return {"ok": False, "error": str(exc)}

        return {
            "ok": False,
            "error": "No music_generation_provider is registered. Install a music generation plugin.",
        }


def _get_music_generation_provider() -> Any | None:
    try:
        from openclaw.plugins.plugin_manager import get_global_plugin_registry
        registry = get_global_plugin_registry()
        if registry:
            providers = getattr(registry, "media_providers", {})
            items = providers.get("music_generation", [])
            if items:
                return items[-1]["provider"]
    except Exception:
        pass
    return None
