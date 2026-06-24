from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ego_knowledge.cli import main
from ego_knowledge.errors import NotFoundError

from .support import source_payload


def test_get_records_access_log(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="读取来源"))

    entry = fresh_ek.get(source.id)
    row = fresh_ek._registry.conn.execute(
        "SELECT COUNT(*) AS total FROM access_log WHERE entry_id = ?",
        (source.id,),
    ).fetchone()

    assert entry.title == "读取来源"
    assert row is not None
    assert row["total"] == 1


def test_get_missing_entry_raises(fresh_ek) -> None:
    with pytest.raises(NotFoundError):
        fresh_ek.get("ek_src_01HXYZ1234ABCDEFGHJKMNPQRS")


def test_cli_build_registry_and_ingest_smoke(ek_root: Path) -> None:
    runner = CliRunner()
    env = {"EK_DATA_ROOT": str(ek_root)}

    build_result = runner.invoke(main, ["build-registry"], env=env)
    assert build_result.exit_code == 0
    assert json.loads(build_result.output)["entries_ok"] == 0

    ingest_result = runner.invoke(
        main,
        [
            "ingest",
            "--kind",
            "source",
            "--payload",
            json.dumps(
                source_payload(
                    title="CLI 烟测",
                    search_terms=["CLI 烟测", "cli", "smoke", "烟测别名", "alias-cli"],
                ),
                ensure_ascii=False,
            ),
        ],
        env=env,
    )
    assert ingest_result.exit_code == 0
    payload = json.loads(ingest_result.output)
    assert payload["id"].startswith("ek_src_")
