"""Plugin runtime STT module — mirrors src/plugins/runtime/types-core.ts#L33"""
from __future__ import annotations

from pathlib import Path
from typing import Any


async def transcribe_audio_file(
    file_path: str | Path,
    cfg: dict[str, Any],
    agent_dir: str | None = None,
    mime: str | None = None,
) -> dict[str, str | None]:
    """
    Transcribe an audio file using the configured media-understanding provider.
    
    Reads provider/model/apiKey from tools.media.audio in the openclaw config,
    falling back through configured models until one succeeds.
    
    This is the runtime-exposed entry point for external plugins (e.g. marmot)
    that need STT without importing internal media-understanding modules directly.
    
    Args:
        file_path: Path to audio file
        cfg: OpenClaw config dict
        agent_dir: Optional agent directory
        mime: Optional MIME type
    
    Returns:
        Dict with "text" key containing transcript (or None if failed)
    """
    from openclaw.media_understanding.audio import AudioAnalyzer
    from openclaw.media_understanding.types import Provider
    
    # Resolve provider from config
    media_config = cfg.get("tools", {}).get("media", {})
    audio_config = media_config.get("audio", {})
    
    # Build provider config
    provider_config = {
        "openai_api_key": audio_config.get("openaiApiKey"),
        "deepgram_api_key": audio_config.get("deepgramApiKey"),
        "groq_api_key": audio_config.get("groqApiKey"),
    }
    
    # Get provider name
    provider_name = audio_config.get("provider")
    provider = None
    if provider_name:
        try:
            provider = Provider(provider_name.lower())
        except ValueError:
            pass
    
    analyzer = AudioAnalyzer(config=provider_config)
    
    try:
        result = await analyzer.analyze(
            path=file_path,
            provider=provider,
        )
        
        if result.success:
            return {"text": result.text}
        else:
            return {"text": None}
    except Exception:
        return {"text": None}


def create_runtime_stt():
    """Create runtime.stt module — mirrors TS src/plugins/runtime/types-core.ts"""
    return {
        "transcribe_audio_file": transcribe_audio_file,
    }
