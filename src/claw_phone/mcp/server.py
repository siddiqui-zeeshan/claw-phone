"""Expose claw-phone tools as an MCP server via FastMCP (stdio).

Entry point: ``python -m claw_phone mcp-server``

This starts a headless MCP server that exposes native phone tools (shell,
files, web search, etc.) so that external MCP clients like Claude Code can
call them.  The Telegram bot and cron scheduler are NOT started.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from claw_phone.config import config

logger = logging.getLogger(__name__)

# Tools that require a running Telegram bot / app_state and cannot work headless
_EXCLUDED_TOOLS = frozenset({
    "send_message",
    "send_file",
    "cron_create",
    "cron_delete",
    "cron_list",
    "spawn_agent",
    "read_logs",
})


def _register_native_tools(tool_registry: Any, config_data: dict[str, Any]) -> None:
    """Register native tools into the registry (same as gateway, minus inline tools)."""
    from claw_phone.tools import files, memory, shell, tavily_search, web_scrape

    shell.register(tool_registry, config_data)
    files.register(tool_registry, config_data)
    tavily_search.register(tool_registry, config_data)
    web_scrape.register(tool_registry, config_data)
    memory.register(tool_registry, config_data)


def run_mcp_server() -> None:
    """Start the MCP server (blocking, stdio transport)."""
    config.load()

    # Minimal logging setup
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    asyncio.run(_async_run())


async def _async_run() -> None:
    """Async entry point for the MCP server."""
    from claw_phone.db import init_db
    from claw_phone.tools.registry import ToolRegistry

    await init_db()

    tool_registry = ToolRegistry()
    config_data = config.data

    _register_native_tools(tool_registry, config_data)
    logger.info("Registered %d native tools for MCP server", len(tool_registry))

    # Build FastMCP server
    mcp = FastMCP("claw-phone")

    # Expose each non-excluded tool via FastMCP
    schemas = tool_registry.get_schemas()
    for schema in schemas:
        func_def = schema["function"]
        name = func_def["name"]

        if name in _EXCLUDED_TOOLS:
            continue

        _register_fastmcp_tool(mcp, name, func_def, tool_registry)

    logger.info("Starting MCP server (stdio)")
    await mcp.run_stdio_async()


def _register_fastmcp_tool(
    mcp: FastMCP,
    name: str,
    func_def: dict[str, Any],
    tool_registry: Any,
) -> None:
    """Register a single ToolRegistry tool as a FastMCP tool."""

    async def _handler(**kwargs: Any) -> str:
        return await tool_registry.execute(name, kwargs)

    # FastMCP's @mcp.tool() decorator sets metadata; we call the low-level API
    mcp.tool(name=name, description=func_def.get("description", ""))(_handler)
