"""Aggregate MCP tool registration for EgoKnowledge."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from . import admin, relations, search, write

if TYPE_CHECKING:
    from ...core import EgoKnowledge

__all__ = ["register_all_tools"]


def register_all_tools(
    mcp: FastMCP,
    get_core: Callable[[], EgoKnowledge],
) -> None:
    search.register(mcp, get_core)
    write.register(mcp, get_core)
    relations.register(mcp, get_core)
    admin.register(mcp, get_core)
