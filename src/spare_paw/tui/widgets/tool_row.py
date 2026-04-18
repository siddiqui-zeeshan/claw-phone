"""Inline collapsible tool-call widget."""

from __future__ import annotations

import json
from typing import Literal

from textual.binding import Binding
from textual.widgets import Static

ICON_BY_TOOL = {
    "read_file": "⬚",
    "write_file": "□",
    "edit_file": "□",
    "shell": "⟩",
    "web_search": "◎",
    "web_scrape": "◎",
    "browser_navigate": "◎",
    "browser_click": "◎",
}

_Status = Literal["running", "success", "error", "cancelled"]


class ToolRow(Static):
    """One tool call row. Collapsed by default; expands on Enter/Space."""

    BINDINGS = [
        Binding("enter", "toggle", "Expand"),
        Binding("space", "toggle", "Expand"),
    ]

    def __init__(
        self,
        call_id: str,
        tool: str,
        args: dict,
        **kwargs,
    ) -> None:
        super().__init__("", **kwargs)
        self.call_id = call_id
        self.tool = tool
        self.args = args
        self.status: _Status = "running"
        self.duration_ms: int = 0
        self.preview: str = ""
        self.expanded: bool = False
        self.add_class("running")
        self.can_focus = True

    def on_mount(self) -> None:
        self.update(self.render_text())

    def mark_complete(self, success: bool, duration_ms: int, preview: str) -> None:
        self.status = "success" if success else "error"
        self.duration_ms = duration_ms
        self.preview = preview
        self.remove_class("running")
        self.add_class(self.status)
        self.update(self.render_text())

    def mark_cancelled(self) -> None:
        self.status = "cancelled"
        self.remove_class("running")
        self.add_class("cancelled")
        self.update(self.render_text())

    def toggle_expanded(self) -> None:
        self.expanded = not self.expanded
        self.update(self.render_text())

    def action_toggle(self) -> None:
        self.toggle_expanded()

    def render_text(self) -> str:
        icon = ICON_BY_TOOL.get(self.tool, "○")
        status_mark = {
            "running": "⣾ running...",
            "success": f"✓ {self.duration_ms/1000:.1f}s",
            "error": f"✗ {self.duration_ms/1000:.1f}s  {self._short_error()}",
            "cancelled": "⊘ cancelled",
        }[self.status]
        args_summary = self._summarize_args()
        header_prefix = "▾" if self.expanded else "▸"
        header = f"  {header_prefix} {icon} {self.tool}({args_summary})  {status_mark}"
        if not self.expanded or self.status == "running":
            return header
        body_lines = [
            f"      args:   {json.dumps(self.args)}",
            "      result: " + (self.preview.replace("\n", "\n              ") or "(empty)"),
        ]
        return "\n".join([header, *body_lines])

    def _summarize_args(self) -> str:
        if not self.args:
            return ""
        if not isinstance(self.args, dict):
            text = str(self.args)
            return text if len(text) <= 60 else text[:57] + "..."
        parts: list[str] = []
        for key, val in list(self.args.items())[:3]:
            rendered = repr(val) if not isinstance(val, str) else f"'{val}'"
            if len(rendered) > 20:
                rendered = rendered[:17] + "...'"
            parts.append(f"{key}={rendered}")
        text = ", ".join(parts)
        return text if len(text) <= 60 else text[:57] + "..."

    def _short_error(self) -> str:
        if not self.preview:
            return ""
        first_line = self.preview.splitlines()[0] if self.preview else ""
        return first_line[:80]
