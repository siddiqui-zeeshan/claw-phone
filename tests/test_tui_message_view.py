from __future__ import annotations

import pytest
from textual.app import App

from spare_paw.tui.widgets.message_view import MessageView


class _Host(App):
    def compose(self):
        yield MessageView(role="assistant", id="mv")


@pytest.mark.asyncio
async def test_append_during_stream_grows_text():
    app = _Host()
    async with app.run_test() as pilot:
        mv = app.query_one("#mv", MessageView)
        mv.append_stream("hello ")
        mv.append_stream("world")
        await pilot.pause()
        assert mv.live_text == "hello world"


@pytest.mark.asyncio
async def test_finalize_swaps_to_markdown():
    app = _Host()
    async with app.run_test() as pilot:
        mv = app.query_one("#mv", MessageView)
        mv.append_stream("# Heading\n\nbody")
        mv.finalize()
        await pilot.pause()
        assert mv.finalized is True


@pytest.mark.asyncio
async def test_mount_tool_row_adds_child():
    app = _Host()
    async with app.run_test() as pilot:
        mv = app.query_one("#mv", MessageView)
        mv.add_tool_call(call_id="c1", tool="read_file", args={"path": "x"})
        await pilot.pause()
        assert mv.tool_row_count() == 1


@pytest.mark.asyncio
async def test_user_message_shows_text_static():
    class _UHost(App):
        def compose(self):
            yield MessageView(role="user", initial_text="hello there", id="u")
    app = _UHost()
    async with app.run_test() as _pilot:
        mv = app.query_one("#u", MessageView)
        assert "hello there" in mv.live_text
