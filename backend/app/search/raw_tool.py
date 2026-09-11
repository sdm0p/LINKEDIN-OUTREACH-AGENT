"""Expose the raw tool response for validation, alongside the normalized one."""

from typing import Any

from .base import SearchError


async def raw_tool(self: Any, tool: str, arguments: dict[str, Any]) -> str:
    """Call an MCP tool and return its text content verbatim."""
    try:
        return await self._tool_text(tool, arguments)
    except SearchError:
        await self.close()
        raise
