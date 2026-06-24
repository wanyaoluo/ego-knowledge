"""RelationsOps: relation queries, refresh, and graph traversal helpers."""

from __future__ import annotations

from typing import cast

from .._frontmatter_coercion import _normalize_kind
from .._serde import _materialized_relation_rows
from ..errors import ValidationError
from ..models import RelationType
from ._typing import RegistryMixinBase

type RelationRow = tuple[str, str, str, str]


class RelationsOps(RegistryMixinBase):
    """Relation queries, _refresh_relations, and graph traversal helpers."""

    def is_superseded(self, entry_id: str) -> bool:
        outgoing = self.count_out_relations(entry_id, "superseded_by")
        incoming = self.count_in_relations(entry_id, RelationType.SUPERSEDES.value)
        return outgoing > 0 or incoming > 0

    def one_hop_neighbors(self, entry_id: str) -> list[str]:
        return self.neighbors(entry_id, direction="both")

    def neighbors(
        self,
        entry_id: str,
        rel_type: str | None = None,
        direction: str = "out",
        *,
        include_archived: bool = True,
    ) -> list[str]:
        if direction not in {"out", "in", "both"}:
            raise ValidationError(f"不支持的关系方向: {direction}")
        neighbors: set[str] = set()
        if direction in {"out", "both"}:
            rows = self._relation_targets(entry_id, rel_type)
            neighbors.update(rows)
        if direction in {"in", "both"}:
            rows = self._relation_sources(entry_id, rel_type)
            neighbors.update(rows)
        if not include_archived:
            neighbors = self._exclude_archived(neighbors)
        return sorted(neighbors)

    def _exclude_archived(self, entry_ids: set[str]) -> set[str]:
        if not entry_ids:
            return entry_ids
        placeholders = ",".join("?" for _ in entry_ids)
        # Safe f-string: placeholders are generated as parameter markers only;
        # entry ids remain bound through sqlite parameters.
        rows = self.conn.execute(
            f"SELECT id FROM entries WHERE id IN ({placeholders}) AND status != 'archived'",
            tuple(entry_ids),
        ).fetchall()
        return {cast(str, row["id"]) for row in rows}

    def out_refs(self, entry_id: str, types: list[str]) -> list[str]:
        if not types:
            return []
        placeholders = ",".join("?" for _ in types)
        rows = self.conn.execute(
            f"""
            SELECT target_id
              FROM relations
             WHERE source_id = ?
               AND type IN ({placeholders})
             ORDER BY target_id
            """,
            (entry_id, *types),
        ).fetchall()
        return [cast(str, row["target_id"]) for row in rows]

    def count_out_relations(self, entry_id: str, rel_type: str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS total
              FROM relations
             WHERE source_id = ?
               AND type = ?
            """,
            (entry_id, rel_type),
        ).fetchone()
        return int(cast(int, row["total"])) if row is not None else 0

    def count_in_relations(
        self,
        entry_id: str,
        rel_type: str,
        from_kind: str | None = None,
    ) -> int:
        sql = """
            SELECT COUNT(*) AS total
              FROM relations AS r
              JOIN entries AS e
                ON e.id = r.source_id
             WHERE r.target_id = ?
               AND r.type = ?
        """
        params: list[str] = [entry_id, rel_type]
        if from_kind is not None:
            sql += " AND e.kind = ?"
            params.append(_normalize_kind(from_kind).value)
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return int(cast(int, row["total"])) if row is not None else 0

    def count_in_evidence_refs(self, entry_id: str, from_kind: str) -> int:
        return self.count_in_relations(entry_id, "evidence_refs", from_kind)

    def _refresh_relations(
        self,
        entry: Entry,  # type: ignore[name-defined]  # noqa: F821
        *,
        allow_missing_targets: bool,
    ) -> None:
        self.conn.execute("DELETE FROM relations WHERE source_id = ?", (entry.id,))
        self._insert_relation_rows(
            [
                (
                    entry.id,
                    relation.target,
                    relation.type.value,
                    relation.source.value,
                )
                for relation in entry.relations
            ],
            allow_missing_targets=allow_missing_targets,
        )
        self._insert_relation_rows(
            _materialized_relation_rows(entry),
            allow_missing_targets=allow_missing_targets,
        )

    def _insert_relation_rows(
        self,
        relation_rows: list[RelationRow],
        *,
        allow_missing_targets: bool,
    ) -> None:
        if not relation_rows:
            return
        rows_to_insert = relation_rows
        if allow_missing_targets:
            rows_to_insert = [row for row in relation_rows if self.has_entry(row[1])]
            if not rows_to_insert:
                return
        self.conn.executemany(
            """
            INSERT INTO relations(source_id, target_id, type, origin)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(source_id, target_id, type)
            DO UPDATE SET origin = excluded.origin
            """,
            rows_to_insert,
        )

    def _relation_targets(self, entry_id: str, rel_type: str | None) -> list[str]:
        if rel_type is None:
            rows = self.conn.execute(
                """
                SELECT target_id
                  FROM relations
                 WHERE source_id = ?
                 ORDER BY target_id
                """,
                (entry_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT target_id
                  FROM relations
                 WHERE source_id = ?
                   AND type = ?
                 ORDER BY target_id
                """,
                (entry_id, rel_type),
            ).fetchall()
        return [cast(str, row["target_id"]) for row in rows]

    def _relation_sources(self, entry_id: str, rel_type: str | None) -> list[str]:
        if rel_type is None:
            rows = self.conn.execute(
                """
                SELECT source_id
                  FROM relations
                 WHERE target_id = ?
                 ORDER BY source_id
                """,
                (entry_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT source_id
                  FROM relations
                 WHERE target_id = ?
                   AND type = ?
                 ORDER BY source_id
                """,
                (entry_id, rel_type),
            ).fetchall()
        return [cast(str, row["source_id"]) for row in rows]
