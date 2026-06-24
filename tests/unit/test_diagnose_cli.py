from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from ego_knowledge.cli import main

from .support import concept_payload, source_payload


def test_diagnose_recompute_authority_cli(ek_root: Path, fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="CLI 权威来源"))
    fresh_ek.ingest("concept", concept_payload(source.id, title="CLI 权威概念"))

    result = CliRunner().invoke(
        main,
        ["diagnose", "--recompute-authority"],
        env={"EK_DATA_ROOT": str(ek_root)},
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["action"] == "recompute_authority"
    assert payload["entries_recomputed"] >= 1


def test_diagnose_recompute_authority_conflicts_with_baseline(ek_root: Path) -> None:
    result = CliRunner().invoke(
        main,
        ["diagnose", "--recompute-authority", "--establish-baseline"],
        env={"EK_DATA_ROOT": str(ek_root)},
    )

    assert result.exit_code != 0
    assert "互斥" in result.output
