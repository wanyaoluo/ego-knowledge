from __future__ import annotations

import pytest

from ego_knowledge.errors import (
    ConflictError,
    NotFoundError,
    StorageError,
    ValidationError,
    to_transport,
)


@pytest.mark.parametrize(
    ("error_cls", "code", "exit_code", "http_status"),
    [
        (ValidationError, "EK_VALIDATION", 1, 400),
        (ConflictError, "EK_CONFLICT", 2, 409),
        (NotFoundError, "EK_NOT_FOUND", 3, 404),
        (StorageError, "EK_STORAGE", 4, 500),
    ],
)
def test_error_transport_uses_stable_metadata(
    error_cls: type[ValidationError | ConflictError | NotFoundError | StorageError],
    code: str,
    exit_code: int,
    http_status: int,
) -> None:
    error = error_cls("消息", details={"entry_id": "ek_con_demo"})

    assert error.code == code
    assert error.exit_code == exit_code
    assert error.http_status == http_status
    assert to_transport(error) == {
        "code": code,
        "message": "消息",
        "details": {"entry_id": "ek_con_demo"},
    }
