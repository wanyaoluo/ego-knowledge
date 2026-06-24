"""Local L3 rule checks for recently touched EgoKnowledge entries."""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from ._validation import CachedEmbedder, collect_conflict_candidates, collect_semantic_candidates
from .doctor import Finding, Severity
from .errors import ValidationError
from .models import ConceptEntry, DossierEntry, Entry, Kind, entry_to_frontmatter
from .registry import Registry
from .search import search as run_search
from .unicode_utils import to_nfc

_MISSING_RELATION_HINT_TOP_N = 10
_FINDING_MESSAGE_LIMIT = 500
SEMANTIC_DUPLICATE_THRESHOLD = 0.92
SEMANTIC_DUPLICATE_THRESHOLD_META_KEY = "semantic_duplicate_threshold"

# Phase 3 task 3.1 plan L90-L95 temporary defaults; Phase 5 baseline may replace them.
_PROMOTION_SIGNAL_THRESHOLDS: tuple[tuple[Kind, str, float, float, bool], ...] = (
    (Kind.NOTE, Kind.DOSSIER.value, 3.0, 1.0, False),
    (Kind.NOTE, Kind.CONCEPT.value, 5.0, 3.0, False),
    (Kind.DOSSIER, Kind.CONCEPT.value, 3.0, 0.0, True),
    (Kind.CONCEPT, Kind.DECISION.value, 0.0, 5.0, True),
)


def check_local_rules(
    touched_ids: set[str],
    registry: Registry,
    *,
    embedder: CachedEmbedder | None = None,
) -> list[Finding]:
    """Run local rules for touched entries and return findings."""

    if not touched_ids:
        return []

    findings: list[Finding] = []
    for touched_id in sorted(touched_ids):
        if not registry.has_entry(touched_id):
            continue
        entry = registry.get_entry(touched_id)
        findings.extend(_rule_duplicate_candidate(entry, registry))
        findings.extend(_rule_promotion_signal(entry))
        findings.extend(_rule_missing_relation_hint(entry, registry))
        findings.extend(_rule_semantic_duplicate_candidate(entry, registry, embedder))
    return findings


def _rule_duplicate_candidate(entry: Entry, registry: Registry) -> list[Finding]:
    candidate_ids = collect_conflict_candidates(
        registry,
        entry.kind,
        entry_to_frontmatter(entry),
        ignore_ids={entry.id},
    )
    if not candidate_ids:
        return []
    return [
        Finding(
            rule_id="duplicate_candidate",
            severity=Severity.MEDIUM,
            target_id=entry.id,
            target_path=_target_path(entry),
            message=f"重复候选: {','.join(candidate_ids[:3])}",
        )
    ]


def _rule_promotion_signal(entry: Entry) -> list[Finding]:
    if entry.metrics is None:
        return []

    ratio = _metric_value(entry.metrics, "compression_ratio")
    relevance = _metric_value(entry.metrics, "action_relevance")
    findings: list[Finding] = []
    for (
        source_kind,
        target_kind,
        min_ratio,
        min_relevance,
        require_evidence,
    ) in _PROMOTION_SIGNAL_THRESHOLDS:
        if entry.kind != source_kind:
            continue
        if ratio < min_ratio or relevance < min_relevance:
            continue
        if require_evidence and not _has_evidence_refs(entry):
            continue
        findings.append(
            Finding(
                rule_id="promotion_signal",
                severity=Severity.MEDIUM,
                target_id=entry.id,
                target_path=_target_path(entry),
                message=(
                    f"建议 {entry.kind.value}→{target_kind} 升格: "
                    f"ratio={ratio:g}, relevance={relevance:g}"
                ),
            )
        )
    return findings


def _rule_missing_relation_hint(entry: Entry, registry: Registry) -> list[Finding]:
    query_terms = [term for term in entry.search_terms if term.strip()]
    if not query_terms:
        return []
    tags = [tag for tag in entry.tags if tag.strip()]
    query_parts = [*query_terms, *tags]
    results = _merge_ids(
        _search_text(registry, " ".join(query_parts), limit=_MISSING_RELATION_HINT_TOP_N),
        _tag_exact_matches(registry, tags, limit=_MISSING_RELATION_HINT_TOP_N),
        limit=_MISSING_RELATION_HINT_TOP_N,
    )
    related_ids = set(registry.neighbors(entry.id, direction="both"))
    hint_ids: list[str] = []
    for result_id in results:
        if result_id == entry.id or result_id in related_ids or not registry.has_entry(result_id):
            continue
        candidate = registry.get_entry(result_id)
        if candidate.kind == Kind.SOURCE:
            continue
        hint_ids.append(result_id)
    if len(hint_ids) < 2:
        return []
    return [
        Finding(
            rule_id="missing_relation_hint",
            severity=Severity.MEDIUM,
            target_id=entry.id,
            target_path=_target_path(entry),
            message=f"未关联候选: {','.join(hint_ids[:3])}",
        )
    ]


def _rule_semantic_duplicate_candidate(
    entry: Entry,
    registry: Registry,
    embedder: CachedEmbedder | None,
) -> list[Finding]:
    if embedder is None:
        return []

    threshold = _semantic_duplicate_threshold(registry)
    candidate_ids = collect_semantic_candidates(
        registry,
        embedder,
        entry,
        ignore_ids={entry.id},
        threshold=threshold,
    )
    if not candidate_ids:
        return []
    message = f"语义重复候选(≥{threshold:g}): {','.join(candidate_ids[:3])}"
    if len(message) > _FINDING_MESSAGE_LIMIT:
        message = message[: _FINDING_MESSAGE_LIMIT - 1] + "…"
    return [
        Finding(
            rule_id="semantic_duplicate_candidate",
            severity=Severity.MEDIUM,
            target_id=entry.id,
            target_path=_target_path(entry),
            message=message,
        )
    ]


def _semantic_duplicate_threshold(registry: Registry) -> float:
    row = registry.conn.execute(
        "SELECT value FROM registry_meta WHERE key = ?",
        (SEMANTIC_DUPLICATE_THRESHOLD_META_KEY,),
    ).fetchone()
    if row is None:
        return SEMANTIC_DUPLICATE_THRESHOLD
    raw_value = row["value"]
    try:
        threshold = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"{SEMANTIC_DUPLICATE_THRESHOLD_META_KEY} 配置值非法: {raw_value!r}"
        ) from exc
    if not 0.0 < threshold <= 1.0:
        raise ValidationError(f"{SEMANTIC_DUPLICATE_THRESHOLD_META_KEY} 必须在 (0, 1] 区间")
    return threshold


def _search_text(registry: Registry, query: str, *, limit: int) -> list[str]:
    search_text = getattr(registry, "search_text", None)
    if callable(search_text):
        raw_results = search_text(query=query, limit=limit)
    else:
        raw_results = run_search(
            registry,
            query=query,
            limit=limit,
            expand_graph=False,
            data_root=registry.path.parent.parent,
        )
    return _result_ids(cast(Iterable[object], raw_results))


def _tag_exact_matches(registry: Registry, tags: list[str], *, limit: int) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        rows = registry.conn.execute(
            """
            SELECT entry_id
              FROM entry_tags
             WHERE tag = ?
             ORDER BY entry_id
             LIMIT ?
            """,
            (to_nfc(tag), limit),
        ).fetchall()
        for row in rows:
            entry_id = cast(str, row["entry_id"])
            if entry_id in seen:
                continue
            seen.add(entry_id)
            ids.append(entry_id)
    return ids[:limit]


def _result_ids(results: Iterable[object]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for result in results:
        result_id: object
        if isinstance(result, str):
            result_id = result
        elif isinstance(result, dict):
            result_id = result.get("id")
        else:
            result_id = getattr(result, "id", None)
        if isinstance(result_id, str) and result_id not in seen:
            seen.add(result_id)
            ids.append(result_id)
    return ids


def _merge_ids(primary: list[str], secondary: list[str], *, limit: int) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for entry_id in [*primary, *secondary]:
        if entry_id in seen:
            continue
        seen.add(entry_id)
        ids.append(entry_id)
        if len(ids) >= limit:
            break
    return ids


def _metric_value(metrics: dict[str, object], key: str) -> float:
    value = metrics.get(key, 0.0)
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _has_evidence_refs(entry: Entry) -> bool:
    return isinstance(entry, (ConceptEntry, DossierEntry)) and bool(entry.evidence_refs)


def _target_path(entry: Entry) -> str | None:
    return str(entry.file_path) if entry.file_path is not None else None
