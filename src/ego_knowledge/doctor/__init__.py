"""File-level and terminology health checks for EgoKnowledge."""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import shutil
import subprocess
import tempfile
from enum import Enum
from pathlib import Path

from .._validation import _asdict, _is_dataclass_instance
from ..errors import StorageError
from ..frontmatter import _fm_to_entry, read_file
from ..metrics import recompute_for_neighbors
from ..registry import Registry
from ._checks import (
    _CHECK_REGISTRY,
    _check_alias_conflicts,
    _check_broken_relations,
    _check_dir_size_over_200,
    _check_frontmatter_body_link_diff,
    _check_fullwidth_chars,
    _check_id_uniqueness,
    _check_jieba_fallback_summary,
    _check_metrics_stale,
    _check_nfc_residuals,
    _check_orphan_files,
    _check_orphan_relation_type,
    _check_schema_validation,
    _check_terminology_audit,
)
from ._types import CheckHandler, DoctorReport, Finding, RecoveryRecord, Severity

log = logging.getLogger(__name__)

__all__ = [
    "CheckHandler",
    "DoctorReport",
    "Finding",
    "RecoveryRecord",
    "Severity",
    "_CHECK_REGISTRY",
    "_category_from_rule",
    "_check_alias_conflicts",
    "_check_broken_relations",
    "_check_dir_size_over_200",
    "_check_frontmatter_body_link_diff",
    "_check_fullwidth_chars",
    "_check_id_uniqueness",
    "_check_jieba_fallback_summary",
    "_check_metrics_stale",
    "_check_nfc_residuals",
    "_check_orphan_files",
    "_check_orphan_relation_type",
    "_check_schema_validation",
    "_check_terminology_audit",
    "_create_task_board_task",
    "_parse_recovery_log",
    "_resolve_task_board_dir",
    "_write_report",
    "doctor",
]


def _json_default(obj: object) -> object:
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if _is_dataclass_instance(obj):
        return _asdict(obj)
    if isinstance(obj, Path):
        return obj.as_posix()
    return obj


def _write_report(findings: list[Finding], log_dir: Path, prefix: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    report_path = log_dir / f"{prefix}-{ts}.json"
    payload = [_asdict(item) if _is_dataclass_instance(item) else item for item in findings]
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return report_path


def _category_from_rule(rule_id: str) -> str:
    """Map rule_id to task-board category: 'fix' or 'refine'."""
    if (
        rule_id.startswith(("decay_", "push_premise_shaken"))
        or rule_id == "structure_orphan_decision"
    ):
        return "fix"
    if rule_id.startswith("action_") or rule_id in {"push_crystallize", "push_internal_split"}:
        return "refine"
    return "fix"


def _resolve_task_board_dir() -> Path | None:
    """惰性解析 task-board CLI 目录。

    返回 ``EK_TASK_BOARD_DIR`` 环境变量指向的目录；未设置或空字符串时返回
    ``None``，表示 task-board 集成未配置（开源环境常态），调用方据此降级。

    路径性质：``EK_TASK_BOARD_DIR`` 应为**绝对路径**——后续 ``is_dir()`` 与
    ``subprocess cwd`` 都依赖绝对路径才有稳定语义；相对路径会让 task-board
    CLI 的 cwd 取决于本进程 cwd，行为不可预测。

    设计理由：原先 ``_TASK_BOARD_CLI_DIR`` 是模块级常量，import 期固化
    ``parents[5] / "tools" / "task-board"``——既依赖 monorepo 布局（开源仓
    无此结构），又让环境变量（运行时才设）无法生效。改为惰性函数后：

    - 宿主环境注入 ``EK_TASK_BOARD_DIR`` 后，目录存在即推送 task-board。
    - 开源仓不设环境变量 → 返回 ``None`` → ``_create_task_board_task``
      抛 ``StorageError`` → ``diagnose._push_findings_by_severity`` 接住并
      ``log.warning``，finding 仍进 maintenance_queue，只是不外推（best-effort
      降级，符合 spec §5.4 语义）。
    """
    env_value = os.environ.get("EK_TASK_BOARD_DIR", "").strip()
    if not env_value:
        return None
    return Path(env_value)


def _create_task_board_task(finding: Finding) -> None:
    """Create a task-board entry for a high-severity finding via Node CLI.

    未配置 ``EK_TASK_BOARD_DIR`` 时抛 ``StorageError``，由调用方
    (``diagnose._push_findings_by_severity``) 接住做 best-effort 降级。
    """
    task_board_dir = _resolve_task_board_dir()

    if task_board_dir is None:
        raise StorageError("task-board 集成未配置 (EK_TASK_BOARD_DIR 未设置)")

    if not task_board_dir.is_dir():
        raise StorageError(f"task-board 目录不存在: {task_board_dir}")

    node_bin = shutil.which("node")
    if node_bin is None:
        raise StorageError("node 不在 PATH, task-board CLI 不可用")

    category = _category_from_rule(finding.rule_id)
    payload = {
        "kind": "task",
        "title": f"[diagnose] {finding.rule_id} - {finding.message[:40]}",
        "summary": finding.message,
        "category": category,
        "priority": "high",
        "docRefs": [finding.target_path] if finding.target_path else [],
        "ownerKind": "agent",
    }

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        encoding="utf-8",
        delete=False,
    ) as fh:
        json.dump(payload, fh, ensure_ascii=False)
        payload_path = fh.name

    try:
        env = {**os.environ, "NODE_NO_WARNINGS": "1"}
        subprocess.run(
            [node_bin, "src/cli.mjs", "upsert", "--file", payload_path, "--json"],
            cwd=task_board_dir,
            check=True,
            timeout=10,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise StorageError("task-board upsert 超时") from exc
    except subprocess.CalledProcessError as exc:
        raise StorageError(f"task-board upsert 失败 (exit {exc.returncode})") from exc
    except OSError as exc:
        # 兜底 race condition：which 找到 node 后被删/权限变更等 OS 级失败。
        # TimeoutExpired/CalledProcessError 在 Python 3.11+ 是 SubprocessError 子类、
        # 非 OSError 子类，故需独立 except 子句；此处只接 OS 级错误。
        raise StorageError(f"task-board subprocess 调用失败: {exc}") from exc
    finally:
        Path(payload_path).unlink(missing_ok=True)


def doctor(registry: Registry, data_root: Path, repair: bool = False) -> DoctorReport:
    findings: list[Finding] = []
    checked_rules: list[str] = []
    ran_handlers: set[int] = set()

    for rule_id, handler in _CHECK_REGISTRY:
        checked_rules.append(rule_id)
        handler_key = id(handler)
        if handler_key in ran_handlers:
            continue
        findings.extend(handler(registry, data_root))
        ran_handlers.add(handler_key)

    if repair:
        findings = _run_repair(findings, registry, data_root)

    report_path = _write_report(findings, data_root / "logs" / "diagnose", prefix="doctor")
    return DoctorReport(
        checked_rules=checked_rules,
        findings=findings,
        report_path=str(report_path),
    )


def _run_repair(
    findings: list[Finding],
    registry: Registry,
    data_root: Path,
) -> list[Finding]:
    recovery_log = data_root / "logs" / "refresh" / "recovery.log"
    if not recovery_log.exists():
        return findings

    for record in _parse_recovery_log(recovery_log):
        target_path = record.get("target_path")
        if not target_path:
            continue
        try:
            _rebuild_registry_for_file(registry, Path(target_path))
        except Exception as exc:  # pragma: no cover - defensive logging path
            log.warning("repair 回放失败 %s: %s", target_path, exc)
    return findings


def _parse_recovery_log(log_path: Path) -> list[RecoveryRecord]:
    records: list[RecoveryRecord] = []
    with log_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                log.warning("recovery.log 含损坏行: %s", line[:80])
                continue
            if not isinstance(payload, dict):
                continue
            target_path = payload.get("target_path")
            if not isinstance(target_path, str) or not target_path:
                continue
            record: RecoveryRecord = {
                "target_path": target_path,
                "message": str(payload.get("message", "")),
                "ts": str(payload.get("ts", "")),
            }
            records.append(record)
    return records


def _rebuild_registry_for_file(registry: Registry, file_path: Path) -> None:
    target_path = file_path
    if not target_path.is_absolute():
        target_path = registry.path.parent.parent / file_path
    if not target_path.exists():
        registry.delete_entry_by_path(str(target_path))
        registry.commit()
        return

    frontmatter_map, body = read_file(str(target_path))
    entry = _fm_to_entry(frontmatter_map, file_path=str(target_path), body=body)
    registry.upsert_entry(entry, target_path, body)
    recompute_for_neighbors(entry.id, registry)
    registry.commit()
