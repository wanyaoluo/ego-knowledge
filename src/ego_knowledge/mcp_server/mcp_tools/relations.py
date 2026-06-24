"""Relation-tier MCP tools for EgoKnowledge."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from .._errors import to_payload, wrap_core_errors

if TYPE_CHECKING:
    from ...core import EgoKnowledge


def register(mcp: FastMCP, get_core: Callable[[], EgoKnowledge]) -> None:
    @mcp.tool()
    @wrap_core_errors
    def ek_link(
        source_id: str,
        target_id: str,
        rel_type: str,
        source: str = "confirmed",
    ) -> object:
        return to_payload(
            get_core().link(
                source_id=source_id,
                target_id=target_id,
                rel_type=rel_type,
                source=source,
            )
        )

    @mcp.tool()
    @wrap_core_errors
    def ek_unlink(source_id: str, target_id: str) -> None:
        get_core().unlink(source_id=source_id, target_id=target_id)
