"""TTS + ffmpeg pipeline.

Wraps `router.tts.synthesize` + an `ffmpeg` subprocess that transcodes
the returned mp3 to opus-in-ogg, which is the codec Telegram voice
notes need.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

from spare_paw.router.tts import TTSError, synthesize as _tts_synthesize

logger = logging.getLogger(__name__)


class VoiceRenderError(Exception):
    """Raised when rendering a voice note fails (TTS, ffmpeg, or env setup)."""


async def render_voice_note(text: str, voice: str, config: Any) -> bytes:
    """Render `text` to opus/ogg bytes, ready for Telegram `send_voice`."""
    ffmpeg_path = config.get("voice.ffmpeg_path", "ffmpeg")
    resolved = shutil.which(ffmpeg_path)
    if resolved is None:
        raise VoiceRenderError(
            f"ffmpeg not found (looked for {ffmpeg_path!r} on $PATH)"
        )

    try:
        mp3_bytes = await _tts_synthesize(text, voice, config)
    except TTSError as exc:
        raise VoiceRenderError(f"TTS failed: {exc}") from exc

    proc = await asyncio.create_subprocess_exec(
        resolved,
        "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0",
        "-c:a", "libopus",
        "-b:a", "32k",
        "-f", "ogg",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=mp3_bytes)
    if proc.returncode != 0:
        tail = stderr.decode("utf-8", errors="replace")[-200:]
        raise VoiceRenderError(f"ffmpeg failed (exit {proc.returncode}): {tail}")
    return stdout
