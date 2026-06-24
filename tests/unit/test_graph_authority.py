from __future__ import annotations

import pytest

from ego_knowledge._graph_authority import compute_pagerank, persist_authority_scores

from .support import source_payload


def test_empty_graph(fresh_ek) -> None:
    assert compute_pagerank(fresh_ek._registry) == {}


def test_single_node(fresh_ek) -> None:
    entry = fresh_ek.ingest("source", source_payload(title="单节点权威"))

    scores = compute_pagerank(fresh_ek._registry)

    assert scores == {entry.id: pytest.approx(1.0)}


def test_isolated_nodes_get_uniform_rank(fresh_ek) -> None:
    entries = [fresh_ek.ingest("source", source_payload(title=f"孤立节点{i}")) for i in range(3)]

    scores = compute_pagerank(fresh_ek._registry)

    assert set(scores) == {entry.id for entry in entries}
    assert all(score == pytest.approx(1.0 / 3.0) for score in scores.values())


def test_dangling_nodes_distribute_uniformly(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="出边节点"))
    target = fresh_ek.ingest("source", source_payload(title="悬挂节点"))
    fresh_ek.link(source.id, target.id, rel_type="related")

    scores = compute_pagerank(fresh_ek._registry)

    assert sum(scores.values()) == pytest.approx(1.0)
    assert scores[target.id] > scores[source.id]


def test_pagerank_sum_equals_one(fresh_ek) -> None:
    entries = [fresh_ek.ingest("source", source_payload(title=f"环节点{i}")) for i in range(4)]
    for index, entry in enumerate(entries):
        target = entries[(index + 1) % len(entries)]
        fresh_ek.link(entry.id, target.id, rel_type="related")

    scores = compute_pagerank(fresh_ek._registry)

    assert sum(scores.values()) == pytest.approx(1.0)


def test_convergence_on_dense_graph(fresh_ek) -> None:
    entries = [fresh_ek.ingest("source", source_payload(title=f"稠密节点{i}")) for i in range(8)]
    rows = [
        (source.id, target.id, "related", "confirmed")
        for source in entries
        for target in entries
        if source.id != target.id
    ]
    fresh_ek._registry.conn.executemany(
        "INSERT INTO relations(source_id, target_id, type, origin) VALUES(?, ?, ?, ?)",
        rows,
    )

    scores = compute_pagerank(fresh_ek._registry)

    assert len(scores) == len(entries)
    assert sum(scores.values()) == pytest.approx(1.0)


def test_persist_updates_entry_metrics(fresh_ek) -> None:
    entry = fresh_ek.ingest("source", source_payload(title="持久化权威"))

    persist_authority_scores(fresh_ek._registry, {entry.id: 0.75})

    row = fresh_ek._registry.conn.execute(
        "SELECT authority_score FROM entry_metrics WHERE entry_id = ?",
        (entry.id,),
    ).fetchone()
    assert row["authority_score"] == pytest.approx(0.75)


def test_pollution_relations_filtered(fresh_ek) -> None:
    entry = fresh_ek.ingest("source", source_payload(title="污染过滤"))
    fresh_ek._registry.commit()
    fresh_ek._registry.conn.execute("PRAGMA foreign_keys = OFF")
    fresh_ek._registry.conn.execute(
        "INSERT INTO relations(source_id, target_id, type, origin) VALUES(?, ?, ?, ?)",
        (entry.id, "missing-entry", "related", "confirmed"),
    )

    scores = compute_pagerank(fresh_ek._registry)

    assert scores == {entry.id: pytest.approx(1.0)}


def test_archived_entries_are_excluded_by_default(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="活跃源节点"))
    target = fresh_ek.ingest("source", source_payload(title="活跃目标节点"))
    archived = fresh_ek.ingest("source", source_payload(title="归档节点"))
    fresh_ek.link(source.id, target.id, rel_type="related")
    fresh_ek.link(archived.id, target.id, rel_type="related")
    fresh_ek.update(archived.id, {"status": "archived"})

    scores = compute_pagerank(fresh_ek._registry)

    assert set(scores) == {source.id, target.id}

    assert archived.id not in scores


def test_persist_resets_entries_outside_active_graph(fresh_ek) -> None:
    active = fresh_ek.ingest("source", source_payload(title="活跃持久化"))
    archived = fresh_ek.ingest("source", source_payload(title="归档持久化"))
    fresh_ek.update(archived.id, {"status": "archived"})

    persist_authority_scores(fresh_ek._registry, {active.id: 1.0})

    rows = fresh_ek._registry.conn.execute(
        "SELECT entry_id, authority_score FROM entry_metrics"
    ).fetchall()
    score_map = {row["entry_id"]: row["authority_score"] for row in rows}
    assert score_map[active.id] == pytest.approx(1.0)
    assert score_map[archived.id] == pytest.approx(0.0)
