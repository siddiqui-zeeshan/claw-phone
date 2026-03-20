"""Tests for the webhook backend."""

from __future__ import annotations

import pytest

from spare_paw.backend import MessageBackend
from spare_paw.webhook.backend import WebhookBackend


class TestWebhookBackend:
    def test_implements_protocol(self):
        backend = WebhookBackend()
        assert isinstance(backend, MessageBackend)

    @pytest.mark.asyncio
    async def test_send_text_queues_message(self):
        backend = WebhookBackend()
        await backend.send_text("hello")
        msg = backend._response_queue.get_nowait()
        assert msg == {"type": "text", "text": "hello"}

    @pytest.mark.asyncio
    async def test_send_file_queues_message(self):
        backend = WebhookBackend()
        await backend.send_file("/tmp/test.txt", caption="test")
        msg = backend._response_queue.get_nowait()
        assert msg["type"] == "file"
        assert msg["caption"] == "test"

    @pytest.mark.asyncio
    async def test_send_typing_is_noop(self):
        backend = WebhookBackend()
        await backend.send_typing()

    @pytest.mark.asyncio
    async def test_send_notification_queues_message(self):
        backend = WebhookBackend()
        await backend.send_notification("alert", actions=[{"label": "OK"}])
        msg = backend._response_queue.get_nowait()
        assert msg["type"] == "notification"
        assert msg["text"] == "alert"
        assert msg["actions"] == [{"label": "OK"}]

    @pytest.mark.asyncio
    async def test_start_stop(self):
        backend = WebhookBackend(port=18923)
        await backend.start()
        assert backend._runner is not None
        await backend.stop()

    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        backend = WebhookBackend(port=18924)
        await backend.start()
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get("http://localhost:18924/health") as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["status"] == "ok"
        finally:
            await backend.stop()

    @pytest.mark.asyncio
    async def test_message_endpoint_auth(self):
        backend = WebhookBackend(port=18925, secret="test-secret")
        await backend.start()
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                # No auth
                async with session.post(
                    "http://localhost:18925/message",
                    json={"text": "hi"},
                ) as resp:
                    assert resp.status == 401

                # With auth
                async with session.post(
                    "http://localhost:18925/message",
                    json={"text": "hi"},
                    headers={"Authorization": "Bearer test-secret"},
                ) as resp:
                    assert resp.status == 200
        finally:
            await backend.stop()
