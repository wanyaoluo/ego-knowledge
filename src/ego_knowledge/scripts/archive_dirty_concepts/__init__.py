"""Archive dirty concept entries from a signed snapshot.

Exports the CLI parser/main entry, four workflow commands, snapshot loading,
and the public ArchiveError/Snapshot types for callers and tests.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ego_knowledge.paths import resolve_data_root

from ._dry_run import run_dry_run
from ._execute import run_execute
from ._helpers import ArchiveError, Snapshot, load_snapshot
from ._reconcile import run_reconcile
from ._restore import run_restore

__all__ = [
    "ArchiveError",
    "Snapshot",
    "build_parser",
    "load_snapshot",
    "main",
    "run_dry_run",
    "run_execute",
    "run_reconcile",
    "run_restore",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Archive dirty EgoKnowledge concept entries")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true", help="query registry and create snapshot")
    modes.add_argument("--execute", action="store_true", help="archive entries from snapshot")
    modes.add_argument("--reconcile", action="store_true", help="four-way reconcile")
    modes.add_argument("--restore", action="store_true", help="restore statuses from snapshot")
    # argparse 在 parser 构建时求值 default（非延迟），与原 _default_data_root() 行为一致；
    # resolve_data_root 不做 .resolve()，保持既有不绝对化的契约。
    parser.add_argument("--data-root", type=Path, default=resolve_data_root())
    parser.add_argument("--filter", dest="filter_expr")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--from-snapshot", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--execution", type=Path)
    parser.add_argument("--restore-log", type=Path)
    parser.add_argument("--yes", action="store_true", help="non-interactive dry-run confirmation")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.dry_run:
            if not args.filter_expr or args.snapshot is None or args.expected_count is None:
                parser.error("--dry-run requires --filter, --snapshot, --expected-count")
            result = run_dry_run(
                data_root=args.data_root,
                filter_expr=args.filter_expr,
                snapshot_path=args.snapshot,
                expected_count=args.expected_count,
                assume_yes=args.yes,
            )
        elif args.execute:
            if args.from_snapshot is None or args.log is None:
                parser.error("--execute requires --from-snapshot and --log")
            result = run_execute(
                data_root=args.data_root,
                snapshot_path=args.from_snapshot,
                log_path=args.log,
            )
        elif args.reconcile:
            if args.snapshot is None or args.execution is None:
                parser.error("--reconcile requires --snapshot and --execution")
            result = run_reconcile(
                data_root=args.data_root,
                snapshot_path=args.snapshot,
                execution_path=args.execution,
                restore_log_path=args.restore_log,
            )
        else:
            if args.from_snapshot is None or args.log is None or args.execution is None:
                parser.error("--restore requires --from-snapshot, --log, and --execution")
            result = run_restore(
                data_root=args.data_root,
                snapshot_path=args.from_snapshot,
                log_path=args.log,
                execution_path=args.execution,
            )
    except ArchiveError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def __getattr__(name: str) -> object:
    """Forward private attribute access to submodules for backward compat."""
    from . import _helpers

    if hasattr(_helpers, name):
        return getattr(_helpers, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    raise SystemExit(main())
