from __future__ import annotations

import pytest
from textual.app import App

from spare_paw.tui.widgets.chat_log import ChatLog
from spare_paw.tui.widgets.message_view import MessageView


class _Host(App):
    def compose(self):
        yield ChatLog(id="log")


@pytest.mark.asyncio
async def test_mount_turn_adds_message_view():
    app = _Host()
    async with app.run_test() as pilot:
        log = app.query_one("#log", ChatLog)
        turn = MessageView(role="user", initial_text="hello")
        log.mount_turn(turn)
        await pilot.pause()
        assert len(list(log.query(MessageView))) == 1


@pytest.mark.asyncio
async def test_active_assistant_returns_unfinalized_turn():
    app = _Host()
    async with app.run_test() as pilot:
        log = app.query_one("#log", ChatLog)
        log.mount_turn(MessageView(role="user", initial_text="q", historical=True))
        active = MessageView(role="assistant")
        log.mount_turn(active)
        await pilot.pause()
        assert log.active_assistant() is active


@pytest.mark.asyncio
async def test_active_assistant_none_when_finalized():
    app = _Host()
    async with app.run_test() as pilot:
        log = app.query_one("#log", ChatLog)
        done = MessageView(role="assistant")
        log.mount_turn(done)
        done.append_stream("hi")
        done.finalize()
        await pilot.pause()
        assert log.active_assistant() is None
