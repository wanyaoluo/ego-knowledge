"""Error types for EgoKnowledge Core Library.

Four error categories, mapped to CLI exit codes and HTTP status codes:
- ValidationError → exit 1 / HTTP 400
- ConflictError   → exit 2 / HTTP 409
- NotFoundError   → exit 3 / HTTP 404
- StorageError    → exit 4 / HTTP 500
"""

from __future__ import annotations


class EgoKnowledgeError(Exception):
    """Base class for all EgoKnowledge errors.

    Stable English code (for transport), Chinese message (for user).
    """

    code: str = "EK_UNKNOWN"
    exit_code: int = 99
    http_status: int = 500

    def __init__(self, message: str, *, details: dict[str, object] | None = None):
        super().__init__(message)
        self.message = message
        self.details: dict[str, object] = details or {}


class ValidationError(EgoKnowledgeError):
    code = "EK_VALIDATION"
    exit_code = 1
    http_status = 400


class ConflictError(EgoKnowledgeError):
    code = "EK_CONFLICT"
    exit_code = 2
    http_status = 409


class NotFoundError(EgoKnowledgeError):
    code = "EK_NOT_FOUND"
    exit_code = 3
    http_status = 404


class StorageError(EgoKnowledgeError):
    code = "EK_STORAGE"
    exit_code = 4
    http_status = 500


class BodyInvalidUTF8(ValidationError):  # noqa: N818 - frozen PR-B1 error class name
    code = "body_invalid_utf8"

    def __init__(self, message: str = "[ek-code:body_invalid_utf8] body 必须是合法 UTF-8 文本"):
        super().__init__(message, details={"error_code": self.code})


class BodyLengthBelowMin(ValidationError):  # noqa: N818 - frozen PR-B1 error class name
    code = "body_length_below_min"

    def __init__(self, length: int, minimum: int):
        super().__init__(
            f"[ek-code:body_length_below_min] body length {length} < minimum {minimum}",
            details={"error_code": self.code, "length": length, "minimum": minimum},
        )


class BodyLengthAboveMax(ValidationError):  # noqa: N818 - frozen PR-B1 error class name
    code = "body_length_above_max"

    def __init__(self, length: int, maximum: int):
        super().__init__(
            f"[ek-code:body_length_above_max] body length {length} > limit {maximum}",
            details={"error_code": self.code, "length": length, "maximum": maximum},
        )


class BodyFrontmatterMismatch(ValidationError):  # noqa: N818 - frozen PR-B1 error class name
    code = "body_frontmatter_mismatch"

    def __init__(self, message: str = "body frontmatter 与目标 frontmatter 不一致"):
        super().__init__(
            f"[ek-code:body_frontmatter_mismatch] {message}",
            details={"error_code": self.code},
        )


class BodyBatchNotSupported(ValidationError):  # noqa: N818 - frozen PR-B1 error class name
    code = "body_batch_not_supported"

    def __init__(self) -> None:
        super().__init__(
            "[ek-code:body_batch_not_supported] ek_update body 只支持单条原地正文写入，"
            "不支持批量或同批路径迁移",
            details={"error_code": self.code},
        )


class BodyRecoveryFailedSnapshotMissing(StorageError):  # noqa: N818 - frozen PR-B1 error class name
    code = "body_recovery_failed_snapshot_missing"

    def __init__(self, message: str = "body 事务恢复失败，快照缺失或不可用"):
        super().__init__(
            f"[ek-code:body_recovery_failed_snapshot_missing] {message}",
            details={"error_code": self.code},
        )


def to_transport(error: EgoKnowledgeError) -> dict[str, object]:
    """Serialize error for MCP/HTTP/CLI transport."""
    return {
        "code": error.code,
        "message": error.message,
        "details": error.details,
    }
