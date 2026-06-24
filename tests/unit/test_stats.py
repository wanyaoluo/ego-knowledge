from __future__ import annotations

import pytest

from ego_knowledge.errors import ValidationError

from .support import concept_payload, source_payload


def test_stats_supports_total_and_grouped_views(fresh_ek) -> None:
    fresh_ek.domains_add("治理")
    source = fresh_ek.ingest("source", source_payload(title="统计来源"))
    fresh_ek.ingest("concept", concept_payload(source.id, title="统计概念", domain="治理"))

    summary = fresh_ek.stats()
    kind_stats = fresh_ek.stats(group_by="kind")
    domain_stats = fresh_ek.stats(group_by="domain")

    assert summary["total"] == 2
    assert len(summary["entries"]) == 2
    assert summary["counts"] == {}
    assert kind_stats["counts"]["concept"] == 1
    assert kind_stats["counts"]["source"] == 1
    assert domain_stats["counts"]["治理"] == 1
    assert domain_stats["counts"]["<null>"] == 1


def test_stats_rejects_unsupported_group_by(fresh_ek) -> None:
    with pytest.raises(ValidationError, match="不支持的分组字段"):
        fresh_ek.stats(group_by="owner")
