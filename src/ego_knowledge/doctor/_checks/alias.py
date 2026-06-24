"""Alias conflict check."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from ...registry import Registry
from .._types import Finding, Severity


def _check_alias_conflicts(registry: Registry, data_root: Path) -> list[Finding]:
    del data_root
    findings: list[Finding] = []
    rows = registry.conn.execute(
        """
        SELECT a.alias_nfc AS alias_nfc,
               GROUP_CONCAT(a.entry_id) AS entry_ids,
               COUNT(*) AS cnt
          FROM aliases AS a
          JOIN entries AS e ON e.id = a.entry_id
         WHERE e.status != 'archived'
        GROUP BY a.alias_nfc
        HAVING cnt > 1
        """
    ).fetchall()
    for row in rows:
        alias_nfc = cast(str, row["alias_nfc"])
        entry_ids = sorted(cast(str, row["entry_ids"]).split(","))
        findings.append(
            Finding(
                rule_id="alias_conflicts",
                severity=Severity.MEDIUM,
                target_id=" vs ".join(entry_ids),
                target_path=None,
                message=f"alias '{alias_nfc}' 被多个 active entry 持有: {entry_ids}",
            )
        )
    return findings
