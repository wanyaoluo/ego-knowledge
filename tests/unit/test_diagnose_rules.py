from __future__ import annotations

import json
from pathlib import Path

import pytest

from ego_knowledge.diagnose import (
    _DIAGNOSE_RULES,
    _compute_metric_stats,
    _rule_source_reachability,
    _rule_view_as_evidence,
    diagnose,
    establish_baseline,
)
from ego_knowledge.frontmatter import _fm_to_entry, read_file, write_file

from .support import (
    absolute_entry_path,
    concept_payload,
    source_payload,
    view_payload,
)


def test_source_reachability_flags_entries_without_source_path(fresh_ek, ek_root: Path) -> None:
    source = fresh_ek.ingest("source", source_payload(title="诊断来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="失联概念"))

    _rewrite_frontmatter(
        fresh_ek,
        ek_root,
        concept.file_path or "",
        {"evidence_refs": []},
    )

    findings = _rule_source_reachability(fresh_ek._registry)

    assert [finding.target_id for finding in findings] == [concept.id]


def test_view_as_evidence_flags_view_targets_on_any_outgoing_edge(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="视图来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="概念"))
    view = fresh_ek.ingest("view", view_payload(title="视图"))

    fresh_ek.link(concept.id, view.id, "related")

    findings = _rule_view_as_evidence(fresh_ek._registry)

    assert [finding.rule_id for finding in findings] == ["redline_10_view_as_evidence"]
    assert findings[0].target_id == concept.id


def test_diagnose_checked_rules_include_registered_l4_rules(fresh_ek, ek_root: Path) -> None:
    fresh_ek.ingest("view", view_payload(title="空视图"))

    report = diagnose(fresh_ek._registry, ek_root)

    assert report.checked_rules == [rule_id for rule_id, _ in _DIAGNOSE_RULES]
    assert len(report.checked_rules) == 16
    assert Path(report.report_path).exists()


# ---------------------------------------------------------------------------
# 2.4  establish_baseline
# ---------------------------------------------------------------------------


def test_establish_baseline_produces_json_with_five_metrics(fresh_ek, ek_root: Path) -> None:
    fresh_ek.ingest("source", source_payload(title="baseline 来源"))

    baseline_path = establish_baseline(fresh_ek._registry, ek_root)

    assert baseline_path.exists()
    data = json.loads(baseline_path.read_text(encoding="utf-8"))

    expected_keys = {
        "evidence_strength",
        "drift_score",
        "compression_ratio",
        "action_relevance",
        "retrieval_heat",
    }
    assert expected_keys.issubset(set(data.keys()))

    for metric_name in expected_keys:
        stats = data[metric_name]
        for stat_key in ("count", "min", "p50", "p90", "p95", "max"):
            assert stat_key in stats, f"{metric_name} 缺少 {stat_key}"


def test_compute_metric_stats_handles_empty_values() -> None:
    assert _compute_metric_stats([]) == {
        "count": 0,
        "min": 0.0,
        "p50": 0.0,
        "p90": 0.0,
        "p95": 0.0,
        "max": 0.0,
    }


def test_compute_metric_stats_uses_linear_percentiles() -> None:
    stats = _compute_metric_stats([20.0, 0.0, 10.0])

    assert stats["count"] == 3
    assert stats["min"] == 0.0
    assert stats["p50"] == 10.0
    assert stats["p90"] == pytest.approx(18.0)
    assert stats["p95"] == pytest.approx(19.0)
    assert stats["max"] == 20.0


def _rewrite_frontmatter(
    ek: object,
    data_root: Path,
    relative_path: str,
    updates: dict[str, object],
) -> None:
    path = absolute_entry_path(data_root, relative_path)
    frontmatter_map, body = read_file(str(path))
    frontmatter_map.update(updates)
    write_file(str(path), frontmatter_map, body)
    entry = _fm_to_entry(frontmatter_map, file_path=str(path), body=body)
    registry = getattr(ek, "_registry")
    registry.upsert_entry(entry, path, body)
    registry.commit()
