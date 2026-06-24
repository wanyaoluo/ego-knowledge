"""apply 阶段：备份原始内容 + 写回修复后的 frontmatter。

策略：
- 备份布局镜像 ``data_root`` 相对路径，``backup_dir/entries/notes/x.md``
  对应 ``data_root/entries/notes/x.md`` 的原始内容；restore 时反向遍历即可。
- 备份内容是「apply 前的完整原始文本」，restore 直接覆盖即可还原。
- 只对 ``would_change > 0`` 的文件备份与写回，干净文件不动。
- 备份完成后在 ``backup_dir`` 根写一份 manifest（JSON），记录所有备份条目
  的相对路径与原始内容 sha256；restore 据此校验，不再盲扫 backup_dir。
- 批量两阶段：先全部备份成功并写 manifest，再批量写回；写回阶段任一文件
  失败时按已写清单反向恢复，错误消息包含 partial rollback 上下文。
- ``scan_sources=True`` 时扫描边界扩展到 ``sources/{docs,imports}/``，manifest
  会记录 ``scan_sources`` 字段，restore 时据此决定允许的 relative_path 前缀。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path, PurePosixPath

from ego_knowledge.paths import sha256_text_digest

from ._dry_run import (
    FileChange,
    FileScan,
    NormalizeLegacyError,
    NormalizeReport,
    _allowed_subtree_roots,
    _assert_path_within_allowed_roots,
    _validate_data_root,
    scan_for_changes,
)

_logger = logging.getLogger(__name__)

_MANIFEST_FILENAME = "normalize-legacy-manifest.json"
_MANIFEST_RECORD_TYPE = "normalize_legacy.backup.manifest/v1"

# manifest 允许的 relative_path 前缀（按 scan_sources 状态决定）。
# 单一真源（W1 修复）：从 _dry_run.py 移到 _apply.py，与 manifest 写入/读取
# 职责模块对齐——这些前缀是 manifest 契约，逻辑上属于 _apply 而非扫描器。
_MANIFEST_PREFIX_ENTRIES = "entries/"
_MANIFEST_PREFIX_SOURCES_DOCS = "sources/docs/"
_MANIFEST_PREFIX_SOURCES_IMPORTS = "sources/imports/"

# manifest.entries[].sha256 必须形如 ``sha256:<64 位小写 hex>``。
# 加 ``sha256:`` 前缀是为了与 ``archive_dirty_concepts`` 等其他工作流的哈希
# 写入风格一致（见 ``paths.sha256_text_digest`` 与
# ``archive_dirty_concepts/_helpers._file_sha256``）。
_MANIFEST_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def normalize_legacy_apply(
    data_root: Path, backup_dir: Path, *, scan_sources: bool = False
) -> NormalizeReport:
    """备份原始内容并写回修复后的 frontmatter。

    返回与 dry-run 同结构的 ``NormalizeReport``，便于复跑对比。

    ``scan_sources=True`` 时扫描边界扩展到 ``sources/{docs,imports}/``，
    manifest 记录该状态让 restore 不依赖额外参数即可校验路径前缀。
    """

    _validate_data_root(data_root)
    _validate_backup_dir(data_root, backup_dir)

    scanned, changes = scan_for_changes(data_root, scan_sources=scan_sources)
    if not changes:
        _logger.info(
            "normalize_legacy.apply nothing to do",
            extra={"data_root": str(data_root), "scanned": scanned},
        )
        return NormalizeReport(
            data_root=data_root,
            scanned=scanned,
            would_change=0,
            changes=[],
        )

    backup_dir.mkdir(parents=True, exist_ok=True)
    # 阶段 1：批量备份 + 写 manifest（任一失败立即中止，原文件未被改）
    manifest_entries: list[dict[str, object]] = []
    for change in changes:
        _backup_original(change, backup_dir)
        manifest_entries.append(
            {
                "relative_path": change.relative_path,
                "sha256": sha256_text_digest(change.original_text),
            }
        )
    _write_manifest(backup_dir, manifest_entries, scan_sources=scan_sources)

    # 阶段 2：批量写回（任一失败时按已写清单回滚，错误消息含上下文）。
    # qa-strict R1：写回前对每个 change.abs_path 再跑一次 allowed-root 校验，
    # 与扫描阶段护栏对称（防 symlink 在扫描与写回之间被植入）。
    data_root_resolved = data_root.resolve(strict=False)
    allowed_roots = _allowed_subtree_roots(
        data_root_resolved, scan_sources=scan_sources
    )
    written: list[FileScan] = []
    try:
        for change in changes:
            _assert_path_within_allowed_roots(change.abs_path, allowed_roots)
            _write_fixed(change)
            written.append(change)
    except NormalizeLegacyError as exc:
        rolled_back, rollback_failures = _rollback_written(written)
        _raise_batch_write_failure(exc, rolled_back, rollback_failures, len(changes))

    _logger.info(
        "normalize_legacy.apply done",
        extra={
            "data_root": str(data_root),
            "backup_dir": str(backup_dir),
            "scanned": scanned,
            "would_change": len(changes),
            "scan_sources": scan_sources,
        },
    )
    return NormalizeReport(
        data_root=data_root,
        scanned=scanned,
        would_change=len(changes),
        changes=[
            FileChange(
                path=c.relative_path,
                changed_fields=c.changed_fields,
                diff_summary=c.diff_summary,
            )
            for c in changes
        ],
    )


def _backup_original(change: FileScan, backup_dir: Path) -> None:
    """把原始内容写到 backup_dir 镜像路径。

    顺序：先备份成功再覆盖原文件，确保失败路径下原文件不被破坏。
    """

    backup_path = backup_dir / change.relative_path
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        backup_path.write_text(change.original_text, encoding="utf-8")
    except OSError as exc:
        raise NormalizeLegacyError(
            f"备份失败，已中止（原文件未改）: {change.abs_path} -> {backup_path}: {exc}"
        ) from exc


def _write_fixed(change: FileScan) -> None:
    """写回修复后的全文；写回失败时尽量恢复原文件，错误消息区分回滚成功/失败。

    单文件两态：

    - 写回失败 + 回滚成功 → 「已回滚到原内容（未损坏）」
    - 写回失败 + 回滚也失败 → 「原文件可能损坏，请从 backup 手动恢复」

    让用户从 stderr JSON 即可判断是否需要人工介入，符合玻璃盒可定位目标。
    """

    abs_path = change.abs_path
    try:
        abs_path.write_text(change.fixed_text, encoding="utf-8")
    except OSError as exc:
        rollback_ok = True
        try:
            abs_path.write_text(change.original_text, encoding="utf-8")
        except OSError:
            rollback_ok = False
            _logger.exception(
                "normalize_legacy.apply rollback failed",
                extra={"path": str(abs_path)},
            )
        if rollback_ok:
            msg = f"写回失败，已回滚到原内容（未损坏）: {abs_path}: {exc}"
        else:
            msg = f"写回且回滚均失败，原文件可能损坏，请从 backup 手动恢复: {abs_path}: {exc}"
        raise NormalizeLegacyError(msg) from exc


def _rollback_written(written: list[FileScan]) -> tuple[int, list[Path]]:
    """批量回滚已写文件，返回 (成功数, 失败路径清单)。"""

    rolled_back = 0
    failures: list[Path] = []
    for change in written:
        try:
            change.abs_path.write_text(change.original_text, encoding="utf-8")
            rolled_back += 1
        except OSError:
            failures.append(change.abs_path)
            _logger.exception(
                "normalize_legacy.apply batch rollback failed",
                extra={"path": str(change.abs_path)},
            )
    return rolled_back, failures


def _raise_batch_write_failure(
    cause: NormalizeLegacyError,
    rolled_back: int,
    rollback_failures: list[Path],
    total: int,
) -> None:
    """构造批处理失败的错误消息，含 partial rollback 上下文。"""

    parts = [f"批处理失败 (已回滚 {rolled_back}/{total}): {cause}"]
    if rollback_failures:
        parts.append(
            "以下文件回滚失败，原文件可能损坏，请从 backup 手动恢复: "
            + ", ".join(str(p) for p in rollback_failures)
        )
    raise NormalizeLegacyError("; ".join(parts)) from cause


def _write_manifest(
    backup_dir: Path,
    entries: list[dict[str, object]],
    *,
    scan_sources: bool = False,
) -> None:
    """写 manifest 到 backup_dir 根，供 restore 校验。

    ``scan_sources`` 字段记录 apply 时的扫描边界状态，让 restore 不依赖
    额外参数即可决定允许的 relative_path 前缀（默认 ``entries/``；
    ``scan_sources=True`` 时追加 ``sources/{docs,imports}/``）。
    """

    manifest_path = backup_dir / _MANIFEST_FILENAME
    payload: dict[str, object] = {
        "record_type": _MANIFEST_RECORD_TYPE,
        "entry_count": len(entries),
        "scan_sources": scan_sources,
        "entries": entries,
    }
    try:
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as exc:
        raise NormalizeLegacyError(
            f"写 manifest 失败 (备份已建但 restore 将无法校验): {manifest_path}: {exc}"
        ) from exc


def read_manifest(backup_dir: Path) -> list[dict[str, object]]:
    """读取并强校验 manifest，返回 entries 列表；缺失/损坏/契约不符均报错。

    供测试与诊断使用；restore 路径直接用 ``read_manifest_full`` 一次拿
    ``(entries, scan_sources)``，避免连续调用引发双次磁盘 IO。

    强校验项（任一不符即拒绝，防 manifest 被篡改或部分备份；契约由
    ``read_manifest_full`` 实际执行）：

    - 顶层 ``record_type`` 必须是 normalize_legacy manifest v1；
    - 顶层 ``entry_count`` 必须是 int 且等于 ``len(entries)``；
    - 顶层 ``scan_sources`` 必须是 bool（缺省视为 False，向后兼容旧 manifest）；
    - 每个 entry 必须是 dict；
    - 每个 entry 的 ``relative_path`` 必须是规范化 posix 字符串、非绝对、
      不含 ``..`` 段、并以 manifest ``scan_sources`` 状态决定的允许前缀
      之一开头 + ``.md`` 结尾（锚定 spec 真源边界）；
    - 每个 entry 的 ``sha256`` 必须形如 ``sha256:<64 位小写 hex>``。
    """

    entries, _ = read_manifest_full(backup_dir)
    return entries


def read_manifest_scan_sources(backup_dir: Path) -> bool:
    """读取 manifest 的 ``scan_sources`` 字段。

    供测试与诊断使用；restore 路径直接用 ``read_manifest_full`` 一次拿
    ``(entries, scan_sources)``，避免连续调用引发双次磁盘 IO。

    缺省视为 False（向后兼容 Phase 2.2 已写入的旧 manifest，无 scan_sources 字段）。
    非 bool 类型拒绝（防 manifest 篡改注入非法值绕过路径校验）。
    """

    _, scan_sources = read_manifest_full(backup_dir)
    return scan_sources


def read_manifest_full(
    backup_dir: Path,
) -> tuple[list[dict[str, object]], bool]:
    """manifest 读取真源：单次 IO + JSON 解析同时返回 (entries, scan_sources)。

    W4 修复：read_manifest / read_manifest_scan_sources / restore 全部走本函数，
    消除 manifest.json 多次磁盘读 + 多次 JSON 解析 + 多次顶层校验的隐患。
    """

    payload = _read_manifest_payload(backup_dir)
    entries_obj, scan_sources = _validate_manifest_top(payload, backup_dir)
    allowed_prefixes = _allowed_prefixes_for(scan_sources)
    entries = _validate_manifest_entries(entries_obj, allowed_prefixes, backup_dir)
    return entries, scan_sources


def _read_manifest_payload(backup_dir: Path) -> dict[str, object]:
    """单次磁盘读 + JSON 解析 + 顶层 dict 校验。

    抽出 IO 与基础解析（W4：避免 read_manifest 与 read_manifest_scan_sources
    各读一次；W6：让 read_manifest 主函数圈复杂度从 C 降到 B）。
    """

    manifest_path = backup_dir / _MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise NormalizeLegacyError(
            f"backup_dir 缺少 manifest，拒绝恢复 (非 normalize_legacy 备份): {manifest_path}"
        )
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise NormalizeLegacyError(f"读取 manifest 失败: {manifest_path}: {exc}") from exc
    try:
        payload: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NormalizeLegacyError(f"manifest JSON 解析失败: {manifest_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise NormalizeLegacyError(f"manifest 顶层不是 JSON object: {manifest_path}")
    return payload


def _validate_manifest_top(
    payload: dict[str, object], manifest_path: Path
) -> tuple[list[object], bool]:
    """校验顶层字段（record_type / entries 是 list / entry_count / scan_sources）。

    返回 ``(entries 列表, scan_sources)``；任一不符即抛 ``NormalizeLegacyError``。
    W6 修复：把顶层字段校验抽出来，让 ``read_manifest`` 主函数线性编排。
    """

    if payload.get("record_type") != _MANIFEST_RECORD_TYPE:
        raise NormalizeLegacyError(
            f"manifest record_type 不符 (期望 {_MANIFEST_RECORD_TYPE}): {manifest_path}"
        )
    entries_obj = payload.get("entries")
    if not isinstance(entries_obj, list):
        raise NormalizeLegacyError(f"manifest.entries 不是 list: {manifest_path}")
    entry_count = payload.get("entry_count")
    if not isinstance(entry_count, int) or isinstance(entry_count, bool):
        raise NormalizeLegacyError(
            f"manifest.entry_count 不是 int: {entry_count!r} ({manifest_path})"
        )
    if entry_count != len(entries_obj):
        raise NormalizeLegacyError(
            f"manifest.entry_count={entry_count} 与 entries 实际长度 "
            f"{len(entries_obj)} 不一致 ({manifest_path})"
        )
    # scan_sources 字段：旧 manifest 缺省视为 False（向后兼容 Phase 2.2 已有备份）。
    # 必须是 bool，拒绝其他类型防 manifest 篡改注入越界前缀。
    scan_sources_raw = payload.get("scan_sources", False)
    if not isinstance(scan_sources_raw, bool):
        raise NormalizeLegacyError(
            f"manifest.scan_sources 不是 bool: {scan_sources_raw!r} ({manifest_path})"
        )
    return entries_obj, scan_sources_raw


def _validate_manifest_entries(
    entries_obj: list[object],
    allowed_prefixes: tuple[str, ...],
    manifest_path: Path,
) -> list[dict[str, object]]:
    """校验每个 entry 是 dict 且 relative_path / sha256 符合契约。

    W6 修复：把 entry 字段校验抽出来，让 ``read_manifest`` 主函数圈复杂度
    从 C 降到 B。返回稳定的 list[dict[str, object]] 视图供 restore 写回路径消费。
    """

    entries: list[dict[str, object]] = []
    for index, entry in enumerate(entries_obj):
        if not isinstance(entry, dict):
            raise NormalizeLegacyError(
                f"manifest.entries[{index}] 不是 JSON object: {entry!r} ({manifest_path})"
            )
        relative_path = _assert_manifest_relative_path(
            entry.get("relative_path"), index, manifest_path, allowed_prefixes
        )
        sha256 = _assert_manifest_sha256(entry.get("sha256"), index, manifest_path)
        entries.append({"relative_path": relative_path, "sha256": sha256})
    return entries


def _allowed_prefixes_for(scan_sources: bool) -> tuple[str, ...]:
    """按 ``scan_sources`` 状态返回 manifest 允许的 relative_path 前缀。

    - ``scan_sources=False``（默认）：只允许 ``entries/``（spec.md:207 真源边界）。
    - ``scan_sources=True``：追加 ``sources/docs/`` 与 ``sources/imports/``
      （Phase 2 遗留 sources 债务；github/、web/ 仍是不可逆素材，不纳入）。
    """

    if scan_sources:
        return (
            _MANIFEST_PREFIX_ENTRIES,
            _MANIFEST_PREFIX_SOURCES_DOCS,
            _MANIFEST_PREFIX_SOURCES_IMPORTS,
        )
    return (_MANIFEST_PREFIX_ENTRIES,)


def _assert_manifest_relative_path(
    value: object,
    index: int,
    manifest_path: Path,
    allowed_prefixes: tuple[str, ...],
) -> str:
    """校验 manifest entry 的 relative_path 符合允许的前缀契约。

    防止 manifest 被篡改后插入绝对路径、``..`` 越界、非允许子树或非 md
    文件，让后续 restore 写回路径仍收束在 spec 真源边界内。校验通过返回
    窄化后的 str，让调用方拿到强类型值（避免 mypy strict 走 ``Any``）。
    """

    if not isinstance(value, str) or not value:
        raise NormalizeLegacyError(
            f"manifest.entries[{index}].relative_path 缺少字符串值: {value!r} ({manifest_path})"
        )
    # manifest 由 apply 通过 ``Path.as_posix()`` 写入，反序列化必须用 posix
    # 语义解析；用 PureWindowsPath 会把 ``/`` 当成普通字符，掩盖越界路径。
    parts = PurePosixPath(value).parts
    if PurePosixPath(value).is_absolute():
        raise NormalizeLegacyError(
            f"manifest.entries[{index}].relative_path 是绝对路径: {value!r} ({manifest_path})"
        )
    if ".." in parts:
        raise NormalizeLegacyError(
            f"manifest.entries[{index}].relative_path 含 '..' 段 (越界风险): "
            f"{value!r} ({manifest_path})"
        )
    if not value.startswith(allowed_prefixes):
        raise NormalizeLegacyError(
            f"manifest.entries[{index}].relative_path 不以允许前缀 "
            f"{allowed_prefixes} 开头 (非 spec 真源边界): {value!r} ({manifest_path})"
        )
    # ``entries/x.md`` 也合法（rglob('*.md') 会扫到）；只要以 .md 结尾即可。
    if not value.endswith(".md"):
        raise NormalizeLegacyError(
            f"manifest.entries[{index}].relative_path 不以 '.md' 结尾: {value!r} ({manifest_path})"
        )
    return value


def _assert_manifest_sha256(value: object, index: int, manifest_path: Path) -> str:
    """校验 manifest entry 的 sha256 形如 ``sha256:<64 位小写 hex>``。

    apply 写入侧由 ``paths.sha256_text_digest`` 产出该前缀格式；restore 比对前必须先
    拒绝畸形哈希，否则 ``backup_text.encode`` 后的 sha256 与字符串字面比对
    会把异常 manifest 当成「内容被改」误判，掩盖 manifest 本身损坏的事实。
    校验通过返回窄化后的 str。
    """

    if not isinstance(value, str) or not _MANIFEST_SHA256_RE.match(value):
        raise NormalizeLegacyError(
            f"manifest.entries[{index}].sha256 不符 sha256:<64hex> 格式: "
            f"{value!r} ({manifest_path})"
        )
    return value


def _validate_backup_dir(data_root: Path, backup_dir: Path) -> None:
    """护栏：backup_dir 不能嵌套在 data_root 内，不能已存在且非空。

    - 嵌套防递归写：apply 写 backup_dir 时被自身扫描到会引发循环。
    - 非空防覆盖：既有备份可能对应一次未完成的工作流，不能静默覆盖。
    """

    # W5 修复：用 ``Path.parents`` 显式比较替代 ``try/except ValueError``
    # 控制业务流程。原实现靠 ``relative_to`` 抛 ValueError 判定合法不嵌套，
    # except 块同时吞「合法」与「路径错误」两种语义，调试时不直观。
    backup_resolved = backup_dir.resolve(strict=False)
    data_root_resolved = data_root.resolve(strict=False)
    if backup_resolved == data_root_resolved or data_root_resolved in backup_resolved.parents:
        raise NormalizeLegacyError(
            f"backup_dir 不能嵌套在 data_root 内 (相对路径: "
            f"{backup_resolved.relative_to(data_root_resolved)}): {backup_dir}"
        )

    if backup_dir.exists() and any(backup_dir.rglob("*")):
        raise NormalizeLegacyError(
            f"backup_dir 已存在且非空，拒绝覆盖 (避免丢失既有备份): {backup_dir}"
        )
