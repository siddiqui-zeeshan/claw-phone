"""Per-turn widget: one user or assistant message, owning its own ToolRows."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Markdown, Static

from spare_paw.tui.widgets.tool_row import ToolRow

_Role = Literal["user", "assistant"]


def _fmt_timestamp(dt: datetime | None = None) -> str:
    return (dt or datetime.now()).strftime("%-I:%M %p")


class MessageView(Vertical):
    """One conversation turn.

    Assistant variants stream plain-text tokens into a ``Static`` body;
    on ``finalize`` the Static is replaced with a rendered Markdown widget
    so bold/italic/tables/lists display properly. Historical assistant
    messages skip the streaming step and render as Markdown directly.
    """

    def __init__(
        self,
        role: _Role,
        initial_text: str = "",
        timestamp: datetime | None = None,
        historical: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.role = role
        self.live_text = initial_text
        self.finalized = False
        self._historical = historical
        self._timestamp = timestamp or datetime.now()
        self._body: Widget | None = None
        self.add_class(role)

    def compose(self) -> ComposeResult:
        label = "You" if self.role == "user" else "spare-paw"
        header = f"[bold]{label}[/bold]   [dim]{_fmt_timestamp(self._timestamp)}[/dim]"
        yield Static(header, classes="header")
        if self.live_text:
            if self.role == "assistant" and self._historical:
                self._body = Markdown(self.live_text)
                self.finalized = True
            else:
                self._body = Static(self.live_text)
            yield self._body

    def _ensure_body(self) -> None:
        """Lazily mount the streaming body below any existing tool rows."""
        if self._body is None:
            self._body = Static(self.live_text)
            self.mount(self._body)

    def append_stream(self, chunk: str) -> None:
        if self.finalized:
            return
        self.live_text += chunk
        self._ensure_body()
        if isinstance(self._body, Static):
            self._body.update(self.live_text)

    def finalize(self) -> None:
        """Swap the streaming Static body for a rendered Markdown widget."""
        if self.finalized:
            return
        self.finalized = True
        if self.role != "assistant" or not self.live_text:
            return
        if isinstance(self._body, Markdown):
            return
        old_body = self._body
        new_body = Markdown(self.live_text)
        self._body = new_body
        self.mount(new_body)
        if old_body is not None:
            old_body.remove()

    def mark_cancelled(self) -> None:
        if not isinstance(self._body, Static):
            return
        self.finalized = True
        self._body.update(
            self.live_text + "\n[dim italic][cancelled][/dim italic]"
        )

    def add_tool_call(self, call_id: str, tool: str, args: dict) -> ToolRow:
        row = ToolRow(call_id=call_id, tool=tool, args=args)
        self.mount(row)
        return row

    def complete_tool_call(
        self,
        call_id: str,
        success: bool,
        duration_ms: int,
        preview: str,
    ) -> None:
        for row in self.query(ToolRow):
            if row.call_id == call_id:
                row.mark_complete(
                    success=success,
                    duration_ms=duration_ms,
                    preview=preview,
                )
                return

    def tool_row_count(self) -> int:
        return len(list(self.query(ToolRow)))
