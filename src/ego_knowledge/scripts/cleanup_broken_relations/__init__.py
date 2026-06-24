"""存量断裂关系清理脚本。

三种模式：
- ``dry-run``：扫描 Markdown 真源与当前 registry，输出断裂关系清单与 AI 边清理计划。
- ``apply``：先备份受影响文件/registry，再删除 ``ai_suggested`` / ``ai_confirmed`` 断裂边。
- ``restore``：从备份恢复 apply 前状态。

策略边界：``origin=confirmed`` 只进入裁决清单，不自动删除；脚本只删除关系边，
不删除任何条目。Markdown 真源扫描范围与 ``ek build-registry`` 保持一致：
``entries/**/*.md`` 与 ``sources/**/*.md``。
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

from ._apply import cleanup_broken_relations_apply, cleanup_broken_relations_restore
from ._scan import (
    CleanupBrokenRelationsError,
    cleanup_broken_relations_dry_run,
    report_to_payload,
)
from ._types import BrokenRelation, CleanupReport, FileCleanupChange

__all__ = [
    "BrokenRelation",
    "CleanupBrokenRelationsError",
    "CleanupReport",
    "FileCleanupChange",
    "build_parser",
    "cleanup_broken_relations_apply",
    "cleanup_broken_relations_dry_run",
    "cleanup_broken_relations_restore",
    "main",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ego_knowledge.scripts.cleanup_broken_relations",
        description="扫描并清理 EgoKnowledge 存量 AI 断裂关系边",
    )
    modes = parser.add_mutually_exclusive_group(required=False)
    modes.add_argument("--dry-run", action="store_true", help="只读扫描并输出清理计划")
    modes.add_argument("--apply", action="store_true", help="备份后执行 AI 断裂边清理")
    modes.add_argument("--restore", action="store_true", help="从 backup-dir 恢复 apply 前状态")
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
        "--report-dir",
        type=Path,
        default=None,
        help="可选，写出 broken-relations 与 confirmed 裁决清单文件；未传时不写出",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_mode_args(parser, args)
    data_root = resolve_data_root(args.data_root).resolve(strict=False)
    try:
        payload = _dispatch_mode(parser, args, data_root)
    except (CleanupBrokenRelationsError, ValidationError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _validate_mode_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    has_flag = args.dry_run or args.apply or args.restore
    if has_flag and args.mode is not None:
        parser.error("--dry-run/--apply/--restore 与位置模式子命令互斥，请二选一")
    if not has_flag and args.mode is None:
        parser.error("必须指定一种模式: dry-run / apply / restore")



def _dispatch_mode(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    data_root: Path,
) -> dict[str, object]:
    if _mode_is(args, "dry-run"):
        return _run_dry_run(args, data_root)
    if _mode_is(args, "apply"):
        return _run_apply(args, data_root)
    if _mode_is(args, "restore"):
        return _run_restore(parser, args, data_root)
    parser.error("未知模式")


def _run_dry_run(args: argparse.Namespace, data_root: Path) -> dict[str, object]:
    report = cleanup_broken_relations_dry_run(data_root)
    payload = report_to_payload(report, mode="dry-run")
    _write_report_files(args.report_dir, report, payload, mode="dry-run")
    return payload


def _run_apply(args: argparse.Namespace, data_root: Path) -> dict[str, object]:
    backup_dir = _resolve_backup_dir(args.backup_dir, data_root)
    report = cleanup_broken_relations_apply(data_root, backup_dir)
    payload = report_to_payload(report, mode="apply")
    payload["backup_dir"] = str(backup_dir)
    _write_report_files(args.report_dir, report, payload, mode="apply")
    return payload


def _run_restore(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    data_root: Path,
) -> dict[str, object]:
    if args.backup_dir is None:
        parser.error("restore 需要 --backup-dir")
    cleanup_broken_relations_restore(args.backup_dir, data_root)
    return {"ok": True, "mode": "restore", "backup_dir": str(args.backup_dir)}


def _mode_is(args: argparse.Namespace, name: str) -> bool:
    flag = getattr(args, name.replace("-", "_"))
    return bool(flag) or args.mode == name


def _resolve_backup_dir(explicit: Path | None, data_root: Path) -> Path:
    if explicit is not None:
        return explicit
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return data_root.parent.parent / "_backup" / f"egoknowledge-relations-{timestamp}"


def _write_report_files(
    report_dir: Path | None,
    report: CleanupReport,
    payload: dict[str, object],
    *,
    mode: str,
) -> None:
    if report_dir is None:
        return
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"cleanup-{mode}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (report_dir / "broken-relations.json").write_text(
        json.dumps(payload["broken_relations"], ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    confirmed_payload = payload["confirmed_adjudication"]
    (report_dir / "confirmed-adjudication.json").write_text(
        json.dumps(confirmed_payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (report_dir / "confirmed-adjudication.md").write_text(
        _render_confirmed_adjudication(list(report.confirmed_adjudication)),
        encoding="utf-8",
    )


def _render_confirmed_adjudication(relations: list[BrokenRelation]) -> str:
    lines = ["# confirmed 断裂关系裁决清单", ""]
    if not relations:
        lines.append("当前没有 origin=confirmed 的断裂关系需要人工裁决。")
        lines.append("")
        return "\n".join(lines)
    lines.extend(
        [
            "| source | target | type | source_path | storage | 建议裁决 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for relation in relations:
        lines.append(
            "| "
            + " | ".join(
                [
                    relation.source_id,
                    relation.target,
                    relation.type,
                    relation.source_path or "",
                    ", ".join(relation.storages),
                    "人工确认：补建目标 / 改指向 / 删除关系",
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
