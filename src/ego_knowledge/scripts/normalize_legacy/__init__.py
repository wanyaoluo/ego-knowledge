"""存量 frontmatter 全角结构标点修复 CLI。

三种模式：
- ``dry-run``：扫描 ``entries/**/*.md``，报告待修复清单（不写文件）。
- ``apply``：备份原始内容到 ``backup_dir`` 镜像路径，再写回修复后的 frontmatter。
- ``restore``：从 ``backup_dir`` 反向恢复原始内容。

数据根解析复用 ``ego_knowledge.paths.resolve_data_root`` 真源，优先级链与
``scripts/archive_dirty_concepts`` 一致：``--data-root`` 参数 >
``EGOKNOWLEDGE_DATA_ROOT`` > ``EK_DATA_ROOT`` > ``default_data_root()``。
**禁止隐式扫描仓库根**。

修复规则复用 Phase 0.2 的 ``ego_knowledge.frontmatter._fix_fullwidth_punctuation``。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from ego_knowledge.errors import ValidationError
from ego_knowledge.paths import resolve_data_root

from ._apply import normalize_legacy_apply, read_manifest_full
from ._dry_run import (
    FileChange,
    NormalizeLegacyError,
    NormalizeReport,
    normalize_legacy_dry_run,
)
from ._restore import normalize_legacy_restore

__all__ = [
    "FileChange",
    "NormalizeLegacyError",
    "NormalizeReport",
    "build_parser",
    "main",
    "normalize_legacy_apply",
    "normalize_legacy_dry_run",
    "normalize_legacy_restore",
    "read_manifest_full",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ego_knowledge.scripts.normalize_legacy",
        description=("扫描 EgoKnowledge entries frontmatter 全角结构标点并按模式修复"),
    )
    modes = parser.add_mutually_exclusive_group(required=False)
    modes.add_argument(
        "--dry-run",
        action="store_true",
        help="只读扫描，输出待修复清单与 diff 摘要",
    )
    modes.add_argument(
        "--apply",
        action="store_true",
        help="备份原始内容到 backup-dir 后写回修复后的 frontmatter",
    )
    modes.add_argument(
        "--restore",
        action="store_true",
        help="从 backup-dir 反向恢复原始内容",
    )
    # 兼容 plan 任务的子命令形式：python -m ... dry-run|apply|restore
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["dry-run", "apply", "restore"],
        help="子命令模式（与 --dry-run/--apply/--restore 等价）",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="数据根目录；未传时按 EGOKNOWLEDGE_DATA_ROOT / EK_DATA_ROOT / 默认解析",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="apply/restore 使用的备份目录；apply 未传时基于 data_root 推断",
    )
    parser.add_argument(
        "--scan-sources",
        action="store_true",
        help=(
            "扩展扫描范围到 sources/{docs,imports}/（Phase 2 遗留债务）；"
            "默认只扫 entries/（spec.md 真源边界）"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # resolve_data_root 不做 .resolve()；本工具的备份镜像相对路径计算与日志
    # 需要绝对路径，故在此显式 resolve（行为与原 _resolve_data_root 一致）。
    data_root = resolve_data_root(args.data_root).resolve(strict=False)

    # 统一判定模式：子命令或 flag 二选一。
    # mutually_exclusive_group 只约束三个 flag 互斥，不约束 positional mode；
    # 这里显式拒绝 ``--flag + positional`` 组合，避免命令意图不唯一
    # （例如 ``--restore apply`` 会被按分支顺序解释成 apply，与用户意图相悖）。
    has_flag = args.dry_run or args.apply or args.restore
    if has_flag and args.mode is not None:
        parser.error("--dry-run/--apply/--restore 与位置模式子命令互斥，请二选一")
    if not has_flag and args.mode is None:
        parser.error("必须指定一种模式: dry-run / apply / restore")

    try:
        if _mode_is(args, "dry-run"):
            result = _run_dry_run(data_root, scan_sources=args.scan_sources)
        elif _mode_is(args, "apply"):
            backup_dir = _resolve_backup_dir(args.backup_dir, data_root)
            result = _run_apply(
                data_root, backup_dir, scan_sources=args.scan_sources
            )
        elif _mode_is(args, "restore"):
            if args.backup_dir is None:
                parser.error("restore 需要 --backup-dir")
            result = _run_restore(args.backup_dir, data_root)
        else:  # 防御性兜底（互斥组 + 上面的判定已保证不会走到这里）
            parser.error("未知模式")
    except (NormalizeLegacyError, ValidationError) as exc:
        print(
            json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _mode_is(args: argparse.Namespace, name: str) -> bool:
    """统一判定子命令与 ``--<name>`` 两种等价入口。"""

    flag = getattr(args, name.replace("-", "_"))
    return bool(flag) or args.mode == name


def _run_dry_run(data_root: Path, *, scan_sources: bool = False) -> dict[str, object]:
    report = normalize_legacy_dry_run(data_root, scan_sources=scan_sources)
    payload = _report_to_payload(report, mode="dry-run")
    payload["scan_sources"] = scan_sources
    return payload


def _run_apply(
    data_root: Path, backup_dir: Path, *, scan_sources: bool = False
) -> dict[str, object]:
    report = normalize_legacy_apply(data_root, backup_dir, scan_sources=scan_sources)
    payload = _report_to_payload(report, mode="apply")
    payload["backup_dir"] = str(backup_dir)
    payload["scan_sources"] = scan_sources
    return payload


def _run_restore(backup_dir: Path, data_root: Path) -> dict[str, object]:
    normalize_legacy_restore(backup_dir, data_root)
    return {"ok": True, "mode": "restore", "backup_dir": str(backup_dir)}


def _report_to_payload(report: NormalizeReport, *, mode: str) -> dict[str, object]:
    return {
        "ok": True,
        "mode": mode,
        "data_root": str(report.data_root),
        "scanned": report.scanned,
        "would_change": report.would_change,
        "changes": [
            {
                "path": c.path,
                "changed_fields": list(c.changed_fields),
                "diff_summary": c.diff_summary,
            }
            for c in report.changes
        ],
    }


def _resolve_backup_dir(explicit: Path | None, data_root: Path) -> Path:
    """apply 默认备份目录：``<repo_root>/_backup/egoknowledge-normalize-<ts>/``。

    推断路径：``data_root.parent.parent`` 是常见部署的上级目录。
    其他部署形态应显式传 ``--backup-dir``，
    避免误写到非预期位置。
    """

    if explicit is not None:
        return explicit
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return data_root.parent.parent / "_backup" / f"egoknowledge-normalize-{timestamp}"


if __name__ == "__main__":
    raise SystemExit(main())
