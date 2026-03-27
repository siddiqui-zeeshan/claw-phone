"""Tests for bot/handler.py — Telegram message handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestQueueMessageVideo:
    @pytest.mark.asyncio
    async def test_video_message_creates_incoming_with_video_bytes(self):
        """Video message populates video_bytes and video_mime on IncomingMessage."""
        from spare_paw.bot.handler import _queue_message

        update = MagicMock()
        update.effective_user.id = 12345
        update.message.text = None
        update.message.voice = None
        update.message.photo = None
        update.message.caption = "check this"
        update.message.reply_to_message = None

        video_file = MagicMock()
        video_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"\x00\x01\x02"))
        video_obj = MagicMock()
        video_obj.get_file = AsyncMock(return_value=video_file)
        video_obj.mime_type = "video/webm"
        update.message.video = video_obj
        update.message.video_note = None

        context = MagicMock()
        app_state = MagicMock()
        app_state.config.get.return_value = 12345
        context.bot_data = {"app_state": app_state}

        with patch("spare_paw.core.engine.enqueue", new_callable=AsyncMock) as mock_enqueue:
            await _queue_message(update, context)

        msg = mock_enqueue.call_args.args[0]
        assert msg.video_bytes == b"\x00\x01\x02"
        assert msg.video_mime == "video/webm"

    @pytest.mark.asyncio
    async def test_video_note_uses_mp4_mime(self):
        """Video note (circle video) always uses video/mp4."""
        from spare_paw.bot.handler import _queue_message

        update = MagicMock()
        update.effective_user.id = 12345
        update.message.text = None
        update.message.voice = None
        update.message.photo = None
        update.message.video = None
        update.message.caption = None
        update.message.reply_to_message = None

        video_note_file = MagicMock()
        video_note_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"\x00\x01"))
        video_note_obj = MagicMock()
        video_note_obj.get_file = AsyncMock(return_value=video_note_file)
        update.message.video_note = video_note_obj

        context = MagicMock()
        app_state = MagicMock()
        app_state.config.get.return_value = 12345
        context.bot_data = {"app_state": app_state}

        with patch("spare_paw.core.engine.enqueue", new_callable=AsyncMock) as mock_enqueue:
            await _queue_message(update, context)

        msg = mock_enqueue.call_args.args[0]
        assert msg.video_bytes == b"\x00\x01"
        assert msg.video_mime == "video/mp4"
