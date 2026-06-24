"""Unit tests for archive snapshot validation safety gates.

Covers G1-G24 from the QA remediation matrix: load_snapshot() validation,
filter parsing, snapshot overwrite protection, path traversal, coverage check.
These are pure function tests — no database or network required.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ego_knowledge.paths import sha256_text_hex
from ego_knowledge.scripts.archive_dirty_concepts._dry_run import (
    _enforce_dirty_concept_filter,
    _ensure_snapshot_can_be_created,
    _parse_filter,
)
from ego_knowledge.scripts.archive_dirty_concepts._helpers import (
    ArchiveError,
    _assert_exact_coverage,
    _entry_path,
    _ids_sha256,
    load_snapshot,
)

# ---------------------------------------------------------------------------
# Helpers to build valid snapshot fixtures
# ---------------------------------------------------------------------------


def _make_entry(entry_id: str = "id-001") -> dict[str, object]:
    return {"record_type": "entry", "id": entry_id, "title": f"entry {entry_id}"}


def _json_line(value: dict[str, object]) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def _build_snapshot_text(entries: list[dict[str, object]]) -> str:
    """Build a valid snapshot text string (manifest + entry lines)."""
    entry_lines = "".join(_json_line(e) for e in entries)
    ids = [e["id"] for e in entries]
    manifest = {
        "record_type": "manifest",
        "format_version": 1,
        "entry_count": len(entries),
        "payload_sha256": sha256_text_hex(entry_lines),
        "entry_ids_sha256": _ids_sha256(ids),
    }
    manifest_line = _json_line(manifest)
    return manifest_line + entry_lines


def _write_valid_snapshot(path: Path, entries: list[dict[str, object]] | None = None) -> None:
    """Write a valid snapshot file + sidecar to disk."""
    if entries is None:
        entries = [_make_entry("id-001")]
    text = _build_snapshot_text(entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar.write_text(sha256_text_hex(text) + "\n", encoding="utf-8")


# ===========================================================================
# G1: snapshot file does not exist
# ===========================================================================


def test_load_snapshot_rejects_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.snapshot.jsonl"
    with pytest.raises(ArchiveError, match="快照不存在"):
        load_snapshot(missing)


# ===========================================================================
# G2: sidecar .sha256 does not exist
# ===========================================================================


def test_load_snapshot_rejects_missing_sidecar(tmp_path: Path) -> None:
    path = tmp_path / "test.snapshot.jsonl"
    entries = [_make_entry("id-001")]
    text = _build_snapshot_text(entries)
    path.write_text(text, encoding="utf-8")
    # Intentionally NOT writing the sidecar
    with pytest.raises(ArchiveError, match="sidecar 不存在"):
        load_snapshot(path)


# ===========================================================================
# G3: sidecar hash mismatch
# ===========================================================================


def test_load_snapshot_rejects_bad_sidecar_hash(tmp_path: Path) -> None:
    path = tmp_path / "bad_sidecar.snapshot.jsonl"
    text = _build_snapshot_text([_make_entry("id-001")])
    path.write_text(text, encoding="utf-8")
    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar.write_text("0" * 64 + "\n", encoding="utf-8")
    with pytest.raises(ArchiveError, match="sidecar 校验失败"):
        load_snapshot(path)


# ===========================================================================
# G4: snapshot is empty (0 lines)
# ===========================================================================


def test_load_snapshot_rejects_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.snapshot.jsonl"
    path.write_text("", encoding="utf-8")
    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar.write_text(sha256_text_hex("") + "\n", encoding="utf-8")
    with pytest.raises(ArchiveError, match="快照为空"):
        load_snapshot(path)


# ===========================================================================
# G5: snapshot contains blank lines
# ===========================================================================


def test_load_snapshot_rejects_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "blank.snapshot.jsonl"
    text = _build_snapshot_text([_make_entry("id-001")])
    # Insert a blank line between manifest and entry
    lines = text.splitlines(keepends=True)
    corrupted = lines[0] + "\n" + lines[1]
    path.write_text(corrupted, encoding="utf-8")
    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar.write_text(sha256_text_hex(corrupted) + "\n", encoding="utf-8")
    with pytest.raises(ArchiveError, match="含空行"):
        load_snapshot(path)


# ===========================================================================
# G6: manifest record_type != "manifest"
# ===========================================================================


def test_load_snapshot_rejects_bad_manifest_record_type(tmp_path: Path) -> None:
    path = tmp_path / "bad_manifest.snapshot.jsonl"
    entry = _make_entry("id-001")
    entry_line = _json_line(entry)
    manifest = {
        "record_type": "entry",  # wrong
        "format_version": 1,
        "entry_count": 1,
        "payload_sha256": sha256_text_hex(entry_line),
        "entry_ids_sha256": _ids_sha256(["id-001"]),
    }
    manifest_line = _json_line(manifest)
    text = manifest_line + entry_line
    path.write_text(text, encoding="utf-8")
    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar.write_text(sha256_text_hex(text) + "\n", encoding="utf-8")
    with pytest.raises(ArchiveError, match="首行必须是 manifest"):
        load_snapshot(path)


# ===========================================================================
# G7: payload_sha256 mismatch (tampered entry content)
# ===========================================================================


def test_load_snapshot_rejects_tampered_payload(tmp_path: Path) -> None:
    path = tmp_path / "tampered.snapshot.jsonl"
    text = _build_snapshot_text([_make_entry("id-001")])
    path.write_text(text, encoding="utf-8")
    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar.write_text(sha256_text_hex(text) + "\n", encoding="utf-8")
    # Tamper entry content — must also update sidecar to pass layer-1 check
    lines = text.splitlines(keepends=True)
    tampered_entry = _json_line(
        {"record_type": "entry", "id": "id-001", "title": "TAMPERED"},
    )
    tampered = lines[0] + tampered_entry
    path.write_text(tampered, encoding="utf-8")
    # Update sidecar to match tampered file so we reach payload_sha256 check
    sidecar.write_text(sha256_text_hex(tampered) + "\n", encoding="utf-8")
    with pytest.raises(ArchiveError, match="payload sha256 校验失败"):
        load_snapshot(path)


# ===========================================================================
# G8: entry record_type != "entry"
# ===========================================================================


def test_load_snapshot_rejects_bad_entry_record_type(tmp_path: Path) -> None:
    path = tmp_path / "bad_entry.snapshot.jsonl"
    bad_entry = {"record_type": "other", "id": "id-001", "title": "bad"}
    entry_line = _json_line(bad_entry)
    manifest = {
        "record_type": "manifest",
        "format_version": 1,
        "entry_count": 1,
        "payload_sha256": sha256_text_hex(entry_line),
        "entry_ids_sha256": _ids_sha256(["id-001"]),
    }
    manifest_line = _json_line(manifest)
    text = manifest_line + entry_line
    path.write_text(text, encoding="utf-8")
    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar.write_text(sha256_text_hex(text) + "\n", encoding="utf-8")
    with pytest.raises(ArchiveError, match="record_type=entry"):
        load_snapshot(path)


# ===========================================================================
# G9: duplicate entry IDs
# ===========================================================================


def test_load_snapshot_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "dup.snapshot.jsonl"
    _write_valid_snapshot(path, [_make_entry("id-001"), _make_entry("id-001")])
    with pytest.raises(ArchiveError, match="重复 ID"):
        load_snapshot(path)


# ===========================================================================
# G10: entry_count mismatch
# ===========================================================================


def test_load_snapshot_rejects_entry_count_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "count_mismatch.snapshot.jsonl"
    entries = [_make_entry("id-001"), _make_entry("id-002")]
    entry_lines = "".join(_json_line(e) for e in entries)
    ids = [e["id"] for e in entries]
    manifest = {
        "record_type": "manifest",
        "format_version": 1,
        "entry_count": 5,  # wrong — actual count is 2
        "payload_sha256": sha256_text_hex(entry_lines),
        "entry_ids_sha256": _ids_sha256(ids),
    }
    manifest_line = _json_line(manifest)
    text = manifest_line + entry_lines
    path.write_text(text, encoding="utf-8")
    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar.write_text(sha256_text_hex(text) + "\n", encoding="utf-8")
    with pytest.raises(ArchiveError, match="entry_count"):
        load_snapshot(path)


# ===========================================================================
# G11: entry_ids_sha256 mismatch (tampered entry IDs)
# ===========================================================================


def test_load_snapshot_rejects_ids_sha256_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "ids_sha256.snapshot.jsonl"
    entries = [_make_entry("id-001")]
    entry_lines = "".join(_json_line(e) for e in entries)
    manifest = {
        "record_type": "manifest",
        "format_version": 1,
        "entry_count": 1,
        "payload_sha256": sha256_text_hex(entry_lines),
        "entry_ids_sha256": "0" * 64,  # wrong
    }
    manifest_line = _json_line(manifest)
    text = manifest_line + entry_lines
    path.write_text(text, encoding="utf-8")
    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar.write_text(sha256_text_hex(text) + "\n", encoding="utf-8")
    with pytest.raises(ArchiveError, match="ID 集 sha256 校验失败"):
        load_snapshot(path)


# ===========================================================================
# G12: unsupported filter clause syntax
# ===========================================================================


def test_parse_filter_rejects_unsupported_syntax() -> None:
    with pytest.raises(ArchiveError, match="不支持的 filter 子句"):
        _parse_filter("kind concept")


# ===========================================================================
# G13: filter column not in whitelist
# ===========================================================================


def test_parse_filter_rejects_non_whitelisted_column() -> None:
    with pytest.raises(ArchiveError, match="字段不在白名单"):
        _parse_filter("password=x")


# ===========================================================================
# G14: filter missing kind=concept
# ===========================================================================


def test_enforce_dirty_concept_filter_requires_kind_concept() -> None:
    clauses = [("status", "!=", "archived")]
    with pytest.raises(ArchiveError, match="kind=concept"):
        _enforce_dirty_concept_filter(clauses)


# ===========================================================================
# G15: filter missing status!=archived
# ===========================================================================


def test_enforce_dirty_concept_filter_requires_status_not_archived() -> None:
    clauses = [("kind", "=", "concept")]
    with pytest.raises(ArchiveError, match="status"):
        _enforce_dirty_concept_filter(clauses)


# ===========================================================================
# G21: snapshot already exists with execution data → block
# ===========================================================================


def test_ensure_snapshot_blocks_when_execution_has_data(tmp_path: Path) -> None:
    snapshot = tmp_path / "existing.snapshot.jsonl"
    snapshot.write_text('{"record_type":"manifest"}\n', encoding="utf-8")
    execution = snapshot.with_name("existing.execution.jsonl")
    execution.write_text('{"status":"started"}\n', encoding="utf-8")
    with pytest.raises(ArchiveError, match="execution.jsonl 有数据"):
        _ensure_snapshot_can_be_created(snapshot)


# ===========================================================================
# G22: snapshot already exists without execution → block
# ===========================================================================


def test_ensure_snapshot_blocks_existing_empty(tmp_path: Path) -> None:
    snapshot = tmp_path / "existing.snapshot.jsonl"
    snapshot.write_text("some data", encoding="utf-8")
    # No execution file, but snapshot exists → should block
    with pytest.raises(ArchiveError, match="拒绝覆盖"):
        _ensure_snapshot_can_be_created(snapshot)


# ===========================================================================
# G23: entry file_path escapes data_root
# ===========================================================================


def test_entry_path_rejects_path_traversal() -> None:
    with pytest.raises(ArchiveError, match="越过 data-root"):
        _entry_path(Path("/data"), "../../etc/passwd")


# ===========================================================================
# G24: coverage reconciliation failure (missing/extra IDs)
# ===========================================================================


def test_assert_exact_coverage_rejects_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "cov.snapshot.jsonl"
    _write_valid_snapshot(path, [_make_entry("id-001")])
    snapshot = load_snapshot(path)
    # Pass wrong success_ids — extra and missing
    with pytest.raises(ArchiveError, match="覆盖率对账失败"):
        _assert_exact_coverage(snapshot, {"id-999"}, label="test")


def test_assert_exact_coverage_rejects_missing_ids(tmp_path: Path) -> None:
    path = tmp_path / "cov2.snapshot.jsonl"
    _write_valid_snapshot(path, [_make_entry("id-001")])
    snapshot = load_snapshot(path)
    # Empty success_ids → missing id-001
    with pytest.raises(ArchiveError, match="覆盖率对账失败"):
        _assert_exact_coverage(snapshot, set(), label="test")


def test_assert_exact_coverage_passes_on_match(tmp_path: Path) -> None:
    path = tmp_path / "cov3.snapshot.jsonl"
    _write_valid_snapshot(path, [_make_entry("id-001")])
    snapshot = load_snapshot(path)
    # Should not raise
    _assert_exact_coverage(snapshot, {"id-001"}, label="test")
