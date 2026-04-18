"""Multi-line composer widget with input history and slash suggester."""

from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea


SLASH_COMMANDS = [
    "/help",
    "/exit",
    "/quit",
    "/forget",
    "/status",
    "/model",
    "/models",
    "/roles",
    "/image",
    "/plan",
]


class ComposerSubmitted(Message):
    """Emitted when the user presses Enter to submit the composer contents."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class Composer(TextArea):
    """Multi-line input. Enter submits, Shift+Enter inserts newline,
    Up/Down cycle history."""

    BINDINGS = [
        Binding("enter", "submit", "Send", show=False, priority=True),
        Binding("shift+enter", "newline", "Newline", show=False, priority=True),
        Binding("up", "history_prev", "History Up", show=False, priority=True),
        Binding("down", "history_next", "History Next", show=False, priority=True),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__("", id=kwargs.pop("id", None), **kwargs)
        self._history: list[str] = []
        self._history_index: int | None = None
        self._draft_saved: str | None = None

    def current_text(self) -> str:
        return self.text

    def action_submit(self) -> None:
        text = self.text.strip("\n")
        if not text.strip():
            return
        self._history.append(text)
        self._history_index = None
        self._draft_saved = None
        self.load_text("")
        self.post_message(ComposerSubmitted(text))

    def action_newline(self) -> None:
        self.insert("\n")

    def action_history_prev(self) -> None:
        if not self._history:
            return
        if self._history_index is None:
            self._draft_saved = self.text
            self._history_index = len(self._history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        self.load_text(self._history[self._history_index])

    def action_history_next(self) -> None:
        if self._history_index is None:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self.load_text(self._history[self._history_index])
        else:
            self._history_index = None
            self.load_text(self._draft_saved or "")
            self._draft_saved = None
