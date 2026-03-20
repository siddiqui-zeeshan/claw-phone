"""Minimal HTTP webhook backend implementing MessageBackend.

Provides a simple REST API for sending/receiving messages without Telegram.
Useful for headless testing, Docker deployments, and custom integrations.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from aiohttp import web

from spare_paw.backend import IncomingMessage

logger = logging.getLogger(__name__)


class WebhookBackend:
    """HTTP-based message backend with long-poll response delivery."""

    def __init__(self, port: int = 8080, secret: str = "", app_state: Any = None) -> None:
        self._port = port
        self._secret = secret
        self._app_state = app_state
        self._response_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._web_app: web.Application | None = None
        self._runner: web.AppRunner | None = None

    def _check_auth(self, request: web.Request) -> bool:
        if not self._secret:
            return True
        auth = request.headers.get("Authorization", "")
        return auth == f"Bearer {self._secret}"

    async def _handle_message(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response({"error": "Invalid JSON"}, status=400)

        text = data.get("text")
        image_b64 = data.get("image")
        voice_b64 = data.get("voice")

        image_bytes = base64.b64decode(image_b64) if image_b64 else None
        voice_bytes = base64.b64decode(voice_b64) if voice_b64 else None

        msg = IncomingMessage(
            text=text,
            image_bytes=image_bytes,
            voice_bytes=voice_bytes,
        )

        if self._app_state is not None:
            asyncio.create_task(self._process_message(msg))

        return web.json_response({"status": "accepted"})

    async def _process_message(self, msg: IncomingMessage) -> None:
        """Route an incoming message through the engine."""
        try:
            from spare_paw.core.engine import process_message

            await process_message(self._app_state, msg, self)
        except Exception:
            logger.exception("Error processing webhook message")

    async def _handle_poll(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        timeout = float(request.query.get("timeout", "30"))
        messages: list[dict[str, Any]] = []

        try:
            msg = await asyncio.wait_for(self._response_queue.get(), timeout=timeout)
            messages.append(msg)
            while not self._response_queue.empty():
                messages.append(self._response_queue.get_nowait())
        except asyncio.TimeoutError:
            pass

        return web.json_response({"messages": messages})

    async def _handle_health(self, _request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    # -- MessageBackend protocol --

    async def send_text(self, text: str) -> None:
        await self._response_queue.put({"type": "text", "text": text})

    async def send_file(self, path: str, caption: str = "") -> None:
        await self._response_queue.put({
            "type": "file",
            "url": f"/files/{path}",
            "caption": caption,
        })

    async def send_typing(self) -> None:
        pass

    async def send_notification(
        self, text: str, actions: list[dict] | None = None
    ) -> None:
        await self._response_queue.put({
            "type": "notification",
            "text": text,
            "actions": actions or [],
        })

    async def start(self) -> None:
        self._web_app = web.Application()
        self._web_app.router.add_post("/message", self._handle_message)
        self._web_app.router.add_get("/poll", self._handle_poll)
        self._web_app.router.add_get("/health", self._handle_health)

        self._runner = web.AppRunner(self._web_app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()
        logger.info("Webhook backend listening on port %d", self._port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            logger.info("Webhook backend stopped")
