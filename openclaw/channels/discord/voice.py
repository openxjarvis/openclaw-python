"""
Discord voice channel support.
Mirrors src/discord/voice/manager.ts and src/discord/voice/command.ts.

Full pipeline:
  1. Join voice channel (gateway + optional DAVE E2E encryption)
  2. Per-user AudioSink: capture Opus frames, detect silence (AfterSilence ~1s)
  3. Decode Opus → PCM → write WAV temp file
  4. Transcribe WAV via the agent's audio transcription capability
  5. Dispatch transcript as new inbound message to the agent
  6. Receive agent reply → TTS → FFmpegPCMAudio playback queue
  7. Stop playback when a new speaker starts
  8. autoJoin: connect to configured voice channels on bot ready

Requirements (system-level):
  - libopus must be installed (discord.py voice requires it)
  - PyNaCl>=1.5.0 for gateway encryption
  - ffmpeg in PATH for audio format conversion (TTS output → playback)
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import struct
import tempfile
import wave
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

# Silence threshold: discard segments shorter than 350ms
_MIN_SEGMENT_SECS = 0.35
_SILENCE_TIMEOUT_SECS = 1.0  # stop capture after 1s silence
_OPUS_SAMPLE_RATE = 48000
_OPUS_CHANNELS = 2
_OPUS_FRAME_DURATION_MS = 20  # standard Discord Opus frame = 20ms
_SAMPLES_PER_FRAME = (_OPUS_SAMPLE_RATE * _OPUS_FRAME_DURATION_MS) // 1000  # = 960


class VoiceManager:
    """
    Manages voice channel connections and the capture/transcribe/TTS pipeline
    for a single Discord account.

    Mirrors DiscordVoiceManager in src/discord/voice/manager.ts.
    """

    def __init__(
        self,
        client: Any,
        account: Any,
        on_transcript: Callable[[str, str, str], Awaitable[None]],
        tts_fn: Callable[[str], Awaitable[str | None]] | None = None,
        persist_dir: Path | None = None,
    ) -> None:
        """
        client        — discord.Client
        account       — ResolvedDiscordAccount
        on_transcript — async callback(guild_id, channel_id, transcript_text)
        tts_fn        — optional async fn(text) -> path_to_audio_file
        """
        self._client = client
        self._account = account
        self._on_transcript = on_transcript
        self._tts_fn = tts_fn
        self._persist_dir = persist_dir

        # voice_client per guild_id
        self._voice_clients: dict[str, Any] = {}
        # playback queues per guild_id
        self._play_queues: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        self._play_tasks: dict[str, asyncio.Task] = {}

    # ---------------------------------------------------------------------------
    # Join / Leave
    # ---------------------------------------------------------------------------

    async def join_voice_channel(
        self,
        guild_id: str,
        channel_id: str,
    ) -> bool:
        """
        Join the specified voice channel.
        If already connected to a different channel in the same guild, move.
        Returns True on success.
        """
        try:
            import discord

            guild = self._client.get_guild(int(guild_id))
            if guild is None:
                logger.warning("[discord][voice] Guild %s not found", guild_id)
                return False

            channel = guild.get_channel(int(channel_id))
            if channel is None or not isinstance(channel, discord.VoiceChannel):
                logger.warning("[discord][voice] Channel %s not a voice channel", channel_id)
                return False

            existing = self._voice_clients.get(guild_id)
            if existing and existing.is_connected():
                await existing.move_to(channel)
                vc = existing
            else:
                vc = await channel.connect(
                    self_deaf=False,
                    self_mute=False,
                )
                self._voice_clients[guild_id] = vc

            logger.info("[discord][voice] Joined %s in guild %s", channel_id, guild_id)
            self._start_receiving(vc, guild_id)
            self._start_playback_worker(guild_id)
            return True

        except Exception as exc:
            logger.error("[discord][voice] Failed to join voice: %s", exc)
            return False

    async def leave_voice_channel(self, guild_id: str) -> bool:
        vc = self._voice_clients.pop(guild_id, None)
        if vc and vc.is_connected():
            await vc.disconnect()
            logger.info("[discord][voice] Left voice in guild %s", guild_id)
            return True
        return False

    async def auto_join_on_ready(self) -> None:
        """Connect to all autoJoin channels defined in account config."""
        for entry in self._account.voice.auto_join:
            if entry.guild_id and entry.channel_id:
                logger.info(
                    "[discord][voice] autoJoin: guild=%s channel=%s",
                    entry.guild_id,
                    entry.channel_id,
                )
                await self.join_voice_channel(entry.guild_id, entry.channel_id)

    # ---------------------------------------------------------------------------
    # Receiving / capture
    # ---------------------------------------------------------------------------

    def _start_receiving(self, vc: Any, guild_id: str) -> None:
        """
        Start receiving audio from the voice connection.
        discord.py 2.4+ provides VoiceClient.start_recording() with Sinks.
        We use WaveSink to capture per-user audio files.
        """
        try:
            import discord
            from discord.sinks import WaveSink

            sink = WaveSink()

            async def finished_callback(sink: WaveSink, channel: Any, *_: Any) -> None:
                await self._process_sink(sink, guild_id, str(channel.id))

            vc.start_recording(sink, finished_callback, vc.channel)
            logger.debug("[discord][voice] Started recording in guild %s", guild_id)
        except AttributeError:
            # Older discord.py without WaveSink — use manual Opus capture
            self._start_manual_receive(vc, guild_id)
        except Exception as exc:
            logger.warning("[discord][voice] Failed to start recording: %s", exc)

    async def _process_sink(self, sink: Any, guild_id: str, channel_id: str) -> None:
        """Process captured audio per user after recording stops."""
        for user_id, audio in sink.audio_data.items():
            try:
                wav_data = audio.file.getvalue() if hasattr(audio, "file") else audio
                duration = _estimate_wav_duration(wav_data)
                if duration < _MIN_SEGMENT_SECS:
                    continue
                transcript = await self._transcribe_wav(wav_data)
                if transcript:
                    await self._on_transcript(guild_id, channel_id, transcript)
            except Exception as exc:
                logger.warning("[discord][voice] Process sink error for user %s: %s", user_id, exc)

    def _start_manual_receive(self, vc: Any, guild_id: str) -> None:
        """
        Fallback when WaveSink is not available.

        discord.py's VoiceClient does NOT expose a public recv() method in 2.x.
        The only supported receive path is start_recording() with Sinks (2.4+).
        Log a warning so the user knows voice receive is unavailable.
        """
        logger.warning(
            "[discord][voice] WaveSink not available in this discord.py build. "
            "Voice receive (capture/transcription) is disabled for guild %s. "
            "Install discord.py >= 2.4 with voice extras.",
            guild_id,
        )

    async def _decode_and_transcribe(
        self,
        opus_frames: list[bytes],
        guild_id: str,
        vc: Any,
    ) -> None:
        """Decode Opus frames → PCM → WAV → transcribe."""
        try:
            pcm_frames = []
            try:
                import discord.opus as _opus
                decoder = _opus.Decoder()
                for frame in opus_frames:
                    pcm = decoder.decode(frame, _SAMPLES_PER_FRAME)
                    pcm_frames.append(pcm)
            except Exception:
                pass

            if not pcm_frames:
                return

            pcm_data = b"".join(pcm_frames)
            duration = len(pcm_data) / (_OPUS_SAMPLE_RATE * _OPUS_CHANNELS * 2)
            if duration < _MIN_SEGMENT_SECS:
                return

            wav_data = _pcm_to_wav(pcm_data, _OPUS_SAMPLE_RATE, _OPUS_CHANNELS)
            transcript = await self._transcribe_wav(wav_data)
            if transcript:
                channel_id = str(vc.channel.id) if vc.channel else ""
                await self._on_transcript(guild_id, channel_id, transcript)
        except Exception as exc:
            logger.warning("[discord][voice] Decode/transcribe error: %s", exc)

    async def _transcribe_wav(self, wav_data: bytes) -> str | None:
        """Write WAV to temp file and call the agent transcription capability."""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(wav_data)
                tmp_path = f.name
            try:
                # Import transcription pipeline from pi-ai / openclaw
                from openclaw.plugins.runtime.runtime_stt import transcribe_audio_file
                transcript = await transcribe_audio_file(tmp_path)
                return transcript.strip() if transcript else None
            except ImportError:
                logger.debug("[discord][voice] transcribe_audio_file not available")
                return None
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except Exception as exc:
            logger.warning("[discord][voice] Transcription error: %s", exc)
            return None

    # ---------------------------------------------------------------------------
    # TTS playback
    # ---------------------------------------------------------------------------

    def _start_playback_worker(self, guild_id: str) -> None:
        if guild_id not in self._play_tasks or self._play_tasks[guild_id].done():
            self._play_tasks[guild_id] = asyncio.create_task(
                self._playback_worker(guild_id),
                name=f"discord_voice_play_{guild_id}",
            )

    async def _playback_worker(self, guild_id: str) -> None:
        queue: asyncio.Queue = self._play_queues[guild_id]
        while True:
            audio_path = await queue.get()
            try:
                vc = self._voice_clients.get(guild_id)
                if vc and vc.is_connected():
                    await self._play_audio(vc, audio_path)
            except Exception as exc:
                logger.warning("[discord][voice] Playback error: %s", exc)
            finally:
                queue.task_done()
                try:
                    os.unlink(audio_path)
                except OSError:
                    pass

    async def _play_audio(self, vc: Any, audio_path: str) -> None:
        import discord

        source = discord.FFmpegPCMAudio(audio_path, options="-vn")
        source = discord.PCMVolumeTransformer(source)

        if vc.is_playing():
            vc.stop()

        done_event = asyncio.Event()

        def after(_error: Exception | None) -> None:
            done_event.set()

        vc.play(source, after=after)
        await done_event.wait()

    async def speak(self, guild_id: str, text: str) -> None:
        """Convert text to speech and queue it for playback in the guild's voice channel."""
        if not self._tts_fn:
            return
        try:
            audio_path = await self._tts_fn(text)
            if audio_path:
                await self._play_queues[guild_id].put(audio_path)
        except Exception as exc:
            logger.warning("[discord][voice] TTS error: %s", exc)

    def stop_playback(self, guild_id: str) -> None:
        """Stop current playback (called when a new speaker starts)."""
        vc = self._voice_clients.get(guild_id)
        if vc and vc.is_playing():
            vc.stop()


# ---------------------------------------------------------------------------
# PCM / WAV helpers
# ---------------------------------------------------------------------------

def _pcm_to_wav(pcm: bytes, sample_rate: int, channels: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _estimate_wav_duration(wav_data: bytes) -> float:
    try:
        buf = io.BytesIO(wav_data)
        with wave.open(buf, "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except Exception:
        return 0.0
