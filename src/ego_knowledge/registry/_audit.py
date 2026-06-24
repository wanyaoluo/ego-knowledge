"""AuditOps: stats, review queue, heat computation, and metrics persistence."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import cast

from .._frontmatter_coercion import (
    _as_float,
    _as_int,
    _metrics_to_map,
    _parse_datetime_text,
    _utc_now_text,
)
from ..errors import StorageError, ValidationError
from ._typing import RegistryMixinBase

type Metrics = object


class AuditOps(RegistryMixinBase):
    """Stats, review queue, heat computation, and metrics persistence."""

    def stats(self, group_by: str | None) -> dict[str, object]:
        row = self.conn.execute("SELECT COUNT(*) AS total FROM entries").fetchone()
        total = int(cast(int, row["total"])) if row is not None else 0
        counts: dict[str, int] = {}
        entries: list[dict[str, object]] = []

        if group_by is None:
            rows = self.conn.execute(
                """
                SELECT id, kind, slug
                  FROM entries
                 ORDER BY id
                """
            ).fetchall()
            entries = [
                {
                    "id": cast(str, record["id"]),
                    "kind": cast(str, record["kind"]),
                    "slug": cast(str, record["slug"]),
                }
                for record in rows
            ]
            return {"total": total, "counts": counts, "entries": entries}

        allowed = {"kind", "status", "freshness", "domain"}
        if group_by not in allowed:
            raise ValidationError(f"不支持的分组字段: {group_by}")
        group_column = group_by
        # Safe f-string: group_column is restricted by the allowlist above.
        rows = self.conn.execute(
            f"""
            SELECT COALESCE(NULLIF({group_column}, ''), '<null>') AS group_value,
                   COUNT(*) AS total
              FROM entries
             GROUP BY COALESCE(NULLIF({group_column}, ''), '<null>')
             ORDER BY group_value
            """
        ).fetchall()
        counts = {
            cast(str, record["group_value"]): int(cast(int, record["total"])) for record in rows
        }
        if group_by == "kind":
            rows = self.conn.execute(
                """
                SELECT id, kind, slug
                  FROM entries
                 ORDER BY id
                """
            ).fetchall()
            entries = [
                {
                    "id": cast(str, record["id"]),
                    "kind": cast(str, record["kind"]),
                    "slug": cast(str, record["slug"]),
                }
                for record in rows
            ]
        return {"total": total, "counts": counts, "entries": entries}

    def review_queue(self, overdue_only: bool, *, include_archived: bool = False) -> list[Entry]:  # type: ignore[name-defined]  # noqa: F821
        archived_clause = "" if include_archived else " AND e.status != 'archived'"
        today = date.today().isoformat()
        if overdue_only:
            sql = (
                "SELECT e.id "
                "  FROM entries AS e "
                "  JOIN dossier_fields AS d "
                "    ON d.entry_id = e.id "
                " WHERE e.kind = 'dossier' "
                "   AND d.review_due_at IS NOT NULL "
                "   AND d.review_due_at <= ?"
                f"{archived_clause} "
                " ORDER BY d.review_due_at ASC, e.id ASC"
            )
            rows = self.conn.execute(sql, (today,)).fetchall()
        else:
            sql = (
                "SELECT e.id "
                "  FROM entries AS e "
                "  JOIN dossier_fields AS d "
                "    ON d.entry_id = e.id "
                " WHERE e.kind = 'dossier' "
                "   AND d.review_due_at IS NOT NULL"
                f"{archived_clause} "
                " ORDER BY d.review_due_at ASC, e.id ASC"
            )
            rows = self.conn.execute(sql).fetchall()
        return [self.get_entry(cast(str, row["id"])) for row in rows]

    def compute_heat_from_log(
        self,
        entry_id: str,
        window_days: int,
        half_life_days: int,
    ) -> float:
        if window_days <= 0 or half_life_days <= 0:
            raise ValidationError("window_days 与 half_life_days 必须为正整数")
        rows = self.conn.execute(
            """
            SELECT accessed_at
              FROM access_log
             WHERE entry_id = ?
               AND accessed_at IS NOT NULL
            """,
            (entry_id,),
        ).fetchall()
        now = datetime.now(tz=UTC)
        cutoff = now - timedelta(days=window_days)
        total = 0.0
        for row in rows:
            accessed_at = _parse_datetime_text(cast(str, row["accessed_at"]))
            if accessed_at < cutoff:
                continue
            days = max(0.0, (now - accessed_at).total_seconds() / 86400.0)
            total += 0.5 ** (days / float(half_life_days))
        return total

    def upsert_metrics(self, entry_id: str, metrics: Metrics) -> None:
        metrics_map = _metrics_to_map(metrics)
        try:
            self.conn.execute(
                """
                INSERT INTO entry_metrics(
                    entry_id,
                    evidence_strength,
                    drift_score,
                    compression_ratio,
                    action_relevance,
                    retrieval_heat,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_id) DO UPDATE SET
                    evidence_strength = excluded.evidence_strength,
                    drift_score = excluded.drift_score,
                    compression_ratio = excluded.compression_ratio,
                    action_relevance = excluded.action_relevance,
                    retrieval_heat = excluded.retrieval_heat,
                    updated_at = excluded.updated_at
                """,
                (
                    entry_id,
                    _as_float(metrics_map["evidence_strength"]),
                    _as_float(metrics_map["drift_score"]),
                    _as_int(metrics_map["compression_ratio"]),
                    _as_int(metrics_map["action_relevance"]),
                    _as_float(metrics_map["retrieval_heat"]),
                    _utc_now_text(),
                ),
            )
        except sqlite3.Error as exc:
            raise StorageError(f"写入 metrics 失败 {entry_id}: {exc}") from exc
