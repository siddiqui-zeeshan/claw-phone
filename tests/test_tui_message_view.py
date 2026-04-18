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


@pytest.mark.asyncio
async def test_finalize_mounts_markdown_widget():
    """After finalize, the body must be a Textual Markdown widget, not a Static."""
    from textual.widgets import Markdown as TxMarkdown

    app = _Host()
    async with app.run_test() as pilot:
        mv = app.query_one("#mv", MessageView)
        mv.append_stream("**bold** and _italic_\n\n- item")
        await pilot.pause()
        mv.finalize()
        await pilot.pause()
        assert any(isinstance(c, TxMarkdown) for c in mv.children), (
            f"expected a Markdown widget after finalize, got: "
            f"{[type(c).__name__ for c in mv.children]}"
        )


@pytest.mark.asyncio
async def test_historical_assistant_message_renders_markdown():
    """Historical assistant messages must be rendered as Markdown too."""
    from textual.widgets import Markdown as TxMarkdown

    class _HistoricHost(App):
        def compose(self):
            yield MessageView(
                role="assistant",
                initial_text="**Bold** text",
                historical=True,
                id="hist",
            )

    app = _HistoricHost()
    async with app.run_test() as _pilot:
        mv = app.query_one("#hist", MessageView)
        assert any(isinstance(c, TxMarkdown) for c in mv.children), (
            f"expected Markdown widget in historical assistant view, got: "
            f"{[type(c).__name__ for c in mv.children]}"
        )


@pytest.mark.asyncio
async def test_historical_user_message_stays_plain():
    """User messages stay plain text (no markdown interpretation)."""
    from textual.widgets import Markdown as TxMarkdown, Static

    class _UHost(App):
        def compose(self):
            yield MessageView(role="user", initial_text="hello *world*", id="u")

    app = _UHost()
    async with app.run_test() as _pilot:
        mv = app.query_one("#u", MessageView)
        assert not any(isinstance(c, TxMarkdown) for c in mv.children), (
            "user messages must not be rendered as Markdown"
        )
        assert any(isinstance(c, Static) for c in mv.children)


@pytest.mark.asyncio
async def test_tool_rows_appear_above_streamed_text():
    """When tool_calls happen before the final text, UI order must mirror that."""
    from spare_paw.tui.widgets.tool_row import ToolRow
    from textual.widgets import Static

    class _Host(App):
        def compose(self):
            yield MessageView(role="assistant", id="mv")

    app = _Host()
    async with app.run_test() as pilot:
        mv = app.query_one("#mv", MessageView)
        mv.add_tool_call(call_id="c1", tool="web_search", args={"q": "x"})
        mv.add_tool_call(call_id="c2", tool="web_search", args={"q": "y"})
        await pilot.pause()
        mv.append_stream("Final answer here.")
        await pilot.pause()

        children = list(mv.children)
        # Find positions of ToolRow and body Static.
        tool_indices = [i for i, c in enumerate(children) if isinstance(c, ToolRow)]
        # The streamed body must come AFTER every ToolRow in child order.
        body = mv._body  # type: ignore[attr-defined]
        body_index = children.index(body)
        assert tool_indices, "tool rows should exist"
        assert all(ti < body_index for ti in tool_indices), (
            f"body at {body_index}, tool rows at {tool_indices} "
            f"— body must render below tool rows"
        )
        _ = Static


@pytest.mark.asyncio
async def test_message_view_header_has_no_hardcoded_width():
    from textual.widgets import Static
    from textual.app import App as _App

    class _Host(_App):
        def compose(self):
            yield MessageView(role="user", initial_text="x", id="u")

    app = _Host()
    async with app.run_test() as _pilot:
        mv = app.query_one("#u", MessageView)
        # Header should not contain 55+ space runs (that was the old 60-char padding)
        header = mv.query(Static).first()
        rendered = str(header.render())
        assert "        " * 7 not in rendered  # no 56+ consecutive spaces
