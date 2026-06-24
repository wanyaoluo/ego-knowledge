from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ego_knowledge.errors import StorageError
from ego_knowledge.transactions import transactional_write


class CommitFailingConnection:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def cursor(self) -> sqlite3.Cursor:
        return self._conn.cursor()

    def rollback(self) -> None:
        self._conn.rollback()

    def commit(self) -> None:
        raise sqlite3.OperationalError("boom")


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE demo(value TEXT)")
    return conn


def test_transactional_write_rename_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    (data_root / "registry").mkdir(parents=True)
    target_path = data_root / "entries" / "entry.md"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("旧内容", encoding="utf-8")
    conn = _memory_conn()

    def _raise_rename_error(src: Path, dst: Path) -> None:
        del src, dst
        raise OSError("rename failed")

    monkeypatch.setattr("ego_knowledge.transactions.os.rename", _raise_rename_error)

    with pytest.raises(StorageError, match="rename 失败"):
        with transactional_write(target_path, "新内容", conn) as cursor:
            cursor.execute("INSERT INTO demo(value) VALUES (?)", ("written",))

    row = conn.execute("SELECT COUNT(*) AS total FROM demo").fetchone()
    assert row is not None
    assert row[0] == 0
    assert target_path.read_text(encoding="utf-8") == "旧内容"
    assert not target_path.with_suffix(".md.tmp").exists()


def test_transactional_write_commit_failure_writes_recovery_log(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    (data_root / "registry").mkdir(parents=True)
    target_path = data_root / "entries" / "entry.md"
    conn = _memory_conn()
    wrapped_conn = CommitFailingConnection(conn)

    with pytest.raises(StorageError, match="doctor --repair"):
        with transactional_write(target_path, "新内容", wrapped_conn) as cursor:
            cursor.execute("INSERT INTO demo(value) VALUES (?)", ("written",))

    row = conn.execute("SELECT COUNT(*) AS total FROM demo").fetchone()
    assert row is not None
    assert row[0] == 0
    assert target_path.read_text(encoding="utf-8") == "新内容"
    assert not target_path.with_suffix(".md.tmp").exists()

    log_file = data_root / "logs" / "refresh" / "recovery.log"
    assert log_file.exists()
    record = json.loads(log_file.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["target_path"] == str(target_path)
    assert "COMMIT 失败" in record["message"]
