from __future__ import annotations

import pytest
from textual.app import App

from spare_paw.tui.widgets.tool_row import ToolRow


class _Host(App):
    def compose(self):
        yield ToolRow(call_id="c1", tool="read_file", args={"path": "foo.py"}, id="tr")


@pytest.mark.asyncio
async def test_tool_row_running_state():
    app = _Host()
    async with app.run_test():
        tr = app.query_one("#tr", ToolRow)
        assert tr.status == "running"
        assert "read_file" in tr.render_text()
        assert "running" in tr.render_text().lower() or "⣾" in tr.render_text()


@pytest.mark.asyncio
async def test_tool_row_success_state_with_duration():
    app = _Host()
    async with app.run_test() as pilot:
        tr = app.query_one("#tr", ToolRow)
        tr.mark_complete(success=True, duration_ms=320, preview="ok")
        await pilot.pause()
        assert tr.status == "success"
        assert "✓" in tr.render_text()
        assert "0.3s" in tr.render_text() or "320" in tr.render_text()


@pytest.mark.asyncio
async def test_tool_row_error_shows_preview_when_collapsed():
    app = _Host()
    async with app.run_test() as pilot:
        tr = app.query_one("#tr", ToolRow)
        tr.mark_complete(success=False, duration_ms=2100, preview="Error: ModuleNotFoundError")
        await pilot.pause()
        assert "✗" in tr.render_text()
        assert "Error:" in tr.render_text()


@pytest.mark.asyncio
async def test_tool_row_toggle_expand():
    app = _Host()
    async with app.run_test() as pilot:
        tr = app.query_one("#tr", ToolRow)
        tr.mark_complete(success=True, duration_ms=100, preview="result text")
        tr.toggle_expanded()
        await pilot.pause()
        assert tr.expanded is True
        assert "path" in tr.render_text()
        assert "result text" in tr.render_text()


class _HostStringArgs(App):
    def compose(self):
        yield ToolRow(
            call_id="c1",
            tool="read_file",
            args="{'path': 'foo.py'}",  # string, not dict — webhook used to send this
            id="tr",
        )


@pytest.mark.asyncio
async def test_tool_row_tolerates_string_args():
    """Non-dict args must not crash render_text (remote webhook compatibility)."""
    app = _HostStringArgs()
    async with app.run_test():
        tr = app.query_one("#tr", ToolRow)
        text = tr.render_text()
        assert "read_file" in text
        assert "path" in text  # args rendered somehow, not crashed
