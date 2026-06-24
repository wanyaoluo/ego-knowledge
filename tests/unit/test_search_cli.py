from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from ego_knowledge.cli import main
from ego_knowledge.core import EgoKnowledge

from .support import concept_payload, source_payload


def test_search_and_related_cli_output_contract(ek_root: Path) -> None:
    ek = EgoKnowledge(ek_root)
    try:
        source = ek.ingest("source", source_payload(title="CLI 检索来源"))
        left = ek.ingest(
            "concept",
            concept_payload(
                source.id,
                title="AI 对齐实践",
                search_terms=["AI", "alignment", "对齐", "行为约束", "safe AI"],
                body="AI alignment 可以稳定模型输出。" + " " + "x" * 30,
            ),
        )
        right = ek.ingest(
            "concept",
            concept_payload(
                source.id,
                title="OpenAI GPT 微调方案",
                search_terms=["OpenAI", "GPT", "fine-tuning", "plan", "微调"],
                body="OpenAI GPT fine-tuning plan 关注数据和目标。" + " " + "x" * 20,
            ),
        )
        ek.link(left.id, right.id, rel_type="related")
    finally:
        ek.close()

    runner = CliRunner()
    env = {"EK_DATA_ROOT": str(ek_root)}

    search_result = runner.invoke(
        main,
        ["search", "AI alignment", "--limit", "5", "--no-semantic"],
        env=env,
    )
    assert search_result.exit_code == 0
    search_payload = json.loads(search_result.output)
    assert search_payload["query"] == "AI alignment"
    assert search_payload["results"][0]["id"] == left.id
    assert search_payload["results"][0]["backends"]
    assert "snippet" in search_payload["results"][0]

    related_result = runner.invoke(
        main,
        ["related", left.id, "--depth", "1", "--type", "related"],
        env=env,
    )
    assert related_result.exit_code == 0
    related_payload = json.loads(related_result.output)
    assert [entry["id"] for entry in related_payload] == [right.id]
