"""Admin-tier MCP tools for EgoKnowledge."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from ...errors import ValidationError
from .._errors import to_payload, wrap_core_errors

if TYPE_CHECKING:
    from ...core import EgoKnowledge

_MAINTAIN_ACTIONS = ("diagnose", "doctor", "stats")
_DOMAIN_ACTIONS = ("add", "list", "migrate")


def register(mcp: FastMCP, get_core: Callable[[], EgoKnowledge]) -> None:
    @mcp.tool()
    @wrap_core_errors
    def ek_maintain(
        action: str,
        group_by: str | None = None,
    ) -> object:
        if action not in _MAINTAIN_ACTIONS:
            raise ValidationError(
                f"invalid action '{action}', expected one of {list(_MAINTAIN_ACTIONS)}",
                details={"valid_actions": list(_MAINTAIN_ACTIONS)},
            )
        core = get_core()
        if action == "doctor":
            return to_payload(core.doctor(repair=False))
        if action == "diagnose":
            return to_payload(core.diagnose())
        return to_payload(core.stats(group_by=group_by))

    @mcp.tool()
    @wrap_core_errors
    def ek_domains(
        action: str,
        name: str | None = None,
        entries: list[str] | None = None,
        target_domain: str | None = None,
    ) -> object:
        if action not in _DOMAIN_ACTIONS:
            raise ValidationError(
                f"invalid action '{action}', expected one of {list(_DOMAIN_ACTIONS)}",
                details={"valid_actions": list(_DOMAIN_ACTIONS)},
            )
        core = get_core()
        if action == "list":
            return to_payload(core.domains_list())
        if action == "add":
            if not name:
                raise ValidationError(
                    "ek_domains action='add' requires 'name'",
                    details={"missing_fields": ["name"]},
                )
            core.domains_add(name)
            return None
        missing = [
            key
            for key, value in (("entries", entries), ("target_domain", target_domain))
            if not value
        ]
        if missing:
            raise ValidationError(
                "ek_domains action='migrate' requires 'entries' + 'target_domain'",
                details={"missing_fields": missing},
            )
        return to_payload(
            core.domains_migrate(
                entries=entries or [],
                target_domain=target_domain or "",
            )
        )
