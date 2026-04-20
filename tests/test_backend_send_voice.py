"""Tests for TelegramBackend.send_voice."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from spare_paw.bot.backend import TelegramBackend


@pytest.mark.asyncio
async def test_send_voice_calls_bot_send_voice_with_buffered_input():
    application = MagicMock()
    application.bot = MagicMock()
    application.bot.send_voice = AsyncMock()

    backend = TelegramBackend(application=application, chat_id=42)
    await backend.send_voice(b"fake-ogg-bytes")

    application.bot.send_voice.assert_awaited_once()
    kwargs = application.bot.send_voice.await_args.kwargs
    assert kwargs["chat_id"] == 42
    voice_arg = kwargs["voice"]
    if hasattr(voice_arg, "input_file_content"):
        assert voice_arg.input_file_content == b"fake-ogg-bytes"
    elif hasattr(voice_arg, "getvalue"):
        assert voice_arg.getvalue() == b"fake-ogg-bytes"
    else:
        pytest.fail(f"Unexpected voice arg type: {type(voice_arg)}")


@pytest.mark.asyncio
async def test_send_voice_propagates_telegram_errors():
    application = MagicMock()
    application.bot = MagicMock()
    application.bot.send_voice = AsyncMock(side_effect=RuntimeError("telegram boom"))

    backend = TelegramBackend(application=application, chat_id=42)
    with pytest.raises(RuntimeError, match="telegram boom"):
        await backend.send_voice(b"anything")
