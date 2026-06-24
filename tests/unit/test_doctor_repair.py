from __future__ import annotations

import json
from pathlib import Path

from ego_knowledge.doctor import _parse_recovery_log, doctor

from .support import absolute_entry_path, source_payload


def test_parse_recovery_log_skips_malformed_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "recovery.log"
    log_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-04-17T09:00:00+0800",
                        "target_path": "/tmp/good.md",
                        "message": "ok",
                    }
                ),
                "{bad json",
                json.dumps(["not-a-dict"]),
                json.dumps({"message": "missing target"}),
            ]
        ),
        encoding="utf-8",
    )

    records = _parse_recovery_log(log_path)

    assert records == [
        {
            "ts": "2026-04-17T09:00:00+0800",
            "target_path": "/tmp/good.md",
            "message": "ok",
        }
    ]


def test_doctor_repair_rebuilds_registry_entry_from_recovery_log(fresh_ek, ek_root: Path) -> None:
    source = fresh_ek.ingest("source", source_payload(title="待恢复来源"))
    source_path = absolute_entry_path(ek_root, source.file_path or "")
    recovery_log = ek_root / "logs" / "refresh" / "recovery.log"
    recovery_log.parent.mkdir(parents=True, exist_ok=True)
    recovery_log.write_text(
        json.dumps(
            {
                "ts": "2026-04-17T09:00:00+0800",
                "target_path": str(source_path),
                "message": "COMMIT 失败",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    fresh_ek._registry.delete_entry_by_path(str(source_path))
    fresh_ek._registry.commit()

    assert fresh_ek._registry.has_entry(source.id) is False

    doctor(fresh_ek._registry, ek_root, repair=True)

    assert fresh_ek._registry.has_entry(source.id) is True
