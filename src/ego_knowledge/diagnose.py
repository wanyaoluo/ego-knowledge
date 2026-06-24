"""Knowledge-level diagnosis for EgoKnowledge."""

from __future__ import annotations

import json
import logging
import math
import shutil
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

from ._diagnose_rules import action, decay, push, structure
from .doctor import Finding, Severity, _create_task_board_task, _write_report
from .errors import EgoKnowledgeError, StorageError
from .maintenance_queue_store import enqueue as mq_enqueue
from .maintenance_queue_store import mark_sent
from .models import Kind
from .registry import Registry

log = logging.getLogger(__name__)

type DiagnoseRule = Callable[[Registry], list[Finding]]

_METRIC_COLUMNS: tuple[str, ...] = (
    "evidence_strength",
    "drift_score",
    "compression_ratio",
    "action_relevance",
    "retrieval_heat",
)


@dataclass(slots=True)
class DiagnoseReport:
    checked_rules: list[str]
    findings: list[Finding]
    report_path: str


def _rule_source_reachability(registry: Registry) -> list[Finding]:
    findings: list[Finding] = []
    for entry in registry.all_entries_except_kind(Kind.VIEW.value):
        if _can_reach_source(entry.id, registry):
            continue
        findings.append(
            Finding(
                rule_id="redline_9_source_reachability",
                severity=Severity.HIGH,
                target_id=entry.id,
                target_path=entry.file_path,
                message=f"{entry.kind} {entry.id} 无法追溯到任何 source",
            )
        )
    return findings


def _can_reach_source(entry_id: str, registry: Registry, max_depth: int = 10) -> bool:
    visited: set[str] = {entry_id}
    queue: deque[tuple[str, int]] = deque([(entry_id, 0)])

    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_depth:
            continue
        entry = registry.get_entry(current_id)
        if entry.kind == Kind.SOURCE:
            return True
        for neighbor_id in registry.out_refs(
            current_id,
            types=["source_refs", "evidence_refs", "derived_from"],
        ):
            if neighbor_id in visited:
                continue
            visited.add(neighbor_id)
            queue.append((neighbor_id, depth + 1))
    return False


def _rule_view_as_evidence(registry: Registry) -> list[Finding]:
    findings: list[Finding] = []
    view_ids = {entry.id for entry in registry.all_entries_by_kind(Kind.VIEW.value)}
    if not view_ids:
        return findings

    for entry in registry.all_entries_except_kind(Kind.VIEW.value):
        for neighbor_id in registry.neighbors(entry.id, direction="out"):
            if neighbor_id not in view_ids:
                continue
            findings.append(
                Finding(
                    rule_id="redline_10_view_as_evidence",
                    severity=Severity.HIGH,
                    target_id=entry.id,
                    target_path=entry.file_path,
                    message=f"{entry.kind} {entry.id} 将 view {neighbor_id} 用作出边目标",
                )
            )
    return findings


_DIAGNOSE_RULES: list[tuple[str, DiagnoseRule]] = [
    # L3 redlines (highest priority, run first)
    ("redline_9_source_reachability", _rule_source_reachability),
    ("redline_10_view_as_evidence", _rule_view_as_evidence),
    # L4 decay
    ("decay_source_context", decay.rule_decay_source_context),
    ("decay_dossier_outdated", decay.rule_decay_dossier_outdated),
    ("decay_concept_evidence_singular", decay.rule_decay_concept_evidence_singular),
    # L4 structure
    ("structure_fossil_concept", structure.rule_structure_fossil_concept),
    ("structure_monoculture_evidence", structure.rule_structure_monoculture_evidence),
    ("structure_supersedes_cycle", structure.rule_structure_supersedes_cycle),
    # L4 action
    ("action_demote", action.rule_action_demote),
    ("action_merge", action.rule_action_merge),
    ("action_retract", action.rule_action_retract),
    # L4 push
    ("push_premise_shaken", push.rule_push_premise_shaken),
    ("push_crystallize", push.rule_push_crystallize),
    ("push_pseudo_stable", push.rule_push_pseudo_stable),
    ("push_internal_split", push.rule_push_internal_split),
    ("push_cognitive_divergence", push.rule_push_cognitive_divergence),
]


# action_demote 不入 queue（spec §6.2 line 336）
_NO_QUEUE_RULES = frozenset({"action_demote"})


def _push_findings_by_severity(findings: list[Finding], registry: Registry) -> None:
    for finding in findings:
        if finding.severity == Severity.LOW:
            continue  # low 不进 queue，落 logs/stats/
        if finding.rule_id in _NO_QUEUE_RULES:
            continue  # action_demote AI 自主，不进 queue
        queue_id = mq_enqueue(registry, finding)
        if finding.severity == Severity.HIGH:
            # task-board 外推（best-effort，spec §5.4）：未配置 EK_TASK_BOARD_DIR、
            # task-board 调用失败或 OS 级异常时，finding 仍留 maintenance_queue
            # （enqueue 已发生，status 不为 sent），仅外推跳过，不阻断 diagnose。
            _push_to_task_board_best_effort(finding, queue_id, registry)


def _push_to_task_board_best_effort(
    finding: Finding, queue_id: str, registry: Registry
) -> None:
    """对 HIGH finding 做 task-board best-effort 外推 + mark_sent。

    分离两个故障域：
    - task-board 推送失败（``StorageError``）→ ``log.warning``，跳过 mark_sent，
      finding 留 pending（外推跳过，运维可从日志 rule_id/queue_id 定位）。
    - mark_sent 失败（任意 ``EgoKnowledgeError``，含 NotFoundError/ValidationError
      等非 StorageError 子类）→ ``log.error``，task-board 已推送但 queue 状态未更新；
      独立 log 措辞避免与 task-board 推送失败混淆故障域。
    """
    try:
        _create_task_board_task(finding)
    except StorageError as exc:
        log.warning(
            "task-board 推送失败，finding 留 maintenance_queue 等待人工处理: "
            "rule_id=%s queue_id=%s target=%s: %s",
            finding.rule_id,
            queue_id,
            finding.target_id,
            exc,
        )
        return
    try:
        mark_sent(registry, queue_id)
    except EgoKnowledgeError as exc:
        log.error(
            "maintenance_queue mark_sent 失败（task-board 已外推） "
            "queue_id=%s rule_id=%s target=%s: %s",
            queue_id,
            finding.rule_id,
            finding.target_id,
            exc,
        )


def diagnose(registry: Registry, data_root: Path) -> DiagnoseReport:
    findings: list[Finding] = []
    checked_rules: list[str] = []

    for rule_id, rule_fn in _DIAGNOSE_RULES:
        try:
            findings.extend(rule_fn(registry))
            checked_rules.append(rule_id)
        except Exception as exc:  # pragma: no cover - defensive logging path
            log.warning("诊断规则 %s 失败: %s", rule_id, exc)

    _push_findings_by_severity(findings, registry)
    report_path = _write_report(findings, data_root / "logs" / "diagnose", prefix="diagnose")
    return DiagnoseReport(
        checked_rules=checked_rules,
        findings=findings,
        report_path=str(report_path),
    )


# ---------------------------------------------------------------------------
# 2.4  establish_baseline
# ---------------------------------------------------------------------------


def _percentile(values: list[float], p: float) -> float:
    """Compute the p-th percentile with diagnose's empty-input default."""
    if not values:
        return 0.0

    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return float(sorted_values[0])

    rank = (len(sorted_values) - 1) * p / 100.0
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(sorted_values[int(rank)])

    weight = rank - low
    return float(sorted_values[low] * (1 - weight) + sorted_values[high] * weight)


def _compute_metric_stats(values: list[float]) -> dict[str, float]:
    """Compute count, min, p50, p90, p95, max for a list of values."""
    if not values:
        return {"count": 0, "min": 0.0, "p50": 0.0, "p90": 0.0, "p95": 0.0, "max": 0.0}
    sorted_vals = sorted(values)
    return {
        "count": len(sorted_vals),
        "min": sorted_vals[0],
        "p50": _percentile(sorted_vals, 50),
        "p90": _percentile(sorted_vals, 90),
        "p95": _percentile(sorted_vals, 95),
        "max": sorted_vals[-1],
    }


def establish_baseline(registry: Registry, data_root: Path) -> Path:
    """Compute five-metric baseline statistics and write to baseline.json.

    If a previous baseline.json exists, it is renamed to
    ``baseline.{ISO_TIMESTAMP}.json`` and at most 5 historical copies
    are kept (oldest pruned).

    Returns the path to the written baseline file.
    """
    rows = registry.conn.execute(
        """
        SELECT evidence_strength, drift_score, compression_ratio,
               action_relevance, retrieval_heat
          FROM entry_metrics
        """
    ).fetchall()

    buckets: dict[str, list[float]] = {col: [] for col in _METRIC_COLUMNS}
    for row in rows:
        for col in _METRIC_COLUMNS:
            buckets[col].append(float(cast(float, row[col])))

    baseline: dict[str, dict[str, float]] = {}
    for col in _METRIC_COLUMNS:
        baseline[col] = _compute_metric_stats(buckets[col])

    baseline_dir = data_root / "logs" / "diagnose"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = baseline_dir / "baseline.json"

    # --- W4: preserve previous baseline version ---
    if baseline_path.exists():
        ts_str = datetime.now().strftime("%Y%m%dT%H%M%S")
        archived = baseline_dir / f"baseline.{ts_str}.json"
        shutil.move(str(baseline_path), str(archived))
        log.info("旧 baseline 已保留: %s", archived)
        _prune_old_baselines(baseline_dir, keep=5)

    baseline_path.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return baseline_path


def _prune_old_baselines(baseline_dir: Path, keep: int = 5) -> None:
    """Remove oldest archived baselines beyond *keep*."""
    archives = sorted(
        baseline_dir.glob("baseline.????????T??????.json"),
    )
    for old in archives[:-keep]:
        old.unlink(missing_ok=True)
        log.info("已删除过期 baseline: %s", old)
