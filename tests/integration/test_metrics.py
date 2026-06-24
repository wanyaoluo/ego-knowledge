from __future__ import annotations

import pytest

from ego_knowledge.metrics import full_recompute
from tests.unit.support import source_payload


def test_full_recompute_writes_authority_score(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="全量源节点"))
    target = fresh_ek.ingest("source", source_payload(title="全量目标节点"))
    archived = fresh_ek.ingest("source", source_payload(title="全量归档节点"))
    fresh_ek.link(source.id, target.id, rel_type="related")
    fresh_ek.update(archived.id, {"status": "archived"})

    total = full_recompute(fresh_ek._registry)

    rows = fresh_ek._registry.conn.execute(
        "SELECT entry_id, authority_score FROM entry_metrics"
    ).fetchall()
    score_map = {row["entry_id"]: row["authority_score"] for row in rows}
    assert total == 3
    assert score_map[source.id] > 0
    assert score_map[target.id] > score_map[source.id]
    assert score_map[archived.id] == pytest.approx(0.0)
    assert score_map[source.id] + score_map[target.id] == pytest.approx(1.0)
