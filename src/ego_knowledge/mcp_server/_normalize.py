"""Argument normalization for MCP write tools.

Some MCP clients mis-serialize list values nested inside ``dict[str, object]``
parameters, whose JSON Schema is ``{"type": "object", "additionalProperties": true}``
— the value type is opaque to the client. A list such as ``["a", "b"]`` may
therefore arrive as:

- a single-key wrapper ``{"item": ["a", "b"]}`` / ``{"items": ["a", "b"]}``, or
- a flattened JSON string ``'["a", "b"]'``.

This module restores the intended list shape before the payload reaches Core,
so ``ek_ingest`` / ``ek_update`` keep accepting well-formed data regardless of
client serialization quirks.

Assumptions / known limits: this is a narrow compatibility shim for the current
entry schemas, where write payload fields are scalars or flat list-like fields.
It intentionally treats any single-key ``item`` / ``items`` list wrapper and any
``[...]`` JSON array string as a list at any nested depth. Real clients have been
observed emitting multiple layers of wrapping, e.g. ``{"item": {"item": [...]}}``
or ``{"tags": {"item": {"items": [...]}}}``; these are collapsed recursively so
no residual ``item``/``items`` wrapper survives. If a future schema adds
legitimate nested mappings shaped like ``{"item": [...]}``, or legitimate string
fields whose value is a JSON array literal, this module must be revisited before
routing that payload through ``normalize_mapping``.
"""

from __future__ import annotations

import json

_ARRAY_WRAPPER_KEYS = ("item", "items")


def _coerce_listlike(value: object) -> object:
    """Restore a list that a client mis-serialized.

    - ``{"item": [...]}`` / ``{"items": [...]}`` single-key wrappers -> inner list.
    - A JSON-array string ``'[...]'`` -> the parsed list.
    - Everything else -> returned unchanged.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and len(value) == 1:
        only_key = next(iter(value))
        if only_key in _ARRAY_WRAPPER_KEYS and isinstance(value[only_key], list):
            return value[only_key]
    if isinstance(value, str):
        stripped = value.strip()
        if len(stripped) >= 2 and stripped[0] == "[" and stripped[-1] == "]":
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                return value
            if isinstance(parsed, list):
                return parsed
    return value


def _normalize_value(value: object) -> object:
    """Recursively normalize any value, fixing mis-serialized nested lists.

    After normalizing a dict's children, re-coerce the dict itself: a child
    may itself have been a nested wrapper (e.g. ``{"item": {"item": [...]}}``)
    whose inner layer only becomes a list once its own children are processed.
    Without this re-coerce the outer ``item`` key would survive.
    """
    coerced = _coerce_listlike(value)
    if isinstance(coerced, list):
        return [_normalize_value(item) for item in coerced]
    if isinstance(coerced, dict):
        normalized = {key: _normalize_value(val) for key, val in coerced.items()}
        return _coerce_listlike(normalized)
    return coerced


def normalize_mapping(payload: dict[str, object]) -> dict[str, object]:
    """Normalize mapping values while preserving the root mapping contract."""
    return {key: _normalize_value(value) for key, value in payload.items()}
