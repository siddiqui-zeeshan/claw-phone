from __future__ import annotations

import pytest
from textual.app import App

from spare_paw.tui.widgets.composer import Composer, ComposerSubmitted


class _Host(App):
    def __init__(self):
        super().__init__()
        self.submitted: list[str] = []

    def compose(self):
        yield Composer(id="composer")

    def on_composer_submitted(self, msg: ComposerSubmitted) -> None:
        self.submitted.append(msg.text)


@pytest.mark.asyncio
async def test_enter_submits_text():
    app = _Host()
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.focus()
        await pilot.press("h", "i")
        await pilot.press("enter")
        assert app.submitted == ["hi"]


@pytest.mark.asyncio
async def test_empty_submit_noop():
    app = _Host()
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.focus()
        await pilot.press("enter")
        assert app.submitted == []


@pytest.mark.asyncio
async def test_history_cycle():
    app = _Host()
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.focus()
        for word in ("first", "second"):
            for ch in word:
                await pilot.press(ch)
            await pilot.press("enter")
        await pilot.press("up")
        assert composer.current_text() == "second"
        await pilot.press("up")
        assert composer.current_text() == "first"
        await pilot.press("down")
        assert composer.current_text() == "second"


@pytest.mark.asyncio
async def test_shift_enter_inserts_newline():
    app = _Host()
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.focus()
        await pilot.press("h", "i")
        await pilot.press("shift+enter")
        await pilot.press("y", "o")
        assert composer.current_text() == "hi\nyo"
        assert app.submitted == []
        await pilot.press("enter")
        assert app.submitted == ["hi\nyo"]
