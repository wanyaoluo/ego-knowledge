"""Unit tests for ego_knowledge.paths — slug 冲突拒绝护栏（Phase 1 T1.1）。

补充覆盖 ``resolve_data_root`` 优先级链与 ``sha256_text_hex`` / ``sha256_text_digest``
语义边界（治理 finding 修复：消除 archive_dirty_concepts 与 normalize_legacy
对 data_root 解析和 text sha256 的重复定义）。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ego_knowledge.errors import ConflictError
from ego_knowledge.models import ConceptEntry, Freshness, Kind, Status
from ego_knowledge.paths import (
    allocate_unique_path,
    default_data_root,
    resolve_data_root,
    sha256_text_digest,
    sha256_text_hex,
)
from ego_knowledge.registry import Registry

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_concept(
    *,
    slug: str,
    entry_id: str | None = None,
    domain: str | None = None,
) -> ConceptEntry:
    """快速构造一个 ConceptEntry，用于路径分配测试。"""
    if entry_id is None:
        import ulid

        u = ulid.new()
        entry_id = f"ek_con_{u}"
    return ConceptEntry(
        id=entry_id,
        kind=Kind.CONCEPT,
        title=slug,
        slug=slug,
        status=Status.DRAFT,
        freshness=Freshness.STABLE,
        schema_version="2.0",
        created_at=date(2026, 1, 1),
        updated_at=date(2026, 1, 1),
        domain=domain,
    )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_data_root(tmp_path: Path) -> Path:
    """创建最小化的 data_root 目录结构。"""
    root = tmp_path / "data"
    (root / "entries" / "concepts" / "_unsorted").mkdir(parents=True)
    return root


@pytest.fixture()
def registry(tmp_path: Path) -> Registry:
    """创建内存级 Registry（db 放在临时目录）。"""
    db_path = tmp_path / "catalog.sqlite"
    reg = Registry(db_path)
    reg.init_schema()
    return reg


# ---------------------------------------------------------------------------
# T1.1 测试用例
# ---------------------------------------------------------------------------


class TestAllocateUniquePathConflictGuard:
    """Phase 1 护栏：ingest 路径撞车直接抛 ConflictError。"""

    def test_ingest_collision_raises_conflict(
        self, tmp_data_root: Path, registry: Registry
    ) -> None:
        """ingest 路径（current_id=None）下 base path 冲突应直接抛 ConflictError。"""
        # 预占路径
        existing = _make_concept(slug="同名")
        existing_path = tmp_data_root / "entries" / "concepts" / "_unsorted" / "同名.md"
        existing_path.parent.mkdir(parents=True, exist_ok=True)
        existing_path.write_text("placeholder")
        registry.upsert_entry(existing, existing_path, body="...")

        # 新条目同 slug，ingest 路径
        new_entry = _make_concept(slug="同名")
        with pytest.raises(ConflictError, match="slug 冲突"):
            allocate_unique_path(tmp_data_root, registry, new_entry, current_id=None)

    def test_update_collision_keeps_suffix_fallback(
        self, tmp_data_root: Path, registry: Registry
    ) -> None:
        """update 路径（current_id≠None）下 base path 冲突仍走后缀 fallback。"""
        # 预占路径
        existing = _make_concept(slug="同名", entry_id="ek_con_AAAA_existing")
        existing_path = tmp_data_root / "entries" / "concepts" / "_unsorted" / "同名.md"
        existing_path.parent.mkdir(parents=True, exist_ok=True)
        existing_path.write_text("placeholder")
        registry.upsert_entry(existing, existing_path, body="...")

        # 当前条目同 slug，update 路径
        current_id = "ek_con_01YYYYYYYYY"
        current_entry = _make_concept(slug="同名", entry_id=current_id)
        result = allocate_unique_path(tmp_data_root, registry, current_entry, current_id=current_id)
        # 后缀取 entry.id 最后一段的前 6 位：01YYYY → 文件名 "同名-01YYYY.md"
        assert result.stem == "同名-01YYYY"

    def test_update_suffix_also_taken_raises_conflict(
        self, tmp_data_root: Path, registry: Registry
    ) -> None:
        """update 路径下 base + 后缀都被占 → 抛 ConflictError。"""
        current_id = "ek_con_01YYYYYYYYY"

        # 预占 base 路径
        existing_base = _make_concept(slug="同名", entry_id="ek_con_AAAA_base")
        base_path = tmp_data_root / "entries" / "concepts" / "_unsorted" / "同名.md"
        base_path.parent.mkdir(parents=True, exist_ok=True)
        base_path.write_text("placeholder")
        registry.upsert_entry(existing_base, base_path, body="...")

        # 预占后缀路径：同名-01YYYY.md
        existing_suffix = _make_concept(slug="同名-01YYYY", entry_id="ek_con_BBBB_suffix")
        suffix_path = tmp_data_root / "entries" / "concepts" / "_unsorted" / "同名-01YYYY.md"
        suffix_path.write_text("placeholder")
        registry.upsert_entry(existing_suffix, suffix_path, body="...")

        # 两条路都被堵死，应抛 ConflictError
        current_entry = _make_concept(slug="同名", entry_id=current_id)
        with pytest.raises(ConflictError, match="slug 冲突"):
            allocate_unique_path(tmp_data_root, registry, current_entry, current_id=current_id)


# ---------------------------------------------------------------------------
# resolve_data_root：scripts 子包共享的数据根优先级链
# ---------------------------------------------------------------------------
# 覆盖 finding A：archive_dirty_concepts 与 normalize_legacy 历史上有两套
# 同语义优先级链（explicit > EGOKNOWLEDGE_DATA_ROOT > EK_DATA_ROOT > default），
# 现已下沉到 paths.resolve_data_root 真源。下列测试锁定真源行为契约。


class TestResolveDataRoot:
    """优先级链：explicit > EGOKNOWLEDGE_DATA_ROOT > EK_DATA_ROOT > default。"""

    def test_explicit_wins_over_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """显式参数优先于任何环境变量。"""

        monkeypatch.setenv("EGOKNOWLEDGE_DATA_ROOT", str(tmp_path / "ego"))
        monkeypatch.setenv("EK_DATA_ROOT", str(tmp_path / "ek"))
        explicit = tmp_path / "explicit"
        assert resolve_data_root(explicit) == explicit

    def test_ego_env_wins_over_ek_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """EGOKNOWLEDGE_DATA_ROOT 优先于 EK_DATA_ROOT（与历史行为一致）。"""

        ego = tmp_path / "ego"
        monkeypatch.setenv("EGOKNOWLEDGE_DATA_ROOT", str(ego))
        monkeypatch.setenv("EK_DATA_ROOT", str(tmp_path / "ek"))
        assert resolve_data_root() == ego

    def test_ek_env_used_when_ego_absent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """EGOKNOWLEDGE_DATA_ROOT 未设时回落到 EK_DATA_ROOT。"""

        ek = tmp_path / "ek"
        monkeypatch.delenv("EGOKNOWLEDGE_DATA_ROOT", raising=False)
        monkeypatch.setenv("EK_DATA_ROOT", str(ek))
        assert resolve_data_root() == ek

    def test_falls_back_to_default_when_no_env_no_explicit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """无 explicit 且无 env 时回落到 default_data_root()。"""

        monkeypatch.delenv("EGOKNOWLEDGE_DATA_ROOT", raising=False)
        monkeypatch.delenv("EK_DATA_ROOT", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert resolve_data_root() == tmp_path / ".ego-knowledge" / "data"
        assert resolve_data_root() == default_data_root()

    def test_empty_string_env_treated_as_unset(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """空字符串 env 视为未设置，继续走下一优先级（与原 normalize_legacy 行为一致）。"""

        ego = tmp_path / "ego"
        # EGOKNOWLEDGE_DATA_ROOT 空字符串 → 跳过；EK_DATA_ROOT 命中
        monkeypatch.setenv("EGOKNOWLEDGE_DATA_ROOT", "")
        monkeypatch.setenv("EK_DATA_ROOT", str(ego))
        assert resolve_data_root() == ego

    def test_no_resolve_called_on_explicit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """真源不做 .resolve()；返回值与传入 Path 相等（绝对化由调用方负责）。

        锁定 archive_dirty_concepts 的既有契约：argparse default 不 resolve，
        避免在 parser 构建期触发文件系统访问。
        """

        monkeypatch.delenv("EGOKNOWLEDGE_DATA_ROOT", raising=False)
        monkeypatch.delenv("EK_DATA_ROOT", raising=False)
        relative = Path("relative/path")
        # 返回值就是传入对象本身，未被 resolve
        assert resolve_data_root(relative) is relative


# ---------------------------------------------------------------------------
# sha256_text_hex / sha256_text_digest：语义边界锁定
# ---------------------------------------------------------------------------
# 覆盖 finding B：archive_dirty_concepts._sha256_text 返回裸 hex，
# normalize_legacy._sha256_text 返回带 ``sha256:`` 前缀，同名异义导致 grep 困惑。
# 现已拆分为 paths.sha256_text_hex（裸 hex）与 paths.sha256_text_digest（带前缀），
# 命名自描述语义。下列测试锁定两者的输出契约与互斥语义。


class TestSha256TextSemantics:
    """锁定 hex/digest 两种语义的输出格式与互斥性。"""

    def test_hex_returns_bare_64_lowercase_hex(self) -> None:
        """sha256_text_hex 返回 64 位小写 hex，无前缀。"""

        out = sha256_text_hex("hello")
        assert len(out) == 64
        assert out == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        assert not out.startswith("sha256:")

    def test_digest_returns_prefixed_64_lowercase_hex(self) -> None:
        """sha256_text_digest 返回 ``sha256:<64hex>``，前缀自描述。"""

        out = sha256_text_digest("hello")
        assert out == "sha256:" + "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        assert out.startswith("sha256:")

    def test_hex_and_digest_share_same_underlying_hash(self) -> None:
        """两函数底层哈希相同，差异仅在前缀（语义边界 = 前缀有无）。"""

        text = "任意 unicode 内容：全角，emoji 🎉，换行\n\t制表"
        assert sha256_text_digest(text) == "sha256:" + sha256_text_hex(text)

    def test_hex_and_digest_are_not_equal(self) -> None:
        """同名异义的历史已消除：两函数输出永不相等（前缀差异）。"""

        assert sha256_text_hex("x") != sha256_text_digest("x")

    def test_empty_string_has_well_defined_hash(self) -> None:
        """空串哈希稳定，前缀语义一致（archive dirty_concepts sidecar 空 snapshot 场景）。"""

        empty_hex = sha256_text_hex("")
        empty_digest = sha256_text_digest("")
        assert empty_hex == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert empty_digest == "sha256:" + empty_hex

    def test_unicode_normalized_to_utf8_bytes(self) -> None:
        """中文按 UTF-8 编码后哈希，结果与 hashlib 标准实现一致。"""

        import hashlib

        text = "中文测试"
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert sha256_text_hex(text) == expected
        assert sha256_text_digest(text) == "sha256:" + expected
