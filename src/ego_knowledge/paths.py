"""Path planning helpers for EgoKnowledge entries.

``sha256_text_*`` 系列为本次下沉的过渡方案——scripts 子包内同名异义私有副本
（``_sha256_text``）经 commit ``fc1b97c`` 消除后，文本哈希真源暂住本文件。
后续若哈希工具继续扩张（多算法 / 流式 / 二进制输入 / keyed hash），应抽至
独立的 ``_hash.py`` 模块，本文件回归纯路径职责。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import cast

from .errors import ConflictError, StorageError, ValidationError
from .models import ConceptEntry, DecisionEntry, DossierEntry, Entry, NoteEntry, SourceEntry
from .registry import Registry

_UNSORTED_DOMAIN = "_unsorted"
_SOURCE_TYPE_DIRS: dict[str, str] = {
    "web": "web",
    "doc": "docs",
    "media": "media",
    "import": "imports",
    "github_release": "github",
}

# scripts 子包（archive_dirty_concepts / normalize_legacy）共享的数据根环境变量优先级链。
# MCP server 走独立路径（强制 EGOKNOWLEDGE_DATA_ROOT，见 mcp_server.py），
# click CLI（cli.py）只识别 EK_DATA_ROOT 单变量——两者优先级链与本常量不同语义，
# 故不纳入本真源，避免破坏其既有契约。
_DATA_ROOT_ENV_VARS: tuple[str, ...] = ("EGOKNOWLEDGE_DATA_ROOT", "EK_DATA_ROOT")

# 持久化/比对用的 sha256 摘要前缀。``archive_dirty_concepts._file_sha256``
# （Path→bytes，就地实现）与 normalize_legacy manifest（经本文件
# ``sha256_text_digest``，str→bytes）在 commit ``fc1b97c`` 后已分别独立实现，
# 不再共用同一函数；二者仅前缀格式（``sha256:``）保持一致以便跨子包 grep。
# 裸 hex 仅用于内部即时比对（snapshot payload 校验、sidecar 哈希、
# entry_ids_sha256），不写入持久化字段。
_SHA256_PREFIX = "sha256:"


def default_data_root() -> Path:
    """返回 CLI/工具未设置 ``EK_DATA_ROOT`` 时的默认数据根。

    惰性求值：每次调用都重新读取 ``Path.home()``，不在模块导入期固化。
    这样 HOME 隔离的测试（子进程改 ``HOME``）能真实生效；若退化为模块级
    常量，import 时刻的 ``Path.home()`` 会冻结，monkeypatch HOME 失效。

    HOME 缺失兜底：``Path.home()`` 在 ``$HOME`` 未设时走系统用户数据库
    （POSIX ``getpwuid``）解析真实用户家目录，不会抛错；故本函数在容器
    或隔离 env 下仍可安全调用。

    配合 ``EK_DATA_ROOT`` 环境变量覆盖语义：调用方用
    ``os.environ.get("EK_DATA_ROOT", default_data_root())`` 即可在显式设置时
    优先环境变量、缺失时回落本默认值。
    """
    return Path.home() / ".ego-knowledge" / "data"


def resolve_data_root(explicit: Path | None = None) -> Path:
    """scripts 子包共享的数据根解析真源。

    优先级链（高 → 低）：

    1. ``explicit``（CLI ``--data-root`` 显式参数）
    2. ``EGOKNOWLEDGE_DATA_ROOT`` 环境变量
    3. ``EK_DATA_ROOT`` 环境变量
    4. ``default_data_root()``（``~/.ego-knowledge/data``）

    **不**做 ``Path.resolve()``：是否绝对化由调用方决定，避免绑架
    archive_dirty_concepts（不 resolve，作 argparse default）与
    normalize_legacy（``.resolve(strict=False)``，用于备份镜像相对路径计算）
    各自的既有契约。

    返回未 resolve 的 ``Path``，调用方按需 ``.resolve(strict=False)``。
    """

    if explicit is not None:
        return explicit
    for env_var in _DATA_ROOT_ENV_VARS:
        value = os.environ.get(env_var)
        if value:
            return Path(value)
    return default_data_root()


def sha256_text_hex(text: str) -> str:
    """text → 裸 64 位小写 hex（无前缀）。

    用于内部即时比对：snapshot payload sha256、sidecar 哈希、entry_ids_sha256。
    不写入持久化字段——持久化字段统一用 ``sha256_text_digest`` 带前缀形式，
    避免与裸 hex 混淆。
    """

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_text_digest(text: str) -> str:
    """text → ``sha256:<64 位小写 hex>``（带前缀）。

    用于持久化字段：normalize_legacy manifest.entries[].sha256、
    archive_dirty_concepts file_hash_before。带前缀使哈希值自描述，
    与裸 hex（内部比对）显式区分，防 grep 误读。
    """

    return _SHA256_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


def allocate_unique_path(
    data_root: Path,
    registry: Registry,
    entry: Entry,
    *,
    current_id: str | None = None,
) -> Path:
    base_path = path_for_entry(data_root, entry, slug=entry.slug)
    if path_is_available(registry, base_path, current_id=current_id):
        return base_path

    # F1: ingest 路径（current_id is None）不允许静默加后缀
    if current_id is None:
        raise ConflictError(f"slug 冲突无法分配路径: {entry.slug}")

    # update 路径保留后缀 fallback
    suffix = entry.id.split("_", 2)[-1][:6]
    candidate_slug = f"{entry.slug}-{suffix}"
    candidate = path_for_entry(data_root, entry, slug=candidate_slug)
    if path_is_available(registry, candidate, current_id=current_id):
        return candidate
    raise ConflictError(f"slug 冲突无法分配路径: {entry.slug}")


def path_is_available(registry: Registry, path: Path, current_id: str | None = None) -> bool:
    row = registry.conn.execute(
        "SELECT id FROM entries WHERE file_path = ? LIMIT 1",
        (str(path),),
    ).fetchone()
    if row is None:
        return not path.exists()
    return current_id is not None and cast(str, row["id"]) == current_id


def path_for_entry(data_root: Path, entry: Entry, *, slug: str) -> Path:
    if isinstance(entry, SourceEntry):
        captured_at = entry.captured_at or entry.created_at
        source_root = _SOURCE_TYPE_DIRS.get(entry.source_type)
        if source_root is None:
            raise ValidationError(f"不支持的 source_type: {entry.source_type}")
        if entry.source_type == "import":
            return data_root / "sources" / source_root / f"{slug}.md"
        return (
            data_root
            / "sources"
            / source_root
            / f"{captured_at.year:04d}"
            / f"{captured_at.month:02d}"
            / f"{slug}.md"
        )
    if isinstance(entry, NoteEntry):
        extracted_at = entry.extracted_at or entry.created_at
        return (
            data_root
            / "entries"
            / "notes"
            / f"{extracted_at.year:04d}"
            / f"{extracted_at.month:02d}"
            / f"{slug}.md"
        )
    if isinstance(entry, ConceptEntry):
        return data_root / "entries" / "concepts" / domain_dir(entry.domain) / f"{slug}.md"
    if isinstance(entry, DossierEntry):
        return data_root / "entries" / "dossiers" / domain_dir(entry.domain) / f"{slug}.md"
    if isinstance(entry, DecisionEntry):
        decided_at = entry.decided_at or entry.created_at
        return data_root / "entries" / "decisions" / f"{decided_at.year:04d}" / f"{slug}.md"
    return data_root / "views" / "indexes" / f"{entry.created_at.year:04d}" / f"{slug}.md"


def domain_dir(domain: str | None) -> str:
    return domain or _UNSORTED_DOMAIN


def relative_path(data_root: Path, path: Path) -> str:
    try:
        return path.relative_to(data_root).as_posix()
    except ValueError:
        return path.as_posix()


def file_path_of(entry: Entry) -> Path:
    if entry.file_path is None:
        raise StorageError(f"条目缺少 file_path: {entry.id}")
    return Path(entry.file_path)
