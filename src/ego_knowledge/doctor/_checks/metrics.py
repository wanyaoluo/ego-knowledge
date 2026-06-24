"""Metrics checks: stale metrics, orphan relation types, jieba fallback."""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import cast

from ...models import RelationType
from ...registry import Registry
from .._helpers import _now_epoch, _to_naive_utc
from .._types import Finding, Severity


def _check_jieba_fallback_summary(
    registry: Registry,
    data_root: Path,
) -> list[Finding]:
    del registry
    log_path = data_root / "logs" / "refresh" / "jieba-fallback.log"
    if not log_path.exists():
        return []

    threshold = 50
    cutoff = _now_epoch() - 24 * 3600
    count = 0
    samples: list[str] = []

    with log_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            ts_epoch = record.get("ts_epoch")
            if not isinstance(ts_epoch, int) or ts_epoch < cutoff:
                continue
            count += 1
            token = record.get("token")
            if isinstance(token, str) and len(samples) < 5:
                samples.append(token)

    if count < threshold:
        return []

    return [
        Finding(
            rule_id="jieba_fallback_summary",
            severity=Severity.MEDIUM,
            target_id=None,
            target_path=str(log_path),
            message=(f"近 24 小时 jieba fallback {count} 次（阈值 {threshold}），样本: {samples}"),
        )
    ]


def _check_metrics_stale(registry: Registry, data_root: Path) -> list[Finding]:
    """Report entries whose metrics updated_at is more than 24h older than entries.updated_at."""
    del data_root
    findings: list[Finding] = []
    rows = registry.conn.execute(
        """
        SELECT e.id,
               e.updated_at AS entry_updated,
               m.updated_at AS metrics_updated
          FROM entries AS e
          JOIN entry_metrics AS m ON m.entry_id = e.id
        """
    ).fetchall()

    for row in rows:
        entry_id = cast(str, row["id"])
        entry_updated_str = cast(str, row["entry_updated"])
        metrics_updated_str = cast(str, row["metrics_updated"])

        try:
            entry_updated = _to_naive_utc(_dt.datetime.fromisoformat(entry_updated_str))
            metrics_updated = _to_naive_utc(_dt.datetime.fromisoformat(metrics_updated_str))
        except (ValueError, TypeError):
            continue

        # Only flag if metrics are older than entry AND the gap exceeds 24h
        if metrics_updated >= entry_updated:
            continue

        gap = (entry_updated - metrics_updated).total_seconds()
        if gap <= 24 * 3600:
            continue

        findings.append(
            Finding(
                rule_id="metrics_stale",
                severity=Severity.MEDIUM,
                target_id=entry_id,
                target_path=None,
                message=(
                    f"指标陈旧: entry {entry_id} "
                    f"更新于 {entry_updated_str}，指标更新于 {metrics_updated_str}"
                ),
            )
        )

    return findings


def _check_orphan_relation_type(
    registry: Registry,
    data_root: Path,
) -> list[Finding]:
    """Report relation types not present in the RelationType enum."""
    del data_root
    findings: list[Finding] = []
    valid_types = {rt.value for rt in RelationType}
    rows = registry.conn.execute("SELECT DISTINCT type FROM relations").fetchall()
    for row in rows:
        t = cast(str, row["type"])
        if t not in valid_types:
            findings.append(
                Finding(
                    rule_id="orphan_relation_type",
                    severity=Severity.WARNING,
                    target_id=None,
                    target_path=None,
                    message=f"未注册的关系类型: '{t}'",
                )
            )
    return findings
