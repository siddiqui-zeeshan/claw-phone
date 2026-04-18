import pytest

from spare_paw.tui.app import SparePawTUI
from spare_paw.tui.widgets.chat_log import ChatLog
from spare_paw.tui.widgets.message_view import MessageView


@pytest.mark.asyncio
async def test_search_highlights_matching_turn():
    app = SparePawTUI(client=None, app_state=None)
    async with app.run_test() as _pilot:
        log = app.query_one(ChatLog)
        log.mount_turn(MessageView(role="user", initial_text="find the needle here", historical=True))
        log.mount_turn(MessageView(role="assistant", initial_text="other content", historical=True))
        matches = log.search("needle")
        assert len(matches) == 1


@pytest.mark.asyncio
async def test_search_no_match_returns_empty():
    app = SparePawTUI(client=None, app_state=None)
    async with app.run_test() as _pilot:
        log = app.query_one(ChatLog)
        log.mount_turn(MessageView(role="user", initial_text="hello", historical=True))
        matches = log.search("xyz")
        assert matches == []
