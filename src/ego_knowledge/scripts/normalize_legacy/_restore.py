"""restore 阶段：从 ``backup_dir`` 反向恢复 apply 前的原始内容。

策略：
- backup_dir 必须含 manifest（由 apply 阶段写入）；拒绝非 normalize_legacy
  备份，防误用其他 backup_dir 越界覆盖 data_root。
- manifest 强校验（``read_manifest_full`` 一次 IO 拿 entries + scan_sources）：
  record_type / entry_count / 每个 entry 的 ``relative_path``（必须匹配允许前缀）
  与 ``sha256`` 格式；防止 manifest 被篡改或部分备份导致越界写或错误覆盖。
  允许前缀由 manifest ``scan_sources`` 字段决定：False → ``entries/``；
  True → 追加 ``sources/{docs,imports}/``。
- 写回前对每个 backup 文件计算 sha256 并与 manifest 记录值比对，不一致则拒绝
  恢复；防止 backup 内容被改动后仍覆盖条目真源。
- 只恢复 manifest 中记录的合规子树（默认 ``entries/**/*.md``；
  ``scan_sources=True`` 时追加 ``sources/{docs,imports}/**/*.md``），不再盲扫
  backup_dir；backup_dir 内的 stray.md / sources/github/* 等不会被复制到 data_root。
- 写回前对每个 target 做落点校验：``target.resolve()`` 必须位于允许的子树根
  之内（``data_root/entries``；扩展时含 ``data_root/sources/{docs,imports}``），
  防符号链接绕过与 manifest 篡改后的越界写。
- data_root 复用 ``_validate_data_root`` 校验（拒绝文件系统根、仓库根、
  缺失 entries/ 子目录等非 canonical 数据根）。
- 任一文件恢复失败时，错误消息包含「已完成 N/M」上下文，便于定位中间态。
- 不删除 ``backup_dir``：恢复后由用户检查再决定清理，保留故障兜底。
"""

from __future__ import annotations

import logging
from pathlib import Path

from ego_knowledge.paths import sha256_text_digest

from ._apply import read_manifest_full
from ._dry_run import (
    NormalizeLegacyError,
    _allowed_subtree_roots,
    _assert_path_within_allowed_roots,
    _validate_data_root,
)

_logger = logging.getLogger(__name__)


def normalize_legacy_restore(backup_dir: Path, data_root: Path) -> None:
    """按 manifest 把 ``backup_dir`` 下的备份恢复到 ``data_root`` 同位置。

    - backup_dir 必须存在且含 manifest，否则报错（不静默成功掩盖配置错误）。
    - data_root 必须通过 ``_validate_data_root`` 校验。
    - 只恢复 manifest 中记录的合规相对路径，并校验 target 落点不越界。
    - 写回前校验 backup 文件 sha256 与 manifest 一致，防备份内容被改动。
    """

    if not backup_dir.exists():
        raise NormalizeLegacyError(f"backup_dir 不存在: {backup_dir}")
    if not backup_dir.is_dir():
        raise NormalizeLegacyError(f"backup_dir 不是目录: {backup_dir}")
    _validate_data_root(data_root)

    # W4 修复：单次 IO 同时拿 entries + scan_sources（原 read_manifest +
    # read_manifest_scan_sources 各读一次 manifest.json + JSON 解析）。
    entries, scan_sources = read_manifest_full(backup_dir)
    if not entries:
        raise NormalizeLegacyError(
            f"manifest.entries 为空，无可恢复内容: {backup_dir}"
        )

    # 落点子树根（与 apply 写回阶段 + dry-run 扫描共用同一真源；
    # W1+W2 修复：删除 _restore 本地副本，全部 import _dry_run 复用）。
    data_root_resolved = data_root.resolve(strict=False)
    allowed_roots = _allowed_subtree_roots(
        data_root_resolved, scan_sources=scan_sources
    )

    total = len(entries)
    restored = 0
    for entry in entries:
        restored = _restore_one(
            entry,
            backup_dir,
            data_root_resolved,
            allowed_roots,
            restored,
            total,
        )

    _logger.info(
        "normalize_legacy.restore done",
        extra={
            "backup_dir": str(backup_dir),
            "data_root": str(data_root),
            "restored": restored,
            "total": total,
            "scan_sources": scan_sources,
        },
    )


def _restore_one(
    entry: dict[str, object],
    backup_dir: Path,
    data_root_resolved: Path,
    allowed_roots: list[Path],
    restored: int,
    total: int,
) -> int:
    """恢复单条 entry 到 ``data_root_resolved / relative``；返回新 restored 计数。

    W6 修复：把单 entry 恢复逻辑从 ``normalize_legacy_restore`` 抽出，让主函数
    线性编排（圈复杂度从 C 降到 B）。

    target 落点 = ``data_root_resolved / relative``（与 apply 写备份时 abs_path
    同口径）；落点越界由 ``_assert_path_within_allowed_roots`` 兜底符号链接绕过
    与 manifest 篡改后的越界写。
    """

    relative = entry.get("relative_path")
    expected_sha = entry.get("sha256")
    # read_manifest_full 已强校验过类型与格式，这里仅做窄化断言，
    # 让 mypy strict 不需要 ``# type: ignore`` 即可访问字符串方法。
    if not isinstance(relative, str) or not relative:
        raise NormalizeLegacyError(
            f"manifest entry 缺少 relative_path 字符串字段: {entry!r}"
        )
    if not isinstance(expected_sha, str) or not expected_sha:
        raise NormalizeLegacyError(
            f"manifest entry 缺少 sha256 字符串字段: {entry!r}"
        )
    backup_path = backup_dir / relative
    target = data_root_resolved / relative
    _assert_path_within_allowed_roots(target, allowed_roots)
    if not backup_path.is_file():
        raise NormalizeLegacyError(
            f"备份文件缺失 (已完成 {restored}/{total}): {backup_path}"
        )
    try:
        # read_text + mkdir + write_text 统一包装 OSError，
        # 让权限错误也走 NormalizeLegacyError → stderr JSON，
        # 与 _dry_run._scan_entries 的处理风格保持一致。
        original_text = backup_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise NormalizeLegacyError(
            f"恢复失败 (已完成 {restored}/{total}): 读取 {backup_path}: {exc}"
        ) from exc
    actual_sha = sha256_text_digest(original_text)
    if actual_sha != expected_sha:
        # sha 不一致即备份被改动/损坏；继续写回会覆盖真源为错误内容，
        # 直接拒绝并提示用户检查 backup_dir 完整性。
        raise NormalizeLegacyError(
            f"备份 sha256 校验失败 (已完成 {restored}/{total}): "
            f"{backup_path} 期望 {expected_sha} 实际 {actual_sha} "
            f"(backup 可能被篡改或损坏，已拒绝恢复)"
        )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(original_text, encoding="utf-8")
    except OSError as exc:
        raise NormalizeLegacyError(
            f"恢复失败 (已完成 {restored}/{total}): {backup_path} -> {target}: {exc}"
        ) from exc
    return restored + 1
