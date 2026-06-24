from __future__ import annotations

import logging
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from ego_knowledge._validation import MAX_UPDATE_BODY_BYTES
from ego_knowledge.core import EgoKnowledge
from ego_knowledge.errors import (
    BodyBatchNotSupported,
    BodyFrontmatterMismatch,
    BodyInvalidUTF8,
    BodyLengthAboveMax,
    BodyLengthBelowMin,
    BodyRecoveryFailedSnapshotMissing,
    StorageError,
)
from ego_knowledge.frontmatter import read_file
from ego_knowledge.transactions import transactional_write
from tests.unit.support import source_payload


class CommitFailingConnection:
    def __init__(self, conn: sqlite3.Connection, exc: Exception | None = None) -> None:
        self._conn = conn
        self._exc = exc or sqlite3.OperationalError("boom after rename")

    def cursor(self) -> sqlite3.Cursor:
        return self._conn.cursor()

    def rollback(self) -> None:
        self._conn.rollback()

    def commit(self) -> None:
        raise self._exc


def _entry_path(data_root: Path, file_path: str | None) -> Path:
    assert file_path is not None
    return data_root / file_path


def _seed_source(ek: EgoKnowledge, title: str = "body 写入来源"):
    return ek.ingest(
        "source",
        source_payload(
            title=title,
            body="原始正文",
            search_terms=[title, "body write", "正文写入", "body", "alias-body"],
        ),
    )


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE demo(value TEXT)")
    return conn


def test_body_update_happy_path(fresh_ek: EgoKnowledge, ek_root: Path) -> None:
    entry = _seed_source(fresh_ek)
    new_body = "更新后的正文\n\n保留 Markdown 语义。"
    expected_body = "更新后的正文\n\n保留 Markdown 语义。\n"

    updated = fresh_ek.update(entry.id, {"body": new_body})
    fetched = fresh_ek.get(entry.id)
    _, file_body = read_file(str(_entry_path(ek_root, updated.file_path)))

    assert updated.id == entry.id
    assert fetched.body == expected_body
    assert file_body == expected_body


def test_update_without_body_keeps_existing_behavior(
    fresh_ek: EgoKnowledge,
    ek_root: Path,
) -> None:
    entry = _seed_source(fresh_ek, title="旧标题 body 保持")
    old_path = _entry_path(ek_root, entry.file_path)

    updated = fresh_ek.update(entry.id, {"title": "新标题 body 保持"})
    fetched = fresh_ek.get(entry.id)

    assert updated.file_path != entry.file_path
    assert not old_path.exists()
    assert fetched.body == entry.body


def test_body_invalid_utf8_bytes_rejected(fresh_ek: EgoKnowledge) -> None:
    entry = _seed_source(fresh_ek)

    with pytest.raises(BodyInvalidUTF8) as exc_info:
        fresh_ek.update(entry.id, {"body": b"\xff"})

    assert exc_info.value.details["error_code"] == "body_invalid_utf8"


def test_body_non_text_rejected(fresh_ek: EgoKnowledge) -> None:
    entry = _seed_source(fresh_ek)

    with pytest.raises(BodyInvalidUTF8):
        fresh_ek.update(entry.id, {"body": {"not": "text"}})


def test_body_length_below_min_rejected(fresh_ek: EgoKnowledge) -> None:
    entry = _seed_source(fresh_ek)

    with pytest.raises(BodyLengthBelowMin) as exc_info:
        fresh_ek.update(entry.id, {"body": ""})

    assert exc_info.value.details["minimum"] == 1


def test_body_length_above_max_rejected(fresh_ek: EgoKnowledge) -> None:
    entry = _seed_source(fresh_ek)

    with pytest.raises(BodyLengthAboveMax) as exc_info:
        fresh_ek.update(entry.id, {"body": "x" * (MAX_UPDATE_BODY_BYTES + 1)})

    assert exc_info.value.details["maximum"] == MAX_UPDATE_BODY_BYTES


def test_body_is_nfc_normalized(fresh_ek: EgoKnowledge) -> None:
    entry = _seed_source(fresh_ek)

    updated = fresh_ek.update(entry.id, {"body": "Cafe\u0301"})

    assert updated.body == "Café\n"
    assert fresh_ek.get(entry.id).body == "Café\n"


def test_body_with_consistent_frontmatter_is_accepted(
    fresh_ek: EgoKnowledge,
    ek_root: Path,
) -> None:
    entry = _seed_source(fresh_ek)
    current = _entry_path(ek_root, entry.file_path).read_text(encoding="utf-8")
    _, frontmatter_raw, _ = current.split("---\n", 2)

    updated = fresh_ek.update(entry.id, {"body": f"---\n{frontmatter_raw}---\n\n仅正文被写入"})

    assert updated.body == "仅正文被写入\n"
    assert fresh_ek.get(entry.id).body == "仅正文被写入\n"


def test_body_frontmatter_mismatch_rejected(fresh_ek: EgoKnowledge) -> None:
    entry = _seed_source(fresh_ek)
    mismatched_body = "---\ntitle: 另一个标题\n---\n\n正文"

    with pytest.raises(BodyFrontmatterMismatch) as exc_info:
        fresh_ek.update(entry.id, {"body": mismatched_body})

    assert exc_info.value.details["error_code"] == "body_frontmatter_mismatch"


def test_body_batch_list_rejected(fresh_ek: EgoKnowledge) -> None:
    entry = _seed_source(fresh_ek)

    with pytest.raises(BodyBatchNotSupported):
        fresh_ek.update(entry.id, {"body": ["正文一", "正文二"]})


def test_body_batch_keys_rejected(fresh_ek: EgoKnowledge) -> None:
    entry = _seed_source(fresh_ek)

    with pytest.raises(BodyBatchNotSupported) as exc_info:
        fresh_ek.update(entry.id, {"body": "正文", "ids": [entry.id]})

    assert exc_info.value.details["error_code"] == "body_batch_not_supported"


def test_body_batch_id_rejected_before_lookup(fresh_ek: EgoKnowledge) -> None:
    _seed_source(fresh_ek)

    with pytest.raises(BodyBatchNotSupported):
        fresh_ek.update(["ek_src_a", "ek_src_b"], {"body": "正文"})  # type: ignore[arg-type]


def test_body_with_path_changing_title_rejected(
    fresh_ek: EgoKnowledge,
    ek_root: Path,
) -> None:
    entry = _seed_source(fresh_ek, title="body path old")
    old_path = _entry_path(ek_root, entry.file_path)

    with pytest.raises(BodyBatchNotSupported) as exc_info:
        fresh_ek.update(
            entry.id,
            {
                "title": "body path new",
                "body": "正文",
                "search_terms": ["body path new", "body", "path", "路径", "alias-body-path"],
            },
        )

    assert exc_info.value.details["error_code"] == "body_batch_not_supported"
    assert old_path.exists()
    assert fresh_ek.get(entry.id).title == "body path old"


def test_body_no_sanitization_preserves_markdown_html(fresh_ek: EgoKnowledge) -> None:
    entry = _seed_source(fresh_ek)
    body = "# 标题\n\n<script>alert('local')</script>\n[相对链接](./a.md)"
    expected_body = "# 标题\n\n<script>alert('local')</script>\n\n[相对链接](./a.md)\n"

    updated = fresh_ek.update(entry.id, {"body": body})

    assert updated.body == expected_body
    assert fresh_ek.get(entry.id).body == expected_body


def test_body_snapshot_manifest_roundtrip(
    fresh_ek: EgoKnowledge,
    ek_root: Path,
) -> None:
    entry = _seed_source(fresh_ek)

    fresh_ek.update(entry.id, {"body": "触发快照 manifest"})

    snapshot_roots = sorted((ek_root / ".txn-snapshots").iterdir())
    assert snapshot_roots
    manifest = snapshot_roots[-1] / "manifest.json"
    assert oct(snapshot_roots[-1].stat().st_mode & 0o777) == "0o700"
    assert oct(manifest.stat().st_mode & 0o777) == "0o600"
    assert entry.id.encode("utf-8").hex() not in manifest.read_text(encoding="utf-8")


def test_body_transaction_split_commit_fail_after_rename(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    (data_root / "registry").mkdir(parents=True)
    target_path = data_root / "entries" / "entry.md"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("旧内容", encoding="utf-8")
    conn = _memory_conn()

    with pytest.raises(StorageError, match="已按快照恢复"):
        with transactional_write(
            target_path,
            "新内容",
            CommitFailingConnection(conn),
            snapshot_entry_id="ek_src_split",
        ) as cursor:
            cursor.execute("INSERT INTO demo(value) VALUES (?)", ("written",))

    assert target_path.read_text(encoding="utf-8") == "旧内容"
    assert conn.execute("SELECT COUNT(*) FROM demo").fetchone()[0] == 0
    assert list((data_root / ".txn-snapshots").glob("*/manifest.json"))


def test_body_transaction_split_database_error_after_rename(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    (data_root / "registry").mkdir(parents=True)
    target_path = data_root / "entries" / "entry.md"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("旧内容", encoding="utf-8")
    conn = _memory_conn()

    with pytest.raises(StorageError, match="已按快照恢复"):
        with transactional_write(
            target_path,
            "新内容",
            CommitFailingConnection(conn, sqlite3.DatabaseError("database closed after rename")),
            snapshot_entry_id="ek_src_split_database",
        ) as cursor:
            cursor.execute("INSERT INTO demo(value) VALUES (?)", ("written",))

    assert target_path.read_text(encoding="utf-8") == "旧内容"
    assert conn.execute("SELECT COUNT(*) FROM demo").fetchone()[0] == 0


def test_body_recovery_log_failure_does_not_block_snapshot_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    (data_root / "registry").mkdir(parents=True)
    target_path = data_root / "entries" / "entry.md"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("旧内容", encoding="utf-8")
    conn = _memory_conn()

    def fail_log(*_args: object, **_kwargs: object) -> None:
        raise OSError("log unavailable")

    monkeypatch.setattr("ego_knowledge.transactions._write_recovery_log", fail_log)

    with pytest.raises(StorageError, match="已按快照恢复"):
        with transactional_write(
            target_path,
            "新内容",
            CommitFailingConnection(conn),
            snapshot_entry_id="ek_src_log_failed",
        ) as cursor:
            cursor.execute("INSERT INTO demo(value) VALUES (?)", ("written",))

    assert target_path.read_text(encoding="utf-8") == "旧内容"
    assert conn.execute("SELECT COUNT(*) FROM demo").fetchone()[0] == 0


def test_body_recovery_failure_writes_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    (data_root / "registry").mkdir(parents=True)
    target_path = data_root / "entries" / "entry.md"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("旧内容", encoding="utf-8")
    original_copy2 = __import__("shutil").copy2
    calls = 0

    def flaky_copy2(src: Path, dst: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_copy2(src, dst)
        raise OSError("snapshot lost")

    monkeypatch.setattr("ego_knowledge.transactions.shutil.copy2", flaky_copy2)

    with pytest.raises(BodyRecoveryFailedSnapshotMissing) as exc_info:
        with transactional_write(
            target_path,
            "新内容",
            CommitFailingConnection(_memory_conn()),
            snapshot_entry_id="ek_src_missing",
        ):
            pass

    assert exc_info.value.details["error_code"] == "body_recovery_failed_snapshot_missing"
    recovery_log = data_root / "logs" / "refresh" / "recovery.log"
    assert recovery_log.exists()
    assert oct(recovery_log.stat().st_mode & 0o777) == "0o600"
    assert list((data_root / ".txn-snapshots").glob("*/recovery.log"))


def test_body_post_transaction_steps_warn_without_bubbling(
    fresh_ek: EgoKnowledge,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    entry = _seed_source(fresh_ek)

    def fail_step(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("post step boom")

    monkeypatch.setattr("ego_knowledge._entry_store._recompute_ids", fail_step)
    monkeypatch.setattr("ego_knowledge._entry_store.enqueue_local_findings", fail_step)
    monkeypatch.setattr("ego_knowledge.core.enqueue_dense_embedding", fail_step)

    with caplog.at_level(logging.WARNING):
        updated = fresh_ek.update(entry.id, {"body": "post tx body"})

    assert updated.body == "post tx body\n"
    warning_events = [record.__dict__.get("event") for record in caplog.records]
    assert "metrics_recompute_failed" in warning_events
    assert "local_findings_failed" in warning_events
    assert "dense_enqueue_failed" in warning_events

    # i-a3 收紧：3 个 post-commit step 都失败时，post_commit_errors 必须覆盖全部 3 条
    # （验证 w-a1 修复后 dense_enqueue 已纳入统一队列，调用方可通过公共 API 感知）。
    errors = fresh_ek.post_commit_errors
    assert {e.label for e in errors} == {
        "metrics_recompute_failed",
        "local_findings_failed",
        "dense_enqueue_failed",
    }
    assert all(e.entry_id == entry.id for e in errors)
    assert all(isinstance(e.exception, RuntimeError) for e in errors)


def test_post_commit_step_failure_surfaces_to_caller(
    fresh_ek: EgoKnowledge,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """w1: post-commit step 失败必须能让调用方可查询（玻璃盒「失败必须能被外部看见」）。

    场景：mock _recompute_ids 抛异常 → update 仍返回（事务已提交），
    调用方可通过 EgoKnowledge.post_commit_errors 公共 property 感知失败，并能区分步骤 label。
    """
    from ego_knowledge.core import PostCommitError

    entry = _seed_source(fresh_ek)

    def boom_recompute(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("metrics boom")

    monkeypatch.setattr("ego_knowledge._entry_store._recompute_ids", boom_recompute)

    with caplog.at_level(logging.WARNING):
        updated = fresh_ek.update(entry.id, {"body": "新正文内容"})

    # 事务已提交，update 不应抛出
    assert updated.body == "新正文内容\n"

    # w1 核心 + w-a2：调用方通过 EgoKnowledge facade 的公共 property 感知失败
    errors = fresh_ek.post_commit_errors
    assert len(errors) == 1
    assert isinstance(errors[0], PostCommitError)
    assert errors[0].label == "metrics_recompute_failed"
    assert errors[0].entry_id == entry.id
    assert isinstance(errors[0].exception, RuntimeError)
    assert "metrics boom" in str(errors[0].exception)

    # w2 核心：日志 message 带 label，可直接 grep 命中
    matching = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "metrics_recompute_failed 失败" in record.message
    ]
    assert matching, "warning 日志 message 应含 label 以便 grep 命中"


def test_dense_enqueue_failure_surfaces_via_facade(
    fresh_ek: EgoKnowledge,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """w-a1 + w-a2 闭环：facade 层 dense_enqueue 失败也进 post_commit_errors 队列。

    场景：mock core.enqueue_dense_embedding 抛异常 → 仅 dense_enqueue 失败，
    验证：(1) 队列含 1 条 label="dense_enqueue_failed"；
          (2) EntryStore 内 2 个 step 未失败（label 不含 metrics/local）；
          (3) 日志 message 走统一 f"{label} 失败" 风格（w-a3）。
    """
    from ego_knowledge.core import PostCommitError

    entry = _seed_source(fresh_ek)

    def boom_enqueue(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("dense enqueue boom")

    monkeypatch.setattr("ego_knowledge.core.enqueue_dense_embedding", boom_enqueue)

    with caplog.at_level(logging.WARNING):
        updated = fresh_ek.update(entry.id, {"body": "触发 dense enqueue 失败"})

    # 事务已提交，update 不应抛出
    assert updated.body == "触发 dense enqueue 失败\n"

    # w-a1：dense_enqueue 失败进同一队列
    errors = fresh_ek.post_commit_errors
    assert len(errors) == 1
    assert isinstance(errors[0], PostCommitError)
    assert errors[0].label == "dense_enqueue_failed"
    assert errors[0].entry_id == entry.id
    assert isinstance(errors[0].exception, RuntimeError)
    assert "dense enqueue boom" in str(errors[0].exception)

    # w-a3：日志 message 统一 f"{label} 失败" 风格，可直接 grep 命中
    matching = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "dense_enqueue_failed 失败" in record.message
    ]
    assert matching, (
        "dense_enqueue 日志 message 应含 label 以便 grep 命中（与 _entry_store 风格统一）"
    )


def test_post_commit_errors_reset_per_update(fresh_ek: EgoKnowledge) -> None:
    """post_commit_errors 每次 update 开头清空，不跨操作累积。"""
    entry = _seed_source(fresh_ek)

    # 第一次 update 正常，errors 应为空
    fresh_ek.update(entry.id, {"body": "第一次正文"})
    assert fresh_ek.post_commit_errors == []

    # 第二次 update 也正常，仍为空（验证 clear 不残留）
    fresh_ek.update(entry.id, {"body": "第二次正文"})
    assert fresh_ek.post_commit_errors == []


def test_txn_snapshots_gitignore_guard() -> None:
    ek_root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        ["git", "check-ignore", "-q", ".txn-snapshots/probe.md"],
        cwd=ek_root,
        check=False,
    )

    assert result.returncode == 0


def test_body_n13_how_to_documents_stop_points() -> None:
    doc_path = Path(__file__).resolve().parents[2] / "docs" / "how-to" / "body-update-standard.md"
    content = doc_path.read_text(encoding="utf-8")

    # 命令锚点：commit 31367f015 已把内部 agent 名 `ops-manage-knowledge` 脱敏为通用
    # "调用方" 描述，文档当前以 `ek_update` 作为唯一 body 写入命令名（出现于适用边界
    # 与失败信号两节）。断言改为 `ek_update`，与文档事实同步。
    assert "ek_update" in content
    assert "富文本停点" in content
    assert "授权停点" in content
    assert "外流停点" in content
    assert "共享停点" in content
    assert "body_batch_not_supported" in content


def test_body_reference_docs_capture_recovery_and_snippet_boundaries() -> None:
    ego_root = Path(__file__).resolve().parents[2]
    error_types = (ego_root / "docs" / "reference" / "error-types.md").read_text(encoding="utf-8")
    transactions = (ego_root / "docs" / "explanation" / "transactions.md").read_text(
        encoding="utf-8"
    )
    search_contract = (ego_root / "docs" / "reference" / "search-contract.md").read_text(
        encoding="utf-8"
    )

    assert "body_recovery_failed_snapshot_missing" in error_types
    assert "body_batch_not_supported" in error_types
    assert "file_path/body/metrics" not in error_types
    assert ".txn-snapshots" in transactions
    assert "0600" in transactions
    assert "body_recovery_failed_snapshot_missing" in transactions
    assert "raw text" in search_contract
    assert "纯文本化或转义" in search_contract


def test_body_benchmark_latency(fresh_ek: EgoKnowledge) -> None:
    entry = _seed_source(fresh_ek, title="body benchmark")

    started = time.perf_counter()
    updated = fresh_ek.update(entry.id, {"body": "benchmark body"})
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert updated.body == "benchmark body\n"
    assert elapsed_ms <= 100
