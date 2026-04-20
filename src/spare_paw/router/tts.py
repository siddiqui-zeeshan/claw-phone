"""OpenRouter text-to-speech client.

Single function: synthesize(text, voice, config) -> bytes.

Follows the same retry/backoff pattern as router/openrouter.py but keeps
its own aiohttp session so TTS calls don't contend with chat calls on
the completions-endpoint rate limiter.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

TTS_URL = "https://openrouter.ai/api/v1/audio/speech"

KNOWN_VOICES: tuple[str, ...] = (
    "alloy", "ash", "ballad", "coral", "echo",
    "fable", "nova", "onyx", "sage", "shimmer",
)

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_NON_RETRYABLE_STATUSES = frozenset({400, 401, 403})
_MAX_RETRIES = 3
_BASE_DELAY = 1.0
_MAX_DELAY = 30.0


class TTSError(Exception):
    """Raised on non-retryable TTS failures or after retries are exhausted."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"TTS error {status}: {message}")


_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def close_session() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
        _session = None


async def synthesize(text: str, voice: str, config: Any) -> bytes:
    """Turn *text* into raw audio bytes via OpenRouter TTS."""
    if voice not in KNOWN_VOICES:
        raise ValueError(f"Unknown voice: {voice!r}. Known: {KNOWN_VOICES}")

    api_key = config.get("openrouter_api_key")
    model = config.get("voice.tts_model", "openai/gpt-4o-mini-tts-2025-12-15")
    timeout_s = config.get("voice.tts_timeout_seconds", 30)

    body = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": "mp3",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://github.com/spare-paw/spare-paw",
        "X-Title": "spare-paw",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=timeout_s)

    session = _get_session()
    last_exception: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with session.post(TTS_URL, json=body, headers=headers, timeout=timeout) as resp:
                if resp.status in _NON_RETRYABLE_STATUSES:
                    text_body = await resp.text()
                    raise TTSError(resp.status, text_body[:200])
                if resp.status in _RETRYABLE_STATUSES:
                    text_body = await resp.text()
                    last_exception = TTSError(resp.status, text_body[:200])
                    if attempt < _MAX_RETRIES:
                        delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
                        logger.warning(
                            "TTS %d on attempt %d/%d, retrying in %.1fs",
                            resp.status, attempt + 1, _MAX_RETRIES + 1, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise last_exception
                if resp.status >= 300:
                    text_body = await resp.text()
                    raise TTSError(resp.status, text_body[:200])
                audio = await resp.read()
                logger.info(
                    "TTS ok: voice=%s chars=%d bytes=%d model=%s",
                    voice, len(text), len(audio), model,
                )
                return audio
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_exception = exc
            if attempt < _MAX_RETRIES:
                delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
                logger.warning(
                    "TTS connection error on attempt %d/%d (%s), retrying in %.1fs",
                    attempt + 1, _MAX_RETRIES + 1, exc, delay,
                )
                await asyncio.sleep(delay)
                continue

    assert last_exception is not None
    if isinstance(last_exception, TTSError):
        raise last_exception
    raise TTSError(0, f"connection error after retries: {last_exception}")
