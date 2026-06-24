"""Search-tier MCP tools for EgoKnowledge."""

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
    def ek_search(
        query: str,
        kinds: list[str] | None = None,
        filters: dict[str, object] | None = None,
        backends: list[str] | None = None,
        limit: int = 20,
        expand_graph: bool = True,
        include_archived: bool = False,
    ) -> object:
        return to_payload(
            get_core().search(
                query=query,
                kinds=kinds,
                filters=filters,
                backends=backends,
                limit=limit,
                expand_graph=expand_graph,
                include_archived=include_archived,
            )
        )

    @mcp.tool()
    @wrap_core_errors
    def ek_get(id: str) -> object:
        return to_payload(get_core().get(id))

    @mcp.tool()
    @wrap_core_errors
    def ek_related(
        id: str,
        depth: int = 1,
        rel_type: str | None = None,
        include_archived: bool = False,
    ) -> object:
        return to_payload(
            get_core().related(
                id=id,
                depth=depth,
                rel_type=rel_type,
                include_archived=include_archived,
            )
        )

    @mcp.tool()
    @wrap_core_errors
    def ek_review(overdue_only: bool = False, include_archived: bool = False) -> object:
        return to_payload(
            get_core().review_queue(overdue_only=overdue_only, include_archived=include_archived)
        )
