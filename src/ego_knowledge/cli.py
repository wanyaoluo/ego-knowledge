"""Click CLI for EgoKnowledge."""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import os
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import cast

import click

from ._review_cli import run_review_command
from ._validation import _asdict, _is_dataclass_instance
from .core import (
    EgoKnowledge,
)
from .doctor import _create_task_board_task, _write_report
from .errors import EgoKnowledgeError, to_transport
from .frontmatter import _fm_to_entry
from .paths import default_data_root

KINDS = ["source", "note", "dossier", "concept", "decision", "view"]

__all__ = [
    "main",
    "_get_ek",
    "_json_default",
    "_fm_to_entry",
    "_write_report",
    "_create_task_board_task",
]


def _get_ek(*, dense_disabled: bool = False) -> EgoKnowledge:
    root = Path(os.environ.get("EK_DATA_ROOT", default_data_root()))
    return EgoKnowledge(root, dense_disabled=dense_disabled)


def _json_default(obj: object) -> object:
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return obj.as_posix()
    if _is_dataclass_instance(obj):
        return _asdict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _entry_to_output(entry: object) -> dict[str, object]:
    if not _is_dataclass_instance(entry):
        raise TypeError("entry 必须是 dataclass 实例")
    data = _asdict(entry)
    data.pop("body", None)
    return data


def _emit_json(payload: object) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, default=_json_default))


def _run_json(action: Callable[[], object]) -> None:
    try:
        payload = action()
    except EgoKnowledgeError as exc:
        click.echo(
            json.dumps(to_transport(exc), ensure_ascii=False, default=_json_default),
            err=True,
        )
        raise click.exceptions.Exit(exc.exit_code) from exc
    _emit_json(payload)


@click.group()
def main() -> None:
    """ek - EgoKnowledge CLI."""


@main.command()
@click.option("--kind", required=True, type=click.Choice(KINDS))
@click.option("--payload", required=True, type=str, help="JSON payload")
@click.option(
    "--conflict-policy",
    default="strict",
    type=click.Choice(["strict", "merge_suggest", "allow"]),
)
def ingest(kind: str, payload: str, conflict_policy: str) -> None:
    def action() -> dict[str, object]:
        ek = _get_ek()
        try:
            entry = ek.ingest(
                kind=kind,
                payload=_parse_json_object(payload, name="payload"),
                conflict_policy=conflict_policy,
            )
            return _entry_to_output(entry)
        finally:
            ek.close()

    _run_json(action)


@main.command()
@click.argument("id")
def get(id: str) -> None:
    def action() -> dict[str, object]:
        ek = _get_ek()
        try:
            return _entry_to_output(ek.get(id))
        finally:
            ek.close()

    _run_json(action)


@main.command()
@click.argument("id")
@click.option("--changes", required=True, type=str, help="JSON changes payload")
def update(id: str, changes: str) -> None:
    def action() -> dict[str, object]:
        ek = _get_ek()
        try:
            entry = ek.update(id=id, changes=_parse_json_object(changes, name="changes"))
            return _entry_to_output(entry)
        finally:
            ek.close()

    _run_json(action)


@main.command()
@click.argument("source_id")
@click.argument("target_id")
@click.option("--type", "rel_type", required=True, type=str)
@click.option(
    "--source",
    "relation_source",
    default="confirmed",
    type=click.Choice(["confirmed", "ai_suggested", "ai_confirmed"]),
)
def link(source_id: str, target_id: str, rel_type: str, relation_source: str) -> None:
    def action() -> dict[str, object]:
        ek = _get_ek()
        try:
            relation = ek.link(
                source_id=source_id,
                target_id=target_id,
                rel_type=rel_type,
                source=relation_source,
            )
            return {
                "ok": True,
                "source_id": source_id,
                "target_id": target_id,
                "relation": dataclasses.asdict(relation),
            }
        finally:
            ek.close()

    _run_json(action)


@main.command()
@click.argument("source_id")
@click.argument("target_id")
def unlink(source_id: str, target_id: str) -> None:
    def action() -> dict[str, object]:
        ek = _get_ek()
        try:
            ek.unlink(source_id=source_id, target_id=target_id)
            return {"ok": True, "source_id": source_id, "target_id": target_id}
        finally:
            ek.close()

    _run_json(action)


@main.command()
@click.argument("id")
@click.option("--to", "target_kind", required=True, type=click.Choice(KINDS))
@click.option(
    "--freshness",
    default="watch",
    type=click.Choice(["stable", "watch", "volatile"]),
)
def promote(id: str, target_kind: str, freshness: str) -> None:
    def action() -> dict[str, object]:
        ek = _get_ek()
        try:
            return _entry_to_output(ek.promote(id=id, target_kind=target_kind, freshness=freshness))
        finally:
            ek.close()

    _run_json(action)


@main.command()
@click.argument("id")
@click.option("--slug", "new_slug", required=True, type=str)
def rename(id: str, new_slug: str) -> None:
    def action() -> dict[str, object]:
        ek = _get_ek()
        try:
            return _entry_to_output(ek.rename(id=id, new_slug=new_slug))
        finally:
            ek.close()

    _run_json(action)


@main.command("domains")
@click.argument("subcommand", type=click.Choice(["list", "add", "migrate"]))
@click.option("--name", help="用于 add（新 domain 名）")
@click.option("--entries", help="用于 migrate（逗号分隔的 entry ID 列表）")
@click.option("--to", "target_domain", help="用于 migrate（目标 domain 名）")
def domains(
    subcommand: str,
    name: str | None,
    entries: str | None,
    target_domain: str | None,
) -> None:
    def action() -> object:
        ek = _get_ek()
        try:
            if subcommand == "list":
                return ek.domains_list()
            if subcommand == "add":
                if not name:
                    raise click.UsageError("--name required for add")
                ek.domains_add(name)
                return {"ok": True, "name": name}
            if not entries or not target_domain:
                raise click.UsageError("--entries and --to required for migrate")
            entry_ids = [item for item in entries.split(",") if item]
            return dataclasses.asdict(ek.domains_migrate(entry_ids, target_domain=target_domain))
        finally:
            ek.close()

    _run_json(action)


@main.command()
@click.argument("query")
@click.option(
    "--kind",
    "kinds",
    multiple=True,
    type=click.Choice(KINDS),
    help="按 kind 过滤，可多次传入",
)
@click.option("--limit", default=20, type=click.IntRange(min=1))
@click.option(
    "--backend",
    "backends",
    multiple=True,
    type=click.Choice(["exact", "bm25", "graph", "dense"]),
    help="覆盖默认后端组合（默认 exact+bm25，dense 可用时自动追加）",
)
@click.option(
    "--semantic",
    "semantic",
    is_flag=True,
    default=False,
    help="显式启用 dense 语义检索（与默认行为等价，便于脚本显式声明）",
)
@click.option(
    "--no-semantic",
    "no_semantic",
    is_flag=True,
    default=False,
    help="关闭 dense 语义检索，回退到四路（exact+bm25+graph+authority）",
)
def search(
    query: str,
    kinds: tuple[str, ...],
    limit: int,
    backends: tuple[str, ...],
    semantic: bool,
    no_semantic: bool,
) -> None:
    need_api_key = semantic or "dense" in backends
    # --- 互斥校验 ---
    if semantic and no_semantic:
        raise click.UsageError("--semantic 和 --no-semantic 不能同时使用")
    if no_semantic and "dense" in backends:
        raise click.UsageError("--no-semantic 与 --backend dense 冲突")

    if need_api_key:
        from ._secrets import get_siliconflow_api_key

        if not get_siliconflow_api_key():
            flag = "--semantic" if semantic else "--backend dense"
            raise click.UsageError(
                f"{flag} 需要 SiliconFlow api_key，请先配置 secrets.toml [siliconflow]"
            )

    def action() -> dict[str, object]:
        ek = _get_ek(dense_disabled=no_semantic)
        try:
            # --backend dense 但索引为空
            if "dense" in backends and not ek.dense_index_populated():
                from .errors import ValidationError as EkValidationError

                raise EkValidationError("dense 索引为空，先跑 ek rebuild-dense-index")

            # 默认模式 + api_key 缺失时打印提示（不阻写，仅 stderr）
            if (
                not no_semantic
                and not semantic
                and not ek.dense_embedder_available()
                and "dense" not in backends
            ):
                click.echo(
                    "提示: dense 未启用（未配置 SiliconFlow api_key），"
                    "请配置 secrets.toml [siliconflow] 后跑 ek rebuild-dense-index",
                    err=True,
                )

            results = ek.search(
                query=query,
                kinds=list(kinds) or None,
                backends=list(backends) or None,
                limit=limit,
                expand_graph="graph" in backends if backends else True,
            )
            return {
                "query": query,
                "results": [dataclasses.asdict(result) for result in results],
            }
        finally:
            ek.close()

    _run_json(action)


@main.command()
@click.argument("id")
@click.option("--depth", default=1, type=click.IntRange(min=1))
@click.option(
    "--type",
    "rel_type",
    default=None,
    type=str,
    help="按单个 relation type 过滤",
)
def related(id: str, depth: int, rel_type: str | None) -> None:
    def action() -> list[dict[str, object]]:
        ek = _get_ek()
        try:
            return [
                _entry_to_output(entry)
                for entry in ek.related(id=id, depth=depth, rel_type=rel_type)
            ]
        finally:
            ek.close()

    _run_json(action)


@main.command("build-registry")
def build_registry() -> None:
    def action() -> dict[str, object]:
        ek = _get_ek()
        try:
            return dataclasses.asdict(ek.build_registry())
        finally:
            ek.close()

    _run_json(action)


@main.command("rebuild-dense-index")
@click.option("--stale", "only_stale", is_flag=True, help="只重建内容 hash 已漂移的条目")
@click.option("--resume", is_flag=True, help="读取进度日志，跳过已成功条目")
@click.option(
    "--batch-size",
    default=16,
    type=click.IntRange(min=1, max=16),
    show_default=True,
    help="单次调用 SiliconFlow 的条目数",
)
def rebuild_dense_index(only_stale: bool, resume: bool, batch_size: int) -> None:
    def action() -> dict[str, object]:
        from ._dense_embedder import DenseEmbedder
        from ._dense_index import default_progress_log
        from ._secrets import get_siliconflow_api_key

        api_key = get_siliconflow_api_key()
        if not api_key:
            raise click.UsageError("缺少 SiliconFlow api_key，请先配置 secrets.toml [siliconflow]")

        data_root = Path(os.environ.get("EK_DATA_ROOT", default_data_root()))
        ek = _get_ek()
        try:
            progress_log = default_progress_log(data_root)
            embedder = DenseEmbedder(
                api_key,
                cache_dir=data_root / "cache" / "embeddings",
                log_dir=data_root / "logs" / "retrieval",
            )
            stats = ek.rebuild_dense_index(
                embedder,
                only_stale=only_stale,
                resume=resume,
                batch_size=batch_size,
            )
            return {
                "ok": stats["failed"] == 0,
                "action": "rebuild_dense_index",
                "stats": stats,
                "stale": only_stale,
                "resume": resume,
                "progress_log": progress_log,
            }
        finally:
            ek.close()

    _run_json(action)


@main.command()
@click.option("--repair", is_flag=True)
def doctor(repair: bool) -> None:
    def action() -> dict[str, object]:
        ek = _get_ek()
        try:
            report = ek.doctor(repair=repair)
            return {
                "checked_rules": report.checked_rules,
                "findings": [dataclasses.asdict(finding) for finding in report.findings],
                "report_path": report.report_path,
            }
        finally:
            ek.close()

    _run_json(action)


@main.command()
@click.option("--establish-baseline", is_flag=True, help="写入 baseline.json 含五指标统计")
@click.option(
    "--recompute-authority",
    is_flag=True,
    help="全图重算五元指标 + PageRank 图权威传播",
)
def diagnose(establish_baseline: bool, recompute_authority: bool) -> None:
    if establish_baseline and recompute_authority:
        raise click.UsageError("--establish-baseline 与 --recompute-authority 互斥，只能选一个")

    def action() -> dict[str, object]:
        ek = _get_ek()
        try:
            if recompute_authority:
                total = ek.recompute_authority()
                return {
                    "ok": True,
                    "action": "recompute_authority",
                    "entries_recomputed": total,
                }

            report = ek.diagnose()
            result: dict[str, object] = {
                "checked_rules": report.checked_rules,
                "findings": [dataclasses.asdict(finding) for finding in report.findings],
                "report_path": report.report_path,
            }
            if establish_baseline:
                baseline_path = ek.establish_diagnose_baseline()
                result["baseline_path"] = str(baseline_path)
            return result
        finally:
            ek.close()

    _run_json(action)


@main.command()
@click.option("--by", type=click.Choice(["kind", "domain", "freshness", "status"]))
@click.option("--snapshot", is_flag=True, help="写入 logs/stats/YYYY-MM-DD.json")
def stats(by: str | None, snapshot: bool) -> None:
    def action() -> dict[str, object]:
        ek = _get_ek()
        try:
            data = ek.stats(group_by=by)
            if snapshot:
                ek.write_stats_snapshot(data)
            return data
        finally:
            ek.close()

    _run_json(action)


@main.command()
@click.option(
    "--due",
    "show_due",
    is_flag=True,
    help="只显示 review_due_at 已过期的待审条目（原 --overdue）",
)
@click.option("--overdue", is_flag=True, hidden=True, help="已改名 --due，保留兼容")
@click.option("--id", "queue_id", default=None, help="查看队列条目详情")
@click.option("--resolve", "resolve_id", default=None, help="标记队列条目已处理")
@click.option("--dismiss", "dismiss_id", default=None, help="忽略队列条目")
@click.option("--origin", type=click.Choice(["human", "ai_auto", "ai_proposed"]), default=None)
@click.option("--approve", "approve_flag", is_flag=True, default=False, help="批准 --id AI 提议")
@click.option("--reject", "reject_flag", is_flag=True, default=False, help="拒绝 --id AI 提议")
@click.option("--reason", "reject_reason", default="", help="拒绝理由")
def review(
    show_due: bool,
    overdue: bool,
    queue_id: str | None,
    resolve_id: str | None,
    dismiss_id: str | None,
    origin: str | None,
    approve_flag: bool,
    reject_flag: bool,
    reject_reason: str,
) -> None:
    """列 maintenance_queue 待办或查看/处理单条。"""
    _run_json(
        lambda: run_review_command(
            get_ek=_get_ek,
            entry_to_output=_entry_to_output,
            show_due=show_due,
            overdue=overdue,
            queue_id=queue_id,
            resolve_id=resolve_id,
            dismiss_id=dismiss_id,
            origin=origin,
            approve_flag=approve_flag,
            reject_flag=reject_flag,
            reject_reason=reject_reason,
        )
    )


def _parse_json_object(raw: str, *, name: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise click.BadParameter(f"{name} 不是合法 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise click.BadParameter(f"{name} 必须是 JSON object")
    return cast(dict[str, object], value)


@main.command("watch-github")
@click.option("--add", "add_target", default=None, help="注册新监听 (owner/repo)")
@click.option("--list", "list_targets", is_flag=True, help="查看所有监听")
@click.option("--config-init", is_flag=True, help="引导创建 secrets.toml")
def watch_github(
    add_target: str | None,
    list_targets: bool,
    config_init: bool,
) -> None:
    """L1 GitHub releases 轮询：注册/查看/轮询监听。"""
    from ._secrets import get_github_token, init_secrets_file

    def action() -> dict[str, object]:
        ek = _get_ek()
        try:
            if config_init:
                init_secrets_file()
                return {"ok": True, "action": "config_init"}

            if add_target:
                watch_id = ek.add_external_watch(add_target)
                return {"ok": True, "action": "add", "id": watch_id, "target": add_target}

            if list_targets:
                watches = ek.list_external_watches()
                return {"watches": watches}

            # Default: poll all
            token = get_github_token()
            result = ek.poll_external_watches(token=token)
            return {
                "processed": result.processed,
                "new": result.new,
                "errors": result.errors,
            }
        finally:
            ek.close()

    _run_json(action)
