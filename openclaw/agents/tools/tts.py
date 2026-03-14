"""Text-to-Speech tool"""

import logging
from pathlib import Path
from typing import Any

from .base import AgentTool, ToolResult

logger = logging.getLogger(__name__)


class TTSTool(AgentTool):
    """
    Convert text to speech using configured TTS providers.
    
    Aligned with TypeScript openclaw/src/agents/tools/tts-tool.ts.
    
    Supports providers: openai, elevenlabs, google, azure, etc.
    Audio is delivered automatically from the tool result.
    """

    def __init__(self, workspace_root: Path | None = None, config: dict[str, Any] | None = None):
        super().__init__()
        self.name = "tts"
        # Matches TS tts-tool.ts:24 description with SILENT_REPLY_TOKEN hint
        self.description = (
            "Convert text to speech. Audio is delivered automatically from the tool result — "
            "reply with [[SILENT_REPLY]] after a successful call to avoid duplicate messages."
        )
        self.workspace_root = workspace_root
        self.config = config or {}

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to convert to speech."
                },
                "channel": {
                    "type": "string",
                    "description": "Optional channel id to pick output format (e.g. telegram)."
                },
            },
            "required": ["text"],
        }

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Convert text to speech"""
        text = params.get("text", "")
        output_path = params.get("output_path", "output.mp3")
        provider = params.get("provider", "openai")
        voice = params.get("voice", "alloy")
        model = params.get("model", "tts-1")

        if not text:
            return ToolResult(success=False, content="", error="text required")
        
        # Resolve output path relative to workspace if available
        if self.workspace_root:
            output_file = Path(self.workspace_root) / output_path
        else:
            output_file = Path(output_path).expanduser()

        try:
            if provider == "openai":
                return await self._openai_tts(text, output_file, voice, model)
            elif provider == "elevenlabs":
                return await self._elevenlabs_tts(text, output_file, voice)
            else:
                return ToolResult(success=False, content="", error=f"Unknown provider: {provider}")

        except Exception as e:
            logger.error(f"TTS error: {e}", exc_info=True)
            return ToolResult(
                success=False,
                content="",
                error=f"TTS failed: {str(e)}. Please check your API key and try again."
            )

    async def _openai_tts(self, text: str, output_file: Path, voice: str, model: str) -> ToolResult:
        """Generate speech using OpenAI TTS"""
        try:
            import os

            from openai import AsyncOpenAI

            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                return ToolResult(
                    success=False,
                    content="",
                    error="OPENAI_API_KEY not set. Please configure your OpenAI API key first."
                )

            client = AsyncOpenAI(api_key=api_key)

            # Ensure output directory exists
            output_file.parent.mkdir(parents=True, exist_ok=True)

            response = await client.audio.speech.create(model=model, voice=voice, input=text)

            # Save to file
            response.stream_to_file(str(output_file))
            
            # Return with MEDIA: prefix for delivery
            # Use absolute path to ensure file can be found
            result_content = f"MEDIA:{output_file.absolute()}\nSpeech generated successfully using {voice} voice."

            return ToolResult(
                success=True,
                content=result_content,
                metadata={
                    "output_path": str(output_file.absolute()),
                    "provider": "openai",
                    "voice": voice,
                    "model": model,
                },
            )

        except ImportError:
            return ToolResult(
                success=False,
                content="",
                error="openai package not installed. Install with: pip install openai"
            )
        except Exception as e:
            logger.error(f"OpenAI TTS error: {e}", exc_info=True)
            return ToolResult(
                success=False,
                content="",
                error=f"OpenAI TTS failed: {str(e)}"
            )

    async def _elevenlabs_tts(self, text: str, output_file: Path, voice: str) -> ToolResult:
        """Generate speech using ElevenLabs"""
        try:
            import os

            from elevenlabs import save
            from elevenlabs.client import ElevenLabs

            api_key = os.getenv("ELEVENLABS_API_KEY")
            if not api_key:
                return ToolResult(
                    success=False,
                    content="",
                    error="ELEVENLABS_API_KEY not set. Please configure your ElevenLabs API key first."
                )

            client = ElevenLabs(api_key=api_key)

            # Generate speech
            audio = client.generate(text=text, voice=voice, model="eleven_monolingual_v1")

            # Ensure output directory exists
            output_file.parent.mkdir(parents=True, exist_ok=True)

            # Save to file
            save(audio, str(output_file))
            
            # Return with MEDIA: prefix for delivery
            # Use absolute path to ensure file can be found
            result_content = f"MEDIA:{output_file.absolute()}\nSpeech generated successfully using {voice} voice."

            return ToolResult(
                success=True,
                content=result_content,
                metadata={
                    "output_path": str(output_file.absolute()),
                    "provider": "elevenlabs",
                    "voice": voice,
                },
            )

        except ImportError:
            return ToolResult(
                success=False,
                content="",
                error="elevenlabs package not installed. Install with: pip install elevenlabs",
            )
        except Exception as e:
            logger.error(f"ElevenLabs TTS error: {e}", exc_info=True)
            return ToolResult(
                success=False,
                content="",
                error=f"ElevenLabs TTS failed: {str(e)}"
            )
