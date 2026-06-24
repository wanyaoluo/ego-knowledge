from __future__ import annotations

from ego_knowledge.diagnose import _DIAGNOSE_RULES, diagnose
from tests.unit.support import view_payload


def test_diagnose_rules_registry_has_16_rules() -> None:
    rule_ids = [rule_id for rule_id, _ in _DIAGNOSE_RULES]

    assert len(rule_ids) == 16
    assert len(set(rule_ids)) == 16
    assert "action_demote" in rule_ids
    assert "push_premise_shaken" in rule_ids


def test_diagnose_runs_all_registered_rules(fresh_ek, ek_root) -> None:
    fresh_ek.ingest("view", view_payload(title="规则集空视图"))

    report = diagnose(fresh_ek._registry, ek_root)

    assert report.checked_rules == [rule_id for rule_id, _ in _DIAGNOSE_RULES]
    assert len(report.checked_rules) == 16
