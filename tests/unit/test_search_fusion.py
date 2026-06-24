from __future__ import annotations

import pytest

from ego_knowledge.search import (
    AUTHORITY_WEIGHT,
    SearchResult,
    SearchRouter,
    _build_snippet,
    _exact_sql_statements,
    _match_filters,
    _merge_backends,
)

from .support import concept_payload, source_payload


def test_search_router_returns_empty_for_emoji_only_query(fresh_ek, ek_root) -> None:
    router = SearchRouter(fresh_ek._registry, data_root=ek_root)

    assert router.search("😀 😀") == []


def test_search_helpers_cover_filters_and_backend_merge(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="融合来源"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="Graph Projection",
            aliases=["projection"],
            tags=["graph", "search"],
            body="Graph projection 在图扩展检索里常见。" + " " + "x" * 40,
        ),
    )
    entry = fresh_ek.get(concept.id)

    assert "Graph projection" in _build_snippet(entry, ["projection"])
    assert _match_filters(entry, {"tags": ["graph"], "kind": entry.kind.value}) is True
    assert _match_filters(entry, {"tags": ["missing"]}) is False
    assert _merge_backends(["exact"], ["bm25", "exact", "graph"]) == ["exact", "bm25", "graph"]
    assert _exact_sql_statements(include_title_substring=False)[0][1] == 80.0
    assert _exact_sql_statements(include_title_substring=True)[-1][1] == 55.0


def test_authority_boost_increases_high_rank(fresh_ek, ek_root) -> None:
    source = fresh_ek.ingest("source", source_payload(title="权威来源"))
    high = fresh_ek.ingest("concept", concept_payload(source.id, title="高图权威"))
    low = fresh_ek.ingest("concept", concept_payload(source.id, title="低检索权重"))
    fresh_ek._registry.conn.executemany(
        "UPDATE entry_metrics SET authority_score = ? WHERE entry_id = ?",
        [(0.8, high.id), (0.2, low.id)],
    )
    fresh_ek._registry.commit()
    router = SearchRouter(fresh_ek._registry, data_root=ek_root)
    results = {
        high.id: SearchResult(id=high.id, score=10.0, backends=["bm25"]),
        low.id: SearchResult(id=low.id, score=10.0, backends=["bm25"]),
    }

    ranked = router._rank_results(results, limit=2)

    assert ranked[0].id == high.id
    assert ranked[0].score == pytest.approx(10.0 * (1.0 + AUTHORITY_WEIGHT))
    assert ranked[1].score == pytest.approx(10.0 * (1.0 + AUTHORITY_WEIGHT * 0.25))


def test_authority_score_map_returns_persisted_scores(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="权威映射来源"))
    entry = fresh_ek.ingest("concept", concept_payload(source.id, title="权威映射"))
    fresh_ek._registry.conn.execute(
        "UPDATE entry_metrics SET authority_score = 0.42 WHERE entry_id = ?",
        (entry.id,),
    )
    fresh_ek._registry.commit()

    assert fresh_ek._registry.authority_score_map([entry.id]) == {entry.id: 0.42}


def test_authority_score_map_empty_input_returns_empty(fresh_ek) -> None:
    assert fresh_ek._registry.authority_score_map([]) == {}


def test_authority_weight_zero_disables_boost(fresh_ek, ek_root) -> None:
    source = fresh_ek.ingest("source", source_payload(title="关闭权威来源"))
    entry = fresh_ek.ingest("concept", concept_payload(source.id, title="关闭权威"))
    fresh_ek._registry.conn.execute(
        "UPDATE entry_metrics SET authority_score = 1 WHERE entry_id = ?",
        (entry.id,),
    )
    fresh_ek._registry.commit()
    router = SearchRouter(fresh_ek._registry, data_root=ek_root)
    results = {entry.id: SearchResult(id=entry.id, score=10.0, backends=["bm25"])}

    ranked = router._rank_results(results, limit=1, authority_weight=0.0)

    assert ranked[0].score == 10.0


def test_exact_match_also_boosted(fresh_ek) -> None:
    entry = fresh_ek.ingest("source", source_payload(title="精确权威"))
    fresh_ek._registry.conn.execute(
        "UPDATE entry_metrics SET authority_score = 1 WHERE entry_id = ?",
        (entry.id,),
    )
    fresh_ek._registry.commit()

    result = fresh_ek.search(entry.id, backends=["exact"], limit=1)[0]

    assert result.id == entry.id
    assert result.score == pytest.approx(1000.0 * (1.0 + AUTHORITY_WEIGHT))


def test_missing_authority_defaults_to_zero(fresh_ek, ek_root) -> None:
    source = fresh_ek.ingest("source", source_payload(title="缺失来源"))
    present = fresh_ek.ingest("concept", concept_payload(source.id, title="有权威"))
    missing = fresh_ek.ingest("concept", concept_payload(source.id, title="缺权威"))
    fresh_ek._registry.conn.execute(
        "UPDATE entry_metrics SET authority_score = 0.5 WHERE entry_id = ?",
        (present.id,),
    )
    fresh_ek._registry.conn.execute(
        "DELETE FROM entry_metrics WHERE entry_id = ?",
        (missing.id,),
    )
    fresh_ek._registry.commit()
    router = SearchRouter(fresh_ek._registry, data_root=ek_root)
    results = {
        present.id: SearchResult(id=present.id, score=10.0, backends=["bm25"]),
        missing.id: SearchResult(id=missing.id, score=10.0, backends=["bm25"]),
    }

    ranked = router._rank_results(results, limit=2)

    assert ranked[0].id == present.id
    assert ranked[0].score == pytest.approx(10.0 * (1.0 + AUTHORITY_WEIGHT))
    assert results[missing.id].score == 10.0


def test_authority_boost_does_not_mutate_input_results(fresh_ek, ek_root) -> None:
    source = fresh_ek.ingest("source", source_payload(title="无副作用来源"))
    entry = fresh_ek.ingest("concept", concept_payload(source.id, title="无副作用权威"))
    fresh_ek._registry.conn.execute(
        "UPDATE entry_metrics SET authority_score = 1 WHERE entry_id = ?",
        (entry.id,),
    )
    fresh_ek._registry.commit()
    router = SearchRouter(fresh_ek._registry, data_root=ek_root)
    results = {entry.id: SearchResult(id=entry.id, score=10.0, backends=["bm25"])}

    ranked_once = router._rank_results(results, limit=1)
    ranked_twice = router._rank_results(results, limit=1)

    assert results[entry.id].score == 10.0
    assert ranked_once[0].score == pytest.approx(10.0 * (1.0 + AUTHORITY_WEIGHT))
    assert ranked_once[0].score == ranked_twice[0].score


def test_all_zero_authority_no_change(fresh_ek, ek_root) -> None:
    source = fresh_ek.ingest("source", source_payload(title="零权威来源"))
    entry = fresh_ek.ingest("concept", concept_payload(source.id, title="零权威"))
    router = SearchRouter(fresh_ek._registry, data_root=ek_root)
    results = {entry.id: SearchResult(id=entry.id, score=10.0, backends=["bm25"])}

    ranked = router._rank_results(results, limit=1)

    assert ranked[0].score == 10.0


def test_normalization_by_max(fresh_ek, ek_root) -> None:
    source = fresh_ek.ingest("source", source_payload(title="归一化来源"))
    max_entry = fresh_ek.ingest("concept", concept_payload(source.id, title="最大权威"))
    mid_entry = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="半数权威"),
        conflict_policy="allow",
    )
    fresh_ek._registry.conn.executemany(
        "UPDATE entry_metrics SET authority_score = ? WHERE entry_id = ?",
        [(10.0, max_entry.id), (5.0, mid_entry.id)],
    )
    fresh_ek._registry.commit()
    router = SearchRouter(fresh_ek._registry, data_root=ek_root)
    results = {
        max_entry.id: SearchResult(id=max_entry.id, score=10.0, backends=["bm25"]),
        mid_entry.id: SearchResult(id=mid_entry.id, score=10.0, backends=["bm25"]),
    }

    ranked = router._rank_results(results, limit=2)

    scores = {result.id: result.score for result in ranked}
    assert scores[max_entry.id] == pytest.approx(10.0 * (1.0 + AUTHORITY_WEIGHT))
    assert scores[mid_entry.id] == pytest.approx(10.0 * (1.0 + AUTHORITY_WEIGHT * 0.5))


def test_graph_neighbor_authority_boosted_in_search_path(fresh_ek) -> None:
    source = fresh_ek.ingest(
        "source",
        source_payload(title="graphseedalpha"),
    )
    neighbor = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="gneighborbeta",
            body="graph neighbor authority boost fixture" + "x" * 50,
        ),
    )

    base_results = fresh_ek.search("graphseedalpha", backends=["bm25", "graph"], limit=5)
    base_neighbor = next(result for result in base_results if result.id == neighbor.id)

    fresh_ek._registry.conn.execute(
        "UPDATE entry_metrics SET authority_score = 1 WHERE entry_id = ?",
        (neighbor.id,),
    )
    fresh_ek._registry.commit()

    boosted_results = fresh_ek.search("graphseedalpha", backends=["bm25", "graph"], limit=5)
    boosted_neighbor = next(result for result in boosted_results if result.id == neighbor.id)

    assert "graph" in boosted_neighbor.backends
    assert boosted_neighbor.score == pytest.approx(base_neighbor.score * (1.0 + AUTHORITY_WEIGHT))
