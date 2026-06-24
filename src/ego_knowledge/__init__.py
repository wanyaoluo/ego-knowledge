"""Public exports for EgoKnowledge Core."""

from __future__ import annotations

from typing import Any

__all__ = [
    "REGISTRY_SCHEMA_VERSION",
    "Registry",
    "RegistryStats",
    "build_registry",
    "check_local_rules",
    "transactional_write",
]


def __getattr__(name: str) -> Any:
    if name == "transactional_write":
        from .transactions import transactional_write

        return transactional_write
    if name == "check_local_rules":
        from .local_rules import check_local_rules

        return check_local_rules
    if name in {"REGISTRY_SCHEMA_VERSION", "Registry", "RegistryStats", "build_registry"}:
        from .registry import REGISTRY_SCHEMA_VERSION, Registry, RegistryStats, build_registry

        mapping = {
            "REGISTRY_SCHEMA_VERSION": REGISTRY_SCHEMA_VERSION,
            "Registry": Registry,
            "RegistryStats": RegistryStats,
            "build_registry": build_registry,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
