# CLI 命令参考

> `ek` 子命令与可调用脚本的完整参数清单。真源：[`cli.py`](../../src/ego_knowledge/cli.py) 与 [`scripts/`](../../src/ego_knowledge/scripts/)。

CLI 是 Core 的薄壳；`ek` 子命令对应 `EgoKnowledge` 的方法，存量修复脚本另走 `ego_knowledge.scripts.*` 入口，不经过 Core。所有命令输出 JSON（含错误）。

## 写入

### `ek ingest --kind <K> --payload <JSON> [--conflict-policy P]`

- `--kind`：6 选 1（source/note/dossier/concept/decision/view）
- `--payload`：JSON object 字符串
- `--conflict-policy`：`strict`（默认）/ `merge_suggest` / `allow`，见 [conflict-policies.md](conflict-policies.md)

### `ek update <id> --changes <JSON>`

- 不能改 `id/kind/file_path/metrics`
- `body` 只允许按 [body-update-standard.md](../how-to/body-update-standard.md) 单条原地写入
- 稳定 slug 类型（concept/dossier/decision）改 slug 必须走 `rename`

### `ek rename <id> --slug <new>`

- 仅稳定 slug 类型生效

### `ek promote <id> --to <K> [--freshness F]`

- `--freshness`：`stable / watch（默认） / volatile`

## 读取

### `ek get <id>`

- 输出去掉 `body`，保留 frontmatter + 派生元数据

### `ek search <query> [--kind K]... [--limit N] [--backend B]... [--semantic] [--no-semantic]`

- `--kind`：可重复，过滤 kind
- `--backend`：`exact / bm25 / graph / dense`，可重复；缺省 `exact+bm25`，dense 可用时自动加入
- `--semantic`：显式启用 dense 语义路（与默认等价，便于脚本显式声明）
- `--no-semantic`：关闭 dense 路，回四路检索
- `--semantic` 与 `--no-semantic` 互斥；`--no-semantic` 与 `--backend dense` 冲突
- authority 不作为显式 backend 选项；它在排序阶段自动加权。
- 需配置 `SILICONFLOW_API_KEY` 环境变量，或配置 `secrets.toml [siliconflow] api_key`。

### `ek related <id> [--depth D] [--type T]`

- `--depth >= 1`
- `--type`：单个 RelationType 过滤

## 关系

### `ek link <src> <tgt> --type T [--source S]`

- `--type`：8 种 RelationType 之一
- `--source`：`confirmed`（默认）/ `ai_suggested` / `ai_confirmed`

### `ek unlink <src> <tgt>`

## domain

### `ek domains list`

### `ek domains add --name <N>`

### `ek domains migrate --entries <id1,id2,...> --to <D>`

## 维护

### `ek build-registry`

全量从文件重建 `catalog.sqlite`。

### `ek doctor [--repair]`

文件级体检。`--repair` 自动修可修项；不可修产出报告 + 任务板任务。

### `ek diagnose`

知识级诊断（drift、冲突簇、孤儿）。

### `ek diagnose --establish-baseline`

写入 baseline.json 含五指标统计。

### `ek diagnose --recompute-authority`

全图重算五元指标 + PageRank 图权威传播。

### `ek rebuild-dense-index [--stale] [--resume] [--batch-size N]`

重建稠密语义索引（调用 SiliconFlow BGE-M3 API）。`--stale` 只重建内容 hash 已漂移的 active 条目；`--resume` 断点续跑；`--batch-size` 单次 API 调用条目数（1-16，默认 16）。需配置 `SILICONFLOW_API_KEY` 环境变量，或配置 `secrets.toml [siliconflow] api_key`。

### `ek stats [--by kind|status|freshness|domain] [--snapshot]`

`--snapshot` 落盘到 `logs/stats/YYYY-MM-DD.json`。

### `ek review [--due]`

列出 dossier 的 review 队列，`--due` 只看过期（`--overdue` 保留为隐藏兼容参数）。

### `ek review --id <queue_id>`

查看 maintenance_queue 单条详情。

### `ek review --resolve <queue_id>` / `ek review --dismiss <queue_id>`

标记 maintenance_queue 条目已处理或忽略。

## 存量修复脚本

### `python3 -m ego_knowledge.scripts.normalize_legacy`

存量 frontmatter 全角结构标点修复。三种模式：

- `--dry-run`：只读扫描，输出待修复清单与 diff 摘要
- `--apply`：备份原始内容到 `--backup-dir` 后写回修复后的 frontmatter
- `--restore`：从 `--backup-dir` 反向恢复原始内容

参数：

- `--data-root`：数据根目录；未传时按 `EGOKNOWLEDGE_DATA_ROOT` / `EK_DATA_ROOT` / 默认解析
- `--backup-dir`：apply/restore 使用的备份目录；apply 未传时基于 data_root 推断

### `python3 -m ego_knowledge.scripts.cleanup_broken_relations`

存量 AI 断裂关系边清理。三种模式：

- `--dry-run`：只读扫描并输出清理计划
- `--apply`：备份后执行 AI 断裂边清理
- `--restore`：从 `--backup-dir` 恢复 apply 前状态

参数：

- `--data-root`：数据根目录；未传时按 `EGOKNOWLEDGE_DATA_ROOT` / `EK_DATA_ROOT` / 默认解析
- `--backup-dir`：apply/restore 使用的备份目录；apply 未传时基于 data_root 推断
- `--report-dir`：可选，写出 broken-relations 与 confirmed 裁决清单文件；未传时不写出

策略边界：`origin=confirmed` 只进入裁决清单，不自动删除；脚本只删除关系边，不删除任何条目。

## 输出契约

成功：`stdout` 一行 JSON（dataclass 转 dict，Path/Enum/date 用 `_json_default` 序列化）。

失败：`stderr` 一行 JSON（`to_transport(exc)` 的扁平输出 `{code, message, details}`），进程退出码 = error.exit_code。详见
[error-types.md](error-types.md)。

## 数据根

通过环境变量 `EK_DATA_ROOT` 指定，默认 `~/.ego-knowledge/data`（由 `ego_knowledge.paths.default_data_root()` 运行时惰性求值）。
