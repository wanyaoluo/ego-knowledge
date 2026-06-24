"""Error mapping and payload helpers for EgoKnowledge MCP tools."""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
from collections.abc import Callable
from enum import Enum
from functools import wraps
from pathlib import Path

from mcp.server.fastmcp.exceptions import ToolError

from ..errors import ConflictError, NotFoundError, StorageError, ValidationError

_CORE_ERRORS = (ValidationError, ConflictError, NotFoundError, StorageError)
_ERROR_TYPE_MAP = {
    ValidationError: "validation_error",
    ConflictError: "conflict_error",
    NotFoundError: "not_found_error",
    StorageError: "storage_error",
}


def _error_type_for(exc: Exception) -> str:
    for error_cls, error_type in _ERROR_TYPE_MAP.items():
        if isinstance(exc, error_cls):
            return error_type
    return "internal_error"


def to_payload(value: object) -> object:
    """Convert dataclasses, dates, paths, and enums into JSON-safe structures."""

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        raw = dataclasses.asdict(value)
        return {key: to_payload(item) for key, item in raw.items()}
    if isinstance(value, dict):
        return {str(key): to_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_payload(item) for item in value]
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return value.as_posix()
    return value


def _error_payload(error_type: str, message: str, details: object) -> str:
    return json.dumps(
        {
            "error_type": error_type,
            "message": message,
            "details": to_payload(details),
        },
        ensure_ascii=False,
    )


def wrap_core_errors[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Wrap Core exceptions into JSON-string ToolErrors for MCP transport."""

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except _CORE_ERRORS as exc:
            raise ToolError(
                _error_payload(
                    _error_type_for(exc),
                    exc.message,
                    exc.details,
                )
            ) from exc
        except Exception as exc:
            raise ToolError(
                _error_payload(
                    "internal_error",
                    str(exc),
                    {"tool": func.__name__},
                )
            ) from exc

    return wrapper
