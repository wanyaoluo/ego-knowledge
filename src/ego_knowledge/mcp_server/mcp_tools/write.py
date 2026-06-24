"""Write-tier MCP tools for EgoKnowledge."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from .._errors import to_payload, wrap_core_errors
from .._normalize import normalize_mapping

if TYPE_CHECKING:
    from ...core import EgoKnowledge


def register(mcp: FastMCP, get_core: Callable[[], EgoKnowledge]) -> None:
    @mcp.tool()
    @wrap_core_errors
    def ek_ingest(
        kind: str,
        payload: dict[str, object],
        conflict_policy: str = "strict",
    ) -> object:
        # Clients may mis-serialize nested list fields (tags/search_terms/...)
        # inside the opaque ``additionalProperties: true`` payload; normalize
        # them back to lists before Core sees the data.
        return to_payload(
            get_core().ingest(
                kind=kind,
                payload=normalize_mapping(payload),
                conflict_policy=conflict_policy,
            )
        )

    @mcp.tool()
    @wrap_core_errors
    def ek_update(id: str, changes: dict[str, object]) -> object:
        return to_payload(get_core().update(id=id, changes=normalize_mapping(changes)))

    @mcp.tool()
    @wrap_core_errors
    def ek_promote(
        id: str,
        target_kind: str,
        freshness: str = "watch",
    ) -> object:
        return to_payload(
            get_core().promote(
                id=id,
                target_kind=target_kind,
                freshness=freshness,
            )
        )
