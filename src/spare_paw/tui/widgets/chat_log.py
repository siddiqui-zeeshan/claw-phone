"""Scrolling container of MessageView widgets."""

from __future__ import annotations

from typing import Iterable

from textual.containers import VerticalScroll

from spare_paw.tui.widgets.message_view import MessageView


class ChatLog(VerticalScroll):
    """Scrolls the conversation. Owns the active (most recent) MessageView."""

    def mount_turn(self, turn: MessageView) -> MessageView:
        self.mount(turn)
        self.scroll_end(animate=False)
        return turn

    def active_assistant(self) -> MessageView | None:
        """The most recently mounted assistant MessageView, if not yet finalized."""
        views = list(self.query(MessageView))
        for view in reversed(views):
            if view.role == "assistant" and not view.finalized:
                return view
        return None

    def append_error(self, text: str) -> None:
        from textual.widgets import Static

        self.mount(Static(f"[red]\\[!] {text}[/red]"))
        self.scroll_end(animate=False)

    def render_history(self, turns: Iterable[MessageView]) -> None:
        for turn in turns:
            self.mount(turn)
        self.scroll_end(animate=False)

    def search(self, query: str) -> list[MessageView]:
        """Return MessageViews whose live_text contains ``query`` (case-insensitive)."""
        if not query:
            return []
        q = query.lower()
        hits: list[MessageView] = []
        for view in self.query(MessageView):
            if q in view.live_text.lower():
                hits.append(view)
        return hits
