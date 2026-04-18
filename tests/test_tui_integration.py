from __future__ import annotations

import pytest

from spare_paw.router.openrouter import StreamChunk
from spare_paw.tui.app import SparePawTUI
from spare_paw.tui.backend import TUIBackend
from spare_paw.tui.widgets.chat_log import ChatLog
from spare_paw.tui.widgets.message_view import MessageView


class _FakeClient:
    """Minimal OpenRouterClient stub that returns a canned stream."""
    def __init__(self, chunks): self._chunks = chunks
    async def chat_stream(self, messages, model, tools):
        for c in self._chunks:
            yield c
    async def chat(self, *a, **kw):
        raise AssertionError("should stream, not call chat()")


@pytest.mark.asyncio
async def test_full_stream_flows_into_message_view():
    """Router streams text + tool deltas; TUIBackend marshals to widgets."""
    from spare_paw.router.tool_loop import _stream_and_assemble

    app = SparePawTUI(client=None, app_state=None)
    async with app.run_test() as pilot:
        backend = TUIBackend(app)
        log = app.query_one(ChatLog)
        turn = MessageView(role="assistant")
        log.mount_turn(turn)
        await pilot.pause()
        chunks = [
            StreamChunk(kind="text_delta", content="Hel"),
            StreamChunk(kind="text_delta", content="lo"),
            StreamChunk(kind="tool_call_delta", tool_index=0, tool_id="c1",
                        tool_name="read_file", arguments_fragment='{"path":"a"}'),
            StreamChunk(kind="done", finish_reason="tool_calls",
                        usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}),
        ]
        client = _FakeClient(chunks)

        def on_token(t):
            backend.on_token(t)

        await _stream_and_assemble(client, [], "m", None, on_token)
        await pilot.pause(0.1)

        # Tokens should have been posted as StreamToken messages; confirm live_text grew
        assert "Hel" in turn.live_text or len(turn.live_text) > 0
