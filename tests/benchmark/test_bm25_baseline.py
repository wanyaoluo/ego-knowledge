from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

from ego_knowledge.core import EgoKnowledge


def test_bm25_baseline(tmp_path: Path) -> None:
    fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "bm25-queries.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    data_root = tmp_path / "data" / "EgoKnowledge"
    ek = EgoKnowledge(data_root)
    try:
        for seed in fixture["seed_entries"]:
            seed_map = dict(seed)
            kind = str(seed_map.pop("kind"))
            body = seed_map.get("body")
            if isinstance(body, str) and len(body) < 50:
                seed_map["body"] = body + " " + "x" * (50 - len(body))
            ek.ingest(kind=kind, payload=seed_map)

        per_query: list[dict[str, object]] = []
        recall_scores: list[float] = []
        mrr_scores: list[float] = []
        by_category: dict[str, dict[str, list[float]]] = {}

        for query_case in fixture["queries"]:
            query = str(query_case["query"])
            expected = {str(item) for item in query_case["expected_relevant"]}
            category = str(query_case["category"])
            result_ids = [result.id for result in ek.search(query, limit=10, backends=["bm25"])]
            recall = _recall_at_10(result_ids, expected)
            mrr = _mrr(result_ids, expected)
            recall_scores.append(recall)
            mrr_scores.append(mrr)
            stats = by_category.setdefault(category, {"recall_at_10": [], "mrr": []})
            stats["recall_at_10"].append(recall)
            stats["mrr"].append(mrr)
            per_query.append(
                {
                    "query": query,
                    "category": category,
                    "expected_relevant": sorted(expected),
                    "result_ids": result_ids,
                    "recall_at_10": recall,
                    "mrr": mrr,
                }
            )
    finally:
        ek.close()

    baseline = {
        "schema_version": fixture["schema_version"],
        "query_count": len(per_query),
        "recall_at_10_mean": mean(recall_scores),
        "mrr_mean": mean(mrr_scores),
        "by_category": {
            category: {
                "query_count": len(stats["recall_at_10"]),
                "recall_at_10_mean": mean(stats["recall_at_10"]),
                "mrr_mean": mean(stats["mrr"]),
            }
            for category, stats in by_category.items()
        },
        "queries": per_query,
    }

    output = Path(__file__).resolve().parents[2] / "logs" / "retrieval-bench" / "baseline.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")

    assert baseline["query_count"] == 50
    assert output.exists()


def _recall_at_10(result_ids: list[str], expected: set[str]) -> float:
    if not expected:
        return 0.0
    hits = sum(1 for entry_id in result_ids[:10] if entry_id in expected)
    return hits / len(expected)


def _mrr(result_ids: list[str], expected: set[str]) -> float:
    for index, entry_id in enumerate(result_ids[:10], start=1):
        if entry_id in expected:
            return 1.0 / index
    return 0.0
