# tutorials/extending-with-new-kind.md

> 跟着步骤为 EgoKnowledge 加一种新 kind。本教程用 `experiment`（个人实验记录）做演示。预计 20 分钟。
>
> **注**：本文档中的版本号（如 schema 1.0→1.1）为教学示例，实际版本请以 [`registry/`](../../src/ego_knowledge/registry/) 中 `REGISTRY_SCHEMA_VERSION` 为准。
> 默认数据目录是 `~/.ego-knowledge/data`；下文用 `EK_DATA_ROOT` 显式指向数据目录。

端到端**走一遍流程**的教程，每步给出 diff 级粒度。设计权衡见 [how-to/add-new-kind.md](../how-to/add-new-kind.md)。

> schema 升 1.0 → 1.1。跑前先备份：`cp -r "$EK_DATA_ROOT" "$EK_DATA_ROOT.backup-tutorial"`

## 目标

加 `experiment` kind，含字段 `hypothesis` / `started_at` / `result`，其余继承 EntryBase。

## 1. 改 models.py

[`src/ego_knowledge/models.py`](../../src/ego_knowledge/models.py)：

```python
class Kind(StrEnum):
    # ...原 6 项...
    EXPERIMENT = "experiment"   # 新

@dataclass(slots=True)
class ExperimentEntry(EntryBase):
    hypothesis: str = ""
    started_at: date | None = None
    result: str = ""

_KIND_SHORT[Kind.EXPERIMENT] = "exp"
KIND_TO_CLASS[Kind.EXPERIMENT] = ExperimentEntry
_PERSISTENT_FIELDS |= {"hypothesis", "started_at", "result"}
```

`_KIND_SHORT` 决定 id 形如 `ek_exp_01HXXXX...`。

## 2. 改 SCHEMA_SQL

[`src/ego_knowledge/registry/`](../../src/ego_knowledge/registry/)：找 `entries` 表的 CHECK 约束，加 `experiment`：

```sql
kind TEXT NOT NULL CHECK (kind IN (
    'source','note','dossier','concept','decision','view','experiment'
)),
```

加 fields 表（SCHEMA_SQL 末尾）：

```sql
CREATE TABLE IF NOT EXISTS experiment_fields (
    entry_id TEXT PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
    hypothesis TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    result TEXT NOT NULL DEFAULT ''
);
```

升 schema_version：

```python
REGISTRY_SCHEMA_VERSION = "1.1"
```

## 3. 加 JSON Schema

新建 `schemas/experiment.schema.json`，复制 `concept.schema.json` 改字段：

```json
{
  "$schema": "https://json-schema.org/draft-07/schema#",
  "title": "ExperimentEntry",
  "allOf": [{ "$ref": "_common.schema.json" }],
  "properties": {
    "kind": { "const": "experiment" },
    "hypothesis": { "type": "string" },
    "started_at": { "type": "string", "format": "date" },
    "result": { "type": "string" }
  },
  "required": ["kind", "title", "search_terms", "hypothesis"]
}
```

## 4. 改 service 默认值

[`src/ego_knowledge/_entry_store.py`](../../src/ego_knowledge/_entry_store.py)：

```python
_DEFAULT_STATUS[Kind.EXPERIMENT] = Status.ACTIVE
_DEFAULT_FRESHNESS[Kind.EXPERIMENT] = Freshness.WATCH
```

不要把 EXPERIMENT 加进 `STABLE_SLUG_KINDS`，实验记录的 slug 应可改。

## 5. 改 CLI

[`src/ego_knowledge/cli.py`](../../src/ego_knowledge/cli.py)：

```python
KINDS = ["source","note","dossier","concept","decision","view","experiment"]
```

## 6. 写迁移脚本

`entries.kind` 的 CHECK 约束**不能用 ALTER TABLE 修改**（SQLite 限制）。实战路径：备份 catalog → 删除 → 跑 `build-registry` 从 .md 重建。

新建 `migrations/1.0-to-1.1.py`：

```python
"""Add experiment kind, no data migration needed."""
from pathlib import Path
import os

from ego_knowledge.registry import build_registry

def migrate(data_root: Path) -> None:
    catalog = data_root / "registry/catalog.sqlite"
    backup = data_root / "registry/catalog.sqlite.bak"
    if catalog.exists():
        catalog.rename(backup)
    build_registry(data_root)  # 从 .md 重建，新 CHECK 随 CREATE 应用
    print("catalog 重建完成；旧库备份在 catalog.sqlite.bak")

if __name__ == "__main__":
    default_root = Path.home() / ".ego-knowledge" / "data"
    migrate(Path(os.environ.get("EK_DATA_ROOT", default_root)))
```

## 7. 跑迁移并写一条

```bash
cd <repo>
uv sync && uv run python migrations/1.0-to-1.1.py
uv run ek doctor                                # 应全 PASS
uv run ek ingest --kind experiment --payload '{
  "title": "test 番茄钟+block 写作能否减少切换成本",
  "hypothesis": "25min 一段不切窗口能写更多",
  "started_at": "2026-04-26",
  "search_terms": ["番茄钟","pomodoro","deep work","writing","block-writing"],
  "tags": ["productivity"]
}'
uv run ek stats --by kind                        # 应看到 experiment=1
```

## 下一步

- 写测试 + 文档收尾：[extending-with-new-kind-part2.md](extending-with-new-kind-part2.md)
- 加 RelationType：[how-to/add-new-relation-type.md](../how-to/add-new-relation-type.md)
- schema 升级完整考虑：[how-to/migrate-schema.md](../how-to/migrate-schema.md)
