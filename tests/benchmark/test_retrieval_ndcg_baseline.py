"""NDCG gold-set benchmark for the real active EgoKnowledge corpus.

This complements ``test_bm25_baseline.py``: that test seeds a tiny synthetic
fixture for deterministic regression, while this file evaluates the manually
annotated 30-query gold set against the current catalog and writes v1 baseline
metrics for dense/BM25 comparison.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from statistics import mean, median
from typing import Any

import pytest

from ego_knowledge.core import EgoKnowledge

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "bm25_baseline_queries.jsonl"
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = PACKAGE_ROOT / "data" / "EgoKnowledge"
OUTPUT_PATH = DEFAULT_DATA_ROOT / "logs" / "retrieval-bench" / "v1-baseline.json"


def test_compute_ndcg() -> None:
    relevance = {"a": 3, "b": 2, "c": 0}

    assert _ndcg_at_k(["a", "b", "c"], relevance, 10) == pytest.approx(1.0)
    assert _ndcg_at_k(["c", "b", "a"], relevance, 10) < 1.0
    assert _ndcg_at_k([], relevance, 10) == 0.0


def test_multi_scale_baseline() -> None:
    cases = _load_cases(FIXTURE_PATH)
    assert len(cases) >= 30
    assert all(any(score > 0 for score in _relevance(case).values()) for case in cases)

    data_root = Path(os.environ.get("EK_DATA_ROOT", DEFAULT_DATA_ROOT))
    catalog_path = data_root / "registry" / "catalog.sqlite"
    if not catalog_path.exists():
        pytest.skip(f"EgoKnowledge catalog 不存在: {catalog_path}")

    try:
        ek = EgoKnowledge(data_root, dense_disabled=True)
    except RuntimeError as exc:
        if "schema_version" in str(exc):
            pytest.skip(f"EgoKnowledge catalog schema 不兼容: {exc}")
        raise
    try:
        per_query = [_evaluate_case(ek, case) for case in cases]
        total_entries = _active_entry_count(ek)
    finally:
        ek.close()

    scale_summary = {
        name: _summarize_scale(per_query, total_entries, cap)
        for name, cap in {"small_100": 100, "medium_500": 500, "full": None}.items()
    }
    baseline = {
        "schema_version": "2.2",
        "query_count": len(per_query),
        "fixture": FIXTURE_PATH.as_posix(),
        "corpus_entry_count": total_entries,
        "by_scale": scale_summary,
        "queries": per_query,
    }

    output_path = Path(os.environ.get("EK_NDCG_BASELINE_PATH", OUTPUT_PATH))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")

    assert output_path.exists()
    assert set(scale_summary) == {"small_100", "medium_500", "full"}
    assert all(0.0 <= item["ndcg_at_10_mean"] <= 1.0 for item in scale_summary.values())


def _load_cases(path: Path) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    for case in cases:
        relevance = case.get("relevance")
        if not isinstance(case.get("query"), str) or not isinstance(relevance, dict):
            raise AssertionError(f"gold set 格式错误: {case}")
        case["relevance"] = {str(entry_id): int(score) for entry_id, score in relevance.items()}
    return cases


def _evaluate_case(ek: EgoKnowledge, case: dict[str, Any]) -> dict[str, Any]:
    query = str(case["query"])
    relevance = _relevance(case)
    result_ids = [result.id for result in ek.search(query, limit=10, backends=["bm25"])]
    return {
        "query_id": case.get("query_id", ""),
        "query": query,
        "result_ids": result_ids,
        "ndcg_at_10": _ndcg_at_k(result_ids, relevance, 10),
        "mrr_at_10": _mrr_at_k(result_ids, relevance, 10),
        "recall_at_10": _recall_at_k(result_ids, relevance, 10),
    }


def _summarize_scale(
    per_query: list[dict[str, Any]],
    total_entries: int,
    cap: int | None,
) -> dict[str, Any]:
    scores = [float(item["ndcg_at_10"]) for item in per_query]
    return {
        "query_count": len(per_query),
        "corpus_entry_count": total_entries if cap is None else min(total_entries, cap),
        "ndcg_at_10_mean": mean(scores),
        "p50": median(scores),
        "p90": _percentile(scores, 0.9),
        "note": "当前实现按完整活跃语料检索；scale 字段记录对应规模口径。",
    }


def _ndcg_at_k(result_ids: list[str], relevance: dict[str, int], k: int) -> float:
    ideal = sorted((score for score in relevance.values() if score > 0), reverse=True)[:k]
    ideal_dcg = _dcg(ideal)
    if ideal_dcg <= 0:
        return 0.0
    actual = [relevance.get(entry_id, 0) for entry_id in result_ids[:k]]
    return _dcg(actual) / ideal_dcg


def _dcg(scores: list[int]) -> float:
    return float(sum((2**score - 1) / math.log2(index + 2) for index, score in enumerate(scores)))


def _relevance(case: dict[str, Any]) -> dict[str, int]:
    relevance = case.get("relevance")
    if not isinstance(relevance, dict):
        raise AssertionError(f"gold set 缺少 relevance: {case}")
    return {str(entry_id): int(score) for entry_id, score in relevance.items()}


def _mrr_at_k(result_ids: list[str], relevance: dict[str, int], k: int) -> float:
    for index, entry_id in enumerate(result_ids[:k], start=1):
        if relevance.get(entry_id, 0) > 0:
            return 1.0 / index
    return 0.0


def _recall_at_k(result_ids: list[str], relevance: dict[str, int], k: int) -> float:
    positive = {entry_id for entry_id, score in relevance.items() if score > 0}
    if not positive:
        return 0.0
    hits = sum(1 for entry_id in result_ids[:k] if entry_id in positive)
    return hits / len(positive)


def _percentile(values: list[float], q: float) -> float:
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, max(0, math.ceil(q * len(sorted_values)) - 1))
    return sorted_values[index]


def _active_entry_count(ek: EgoKnowledge) -> int:
    row = ek._registry.conn.execute(
        "SELECT COUNT(*) AS count FROM entries WHERE status != 'archived'"
    ).fetchone()
    return int(row["count"])
