"""Tests for router.tts.synthesize."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from spare_paw.router import tts


def _fake_config(**overrides):
    cfg = MagicMock()
    defaults = {
        "voice.tts_model": "openai/gpt-4o-mini-tts-2025-12-15",
        "voice.tts_timeout_seconds": 30,
        "openrouter.api_key": "test-key",
    }
    defaults.update(overrides)
    cfg.get = lambda k, default=None: defaults.get(k, default)
    return cfg


def test_known_voices_contains_nova():
    assert "nova" in tts.KNOWN_VOICES


def test_known_voices_is_tuple():
    assert isinstance(tts.KNOWN_VOICES, tuple)


@pytest.mark.asyncio
async def test_synthesize_unknown_voice_raises_before_http(monkeypatch):
    monkeypatch.setattr(
        "aiohttp.ClientSession.post",
        AsyncMock(side_effect=AssertionError("HTTP should not be called")),
    )
    with pytest.raises(ValueError, match="Unknown voice"):
        await tts.synthesize("hello", "not-a-real-voice", _fake_config())


@pytest.mark.asyncio
async def test_synthesize_success_returns_bytes(monkeypatch):
    audio = b"\x00\x01\x02\x03fake-mp3-bytes"

    class FakeResp:
        status = 200
        async def read(self): return audio
        async def text(self): return ""
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False

    class FakeSession:
        def post(self, *a, **kw): return FakeResp()
        async def close(self): pass
        closed = False

    monkeypatch.setattr(tts, "_get_session", lambda: FakeSession())

    result = await tts.synthesize("hello world", "nova", _fake_config())
    assert result == audio


@pytest.mark.asyncio
async def test_synthesize_sends_instructions_when_configured(monkeypatch):
    captured = {}

    class FakeResp:
        status = 200
        async def read(self): return b"audio"
        async def text(self): return ""
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False

    class FakeSession:
        def post(self, url, json=None, headers=None, timeout=None):
            captured["body"] = json
            return FakeResp()
        closed = False

    monkeypatch.setattr(tts, "_get_session", lambda: FakeSession())

    cfg = _fake_config(**{"voice.tts_instructions": "Speak warmly."})
    await tts.synthesize("hi", "nova", cfg)
    assert captured["body"]["instructions"] == "Speak warmly."


@pytest.mark.asyncio
async def test_synthesize_omits_instructions_when_not_configured(monkeypatch):
    captured = {}

    class FakeResp:
        status = 200
        async def read(self): return b"audio"
        async def text(self): return ""
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False

    class FakeSession:
        def post(self, url, json=None, headers=None, timeout=None):
            captured["body"] = json
            return FakeResp()
        closed = False

    monkeypatch.setattr(tts, "_get_session", lambda: FakeSession())

    await tts.synthesize("hi", "nova", _fake_config())
    assert "instructions" not in captured["body"]


@pytest.mark.asyncio
async def test_synthesize_429_retries_then_succeeds(monkeypatch):
    call_count = {"n": 0}
    audio = b"final-bytes"

    class FakeResp429:
        status = 429
        async def read(self): return b""
        async def text(self): return "rate limited"
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False

    class FakeResp200:
        status = 200
        async def read(self): return audio
        async def text(self): return ""
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False

    class FakeSession:
        def post(self, *a, **kw):
            call_count["n"] += 1
            return FakeResp429() if call_count["n"] == 1 else FakeResp200()
        closed = False

    monkeypatch.setattr(tts, "_get_session", lambda: FakeSession())
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    result = await tts.synthesize("hello", "nova", _fake_config())
    assert result == audio
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_synthesize_401_raises_tts_error(monkeypatch):
    class FakeResp:
        status = 401
        async def read(self): return b""
        async def text(self): return "unauthorized"
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False

    class FakeSession:
        def post(self, *a, **kw): return FakeResp()
        closed = False

    monkeypatch.setattr(tts, "_get_session", lambda: FakeSession())

    with pytest.raises(tts.TTSError) as excinfo:
        await tts.synthesize("hello", "nova", _fake_config())
    assert "401" in str(excinfo.value)


@pytest.mark.asyncio
async def test_synthesize_error_does_not_leak_api_key(monkeypatch):
    class FakeResp:
        status = 500
        async def read(self): return b""
        async def text(self): return "internal error body"
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False

    class FakeSession:
        def post(self, *a, **kw): return FakeResp()
        closed = False

    monkeypatch.setattr(tts, "_get_session", lambda: FakeSession())
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    cfg = _fake_config(**{"openrouter.api_key": "super-secret-key-xyz"})
    with pytest.raises(tts.TTSError) as excinfo:
        await tts.synthesize("hello", "nova", cfg)
    assert "super-secret-key-xyz" not in str(excinfo.value)
