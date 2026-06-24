from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import pytest

from ego_knowledge.errors import StorageError, ValidationError
from ego_knowledge.metrics import (
    _record_access,
    _record_access_many,
    compute_retrieval_heat,
)

from .support import source_payload

# ---------------------------------------------------------------------------
# Existing test (kept)
# ---------------------------------------------------------------------------


def test_compute_retrieval_heat_uses_decay_and_window(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="检索来源"))
    now = _dt.datetime.now(tz=_dt.UTC).replace(microsecond=0)
    rows = [
        (source.id, "get", now.isoformat()),
        (source.id, "search", (now - _dt.timedelta(days=30)).isoformat()),
        (source.id, "get", (now - _dt.timedelta(days=100)).isoformat()),
    ]
    fresh_ek._registry.conn.executemany(
        """
        INSERT INTO access_log(entry_id, op, accessed_at)
        VALUES(?, ?, ?)
        """,
        rows,
    )
    fresh_ek._registry.commit()

    heat = compute_retrieval_heat(source.id, fresh_ek._registry)

    assert heat == pytest.approx(1.5, rel=1e-5)


# ---------------------------------------------------------------------------
# 2.1  access_log SQLite + jsonl 双写
# ---------------------------------------------------------------------------


def test_record_access_writes_sqlite_and_jsonl(fresh_ek, ek_root: Path) -> None:
    source = fresh_ek.ingest("source", source_payload(title="双写来源"))
    _record_access(fresh_ek._registry, source.id, op="get")

    # SQLite 行已写入
    rows = fresh_ek._registry.conn.execute(
        "SELECT entry_id, op FROM access_log WHERE entry_id = ?",
        (source.id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["op"] == "get"

    # jsonl 文件存在且可逐行解析
    access_dir = ek_root / "logs" / "access"
    jsonl_files = sorted(access_dir.glob("*.jsonl"))
    assert len(jsonl_files) >= 1
    lines = jsonl_files[-1].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["entry_id"] == source.id
    assert record["op"] == "get"
    assert "accessed_at" in record


def test_record_access_many_writes_sqlite_and_jsonl(fresh_ek, ek_root: Path) -> None:
    src_a = fresh_ek.ingest("source", source_payload(title="批量A"))
    src_b = fresh_ek.ingest("source", source_payload(title="批量B"))
    _record_access_many(fresh_ek._registry, [src_a.id, src_b.id], op="search")

    rows = fresh_ek._registry.conn.execute(
        "SELECT entry_id FROM access_log ORDER BY entry_id",
    ).fetchall()
    assert len(rows) == 2

    access_dir = ek_root / "logs" / "access"
    jsonl_files = sorted(access_dir.glob("*.jsonl"))
    assert len(jsonl_files) >= 1
    lines = jsonl_files[-1].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_record_access_jsonl_failure_raises_storage_error(
    fresh_ek, ek_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = fresh_ek.ingest("source", source_payload(title="失败来源"))

    from ego_knowledge import metrics as _metrics_mod

    def _bad_append(*args, **kwargs):
        raise StorageError("写入 access log jsonl 失败: 磁盘满")

    monkeypatch.setattr(_metrics_mod, "_append_jsonl", _bad_append)

    with pytest.raises(StorageError, match="写入 access log jsonl 失败"):
        _record_access(fresh_ek._registry, source.id, op="get")


# ---------------------------------------------------------------------------
# 2.3  retrieval_heat 参数可配
# ---------------------------------------------------------------------------


def test_retrieval_heat_default_params(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="默认参数"))
    now = _dt.datetime.now(tz=_dt.UTC).replace(microsecond=0)
    fresh_ek._registry.conn.execute(
        "INSERT INTO access_log(entry_id, op, accessed_at) VALUES(?, ?, ?)",
        (source.id, "get", now.isoformat()),
    )
    fresh_ek._registry.commit()

    heat = compute_retrieval_heat(source.id, fresh_ek._registry)
    assert heat == pytest.approx(1.0, rel=1e-5)


def test_retrieval_heat_registry_meta_overrides_params(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="meta覆盖"))
    # 记录在 2 天前 → window_days=1 时被排除
    old_time = (_dt.datetime.now(tz=_dt.UTC) - _dt.timedelta(days=2)).replace(microsecond=0)
    fresh_ek._registry.conn.execute(
        "INSERT INTO access_log(entry_id, op, accessed_at) VALUES(?, ?, ?)",
        (source.id, "get", old_time.isoformat()),
    )
    fresh_ek._registry.commit()

    # 写入 registry_meta 配置：window_days=1（太窄，记录被排除）
    fresh_ek._registry.conn.execute(
        "INSERT INTO registry_meta(key, value) VALUES(?, ?)",
        ("retrieval_heat.window_days", "1"),
    )
    fresh_ek._registry.conn.execute(
        "INSERT INTO registry_meta(key, value) VALUES(?, ?)",
        ("retrieval_heat.half_life_days", "10"),
    )
    fresh_ek._registry.commit()

    heat = compute_retrieval_heat(source.id, fresh_ek._registry)
    assert heat == 0.0


def test_retrieval_heat_env_fallback(fresh_ek, monkeypatch: pytest.MonkeyPatch) -> None:
    source = fresh_ek.ingest("source", source_payload(title="env覆盖"))
    # 记录在 2 天前 → window_days=1 时被排除
    old_time = (_dt.datetime.now(tz=_dt.UTC) - _dt.timedelta(days=2)).replace(microsecond=0)
    fresh_ek._registry.conn.execute(
        "INSERT INTO access_log(entry_id, op, accessed_at) VALUES(?, ?, ?)",
        (source.id, "get", old_time.isoformat()),
    )
    fresh_ek._registry.commit()

    monkeypatch.setenv("EGOKNOWLEDGE_RETRIEVAL_HEAT_WINDOW_DAYS", "1")
    monkeypatch.setenv("EGOKNOWLEDGE_RETRIEVAL_HEAT_HALF_LIFE_DAYS", "10")

    heat = compute_retrieval_heat(source.id, fresh_ek._registry)
    assert heat == 0.0


def test_retrieval_heat_invalid_param_raises_validation_error(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="非法参数"))

    fresh_ek._registry.conn.execute(
        "INSERT INTO registry_meta(key, value) VALUES(?, ?)",
        ("retrieval_heat.window_days", "-1"),
    )
    fresh_ek._registry.commit()

    with pytest.raises(ValidationError, match="必须为正整数"):
        compute_retrieval_heat(source.id, fresh_ek._registry)
