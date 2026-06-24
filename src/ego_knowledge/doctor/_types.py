"""Doctor sub-package types — shared across main shell and checks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..registry import Registry

type CheckHandler = Callable[[Registry, Path], list["Finding"]]
type RecoveryRecord = dict[str, str]


class Severity(StrEnum):
    LOW = "low"
    WARNING = "warning"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(slots=True)
class Finding:
    rule_id: str
    severity: Severity
    target_id: str | None
    target_path: str | None
    message: str


@dataclass(slots=True)
class DoctorReport:
    checked_rules: list[str]
    findings: list[Finding]
    report_path: str
