from __future__ import annotations

from ego_knowledge import _entry_store
from ego_knowledge.doctor import Finding
from ego_knowledge.registry import Registry
from tests.unit.support import concept_payload, note_payload, source_payload


def _capture_local_rule_touched(monkeypatch) -> list[set[str]]:
    touched_calls: list[set[str]] = []

    def fake_check_local_rules(
        touched_ids: set[str],
        registry: Registry,
        *,
        embedder: object | None = None,
    ) -> list[Finding]:
        del registry, embedder
        touched_calls.append(set(touched_ids))
        return []

    monkeypatch.setattr(_entry_store, "check_local_rules", fake_check_local_rules)
    return touched_calls


def test_update_non_materialized_rules_include_neighbors(fresh_ek, monkeypatch) -> None:
    source = fresh_ek.ingest("source", source_payload(title="非物化更新来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="非物化更新概念"))
    touched_calls = _capture_local_rule_touched(monkeypatch)

    fresh_ek.update(concept.id, {"tags": ["非物化更新标签"]})

    assert touched_calls[-1] >= {concept.id, source.id}


def test_promote_rules_include_neighbors(fresh_ek, monkeypatch) -> None:
    source = fresh_ek.ingest("source", source_payload(title="升格邻居来源"))
    note = fresh_ek.ingest("note", note_payload(source.id, title="升格邻居笔记"))
    touched_calls = _capture_local_rule_touched(monkeypatch)

    promoted = fresh_ek.promote(note.id, target_kind="dossier")

    assert touched_calls[-1] >= {promoted.id, note.id, source.id}


def test_domains_migrate_rules_include_neighbors(fresh_ek, monkeypatch) -> None:
    fresh_ek.domains_add("规则迁移领域")
    source = fresh_ek.ingest("source", source_payload(title="迁移邻居来源"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="迁移邻居Alpha",
            search_terms=["迁移邻居Alpha", "concept-migrate-a", "src", "来源", "alias-migrate-a"],
        ),
    )
    related = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="关联节点Omega",
            search_terms=["关联节点Omega", "concept-migrate-b", "src", "来源", "alias-migrate-b"],
        ),
    )
    fresh_ek.link(concept.id, related.id, rel_type="related")
    touched_calls = _capture_local_rule_touched(monkeypatch)

    fresh_ek.domains_migrate([concept.id], target_domain="规则迁移领域")

    assert touched_calls[-1] >= {concept.id, source.id, related.id}
