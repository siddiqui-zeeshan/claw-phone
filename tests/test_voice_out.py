"""Tests for core.voice_out.render_voice_note."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from spare_paw.core import voice_out


def _fake_config(**overrides):
    cfg = MagicMock()
    defaults = {
        "voice.ffmpeg_path": "ffmpeg",
    }
    defaults.update(overrides)
    cfg.get = lambda k, default=None: defaults.get(k, default)
    return cfg


class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self, input: bytes = b""):
        return (self._stdout, self._stderr)


@pytest.mark.asyncio
async def test_render_voice_note_happy_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        voice_out, "_tts_synthesize",
        AsyncMock(return_value=b"fake-mp3-bytes"),
    )
    fake_proc = _FakeProc(stdout=b"opus-ogg-bytes", stderr=b"", returncode=0)
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    )

    result = await voice_out.render_voice_note("hello", "nova", _fake_config())
    assert result == b"opus-ogg-bytes"


@pytest.mark.asyncio
async def test_render_voice_note_raises_when_ffmpeg_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr(
        voice_out, "_tts_synthesize",
        AsyncMock(return_value=b"fake-mp3-bytes"),
    )

    with pytest.raises(voice_out.VoiceRenderError, match="ffmpeg not found"):
        await voice_out.render_voice_note("hello", "nova", _fake_config())


@pytest.mark.asyncio
async def test_render_voice_note_raises_on_ffmpeg_nonzero_exit(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        voice_out, "_tts_synthesize",
        AsyncMock(return_value=b"fake-mp3-bytes"),
    )
    fake_proc = _FakeProc(stdout=b"", stderr=b"some ffmpeg error", returncode=1)
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    )

    with pytest.raises(voice_out.VoiceRenderError, match="ffmpeg failed"):
        await voice_out.render_voice_note("hello", "nova", _fake_config())


@pytest.mark.asyncio
async def test_render_voice_note_wraps_tts_error(monkeypatch):
    from spare_paw.router.tts import TTSError

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        voice_out, "_tts_synthesize",
        AsyncMock(side_effect=TTSError(500, "upstream boom")),
    )

    with pytest.raises(voice_out.VoiceRenderError, match="TTS"):
        await voice_out.render_voice_note("hello", "nova", _fake_config())
