from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ego_knowledge.errors import StorageError
from ego_knowledge.transactions import _find_data_root, _write_tmp_file, transactional_write


class _BeginFailCursor:
    def execute(self, statement: str) -> None:
        if statement == "BEGIN IMMEDIATE":
            raise sqlite3.OperationalError("database is locked")

    def close(self) -> None:
        return None


class _BeginFailConnection:
    def cursor(self) -> _BeginFailCursor:
        return _BeginFailCursor()

    def rollback(self) -> None:
        return None

    def commit(self) -> None:
        return None


def test_find_data_root_falls_back_without_registry_dir(tmp_path: Path) -> None:
    target_path = tmp_path / "entries" / "note.md"

    assert _find_data_root(target_path) == tmp_path


def test_write_tmp_file_cleans_partial_file_on_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_file = tmp_path / "entry.md.tmp"

    def fail_fsync(fd: int) -> None:
        del fd
        raise OSError("fsync failed")

    monkeypatch.setattr("ego_knowledge.transactions.os.fsync", fail_fsync)

    with pytest.raises(StorageError, match="临时文件写入失败"):
        _write_tmp_file(tmp_file, "新内容")

    assert not tmp_file.exists()


def test_transactional_write_reports_begin_immediate_failure(tmp_path: Path) -> None:
    target_path = tmp_path / "entry.md"

    with pytest.raises(StorageError, match="无法获取 SQLite 写锁"):
        with transactional_write(target_path, "新内容", _BeginFailConnection()):  # type: ignore[arg-type]
            pass

    assert not target_path.exists()
    assert not target_path.with_suffix(".md.tmp").exists()


def test_transactional_write_wraps_unexpected_exception(tmp_path: Path) -> None:
    target_path = tmp_path / "entry.md"
    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(StorageError, match="事务写入失败"):
            with transactional_write(target_path, "新内容", conn):
                raise RuntimeError("boom")
    finally:
        conn.close()

    assert not target_path.exists()
    assert not target_path.with_suffix(".md.tmp").exists()
