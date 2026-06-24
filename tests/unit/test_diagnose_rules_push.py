from __future__ import annotations

from ego_knowledge._diagnose_rules import push
from ego_knowledge.models import RelationType

from .support import (
    concept_payload,
    decision_payload,
    dossier_payload,
    note_payload,
    source_payload,
)


def _set_metrics(ek, entry_id: str, **metrics: float) -> None:
    registry = ek._registry
    assignments = ", ".join(f"{key} = ?" for key in metrics)
    registry.conn.execute(
        f"UPDATE entry_metrics SET {assignments} WHERE entry_id = ?",
        (*metrics.values(), entry_id),
    )
    registry.commit()


def _relation(target: str, rel_type: str) -> dict[str, str]:
    return {"target": target, "type": rel_type, "source": "confirmed"}


def _source_payload(title: str, index: str, **overrides: object) -> dict[str, object]:
    payload = source_payload(
        title=title,
        source_type="web",
        source_url=f"https://example.com/push/{index}",
        content_hash=f"hash-push-{index}",
        search_terms=[
            title,
            f"push-source-{index}",
            f"origin-{index}",
            f"推送来源{index}",
            f"alias-src-{index}",
        ],
    )
    payload.update(overrides)
    return payload


def _note_payload(source_id: str, title: str, index: str, **overrides: object) -> dict[str, object]:
    payload = note_payload(
        source_id,
        title=title,
        tags=["结晶簇"],
        search_terms=[
            title,
            f"push-note-{index}",
            f"memo-{index}",
            f"推送笔记{index}",
            f"alias-note-{index}",
        ],
    )
    payload.update(overrides)
    return payload


def _concept_payload(
    source_id: str,
    title: str,
    index: str,
    **overrides: object,
) -> dict[str, object]:
    payload = concept_payload(
        source_id,
        title=title,
        search_terms=[
            title,
            f"push-concept-{index}",
            f"idea-{index}",
            f"推送概念{index}",
            f"alias-concept-{index}",
        ],
    )
    payload.update(overrides)
    return payload


def _dossier_payload(
    evidence_ref: str,
    title: str,
    index: str,
    **overrides: object,
) -> dict[str, object]:
    payload = dossier_payload(
        evidence_ref,
        title=title,
        search_terms=[
            title,
            f"push-dossier-{index}",
            f"brief-{index}",
            f"推送档案{index}",
            f"alias-dossier-{index}",
        ],
    )
    payload.update(overrides)
    return payload


def _decision_payload(
    evidence_ref: str,
    title: str,
    index: str,
    **overrides: object,
) -> dict[str, object]:
    payload = decision_payload(
        evidence_ref,
        title=title,
        search_terms=[
            title,
            f"push-decision-{index}",
            f"choice-{index}",
            f"推送决策{index}",
            f"alias-decision-{index}",
        ],
    )
    payload.update(overrides)
    return payload


def test_push_premise_shaken_hit_for_hot_volatile_premise(fresh_ek) -> None:
    source = fresh_ek.ingest("source", _source_payload("推送前提来源甲", "premise-hit"))
    concept = fresh_ek.ingest(
        "concept",
        _concept_payload(source.id, "银帆震荡概念", "premise-concept", freshness="volatile"),
    )
    decision = fresh_ek.ingest(
        "decision",
        _decision_payload(concept.id, "北极震荡决策", "premise-hit"),
    )
    _set_metrics(fresh_ek, decision.id, retrieval_heat=3.0)

    findings = push.rule_push_premise_shaken(fresh_ek._registry)

    assert findings[0].target_id == decision.id
    assert findings[0].severity.value == "high"


def test_push_premise_shaken_miss_when_decision_is_cold(fresh_ek) -> None:
    source = fresh_ek.ingest("source", _source_payload("推送前提来源乙", "premise-miss"))
    concept = fresh_ek.ingest(
        "concept",
        _concept_payload(source.id, "冷杉震荡概念", "premise-cold", freshness="volatile"),
    )
    fresh_ek.ingest(
        "decision",
        _decision_payload(concept.id, "冷湖震荡决策", "premise-cold"),
    )

    assert push.rule_push_premise_shaken(fresh_ek._registry) == []


def test_push_crystallize_hit_for_note_group(fresh_ek) -> None:
    for index, title in enumerate(["琥珀素材甲", "玛瑙素材乙", "白露素材丙"], start=1):
        source = fresh_ek.ingest(
            "source",
            _source_payload(f"推送结晶来源{index}", f"crystal-{index}"),
        )
        fresh_ek.ingest("note", _note_payload(source.id, title, f"crystal-{index}"))

    findings = push.rule_push_crystallize(fresh_ek._registry)

    assert any(finding.rule_id == "push_crystallize" for finding in findings)


def test_push_crystallize_miss_when_absorbed(fresh_ek) -> None:
    absorber_source = fresh_ek.ingest("source", _source_payload("推送结晶来源丁", "crystal-abs"))
    notes = [
        fresh_ek.ingest(
            "note",
            _note_payload(
                fresh_ek.ingest(
                    "source",
                    _source_payload(f"推送吸收来源{index}", f"crystal-abs-{index}"),
                ).id,
                title,
                f"crystal-abs-{index}",
            ),
        )
        for index, title in enumerate(["山茶素材甲", "芦苇素材乙", "霜叶素材丙"], start=1)
    ]
    fresh_ek.ingest(
        "concept",
        _concept_payload(
            absorber_source.id,
            "吸收结晶概念",
            "crystal-absorber",
            relations=[_relation(note.id, RelationType.DERIVED_FROM.value) for note in notes],
        ),
    )

    assert push.rule_push_crystallize(fresh_ek._registry) == []


def test_push_pseudo_stable_hit_for_single_source_type(fresh_ek) -> None:
    source_a = fresh_ek.ingest(
        "source",
        _source_payload("推送伪稳来源甲", "stable-a", source_type="web"),
    )
    source_b = fresh_ek.ingest(
        "source",
        _source_payload("推送伪稳来源乙", "stable-b", source_type="web"),
    )
    concept = fresh_ek.ingest(
        "concept",
        _concept_payload(
            source_a.id,
            "藤蔓伪稳概念",
            "stable-hit",
            evidence_refs=[source_a.id, source_b.id],
        ),
    )

    findings = push.rule_push_pseudo_stable(fresh_ek._registry)

    assert any(finding.target_id == concept.id for finding in findings)


def test_push_pseudo_stable_miss_with_diverse_sources(fresh_ek) -> None:
    source_a = fresh_ek.ingest(
        "source",
        _source_payload("推送多源来源甲", "stable-div-a", source_type="web"),
    )
    source_b = fresh_ek.ingest(
        "source",
        _source_payload("推送多源来源乙", "stable-div-b", source_type="doc"),
    )
    fresh_ek.ingest(
        "concept",
        _concept_payload(
            source_a.id,
            "晴川多源概念",
            "stable-miss",
            evidence_refs=[source_a.id, source_b.id],
        ),
    )

    assert push.rule_push_pseudo_stable(fresh_ek._registry) == []


def test_push_internal_split_hit_for_two_contradicts(fresh_ek) -> None:
    source_a = fresh_ek.ingest("source", _source_payload("推送裂解来源甲", "split-a"))
    source_b = fresh_ek.ingest("source", _source_payload("推送裂解来源乙", "split-b"))
    source_target = fresh_ek.ingest("source", _source_payload("推送裂解来源丙", "split-target"))
    challenger_a = fresh_ek.ingest(
        "concept",
        _concept_payload(source_a.id, "推送反例甲", "split-a"),
    )
    challenger_b = fresh_ek.ingest(
        "concept",
        _concept_payload(source_b.id, "青岚反证乙", "split-b"),
    )
    concept = fresh_ek.ingest(
        "concept",
        _concept_payload(
            source_target.id,
            "飞瀑裂解概念",
            "split-target",
            relations=[
                _relation(challenger_a.id, RelationType.CONTRADICTS.value),
                _relation(challenger_b.id, RelationType.CONTRADICTS.value),
            ],
        ),
    )

    findings = push.rule_push_internal_split(fresh_ek._registry)

    assert any(finding.target_id == concept.id for finding in findings)


def test_push_internal_split_miss_for_single_contradict(fresh_ek) -> None:
    source_a = fresh_ek.ingest("source", _source_payload("推送裂解来源丁", "split-one"))
    source_b = fresh_ek.ingest("source", _source_payload("推送裂解来源戊", "split-miss"))
    challenger = fresh_ek.ingest(
        "concept",
        _concept_payload(source_a.id, "推送单反例甲", "split-one"),
    )
    fresh_ek.ingest(
        "concept",
        _concept_payload(
            source_b.id,
            "丘陵裂解概念",
            "split-miss-target",
            relations=[_relation(challenger.id, RelationType.CONTRADICTS.value)],
        ),
    )

    assert push.rule_push_internal_split(fresh_ek._registry) == []


def test_push_cognitive_divergence_hit_for_hot_drift_dossier(fresh_ek) -> None:
    source = fresh_ek.ingest("source", _source_payload("推送分歧来源甲", "div-hit"))
    dossier = fresh_ek.ingest("dossier", _dossier_payload(source.id, "星河分歧档案", "div-hit"))
    _set_metrics(fresh_ek, dossier.id, drift_score=0.7, retrieval_heat=2.0)

    findings = push.rule_push_cognitive_divergence(fresh_ek._registry)

    assert [finding.target_id for finding in findings] == [dossier.id]


def test_push_cognitive_divergence_miss_for_low_drift(fresh_ek) -> None:
    source = fresh_ek.ingest("source", _source_payload("推送分歧来源乙", "div-miss"))
    dossier = fresh_ek.ingest("dossier", _dossier_payload(source.id, "林海分歧档案", "div-miss"))
    _set_metrics(fresh_ek, dossier.id, drift_score=0.6, retrieval_heat=2.0)

    assert push.rule_push_cognitive_divergence(fresh_ek._registry) == []
